"""MCP-facing wrappers for workflow manifest and lifecycle operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from component_analysis import (
    MULTI_BAND,
    SINGLE_BAND,
    build_multi_band_workflow_manifest,
    build_single_band_workflow_manifest,
    build_workflow_analysis_artifact,
    apply_action_to_config,
    complete_workflow_candidate,
    evaluate_workflow_refit,
    preflight_action,
    prepare_mcp_fit_call,
    record_fit_lifecycle,
    load_policy_state,
    lock_best_round,
    verify_best_round,
)


WORKFLOW_TRANSITIONS = (
    "RUN_FIT",
    "COLLECT_EVIDENCE",
    "REVIEW",
    "VERIFY_BEST_ROUND",
    "REPORT_AND_HANDOFF",
    "FAILED_NEEDS_REVIEW",
    "LOCK_BEST_ROUND",
)

WORKFLOW_ACTIONS = (
    "PROPOSE_ADD",
    "PROPOSE_REPLACE",
    "PROPOSE_REMOVE",
    "PROMOTE_SINGLE_SERSIC_TO_DISK",
    "REFIT_PARAMETERS",
    "KEEP_AND_CONTINUE",
    "CONVERGED",
)


def _order_multiband_results(
    result_files: list[str], image_infos: list[dict[str, Any]]
) -> list[str]:
    """Match returned result FITS to lyric bands by their explicit band token."""
    remaining = list(result_files)
    ordered: list[str] = []
    for info in image_infos:
        band = str(info["band"]).lower()
        matches = [
            value for value in remaining
            if f"_{band}_" in Path(value).name.lower()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"could not unambiguously match result FITS to lyric band {info['band']}: "
                f"{matches or remaining}"
            )
        ordered.append(matches[0])
        remaining.remove(matches[0])
    if remaining:
        raise ValueError(f"MCP returned result FITS not present in lyric bands: {remaining}")
    return ordered


def workflow_capabilities() -> dict[str, Any]:
    """Return the client-independent workflow contract and server readiness."""
    truthy = {"1", "true", "yes"}
    return {
        "contract_version": "workflow-contract@v1",
        "bridge_version": "workflow-bridge@v1",
        "schema_versions": {
            "workflow_round_manifest": "1.0",
            "decision_artifact": "1.1",
            "workflow_lifecycle": "1.0",
            "agent_recommendation": "agent-recommendation@v1",
            "workflow_fit_artifact": "workflow-fit-artifact@v1",
            "verifier_assessment": "verifier-assessment@v1",
            "workflow_verifier": "workflow-verifier@v1",
        },
        "workflow_modes": ["single-band", "multi-band"],
        "actions": list(WORKFLOW_ACTIONS),
        "transitions": list(WORKFLOW_TRANSITIONS),
        "client_independent": True,
        "timing_log_enabled": os.getenv("VLM_TIMING_LOG", "0") == "1",
        "timing_log_path_policy": "galaxy_dir/timing_log.md when VLM_TIMING_LOG=1",
        "pilot_enabled": os.getenv(
            "COMPONENT_ANALYSIS_WORKFLOW_PILOT", "0"
        ).lower() in truthy,
        "fitting_entrypoints": {
            "single-band": "run_galfit",
            "multi-band": "run_galfits_image_fitting",
        },
        "agent_authority": {
            "can_rank_candidates": True,
            "can_submit_parameter_plan": True,
            "can_write_config": False,
            "can_execute_fit": False,
            "can_bypass_policy": False,
            "can_submit_verifier_assessment": True,
            "can_lock_best_round": False,
        },
    }


def build_workflow_round_manifest(
    mode: str,
    config_file: str | None = None,
    summary_file: str | None = None,
    result_files: list[str] | None = None,
    object_id: str | None = None,
    round_id: str | None = None,
    bands: list[dict[str, Any]] | None = None,
    comparison_png: str | None = None,
    working_note_file: str | None = None,
    constraint_files: list[str] | None = None,
    fit_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a mode-specific manifest from explicit MCP-returned paths."""
    if fit_result is not None:
        status = str(fit_result.get("status", "")).lower()
        if status not in {"success", "succeeded", "ok", "completed"}:
            raise ValueError("fit_result must describe a successful existing MCP fit")
        config_file = config_file or fit_result.get("input_param_file") or fit_result.get("config_file")
        if mode == SINGLE_BAND:
            result_files = result_files or [fit_result.get("optimized_fits_file")]
            summary_file = summary_file or fit_result.get("summary_file")
            comparison_png = comparison_png or fit_result.get("image_file")
        elif mode == MULTI_BAND:
            result_files = result_files or list(fit_result.get("result_fits") or [])
            summaries = fit_result.get("summary_files") or []
            summary_file = summary_file or (summaries[0] if summaries else None)
            comparison_png = comparison_png or fit_result.get("comparison_png")
            constraint_files = constraint_files or list(fit_result.get("constrain_files") or [])
            if bands is None and config_file and result_files:
                from component_analysis.artifact_adapter import _band_geometry, _parse_lyric

                _, image_infos = _parse_lyric(config_file)
                if len(image_infos) != len(result_files):
                    raise ValueError("lyric band count does not match MCP result_fits count")
                result_files = _order_multiband_results(result_files, image_infos)
                inferred: list[dict[str, Any]] = []
                for info, result_file in zip(image_infos, result_files, strict=True):
                    science = list(info["science"])
                    psf = list(info["psf"])
                    sigma = list(info["sigma"])
                    mask = list(info["mask"])
                    pixscale, fit_region = _band_geometry(
                        science[0], int(science[1]), float(info["fitting_area"])
                    )
                    inferred.append({
                        "band": info["band"],
                        "science_fits": science[0],
                        "science_hdu": int(science[1]),
                        "sigma_fits": None if str(sigma[0]).lower() == "none" else sigma[0],
                        "sigma_hdu": None if str(sigma[0]).lower() == "none" else int(sigma[1]),
                        "mask_fits": None if str(mask[0]).lower() == "none" else mask[0],
                        "mask_hdu": None if str(mask[0]).lower() == "none" else int(mask[1]),
                        "psf_fits": None if str(psf[0]).lower() == "none" else psf[0],
                        "psf_hdu": None if str(psf[0]).lower() == "none" else int(psf[1]),
                        "result_fits": result_file,
                        "result_hdus": {
                            "original_hdu": 4,
                            "model_hdu": 3,
                            "residual_hdu": 0,
                        },
                        "pixscale_arcsec": pixscale,
                        "fit_region": fit_region,
                        "validation": {"paths_explicit": True},
                    })
                bands = inferred
        object_id = object_id or config_file
        round_id = round_id or str(fit_result.get("workplace") or "fit-round")
    if not config_file or not summary_file or not result_files or not object_id or not round_id:
        raise ValueError("config_file, summary_file, result_files, object_id and round_id are required")
    if mode == SINGLE_BAND:
        if len(result_files) != 1:
            raise ValueError("single-band manifest requires exactly one result file")
        return build_single_band_workflow_manifest(
            config_file=config_file,
            optimized_fits_file=result_files[0],
            summary_file=summary_file,
            comparison_png=comparison_png,
            working_note_file=working_note_file,
            constraint_file=(constraint_files or [None])[0],
            parameter_file=(fit_result or {}).get("output_param_file") if fit_result else None,
            object_id=object_id,
            round_id=round_id,
        )
    if mode != MULTI_BAND:
        raise ValueError(f"unsupported workflow mode: {mode}")
    if not bands or len(bands) != len(result_files):
        raise ValueError("multi-band manifest requires one explicit band record per result file")
    explicit_bands = []
    for record, result_file in zip(bands, result_files):
        item = dict(record)
        item["result_fits"] = result_file
        explicit_bands.append(item)
    return build_multi_band_workflow_manifest(
        config_file=config_file,
        summary_file=summary_file,
        bands=explicit_bands,
        comparison_png=comparison_png,
        working_note_file=working_note_file,
        constraint_files=constraint_files or [],
        parameter_files=(fit_result or {}).get("params_files", []) if fit_result else [],
        object_id=object_id,
        round_id=round_id,
    )


