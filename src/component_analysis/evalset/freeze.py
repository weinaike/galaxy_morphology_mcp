"""S8 A3 freeze: build the three frozen pools from verified adjudications.

Consumes only records that passed validate-full; the benchmark admission gates
(CORRECT+high, eligible compound pattern, semantic-decision-point uniqueness)
are re-checked here as a machine backstop before anything is written.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adjudication_full import _semantic_key
from .config import json_dump, sha256_file
from .pilot import is_benchmark_eligible_compound
from schemas import validate as schema_validate


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parents[2]).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _merged_sample(candidate: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: candidate[key] for key in (
            "sample_id", "dataset_id", "object_id", "mode", "state_round_id", "post_action_round_id",
            "source_components", "expert_final_components", "current_fit_health", "historical_action_raw",
            "canonical_action", "evidence_refs", "terminal_consistency", "historical_best_round_id",
        ) if key in candidate},
        "schema_version": "evaluation-set-sample@v1",
        "input_cutoff": candidate["state_round_id"],
        "state_manifest": f"evaluation-state-manifests/{candidate['sample_id']}.json",
        "action_verdict": record["action_verdict"],
        "confidence": record["confidence"],
        "verdict_reason_codes": record["verdict_reason_codes"],
        "review_status": record["review_status"],
        "evaluation_pool": record["evaluation_pool"],
    }


def freeze_full(run_dir: Path, inventory_path: Path, *, approved_by: str = "小鱼儿") -> dict[str, Any]:
    run_dir = Path(run_dir)
    run_config = json.loads((run_dir / "run-config.json").read_text(encoding="utf-8"))
    if run_config.get("mode") != "full":
        raise ValueError(f"not a full run: {run_dir}")
    status = json.loads((run_dir / "run-status.json").read_text(encoding="utf-8"))
    if status.get("status") != "FULL_REVIEW_REQUIRED":
        raise ValueError(f"run is not at the review gate: status={status.get('status')!r}")
    validation = json.loads((run_dir / "pilot-validation-report.json").read_text(encoding="utf-8"))
    if validation.get("status") != "PASS":
        raise ValueError("validate-full report is missing or not PASS")

    candidates = {json.loads(line)["sample_id"]: json.loads(line) for line in (run_dir / "evaluation-decision-candidates.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    records = [json.loads(line) for line in (run_dir / "evaluation-action-adjudications.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if {r["sample_id"] for r in records} != set(candidates):
        raise ValueError("adjudication coverage does not match candidates")

    # Machine backstop on the benchmark admission gates.
    seen_keys: set[tuple] = set()
    for record in records:
        if record["evaluation_pool"] != "benchmark":
            continue
        action = record["canonical_action"]
        eligible = record["action_verdict"] == "CORRECT" and record["confidence"] == "high" and (action["action_type"] != "COMPOUND" or is_benchmark_eligible_compound(action))
        if not eligible:
            raise ValueError(f"benchmark record fails admission gate: {record['sample_id']}")
        key = _semantic_key(candidates[record["sample_id"]], record)
        if key in seen_keys:
            raise ValueError(f"duplicate benchmark decision point: {record['sample_id']}")
        seen_keys.add(key)

    pools = {"benchmark": [], "audit": [], "excluded": []}
    for record in records:
        sample = _merged_sample(candidates[record["sample_id"]], record)
        schema_validate(sample, "evaluation_set_sample")
        pools[record["evaluation_pool"]].append(sample)

    json_dump(run_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": run_config["run_id"], "status": "FREEZING"})
    _write_jsonl(run_dir / "evaluation-set-v1.jsonl", pools["benchmark"])
    _write_jsonl(run_dir / "evaluation-audit-v1.jsonl", pools["audit"])
    _write_jsonl(run_dir / "evaluation-excluded-v1.jsonl", pools["excluded"])

    inventory_sha, hash_status = sha256_file(inventory_path)
    if hash_status != "hashed":
        raise RuntimeError(f"unable to hash inventory file: {inventory_path}")
    benchmark = pools["benchmark"]
    manifest = {
        "schema_version": "evaluation-set-manifest@v1",
        "run_id": run_config["run_id"],
        "mode": "combined",
        "design_sha256": run_config["design_sha256"],
        "adjudication_rule_version": run_config["adjudication_rule_version"],
        "source_inventory_sha256": inventory_sha,
        "sample_count": len(benchmark),
        "object_count": len({sample["object_id"] for sample in benchmark}),
        "sample_refs": [sample["sample_id"] for sample in benchmark],
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": _git_commit(),
        "pool_counts": {"benchmark": len(pools["benchmark"]), "audit": len(pools["audit"]), "excluded": len(pools["excluded"])},
        "by_mode": dict(Counter(sample["mode"] for sample in benchmark)),
        "by_action": dict(Counter(sample["canonical_action"]["action_type"] for sample in benchmark)),
        "validation_report": "pilot-validation-report.json",
    }
    schema_validate(manifest, "evaluation_set_manifest")
    json_dump(run_dir / "evaluation-set-v1-manifest.json", manifest)
    json_dump(run_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": run_config["run_id"], "status": "FROZEN"})
    return {"manifest": manifest, "counts": manifest["pool_counts"]}


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
