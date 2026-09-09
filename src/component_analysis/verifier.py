"""Shared, read-only best-round verification and structured lock gate."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from schemas import validate

from .artifact_adapter import load_workflow_band_arrays
from .policy import load_policy_state, save_policy_state
from .refit_comparator import (
    _comparison_conditions,
    _parameter_health,
    _residual_score,
    normalize_fit_result,
)


VERIFIER_VERSION = "workflow-verifier@v1"
_ASSESSMENT_DIMENSIONS = (
    "validation",
    "components",
    "fit",
    "physical",
    "parameters",
    "metrics",
)


def _resolved_file(value: str | Path, field: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{field} does not exist: {path}")
    return str(path)


def _write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> str:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        raise
    return str(target)


def _read_json_file(path: str | Path, field: str) -> tuple[dict[str, Any], str]:
    resolved = _resolved_file(path, field)
    value = json.loads(Path(resolved).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{field} must contain a JSON object: {resolved}")
    return value, resolved


def _gate(status: str, detail: str, *refs: str) -> dict[str, Any]:
    return {
        "status": status,
        "evidence_refs": [str(ref) for ref in refs if ref],
        "detail": detail,
    }


def _constraint_gate(
    workflow_manifest: Mapping[str, Any],
    fit_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    config_file = Path(str(fit_artifact.get("config_file") or "")).expanduser().resolve()
    refs = [str(config_file)]
    try:
        if workflow_manifest["workflow_mode"] == "single-band":
            from tools.parse_feedme import parse_components, parse_feedme

            parsed = parse_feedme(str(config_file))
            if not parsed or not parse_components(str(config_file)):
                return _gate("FAIL", "single-band config has no parseable model components", *refs)
            constraint = parsed.get("constraint")
            if constraint and str(constraint).strip().lower() != "none":
                constraint_path = Path(str(constraint)).expanduser()
                if not constraint_path.is_absolute():
                    constraint_path = config_file.parent / constraint_path
                constraint_path = constraint_path.resolve()
                refs.append(str(constraint_path))
                if not constraint_path.is_file():
                    return _gate("FAIL", "single-band constraint reference is missing", *refs)
                text = constraint_path.read_text(encoding="utf-8")
                if text.count("def Update_Constraints") > 1:
                    return _gate("FAIL", "single-band constraint file has duplicate update functions", *refs)
            return _gate("PASS", "single-band config and referenced constraints are parseable", *refs)

        from tools.parse_lyric import parse_component_types

        if not parse_component_types(str(config_file)):
            return _gate("FAIL", "multi-band lyric has no parseable profile components", *refs)
        constraint_files = list(workflow_manifest.get("constraint_files") or [])
        constraint_files.extend(fit_artifact.get("constraint_files") or [])
        for value in constraint_files:
            constraint_path = Path(str(value)).expanduser().resolve()
            refs.append(str(constraint_path))
            if not constraint_path.is_file():
                return _gate("FAIL", "multi-band constraint reference is missing", *refs)
            text = constraint_path.read_text(encoding="utf-8")
            if text.count("def Update_Constraints") > 1:
                return _gate("FAIL", "multi-band constraint file has duplicate update functions", *refs)
            compile(text, str(constraint_path), "exec")
        return _gate("PASS", "multi-band lyric and referenced constraints are parseable", *refs)
    except (OSError, SyntaxError, TypeError, ValueError) as exc:
        return _gate("FAIL", f"constraint/config validation failed: {exc}", *refs)


def _artifact_gate(
    workflow_manifest: Mapping[str, Any],
    fit_artifact: Mapping[str, Any],
    component_analysis_file: str,
    fit_artifact_file: str,
) -> dict[str, Any]:
    refs = [
        str(workflow_manifest["config_file"]),
        fit_artifact_file,
        component_analysis_file,
    ]
    if not fit_artifact.get("valid"):
        return _gate(
            "FAIL",
            f"normalized fit artifact is incomplete: {fit_artifact.get('missing_fields', [])}",
            *refs,
        )
    if not Path(component_analysis_file).is_file():
        return _gate("FAIL", "component analysis evidence file is missing", *refs)
    try:
        arrays = load_workflow_band_arrays(dict(workflow_manifest))
    except (OSError, IndexError, RuntimeError, ValueError) as exc:
        return _gate("FAIL", f"workflow result arrays are not readable: {exc}", *refs)
    if len(arrays) != len(workflow_manifest.get("bands", [])):
        return _gate("FAIL", "workflow result array count does not match manifest bands", *refs)
    return _gate("PASS", "fit artifact, result arrays and component analysis evidence are complete", *refs)


def _parameter_gate(workflow_manifest: Mapping[str, Any]) -> dict[str, Any]:
    health, boundary_hits, warnings = _parameter_health(workflow_manifest)
    refs = [str(workflow_manifest["config_file"])]
    if health == "no" or boundary_hits or warnings:
        return _gate(
            "FAIL",
            f"parameter health failed: boundary_hits={boundary_hits}, warnings={warnings}",
            *refs,
        )
    if health != "yes":
        return _gate("INCONCLUSIVE", "fitted component parameters are incomplete", *refs)
    return _gate("PASS", "fitted component parameters are finite and physical", *refs)


def _metrics_gate(
    workflow_manifest: Mapping[str, Any],
    fit_artifact: Mapping[str, Any],
    baseline_manifest: Mapping[str, Any] | None,
    baseline_fit_result: Mapping[str, Any] | None,
    baseline_artifact_file: str | None,
) -> dict[str, Any]:
    refs = [str(workflow_manifest["config_file"]), str(fit_artifact.get("config_file") or "")]
    if baseline_manifest is None:
        return _gate("PASS", "no baseline supplied; comparative metric gate is not applicable", *refs)
    try:
        baseline_artifact = normalize_fit_result(baseline_manifest, baseline_fit_result)
        if not baseline_artifact["valid"]:
            return _gate("FAIL", "baseline fit artifact is incomplete", *(refs + [baseline_artifact_file or ""]))
        conditions = _comparison_conditions(baseline_manifest, workflow_manifest)
        if not conditions["comparable"]:
            return _gate("FAIL", f"baseline and candidate are not comparable: {conditions['reasons']}", *(refs + [baseline_artifact_file or ""]))
        baseline_score = _residual_score(baseline_manifest)
        candidate_score = _residual_score(workflow_manifest)
        if baseline_score is None or candidate_score is None:
            return _gate("FAIL", "comparable residual metrics are unavailable", *(refs + [baseline_artifact_file or ""]))
        return _gate(
            "PASS",
            f"comparable residual metrics are available: baseline={baseline_score:.8g}, candidate={candidate_score:.8g}",
            *(refs + [baseline_artifact_file or ""]),
        )
    except (OSError, IndexError, RuntimeError, ValueError) as exc:
        return _gate("FAIL", f"comparative metric validation failed: {exc}", *(refs + [baseline_artifact_file or ""]))


def _lifecycle_gate(
    workflow_manifest: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    lifecycle_file: str,
    fit_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    refs = [lifecycle_file, str(workflow_manifest["config_file"])]
    try:
        validate(dict(lifecycle), "workflow_lifecycle")
    except Exception as exc:
        return _gate("FAIL", f"lifecycle schema validation failed: {exc}", *refs)
    resolved = lifecycle.get("resolved_decision") or {}
    action = resolved.get("action") or {}
    if lifecycle.get("workflow_mode") != workflow_manifest.get("workflow_mode"):
        return _gate("FAIL", "lifecycle workflow mode does not match manifest", *refs)
    if lifecycle.get("object_id") != workflow_manifest.get("object_id"):
        return _gate("FAIL", "lifecycle object does not match manifest", *refs)
    if lifecycle.get("round_id") != workflow_manifest.get("round_id"):
        return _gate("FAIL", "lifecycle round does not match manifest", *refs)
    if resolved.get("workflow_status") != "CONVERGED" or action.get("action_type") != "CONVERGED":
        return _gate("FAIL", "only a resolved CONVERGED lifecycle can be locked", *refs)
    if lifecycle.get("fit_completion_status") != "FIT_AVAILABLE" or not lifecycle.get("fit_result", {}).get("valid"):
        return _gate("FAIL", "lifecycle does not contain a valid fitted result", *refs)
    if lifecycle.get("best_round_status") not in {"PENDING_VERIFIER", "VERIFIED"}:
        return _gate("FAIL", "lifecycle is not waiting for verifier lock", *refs)
    if lifecycle.get("needs_review"):
        return _gate("FAIL", "lifecycle still requires review", *refs)
    if not fit_artifact.get("valid"):
        return _gate("FAIL", "lifecycle fit result disagrees with normalized fit artifact", *refs)
    return _gate("PASS", "lifecycle is a converged, review-complete candidate awaiting lock", *refs)


def _assessment_result(
    assessment: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    if assessment is None:
        return "MISSING", None
    value = copy.deepcopy(dict(assessment))
    try:
        validate(value, "verifier_assessment")
    except Exception:
        return "FAILED", None
    statuses = [value["dimensions"][key] for key in _ASSESSMENT_DIMENSIONS]
    if "FAIL" in statuses:
        return "FAILED", value
    if "INCONCLUSIVE" in statuses:
        return "INCONCLUSIVE", value
    return "COMPLETE", value


def verify_best_round(
    *,
    workflow_manifest: Mapping[str, Any],
    lifecycle_file: str | Path,
    component_analysis_file: str | Path,
    output_file: str | Path,
    verifier_assessment: Mapping[str, Any] | None = None,
    baseline_manifest: Mapping[str, Any] | None = None,
    baseline_fit_result: Mapping[str, Any] | None = None,
    baseline_artifact_file: str | Path | None = None,
    manifest_ref: str | None = None,
) -> dict[str, Any]:
    """Read artifacts and create a structured verifier result without mutating them."""
    validate(dict(workflow_manifest), "workflow_round_manifest")
    lifecycle, lifecycle_ref = _read_json_file(lifecycle_file, "lifecycle_file")
    component_analysis_ref = str(Path(component_analysis_file).expanduser().resolve())
    output_ref = str(Path(output_file).expanduser().resolve())
    raw_fit = (lifecycle.get("fit_result") or {}).get("raw")
    fit_artifact = normalize_fit_result(workflow_manifest, raw_fit if isinstance(raw_fit, Mapping) else None)
    fit_artifact_ref = str(Path(output_ref).with_name(Path(output_ref).stem + ".fit-artifact.json"))
    _write_json_atomic(fit_artifact_ref, fit_artifact)
    baseline_ref = str(Path(baseline_artifact_file).expanduser().resolve()) if baseline_artifact_file else None

    hard_gates = {
        "artifact_integrity": _artifact_gate(
            workflow_manifest, fit_artifact, component_analysis_ref, fit_artifact_ref
        ),
        "parameter_health": _parameter_gate(workflow_manifest),
        "constraint_integrity": _constraint_gate(workflow_manifest, fit_artifact),
        "comparable_metrics": _metrics_gate(
            workflow_manifest,
            fit_artifact,
            baseline_manifest,
            baseline_fit_result,
            baseline_ref,
        ),
        "lifecycle_integrity": _lifecycle_gate(
            workflow_manifest, lifecycle, lifecycle_ref, fit_artifact
        ),
    }
    assessment_status, assessment_value = _assessment_result(verifier_assessment)
    statuses = [gate["status"] for gate in hard_gates.values()]
    if "FAIL" in statuses or assessment_status == "FAILED":
        verdict = "FAIL"
    elif any(status != "PASS" for status in statuses) or assessment_status != "COMPLETE":
        verdict = "INCONCLUSIVE"
    else:
        verdict = "PASS"
    artifact = {
        "schema_version": VERIFIER_VERSION,
        "workflow_mode": workflow_manifest["workflow_mode"],
        "object_id": workflow_manifest["object_id"],
        "round_id": workflow_manifest["round_id"],
        "verdict": verdict,
        "lockable": verdict == "PASS",
        "hard_gates": hard_gates,
        "assessment_status": assessment_status,
        "verifier_assessment": assessment_value,
        "evidence_refs": {
            "workflow_manifest": manifest_ref or str(workflow_manifest["config_file"]),
            "lifecycle": lifecycle_ref,
            "fit_artifact": fit_artifact_ref,
            "component_analysis": component_analysis_ref,
            "baseline_manifest": (
                str(baseline_manifest.get("config_file")) if baseline_manifest else None
            ),
            "baseline_fit_artifact": baseline_ref,
        },
        "generated_by": {"name": "workflow-verifier", "version": VERIFIER_VERSION},
    }
    validate(artifact, "workflow_verifier")
    _write_json_atomic(output_ref, artifact)
    return artifact


def lock_best_round(
    *,
    verifier_artifact_ref: str | Path,
    state_file: str | Path,
    lifecycle_file: str | Path,
) -> dict[str, Any]:
    """Lock only a verified artifact; callers cannot submit a raw PASS string."""
    verifier, verifier_ref = _read_json_file(verifier_artifact_ref, "verifier_artifact_ref")
    validate(verifier, "workflow_verifier")
    if verifier["verdict"] != "PASS" or verifier["lockable"] is not True:
        raise ValueError("verifier artifact is not lockable")
    lifecycle, lifecycle_ref = _read_json_file(lifecycle_file, "lifecycle_file")
    validate(lifecycle, "workflow_lifecycle")
    if verifier["evidence_refs"]["lifecycle"] != lifecycle_ref:
        raise ValueError("verifier artifact lifecycle reference does not match lock target")
    if verifier["workflow_mode"] != lifecycle["workflow_mode"] or verifier["object_id"] != lifecycle["object_id"] or verifier["round_id"] != lifecycle["round_id"]:
        raise ValueError("verifier artifact identity does not match lifecycle")
    if lifecycle["resolved_decision"].get("workflow_status") != "CONVERGED":
        raise ValueError("only a converged lifecycle can be locked")
    state_path = Path(state_file).expanduser().resolve()
    if not state_path.is_file():
        raise FileNotFoundError(f"state_file does not exist: {state_path}")
    state = load_policy_state(state_path, object_id=verifier["object_id"])
    if state.last_round_id != verifier["round_id"]:
        raise ValueError("policy state round does not match verifier artifact")
    state.verifier_status = "PASS"
    state.best_round_status = "LOCKED"
    state.needs_review = False
    save_policy_state(state, state_path)
    lifecycle = copy.deepcopy(lifecycle)
    lifecycle["best_round_status"] = "LOCKED"
    lifecycle["needs_review"] = False
    lifecycle["next_step"] = "locked_best_round"
    lifecycle["policy_state"] = state.to_dict()
    lifecycle["downstream_handoff"]["best_round_status"] = "LOCKED"
    lifecycle["downstream_handoff"]["sed_joint_eligible"] = bool(lifecycle["fit_result"]["valid"] and lifecycle["workflow_mode"] == "multi-band")
    _write_json_atomic(lifecycle_ref, lifecycle)
    return {
        "status": "LOCKED",
        "verifier_artifact_ref": verifier_ref,
        "lifecycle_ref": lifecycle_ref,
        "state_ref": str(state_path),
        "object_id": verifier["object_id"],
        "round_id": verifier["round_id"],
    }
