"""Pilot approval artifact for the S8 full extraction (design doc §10 Phase G gate).

The artifact anchors the *current* design document checksum and the inventory
the full chain will run against; the pilot run itself may have been extracted
under an earlier revision of the design document. Approval is only written
when the user has explicitly authorized the full extraction in the active
session — the CLI caller passes ``approved_by``; this module never invents it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ADJUDICATION_RULE_VERSION, DESIGN_DOCUMENT, json_dump, sha256_file
from schemas import validate as schema_validate

_SCHEMA_NAME = "evaluation_pilot_approval"


def current_design_sha256() -> str:
    digest, status = sha256_file(DESIGN_DOCUMENT.resolve())
    if status != "hashed" or digest is None:
        raise RuntimeError(f"unable to hash design document: {DESIGN_DOCUMENT.resolve()}")
    return digest


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_pilot_review_gate(pilot_run_dir: Path, *, prior_approvals_dir: Path | None = None) -> dict[str, Any]:
    """Require a completed, validated, adjudicated pilot run before approval.

    2026-09-21: the pilot run directory may be cleaned up after approval
    (小鱼儿's archival policy keeps only the final run). When the directory is
    gone, the gate falls back to the prior-approval chain: an existing
    ``evaluation-pilot-approval@v1`` artifact referencing the same
    ``pilot_run_id`` attests the validated, adjudicated pilot.
    """
    pilot_run_dir = Path(pilot_run_dir)
    if not (pilot_run_dir / "run-config.json").exists():
        approvals_dir = Path(prior_approvals_dir) if prior_approvals_dir else pilot_run_dir.parent
        for approval_path in sorted(approvals_dir.glob("evalset-approval-*.json")):
            prior = _load_json(approval_path)
            schema_validate(prior, _SCHEMA_NAME)
            if prior.get("pilot_run_id") == pilot_run_dir.name and prior.get("status") == "PILOT_APPROVED":
                return {"run_id": prior["pilot_run_id"], "mode": "pilot", "attested_by_prior_approval": str(approval_path)}
        raise ValueError(f"pilot run directory is missing and no prior approval attests it: {pilot_run_dir}")
    run_config = _load_json(pilot_run_dir / "run-config.json")
    if run_config.get("mode") != "pilot":
        raise ValueError(f"not a pilot run: {pilot_run_dir}")
    status = _load_json(pilot_run_dir / "run-status.json")
    if status.get("status") not in {"PILOT_REVIEW_REQUIRED", "PILOT_APPROVED"}:
        raise ValueError(f"pilot run is not at the review gate: status={status.get('status')!r}")
    validation = _load_json(pilot_run_dir / "pilot-validation-report.json")
    if validation.get("status") != "PASS":
        raise ValueError("pilot validation report is missing or not PASS")
    adjudications = pilot_run_dir / "evaluation-action-adjudications.jsonl"
    if not adjudications.exists() or not adjudications.read_text(encoding="utf-8").strip():
        raise ValueError("pilot run carries no adjudications to approve")
    return run_config


def write_pilot_approval(
    pilot_run_dir: Path,
    inventory_path: Path,
    approved_by: str,
    output_path: Path,
    *,
    approval_id: str | None = None,
    approved_at: str | None = None,
) -> dict[str, Any]:
    run_config = check_pilot_review_gate(pilot_run_dir)
    inventory_path = Path(inventory_path)
    inventory = _load_json(inventory_path)
    schema_validate(inventory, "evaluation_source_inventory")
    design_sha = current_design_sha256()
    if inventory["design_sha256"] != design_sha:
        raise ValueError("inventory design_sha256 does not match the current design document")
    if inventory["adjudication_rule_version"] != ADJUDICATION_RULE_VERSION:
        raise ValueError("inventory adjudication rule version mismatch")
    inventory_file_sha, hash_status = sha256_file(inventory_path)
    if hash_status != "hashed" or inventory_file_sha is None:
        raise RuntimeError(f"unable to hash inventory file: {inventory_path}")
    now = datetime.now(timezone.utc)
    approval = {
        "schema_version": "evaluation-pilot-approval@v1",
        "approval_id": approval_id or f"evalset-approval-{now.strftime('%Y%m%dT%H%M%SZ')}",
        "pilot_run_id": run_config["run_id"],
        "status": "PILOT_APPROVED",
        "approved_by": approved_by,
        "approved_at": approved_at or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "design_sha256": design_sha,
        "source_inventory_sha256": inventory_file_sha,
        "adjudication_rule_version": ADJUDICATION_RULE_VERSION,
    }
    schema_validate(approval, _SCHEMA_NAME)
    json_dump(output_path, approval)
    if "attested_by_prior_approval" not in run_config:
        pilot_status = _load_json(pilot_run_dir / "run-status.json")
        promoted = {"schema_version": "evaluation-run-status@v1", "run_id": run_config["run_id"], "status": "PILOT_APPROVED"}
        if "object_counts" in pilot_status:
            promoted["object_counts"] = pilot_status["object_counts"]
        json_dump(pilot_run_dir / "run-status.json", promoted)
    return approval


def validate_approval_for_full(
    approval_path: Path,
    pilot_run_dir: Path,
    inventory_path: Path,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Gate for ``full prepare`` (skill execution plan §7.4)."""
    approval_path = Path(approval_path)
    inventory_path = Path(inventory_path)
    approval = _load_json(approval_path)
    schema_validate(approval, _SCHEMA_NAME)
    pilot_run_dir = Path(pilot_run_dir)
    if (pilot_run_dir / "run-status.json").exists():
        pilot_config = _load_json(pilot_run_dir / "run-config.json")
        if approval["pilot_run_id"] != pilot_config["run_id"]:
            raise ValueError(f"approval references pilot run {approval['pilot_run_id']!r}, directory holds {pilot_config['run_id']!r}")
        pilot_status = _load_json(pilot_run_dir / "run-status.json")
        if pilot_status.get("status") != "PILOT_APPROVED":
            raise ValueError(f"referenced pilot run is not PILOT_APPROVED: status={pilot_status.get('status')!r}")
    else:
        # Cleaned-up pilot (2026-09-21 archival policy): the directory name must
        # match the approval's pilot_run_id, and at least one prior PILOT_APPROVED
        # artifact for the same run must exist next to this approval.
        if approval["pilot_run_id"] != pilot_run_dir.name:
            raise ValueError(f"approval references pilot run {approval['pilot_run_id']!r}, directory is {pilot_run_dir.name!r}")
        prior_ids = set()
        for prior_path in sorted(pilot_run_dir.parent.glob("evalset-approval-*.json")):
            if prior_path.resolve() == Path(approval_path).resolve():
                continue
            prior = _load_json(prior_path)
            schema_validate(prior, _SCHEMA_NAME)
            if prior.get("status") == "PILOT_APPROVED":
                prior_ids.add(prior.get("pilot_run_id"))
        if approval["pilot_run_id"] not in prior_ids:
            raise ValueError("pilot run directory is missing and no prior approval attests the same pilot_run_id")
    design_sha = current_design_sha256()
    if approval["design_sha256"] != design_sha:
        raise ValueError("approval design checksum does not match the current design document")
    if inventory["design_sha256"] != design_sha:
        raise ValueError("inventory design checksum does not match the current design document")
    if approval["adjudication_rule_version"] != ADJUDICATION_RULE_VERSION or inventory["adjudication_rule_version"] != ADJUDICATION_RULE_VERSION:
        raise ValueError("adjudication rule version mismatch between approval, inventory, and code")
    inventory_file_sha, hash_status = sha256_file(inventory_path)
    if hash_status != "hashed" or approval["source_inventory_sha256"] != inventory_file_sha:
        raise ValueError("approval source_inventory_sha256 does not match the supplied inventory file")
    return approval
