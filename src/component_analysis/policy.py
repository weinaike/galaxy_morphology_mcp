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
from dataclasses import dataclass, field
from typing import Any, Iterable

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


def _with_automation(
    decision: dict[str, Any],
    *,
    action: dict[str, Any],
    resolution: str,
    original_action_type: str,
    resolved_rule_id: str | None,
    reason: str,
) -> dict[str, Any]:
    resolved = copy.deepcopy(decision)
    resolved["action"] = action
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
        action={"action_type": "KEEP_AND_CONTINUE"},
        resolution="conservative_keep",
        original_action_type="INCONCLUSIVE",
        resolved_rule_id=rule_id,
        reason=reason,
    )


def apply_policy(
    decision: dict[str, Any],
    state: PolicyState,
    *,
    evidence_fingerprint: str = "",
    component: str | None = None,
) -> dict[str, Any]:
    """Resolve an INCONCLUSIVE decision into a deterministic default action.

    Non-INCONCLUSIVE decisions pass through unchanged; REJECT_REFIT outcomes
    are still recorded so rejected candidates are not re-proposed later.
    """

    action_type = decision["action"]["action_type"]

    if decision["state"] == "EVALUATE_REFIT":
        if action_type == "REJECT_REFIT":
            state.rejected_components.add(decision["action"]["component"])
            return decision
        if action_type != "INCONCLUSIVE":
            return decision
        target = component or decision["action"].get("component")
        if not target:
            raise ValueError("component is required to resolve an EVALUATE_REFIT INCONCLUSIVE")
        state.rejected_components.add(target)
        return _with_automation(
            decision,
            action={"action_type": "REJECT_REFIT", "component": target},
            resolution="reject_fallback",
            original_action_type="INCONCLUSIVE",
            resolved_rule_id=decision["rule_trace"][-1]["rule_id"],
            reason="EVALUATE_REFIT gates inconclusive; conservative fallback rejects the candidate and reverts.",
        )

    if action_type != "INCONCLUSIVE":
        return decision

    rule_id = decision["rule_trace"][-1]["rule_id"]
    repeated = rule_id in state.inconclusive_seen and (
        state.inconclusive_seen[rule_id] == evidence_fingerprint
    )
    if rule_id in state.terminated_rules or repeated:
        state.terminated_rules.add(rule_id)
        return _conservative(
            decision,
            rule_id,
            "Repeated INCONCLUSIVE with unchanged evidence; question terminated for this galaxy."
            if repeated
            else "Rule already terminated for this galaxy.",
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
            inputs = decision["rule_trace"][-1].get("inputs") or []
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
        )

    return _conservative(
        decision, rule_id, "Non-experimentable INCONCLUSIVE; model structure kept unchanged."
    )


def decide_proposal_with_policy(
    *,
    round_id: str,
    numeric_evidence: dict[str, Any],
    vlm_evidence: dict[str, Any],
    current_components: Iterable[str],
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
        evidence_refs=evidence_refs,
        thresholds=thresholds,
    )
    if (
        decision["action"]["action_type"] == "INCONCLUSIVE"
        and decision["rule_trace"][0]["rule_id"] == "VLM_UNAVAILABLE_V1"
    ):
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
            evidence_refs=evidence_refs,
            thresholds=thresholds,
        )
        decision = _with_automation(
            retry,
            action=retry["action"],
            resolution="numeric_only_retry",
            original_action_type="INCONCLUSIVE",
            resolved_rule_id="VLM_UNAVAILABLE_V1",
            reason=(
                f"VLM evidence unavailable ({vlm_evidence.get('parse_status')}); "
                "rules rerun on numeric evidence with neutral VLM input."
            ),
        )
    return apply_policy(decision, state, evidence_fingerprint=evidence_fingerprint)


def evaluate_refit_with_policy(
    *,
    round_id: str,
    component: str,
    refit_evaluation: dict[str, Any],
    state: PolicyState,
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """evaluate_refit plus automation policy: INCONCLUSIVE falls back to reject."""

    decision = evaluate_refit(
        round_id=round_id,
        component=component,
        refit_evaluation=refit_evaluation,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
    )
    return apply_policy(decision, state, component=component)
