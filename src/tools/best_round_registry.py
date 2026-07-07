"""Best-round registry for iterative component analysis.

Each call to ``component_analysis`` / ``analyze_multiband_components`` compares the
*current* fitting round against the best round seen so far for that galaxy. The
comparison is **visual-primary** (2D residual / 1D residual / component structure);
reduced chi-square, BIC and the summary's parameter/constraint conditions are passed
to the VLM only as secondary reference. The verdict comes from the VLM. When the VLM
cannot run, the historical best is **retained** (no metric-driven replacement).

The registry is keyed by the galaxy main directory (filesystem-unique). State is held
in-process AND persisted per-galaxy to ``<galaxy_main_dir>/.best_round.json`` so the
best round survives an MCP-server restart or session resume — otherwise the first
post-resume call would ``INITIALIZED`` from scratch and lose the whole comparison
history. Persistence is best-effort (a failed write/load never blocks the fit) and can
be disabled with ``BEST_ROUND_PERSIST=0`` (then the registry reverts to pure in-memory,
legacy behavior).
"""

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
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


# ── persistence (survives restart/resume) ─────────────────────────────────────

SCHEMA_VERSION = 1
_STATE_FILENAME = ".best_round.json"


def _persistence_enabled() -> bool:
    return os.environ.get("BEST_ROUND_PERSIST", "1") == "1"


def _state_path(galaxy_key: str) -> str:
    return os.path.join(galaxy_key, _STATE_FILENAME)


def _entry_to_dict(entry: "BestRoundEntry") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "galaxy_key": entry.galaxy_key,
        "round_label": entry.round_label,
        "round_number": entry.round_number,
        "object_name": entry.object_name,
        "image_path": entry.image_path,
        "summary_path": entry.summary_path,
        "reduced_chisq": entry.reduced_chisq,
        "bic": entry.bic,
        "analysis_md": entry.analysis_md,
    }


def _entry_from_dict(d: dict) -> Optional["BestRoundEntry"]:
    try:
        return BestRoundEntry(
            galaxy_key=d["galaxy_key"],
            round_label=d["round_label"],
            round_number=d.get("round_number"),
            object_name=d.get("object_name"),
            image_path=d["image_path"],
            summary_path=d.get("summary_path", ""),
            reduced_chisq=d.get("reduced_chisq"),
            bic=d.get("bic"),
            analysis_md=d.get("analysis_md"),
        )
    except (KeyError, TypeError):
        return None


