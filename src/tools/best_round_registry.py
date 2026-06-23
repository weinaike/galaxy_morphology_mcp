"""In-memory best-round registry for iterative component analysis.

Each call to ``component_analysis`` / ``analyze_multiband_components`` compares the
*current* fitting round against the best round seen so far for that galaxy. The
comparison is **visual-primary** (2D residual / 1D residual / component structure);
reduced chi-square, BIC and the summary's parameter/constraint conditions are passed
to the VLM only as secondary reference. The verdict comes from the VLM. When the VLM
cannot run, the historical best is **retained** (no metric-driven replacement).

The registry is keyed by the galaxy main directory (filesystem-unique) and lives
in-process for the MCP lifetime (bounded to one galaxy's fitting session).
"""

import os
import re
import threading
from dataclasses import dataclass
from typing import Optional

from . import prompt
from .parse_lyric import parse_region_info_from_lyric


_LOCK = threading.Lock()
_REGISTRY: dict[str, "BestRoundEntry"] = {}

# Directories under which per-round run dirs live.
#   single-band (S4G/GALFIT): <galaxy>/archives/<hash>/
#   multi-band (GalfitS)    : <galaxy>/output/<ts>_<base>_iterN[_sed]/
_RUNANCESTORS = {"output", "archives"}
_ITER_RE = re.compile(r"_iter(\d+)", re.IGNORECASE)

_VERDICT_VALUES = ("CURRENT_BETTER", "HISTORICAL_BETTER", "EQUAL")
_VERDICT_FENCE_RE = re.compile(
    r"```verdict\s*\n\s*(CURRENT_BETTER|HISTORICAL_BETTER|EQUAL)\s*\n\s*```",
    re.IGNORECASE,
)
_VERDICT_LINE_RE = re.compile(
    r"VERDICT\s*[:：]\s*(CURRENT_BETTER|HISTORICAL_BETTER|EQUAL)", re.IGNORECASE
)

_COMPARISON_SYSTEM = (
    "你是一位严谨的星系形态学拟合质量评审专家。你的任务是基于两个拟合轮次的对比图，"
    "以图像残差与成分结构为核心依据，判断哪一轮拟合质量更高。"
    "指标（卡方、BIC）与 summary 中的参数/约束条件仅供参考，不得作为主要判据。"
    "最后必须给出严格的 verdict 判定。"
)

_COMPARISON_INTRO = (
    "下面第一张图为历史最优轮次，最后一张图为当前轮次。"
    "请以【2D残差结构 + 1D残差曲线 + 成分结构】的对比为核心依据判断哪一轮拟合质量更高；"
    "指标与参数条件仅为参考。"
)


@dataclass
class BestRoundEntry:
    """Snapshot of one fitting round retained as the current best for its galaxy."""

    galaxy_key: str
    round_label: str                       # run-dir basename (hash or "..._iterN[_sed]")
    round_number: Optional[int]            # int from _iterN when present, else None
    object_name: Optional[str]
    image_path: str
    summary_path: str
    reduced_chisq: Optional[float] = None  # best-effort, reference only
    bic: Optional[float] = None            # best-effort, reference only
    analysis_md: Optional[str] = None


# ── registry accessors ────────────────────────────────────────────────────────

def get_best(galaxy_key: str) -> Optional[BestRoundEntry]:
    with _LOCK:
        return _REGISTRY.get(galaxy_key)


def has_best(galaxy_key: str) -> bool:
    with _LOCK:
        return galaxy_key in _REGISTRY


def set_best(galaxy_key: str, entry: BestRoundEntry) -> None:
    with _LOCK:
        _REGISTRY[galaxy_key] = entry


def clear() -> None:
    """Drop all remembered best rounds (used by tests)."""
    with _LOCK:
        _REGISTRY.clear()


# ── identity extraction (layout-agnostic) ─────────────────────────────────────

