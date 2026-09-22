"""Focused tests for the S8 A2 adjudication rules (pilot-confirmed conventions)."""

from __future__ import annotations

import json
from pathlib import Path

from component_analysis.evalset.adjudication_full import adjudicate_candidate


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _candidate(tmp_path: Path, mode: str, before: str, after: str, action: dict, expert: list[str], source: list[str] | None = None, chi2: tuple[float, float] | None = None, bic: tuple[float, float] | None = None, best: str | None = None, consistency_round: bool = True) -> dict:
    if mode == "single_band":
        before_dir = tmp_path / "r1"
        after_dir = tmp_path / "r2"
        before_path = _write(before_dir / "galfit.01", before)
        after_path = _write(after_dir / "galfit.02", after)
        if consistency_round:
            _write(before_dir / "obj_x_comparison_component_analysis_abc.md", "## 调整决策\n add a fourier component to the disk\n")
    else:
        before_path = _write(tmp_path / "a.lyric", before)
        after_path = _write(tmp_path / "b.lyric", after)
    prelabel = {"evidence_facts": {}}
    if chi2:
        prelabel["evidence_facts"]["reduced_chisq"] = {"before": chi2[0], "after": chi2[1], "delta": chi2[1] - chi2[0]}
    if bic:
        prelabel["evidence_facts"]["bic"] = {"before": bic[0], "after": bic[1], "delta": bic[1] - bic[0]}
    return {
        "schema_version": "evaluation-decision-candidate@v1",
        "sample_id": "full-test-01",
        "mode": mode,
        "evidence_refs": [str(before_path), str(after_path)],
        "post_action_round_id": "r2/galfit.02",
        "state_round_id": "r1/galfit.01",
        "source_components": source if source is not None else [],
        "expert_final_components": expert,
        "canonical_action": action,
        "historical_best_round_id": best,
        "terminal_consistency": "unresolved",
    }


_SINGLE_BEFORE = "0) sersic\n"
_SINGLE_AFTER = "0) sersic\nF1) 0.2 45 1 1\n"
_ADD_FOURIER = {"action_type": "PROPOSE_ADD", "component": "fourier_m1", "replace_from": None, "replace_to": None}


def test_single_band_add_strong_stats_with_decision_md_is_benchmark_high(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "single_band", _SINGLE_BEFORE, _SINGLE_AFTER, _ADD_FOURIER, ["single_sersic", "fourier_m1"], source=["single_sersic"], chi2=(2.43, 1.65))
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 2.43, "after": 1.65, "delta": -0.78}}}, alias=False, decision={"consistency": "CONFIRMED", "path": "md"})
    assert (record["action_verdict"], record["confidence"], record["evaluation_pool"]) == ("CORRECT", "high", "benchmark")
    assert "DECISION_MD_CONFIRMED" in record["verdict_reason_codes"]


def test_single_band_add_strong_stats_without_decision_confirm_is_medium(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "single_band", _SINGLE_BEFORE, _SINGLE_AFTER, _ADD_FOURIER, ["single_sersic", "fourier_m1"], source=["single_sersic"], chi2=(2.43, 1.65), consistency_round=False)
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 2.43, "after": 1.65, "delta": -0.78}}}, alias=False, decision={"consistency": "ABSENT", "path": None})
    assert (record["action_verdict"], record["confidence"], record["evaluation_pool"]) == ("CORRECT", "medium", "audit")


def test_elliptical_promote_is_exploratory(tmp_path: Path) -> None:
    before, after = "0) sersic\n", "0) expdisk\n"
    action = {"action_type": "PROMOTE_SINGLE_SERSIC_TO_DISK", "component": "disk", "replace_from": None, "replace_to": None}
    candidate = _candidate(tmp_path, "single_band", before, after, action, ["single_sersic"], source=["single_sersic"], chi2=(0.897, 0.891))
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 0.897, "after": 0.891, "delta": -0.006}}}, alias=False, decision={"consistency": "CONFIRMED", "path": "md"})
    assert (record["action_verdict"], record["evaluation_pool"]) == ("EXPLORATORY", "audit")


def test_converged_match_with_fit_health_is_benchmark_high(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "multi_band", "Pa1) disk\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\n", {"action_type": "CONVERGED", "component": None, "replace_from": None, "replace_to": None}, ["disk"], source=["disk"], best="r1", chi2=(0.365, 0.365))
    candidate["terminal_consistency"] = "match"
    candidate["current_fit_health"] = {"fit_converged": None, "bic": None, "reduced_chisq": 0.365, "residual_flags": [], "parameter_flags": [], "constraint_flags": [], "data_quality_flags": []}
    record = adjudicate_candidate(candidate, None, alias=False, decision=None)
    assert (record["action_verdict"], record["confidence"], record["evaluation_pool"]) == ("CORRECT", "high", "benchmark")
    assert "TERMINAL_RESIDUAL_EVIDENCE_NOT_VERIFIED" not in record["verdict_reason_codes"]


