"""Deterministic re-validation of a pilot run (S6 ``validate-pilot`` mode).

Re-checks schemas, leakage, canonical action handling, admission gates, and
same-config reproducibility without touching the historical archives.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from . import pilot as pilot_module
from .config import json_dump
from .leakage import check_manifests
from .pilot import is_benchmark_eligible_compound
from schemas import validate as schema_validate

_CANDIDATE_SCHEMA = "evaluation_decision_candidate"
_PRELABEL_SCHEMA = "evaluation_action_prelabel"
_MANIFEST_SCHEMA = "evaluation_state_manifest"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_pilot_run(run_dir: Path, selection: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    candidates = _load_jsonl(run_dir / "evaluation-decision-candidates.jsonl")
    prelabels = _load_jsonl(run_dir / "evaluation-action-prelabels.jsonl")
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_dir / "evaluation-state-manifests").glob("*.json"))]

    for candidate in candidates:
        schema_validate(candidate, _CANDIDATE_SCHEMA)
    for prelabel in prelabels:
        schema_validate(prelabel, _PRELABEL_SCHEMA)
    for manifest in manifests:
        schema_validate(manifest, _MANIFEST_SCHEMA)
    schema_checks = {"candidates": len(candidates), "prelabels": len(prelabels), "manifests": len(manifests), "status": "PASS"}

    stored_leakage = json.loads((run_dir / "leakage-report.json").read_text(encoding="utf-8"))
    recomputed_leakage = check_manifests(manifests)
    leakage_match = recomputed_leakage["violations"] == stored_leakage.get("violations")
    if recomputed_leakage["status"] != "PASS" or not leakage_match:
        failures.append(f"leakage re-check mismatch: stored={stored_leakage.get('status')} recomputed={recomputed_leakage['status']}")

    for candidate in candidates:
        action = candidate["canonical_action"]
        if action["action_type"] == "COMPOUND":
            if not action.get("atomic_actions") and action.get("compound_reason") != "UNMAPPABLE_ATOMIC_CHANGE":
                # UNMAPPABLE compounds may carry an empty atom list by design
                # (unclassified -> non-disk change); they stay INCONCLUSIVE/excluded.
                failures.append(f"compound candidate without atomic actions: {candidate['sample_id']}")
            if candidate.get("evaluation_pool") == "benchmark" and not is_benchmark_eligible_compound(action):
                failures.append(f"compound candidate assigned to benchmark pool without an eligible two-atom pattern: {candidate['sample_id']}")
        if action["action_type"] == "CONVERGED":
            if not candidate.get("historical_best_round_id"):
                failures.append(f"CONVERGED candidate without historical best round: {candidate['sample_id']}")
            if candidate.get("terminal_consistency") == "unresolved":
                failures.append(f"CONVERGED candidate with unresolved terminal consistency: {candidate['sample_id']}")
        if candidate.get("review_status") != "pending" or candidate.get("evaluation_pool") == "benchmark":
            failures.append(f"admission gate violation (auto-signed label): {candidate['sample_id']}")
    for prelabel in prelabels:
        if prelabel.get("prelabel_status") != "PRELABEL_ONLY" or prelabel.get("pre_action_necessity") not in {"unknown", "supported", "unsupported"}:
            failures.append(f"invalid prelabel status: {prelabel['sample_id']}")

    adjudications_path = run_dir / "evaluation-action-adjudications.jsonl"
    if adjudications_path.exists():
        adjudications = _load_jsonl(adjudications_path)
        for record in adjudications:
            schema_validate(record, "evaluation_action_adjudication")
            if record.get("evaluation_pool") == "benchmark":
                action = record["canonical_action"]
                eligible = record.get("action_verdict") == "CORRECT" and record.get("confidence") == "high" and (
                    action["action_type"] != "COMPOUND" or is_benchmark_eligible_compound(action)
                )
                if not eligible:
                    failures.append(f"benchmark-pool adjudication without CORRECT+high eligibility: {record['sample_id']}")

    run_config = json.loads((run_dir / "run-config.json").read_text(encoding="utf-8"))
    is_full = run_config.get("mode") == "full"
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = pilot_module.build_pilot(
            selection,
            inventory,
            Path(tmp),
            {"run_id": "validate-replay"},
            run_kind="full" if is_full else "pilot",
            min_rounds=1 if is_full else 2,
            require_inputs=is_full,
        )
        replay_equal = [c for c in rebuilt["candidates"]] == candidates
    if not replay_equal:
        failures.append("same-config re-extraction differs from stored candidates (repeatability failure)")

    action_counts = Counter(candidate["canonical_action"]["action_type"] for candidate in candidates)
    mode_counts = Counter(candidate["mode"] for candidate in candidates)
    object_counts = Counter((candidate["mode"], candidate["object_id"]) for candidate in candidates)
    report = {
        "schema_version": "evaluation-pilot-validation-report@v1",
        "run_id": run_config["run_id"],
        "schema_checks": schema_checks,
        "leakage_recheck": {"status": recomputed_leakage["status"], "violations": len(recomputed_leakage["violations"]), "matches_stored": leakage_match},
        "admission_gate": {"status": "PASS" if not any("admission gate" in f for f in failures) else "FAIL"},
        "repeatability": {"status": "PASS" if replay_equal else "FAIL"},
        "counts": {"by_mode": dict(mode_counts), "by_action": dict(action_counts), "by_object": {f"{m}:{o}": n for (m, o), n in sorted(object_counts.items())}},
        "failures": failures,
        "warnings": warnings,
        "status": "PASS" if not failures else "FAIL",
    }
    json_dump(run_dir / "pilot-validation-report.json", report)
    return report