def extract_round_identity(
    image_path: str,
    summary_path: Optional[str] = None,
    lyric_file: Optional[str] = None,
) -> tuple[str, str, Optional[int], Optional[str]]:
    """Return ``(galaxy_key, round_label, round_number, object_name)``.

    ``galaxy_key`` is the galaxy main directory (parent of the ``output``/``archives``
    ancestor), which is filesystem-unique and stable across a galaxy's rounds for both
    the single-band (``archives/<hash>``) and multi-band (``output/<ts>_iterN``) layouts.
    """
    run_dir = os.path.abspath(os.path.dirname(image_path))
    round_label = os.path.basename(run_dir)
    m = _ITER_RE.search(round_label)
    round_number = int(m.group(1)) if m else None

    galaxy_key: Optional[str] = None
    cur = run_dir
    for _ in range(8):
        if os.path.basename(cur) in _RUNANCESTORS:
            galaxy_key = os.path.abspath(os.path.dirname(cur))
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    if galaxy_key is None:
        if lyric_file:
            galaxy_key = os.path.abspath(os.path.dirname(lyric_file))
        elif summary_path:
            galaxy_key = os.path.abspath(os.path.dirname(summary_path))
        else:
            galaxy_key = run_dir

    object_name: Optional[str] = None
    if lyric_file:
        try:
            object_name = parse_region_info_from_lyric(lyric_file).object
        except Exception:
            object_name = None
    if not object_name:
        object_name = os.path.basename(galaxy_key.rstrip(os.sep))

    return galaxy_key, round_label, round_number, object_name


def snapshot_current(
    image_path: str,
    summary_path: Optional[str],
    lyric_file: Optional[str] = None,
) -> BestRoundEntry:
    """Build a :class:`BestRoundEntry` for the current round (metrics best-effort)."""
    galaxy_key, round_label, round_number, object_name = extract_round_identity(
        image_path, summary_path, lyric_file
    )

    reduced_chisq: Optional[float] = None
    bic: Optional[float] = None
    try:
        from .run_galfits import _parse_gssummary
        stats = _parse_gssummary(summary_path) if summary_path else {}
        reduced_chisq = stats.get("reduced_chisq")
        bic = stats.get("bic")
    except Exception:
        pass

    return BestRoundEntry(
        galaxy_key=galaxy_key,
        round_label=round_label,
        round_number=round_number,
        object_name=object_name,
        image_path=os.path.abspath(image_path),
        summary_path=os.path.abspath(summary_path) if summary_path else "",
        reduced_chisq=reduced_chisq,
        bic=bic,
    )


# ── VLM comparison ────────────────────────────────────────────────────────────

def _build_reference_block(entry: BestRoundEntry) -> str:
    """Compact secondary-reference text: round id + metrics + summary excerpt."""
    parts: list[str] = []
    if entry.round_number is not None:
        parts.append(f"轮次 round {entry.round_number}（{entry.round_label}）")
    else:
        parts.append(f"轮次 {entry.round_label}")

    metrics = []
    if entry.reduced_chisq is not None:
        metrics.append(f"reduced chi2/nu={entry.reduced_chisq:.4f}")
    if entry.bic is not None:
        metrics.append(f"BIC={entry.bic:.2f}")
    if metrics:
        parts.append("指标：" + ("，".join(metrics)))

    excerpt = ""
    try:
        if entry.summary_path and os.path.exists(entry.summary_path):
            with open(entry.summary_path, encoding="utf-8", errors="ignore") as f:
                excerpt = f.read()
    except Exception:
        excerpt = ""
    if excerpt:
        excerpt = excerpt.strip()
        parts.append("summary 摘要：\n" + excerpt)
    return "\n".join(parts)


def parse_verdict(text: Optional[str]) -> str:
    """Extract the verdict token from VLM output, tolerant of formatting."""
    if not text:
        return "UNKNOWN"
    m = _VERDICT_FENCE_RE.search(text)
    if m:
        return m.group(1).upper()
    m = _VERDICT_LINE_RE.search(text)
    if m:
        return m.group(1).upper()
    found = [v for v in _VERDICT_VALUES if re.search(r"\b" + v + r"\b", text, re.IGNORECASE)]
    if len(found) == 1:
        return found[0]
    return "UNKNOWN"