def test_converged_mismatch_is_exploratory(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "multi_band", "Pa1) disk\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\n", {"action_type": "CONVERGED", "component": None, "replace_from": None, "replace_to": None}, ["disk", "agn"], source=["disk"], best="r1")
    candidate["terminal_consistency"] = "mismatch"
    record = adjudicate_candidate(candidate, None, alias=False, decision=None)
    assert (record["action_verdict"], record["evaluation_pool"]) == ("EXPLORATORY", "audit")


def test_refit_batch_record_is_audit_low(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "multi_band", "Pa1) disk\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\n", {"action_type": "REFIT_PARAMETERS", "component": None, "replace_from": None, "replace_to": None}, ["disk"], source=["disk"], chi2=(0.337, 0.330))
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 0.337, "after": 0.330, "delta": -0.007}}}, alias=False, decision=None)
    assert record["evaluation_pool"] == "audit" and record["confidence"] == "low"
    assert record["verdict_reason_codes"][0] == "REFIT_POOL_AUDIT_V1" or "REFIT_POOL_AUDIT_V1" in record["verdict_reason_codes"]


def test_unmappable_compound_is_inconclusive_excluded(tmp_path: Path) -> None:
    action = {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": [], "compound_reason": "UNMAPPABLE_ATOMIC_CHANGE"}
    candidate = _candidate(tmp_path, "multi_band", "Pa1) obj0\nPa2) sersic\n", "Pa1) bulge\nPa2) sersic\n", action, ["disk"], source=["unclassified_sersic"])
    record = adjudicate_candidate(candidate, None, alias=False, decision=None)
    assert (record["action_verdict"], record["evaluation_pool"]) == ("INCONCLUSIVE", "excluded")


def test_direction_ok_but_bic_conflict_is_inconclusive(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "multi_band", "Pa1) disk\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\nPb1) agn\nPb2) sersic\n", {"action_type": "PROPOSE_ADD", "component": "agn", "replace_from": None, "replace_to": None}, ["disk", "agn"], source=["disk"], chi2=(0.368, 0.368), bic=(1000.0, 1303.0))
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 0.368, "after": 0.368, "delta": 0.0}, "bic": {"before": 1000.0, "after": 1303.0, "delta": 303.0}}}, alias=False, decision=None)
    assert (record["action_verdict"], record["evaluation_pool"]) == ("INCONCLUSIVE", "excluded")
    assert "EVIDENCE_CONFLICT" in record["verdict_reason_codes"]


def test_multiband_eligible_compound_strong_bic_is_benchmark_high(tmp_path: Path) -> None:
    action = {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": [
        {"action_type": "PROMOTE_SINGLE_SERSIC_TO_DISK", "component": "disk", "replace_from": None, "replace_to": None},
        {"action_type": "PROPOSE_ADD", "component": "bulge", "replace_from": None, "replace_to": None},
    ], "compound_reason": "NO_INTERMEDIATE_ROUND"}
    candidate = _candidate(tmp_path, "multi_band", "Pa1) obj0\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\nPb1) bulge\nPb2) sersic\n", action, ["disk", "bulge"], source=["unclassified_sersic"], chi2=(0.811, 0.678), bic=(18261.0, 0.0))
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 0.811, "after": 0.678, "delta": -0.133}, "bic": {"before": 18261.0, "after": 0.0, "delta": -18261.0}}}, alias=False, decision=None)
    assert (record["action_verdict"], record["confidence"], record["evaluation_pool"]) == ("CORRECT", "high", "benchmark")


def test_alias_and_edge_on_objects_never_enter_benchmark(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "multi_band", "Pa1) disk\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\nPb1) bulge\nPb2) sersic\n",
                           {"action_type": "PROPOSE_ADD", "component": "bulge", "replace_from": None, "replace_to": None}, ["disk", "bulge"], source=["disk"], chi2=(0.8, 0.6), bic=(10000, 0))
    prelabel = {"evidence_facts": {"reduced_chisq": {"before": 0.8, "after": 0.6, "delta": -0.2}, "bic": {"before": 10000.0, "after": 0.0, "delta": -10000.0}}}
    alias_record = adjudicate_candidate(candidate, prelabel, alias=True, decision=None)
    assert alias_record["evaluation_pool"] == "audit" and "ALIAS_OF_CANONICAL_SAMPLE" in alias_record["verdict_reason_codes"]
    edge_record = adjudicate_candidate(candidate, prelabel, alias=False, decision=None)
    edge_record  # sanity: base case benchmark
    assert edge_record["evaluation_pool"] == "benchmark"
    candidate["expert_final_components"] = ["disk", "edge_on_disk"]
    edge_only = adjudicate_candidate(candidate, prelabel, alias=False, decision=None)
    assert edge_only["evaluation_pool"] == "audit" and "EDGE_ON_DISK_OBJECT_AUDIT_ONLY" in edge_only["verdict_reason_codes"]


