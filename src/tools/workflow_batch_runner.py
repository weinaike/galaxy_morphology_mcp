"""Client-independent batch coordinator for the structured fitting workflow.

The runner discovers explicit inputs, calls the existing MCP fitting and
lifecycle tools, persists auditable artifacts, and enforces the Image-to-SED
handoff gate.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from component_analysis.artifact_adapter import _parse_lyric
from component_analysis.workflow_summary_renderer import (
    render_final_report,
    render_image_round,
    render_working_note,
)
from schemas import validate


SCHEMA_VERSION = "workflow-batch-manifest@v1"
RUNNER_VERSION = "workflow-batch-runner@v1"
STATE_VERSION = "workflow-state@v1"
OBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
TERMINAL_STATES = {
    "COMPLETED",
    "COMPLETED_WITH_REVIEW",
    "FAILED_NEEDS_REVIEW",
    "PREFLIGHT_FAILED",
}
ALLOWED_COMPONENTS = {
    "disk",
    "bulge",
    "edge_on_disk",
    "bar",
    "agn",
    "fourier_m1",
    "companion",
    "compact_central_source_candidate",
    "lens",
}
JWST0831_ALLOWED_OBJECT_IDS = frozenset({"104", "1071", "1118"})


class BatchManifestError(ValueError):
    """Raised when a batch input cannot be made unambiguous."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_pair(raw_pair: Any, config_dir: Path) -> tuple[Path | None, str]:
    if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) < 2:
        return None, "invalid pair"
    raw_path = str(raw_pair[0]).strip()
    if raw_path.lower() == "none":
        return None, "unavailable"
    path = Path(raw_path)
    if not path.is_absolute():
        path = config_dir / path
    return path.expanduser().resolve(), ""


def _append_input(
    inputs: list[dict[str, Any]],
    seen: set[str],
    issues: list[str],
    *,
    path: Path | None,
    role: str,
    required: bool,
) -> None:
    if path is None:
        if required:
            issues.append(f"{role}: unavailable")
        else:
            issues.append(f"{role}: unavailable; preserved as UNAVAILABLE")
        return
    path_text = str(path)
    if path_text in seen:
        return
    seen.add(path_text)
    if not path.is_file():
        inputs.append({"path": path_text, "role": role, "sha256": None})
        issues.append(f"{role}: file not found: {path_text}")
        return
    inputs.append({"path": path_text, "role": role, "sha256": _sha256(path)})


def _base_lyrics(galaxy_dir: Path) -> list[Path]:
    return sorted(
        path.resolve()
        for path in galaxy_dir.glob("*.lyric")
        if "_iter" not in path.stem
    )


def _discover_object(galaxy_dir: Path, mode: str) -> dict[str, Any]:
    object_id = galaxy_dir.name
    issues: list[str] = []
    inputs: list[dict[str, Any]] = []
    seen: set[str] = set()
    lyrics = _base_lyrics(galaxy_dir)
    config_file: str | None = None
    bands: list[str] = []

    if len(lyrics) != 1:
        if not lyrics:
            issues.append("exactly one base .lyric is required; none found")
        else:
            issues.append(
                f"exactly one base .lyric is required; found {len(lyrics)}"
            )
    else:
        config_path = lyrics[0]
        config_file = str(config_path)
        _append_input(
            inputs,
            seen,
            issues,
            path=config_path,
            role="config_file",
            required=True,
        )
        try:
            _, image_infos = _parse_lyric(str(config_path))
            for info in image_infos:
                band = str(info["band"])
                bands.append(band)
                for field, required in (
                    ("science", True),
                    ("sigma", galaxy_dir.parent.name.casefold() == "jwst0831"),
                    ("psf", False),
                    ("mask", True),
                ):
                    path, pair_issue = _resolve_pair(info.get(field), config_path.parent)
                    _append_input(
                        inputs,
                        seen,
                        issues,
                        path=path,
                        role=f"{band}:{field}",
                        required=required and pair_issue != "unavailable",
                    )
                    if pair_issue == "invalid pair":
                        issues.append(f"{band}:{field}: invalid lyric pair")
                    if (
                        field == "sigma"
                        and galaxy_dir.parent.name.casefold() == "jwst0831"
                        and path is not None
                    ):
                        expected = galaxy_dir.parent / "sigma" / f"Sig{object_id}_{band}.fits"
                        if path.resolve() != expected.resolve():
                            issues.append(
                                f"{band}:sigma: expected explicit JWST0831 sigma path {expected}"
                            )
        except (OSError, ValueError) as exc:
            issues.append(f"lyric parse failed: {type(exc).__name__}: {exc}")

    required_issue = any(
        issue.endswith(": unavailable")
        or "file not found" in issue
        or "lyric parse failed" in issue
        or "exactly one base" in issue
        for issue in issues
    )
    return {
        "object_id": object_id,
        "galaxy_dir": str(galaxy_dir.resolve()),
        "config_file": config_file,
        "bands": bands,
        "input_files": inputs,
        "preflight_status": "PREFLIGHT_FAILED" if required_issue else "READY",
        "issues": issues,
    }


def discover_objects(
    root_dir: str | Path,
    *,
    mode: str = "multi-band",
    object_id: str | None = None,
    max_objects: int | None = None,
) -> list[dict[str, Any]]:
    """Discover immediate object directories with explicit base lyric files."""
    root = Path(root_dir).expanduser().resolve()
    if not root.is_dir():
        raise BatchManifestError(f"root directory does not exist: {root}")
    if mode not in {"single-band", "multi-band"}:
        raise BatchManifestError(f"unsupported workflow mode: {mode}")
    directories = [
        path for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith(".") and path.name != "sigma"
    ]
    if root.name.casefold() == "jwst0831":
        if object_id is not None and object_id not in JWST0831_ALLOWED_OBJECT_IDS:
            raise BatchManifestError(
                f"JWST0831 object {object_id!r} is outside the authorized allowlist"
            )
        directories = [
            path for path in directories
            if path.name in JWST0831_ALLOWED_OBJECT_IDS
        ]
    if object_id is not None:
        directories = [path for path in directories if path.name == object_id]
        if not directories:
            raise BatchManifestError(f"object_id not found below root: {object_id}")
    if max_objects is not None:
        if max_objects < 1:
            raise BatchManifestError("max_objects must be positive")
        directories = directories[:max_objects]
    return [_discover_object(path, mode) for path in directories]