def run_round_comparison(
    best: BestRoundEntry,
    current_image: str,
    current_summary: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """Ask the VLM to compare the historical best vs the current round.

    Returns ``(verdict, comparison_text, error)`` where ``verdict`` is one of
    ``CURRENT_BETTER`` / ``HISTORICAL_BETTER`` / ``EQUAL`` / ``UNKNOWN``.
    """
    _cur_galaxy, cur_label, _cur_round, _cur_obj = extract_round_identity(
        current_image, current_summary
    )
    best_ref = _build_reference_block(best)
    cur_ref = _build_reference_block(snapshot_current(current_image, current_summary))

    try:
        user_prompt = prompt.get_round_comparison_prompt(
            best_round_label=best.round_label,
            current_round_label=cur_label,
            best_reference=best_ref,
            current_reference=cur_ref,
        )
    except Exception as e:  # prompt render failure
        return "UNKNOWN", None, f"comparison prompt render failed: {e}"

    reference_blocks = [{
        "image": best.image_path,
        "caption": f"历史最优轮次（{best.round_label}）的对比图（原图/模型/2D残差/1D残差）",
    }]

    try:
        from .openai_analysis import run_openai_analysis
    except ImportError:
        return "UNKNOWN", None, "openai_analysis module unavailable"

    text, _session_id, err = run_openai_analysis(
        system_prompt=_COMPARISON_SYSTEM,
        analysis_prompts=[user_prompt],
        image_path=os.path.abspath(current_image),
        deferred_system=False,
        reference_blocks=reference_blocks,
        reference_intro=_COMPARISON_INTRO,
    )
    if err:
        return "UNKNOWN", text, err
    return parse_verdict(text), text, None


# ── orchestrator ──────────────────────────────────────────────────────────────

def update_best_round_for_call(
    image_path: str,
    summary_path: Optional[str],
    lyric_file: Optional[str] = None,
) -> Optional[dict]:
    """Maintain the best-round slot for the galaxy behind ``image_path``.

    Returns a small status dict, or ``None`` when tracking is disabled (so the caller
    can stay fully backward-compatible by skipping best-round return keys).
    """
    if os.environ.get("BEST_ROUND_TRACKING", "1") != "1":
        return None
    if not image_path:
        return None

    try:
        current = snapshot_current(image_path, summary_path, lyric_file)
    except Exception as e:
        print(f"[best_round] snapshot failed: {e}")
        return {"status": "ERROR", "best_round": None, "best_round_label": None,
                "verdict": None, "comparison_text": None}

    key = current.galaxy_key
    verbose = os.environ.get("BEST_ROUND_VERBOSE") == "1"
    result: dict = {"best_round_label": None, "best_round": None,
                    "verdict": None, "comparison_text": None}

    best = get_best(key)
    if best is None:
        set_best(key, current)
        result.update(status="INITIALIZED", best_round_label=current.round_label,
                      best_round=current.round_number)
        return result

    if os.path.abspath(best.image_path) == current.image_path:
        result.update(status="SAME_ROUND_NO_UPDATE", best_round_label=best.round_label,
                      best_round=best.round_number)
        return result

    verdict, text, err = run_round_comparison(best, image_path, summary_path)
    result["verdict"] = verdict
    if verbose:
        result["comparison_text"] = text
        if err:
            result["comparison_error"] = err

    best_abs = os.path.abspath(best.image_path)
    replaced = False
    with _LOCK:
        slot = _REGISTRY.get(key)
        if verdict == "CURRENT_BETTER" and slot is not None and \
                os.path.abspath(slot.image_path) == best_abs:
            _REGISTRY[key] = current
            replaced = True

    final = get_best(key) or best
    if replaced:
        status = "UPDATED"
    elif text is None:
        status = "RETAINED_NO_VLM"
    elif verdict == "UNKNOWN":
        status = "RETAINED_UNKNOWN"
    else:
        status = "RETAINED"
    result.update(status=status, best_round_label=final.round_label,
                  best_round=final.round_number)
    return result


def attach_analysis_to_best(image_path: str, analysis_md: Optional[str]) -> None:
    """If ``image_path`` is the recorded best round for its galaxy, store its analysis text."""
    if not image_path or analysis_md is None:
        return
    image_abs = os.path.abspath(image_path)
    with _LOCK:
        for entry in _REGISTRY.values():
            if os.path.abspath(entry.image_path) == image_abs:
                entry.analysis_md = analysis_md
                return
