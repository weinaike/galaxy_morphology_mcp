"""Client-independent proposal and policy-resolution services.

This module is the decision entry used by both the formal workflow bridge and
the non-invasive shadow runner. It never executes GALFIT or GalfitS.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping

from schemas import validate

from .artifact_adapter import (
    extract_numeric_evidence_from_workflow_manifest,
    workflow_fit_components,
)
from .candidate_overlay import create_candidate_overlay
from .policy import PolicyState, apply_policy, record_decision_state, save_policy_state
from .rules import decide_proposal
from .timing import persist_workflow_timing
from .vlm import (
    MAX_OBSERVATIONS_PER_REQUEST,
    PROMPT_VERSION,
    allowed_target_ids,
    build_vlm_prompt,
    make_unavailable_vlm_evidence,
    parse_vlm_response,
)
from .workflow_bridge import validate_agent_recommendation

VLMCallback = Callable[[str, str], str]


def _fingerprint(*artifacts: Mapping[str, Any]) -> str:
    def stable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: stable(item)
                for key, item in value.items()
                if key not in {"round_id", "manifest_ref", "previous_round"}
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    payload = json.dumps(
        [stable(item) for item in artifacts],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def _write_text(path: Path, value: str) -> str:
    path.write_text(value, encoding="utf-8")
    return str(path)


def _run_vlm(
    *,
    round_id: str,
    numeric: dict[str, Any],
    image: str | None,
    callback: VLMCallback | None,
    timing_ref_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Collect sparse VLM evidence with explicit target coverage accounting.

    A successful JSON response is not sufficient for a batch to be complete:
    every requested target must either produce an observation or be retried
    individually with a changed prompt variant. The aggregate is OK only when
    all targets are covered; otherwise it is PARTIAL or fail-closed.
    """
    all_targets = allowed_target_ids(numeric)
    batches = [
        all_targets[index : index + MAX_OBSERVATIONS_PER_REQUEST]
        for index in range(0, len(all_targets), MAX_OBSERVATIONS_PER_REQUEST)
    ]
    if not batches:
        batches = [("central",)]
    model_id = getattr(callback, "model_id", None)
    attempts: list[dict[str, Any]] = []
    timing_attempts: list[dict[str, Any]] = []
    raw_response: str | None = None
    errors: list[str] = []
    started = datetime.now(timezone.utc)
    overall_clock = time.perf_counter()

    def unavailable(status: str, error: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        evidence = make_unavailable_vlm_evidence(
            round_id=round_id, status=status, model_id=model_id
        )
        prompt = build_vlm_prompt(
            round_id=round_id,
            numeric_evidence=numeric,
            target_ids=("central",),
            retry_variant=0,
        )
        return evidence, {
            "status": "DISABLED" if status == "REFUSED" and callback is None else status,
            "model_id": model_id,
            "prompt_version": PROMPT_VERSION,
            "attempts": attempts,
            "raw_response": raw_response,
            "error": error,
            "coverage": {
                "requested": list(all_targets),
                "covered": [],
                "missing": list(all_targets),
                "complete": False,
            },
        }, prompt

    if callback is None:
        vlm, provider, prompt = unavailable(
            "REFUSED", "VLM callback is disabled; numeric-only fallback is available"
        )
        return vlm, provider, prompt
    if not image:
        vlm, provider, prompt = unavailable(
            "PARSE_FAILED", "comparison_png is unavailable"
        )
        return vlm, provider, prompt

    merged_observations: list[dict[str, Any]] = []
    covered_targets: set[str] = set()
    failed_targets: set[str] = set()
    last_status = "PARSE_FAILED"
    last_prompt = ""
    for batch_index, batch in enumerate(batches):
        requested = tuple(batch)
        for retry, retry_targets in enumerate((requested,)):
            prompt = build_vlm_prompt(
                round_id=round_id,
                numeric_evidence=numeric,
                target_ids=retry_targets,
                retry_variant=retry,
            )
            last_prompt = prompt
            attempt_number = len(attempts) + 1
            attempt_started = datetime.now(timezone.utc)
            attempt_clock = time.perf_counter()
            metadata: dict[str, Any] = {}
            parse_status = "PARSE_FAILED"
            error: str | None = None
            try:
                raw_response = callback(image, prompt)
                metadata = dict(getattr(callback, "last_response_metadata", {}) or {})
                vlm, error = parse_vlm_response(
                    raw_response,
                    round_id=round_id,
                    numeric_evidence=numeric,
                    model_id=model_id,
                    allowed_targets=set(retry_targets),
                )
                parse_status = vlm["parse_status"]
                if parse_status in {"OK", "PARTIAL"}:
                    merged_observations.extend(vlm["observations"])
                    covered_targets.update(
                        observation["target_id"] for observation in vlm["observations"]
                    )
            except TimeoutError:
                parse_status = "TIMEOUT"
                error = "VLM callback timed out"
                vlm = make_unavailable_vlm_evidence(
                    round_id=round_id, status="TIMEOUT", model_id=model_id
                )
            except PermissionError:
                parse_status = "REFUSED"
                error = "VLM callback was refused"
                vlm = make_unavailable_vlm_evidence(
                    round_id=round_id, status="REFUSED", model_id=model_id
                )
            except ValueError as exc:
                parse_status = "PARSE_FAILED"
                error = f"VLM callback returned invalid content: {exc}"
                vlm = make_unavailable_vlm_evidence(
                    round_id=round_id, status="PARSE_FAILED", model_id=model_id
                )
            except Exception as exc:
                parse_status = "REFUSED"
                error = f"VLM provider error: {type(exc).__name__}: {exc}"
                vlm = make_unavailable_vlm_evidence(
                    round_id=round_id, status="REFUSED", model_id=model_id
                )
            ended = datetime.now(timezone.utc)
            attempt_record = {
                "attempt": attempt_number,
                "model_id": metadata.get("model_id", model_id),
                "prompt_version": metadata.get("prompt_version", PROMPT_VERSION),
                "batch": batch_index,
                "target_ids": list(retry_targets),
                "retry_variant": retry,
                "parse_status": parse_status,
                "error": error,
                "raw_response": raw_response,
                "response_bytes": metadata.get("response_bytes", "unavailable"),
                "finish_reason": metadata.get("finish_reason", "unavailable"),
                "token_usage": metadata.get("token_usage", "unavailable"),
                "started_at": metadata.get("started_at", attempt_started.isoformat()),
                "ended_at": metadata.get("ended_at", ended.isoformat()),
                "duration_s": metadata.get(
                    "duration_s", round(time.perf_counter() - attempt_clock, 6)
                ),
            }
            attempts.append(attempt_record)
            timing_attempts.append({
                key: attempt_record[key]
                for key in (
                    "attempt", "model_id", "prompt_version", "batch", "target_ids", "retry_variant",
                    "parse_status", "response_bytes", "finish_reason",
                    "token_usage", "started_at", "ended_at", "duration_s",
                )
            })
            last_status = parse_status
            if error:
                errors.append(error)

        missing = [target for target in requested if target not in covered_targets]
        for retry_index, target in enumerate(missing, start=1):
            retry_variant = batch_index + retry_index
            retry_targets = (target,)
            prompt = build_vlm_prompt(
                round_id=round_id,
                numeric_evidence=numeric,
                target_ids=retry_targets,
                retry_variant=retry_variant,
            )
            last_prompt = prompt
            attempt_number = len(attempts) + 1
            attempt_started = datetime.now(timezone.utc)
            attempt_clock = time.perf_counter()
            metadata: dict[str, Any] = {}
            parse_status = "PARSE_FAILED"
            error = None
            response_for_attempt: str | None = None
            try:
                response_for_attempt = callback(image, prompt)
                raw_response = response_for_attempt
                metadata = dict(getattr(callback, "last_response_metadata", {}) or {})
                vlm, error = parse_vlm_response(
                    response_for_attempt,
                    round_id=round_id,
                    numeric_evidence=numeric,
                    model_id=model_id,
                    allowed_targets={target},
                )
                parse_status = vlm["parse_status"]
                if parse_status in {"OK", "PARTIAL"}:
                    merged_observations.extend(vlm["observations"])
                    covered_targets.update(
                        observation["target_id"] for observation in vlm["observations"]
                    )
            except TimeoutError:
                parse_status = "TIMEOUT"
                error = "VLM callback timed out"
            except PermissionError:
                parse_status = "REFUSED"
                error = "VLM callback was refused"
            except ValueError as exc:
                parse_status = "PARSE_FAILED"
                error = f"VLM callback returned invalid content: {exc}"
            except Exception as exc:
                parse_status = "REFUSED"
                error = f"VLM provider error: {type(exc).__name__}: {exc}"
            ended = datetime.now(timezone.utc)
            retry_record = {
                "attempt": attempt_number,
                "model_id": metadata.get("model_id", model_id),
                "prompt_version": metadata.get("prompt_version", PROMPT_VERSION),
                "batch": batch_index,
                "target_ids": [target],
                "retry_variant": retry_variant,
                "parse_status": parse_status,
                "error": error,
                "raw_response": response_for_attempt,
                "response_bytes": metadata.get("response_bytes", "unavailable"),
                "finish_reason": metadata.get("finish_reason", "unavailable"),
                "token_usage": metadata.get("token_usage", "unavailable"),
                "started_at": metadata.get("started_at", attempt_started.isoformat()),
                "ended_at": metadata.get("ended_at", ended.isoformat()),
                "duration_s": metadata.get(
                    "duration_s", round(time.perf_counter() - attempt_clock, 6)
                ),
            }
            attempts.append(retry_record)
            timing_attempts.append(
                {
                    key: retry_record[key]
                    for key in (
                        "attempt", "model_id", "prompt_version", "batch",
                        "target_ids", "retry_variant", "parse_status",
                        "response_bytes", "finish_reason", "token_usage",
                        "started_at", "ended_at", "duration_s",
                    )
                }
            )
            last_status = parse_status
            if error:
                errors.append(error)
        failed_targets.update(
            target for target in requested if target not in covered_targets
        )

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in merged_observations:
        unique[(observation["target_id"], observation["label"])] = observation
    if not failed_targets and covered_targets >= set(all_targets):
        aggregate_status = "OK"
        status = "OK"
    elif unique:
        aggregate_status = "PARTIAL"
        status = "PARTIAL"
    else:
        aggregate_status = (
            last_status
            if last_status in {"TIMEOUT", "REFUSED", "PARSE_FAILED"}
            else "PARSE_FAILED"
        )
        status = aggregate_status
    vlm = {
        "schema_version": "1.0",
        "round_id": round_id,
        "prompt_version": PROMPT_VERSION,
        "model_id": model_id,
        "parse_status": aggregate_status,
        "observations": list(unique.values()),
    }
    covered = sorted(covered_targets)
    missing = sorted(set(all_targets) - covered_targets)
    timing = {
        "schema_version": "workflow-vlm-timing@v1",
        "round_id": round_id,
        "model_id": model_id,
        "prompt_version": PROMPT_VERSION,
        "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(time.perf_counter() - overall_clock, 6),
        "attempts": timing_attempts,
        "fallback_status": vlm["parse_status"],
    }
    if timing_ref_path:
        timing["timing_log_ref"] = persist_workflow_timing(timing_ref_path, timing)
    return (
        vlm,
        {
            "status": status,
            "model_id": model_id,
            "prompt_version": PROMPT_VERSION,
            "attempts": attempts,
            "raw_response": raw_response,
            "error": "; ".join(errors) if errors else None,
            "timing": timing,
            "response_bytes": attempts[-1].get("response_bytes", "unavailable") if attempts else "unavailable",
            "finish_reason": attempts[-1].get("finish_reason", "unavailable") if attempts else "unavailable",
            "token_usage": attempts[-1].get("token_usage", "unavailable") if attempts else "unavailable",
            "coverage": {
                "requested": list(all_targets),
                "covered": covered,
                "missing": missing,
                "complete": not missing,
            },
        },
        last_prompt or build_vlm_prompt(
            round_id=round_id,
            numeric_evidence=numeric,
            target_ids=("central",),
            retry_variant=0,
        ),
    )


def build_workflow_proposal(
    workflow_manifest: Mapping[str, Any],
    *,
    output_dir: str | Path | None = None,
    vlm_callback: VLMCallback | None = None,
    current_components: Iterable[str] | None = None,
    isophote_cache: MutableMapping[str, Any] | None = None,
    previous_round_ref: str | None = None,
    round0_detection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and optionally persist numeric/VLM evidence and raw candidates."""
    manifest = copy.deepcopy(dict(workflow_manifest))
    validate(manifest, "workflow_round_manifest")
    output_path = Path(output_dir).expanduser().resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)
        manifest_ref = _write_json(output_path / "manifest.json", manifest)
    else:
        manifest_ref = manifest["config_file"]

    numeric = extract_numeric_evidence_from_workflow_manifest(
        manifest,
        manifest_ref=manifest_ref,
        isophote_cache=isophote_cache,
    )
    numeric_ref = (
        _write_json(output_path / "numeric_evidence.json", numeric)
        if output_path
        else f"memory:{manifest['round_id']}:numeric_evidence"
    )
    prompt = build_vlm_prompt(round_id=manifest["round_id"], numeric_evidence=numeric)
    image = manifest.get("comparison_png")
    if output_path and image:
        image = create_candidate_overlay(
            manifest, numeric, output_path / "candidate_overlay.png"
        )
    vlm, provider, prompt = _run_vlm(
        round_id=manifest["round_id"],
        numeric=numeric,
        image=image,
        callback=vlm_callback,
        timing_ref_path=manifest.get("config_file"),
    )
    timing = provider.pop("timing", None)
    if output_path:
        prompt_ref = _write_text(output_path / "vlm_prompt.txt", prompt)
        raw_ref = _write_text(output_path / "vlm_response.raw.json", provider.get("raw_response") or "")
        _write_json(output_path / "vlm_response.attempts.json", {"attempts": provider["attempts"]})
        vlm_ref = _write_json(output_path / "vlm_evidence.json", vlm)
    else:
        prompt_ref = f"memory:{manifest['round_id']}:vlm_prompt"
        raw_ref = None
        vlm_ref = f"memory:{manifest['round_id']}:vlm_evidence"
    provider = {
        key: value for key, value in provider.items() if key != "raw_response"
    }
    provider["raw_response_ref"] = raw_ref
    provider["prompt_ref"] = prompt_ref
    fit_components = workflow_fit_components(manifest)
    current_profile = [
        {
            "component_id": item.get("model_label") or item.get("name"),
            "model_label": item.get("model_label") or item.get("name"),
            "model_type": item.get("type"),
            "semantic_label": (
                item.get("component")
                or ("single_sersic" if len(fit_components) == 1 else "unclassified")
            ),
            "classification": (
                "confirmed" if item.get("component") else "unclassified"
            ),
        }
        for item in fit_components
    ]
    confirmed_components = sorted(
        {
            str(item["semantic_label"])
            for item in current_profile
            if item.get("classification") == "confirmed"
        }
    )
    components = (
        sorted(set(current_components))
        if current_components is not None
        else confirmed_components
    )
    if not components and len(current_profile) == 1 and current_profile[0].get("semantic_label") == "single_sersic":
        components = ["single_sersic"]
    fingerprint = _fingerprint(numeric, vlm)
    raw_decision = decide_proposal(
        round_id=manifest["round_id"],
        numeric_evidence=numeric,
        vlm_evidence=vlm,
        current_components=components,
        current_profile=current_profile,
        evidence_refs={
            "numeric_evidence": numeric_ref,
            "vlm_evidence": vlm_ref,
            "manifest": manifest_ref,
            "previous_round": previous_round_ref,
        },
    )
    validate(raw_decision, "decision_artifact")
    proposal = {
        "schema_version": "1.0",
        "workflow_mode": manifest["workflow_mode"],
        "object_id": manifest["object_id"],
        "round_id": manifest["round_id"],
        "manifest_ref": manifest_ref,
        "numeric_evidence": numeric,
        "vlm_evidence": vlm,
        "evidence_fingerprint": fingerprint,
        "rule_decision": raw_decision,
        "raw_decision": copy.deepcopy(raw_decision["raw_decision"]),
        "candidate_actions": copy.deepcopy(raw_decision.get("candidate_actions", [])),
        "rule_trace": copy.deepcopy(raw_decision["rule_trace"]),
        "termination_checks": copy.deepcopy(raw_decision["termination_checks"]),
        "current_components": components,
        "current_profile": current_profile,
        "predicted_components": sorted(
            {
                str(item.get("action", {}).get("component"))
                for item in raw_decision.get("candidate_actions", [])
                if item.get("action", {}).get("component")
            }
        ),
        "confirmed_components": confirmed_components,
        "evidence_targets": list(
            (raw_decision.get("action") or {}).get("evidence_targets") or []
        ),
        "round0_detection": copy.deepcopy(round0_detection),
        "provider": provider,
    }
    if timing is not None:
        proposal["timing"] = timing
    validate(proposal, "workflow_proposal")
    if output_path:
        _write_json(output_path / "workflow_proposal.json", proposal)
    return proposal


def _agent_selected_decision(
    decision: dict[str, Any], recommendation: Mapping[str, Any]
) -> dict[str, Any]:
    validated = validate_agent_recommendation(
        recommendation, decision.get("candidate_actions", [])
    )
    candidates = copy.deepcopy(decision.get("candidate_actions", []))
    selected = next(
        item for item in candidates
        if item.get("rule_id") == validated["recommended_rule_id"]
    )
    for item in candidates:
        item["status"] = (
            "SELECTED"
            if item.get("rule_id") == validated["recommended_rule_id"]
            else "DEFERRED"
        )
    result = copy.deepcopy(decision)
    result["candidate_actions"] = candidates
    result["action"] = copy.deepcopy(selected["action"])
    action_type = result["action"].get("action_type")
    result["workflow_status"] = (
        "CONVERGED"
        if action_type == "CONVERGED"
        else "STOPPED_NEEDS_REVIEW"
        if action_type == "INCONCLUSIVE"
        else "CONTINUE"
    )
    return result


def _next_transition(decision: Mapping[str, Any], *, fit_available: bool) -> str:
    action = decision.get("action") or {}
    action_type = action.get("action_type")
    status = decision.get("workflow_status")
    if action_type in {
        "PROPOSE_ADD", "PROPOSE_REPLACE", "PROPOSE_REMOVE",
        "REFIT_PARAMETERS", "PROMOTE_SINGLE_SERSIC_TO_DISK",
    }:
        return "RUN_FIT"
    if action_type == "CONVERGED" or status == "CONVERGED":
        return "VERIFY_BEST_ROUND"
    if status == "STOPPED_NEEDS_REVIEW":
        return "REPORT_AND_HANDOFF" if fit_available else "FAILED_NEEDS_REVIEW"
    if action_type == "COLLECT_EVIDENCE":
        return "COLLECT_EVIDENCE"
    if action_type == "KEEP_AND_CONTINUE":
        return "REPORT_AND_HANDOFF"
    return "REVIEW"


def resolve_workflow_proposal(
    proposal: Mapping[str, Any],
    *,
    state: PolicyState,
    output_dir: str | Path | None = None,
    agent_recommendation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one proposal with bounded Agent input and persist state."""
    value = copy.deepcopy(dict(proposal))
    validate(value, "workflow_proposal")
    rule_decision = copy.deepcopy(value["rule_decision"])
    validate(rule_decision, "decision_artifact")
    if state.object_id not in {None, value["object_id"]}:
        raise ValueError("policy state object_id does not match workflow proposal")
    state.object_id = value["object_id"]
    event_id = _fingerprint(
        {
            "proposal": value["evidence_fingerprint"],
            "previous_round": (rule_decision.get("evidence_refs") or {}).get("previous_round"),
            "agent_recommendation": agent_recommendation,
        }
    )
    duplicate = state.last_event_id == event_id and state.last_resolved_decision is not None
    if duplicate:
        decision = copy.deepcopy(state.last_resolved_decision)
        previous_round = (rule_decision.get("evidence_refs") or {}).get("previous_round")
        if previous_round:
            decision.setdefault("evidence_refs", {})["previous_round"] = previous_round
            validate(decision, "decision_artifact")
    elif agent_recommendation is not None:
        decision = _agent_selected_decision(rule_decision, agent_recommendation)
        decision = apply_policy(
            decision,
            state,
            evidence_fingerprint=value["evidence_fingerprint"],
        )
    else:
        from .policy import decide_proposal_with_policy

        decision = decide_proposal_with_policy(
            round_id=value["round_id"],
            numeric_evidence=value["numeric_evidence"],
            vlm_evidence=value["vlm_evidence"],
            current_components=value["current_components"],
            current_profile=value.get("current_profile"),
            state=state,
            evidence_fingerprint=value["evidence_fingerprint"],
            evidence_refs=rule_decision.get("evidence_refs"),
        )
    validate(decision, "decision_artifact")

    output_path = Path(output_dir).expanduser().resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)
    decision_ref = (
        _write_json(output_path / "decision_artifact.json", decision)
        if output_path
        else state.last_decision_ref or f"memory:{value['round_id']}:decision_artifact"
    )
    if not duplicate:
        record_decision_state(
            state,
            raw_decision=decision["raw_decision"],
            resolved_decision=decision,
            round_id=value["round_id"],
            decision_ref=decision_ref,
            config_ref=value["manifest_ref"],
            event_id=event_id,
        )
    state.last_evidence_fingerprint = value["evidence_fingerprint"]
    state_ref = None
    if output_path:
        state_ref = save_policy_state(state, output_path / "policy_state.json")
    fit_available = any(
        feature.get("name") == "fit_convergence_summary"
        and (feature.get("value") or {}).get("fit_succeeded") is True
        for feature in value["numeric_evidence"].get("features", [])
    )
    action = decision.get("action") or {}
    action_summary = {
        "raw_action_type": (decision.get("raw_decision", {}).get("action") or {}).get("action_type"),
        "resolved_action_type": action.get("action_type"),
        "candidate_action_types": [
            item.get("action", {}).get("action_type")
            for item in decision.get("candidate_actions", [])
        ],
        "executed_action_type": None,
        "refit_verdict": None,
        "fallback": (decision.get("automation") or {}).get("resolution"),
        "needs_review": bool((decision.get("automation") or {}).get("needs_review", False)),
    }
    result = {
        "proposal": value,
        "decision": decision,
        "policy_state": state.to_dict(),
        "decision_ref": decision_ref,
        "state_ref": state_ref,
        "event_id": event_id,
        "next_transition": _next_transition(decision, fit_available=fit_available),
        "should_write_config": action.get("action_type") in {
            "PROPOSE_ADD", "PROPOSE_REPLACE", "PROPOSE_REMOVE",
            "REFIT_PARAMETERS", "PROMOTE_SINGLE_SERSIC_TO_DISK",
        },
        "should_fit": action.get("action_type") in {
            "PROPOSE_ADD", "PROPOSE_REPLACE", "PROPOSE_REMOVE",
            "REFIT_PARAMETERS", "PROMOTE_SINGLE_SERSIC_TO_DISK",
        },
        "needs_review": bool((decision.get("automation") or {}).get("needs_review", False)),
        "action_summary": action_summary,
    }
    if output_path:
        _write_json(output_path / "resolution.json", result)
    return result