def test_single_band_decision_table_mismatch_blocks_benchmark(tmp_path: Path) -> None:
    # Decision table targets {bulge, disk} but the post Sersic-disk parses as a second bulge.
    before_dir = tmp_path / "r1"
    after_dir = tmp_path / "r2"
    before_path = _write(before_dir / "galfit.01", "0) sersic\n")
    _write(after_dir / "galfit.feedme", "# Object number: 1\n0) sersic\n5) 4.0 1\n# Object number: 2\n0) sersic\n5) 1.0 1\n# Object number: 3\n0) sky\n")
    after_path = _write(after_dir / "galfit.02", "# Component number: 1\n0) sersic\n5) 3.9\n# Component number: 2\n0) sersic\n5) 1.1\n")
    _write(before_dir / "obj_x_comparison_component_analysis_abc.md", "## 调整决策\n| 成分 | x | y | n |\n| --- | --- | --- | --- |\n| Bulge | 1 | 1 | 4.0 |\n| Disk (Sersic) | 1 | 1 | 1.0 |\n")
    candidate = {
        "schema_version": "evaluation-decision-candidate@v1",
        "sample_id": "full-test-02",
        "mode": "single_band",
        "evidence_refs": [str(before_path), str(after_path)],
        "post_action_round_id": "r2/galfit.02",
        "state_round_id": "r1/galfit.01",
        "source_components": ["single_sersic"],
        "expert_final_components": ["bulge", "disk"],
        "canonical_action": {"action_type": "PROPOSE_ADD", "component": "bulge", "replace_from": None, "replace_to": None},
        "historical_best_round_id": None,
        "terminal_consistency": "unresolved",
    }
    prelabel = {"evidence_facts": {"reduced_chisq": {"before": 2.0, "after": 1.0, "delta": -1.0}}}
    decision = {"consistency": "CONFIRMED", "table_status": "MISMATCH", "table_components": ["bulge", "disk"], "path": "md"}
    record = adjudicate_candidate(candidate, prelabel, alias=False, decision=decision)
    assert (record["action_verdict"], record["confidence"], record["evaluation_pool"]) == ("CORRECT", "medium", "audit")
    assert "DECISION_TABLE_STRUCTURE_MISMATCH" in record["verdict_reason_codes"]


def test_away_atom_with_net_improvement_is_medium_audit(tmp_path: Path) -> None:
    action = {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": [
        {"action_type": "PROMOTE_SINGLE_SERSIC_TO_DISK", "component": "disk", "replace_from": None, "replace_to": None},
        {"action_type": "PROPOSE_ADD", "component": "bulge", "replace_from": None, "replace_to": None},
        {"action_type": "PROPOSE_ADD", "component": "companion", "replace_from": None, "replace_to": None},
    ], "compound_reason": "NO_INTERMEDIATE_ROUND"}
    candidate = _candidate(tmp_path, "multi_band", "Pa1) obj0\nPa2) sersic\n", "Pa1) disk\nPa2) sersic\nPb1) bulge\nPb2) sersic\nPc1) companion\nPc2) sersic\n", action, ["disk", "bulge"], source=["unclassified_sersic"], chi2=(2.5, 2.2))
    record = adjudicate_candidate(candidate, {"evidence_facts": {"reduced_chisq": {"before": 2.5, "after": 2.2, "delta": -0.3}}}, alias=False, decision=None)
    assert (record["action_verdict"], record["confidence"], record["evaluation_pool"]) == ("CORRECT", "medium", "audit")
    assert "MIXED_ATOMIC_DIRECTION" in record["verdict_reason_codes"]


def test_semantic_dedup_keeps_one_benchmark_entry_per_decision_point() -> None:
    from component_analysis.evalset.adjudication_full import _semantic_dedup
    record_a = {"sample_id": "full-s-a-01", "evaluation_pool": "benchmark", "canonical_action": _ADD_FOURIER,
                "action_verdict": "CORRECT", "confidence": "high", "verdict_reason_codes": ["STATS_STRONGLY_IMPROVED"], "adjudicator_note": ""}
    record_b = {"sample_id": "full-s-a-02", "evaluation_pool": "benchmark", "canonical_action": _ADD_FOURIER,
                "action_verdict": "CORRECT", "confidence": "high", "verdict_reason_codes": ["DECISION_MD_CONFIRMED"], "adjudicator_note": ""}
    candidates = {
        "full-s-a-01": {"mode": "single_band", "object_id": "obj1", "source_components": ["single_sersic"]},
        "full-s-a-02": {"mode": "single_band", "object_id": "obj1", "source_components": ["single_sersic"]},
    }
    demoted = _semantic_dedup([record_a, record_b], candidates)
    assert demoted == {"full-s-a-02"}  # strong stats outranks decision-md-only
    assert record_b["evaluation_pool"] == "audit" and "SEMANTIC_DUPLICATE" in record_b["verdict_reason_codes"]
    assert record_a["evaluation_pool"] == "benchmark"