def workflow_propose_round(
    workflow_manifest: dict[str, Any],
    output_dir: str | None = None,
    current_components: list[str] | None = None,
    use_vlm: bool = False,
    previous_round_ref: str | None = None,
    round0_detection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the formal evidence package and raw rule proposal."""
    from component_analysis.decision_service import build_workflow_proposal
    from component_analysis.provider import OpenAICompatibleVLM

    callback = OpenAICompatibleVLM() if use_vlm else None
    return build_workflow_proposal(
        workflow_manifest,
        output_dir=output_dir,
        current_components=current_components,
        vlm_callback=callback,
        previous_round_ref=previous_round_ref,
        round0_detection=round0_detection,
    )


def workflow_resolve_round(
    proposal: dict[str, Any],
    state_file: str,
    output_dir: str | None = None,
    agent_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one formal proposal and persist its object-level PolicyState."""
    from component_analysis.decision_service import resolve_workflow_proposal

    state = load_policy_state(state_file, object_id=proposal.get("object_id"))
    result = resolve_workflow_proposal(
        proposal,
        state=state,
        output_dir=output_dir,
        agent_recommendation=agent_recommendation,
    )
    from component_analysis.policy import save_policy_state

    save_policy_state(state, state_file)
    result["state_ref"] = str(__import__("pathlib").Path(state_file).expanduser().resolve())
    return result


def workflow_action_preflight(
    decision_artifact: dict[str, Any],
    workflow_manifest: dict[str, Any],
    current_model_labels: list[str] | None = None,
    allow_remove: bool = False,
    remove_pilot_passed: bool = False,
) -> dict[str, Any]:
    """Validate the resolved action and return a non-executing MCP contract."""
    result = preflight_action(
        decision_artifact,
        workflow_manifest,
        current_model_labels=current_model_labels or [],
        allow_remove=allow_remove,
        remove_pilot_passed=remove_pilot_passed,
    )
    if result.get("ok"):
        result["mcp_call"] = prepare_mcp_fit_call(
            workflow_manifest,
            preflight=result,
        )
    return result


def workflow_analysis_artifact(
    workflow_manifest: dict[str, Any],
    decision_artifact: dict[str, Any],
    component_analysis_file: str | None = None,
    working_note_file: str | None = None,
    agent_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose the narrative-to-structured analysis adapter through MCP."""
    return build_workflow_analysis_artifact(
        workflow_manifest=workflow_manifest,
        decision_artifact=decision_artifact,
        component_analysis_file=component_analysis_file,
        working_note_file=working_note_file,
        agent_recommendation=agent_recommendation,
    )


def workflow_action_config(
    decision_artifact: dict[str, Any],
    workflow_manifest: dict[str, Any],
    target_config: str,
    current_model_labels: list[str] | None = None,
    agent_recommendation: dict[str, Any] | None = None,
    allow_remove: bool = False,
    remove_pilot_passed: bool = False,
) -> dict[str, Any]:
    """Write a new action config and return the existing MCP fit contract."""
    return apply_action_to_config(
        decision_artifact,
        workflow_manifest,
        target_config=target_config,
        current_model_labels=current_model_labels or [],
        agent_recommendation=agent_recommendation,
        allow_remove=allow_remove,
        remove_pilot_passed=remove_pilot_passed,
    )


def record_workflow_fit_lifecycle(
    workflow_manifest: dict[str, Any],
    raw_decision: dict[str, Any],
    resolved_decision: dict[str, Any],
    fit_result: dict[str, Any],
    state_file: str,
    lifecycle_file: str,
    decision_ref: str | None = None,
    verifier_status: str | None = None,
    action_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the object-level state after an existing MCP fit returns."""
    state = load_policy_state(state_file, object_id=workflow_manifest["object_id"])
    return record_fit_lifecycle(
        state=state,
        workflow_manifest=workflow_manifest,
        raw_decision=raw_decision,
        resolved_decision=resolved_decision,
        fit_result=fit_result,
        verifier_status=verifier_status,
        decision_ref=decision_ref,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
        action_summary=action_summary,
    )


def workflow_evaluate_refit(
    round_id: str,
    component: str,
    refit_evaluation: dict[str, Any],
    state_file: str,
    candidate_action_type: str | None = None,
    candidate_reason_code: str | None = None,
    evidence_refs: dict[str, Any] | None = None,
    decision_ref: str | None = None,
    object_id: str | None = None,
    config_ref: str | None = None,
) -> dict[str, Any]:
    """Evaluate a completed refit and persist the updated object state."""
    state = load_policy_state(state_file, object_id=object_id)
    return evaluate_workflow_refit(
        state=state,
        round_id=round_id,
        component=component,
        refit_evaluation=refit_evaluation,
        candidate_action_type=candidate_action_type,
        candidate_reason_code=candidate_reason_code,
        evidence_refs=evidence_refs,
        state_file=state_file,
        decision_ref=decision_ref,
        config_ref=config_ref,
    )


def workflow_complete_candidate(
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
    baseline_fit_result: dict[str, Any] | None,
    candidate_fit_result: dict[str, Any] | None,
    component: str,
    candidate_action_type: str,
    state_file: str,
    candidate_reason_code: str | None = None,
    lifecycle_file: str | None = None,
    evidence_file: str | None = None,
    evidence_refs: dict[str, Any] | None = None,
    decision_ref: str | None = None,
) -> dict[str, Any]:
    """Compare two existing MCP fit results and persist one candidate event."""
    if baseline_manifest.get("object_id") != candidate_manifest.get("object_id"):
        raise ValueError("baseline and candidate manifests must refer to the same object")
    state = load_policy_state(
        state_file,
        object_id=candidate_manifest.get("object_id"),
    )
    return complete_workflow_candidate(
        state=state,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit_result=baseline_fit_result,
        candidate_fit_result=candidate_fit_result,
        component=component,
        candidate_action_type=candidate_action_type,
        candidate_reason_code=candidate_reason_code,
        evidence_refs=evidence_refs,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
        evidence_file=evidence_file,
        decision_ref=decision_ref,
    )


def workflow_verify_best_round(
    workflow_manifest: dict[str, Any],
    lifecycle_file: str,
    component_analysis_file: str,
    output_file: str,
    verifier_assessment: dict[str, Any] | None = None,
    baseline_manifest: dict[str, Any] | None = None,
    baseline_fit_result: dict[str, Any] | None = None,
    baseline_artifact_file: str | None = None,
    manifest_ref: str | None = None,
) -> dict[str, Any]:
    """Read one converged lifecycle and create the structured verifier artifact.

    The assessment is schema-validated evidence. This tool never locks a round
    and never accepts a client-supplied verdict string.
    """
    return verify_best_round(
        workflow_manifest=workflow_manifest,
        lifecycle_file=lifecycle_file,
        component_analysis_file=component_analysis_file,
        output_file=output_file,
        verifier_assessment=verifier_assessment,
        baseline_manifest=baseline_manifest,
        baseline_fit_result=baseline_fit_result,
        baseline_artifact_file=baseline_artifact_file,
        manifest_ref=manifest_ref,
    )


def workflow_lock_best_round(
    verifier_artifact_ref: str,
    state_file: str,
    lifecycle_file: str,
) -> dict[str, Any]:
    """Lock a round only after validating a persisted verifier artifact reference."""
    return lock_best_round(
        verifier_artifact_ref=verifier_artifact_ref,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
    )
