"""S8 A2 layered adjudication over a full prepare run.

Verdicts follow the conventions frozen by the 16 confirmed pilot adjudications
(run ``evalset-20260916T064856Z``): direction first (expert-final membership of
each atomic change), then statistics (reduced chi-square / BIC deltas), then
confidence from evidence completeness. The deterministic rules below are
decision support for the executing agent; ambiguous items are forced to
audit/excluded rather than guessed. REFIT transitions are batch-audited per the
2026-09-17 ruling (reason code ``REFIT_POOL_AUDIT_V1``, never benchmark).
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .config import json_dump
from .pilot import is_benchmark_eligible_compound
from schemas import validate as schema_validate

# chi2_pct = (before - after) / before: positive means the fit improved.
_STRONG_CHI2_PCT = 0.10
_STRONG_BIC = -100.0
_CONFLICT_BIC = 100.0
_CONFLICT_CHI2_PCT = -0.10
_MULTI_HIGH_BIC = -1000.0
_COMPONENT_KEYWORDS = {
    "disk": ("disk", "expdisk", "指数盘", "盘成分"),
    "bulge": ("bulge", "核球"),
    "bar": ("bar", "棒"),
    "fourier_m1": ("fourier", "傅里叶", "m=1", "m1"),
    "agn": ("agn", "nucleus", "psf", "qso", "活动核"),
    "nucleus": ("agn", "nucleus", "psf", "活动核"),
    "companion": ("companion", "伴星系", "伴星"),
    "lens": ("lens", "透镜"),
    "edge_on_disk": ("edge-on", "edgeondisk", "侧向盘"),
    "single_sersic": ("single sersic", "单成分"),
    "unclassified_sersic": (),
}
_REMOVE_WORDS = ("remove", "delete", "删除", "移除")
_REPLACE_WORDS = ("replace", "convert", "改标", "替换", "改为", "回退", "转换", "提升")
_TABLE_ROW_RE = re.compile(r"^\s*\|?\s*\**\s*(Disk|Bulge|Bar|PSF|Nucleus|Sky|Companion|Lens|Fourier)\b[^\n]*\|", re.IGNORECASE | re.MULTILINE)
_LIST_ROW_RE = re.compile(r"(?:成分|component)\s*\d*\s*[\(（]\s*\**\s*(Disk|Bulge|Bar|PSF|Nucleus|Sky|Companion|Lens|Fourier)\b", re.IGNORECASE)
_TABLE_NAME_MAP = {"disk": "disk", "bulge": "bulge", "bar": "bar", "psf": "agn", "nucleus": "agn", "sky": None, "companion": "companion", "lens": "lens", "fourier": "fourier_m1"}


def decision_table_components(excerpt_text: str) -> list[str]:
    """Component names from the decision file's parameter table or bullet-style list."""
    names = [match.group(1) for match in _TABLE_ROW_RE.finditer(excerpt_text)]
    names += [match.group(1) for match in _LIST_ROW_RE.finditer(excerpt_text)]
    return sorted({name for name in (_TABLE_NAME_MAP.get(value.lower()) for value in names) if name})


def _stored_components(prelabel: dict[str, Any] | None) -> dict[str, list[str]]:
    """Component sets parsed at extraction time (with anchors); avoids adjudication-side re-parsing drift."""
    return (prelabel or {}).get("evidence_facts", {}).get("components", {})


def _post_set(candidate: dict[str, Any], prelabel: dict[str, Any] | None) -> set[str]:
    stored = _stored_components(prelabel).get("post")
    if stored is not None:
        return set(stored)
    if not candidate.get("post_action_round_id"):
        return set(candidate["source_components"])
    return set(round_components(candidate["evidence_refs"][1], candidate["mode"]))


def _decision_table_status(candidate: dict[str, Any], excerpt: dict[str, Any], prelabel: dict[str, Any] | None) -> str:
    """Compare the decision table's target structure against the parsed post config.

    Single-band disks are often modeled as plain Sersic (n≈1); without a config
    label the n-heuristic reads them as ``bulge``. A disagreement therefore only
    endangers the benchmark when the *atom's own* label is heuristic (bulge) or
    absent from the table; unrelated blocks may mislabel harmlessly.
    """
    if candidate["mode"] != "single_band" or excerpt["status"] != "PRESENT":
        return "NOT_APPLICABLE"
    table = decision_table_components(excerpt["excerpt"])
    if not table:
        return "NO_TABLE"
    post = sorted(_post_set(candidate, prelabel)) if candidate.get("post_action_round_id") else None
    return "MATCH" if post == table else "MISMATCH"