def build_batch_manifest(
    root_dir: str | Path,
    run_dir: str | Path,
    *,
    mode: str = "multi-band",
    pilot: bool = False,
    use_vlm: bool = False,
    resume: bool = False,
    object_id: str | None = None,
    max_objects: int | None = None,
    timeout_sec: float = 3600,
    retries: int = 1,
    resource_profile: str = "serial-gpu",
) -> dict[str, Any]:
    """Create and validate an immutable input manifest."""
    root = Path(root_dir).expanduser().resolve()
    run = Path(run_dir).expanduser().resolve()
    if timeout_sec <= 0:
        raise BatchManifestError("timeout_sec must be positive")
    if retries < 0:
        raise BatchManifestError("retries must not be negative")
    objects = discover_objects(
        root,
        mode=mode,
        object_id=object_id,
        max_objects=max_objects,
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": f"{root.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "generated_at": _utc_now(),
        "workflow_mode": mode,
        "root_dir": str(root),
        "run_dir": str(run),
        "versions": {
            "runner": RUNNER_VERSION,
            "schema": SCHEMA_VERSION,
            "workflow_bridge": "workflow-bridge@v1",
            "decision_service": "decision-service@v1",
            "prompt": "workflow_galfits.md",
        },
        "options": {
            "pilot_enabled": bool(pilot),
            "use_vlm": bool(use_vlm),
            "resume": bool(resume),
            "timeout_sec": float(timeout_sec),
            "retries": int(retries),
            "resource_profile": resource_profile,
            "object_id": object_id,
            "max_objects": max_objects,
        },
        "objects": objects,
    }
    validate(manifest, "workflow_batch_manifest")
    return manifest


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_batch_manifest(manifest: dict[str, Any]) -> Path:
    validate(manifest, "workflow_batch_manifest")
    path = Path(manifest["run_dir"]).expanduser().resolve() / "batch_manifest.json"
    _atomic_write(path, manifest)
    return path


def read_batch_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    validate(manifest, "workflow_batch_manifest")
    return manifest


def _safe_object_id(object_id: str) -> str:
    if not OBJECT_ID_PATTERN.fullmatch(object_id):
        raise BatchManifestError(f"unsafe object_id for state path: {object_id!r}")
    return object_id


def state_path(run_dir: str | Path, object_id: str) -> Path:
    return (
        Path(run_dir).expanduser().resolve()
        / "objects"
        / _safe_object_id(object_id)
        / "state.json"
    )


def new_object_state(object_id: str, *, preflight_status: str) -> dict[str, Any]:
    return {
        "schema_version": STATE_VERSION,
        "object_id": object_id,
        "status": (
            "PREFLIGHT_FAILED"
            if preflight_status == "PREFLIGHT_FAILED"
            else "NOT_STARTED"
        ),
        "current_round_id": None,
        "baseline_round_id": None,
        "pending_action": None,
        "needs_review": False,
        "termination_reason": None,
        "verifier_status": None,
        "round_index": 0,
        "baseline_fit_file": None,
        "baseline_config_file": None,
        "current_config_file": None,
        "current_fit_file": None,
        "current_manifest_file": None,
        "pending_candidate": None,
        "downstream": {},
        "event_ids": [],
        "action_summary": {
            "raw": {},
            "resolved": {},
            "candidate": {},
            "executed": {},
            "refit": {},
        },
        "history": [],
        "rejected_action_baseline_fingerprints": [],
        "updated_at": _utc_now(),
    }


def load_object_state(
    run_dir: str | Path,
    object_id: str,
    *,
    preflight_status: str = "READY",
) -> dict[str, Any]:
    path = state_path(run_dir, object_id)
    if not path.exists():
        return new_object_state(object_id, preflight_status=preflight_status)
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    if state.get("schema_version") != STATE_VERSION:
        raise BatchManifestError(f"unsupported state schema in {path}")
    if state.get("object_id") != object_id:
        raise BatchManifestError(f"state object mismatch in {path}")
    return state


def save_object_state(run_dir: str | Path, state: dict[str, Any]) -> Path:
    object_id = _safe_object_id(str(state.get("object_id", "")))
    state["updated_at"] = _utc_now()
    path = state_path(run_dir, object_id)
    _atomic_write(path, state)
    return path


def _provider_timing_summary(object_dir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "rounds": 0,
        "attempts": 0,
        "duration_s": 0.0,
        "provider_status_counts": {},
        "parse_status_counts": {},
        "fallback_status_counts": {},
        "timing_log_refs": [],
    }
    timing_log_refs: set[str] = set()
    for proposal_file in sorted(
        object_dir.glob("round_*/proposal/workflow_proposal.json")
    ):
        proposal = _read_json(proposal_file)
        provider_status = str((proposal.get("provider") or {}).get("status", "UNKNOWN"))
        provider_counts = summary["provider_status_counts"]
        provider_counts[provider_status] = provider_counts.get(provider_status, 0) + 1
        timing = proposal.get("timing")
        if not isinstance(timing, dict):
            continue
        summary["rounds"] += 1
        summary["duration_s"] += float(timing.get("duration_s", 0.0))
        fallback_status = str(timing.get("fallback_status", "UNKNOWN"))
        fallback_counts = summary["fallback_status_counts"]
        fallback_counts[fallback_status] = fallback_counts.get(fallback_status, 0) + 1
        timing_log_ref = timing.get("timing_log_ref")
        if isinstance(timing_log_ref, str) and timing_log_ref:
            timing_log_refs.add(timing_log_ref)
        for attempt in timing.get("attempts", []):
            if not isinstance(attempt, dict):
                continue
            summary["attempts"] += 1
            parse_status = str(attempt.get("parse_status", "UNKNOWN"))
            parse_counts = summary["parse_status_counts"]
            parse_counts[parse_status] = parse_counts.get(parse_status, 0) + 1
    summary["duration_s"] = round(summary["duration_s"], 6)
    summary["timing_log_refs"] = sorted(timing_log_refs)
    return summary


def _merge_count_map(target: dict[str, int], source: dict[str, int]) -> None:
    for key, count in source.items():
        target[key] = target.get(key, 0) + int(count)


def batch_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """Summarize persisted object states without inspecting report filenames."""
    counts: dict[str, int] = {}
    objects: list[dict[str, Any]] = []
    downstream_counts: dict[str, dict[str, int]] = {}
    provider_timing: dict[str, Any] = {
        "rounds": 0,
        "attempts": 0,
        "duration_s": 0.0,
        "provider_status_counts": {},
        "parse_status_counts": {},
        "fallback_status_counts": {},
        "timing_log_refs": [],
    }
    batch_timing_refs: set[str] = set()
    for item in manifest["objects"]:
        state = load_object_state(
            manifest["run_dir"],
            item["object_id"],
            preflight_status=item["preflight_status"],
        )
        status = str(state["status"])
        counts[status] = counts.get(status, 0) + 1
        downstream = state.get("downstream", {})
        for stage, stage_status in downstream.items():
            if stage not in {"image", "sed", "image_sed"}:
                continue
            stage_counts = downstream_counts.setdefault(stage, {})
            value = str(stage_status)
            stage_counts[value] = stage_counts.get(value, 0) + 1
        object_timing = _provider_timing_summary(
            Path(manifest["run_dir"]).expanduser().resolve()
            / "objects"
            / _safe_object_id(item["object_id"])
        )
        provider_timing["rounds"] += object_timing["rounds"]
        provider_timing["attempts"] += object_timing["attempts"]
        provider_timing["duration_s"] += object_timing["duration_s"]
        for key in (
            "provider_status_counts",
            "parse_status_counts",
            "fallback_status_counts",
        ):
            _merge_count_map(provider_timing[key], object_timing[key])
        batch_timing_refs.update(object_timing["timing_log_refs"])
        objects.append(
            {
                "object_id": item["object_id"],
                "status": status,
                "state_file": str(state_path(manifest["run_dir"], item["object_id"])),
                "preflight_status": item["preflight_status"],
                "action_summary": state.get("action_summary", {}),
                "downstream": downstream,
                "provider_timing": object_timing,
                "resource_profile": state.get("resource_profile"),
                "resource_switches": state.get("resource_switches", []),
            }
        )
    provider_timing["duration_s"] = round(provider_timing["duration_s"], 6)
    provider_timing["timing_log_refs"] = sorted(batch_timing_refs)
    return {
        "schema_version": "workflow-batch-summary@v1",
        "batch_id": manifest["batch_id"],
        "generated_at": _utc_now(),
        "manifest_file": str(
            Path(manifest["run_dir"]).expanduser().resolve() / "batch_manifest.json"
        ),
        "counts": counts,
        "downstream_counts": downstream_counts,
        "provider_timing": provider_timing,
        "terminal_states": sorted(status for status in counts if status in TERMINAL_STATES),
        "objects": objects,
    }


def write_batch_summary(manifest: dict[str, Any]) -> Path:
    path = Path(manifest["run_dir"]).expanduser().resolve() / "batch_summary.json"
    _atomic_write(path, batch_summary(manifest))
    return path


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise BatchManifestError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    _atomic_write(path, value)
    return path


def _is_success(value: Any) -> bool:
    return str((value or {}).get("status", "")).lower() in {
        "success",
        "succeeded",
        "ok",
        "completed",
    }


def _next_action_config(
    source_config: str,
    round_index: int,
    target_dir: str | Path | None = None,
) -> str:
    source = Path(source_config).expanduser().resolve()
    base = re.sub(r"_iter\d+$", "", source.stem)
    parent = Path(target_dir).expanduser().resolve() if target_dir else source.parent
    parent.mkdir(parents=True, exist_ok=True)
    index = max(1, round_index)
    while True:
        target = parent / f"{base}_iter{index}{source.suffix}"
        if not target.exists():
            return str(target)
        index += 1


def _action_attempt_fingerprint(
    action: dict[str, Any],
    workflow_manifest: dict[str, Any],
    proposal: dict[str, Any] | None = None,
) -> str:
    payload = {
        "action": action,
        "baseline": {
            "config_file": workflow_manifest.get("config_file"),
            "result_files": workflow_manifest.get("result_files", []),
            "summary_file": workflow_manifest.get("summary_file"),
        },
        "evidence_fingerprint": (proposal or {}).get("evidence_fingerprint"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _action_baseline_fingerprint(
    action: dict[str, Any],
    workflow_manifest: dict[str, Any],
) -> str:
    """Identify an action against its unchanged input fit, independent of evidence metadata."""
    payload = {
        "action": action,
        "baseline": {
            "config_file": workflow_manifest.get("config_file"),
            "result_files": workflow_manifest.get("result_files", []),
            "summary_file": workflow_manifest.get("summary_file"),
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _terminal_review_decision(
    decision: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Convert the last auditable decision into a non-executing review stop."""
    terminal = copy.deepcopy(decision)
    previous_action = terminal.get("action")
    action_type = (
        previous_action.get("action_type")
        if isinstance(previous_action, dict)
        else "NO_ACTION"
    )
    reason_code = (
        previous_action.get("reason_code")
        if isinstance(previous_action, dict)
        else None
    )
    terminal["action"] = None
    terminal["workflow_status"] = "STOPPED_NEEDS_REVIEW"
    terminal["automation"] = {
        "policy_version": "automation-policy@v1",
        "resolution": "rule_terminated",
        "original_action_type": action_type or "NO_ACTION",
        "resolved_rule_id": reason_code,
        "reason": reason[:500],
        "needs_review": True,
    }
    return terminal


def _terminal_action_summary(state: dict[str, Any]) -> dict[str, Any]:
    summary = state.get("action_summary") or {}
    candidates = summary.get("candidate")
    if not isinstance(candidates, list):
        candidates = []
    return {
        "raw_action_type": summary.get("raw")
        if isinstance(summary.get("raw"), str)
        else None,
        "resolved_action_type": summary.get("resolved")
        if isinstance(summary.get("resolved"), str)
        else None,
        "candidate_action_types": [
            value for value in candidates if isinstance(value, str)
        ],
        "executed_action_type": summary.get("executed")
        if isinstance(summary.get("executed"), str)
        else None,
        "refit_verdict": summary.get("refit")
        if isinstance(summary.get("refit"), str)
        else None,
        "fallback": "rule_terminated",
        "needs_review": True,
    }


def _policy_review_terminal(state_file: Path) -> bool:
    if not state_file.is_file():
        return False
    policy = _read_json(state_file)
    return (
        policy.get("fit_completion_status") == "FIT_AVAILABLE"
        and policy.get("best_round_status") == "UNLOCKED"
        and policy.get("needs_review") is True
    )


def _write_lifecycle_artifact(
    path: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist only the schema artifact, excluding MCP return metadata."""
    artifact = copy.deepcopy(result)
    removed = False
    for key in ("lifecycle_file", "fit_valid"):
        if key in artifact:
            artifact.pop(key)
            removed = True
    if artifact.get("schema_version") != "1.0":
        raise BatchManifestError("workflow lifecycle artifact schema failed")
    validate(artifact, "workflow_lifecycle")
    if removed or not path.is_file():
        _write_json(path, artifact)
    return artifact


def _persisted_rejected_action_fingerprints(object_dir: Path) -> set[str]:
    fingerprints: set[str] = set()
    for completion_file in sorted(object_dir.glob("round_*/candidate_completion.json")):
        completion = _read_json(completion_file)
        if completion.get("action_summary", {}).get("refit_verdict") != "REJECTED":
            continue
        round_dir = completion_file.parent
        resolution_file = round_dir / "resolution" / "resolution.json"
        manifest_file = round_dir / "workflow_manifest.json"
        if not resolution_file.is_file() or not manifest_file.is_file():
            continue
        resolution = _read_json(resolution_file)
        action = (resolution.get("decision") or {}).get("action") or {}
        if not action:
            continue
        fingerprints.add(
            _action_attempt_fingerprint(action, _read_json(manifest_file))
        )
    return fingerprints


def _persisted_rejected_action_baseline_fingerprints(object_dir: Path) -> set[str]:
    fingerprints: set[str] = set()
    for completion_file in sorted(object_dir.glob("round_*/candidate_completion.json")):
        completion = _read_json(completion_file)
        if completion.get("action_summary", {}).get("refit_verdict") != "REJECTED":
            continue
        round_dir = completion_file.parent
        resolution_file = round_dir / "resolution" / "resolution.json"
        manifest_file = round_dir / "workflow_manifest.json"
        if not resolution_file.is_file() or not manifest_file.is_file():
            continue
        resolution = _read_json(resolution_file)
        action = (resolution.get("decision") or {}).get("action") or {}
        if not action:
            continue
        fingerprints.add(
            _action_baseline_fingerprint(action, _read_json(manifest_file))
        )
    return fingerprints


def _action_component(
    action: dict[str, Any],
    workflow_manifest: dict[str, Any],
) -> str | None:
    for key in ("component", "replace_to", "replace_from"):
        value = action.get(key)
        if value in ALLOWED_COMPONENTS:
            return str(value)
    reason = str(action.get("reason_code", "")).upper()
    for prefix, component in (
        ("DISK_", "disk"),
        ("BULGE_", "bulge"),
        ("BAR_", "bar"),
        ("LENS_", "lens"),
        ("AGN_", "agn"),
        ("COMPANION_", "companion"),
        ("FOURIER_", "fourier_m1"),
    ):
        if reason.startswith(prefix):
            return component
    try:
        from component_analysis.artifact_adapter import workflow_fit_components

        components = [
            item.get("component")
            for item in workflow_fit_components(workflow_manifest)
            if item.get("component") in ALLOWED_COMPONENTS
        ]
    except (OSError, ValueError, TypeError):
        components = []
    return components[0] if len(set(components)) == 1 else None


class MCPToolError(RuntimeError):
    """Raised when the shared MCP server returns a tool error."""


class GPUOutOfMemoryError(MCPToolError):
    """Raised when a fit tool reports a recognized GPU allocation failure."""


def _is_gpu_oom(value: Any) -> bool:
    if isinstance(value, str):
        text = value.lower()
    else:
        text = json.dumps(value, ensure_ascii=True, default=str).lower()
    return any(
        marker in text
        for marker in (
            "cuda out of memory",
            "cuda_error_out_of_memory",
            "resource_exhausted",
            "out of memory while trying to allocate",
            "failed to allocate memory on device",
            "xla runtime error: resource exhausted",
        )
    )


class MCPToolClient:
    """Small async stdio adapter for any MCP-compatible workflow client."""

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        resource_profile: str = "serial-gpu",
    ) -> None:
        self.project_root = project_root
        self.resource_profile = resource_profile
        self._stack: AsyncExitStack | None = None
        self._session: Any | None = None

    def _server_environment(self) -> dict[str, str]:
        """Merge the configured MCP server environment without printing secrets."""
        environment = dict(os.environ)
        config_path = Path(
            os.getenv("MCP_CONFIG", str(self.project_root / ".mcp.json"))
        ).expanduser()
        if config_path.is_file():
            with config_path.open(encoding="utf-8") as handle:
                config = json.load(handle)
            server = (config.get("mcpServers") or {}).get("galmcp") or {}
            configured = server.get("env") or {}
            for key, value in configured.items():
                if isinstance(key, str) and isinstance(value, str):
                    environment[key] = value
        return environment

    async def __aenter__(self) -> "MCPToolClient":
        from dotenv import load_dotenv
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        load_dotenv(self.project_root / ".env")
        python_bin = os.getenv(
            "PYTHON_BIN", "/home/www/ENTER/envs/galfit/bin/python"
        )
        server_script = os.getenv(
            "MCP_SERVER_SCRIPT", str(self.project_root / "src/mcp_server.py")
        )
        env = self._server_environment()
        env["COMPONENT_ANALYSIS_WORKFLOW_PILOT"] = "1"
        if self.resource_profile == "serial-cpu":
            env["JAX_PLATFORMS"] = "cpu"
        params = StdioServerParameters(
            command=python_bin,
            args=[server_script, "--transport", "stdio"],
            env=env,
            cwd=str(self.project_root),
        )
        self._stack = AsyncExitStack()
        read_stream, write_stream = await self._stack.enter_async_context(
            stdio_client(params)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, tb)
        self._stack = None
        self._session = None

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_sec: float = 3600,
    ) -> dict[str, Any]:
        if self._session is None:
            raise MCPToolError("MCP client is not connected")
        result = await self._session.call_tool(
            name,
            arguments or {},
            read_timeout_seconds=timedelta(seconds=timeout_sec),
        )
        if result.isError:
            details = []
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    details.append(text)
            detail = "; ".join(details)
            raise MCPToolError(
                f"{name} returned an MCP tool error"
                + (f": {detail}" if detail else "")
            )
        structured = result.structuredContent
        if isinstance(structured, dict):
            value = structured.get("result")
            if isinstance(value, dict) and len(structured) == 1:
                return value
            return structured
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
        raise MCPToolError(f"{name} returned no structured JSON object")


class WorkflowBatchCoordinator:
    """Run one serial batch through the shared workflow lifecycle contract."""

    def __init__(
        self,
        manifest: dict[str, Any],
        client: Any,
        *,
        max_rounds: int = 10,
        allow_remove: bool = False,
        remove_pilot_passed: bool = False,
        verifier_assessment: dict[str, Any] | None = None,
    ) -> None:
        self.manifest = manifest
        self.client = client
        self.max_rounds = max_rounds
        self.allow_remove = allow_remove
        self.remove_pilot_passed = remove_pilot_passed
        self.verifier_assessment = verifier_assessment
        root_dir = Path(self.manifest["root_dir"]).expanduser().resolve()
        if root_dir.name.casefold() == "jwst0831":
            unauthorized = {
                item["object_id"]
                for item in self.manifest["objects"]
                if item["object_id"] not in JWST0831_ALLOWED_OBJECT_IDS
            }
            if unauthorized:
                raise BatchManifestError(
                    "JWST0831 manifest contains unauthorized objects: "
                    + ", ".join(sorted(unauthorized))
                )

    def _object_dir(self, object_id: str) -> Path:
        return (
            Path(self.manifest["run_dir"]).expanduser().resolve()
            / "objects"
            / _safe_object_id(object_id)
        )

    def _item_for_object(self, object_id: str) -> dict[str, Any]:
        for item in self.manifest["objects"]:
            if item["object_id"] == object_id:
                return item
        raise BatchManifestError(f"object {object_id!r} is not in the batch manifest")

    def _persistent_root(self, object_id: str) -> Path:
        item = self._item_for_object(object_id)
        return (
            Path(item["galaxy_dir"]).expanduser().resolve()
            / "output"
            / "workflow"
            / self.manifest["batch_id"]
        )


    def _prepare_run_scoped_baseline(self, object_id: str, source_config: str) -> str:
        """Copy the immutable base lyric into this run before the first fit."""
        source = Path(source_config).expanduser().resolve()
        target_dir = self._persistent_root(object_id) / "configs"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.stem}_baseline{source.suffix}"
        if target.exists():
            if target.is_file():
                return str(target)
            raise BatchManifestError(f"run-scoped baseline is not a file: {target}")
        shutil.copy2(source, target)
        return str(target)


    def _write_run_index(
        self,
        object_id: str,
        state: dict[str, Any],
        *,
        round_dir: Path | None = None,
        workflow_manifest: Mapping[str, Any] | None = None,
    ) -> Path:
        """Persist an explicit round-to-artifact path index for human audit."""
        if round_dir is not None and workflow_manifest is not None:
            round_id = str(workflow_manifest.get("round_id", round_dir.name))
            candidate_manifest = None
            candidate_path = round_dir / "candidate_manifest.json"
            if candidate_path.is_file():
                try:
                    candidate_manifest = _read_json(candidate_path)
                except (OSError, ValueError, BatchManifestError):
                    candidate_manifest = None
            resolution = None
            resolution_path = round_dir / "resolution" / "resolution.json"
            if resolution_path.is_file():
                try:
                    resolution = _read_json(resolution_path)
                except (OSError, ValueError, BatchManifestError):
                    resolution = None
            refit = None
            refit_path = round_dir / "refit_decision.json"
            if refit_path.is_file():
                try:
                    refit = _read_json(refit_path)
                except (OSError, ValueError, BatchManifestError):
                    refit = None
            records = state.setdefault("run_index_rounds", {})
            records[round_id] = {
                "round_id": round_id,
                "round_dir": str(round_dir.resolve()),
                "action": ((resolution or {}).get("decision") or {}).get("action"),
                "refit_verdict": ((refit or {}).get("action_summary") or {}).get("refit_verdict"),
                "baseline": {
                    "config_file": workflow_manifest.get("config_file"),
                    "result_files": workflow_manifest.get("result_files", []),
                    "summary_file": workflow_manifest.get("summary_file"),
                    "comparison_png": workflow_manifest.get("comparison_png"),
                },
                "candidate": {
                    "config_file": (candidate_manifest or {}).get("config_file"),
                    "result_files": (candidate_manifest or {}).get("result_files", []),
                    "summary_file": (candidate_manifest or {}).get("summary_file"),
                    "comparison_png": (candidate_manifest or {}).get("comparison_png"),
                } if candidate_manifest else None,
                "artifact_index": state.get("artifact_index_file"),
            }
        target = self._persistent_root(object_id) / "run_index.json"
        payload = {
            "schema_version": "workflow-run-index@v1",
            "run_id": self.manifest["batch_id"],
            "object_id": object_id,
            "baseline_config_file": state.get("baseline_config_file"),
            "rounds": [
                state["run_index_rounds"][key]
                for key in sorted(state.get("run_index_rounds", {}))
            ],
        }
        _write_json(target, payload)
        state["run_index_file"] = str(target)
        return target

    def _summary_dir(self, object_id: str) -> Path:
        return self._persistent_root(object_id) / "summaries"

    def _artifact_dir(self, object_id: str) -> Path:
        return self._persistent_root(object_id) / "artifacts"

    def _render_round(
        self,
        *,
        object_id: str,
        round_dir: Path,
        state: dict[str, Any],
    ) -> Path:
        summary_dir = self._summary_dir(object_id)
        detect_result = None
        detection_file = state.get("round0_detection_file")
        if detection_file and Path(detection_file).is_file():
            try:
                detect_result = _read_json(Path(detection_file))
            except (OSError, ValueError, BatchManifestError):
                detect_result = None
        round_summary = render_image_round(
            round_dir=round_dir,
            summary_dir=summary_dir,
            state=state,
            detect_result=detect_result,
        )
        rendered = state.setdefault("round_summary_files", [])
        rendered_path = str(round_summary)
        if rendered_path not in rendered:
            rendered.append(rendered_path)
        state["summary_dir"] = str(summary_dir)
        state["working_note_file"] = str(
            render_working_note(
                summary_dir=summary_dir,
                round_summary_files=rendered,
                round_dirs=state.get("round_dir_files", []),
            )
        )
        return round_summary

    def _render_final_report(self, object_id: str, state: dict[str, Any]) -> Path:
        target = render_final_report(
            object_id=object_id,
            summary_dir=self._summary_dir(object_id),
            state=state,
            round_dirs=state.get("round_dir_files", []),
        )
        state["analysis_report_file"] = str(target)

        return target

    @staticmethod
    def _mark_downstream_not_run(state: dict[str, Any], reason: str) -> None:
        state["best_round_status"] = "UNLOCKED"
        state["sed_joint_eligible"] = False
        state["downstream"] = {
            "image": "FIT_AVAILABLE",
            "sed": "NOT_RUN",
            "image_sed": "NOT_RUN",
            "reason": reason,
        }

    @staticmethod
    def _downstream_is_eligible(state: Mapping[str, Any]) -> bool:
        return (
            state.get("status") == "COMPLETED"
            and state.get("best_round_status") == "LOCKED"
            and state.get("verifier_status") == "PASS"
            and state.get("sed_joint_eligible") is True
        )

    def _persist_round_artifacts(
        self,
        *,
        object_id: str,
        round_dir: Path,
        workflow_manifest: Mapping[str, Any] | None = None,
    ) -> Path:
        target_dir = self._artifact_dir(object_id) / round_dir.name
        target_dir.mkdir(parents=True, exist_ok=True)
        source_files = (
            "workflow_manifest.json", "workflow_analysis_artifact.json", "lifecycle.json",
            "terminal_lifecycle.json", "candidate_manifest.json", "candidate_fit_result.json",
            "candidate_completion.json", "candidate_lifecycle.json", "refit_evidence.json", "refit_decision.json",
            "action_preflight.json", "action_config.json", "lyric_check.json",
            "workflow_verifier.json", "lock_result.json", "proposal/workflow_proposal.json",
            "proposal/numeric_evidence.json", "proposal/vlm_evidence.json",
            "proposal/decision_artifact.json", "resolution/resolution.json",
        )
        records: list[dict[str, Any]] = []
        for relative in source_files:
            source = round_dir / relative
            if not source.is_file():
                continue
            destination = target_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            records.append(
                {
                    "path": str(destination),
                    "source": str(source),
                    "bytes": destination.stat().st_size,
                    "sha256": _sha256(destination),
                }
            )
        comparison = (workflow_manifest or {}).get("comparison_png")
        if isinstance(comparison, str) and Path(comparison).is_file():
            source = Path(comparison)
            destination = target_dir / "all_bands_comparison.png"
            shutil.copy2(source, destination)
            records.append(
                {
                    "path": str(destination),
                    "source": str(source),
                    "bytes": destination.stat().st_size,
                    "sha256": _sha256(destination),
                }
            )
        index_path = _write_json(
            target_dir / "artifact_index.json",
            {
                "schema_version": "workflow-artifact-index@v1",
                "object_id": object_id,
                "round_dir": str(round_dir),
                "records": records,
            },
        )
        artifact_root = self._artifact_dir(object_id)
        artifact_bytes = sum(
            path.stat().st_size
            for path in artifact_root.rglob("*")
            if path.is_file()
        )
        _write_json(
            self._persistent_root(object_id) / "capacity_report.json",
            {
                "schema_version": "workflow-capacity@v1",
                "object_id": object_id,
                "run_id": self.manifest["batch_id"],
                "latest_round": round_dir.name,
                "latest_round_bytes": sum(item["bytes"] for item in records),
                "artifact_bytes": artifact_bytes,
                "artifact_file_count": sum(1 for path in artifact_root.rglob("*") if path.is_file()),
            },
        )
        return index_path

    def _persist_round_artifacts_and_render(
        self,
        *,
        object_id: str,
        round_dir: Path,
        workflow_manifest: Mapping[str, Any] | None,
        state: dict[str, Any],
    ) -> Path:
        index_path = self._persist_round_artifacts(
            object_id=object_id,
            round_dir=round_dir,
            workflow_manifest=workflow_manifest,
        )
        state["artifact_index_file"] = str(index_path)
        round_dirs = state.setdefault("round_dir_files", [])
        round_dir_text = str(round_dir.resolve())
        if round_dir_text not in round_dirs:
            round_dirs.append(round_dir_text)
        self._write_run_index(
            object_id,
            state,
            round_dir=round_dir,
            workflow_manifest=workflow_manifest,
        )
        self._render_round(object_id=object_id, round_dir=round_dir, state=state)
        return index_path

    def _save_runner_state(self, object_id: str, state: dict[str, Any]) -> None:
        save_object_state(self.manifest["run_dir"], state)

    def _record_action_summary(
        self,
        state: dict[str, Any],
        summary: dict[str, Any] | None,
    ) -> None:
        if not summary:
            return
        state.setdefault("action_summary_history", []).append(copy.deepcopy(summary))
        groups = state.setdefault("action_summary", {})
        for key, value in (
            ("raw", summary.get("raw_action_type")),
            ("resolved", summary.get("resolved_action_type")),
            ("candidate", summary.get("candidate_action_types", [])),
            ("executed", summary.get("executed_action_type")),
            ("refit", summary.get("refit_verdict")),
        ):
            if value not in (None, [], {}):
                groups[key] = copy.deepcopy(value)

    def _write_batch_capacity_report(self) -> Path:
        objects: list[dict[str, Any]] = []
        total_bytes = 0
        total_files = 0
        for item in self.manifest["objects"]:
            artifact_root = self._artifact_dir(item["object_id"])
            if not artifact_root.is_dir():
                objects.append({"object_id": item["object_id"], "bytes": 0, "files": 0})
                continue
            files = [path for path in artifact_root.rglob("*") if path.is_file()]
            size = sum(path.stat().st_size for path in files)
            total_bytes += size
            total_files += len(files)
            objects.append({"object_id": item["object_id"], "bytes": size, "files": len(files)})
        return _write_json(
            Path(self.manifest["run_dir"]).expanduser().resolve() / "artifact_capacity_report.json",
            {
                "schema_version": "workflow-capacity-report@v1",
                "run_id": self.manifest["batch_id"],
                "objects": objects,
                "total_bytes": total_bytes,
                "total_files": total_files,
            },
        )

    async def _call_fit(
        self,
        config_file: str,
        *,
        timeout_sec: float,
        extra_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._call_fit_tool(
            "run_galfits_image_fitting",
            {
                "config_file": config_file,
                "timeout_sec": int(timeout_sec),
                "extra_args": extra_args or ["--fit_method", "ES"],
            },
            timeout_sec=timeout_sec,
            phase="baseline image fit",
        )

    async def _call_fit_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_sec: float,
        phase: str,
    ) -> dict[str, Any]:
        try:
            result = await self.client.call_tool(
                tool,
                arguments,
                timeout_sec=timeout_sec,
            )
        except MCPToolError as exc:
            if _is_gpu_oom(str(exc)):
                raise GPUOutOfMemoryError(f"GPU OOM during {phase}") from exc
            raise
        if not _is_success(result) and _is_gpu_oom(result):
            raise GPUOutOfMemoryError(f"GPU OOM during {phase}")
        return result

    async def _build_manifest(
        self,
        *,
        config_file: str,
        fit_result: dict[str, Any],
        object_id: str,
        round_id: str,
    ) -> dict[str, Any]:
        return await self.client.call_tool(
            "build_workflow_round_manifest",
            {
                "mode": self.manifest["workflow_mode"],
                "config_file": config_file,
                "fit_result": fit_result,
                "object_id": object_id,
                "round_id": round_id,
            },
        )

    async def _write_round_note(
        self,
        object_id: str,
        round_dir: Path,
        *,
        round_id: str,
        decision: dict[str, Any] | None = None,
    ) -> Path:
        note = self._object_dir(object_id) / "working_note.md"
        if not note.exists():
            _write_text_atomic(
                note,
                "# Working Note\n\n"
                "- Round 0: 原图成分预测\n"
                "  - 高概率存在成分：由结构化 numeric／VLM 证据和规则层确定，"
                "本文件不单独产生动作。\n",
            )
        if decision is not None:
            action = decision.get("action") or {}
            line = (
                f"- {round_id}: 结构化决策记录\n"
                f"  - resolved action：{action.get('action_type', 'NONE')}\n"
                f"  - round artifact：{round_dir.resolve()}\n"
            )
            with note.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return note

    async def _record_baseline_lifecycle(
        self,
        *,
        round_dir: Path,
        workflow_manifest: dict[str, Any],
        resolution: dict[str, Any],
        fit_result: dict[str, Any],
        state_file: Path,
    ) -> dict[str, Any]:
        lifecycle_file = round_dir / "lifecycle.json"
        if lifecycle_file.exists():
            return _write_lifecycle_artifact(
                lifecycle_file,
                _read_json(lifecycle_file),
            )
        decision = resolution["decision"]
        result = await self.client.call_tool(
            "record_workflow_fit_lifecycle",
            {
                "workflow_manifest": workflow_manifest,
                "raw_decision": decision.get("raw_decision", decision),
                "resolved_decision": decision,
                "fit_result": fit_result,
                "state_file": str(state_file),
                "lifecycle_file": str(lifecycle_file),
                "decision_ref": resolution.get("decision_ref"),
                "action_summary": resolution.get("action_summary"),
            },
        )
        return _write_lifecycle_artifact(lifecycle_file, result)

    async def _run_downstream(
        self,
        *,
        object_id: str,
        config_file: str,
        fit_result: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if not self._downstream_is_eligible(state):
            self._mark_downstream_not_run(
                state, "Image best round is not verifier-authorized and LOCKED"
            )
            return
        state["downstream"] = {"image": "COMPLETED"}
        workplace = fit_result.get("workplace")
        if not workplace:
            state["downstream"].update({
                "sed": "FAILED_NEEDS_REVIEW",
                "image_sed": "NOT_RUN",
                "reason": "image fit did not return an explicit workplace",
            })
            return
        sed = await self._call_fit_tool(
            "run_galfits_sed_fitting",
            {
                "config_file": config_file,
                "image_fitting_workplace": str(workplace),
            },
            timeout_sec=float(self.manifest["options"].get("timeout_sec", 3600)),
            phase="SED fit",
        )
        _write_json(self._object_dir(object_id) / "sed_result.json", sed)
        if not _is_success(sed):
            state["downstream"].update({
                "sed": "FAILED_NEEDS_REVIEW",
                "image_sed": "NOT_RUN",
            })
            return
        state["downstream"]["sed"] = "COMPLETED"
        sed_config = sed.get("new_lyric_file")
        if not sed_config:
            state["downstream"].update({
                "image_sed": "FAILED_NEEDS_REVIEW",
                "reason": "SED result did not return new_lyric_file",
            })
            return
        image_sed = await self._call_fit_tool(
            "run_galfits_image_sed_fitting",
            {"config_file": str(sed_config)},
            timeout_sec=float(self.manifest["options"].get("timeout_sec", 3600)),
            phase="image-SED fit",
        )
        _write_json(self._object_dir(object_id) / "image_sed_result.json", image_sed)
        state["downstream"]["image_sed"] = (
            "COMPLETED" if _is_success(image_sed) else "FAILED_NEEDS_REVIEW"
        )

    async def _finish_with_review(
        self,
        *,
        object_id: str,
        state: dict[str, Any],
        config_file: str,
        fit_result: dict[str, Any],
        reason: str,
        workflow_manifest: dict[str, Any],
        resolution: dict[str, Any],
        round_dir: Path,
        state_file: Path,
    ) -> dict[str, Any]:
        state["status"] = "COMPLETED_WITH_REVIEW"
        state["needs_review"] = True
        state["termination_reason"] = reason
        await self._record_review_terminal_lifecycle(
            state=state,
            fit_result=fit_result,
            reason=reason,
            workflow_manifest=workflow_manifest,
            resolution=resolution,
            round_dir=round_dir,
            state_file=state_file,
        )
        self._mark_downstream_not_run(state, reason)
        self._render_round(object_id=object_id, round_dir=round_dir, state=state)
        self._persist_round_artifacts_and_render(
            object_id=object_id,
            round_dir=round_dir,
            workflow_manifest=workflow_manifest,
            state=state,
        )
        self._render_final_report(object_id, state)
        self._save_runner_state(object_id, state)
        return state

    async def _record_review_terminal_lifecycle(
        self,
        *,
        state: dict[str, Any],
        fit_result: dict[str, Any],
        reason: str,
        workflow_manifest: dict[str, Any],
        resolution: dict[str, Any],
        round_dir: Path,
        state_file: Path,
    ) -> None:
        terminal_decision = _terminal_review_decision(
            resolution["decision"],
            reason=reason,
        )
        terminal_file = round_dir / "terminal_lifecycle.json"
        decision_ref = resolution.get("decision_ref")
        if decision_ref:
            decision_ref = f"{decision_ref}#terminal-review"
        lifecycle = await self.client.call_tool(
            "record_workflow_fit_lifecycle",
            {
                "workflow_manifest": workflow_manifest,
                "raw_decision": terminal_decision.get(
                    "raw_decision", terminal_decision
                ),
                "resolved_decision": terminal_decision,
                "fit_result": fit_result,
                "state_file": str(state_file),
                "lifecycle_file": str(terminal_file),
                "decision_ref": decision_ref,
                "action_summary": _terminal_action_summary(state),
            },
        )
        _write_lifecycle_artifact(terminal_file, lifecycle)
        state["policy_state_file"] = str(state_file)
        state["terminal_lifecycle_file"] = str(terminal_file)

    async def _repair_terminal_review_policy(
        self,
        *,
        object_id: str,
        state: dict[str, Any],
    ) -> None:
        state_file = self._object_dir(object_id) / "policy_state.json"
        for lifecycle_file in self._object_dir(object_id).glob(
            "round_*/*lifecycle.json"
        ):
            _write_lifecycle_artifact(
                lifecycle_file,
                _read_json(lifecycle_file),
            )
        if _policy_review_terminal(state_file):
            return
        fit_file = state.get("current_fit_file")
        manifest_file = state.get("current_manifest_file")
        if not fit_file or not manifest_file:
            return
        fit_path = Path(fit_file)
        manifest_path = Path(manifest_file)
        resolution_file = manifest_path.parent / "resolution" / "resolution.json"
        if not all(
            path.is_file()
            for path in (fit_path, manifest_path, resolution_file)
        ):
            return
        await self._record_review_terminal_lifecycle(
            state=state,
            fit_result=_read_json(fit_path),
            reason=state.get("termination_reason") or "workflow requires review",
            workflow_manifest=_read_json(manifest_path),
            resolution=_read_json(resolution_file),
            round_dir=manifest_path.parent,
            state_file=state_file,
        )
        self._save_runner_state(object_id, state)

    async def _verify_and_maybe_lock(
        self,
        *,
        object_id: str,
        round_dir: Path,
        workflow_manifest: dict[str, Any],
        lifecycle: dict[str, Any],
        fit_result: dict[str, Any],
        state_file: Path,
        state: dict[str, Any],
    ) -> str:
        component_file = self._render_round(
            object_id=object_id, round_dir=round_dir, state=state
        )
        verifier_file = round_dir / "workflow_verifier.json"
        verifier = await self.client.call_tool(
            "workflow_verify_best_round",
            {
                "workflow_manifest": workflow_manifest,
                "lifecycle_file": str(round_dir / "lifecycle.json"),
                "component_analysis_file": str(component_file),
                "output_file": str(verifier_file),
                "verifier_assessment": self.verifier_assessment,
            },
        )
        _write_json(verifier_file, verifier)
        state["verifier_status"] = verifier.get("verdict")
        try:
            validate(verifier, "workflow_verifier")
        except Exception as exc:
            raise BatchManifestError("verifier artifact schema failed") from exc
        if verifier.get("verdict") != "PASS" or not verifier.get("lockable"):
            state["status"] = "COMPLETED_WITH_REVIEW"
            state["needs_review"] = True
            state["termination_reason"] = "verifier did not authorize lock"
            return "COMPLETED_WITH_REVIEW"
        lock = await self.client.call_tool(
            "workflow_lock_best_round",
            {
                "verifier_artifact_ref": str(verifier_file),
                "state_file": str(state_file),
                "lifecycle_file": str(round_dir / "lifecycle.json"),
            },
        )
        _write_json(round_dir / "lock_result.json", lock)
        if lock.get("status") != "LOCKED":
            state["status"] = "COMPLETED_WITH_REVIEW"
            state["needs_review"] = True
            state["termination_reason"] = "best-round lock did not succeed"
            return "COMPLETED_WITH_REVIEW"
        state["verifier_status"] = "PASS"
        state["best_round_status"] = "LOCKED"
        state["sed_joint_eligible"] = True
        state["lock_result_file"] = str(round_dir / "lock_result.json")
        state["status"] = "COMPLETED"
        state["needs_review"] = False
        state["termination_reason"] = "best round locked"
        return "COMPLETED"

    async def _run_object(self, item: dict[str, Any]) -> dict[str, Any]:
        object_id = item["object_id"]
        object_dir = self._object_dir(object_id)
        object_dir.mkdir(parents=True, exist_ok=True)
        state = load_object_state(
            self.manifest["run_dir"],
            object_id,
            preflight_status=item["preflight_status"],
        )
        resume_handled = False
        if (
            state["status"] in TERMINAL_STATES | {"RUNNING"}
            and self.manifest["options"].get("resume")
        ):
            current_fit_file = state.get("current_fit_file")
            current_manifest_file = state.get("current_manifest_file")
            downstream = state.get("downstream") or {}
            can_resume_downstream = (
                state["status"] in {"RUNNING", "COMPLETED_WITH_REVIEW"}
                and current_fit_file
                and current_manifest_file
                and Path(current_fit_file).is_file()
                and Path(current_manifest_file).is_file()
                and downstream.get("image") == "COMPLETED"
                and any(
                    downstream.get(stage) != "COMPLETED"
                    for stage in ("sed", "image_sed")
                )
            )
            if can_resume_downstream:
                resume_handled = True
                fit_result = _read_json(Path(current_fit_file))
                workflow_manifest = _read_json(Path(current_manifest_file))
                if _is_success(fit_result):
                    await self._run_downstream(
                        object_id=object_id,
                        config_file=workflow_manifest["config_file"],
                        fit_result=fit_result,
                        state=state,
                    )
                    if any(
                        value == "FAILED_NEEDS_REVIEW"
                        for value in state.get("downstream", {}).values()
                    ):
                        state["needs_review"] = True
                    elif all(
                        state.get("downstream", {}).get(stage) == "COMPLETED"
                        for stage in ("image", "sed", "image_sed")
                    ):
                        state["termination_reason"] = "downstream completed after interruption"
                    self._save_runner_state(object_id, state)
            elif (
                state["status"] == "RUNNING"
                and state.get("current_fit_file")
                and Path(state["current_fit_file"]).is_file()
                and all(
                    state.get("downstream", {}).get(stage) == "COMPLETED"
                    for stage in ("image", "sed", "image_sed")
                )
            ):
                resume_handled = True
                state["status"] = "COMPLETED_WITH_REVIEW"
                state["needs_review"] = True
                state["termination_reason"] = (
                    "downstream completed after interrupted workflow"
                )
                self._save_runner_state(object_id, state)
            if state["status"] == "COMPLETED_WITH_REVIEW":
                await self._repair_terminal_review_policy(
                    object_id=object_id,
                    state=state,
                )
            if state["status"] in TERMINAL_STATES or resume_handled:
                return state
        if item["preflight_status"] != "READY":
            state["status"] = "PREFLIGHT_FAILED"
            state["needs_review"] = True
            state["termination_reason"] = "batch input preflight failed"
            state["downstream"] = {
                "image": "NOT_RUN",
                "sed": "NOT_RUN",
                "image_sed": "NOT_RUN",
            }
            self._render_final_report(object_id, state)
            self._save_runner_state(object_id, state)
            return state
        state["status"] = "RUNNING"
        state["resource_profile"] = getattr(
            self.client,
            "resource_profile",
            self.manifest["options"].get("resource_profile", "serial-gpu"),
        )
        self._save_runner_state(object_id, state)

        timeout_sec = float(self.manifest["options"].get("timeout_sec", 3600))
        source_config = str(item["config_file"])
        if (
            self.manifest["options"].get("resume")
            and (
                state.get("current_config_file")
                or state.get("baseline_config_file")
            )
            and Path(
                state.get("current_config_file")
                or state.get("baseline_config_file")
            ).is_file()
        ):
            config_file = str(
                Path(
                    state.get("current_config_file")
                    or state["baseline_config_file"]
                ).resolve()
            )
        else:
            config_file = self._prepare_run_scoped_baseline(object_id, source_config)
        state["baseline_config_file"] = config_file
        state["current_config_file"] = config_file
        baseline_check = await self.client.call_tool(
            "check_lyric_file", {"lyric_file": config_file}
        )
        _write_json(self._persistent_root(object_id) / "baseline_lyric_check.json", baseline_check)
        if not _is_success(baseline_check):
            state["status"] = "FAILED_NEEDS_REVIEW"
            state["needs_review"] = True
            state["termination_reason"] = "run-scoped baseline lyric failed check_lyric_file"
            state["downstream"] = {
                "image": "NOT_RUN", "sed": "NOT_RUN", "image_sed": "NOT_RUN"
            }
            self._render_final_report(object_id, state)
            self._save_runner_state(object_id, state)
            return state
        detection_file = object_dir / "round0_detection.json"
        if detection_file.exists() and self.manifest["options"].get("resume"):
            round0_detection = _read_json(detection_file)
        else:
            try:
                round0_detection = await self.client.call_tool(
                    "detect_galfits_bar_lopsidedness",
                    {"lyric_file": config_file, "survey": "JWST"},
                    timeout_sec=timeout_sec,
                )
            except Exception as exc:
                round0_detection = {
                    "status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            _write_json(detection_file, round0_detection)
        state["round0_detection_file"] = str(detection_file)
        fit_file = object_dir / "current_fit_result.json"
        if fit_file.exists() and self.manifest["options"].get("resume"):
            fit_result = _read_json(fit_file)
        else:
            fit_result = await self._call_fit(config_file, timeout_sec=timeout_sec)
            _write_json(fit_file, fit_result)
        if not _is_success(fit_result):
            state["status"] = "FAILED_NEEDS_REVIEW"
            state["needs_review"] = True
            state["termination_reason"] = "baseline MCP fit failed"
            state["downstream"] = {
                "image": "NOT_RUN",
                "sed": "NOT_RUN",
                "image_sed": "NOT_RUN",
            }
            self._render_final_report(object_id, state)
            self._save_runner_state(object_id, state)
            return state
        state["baseline_fit_file"] = str(fit_file)
        state["current_fit_file"] = str(fit_file)
        rejected_fingerprints = set(
            state.get("rejected_action_fingerprints", [])
        ) | _persisted_rejected_action_fingerprints(object_dir)
        rejected_baseline_fingerprints = set(
            state.get("rejected_action_baseline_fingerprints", [])
        ) | _persisted_rejected_action_baseline_fingerprints(object_dir)
        state["rejected_action_fingerprints"] = sorted(rejected_fingerprints)
        state["rejected_action_baseline_fingerprints"] = sorted(
            rejected_baseline_fingerprints
        )
        self._save_runner_state(object_id, state)
        current_fit = fit_result
        current_config = config_file

        for round_index in range(int(state.get("round_index", 0)), self.max_rounds):
            round_id = f"{object_id}_workflow_round{round_index}"
            round_dir = object_dir / f"round_{round_index:03d}"
            round_dir.mkdir(parents=True, exist_ok=True)
            pending = state.get("pending_evidence_proposal")
            if (
                isinstance(pending, dict)
                and Path(str(pending.get("manifest_file", ""))).is_file()
                and Path(str(pending.get("proposal_file", ""))).is_file()
            ):
                workflow_manifest_file = Path(pending["manifest_file"])
                workflow_manifest = _read_json(workflow_manifest_file)
                state["pending_evidence_proposal"] = None
                proposal_file = Path(pending["proposal_file"])
                proposal = _read_json(proposal_file)
                use_pending_proposal = True
            else:
                workflow_manifest_file = round_dir / "workflow_manifest.json"
                use_pending_proposal = False
                if workflow_manifest_file.exists() and self.manifest["options"].get("resume"):
                    workflow_manifest = _read_json(workflow_manifest_file)
                else:
                    workflow_manifest = await self._build_manifest(
                        config_file=current_config,
                        fit_result=current_fit,
                        object_id=object_id,
                        round_id=round_id,
                    )
                    _write_json(workflow_manifest_file, workflow_manifest)
            try:
                validate(workflow_manifest, "workflow_round_manifest")
            except Exception as exc:
                raise BatchManifestError("workflow manifest schema failed") from exc
            state["current_manifest_file"] = str(workflow_manifest_file)
            if not use_pending_proposal:
                proposal_dir = round_dir / "proposal"
                proposal_file = proposal_dir / "workflow_proposal.json"
                if proposal_file.exists() and self.manifest["options"].get("resume"):
                    proposal = _read_json(proposal_file)
                else:
                    proposal = await self.client.call_tool(
                        "workflow_propose_round",
                        {
                            "workflow_manifest": workflow_manifest,
                            "output_dir": str(proposal_dir),
                            "use_vlm": bool(self.manifest["options"].get("use_vlm")),
                            "round0_detection": round0_detection,
                        },
                        timeout_sec=timeout_sec,
                    )
                    _write_json(proposal_file, proposal)
            try:
                validate(proposal, "workflow_proposal")
            except Exception as exc:
                raise BatchManifestError("workflow proposal schema failed") from exc
            resolution_dir = round_dir / "resolution"
            resolution_file = resolution_dir / "resolution.json"
            state_file = object_dir / "policy_state.json"
            if resolution_file.exists() and self.manifest["options"].get("resume"):
                resolution = _read_json(resolution_file)
            else:
                resolution = await self.client.call_tool(
                    "workflow_resolve_round",
                    {
                        "proposal": proposal,
                        "state_file": str(state_file),
                        "output_dir": str(resolution_dir),
                    },
                )
                _write_json(resolution_file, resolution)
            decision = resolution["decision"]
            try:
                validate(decision, "decision_artifact")
            except Exception as exc:
                raise BatchManifestError("decision artifact schema failed") from exc
            lifecycle = await self._record_baseline_lifecycle(
                round_dir=round_dir,
                workflow_manifest=workflow_manifest,
                resolution=resolution,
                fit_result=current_fit,
                state_file=state_file,
            )
            component_file = self._render_round(
                object_id=object_id, round_dir=round_dir, state=state
            )
            analysis_artifact = await self.client.call_tool(
                "workflow_analysis_artifact",
                {
                    "workflow_manifest": workflow_manifest,
                    "decision_artifact": decision,
                    "component_analysis_file": str(component_file),
                    "working_note_file": state["working_note_file"],
                },
            )
            _write_json(round_dir / "workflow_analysis_artifact.json", analysis_artifact)
            self._persist_round_artifacts_and_render(
                object_id=object_id,
                round_dir=round_dir,
                workflow_manifest=workflow_manifest,
                state=state,
            )
            self._record_action_summary(state, resolution.get("action_summary"))
            action = decision.get("action") or {}
            action_type = action.get("action_type")
            state["round_index"] = round_index
            state["current_round_id"] = round_id
            state["pending_action"] = action
            self._save_runner_state(object_id, state)

            action_fingerprint = _action_attempt_fingerprint(
                action, workflow_manifest, proposal
            )
            action_baseline_fingerprint = _action_baseline_fingerprint(
                action, workflow_manifest
            )
            if (
                action_type
                in {
                    "PROPOSE_ADD", "PROPOSE_REPLACE", "PROPOSE_REMOVE",
                    "REFIT_PARAMETERS", "PROMOTE_SINGLE_SERSIC_TO_DISK",
                }
                and (
                    action_fingerprint in rejected_fingerprints
                    or action_baseline_fingerprint in rejected_baseline_fingerprints
                )
            ):
                state["pending_action"] = None
                state["round_index"] = round_index + 1
                return await self._finish_with_review(
                    object_id=object_id,
                    state=state,
                    config_file=current_config,
                    fit_result=current_fit,
                    reason="duplicate rejected action on unchanged baseline",
                    workflow_manifest=workflow_manifest,
                    resolution=resolution,
                    round_dir=round_dir,
                    state_file=state_file,
                )

            preflight = await self.client.call_tool(
                "workflow_action_preflight",
                {
                    "decision_artifact": decision,
                    "workflow_manifest": workflow_manifest,
                    "allow_remove": self.allow_remove,
                    "remove_pilot_passed": self.remove_pilot_passed,
                },
            )
            _write_json(round_dir / "action_preflight.json", preflight)
            if action_type in {
                "PROPOSE_ADD", "PROPOSE_REPLACE", "PROPOSE_REMOVE",
                "REFIT_PARAMETERS", "PROMOTE_SINGLE_SERSIC_TO_DISK",
            }:
                if not preflight.get("ok"):
                    self._record_action_summary(state, preflight.get("action_summary"))
                    return await self._finish_with_review(
                        object_id=object_id,
                        state=state,
                        config_file=current_config,
                        fit_result=current_fit,
                        reason=preflight.get("reason_code", "action preflight failed"),
                        workflow_manifest=workflow_manifest,
                        resolution=resolution,
                        round_dir=round_dir,
                        state_file=state_file,
                    )
                target_config = _next_action_config(
                    current_config,
                    round_index + 1,
                    self._persistent_root(object_id) / "configs",
                )
                action_config = await self.client.call_tool(
                    "workflow_action_config",
                    {
                        "decision_artifact": decision,
                        "workflow_manifest": workflow_manifest,
                        "target_config": target_config,
                        "allow_remove": self.allow_remove,
                        "remove_pilot_passed": self.remove_pilot_passed,
                    },
                )
                _write_json(round_dir / "action_config.json", action_config)
                if not action_config.get("ok"):
                    return await self._finish_with_review(
                        object_id=object_id,
                        state=state,
                        config_file=current_config,
                        fit_result=current_fit,
                        reason=action_config.get(
                            "reason_code", "action config generation failed"
                        ),
                        workflow_manifest=workflow_manifest,
                        resolution=resolution,
                        round_dir=round_dir,
                        state_file=state_file,
                    )
                lyric_check = await self.client.call_tool(
                    "check_lyric_file", {"lyric_file": action_config["config_file"]}
                )
                _write_json(round_dir / "lyric_check.json", lyric_check)
                candidate_tool = action_config["mcp_call"]["tool"]
                if (
                    self.manifest["workflow_mode"] == "multi-band"
                    and candidate_tool != "run_galfits_image_fitting"
                ):
                    raise BatchManifestError(
                        "multi-band candidate must use run_galfits_image_fitting"
                    )
                if not _is_success(lyric_check):
                    raise BatchManifestError("generated lyric did not pass check_lyric_file")
                candidate_fit = await self._call_fit_tool(
                    candidate_tool,
                    action_config["mcp_call"]["arguments"],
                    timeout_sec=timeout_sec,
                    phase=f"candidate image fit for {action_type}",
                )
                candidate_fit_file = round_dir / "candidate_fit_result.json"
                _write_json(candidate_fit_file, candidate_fit)
                candidate_config = action_config["config_file"]
                if not _is_success(candidate_fit):
                    return await self._finish_with_review(
                        object_id=object_id,
                        state=state,
                        config_file=current_config,
                        fit_result=current_fit,
                        reason="candidate MCP fit failed",
                        workflow_manifest=workflow_manifest,
                        resolution=resolution,
                        round_dir=round_dir,
                        state_file=state_file,
                    )
                candidate_manifest = await self._build_manifest(
                    config_file=candidate_config,
                    fit_result=candidate_fit,
                    object_id=object_id,
                    round_id=f"{round_id}_candidate",
                )
                _write_json(round_dir / "candidate_manifest.json", candidate_manifest)
                try:
                    validate(candidate_manifest, "workflow_round_manifest")
                except Exception as exc:
                    raise BatchManifestError("candidate manifest schema failed") from exc
                complete = await self.client.call_tool(
                    "workflow_complete_candidate",
                    {
                        "baseline_manifest": workflow_manifest,
                        "candidate_manifest": candidate_manifest,
                        "baseline_fit_result": current_fit,
                        "candidate_fit_result": candidate_fit,
                        "component": _action_component(action, workflow_manifest),
                        "candidate_action_type": action_type,
                        "candidate_reason_code": action.get("reason_code"),
                        "state_file": str(state_file),
                        "lifecycle_file": str(round_dir / "candidate_lifecycle.json"),
                        "evidence_file": str(round_dir / "refit_evidence.json"),
                        "decision_ref": resolution.get("decision_ref"),
                    },
                )
                _write_json(round_dir / "candidate_completion.json", complete)
                evaluation = complete.get("evaluation")
                if not isinstance(evaluation, dict):
                    raise BatchManifestError(
                        "candidate completion did not return structured refit evaluation"
                    )
                evaluated = await self.client.call_tool(
                    "workflow_evaluate_refit",
                    {
                        "round_id": candidate_manifest["round_id"],
                        "component": _action_component(action, workflow_manifest) or "unknown",
                        "refit_evaluation": evaluation,
                        "state_file": str(state_file),
                        "candidate_action_type": action_type,
                        "candidate_reason_code": action.get("reason_code"),
                        "evidence_refs": {"manifest": str(round_dir / "refit_evidence.json")},
                        "decision_ref": resolution.get("decision_ref"),
                        "object_id": object_id,
                        "config_ref": str(candidate_config),
                    },
                )
                _write_json(round_dir / "refit_decision.json", evaluated)
                self._render_round(
                    object_id=object_id, round_dir=round_dir, state=state
                )
                self._persist_round_artifacts_and_render(
                    object_id=object_id,
                    round_dir=round_dir,
                    workflow_manifest=workflow_manifest,
                    state=state,
                )
                self._record_action_summary(state, complete.get("action_summary"))
                self._record_action_summary(state, evaluated.get("action_summary"))
                state["pending_candidate"] = None
                refit_verdict = (evaluated.get("action_summary") or {}).get("refit_verdict") or complete.get("action_summary", {}).get("refit_verdict")
                if refit_verdict == "ACCEPTED":
                    current_fit = candidate_fit
                    current_config = candidate_config
                    state["current_config_file"] = str(candidate_config)
                    state["current_fit_file"] = str(candidate_fit_file)
                    state["round_index"] = round_index + 1
                    self._save_runner_state(object_id, state)
                    continue
                if refit_verdict == "REJECTED":
                    rejected_fingerprints.add(action_fingerprint)
                    rejected_baseline_fingerprints.add(action_baseline_fingerprint)
                    state["rejected_action_fingerprints"] = sorted(
                        rejected_fingerprints
                    )
                    state["rejected_action_baseline_fingerprints"] = sorted(
                        rejected_baseline_fingerprints
                    )
                state["round_index"] = round_index + 1
                self._save_runner_state(object_id, state)
                continue

            if action_type == "KEEP_AND_CONTINUE":
                has_collector = (
                    action.get("next_transition") == "COLLECT_EVIDENCE"
                    and bool(action.get("evidence_targets"))
                )
                if "next_transition" not in action:
                    # Compatibility for pre-transition fixtures only. New
                    # decisions must carry an explicit collector contract.
                    state["round_index"] = round_index + 1
                    self._save_runner_state(object_id, state)
                    continue
                if not has_collector:
                    return await self._finish_with_review(
                        object_id=object_id,
                        state=state,
                        config_file=current_config,
                        fit_result=current_fit,
                        reason="KEEP_AND_CONTINUE has no explicit evidence collector",
                        workflow_manifest=workflow_manifest,
                        resolution=resolution,
                        round_dir=round_dir,
                        state_file=state_file,
                    )
                collector_dir = round_dir / "evidence_collection"
                collector_dir.mkdir(parents=True, exist_ok=True)
                collector_manifest = dict(workflow_manifest)
                collector_manifest["round_id"] = f"{round_id}_evidence{round_index + 1}"
                collector_manifest_file = collector_dir / "workflow_manifest.json"
                collector_proposal_dir = collector_dir / "proposal"
                collector_proposal_file = collector_proposal_dir / "workflow_proposal.json"
                collected = await self.client.call_tool(
                    "workflow_propose_round",
                    {
                        "workflow_manifest": collector_manifest,
                        "output_dir": str(collector_proposal_dir),
                        "use_vlm": bool(self.manifest["options"].get("use_vlm")),
                        "previous_round_ref": str(proposal_file),
                        "round0_detection": round0_detection,
                    },
                    timeout_sec=timeout_sec,
                )
                _write_json(collector_manifest_file, collector_manifest)
                _write_json(collector_proposal_file, collected)
                validate(collected, "workflow_proposal")
                if collected.get("evidence_fingerprint") == proposal.get("evidence_fingerprint"):
                    return await self._finish_with_review(
                        object_id=object_id,
                        state=state,
                        config_file=current_config,
                        fit_result=current_fit,
                        reason="COLLECT_EVIDENCE produced unchanged evidence fingerprint",
                        workflow_manifest=workflow_manifest,
                        resolution=resolution,
                        round_dir=round_dir,
                        state_file=state_file,
                    )
                state["pending_evidence_proposal"] = {
                    "manifest_file": str(collector_manifest_file),
                    "proposal_file": str(collector_proposal_file),
                    "collector_id": action.get("collector_id"),
                    "source_round_id": round_id,
                }
                state["round_index"] = round_index + 1
                self._save_runner_state(object_id, state)
                continue

            state["pending_action"] = None
            if action_type == "CONVERGED" or decision.get("workflow_status") == "CONVERGED":
                status = await self._verify_and_maybe_lock(
                    object_id=object_id,
                    round_dir=round_dir,
                    workflow_manifest=workflow_manifest,
                    lifecycle=lifecycle,
                    fit_result=current_fit,
                    state_file=state_file,
                    state=state,
                )
            else:
                return await self._finish_with_review(
                    object_id=object_id,
                    state=state,
                    config_file=current_config,
                    fit_result=current_fit,
                    reason="Image workflow did not reach CONVERGED",
                    workflow_manifest=workflow_manifest,
                    resolution=resolution,
                    round_dir=round_dir,
                    state_file=state_file,
                )
            if status != "COMPLETED":
                return await self._finish_with_review(
                    object_id=object_id,
                    state=state,
                    config_file=current_config,
                    fit_result=current_fit,
                    reason=state.get("termination_reason") or "Image verifier did not lock best round",
                    workflow_manifest=workflow_manifest,
                    resolution=resolution,
                    round_dir=round_dir,
                    state_file=state_file,
                )
            await self._run_downstream(
                object_id=object_id,
                config_file=current_config,
                fit_result=current_fit,
                state=state,
            )
            if any(
                value == "FAILED_NEEDS_REVIEW"
                for value in state.get("downstream", {}).values()
            ):
                state["status"] = "COMPLETED_WITH_REVIEW"
                state["needs_review"] = True
            self._render_round(object_id=object_id, round_dir=round_dir, state=state)
            self._persist_round_artifacts_and_render(
                object_id=object_id,
                round_dir=round_dir,
                workflow_manifest=workflow_manifest,
                state=state,
            )
            self._render_final_report(object_id, state)
            self._save_runner_state(object_id, state)
            return state

        return await self._finish_with_review(
            object_id=object_id,
            state=state,
            config_file=current_config,
            fit_result=current_fit,
            reason=f"round budget exhausted: {self.max_rounds}",
            workflow_manifest=workflow_manifest,
            resolution=resolution,
            round_dir=round_dir,
            state_file=state_file,
        )

    async def run(self, *, object_ids: set[str] | None = None) -> dict[str, Any]:
        capabilities = await self.client.call_tool("workflow_capabilities")
        if not capabilities.get("pilot_enabled"):
            raise BatchManifestError("MCP workflow pilot is not enabled")
        if self.manifest["workflow_mode"] not in capabilities.get("workflow_modes", []):
            raise BatchManifestError("MCP server does not advertise requested workflow mode")
        if (
            self.manifest["options"].get("use_vlm")
            and not capabilities.get("timing_log_enabled")
        ):
            raise BatchManifestError(
                "VLM_TIMING_LOG=1 is required for structured VLM batch execution"
            )
        results = []
        for item in self.manifest["objects"]:
            if object_ids is not None and item["object_id"] not in object_ids:
                continue
            try:
                results.append(await self._run_object(item))
            except Exception as exc:
                state = load_object_state(
                    self.manifest["run_dir"],
                    item["object_id"],
                    preflight_status=item["preflight_status"],
                )
                state["status"] = "FAILED_NEEDS_REVIEW"
                state["needs_review"] = True
                state["termination_reason"] = f"{type(exc).__name__}: {exc}"
                current_fit_file = state.get("current_fit_file")
                if current_fit_file and Path(current_fit_file).is_file() and _is_success(
                    _read_json(Path(current_fit_file))
                ):
                    self._mark_downstream_not_run(state, state["termination_reason"])
                else:
                    state["downstream"] = {
                        "image": "NOT_RUN", "sed": "NOT_RUN", "image_sed": "NOT_RUN"
                    }
                self._render_final_report(item["object_id"], state)
                _write_json(
                    self._object_dir(item["object_id"]) / "error.json",
                    {"error": state["termination_reason"]},
                )
                self._save_runner_state(item["object_id"], state)
                results.append(state)
        summary_path = write_batch_summary(self.manifest)
        capacity_path = self._write_batch_capacity_report()
        return {
            "capabilities": capabilities,
            "results": results,
            "summary_file": str(summary_path),
            "capacity_report_file": str(capacity_path),
            "summary": batch_summary(self.manifest),
        }


def _truthy(value: str | int | bool) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _gpu_oom_object_ids(manifest: dict[str, Any]) -> list[str]:
    object_ids = []
    for item in manifest["objects"]:
        state = load_object_state(
            manifest["run_dir"],
            item["object_id"],
            preflight_status=item["preflight_status"],
        )
        if state["status"] != "FAILED_NEEDS_REVIEW":
            continue
        if str(state.get("termination_reason", "")).startswith(
            "GPUOutOfMemoryError:"
        ):
            object_ids.append(item["object_id"])
    return object_ids


def _prepare_cpu_fallback(manifest: dict[str, Any], object_ids: list[str]) -> None:
    for object_id in object_ids:
        state = load_object_state(manifest["run_dir"], object_id)
        previous_reason = state.get("termination_reason")
        state.setdefault("resource_switches", []).append(
            {
                "from": "serial-gpu",
                "to": "serial-cpu",
                "reason": previous_reason,
                "recorded_at": _utc_now(),
            }
        )
        state["status"] = "RUNNING"
        state["needs_review"] = False
        state["termination_reason"] = None
        state["resource_profile"] = "serial-cpu"
        save_object_state(manifest["run_dir"], state)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", "--dir", "-d", required=True, type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("single-band", "multi-band", "multiband"),
        default="multi-band",
    )
    parser.add_argument("--pilot", nargs="?", const="1", default="0")
    parser.add_argument("--use-vlm", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--object-id")
    parser.add_argument("--max-objects", type=int)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--resource-profile", default="serial-gpu")
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--verifier-assessment", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    mode = "multi-band" if args.mode == "multiband" else args.mode
    run_dir = args.run_dir.expanduser().resolve()
    manifest_path = run_dir / "batch_manifest.json"
    if args.resume and manifest_path.exists():
        manifest = read_batch_manifest(manifest_path)
        # The CLI flag controls this invocation even when the manifest was
        # created by an earlier non-resume run.
        manifest["options"]["resume"] = True
    else:
        manifest = build_batch_manifest(
            args.root,
            run_dir,
            mode=mode,
            pilot=_truthy(args.pilot),
            use_vlm=args.use_vlm,
            resume=args.resume,
            object_id=args.object_id,
            max_objects=args.max_objects,
            timeout_sec=args.timeout,
            retries=args.retries,
            resource_profile=args.resource_profile,
        )
        write_batch_manifest(manifest)
    selected_object_ids = {args.object_id} if args.object_id is not None else None
    if selected_object_ids is not None and not any(
        item["object_id"] == args.object_id for item in manifest["objects"]
    ):
        raise BatchManifestError(
            f"object {args.object_id!r} is not present in the batch manifest"
        )
    for item in manifest["objects"]:
        state = load_object_state(
            manifest["run_dir"],
            item["object_id"],
            preflight_status=item["preflight_status"],
        )
        save_object_state(manifest["run_dir"], state)
    summary_path = write_batch_summary(manifest)
    print(json.dumps(batch_summary(manifest), ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    if not _truthy(args.pilot):
        raise SystemExit("实际执行必须显式指定 --pilot 1")
    if args.max_rounds < 1:
        raise SystemExit("--max-rounds must be positive")
    verifier_assessment = (
        _read_json(args.verifier_assessment)
        if args.verifier_assessment is not None
        else None
    )

    async def run_batch() -> dict[str, Any]:
        async with MCPToolClient(resource_profile=args.resource_profile) as client:
            coordinator = WorkflowBatchCoordinator(
                manifest,
                client,
                max_rounds=args.max_rounds,
                verifier_assessment=verifier_assessment,
            )
            result = await coordinator.run(object_ids=selected_object_ids)
        if args.resource_profile != "serial-gpu":
            return result
        oom_object_ids = _gpu_oom_object_ids(manifest)
        if selected_object_ids is not None:
            oom_object_ids = [
                object_id
                for object_id in oom_object_ids
                if object_id in selected_object_ids
            ]
        if not oom_object_ids:
            return result
        _prepare_cpu_fallback(manifest, oom_object_ids)
        async with MCPToolClient(resource_profile="serial-cpu") as client:
            coordinator = WorkflowBatchCoordinator(
                manifest,
                client,
                max_rounds=args.max_rounds,
                verifier_assessment=verifier_assessment,
            )
            return await coordinator.run(object_ids=set(oom_object_ids))

    result = asyncio.run(run_batch())
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    failed = sum(
        count
        for status, count in result["summary"]["counts"].items()
        if status in {"FAILED_NEEDS_REVIEW", "PREFLIGHT_FAILED"}
    )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BatchManifestError as exc:
        print(f"batch preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