def _persist_entry(entry: "BestRoundEntry") -> None:
    """Best-effort atomic write of the entry to its galaxy's state file.

    Failures (read-only galaxy dir, full disk, ...) are logged and swallowed: the
    in-memory registry is still updated, so a persist outage only costs resume safety,
    never the current session.
    """
    if not _persistence_enabled() or not entry.galaxy_key:
        return
    path = _state_path(entry.galaxy_key)
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        data = json.dumps(_entry_to_dict(entry), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(prefix=".best_round.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        print(f"[best_round] persist failed for {path}: {e}")


def _load_entry(galaxy_key: str) -> Optional["BestRoundEntry"]:
    """Best-effort read of the entry from its galaxy's state file."""
    if not _persistence_enabled() or not galaxy_key:
        return None
    path = _state_path(galaxy_key)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[best_round] load failed for {path}: {e}")
        return None
    if d.get("schema_version") != SCHEMA_VERSION:
        return None
    return _entry_from_dict(d)


# ── registry accessors ────────────────────────────────────────────────────────

def get_best(galaxy_key: str) -> Optional[BestRoundEntry]:
    with _LOCK:
        if galaxy_key in _REGISTRY:
            return _REGISTRY[galaxy_key]
    # Lazy-load from disk so the best round survives a restart/resume.
    entry = _load_entry(galaxy_key)
    if entry is None:
        return None
    with _LOCK:
        # Re-check under lock; a concurrent set_best may have populated it first.
        if galaxy_key not in _REGISTRY:
            _REGISTRY[galaxy_key] = entry
        return _REGISTRY[galaxy_key]


def has_best(galaxy_key: str) -> bool:
    return get_best(galaxy_key) is not None


def set_best(galaxy_key: str, entry: BestRoundEntry) -> None:
    with _LOCK:
        _set_best_unlocked(galaxy_key, entry)


def _set_best_unlocked(galaxy_key: str, entry: BestRoundEntry) -> None:
    """Update memory + persist. Caller must hold ``_LOCK`` (or call ``set_best``)."""
    _REGISTRY[galaxy_key] = entry
    _persist_entry(entry)


def clear() -> None:
    """Drop all in-memory best rounds (used by tests).

    Persisted ``.best_round.json`` files are *not* touched; use ``clear_persisted``
    to remove them.
    """
    with _LOCK:
        _REGISTRY.clear()


def clear_persisted(galaxy_key: Optional[str] = None) -> None:
    """Remove persisted best-round state file(s) from disk.

    With ``galaxy_key`` given, removes only that galaxy's file. Without it, removes
    files for all galaxies currently in memory (best-effort — galaxies whose state
    lives only on disk and was never loaded this session are not enumerated).
    """
    if galaxy_key:
        paths = [_state_path(galaxy_key)]
    else:
        with _LOCK:
            paths = [_state_path(k) for k in _REGISTRY]
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"[best_round] clear_persisted failed for {p}: {e}")


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


# ── structured comparison fields (for the regression conclusion) ──────────────

# Each field is emitted by the VLM as `label: value` on one line (only when the
# verdict is HISTORICAL_BETTER — see src/prompts/round_comparison.md).
_COMPARISON_FIELD_RES = {
    "regression_focus": re.compile(r"regression_focus\s*[:：]\s*(.+)", re.IGNORECASE),
    "salvage": re.compile(r"salvage\s*[:：]\s*(.+)", re.IGNORECASE),
    "direction": re.compile(r"direction\s*[:：]\s*(.+)", re.IGNORECASE),
}


def parse_comparison_fields(text: Optional[str]) -> dict:
    """Extract ``regression_focus`` / ``salvage`` / ``direction`` from the VLM output.

    Each value is taken to the end of its line. Missing fields are simply absent
    from the returned dict (the conclusion builder fills placeholders for those).
    """
    if not text:
        return {}
    out: dict[str, str] = {}
    for key, rx in _COMPARISON_FIELD_RES.items():
        m = rx.search(text)
        if m:
            out[key] = m.group(1).strip()
    return out


def _safe_delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def _metrics_summary(best: BestRoundEntry, current: BestRoundEntry) -> str:
    """One-line metric delta + whether metrics corroborate the visual REGRESSED call.

    The visual verdict HISTORICAL_BETTER means "current is worse"; a metric
    therefore *agrees* when Δ = current − best > 0 (current's value larger ⇒ worse
    fit for reduced χ² / BIC). Returns a self-contained Chinese line.
    """
    dchi = _safe_delta(current.reduced_chisq, best.reduced_chisq)
    dbic = _safe_delta(current.bic, best.bic)
    parts: list[str] = []
    signs: list[int] = []
    if dchi is not None:
        parts.append(f"Δχ²={dchi:+.4f}")
        signs.append(1 if dchi > 0 else -1)
    if dbic is not None:
        parts.append(f"ΔBIC={dbic:+.2f}")
        signs.append(1 if dbic > 0 else -1)
    if not parts:
        return "指标缺失（未解析到 reduced χ² / BIC）"
    if all(s == 1 for s in signs):
        agree = "一致"
    elif all(s == -1 for s in signs):
        agree = "冲突"
    else:
        agree = "部分冲突"
    return "、".join(parts) + f"（与视觉判据{agree}）"


def _build_regression_conclusion(
    best: BestRoundEntry,
    current: BestRoundEntry,
    fields: dict,
) -> str:
    """Assemble the soft 'reference' block injected into component_analysis on regression.

    This is a *reference opinion* (visual + metrics, may be wrong), not an order —
    the closing line asks component_analysis to adopt / revise / overrule it via its
    own analysis. Built only when the verdict is HISTORICAL_BETTER.
    """
    focus = fields.get("regression_focus") or "（对比未给出退步落点）"
    salvage = fields.get("salvage") or "无"
    direction = fields.get("direction") or "（对比未给出方向）"
    return (
        f"【最优轮对比·参考】当前轮相对历史最优轮（目录 {best.round_label}）退步。\n"
        f"- 指标：{_metrics_summary(best, current)}\n"
        f"- 退步落点（视觉）：{focus}\n"
        f"- 可保留局部：{salvage}\n"
        f"- 参考方向：{direction}\n"
        f"以上为对比的参考意见（视觉＋指标，可能偏差）。请结合本轮全面分析独立裁定最终方向"
        f"——可采纳、修正或否决（否决需说明）；阶段1 客观视觉提取与物理核验仍独立完成。"
    )


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


# ── comparison report (per-round trace artifact) ──────────────────────────────

_COMPARISON_REPORT_FILENAME = "best_round_comparison.md"


def _format_round_section(title: str, entry: BestRoundEntry) -> list[str]:
    """Render a ``## <title>`` block with one round's identity + metrics."""
    lines = [f"## {title}", ""]
    lines.append(f"- 目录：`{entry.round_label}`")
    if entry.round_number is not None:
        lines.append(f"- round：{entry.round_number}")
    if entry.object_name:
        lines.append(f"- 对象：{entry.object_name}")
    if entry.reduced_chisq is not None:
        lines.append(f"- reduced χ²/nu：{entry.reduced_chisq:.4f}")
    if entry.bic is not None:
        lines.append(f"- BIC：{entry.bic:.2f}")
    if entry.summary_path:
        lines.append(f"- summary：`{entry.summary_path}`")
    if entry.image_path:
        lines.append(f"- 对比图：`{entry.image_path}`")
    lines.append("")
    return lines


def _format_comparison_report(
    current: BestRoundEntry,
    best: BestRoundEntry,
    verdict: Optional[str],
    text: Optional[str],
    error: Optional[str],
    status: Optional[str],
    conclusion: Optional[str],
) -> str:
    """Assemble the human-readable cross-round comparison markdown."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = ["# 最优轮对比报告（best-round comparison）", ""]
    lines.append(f"- 生成时间：{ts}")
    lines.append(f"- 判定 verdict：{verdict or 'UNKNOWN'}")
    if status:
        lines.append(f"- 状态 status：{status}")
    lines.append(f"- {_metrics_summary(best, current)}")
    if error:
        lines.append(f"- 错误：{error}")
    lines.append("")
    lines.extend(_format_round_section("历史最优轮次（best）", best))
    lines.extend(_format_round_section("当前轮次（current）", current))
    if conclusion:
        lines.append("## 对比参考结论（仅退步时生成）")
        lines.append("")
        lines.append(conclusion)
        lines.append("")
    lines.append("## VLM 对比正文")
    lines.append("")
    body = text.strip() if text and text.strip() else "（无 VLM 输出）"
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


def _save_comparison_report(
    current: BestRoundEntry,
    best: BestRoundEntry,
    verdict: Optional[str],
    text: Optional[str],
    error: Optional[str],
    status: Optional[str],
    conclusion: Optional[str],
) -> Optional[str]:
    """Best-effort write of the cross-round comparison into the CURRENT round's dir.

    The VLM comparison (verdict + full reasoning) is otherwise held only in the
    in-memory result dict and lost when the call returns — writing it here makes
    each round's directory self-describing for backtracking ("why was this round
    judged better/worse than the historical best?"). Best-effort: a failed write
    is logged and swallowed, never blocking the fit. Honors ``BEST_ROUND_PERSIST``
    (off => no file, so unit tests and pure in-memory runs stay clean). Returns
    the report path on success, else ``None``.
    """
    if not _persistence_enabled():
        return None
    run_dir = os.path.dirname(current.image_path)
    if not run_dir:
        return None
    body = _format_comparison_report(
        current=current, best=best, verdict=verdict, text=text,
        error=error, status=status, conclusion=conclusion)
    path = os.path.join(run_dir, _COMPARISON_REPORT_FILENAME)
    try:
        os.makedirs(run_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".best_round_cmp.", suffix=".tmp", dir=run_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        print(f"[best_round] comparison report save failed for {path}: {e}")
        return None
    return path


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

    # On regression only, expose a soft "reference" conclusion for component_analysis
    # to weigh (visual + metric delta). IMPROVED / EQUAL carry no such conclusion.
    conclusion = None
    if verdict == "HISTORICAL_BETTER" and text:
        conclusion = _build_regression_conclusion(
            best, current, parse_comparison_fields(text))
        result["comparison_conclusion"] = conclusion

    best_abs = os.path.abspath(best.image_path)
    replaced = False
    with _LOCK:
        slot = _REGISTRY.get(key)
        if verdict == "CURRENT_BETTER" and slot is not None and \
                os.path.abspath(slot.image_path) == best_abs:
            _set_best_unlocked(key, current)
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

    # Persist the cross-round comparison (verdict + full VLM reasoning) into the
    # current round's directory; otherwise it dies with this call's result dict.
    report_path = _save_comparison_report(
        current=current, best=best, verdict=verdict, text=text,
        error=err, status=status, conclusion=conclusion)
    if report_path:
        result["comparison_report_path"] = report_path
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
                _persist_entry(entry)  # keep disk state in sync with the new analysis
                return
