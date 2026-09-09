"""Automation policy layer resolving INCONCLUSIVE decisions deterministically.

Implements the INCONCLUSIVE automation-resolution strategy from
docs/component-analysis/redesign.md section 4.  The policy wraps rule-layer
outputs without modifying the rule functions: every INCONCLUSIVE is mapped to
exactly one deterministic default action, the original outcome and the reason
are recorded in the decision artifact's ``automation`` block, and human
review becomes a post-hoc batch pass over ``needs_review`` flags instead of a
blocking step in the fitting loop.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from schemas import validate

from .rules import RuleThresholds, decide_proposal, evaluate_refit

POLICY_VERSION = "automation-policy@v1"

DEFAULT_TRIAL_BUDGET = 3

# INCONCLUSIVE outcomes a trial refit can settle: the candidate is downgraded
# to a weak proposal and EVALUATE_REFIT arbitrates after the extra fit.
_TRIAL_FIT_ACTIONS: dict[str, dict[str, Any]] = {
    "DISK_AMBIGUOUS_EVIDENCE_V1": {"action_type": "PROPOSE_ADD", "component": "disk"},
    "EDGE_ON_LOW_Q_V1": {
        "action_type": "PROPOSE_REPLACE",
        "replace_from": "disk",
        "replace_to": "edge_on_disk",
    },
    "CENTRAL_RESOLUTION_CONFLICT_V1": {
        "action_type": "PROPOSE_ADD",
        "component": "bulge",
        "resolved_state": "inconclusive",
    },
    "COMPANION_NUMERIC_VLM_V1": {"action_type": "PROPOSE_ADD", "component": "companion"},
}


@dataclass
class PolicyState:
    """Per-galaxy automation state carried across analysis rounds."""

    trial_budget: int = DEFAULT_TRIAL_BUDGET
    trials_used: int = 0
    rejected_components: set[str] = field(default_factory=set)
    inconclusive_seen: dict[str, str] = field(default_factory=dict)
    terminated_rules: set[str] = field(default_factory=set)
    object_id: str | None = None
    last_round_id: str | None = None
    last_decision_ref: str | None = None
    last_evidence_fingerprint: str | None = None
    current_config_ref: str | None = None
    current_result_refs: dict[str, str] = field(default_factory=dict)
    baseline_round_id: str | None = None
    pending_action: dict[str, Any] | None = None
    candidate_trials: list[dict[str, Any]] = field(default_factory=list)
    last_raw_decision: dict[str, Any] | None = None
    last_resolved_decision: dict[str, Any] | None = None
    needs_review: bool = False
    termination_reason: str | None = None
    verifier_status: str | None = None
    fit_completion_status: str | None = None
    best_round_status: str | None = None
    state_version: str = "workflow-state@v1"
    last_event_id: str | None = None
    event_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot suitable for persistence and replay."""
        return {
            "object_id": self.object_id,
            "trial_budget": self.trial_budget,
            "trials_used": self.trials_used,
            "rejected_components": sorted(self.rejected_components),
            "inconclusive_seen": dict(sorted(self.inconclusive_seen.items())),
            "terminated_rules": sorted(self.terminated_rules),
            "last_round_id": self.last_round_id,
            "last_decision_ref": self.last_decision_ref,
            "last_evidence_fingerprint": self.last_evidence_fingerprint,
            "current_config_ref": self.current_config_ref,
            "current_result_refs": dict(sorted(self.current_result_refs.items())),
            "baseline_round_id": self.baseline_round_id,
            "pending_action": copy.deepcopy(self.pending_action),
            "candidate_trials": copy.deepcopy(self.candidate_trials),
            "last_raw_decision": copy.deepcopy(self.last_raw_decision),
            "last_resolved_decision": copy.deepcopy(self.last_resolved_decision),
            "needs_review": self.needs_review,
            "termination_reason": self.termination_reason,
            "verifier_status": self.verifier_status,
            "fit_completion_status": self.fit_completion_status,
            "best_round_status": self.best_round_status,
            "state_version": self.state_version,
            "last_event_id": self.last_event_id,
            "event_history": copy.deepcopy(self.event_history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyState":
        """Restore a state snapshot while tolerating older v1 snapshots."""
        return cls(
            trial_budget=int(data.get("trial_budget", DEFAULT_TRIAL_BUDGET)),
            trials_used=int(data.get("trials_used", 0)),
            rejected_components=set(data.get("rejected_components", [])),
            inconclusive_seen=dict(data.get("inconclusive_seen", {})),
            terminated_rules=set(data.get("terminated_rules", [])),
            object_id=data.get("object_id"),
            last_round_id=data.get("last_round_id"),
            last_decision_ref=data.get("last_decision_ref"),
            last_evidence_fingerprint=data.get("last_evidence_fingerprint"),
            current_config_ref=data.get("current_config_ref"),
            current_result_refs=dict(data.get("current_result_refs", {})),
            baseline_round_id=data.get("baseline_round_id"),
            pending_action=copy.deepcopy(data.get("pending_action")),
            candidate_trials=copy.deepcopy(data.get("candidate_trials", [])),
            last_raw_decision=copy.deepcopy(data.get("last_raw_decision")),
            last_resolved_decision=copy.deepcopy(data.get("last_resolved_decision")),
            needs_review=bool(data.get("needs_review", False)),
            termination_reason=data.get("termination_reason"),
            verifier_status=data.get("verifier_status"),
            fit_completion_status=data.get("fit_completion_status"),
            best_round_status=data.get("best_round_status"),
            state_version=data.get("state_version", "workflow-state@v1"),
            last_event_id=data.get("last_event_id"),
            event_history=copy.deepcopy(data.get("event_history", [])),
        )


def save_policy_state(state: PolicyState, path: str | Path) -> str:
    """Persist one object's state without changing fitting input files."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return str(target)


def load_policy_state(path: str | Path, *, object_id: str | None = None) -> PolicyState:
    """Load a persisted state or return an initialized object state."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        return PolicyState(object_id=object_id)
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"policy state must be a JSON object: {target}")
    state = PolicyState.from_dict(data)
    if object_id is not None and state.object_id not in {None, object_id}:
        raise ValueError(
            f"policy state object_id mismatch: expected {object_id!r}, got {state.object_id!r}"
        )
    if state.object_id is None:
        state.object_id = object_id
    return state


def record_decision_state(
    state: PolicyState,
    *,
    raw_decision: dict[str, Any],
    resolved_decision: dict[str, Any],
    round_id: str,
    decision_ref: str | None = None,
    config_ref: str | None = None,
    event_id: str | None = None,
) -> None:
    """Apply a decision to the replayable object state."""
    state.last_round_id = round_id
    state.last_decision_ref = decision_ref
    if state.baseline_round_id is None:
        state.baseline_round_id = round_id
    state.current_config_ref = config_ref or state.current_config_ref
    state.last_raw_decision = copy.deepcopy(raw_decision)
    state.last_resolved_decision = copy.deepcopy(resolved_decision)
    if event_id is not None:
        state.last_event_id = event_id
    trial = {
        "round_id": round_id,
        "decision_ref": decision_ref,
        "action": copy.deepcopy(resolved_decision.get("action")),
        "workflow_status": resolved_decision.get("workflow_status"),
    }
    if not any(
        item.get("round_id") == round_id
        and item.get("decision_ref") == decision_ref
        for item in state.candidate_trials
    ):
        state.candidate_trials.append(trial)
    if event_id is not None and not any(
        item.get("event_id") == event_id for item in state.event_history
    ):
        state.event_history.append(
            {
                "event_id": event_id,
                "round_id": round_id,
                "decision_ref": decision_ref,
                "action": copy.deepcopy(resolved_decision.get("action")),
            }
        )
    action = resolved_decision.get("action")
    state.pending_action = copy.deepcopy(action) if isinstance(action, dict) else None
    automation = resolved_decision.get("automation") or {}
    state.needs_review = bool(automation.get("needs_review", False))
    if resolved_decision.get("workflow_status") == "STOPPED_NEEDS_REVIEW":
        state.termination_reason = automation.get("reason") or "workflow decision loop stopped"
    elif resolved_decision.get("workflow_status") == "CONVERGED":
        state.termination_reason = "decision rules reached convergence candidate"
    else:
        state.termination_reason = None


def _with_automation(
    decision: dict[str, Any],
    *,
    action: dict[str, Any] | None,
    resolution: str,
    original_action_type: str,
    resolved_rule_id: str | None,
    reason: str,
    workflow_status: str | None = None,
) -> dict[str, Any]:
    resolved = copy.deepcopy(decision)
    if action and action.get("action_type") == "PROPOSE_REPLACE":
        action = dict(action)
        action.setdefault("target_model_label", action.get("replace_from"))
    resolved["action"] = action
    resolved["workflow_status"] = workflow_status or (
        "CONVERGED"
        if action and action.get("action_type") == "CONVERGED"
        else "STOPPED_NEEDS_REVIEW"
        if action is None or action.get("action_type") == "INCONCLUSIVE"
        else "CONTINUE"
    )
    resolved["automation"] = {
        "policy_version": POLICY_VERSION,
        "resolution": resolution,
        "original_action_type": original_action_type,
        "resolved_rule_id": resolved_rule_id,
        "reason": reason,
        "needs_review": True,
    }
    validate(resolved, "decision_artifact")
    return resolved


def _conservative(decision: dict[str, Any], rule_id: str, reason: str) -> dict[str, Any]:
    return _with_automation(
        decision,
        action=None,
        resolution="rule_terminated",
        original_action_type="INCONCLUSIVE",
        resolved_rule_id=rule_id,
        reason=reason,
        workflow_status="STOPPED_NEEDS_REVIEW",
    )


def apply_policy(
    decision: dict[str, Any],
    state: PolicyState,
    *,
    evidence_fingerprint: str = "",
    component: str | None = None,
) -> dict[str, Any]:
    """Resolve an INCONCLUSIVE without hiding the rule-layer decision."""

    action = decision.get("action")
    action_type = action.get("action_type") if isinstance(action, dict) else None

    if decision["state"] == "EVALUATE_REFIT":
        if action_type == "REJECT_REFIT":
            target = component or action.get("component")
            if target:
                state.rejected_components.add(target)
            return decision
        if action_type != "INCONCLUSIVE":
            return decision
        target = component or (action or {}).get("component")
        if not target:
            target = (
                decision.get("refit_evaluation", {}).get("component")
                or "unknown"
            )
        if target != "unknown":
            state.rejected_components.add(target)
        return _with_automation(
            decision,
            action={"action_type": "REJECT_REFIT", "component": target},
            resolution="reject_fallback",
            original_action_type="INCONCLUSIVE",
            resolved_rule_id=decision["rule_trace"][-1]["rule_id"],
            reason="EVALUATE_REFIT input was inconclusive; candidate was rejected and marked for review.",
            workflow_status="STOPPED_NEEDS_REVIEW",
        )

    if action_type != "INCONCLUSIVE":
        return decision

    inconclusive_trace = next(
        (
            item
            for item in reversed(decision.get("rule_trace", []))
            if item.get("outcome") == "INCONCLUSIVE"
        ),
        decision.get("rule_trace", [{}])[-1],
    )
    rule_id = inconclusive_trace["rule_id"]
    repeated = rule_id in state.inconclusive_seen and (
        state.inconclusive_seen[rule_id] == evidence_fingerprint
    )
    if rule_id in state.terminated_rules or repeated:
        state.terminated_rules.add(rule_id)
        return _conservative(
            decision,
            rule_id,
            "Repeated INCONCLUSIVE with unchanged evidence; rule terminated for this object."
            if repeated
            else "Rule already terminated for this object.",
        )
    state.inconclusive_seen[rule_id] = evidence_fingerprint

    template = _TRIAL_FIT_ACTIONS.get(rule_id)
    if template is not None:
        proposed = template.get("component") or template.get("replace_to")
        if state.trials_used >= state.trial_budget:
            return _conservative(
                decision, rule_id, f"Trial budget exhausted ({state.trial_budget} trial fits)."
            )
        if proposed in state.rejected_components:
            return _conservative(
                decision,
                rule_id,
                f"Candidate '{proposed}' was already rejected by EVALUATE_REFIT; not re-proposed.",
            )
        action = dict(template)
        if rule_id == "COMPANION_NUMERIC_VLM_V1":
            inputs = inconclusive_trace.get("inputs") or []
            if len(inputs) >= 2:
                action["target_model_label"] = inputs[1]
        state.trials_used += 1
        return _with_automation(
            decision,
            action=action,
            resolution="trial_fit",
            original_action_type="INCONCLUSIVE",
            resolved_rule_id=rule_id,
            reason="Downgraded to a weak candidate; EVALUATE_REFIT arbitrates after the trial fit.",
            workflow_status="CONTINUE",
        )

    return _conservative(
        decision,
        rule_id,
        "Non-experimentable INCONCLUSIVE; no safe executable action was available.",
    )


def decide_proposal_with_policy(
    *,
    round_id: str,
    numeric_evidence: dict[str, Any],
    vlm_evidence: dict[str, Any],
    current_components: Iterable[str],
    current_profile: Iterable[Mapping[str, Any]] | None = None,
    state: PolicyState,
    evidence_fingerprint: str = "",
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """decide_proposal plus automation policy, with numeric-only VLM fallback.

    When the VLM evidence is unavailable (parse failure, timeout, refusal),
    the rules are rerun with neutral VLM evidence: only stricter numeric
    combinations can then trigger actions, matching the degraded path already
    encoded in the rule layer.
    """

    current = list(current_components)
    decision = decide_proposal(
        round_id=round_id,
        numeric_evidence=numeric_evidence,
        vlm_evidence=vlm_evidence,
        current_components=current,
        current_profile=current_profile,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
    )
    if vlm_evidence.get("parse_status") != "OK":
        neutral = {
            "schema_version": "1.0",
            "round_id": round_id,
            "parse_status": "OK",
            "observations": [],
        }
        retry = decide_proposal(
            round_id=round_id,
            numeric_evidence=numeric_evidence,
            vlm_evidence=neutral,
            current_components=current,
            current_profile=current_profile,
            evidence_refs=evidence_refs,
            thresholds=thresholds,
        )
        retry["raw_decision"] = copy.deepcopy(decision["raw_decision"])
        decision = _with_automation(
            retry,
            action=retry["action"],
            resolution="numeric_only_retry",
            original_action_type=(decision.get("action") or {}).get("action_type") or "INCONCLUSIVE",
            resolved_rule_id="VLM_UNAVAILABLE_V1",
            reason=(
                f"VLM evidence unavailable ({vlm_evidence.get('parse_status')}); "
                "rules rerun on numeric evidence with neutral VLM input."
            ),
        )
        decision["evidence_views"] = {
            "vlm": {
                "parse_status": vlm_evidence.get("parse_status"),
                "observations": copy.deepcopy(vlm_evidence.get("observations", [])),
                "rule_trace": copy.deepcopy(decision.get("raw_decision", {}).get("rule_trace", [])),
                "action": copy.deepcopy(decision.get("raw_decision", {}).get("action")),
                "candidate_actions": copy.deepcopy(decision.get("candidate_actions", [])),
            },
            "numeric_only": {
                "parse_status": "OK",
                "rule_trace": copy.deepcopy(retry.get("rule_trace", [])),
                "action": copy.deepcopy(retry.get("action")),
                "candidate_actions": copy.deepcopy(retry.get("candidate_actions", [])),
            },
        }
    else:
        decision["evidence_views"] = {
            "vlm": {
                "parse_status": "OK",
                "observations": copy.deepcopy(vlm_evidence.get("observations", [])),
                "rule_trace": copy.deepcopy(decision.get("raw_decision", {}).get("rule_trace", [])),
                "action": copy.deepcopy(decision.get("raw_decision", {}).get("action")),
                "candidate_actions": copy.deepcopy(decision.get("candidate_actions", [])),
            },
            "numeric_only": {
                "parse_status": "NOT_RUN",
                "rule_trace": "unavailable",
                "action": "unavailable",
                "candidate_actions": [],
            },
        }
    validate(decision, "decision_artifact")
    return apply_policy(decision, state, evidence_fingerprint=evidence_fingerprint)


def evaluate_refit_with_policy(
    *,
    round_id: str,
    component: str,
    refit_evaluation: dict[str, Any],
    state: PolicyState,
    candidate_action_type: str | None = None,
    candidate_reason_code: str | None = None,
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """evaluate_refit plus automation policy: INCONCLUSIVE falls back to reject."""

    decision = evaluate_refit(
        round_id=round_id,
        component=component,
        refit_evaluation=refit_evaluation,
        candidate_action_type=candidate_action_type,
        candidate_reason_code=candidate_reason_code,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
    )
    return apply_policy(decision, state, component=component)