def _table_blocks_benchmark(action: dict[str, Any], table_components: list[str] | None) -> bool:
    """A table/parse mismatch blocks benchmark only if the atom labels themselves are at risk.

    ``bulge`` is the only heuristic sersic label (any unlabeled n≠0.5 Sersic);
    ``bar`` (n≈0.5), ``disk`` (expdisk / PROMOTE), ``agn`` (psf model) and
    ``fourier_m1`` (F1 block) are self-evident from the configs.
    """
    if not table_components:
        return False
    table = set(table_components)
    atoms = action.get("atomic_actions") or [action]
    for atom in atoms:
        label = atom.get("component") if atom["action_type"] != "PROPOSE_REPLACE" else atom.get("replace_to")
        if label in {None, "disk"}:
            continue
        if label == "bulge" or label not in table:
            return True
        if atom["action_type"] == "PROPOSE_REPLACE" and atom.get("replace_from") == "bulge":
            return True
    return False


def load_full_run(run_dir: Path) -> dict[str, Any]:
    candidates = [json.loads(line) for line in (run_dir / "evaluation-decision-candidates.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    prelabels = {item["sample_id"]: item for item in (json.loads(line) for line in (run_dir / "evaluation-action-prelabels.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    aliases_doc = json.loads((run_dir / "evaluation-aliases.json").read_text(encoding="utf-8"))
    alias_ids = {item["sample_id"] for item in aliases_doc.get("aliases", [])}
    return {"candidates": candidates, "prelabels": prelabels, "alias_ids": alias_ids}


def _structure_path(primary: Path, mode: str) -> Path:
    """Single-band rounds keep their input feedme next to the fitted output."""
    if mode == "single_band":
        feedmes = sorted(path for path in primary.parent.glob("*.feedme"))
        if feedmes:
            return feedmes[0]
    return primary


def round_components(primary_path: str, mode: str) -> list[str]:
    from .pilot import component_signature

    path = _structure_path(Path(primary_path), mode)
    try:
        return component_signature(path, mode)
    except OSError:
        return []


def _decision_files(primary_path: str) -> list[Path]:
    return sorted(Path(primary_path).parent.glob("*component_analysis_*.md"))


# Line-anchored decision-section headers only: the execution appendix references
# the decision mid-line as【调整决策】, which must not count as a section marker.
_DECISION_MARKER_RE = re.compile(r"^\s*[#*\-\s]*(本次调整决策|调整决策如下|adjustment decision|action plan|modification plan|决策如下|下一步动作|next step|本次调整)", re.IGNORECASE | re.MULTILINE)
# gadotti-0707-style rounds carry the generic execution-instruction boilerplate
# instead of a per-round decision; keyword hits there are false confirmations.
_BOILERPLATE_MARKERS = ("严禁私自", "一次只增减一个成分")


def decision_excerpt(primary_path: str, *, max_chars: int = 1600) -> dict[str, Any]:
    """Locate the round's decision markdown and extract text from the decision marker onward.

    The numbered action details usually live under ``# 1.``/``# 2.`` sub-headers
    right after the marker line, so slicing from the last marker keeps them in.
    """
    files = _decision_files(primary_path)
    if not files:
        return {"status": "ABSENT", "path": None, "excerpt": ""}
    excerpts: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        markers = list(_DECISION_MARKER_RE.finditer(text))
        # Some batches append the generic execution instructions AFTER the real
        # decision; cut the slice at the appendix start instead of discarding it.
        boilerplate_starts = [text.find(word) for word in _BOILERPLATE_MARKERS if text.find(word) != -1]
        cut = min(boilerplate_starts) if boilerplate_starts else len(text)
        if markers:
            marker = markers[-1]
            body = text[marker.start():min(marker.start() + max_chars, max(cut, marker.start()))]
            if len(body) < 80 and len(markers) > 1:
                body = text[markers[-2].start():min(markers[-2].start() + max_chars, cut)]
        else:
            # No recognizable marker: decisions live at the file end, not in
            # the opening visual-description phases.
            body = text[max(0, cut - 2 * max_chars):cut] if boilerplate_starts else text[-2 * max_chars:]
        excerpts.append(body[:max_chars])
    joined = "\n--\n".join(excerpts)
    if any(marker in joined for marker in _BOILERPLATE_MARKERS) and not decision_table_components(joined):
        return {"status": "ABSENT", "path": "|".join(str(p) for p in files), "excerpt": joined, "count": len(files), "boilerplate": True}
    return {"status": "PRESENT", "path": str(files[0]) if len(files) == 1 else "|".join(str(p) for p in files), "excerpt": joined, "count": len(files)}


def _atom_direction(atom: dict[str, Any], source: set[str], expert: set[str]) -> str:
    kind = atom["action_type"]
    if kind == "PROPOSE_ADD":
        return "toward" if atom.get("component") in expert and atom.get("component") not in source else "away"
    if kind == "PROPOSE_REMOVE":
        return "away" if atom.get("component") in expert else "toward"
    if kind == "PROPOSE_REPLACE":
        return "toward" if atom.get("replace_to") in expert else "away"
    if kind == "PROMOTE_SINGLE_SERSIC_TO_DISK":
        return "toward" if "disk" in expert else "away"
    return "unknown"


def _stats(prelabel: dict[str, Any] | None) -> dict[str, Any]:
    facts = (prelabel or {}).get("evidence_facts", {})
    chi2, bic = facts.get("reduced_chisq"), facts.get("bic")
    out: dict[str, Any] = {"chi2": chi2, "bic": bic, "class": "missing"}
    chi2_pct = bic_delta = None
    if chi2 and chi2.get("before") not in (None, 0):
        chi2_pct = (chi2["before"] - chi2["after"]) / chi2["before"]
    if bic:
        bic_delta = bic["delta"]
    out["chi2_pct"], out["bic_delta"] = chi2_pct, bic_delta
    strong = (chi2_pct is not None and chi2_pct >= _STRONG_CHI2_PCT) or (bic_delta is not None and bic_delta <= _STRONG_BIC)
    conflict = (bic_delta is not None and bic_delta >= _CONFLICT_BIC) or (chi2_pct is not None and chi2_pct <= _CONFLICT_CHI2_PCT)
    if strong and not conflict:
        out["class"] = "strong_improve"
    elif conflict and not strong:
        out["class"] = "conflict"
    elif (chi2_pct is not None and chi2_pct > 0) or (bic_delta is not None and bic_delta < 0):
        out["class"] = "improve"
    elif chi2_pct is not None or bic_delta is not None:
        out["class"] = "worsen"
    return out


def _decision_consistency(excerpt: dict[str, Any], action: dict[str, Any]) -> str:
    if excerpt["status"] == "ABSENT":
        return "ABSENT"
    text = excerpt["excerpt"].lower()
    kind = action["action_type"]
    if kind == "PROPOSE_ADD":
        keywords = _COMPONENT_KEYWORDS.get(action.get("component") or "", ())
        return "CONFIRMED" if keywords and any(word in text for word in keywords) else "NOMATCH"
    if kind == "PROPOSE_REMOVE":
        keywords = _COMPONENT_KEYWORDS.get(action.get("component") or (), ())
        has_action = any(word in text for word in _REMOVE_WORDS)
        has_target = not keywords or any(word in text for word in keywords)
        return "CONFIRMED" if has_action and has_target else "NOMATCH"
    if kind == "PROPOSE_REPLACE":
        keywords = _COMPONENT_KEYWORDS.get(action.get("replace_to") or "", ())
        has_action = any(word in text for word in _REPLACE_WORDS)
        return "CONFIRMED" if keywords and has_action and any(word in text for word in keywords) else "NOMATCH"
    if kind == "PROMOTE_SINGLE_SERSIC_TO_DISK":
        return "CONFIRMED" if any(word in text for word in ("expdisk", "disk", "指数盘", "盘")) else "NOMATCH"
    if kind == "COMPOUND":
        hits = 0
        for atom in action.get("atomic_actions", []):
            if _decision_consistency(excerpt, atom) == "CONFIRMED":
                hits += 1
        return "CONFIRMED" if hits else "NOMATCH"
    return "NOT_APPLICABLE"


def _best_round_linked(candidate: dict[str, Any]) -> bool:
    best = candidate.get("historical_best_round_id")
    if not best:
        return False
    for round_id in (candidate.get("post_action_round_id"), candidate.get("state_round_id")):
        if round_id and any(part.startswith(best) or best.startswith(part) for part in Path(round_id).parts):
            return True
    return False


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _atoms_match_structure(candidate: dict[str, Any], record: dict[str, Any], prelabel: dict[str, Any] | None = None) -> bool:
    """Benchmark invariant: atomic actions must equal the parsed structural delta."""
    if record["canonical_action"]["action_type"] == "CONVERGED" or not candidate.get("post_action_round_id"):
        return True
    post = _post_set(candidate, prelabel)
    pre = set(candidate["source_components"])
    added, removed = post - pre, pre - post
    atom_adds: set[Any] = set()
    atom_removes: set[Any] = set()
    for atom in record["canonical_action"].get("atomic_actions") or [record["canonical_action"]]:
        if atom["action_type"] == "PROPOSE_ADD":
            atom_adds.add(atom.get("component"))
        elif atom["action_type"] == "PROPOSE_REMOVE":
            atom_removes.add(atom.get("component"))
        elif atom["action_type"] == "PROPOSE_REPLACE":
            atom_adds.add(atom.get("replace_to"))
            atom_removes.add(atom.get("replace_from"))
        else:
            atom_adds.add("disk")
    atom_adds.discard(None)
    atom_removes.discard(None)
    atom_removes -= {"single_sersic", "unclassified_sersic"}
    return atom_adds == added and atom_removes <= removed


def adjudicate_candidate(candidate: dict[str, Any], prelabel: dict[str, Any] | None, *, alias: bool, decision: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic verdict per the pilot-confirmed conventions; never inflates confidence."""
    record = _adjudicate_inner(candidate, prelabel, alias=alias, decision=decision)
    if record["evaluation_pool"] == "benchmark" and not _atoms_match_structure(candidate, record, prelabel):
        record["confidence"] = "medium"
        record["evaluation_pool"] = "audit"
        record["verdict_reason_codes"] = sorted(set(record["verdict_reason_codes"] + ["ATOM_STRUCTURE_INCONSISTENT"]))
        record["adjudicator_note"] = (record["adjudicator_note"] or "") + "原子动作与前后轮解析结构差不一致，降级 audit。"
    if "edge_on_disk" in set(candidate["expert_final_components"]):
        # 2026-09-15 ruling: edge-on-disk objects never enter the core set, on every branch.
        if record["evaluation_pool"] == "benchmark":
            record["evaluation_pool"] = "audit"
        record["verdict_reason_codes"] = sorted(set(record["verdict_reason_codes"] + ["EDGE_ON_DISK_OBJECT_AUDIT_ONLY"]))
    if alias:
        if record["evaluation_pool"] == "benchmark":
            record["evaluation_pool"] = "audit"
        record["verdict_reason_codes"] = sorted(set(record["verdict_reason_codes"] + ["ALIAS_OF_CANONICAL_SAMPLE"]))
    return record


def _adjudicate_inner(candidate: dict[str, Any], prelabel: dict[str, Any] | None, *, alias: bool, decision: dict[str, Any] | None) -> dict[str, Any]:
    action = candidate["canonical_action"]
    kind = action["action_type"]
    expert = set(candidate["expert_final_components"])
    source = set(candidate["source_components"])
    post = _post_set(candidate, prelabel)
    d_i, d_j = len(source ^ expert), len(post ^ expert)
    stats = _stats(prelabel)
    edge_on = "edge_on_disk" in expert
    codes: list[str] = []
    chi2, bic = stats["chi2"], stats["bic"]

    if kind == "CONVERGED":
        if candidate.get("terminal_consistency") == "match":
            verdict = "CORRECT"
            # 2026-09-18 ruling: the 1D/2D residual gate is NOT an adjudication
            # criterion; terminal evidence = best-round designation + expert
            # match + fit health. (Visual residual verification stays reserved
            # as a future upgrade path and is deliberately not checked here.)
            best_chi2 = candidate.get("current_fit_health", {}).get("reduced_chisq")
            high = best_chi2 is not None and not alias and not edge_on
            confidence = "high" if high else "medium"
            codes = ["HISTORY_BEST_ROUND_MATCH_EXPERT_FINAL"]
            if best_chi2 is not None:
                codes.append(f"CHISQ_AT_BEST_{best_chi2:.3f}")
            pool = "benchmark" if high else "audit"
            note = "历史最佳轮与专家终态一致且拟合健康可得；残差视觉核验按 2026-09-18 裁定不作为判据。"
        else:
            verdict, confidence = "EXPLORATORY", "medium"
            codes = ["HISTORY_BEST_MISMATCH_EXPERT_FINAL"]
            note = "历史最佳轮成分与专家终态不一致，按规则不能判正确终止。"
            pool = "audit"
        refs = list(candidate["evidence_refs"])
        return _record(candidate, verdict, confidence, codes, refs, pool, note)

    if kind == "REFIT_PARAMETERS":
        if stats["class"] in {"strong_improve", "improve"}:
            verdict = "CORRECT"
        elif stats["class"] == "conflict":
            verdict = "INCONCLUSIVE"
        elif stats["class"] == "missing":
            verdict = "INCONCLUSIVE"
        else:
            verdict = "EXPLORATORY"
        codes = ["REFIT_POOL_AUDIT_V1"] + [code for code in ((prelabel or {}).get("reason_codes", [])) if code.startswith(("CHISQ", "BIC"))]
        note = f"REFIT 批量归 audit（§3-1 裁定，v1 不入 benchmark、未逐条裁决）；统计事实：chi2/nu { _fmt(chi2 and chi2['before']) }→{_fmt(chi2 and chi2['after'])}，BIC {_fmt(bic and bic['before'])}→{_fmt(bic and bic['after'])}。"
        return _record(candidate, verdict, "low", codes, list(candidate["evidence_refs"]), "audit", note)

    atoms = [action] if kind != "COMPOUND" else [atom for atom in action.get("atomic_actions", [])]
    directions = [_atom_direction(atom, source, expert) for atom in atoms]
    away = [atom for atom, direction in zip(atoms, directions) if direction == "away"]
    if chi2:
        codes.append(f"CHISQ_BEFORE_{chi2['before']:.3f}_AFTER_{chi2['after']:.3f}")
    if kind == "COMPOUND" and action.get("compound_reason") == "UNMAPPABLE_ATOMIC_CHANGE":
        verdict, confidence, pool = "INCONCLUSIVE", "medium", "excluded"
        codes = codes + ["UNMAPPABLE_ATOMIC_CHANGE"]
        note = "复合转换存在无法映射的原子变化（未分类成分变为非 Disk 或血缘不可追踪），按规则不判对错。"
        return _record(candidate, verdict, confidence, codes, list(candidate["evidence_refs"]), pool, note)

    stats_class = stats["class"]
    if away:
        if d_i - d_j > 0 and stats_class in {"strong_improve", "improve"}:
            verdict, confidence = "CORRECT", "medium"
            codes.append("MIXED_ATOMIC_DIRECTION")
            note = f"净结构距离下降（{d_i}→{d_j}）且统计改善，但含 {len(away)} 个偏离专家终态的原子，按 pilot 口径 medium 入 audit。"
            pool = "audit"
        elif stats_class == "conflict":
            verdict, confidence, pool = "INCONCLUSIVE", "medium", "excluded"
            codes.append("EVIDENCE_CONFLICT")
            note = "方向与统计证据冲突，不判对错。"
        else:
            verdict, confidence, pool = "EXPLORATORY", "medium", "audit"
            codes.append("ACTION_AWAY_FROM_EXPERT_FINAL")
            note = f"动作引入/保留专家终态之外的成分（{', '.join(sorted({(a.get('component') or a.get('replace_to') or '?') for a in away}))}），不能判正确。"
        return _record(candidate, verdict, confidence, codes, list(candidate["evidence_refs"]), pool, note)

    # All atoms point toward the expert final set.
    codes.append("ALL_ATOMS_TOWARD_EXPERT_FINAL" if kind == "COMPOUND" else "COMPONENT_IN_EXPERT_FINAL")
    if d_i - d_j <= 0 and kind != "PROMOTE_SINGLE_SERSIC_TO_DISK":
        codes.append("STRUCTURE_DISTANCE_NOT_REDUCED")
    if stats_class == "conflict":
        verdict, confidence, pool = "INCONCLUSIVE", "medium", "excluded"
        codes.append("EVIDENCE_CONFLICT")
        note = "方向正确但统计证据反向（BIC/chi2 显著恶化），按 pilot #11 口径不判对错。"
        return _record(candidate, verdict, confidence, codes, list(candidate["evidence_refs"]), pool, note)
    if stats_class == "missing":
        verdict, confidence, pool = "INCONCLUSIVE", "low", "excluded"
        codes.append("STATS_NOT_AVAILABLE")
        note = "动作前后 chi2/BIC 均不可得，证据不完整。"
        return _record(candidate, verdict, confidence, codes, list(candidate["evidence_refs"]), pool, note)

    verdict = "CORRECT"
    single_band = candidate["mode"] == "single_band"
    consistency = (decision or {}).get("consistency", "NOT_APPLICABLE")
    table_status = (decision or {}).get("table_status", "NOT_APPLICABLE")
    # Any genuine improvement (or a designated best round) completes the
    # evidence line; effect size alone does not cap confidence (design §7.6).
    improved = stats_class in {"strong_improve", "improve"} or _best_round_linked(candidate)
    table_blocking = table_status == "MISMATCH" and _table_blocks_benchmark(action, (decision or {}).get("table_components"))
    if single_band:
        high = improved and consistency == "CONFIRMED" and not table_blocking and stats["chi2"] is not None and not alias and not edge_on
    else:
        high = ((stats["bic_delta"] is not None and stats["bic_delta"] <= _MULTI_HIGH_BIC) or (stats["chi2_pct"] is not None and stats["chi2_pct"] >= _STRONG_CHI2_PCT)) and stats["chi2"] is not None and not alias and not edge_on
    if consistency == "CONFIRMED":
        codes.append("DECISION_MD_CONFIRMED")
    elif consistency == "ABSENT":
        codes.append("DECISION_MD_ABSENT")
    elif consistency == "NOMATCH":
        codes.append("DECISION_MD_NOMATCH")
    if table_blocking:
        codes.append("DECISION_TABLE_STRUCTURE_MISMATCH")
    elif table_status == "MISMATCH":
        codes.append("DECISION_TABLE_UNRELATED_MISMATCH")
    elif table_status == "MATCH":
        codes.append("DECISION_TABLE_STRUCTURE_MATCH")
    if _best_round_linked(candidate):
        codes.append("BEST_ROUND_DESIGNATED_EVIDENCE")
    if stats_class == "strong_improve":
        codes.append("STATS_STRONGLY_IMPROVED")
    confidence = "high" if high else "medium"
    eligible_pattern = kind != "COMPOUND" or is_benchmark_eligible_compound(action)
    pool = "benchmark" if confidence == "high" and eligible_pattern and not alias and not edge_on else "audit"
    if alias:
        codes.append("ALIAS_OF_CANONICAL_SAMPLE")
    if edge_on:
        codes.append("EDGE_ON_DISK_OBJECT_AUDIT_ONLY")
    if not eligible_pattern:
        codes.append("COMPOUND_PATTERN_NOT_BENCHMARK_ELIGIBLE")
    note_bits = [f"结构距离 {d_i}→{d_j}"]
    if chi2:
        note_bits.append(f"chi2/nu {chi2['before']:.3f}→{chi2['after']:.3f}")
    if bic:
        note_bits.append(f"BIC {bic['before']:.1f}→{bic['after']:.1f}")
    if single_band and consistency in {"CONFIRMED", "NOMATCH"}:
        note_bits.append(f"决策文件 {consistency}")
    if table_blocking:
        note_bits.append("决策参数表与后轮配置结构不一致（Sersic-disk／bulge 简并风险），不入 benchmark")
    elif table_status == "MISMATCH":
        table_set = set((decision or {}).get("table_components") or [])
        missing = sorted(table_set - post)
        extra = sorted(post - table_set)
        note_bits.append(f"决策参数表与后轮解析差集：表多 {missing}、解析多 {extra}（多为多步阶梯总目标 vs 立即后轮，或无标签块解析不确定），不影响本动作标签")
    note = "；".join(note_bits) + "。"
    return _record(candidate, verdict, confidence, codes, list(candidate["evidence_refs"]) + ([decision["path"]] if decision and decision.get("path") else []), pool, note)


def _record(candidate: dict[str, Any], verdict: str, confidence: str, codes: list[str], refs: list[str], pool: str, note: str) -> dict[str, Any]:
    return {
        "schema_version": "evaluation-action-adjudication@v1",
        "sample_id": candidate["sample_id"],
        "canonical_action": candidate["canonical_action"],
        "action_verdict": verdict,
        "confidence": confidence,
        "verdict_reason_codes": sorted(set(codes)),
        "evidence_refs": refs,
        "review_status": "adjudicated",
        "evaluation_pool": pool,
        "adjudicator_note": note,
    }


def _semantic_key(candidate: dict[str, Any], record: dict[str, Any]) -> tuple:
    """Decision-point key: cross-batch reruns of the same galaxy produce the same
    (state structure, action) pair with slightly different fitted values; content
    fingerprints do not merge them, so the benchmark dedups at this level
    (design doc 2026-09-20 ruling ⑨)."""
    return (candidate["mode"], candidate["object_id"], tuple(sorted(candidate["source_components"])), json.dumps(record["canonical_action"], sort_keys=True, ensure_ascii=False))


def _evidence_rank(record: dict[str, Any]) -> tuple[int, str]:
    codes = record["verdict_reason_codes"]
    score = 0
    if "STATS_STRONGLY_IMPROVED" in codes:
        score += 4
    if "BEST_ROUND_DESIGNATED_EVIDENCE" in codes:
        score += 2
    if "DECISION_MD_CONFIRMED" in codes:
        score += 2
    if "DECISION_TABLE_STRUCTURE_MATCH" in codes:
        score += 1
    return (score, record["sample_id"])


def _semantic_dedup(records: list[dict[str, Any]], candidates_by_id: dict[str, dict[str, Any]]) -> set[str]:
    """Keep the most evidence-complete record per semantic decision point; the
    rest are demoted to audit with ``SEMANTIC_DUPLICATE`` (never benchmark)."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for record in records:
        if record["evaluation_pool"] != "benchmark":
            continue
        groups.setdefault(_semantic_key(candidates_by_id[record["sample_id"]], record), []).append(record)
    demoted: set[str] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=_evidence_rank, reverse=True)
        for record in group[1:]:
            demoted.add(record["sample_id"])
            record["evaluation_pool"] = "audit"
            record["verdict_reason_codes"] = sorted(set(record["verdict_reason_codes"] + ["SEMANTIC_DUPLICATE"]))
            record["adjudicator_note"] = (record["adjudicator_note"] or "") + "同对象同状态结构同动作的重复演示，语义去重后归 audit。"
    return demoted


def adjudicate_run(run_dir: Path, *, batch_size: int = 500, seed: int = 20260917, redo: bool = False) -> dict[str, Any]:
    """Write missing adjudications incrementally; idempotent per sample_id.

    ``redo=True`` rewrites the file from scratch (used when the decision-text
    extraction rules change mid-task); it never touches anything but this file.
    New records are computed in memory first so the benchmark semantic-dedup
    pass can see every eligible record before anything is written.
    """
    data = load_full_run(run_dir)
    out_path = run_dir / "evaluation-action-adjudications.jsonl"
    existing: set[str] = set()
    if out_path.exists():
        existing = {json.loads(line)["sample_id"] for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    if redo:
        existing = set()
        out_path.write_text("", encoding="utf-8")

    fresh: list[dict[str, Any]] = []
    fresh_candidates: dict[str, dict[str, Any]] = {}
    for candidate in data["candidates"]:
        if candidate["sample_id"] in existing:
            continue
        decision = None
        if candidate["mode"] == "single_band" and candidate["canonical_action"]["action_type"] != "CONVERGED":
            excerpt = decision_excerpt(candidate["evidence_refs"][0])
            decision = {
                "consistency": _decision_consistency(excerpt, candidate["canonical_action"]),
                "table_status": _decision_table_status(candidate, excerpt, data["prelabels"].get(candidate["sample_id"])),
                "table_components": decision_table_components(excerpt["excerpt"]) if excerpt["status"] == "PRESENT" else None,
                "path": excerpt.get("path"),
            }
        record = adjudicate_candidate(
            candidate,
            data["prelabels"].get(candidate["sample_id"]),
            alias=candidate["sample_id"] in data["alias_ids"],
            decision=decision,
        )
        fresh.append(record)
        fresh_candidates[candidate["sample_id"]] = candidate
    _semantic_dedup(fresh, fresh_candidates)

    batch: list[dict[str, Any]] = []
    for start in range(0, len(fresh), batch_size):
        batch = fresh[start:start + batch_size]
        for record in batch:
            schema_validate(record, "evaluation_action_adjudication")
        with out_path.open("a", encoding="utf-8") as handle:
            for record in batch:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    records = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = _summarize(records)
    json_dump(run_dir / "a2-adjudication-counts.json", summary)
    _write_qc_samples(run_dir, data, records, seed=seed)
    _write_summary_md(run_dir, summary)
    return {"records": len(records), "summary": summary}


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_action: dict[str, Counter] = {}
    for record in records:
        kind = record["canonical_action"]["action_type"]
        by_action.setdefault(kind, Counter())[(record["action_verdict"], record["confidence"], record["evaluation_pool"])] += 1
    return {
        "schema_version": "evaluation-a2-counts@v1",
        "total": len(records),
        "by_action": {kind: {"|".join(key): value for key, value in sorted(counter.items())} for kind, counter in sorted(by_action.items())},
        "pool_totals": dict(Counter(record["evaluation_pool"] for record in records)),
        "verdict_totals": dict(Counter(record["action_verdict"] for record in records)),
    }


def _sample(records: list[dict[str, Any]], kind: str, count: int, rng: random.Random) -> list[dict[str, Any]]:
    pool = [record for record in records if record["canonical_action"]["action_type"] == kind]
    return rng.sample(pool, min(count, len(pool)))


def _write_qc_samples(run_dir: Path, data: dict[str, Any], records: list[dict[str, Any]], *, seed: int) -> None:
    rng = random.Random(seed)
    candidates = {candidate["sample_id"]: candidate for candidate in data["candidates"]}
    qc_dir = run_dir / "a2-qc-samples"
    qc_dir.mkdir(exist_ok=True)
    lines = ["# A2 抽样质检包（小鱼儿复核用）", "", f"随机种子 {seed}；每动作类型抽 ≥20 条（不足全取）。", ""]
    for kind in sorted({record["canonical_action"]["action_type"] for record in records}):
        picked = _sample(records, kind, 20, rng)
        rows = ["| sample_id | verdict | conf | pool | action | 关键 reason codes |", "|---|---|---|---|---|---|"]
        for record in picked:
            action = record["canonical_action"]
            label = action["action_type"] if action["action_type"] != "COMPOUND" else "COMPOUND[" + "+".join(atom["action_type"] for atom in action.get("atomic_actions", [])) + "]"
            rows.append(f"| {record['sample_id']} | {record['action_verdict']} | {record['confidence']} | {record['evaluation_pool']} | {label} | {', '.join(record['verdict_reason_codes'][:4])} |")
        (qc_dir / f"action-{kind}.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        lines.append(f"- `{kind}`：{len(picked)} 条 → `a2-qc-samples/action-{kind}.md`")
    (qc_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    single_band_transitions = [
        record for record in records
        if record["sample_id"].startswith("full-single_band-") and not record["sample_id"].endswith("-cv") and record["canonical_action"]["action_type"] != "REFIT_PARAMETERS"
    ]
    picked = rng.sample(single_band_transitions, min(len(single_band_transitions), max(1, len(single_band_transitions) * 20 // 100)))
    verify_rows = ["# 单波段决策文件抽查（20%，adjudication-guide 流程）", "", "逐条：读状态轮决策文件的调整决策段，核对与推断动作及裁决是否一致。", ""]
    for record in picked:
        candidate = candidates[record["sample_id"]]
        excerpt = decision_excerpt(candidate["evidence_refs"][0])
        verify_rows += [
            f"## {record['sample_id']}",
            f"- action: `{json.dumps(record['canonical_action'], ensure_ascii=False)}`；verdict {record['action_verdict']}/{record['confidence']}/{record['evaluation_pool']}",
            f"- 决策文件：{excerpt.get('path')}",
            "```text", excerpt.get("excerpt", "")[:1200], "```", "",
        ]
    (qc_dir / "single-band-decision-verification-20pct.md").write_text("\n".join(verify_rows) + "\n", encoding="utf-8")

    refit = [record for record in records if record["canonical_action"]["action_type"] == "REFIT_PARAMETERS"]
    picked = rng.sample(refit, min(len(refit), max(1, len(refit) * 5 // 100)))
    rows = ["| sample_id | verdict | note |", "|---|---|---|"]
    for record in picked:
        rows.append(f"| {record['sample_id']} | {record['action_verdict']} | {record['adjudicator_note']} |")
    (qc_dir / "refit-sample-5pct.md").write_text("# L3 REFIT 抽检（5%）\n\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _write_summary_md(run_dir: Path, summary: dict[str, Any]) -> None:
    lines = ["# A2 分层裁决汇总", "", f"总记录 {summary['total']}；池分布 {summary['pool_totals']}；verdict 分布 {summary['verdict_totals']}。", ""]
    for kind, counter in summary["by_action"].items():
        lines.append(f"## {kind}")
        lines.append("| verdict/conf/pool | count |")
        lines.append("|---|---|")
        for key, value in counter.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")
    lines.append("REFIT 按 §3-1 裁定批量归 audit（REFIT_POOL_AUDIT_V1）；单波段 20% 决策文件抽查与每动作类型 ≥20 条质检样本见 `a2-qc-samples/`。")
    (run_dir / "a2-adjudication-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
