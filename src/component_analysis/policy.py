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
import hashlib
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

# Lifecycle statuses of one queued refit candidate (N23 state contract; the
# queue itself is populated and consumed by the N25 candidate queue).
CANDIDATE_QUEUE_STATUSES = (
    "PENDING",
    "SELECTED",
    "ACCEPTED",
    "REJECTED",
    "INCONCLUSIVE",
    "INVALIDATED",
)

CANDIDATE_REF_SCHEMA_VERSION = "candidate-ref@v1"

# INCONCLUSIVE outcomes a trial refit can settle: the candidate is downgraded
# to a weak proposal and EVALUATE_REFIT arbitrates after the extra fit.
# N24 (2026-10-27): VLM-dependent inconclusives (COMPANION_NUMERIC_VLM_V1,
# EDGE_ON_LOW_Q_V1) were removed — without VLM confirmation they must stay
# INCONCLUSIVE or go to a declared collector, never to a trial fit.
_TRIAL_FIT_ACTIONS: dict[str, dict[str, Any]] = {
    "DISK_AMBIGUOUS_EVIDENCE_V1": {"action_type": "PROPOSE_ADD", "component": "disk"},
    "CENTRAL_RESOLUTION_CONFLICT_V1": {
        "action_type": "PROPOSE_ADD",
        "component": "bulge",
        "resolved_state": "inconclusive",
    },
}


@dataclass
class PolicyState:
    """Per-galaxy automation state carried across analysis rounds."""

    trial_budget: int = DEFAULT_TRIAL_BUDGET
    trials_used: int = 0
    rejected_components: set[str] = field(default_factory=set)
    rejected_candidate_keys: set[str] = field(default_factory=set)
    inconclusive_seen: dict[str, str] = field(default_factory=dict)
    evidence_collector_keys: set[str] = field(default_factory=set)
    terminated_rules: set[str] = field(default_factory=set)
    pending_candidate_queue: list[dict[str, Any]] = field(default_factory=list)
    # N26: candidate_key -> persisted refit verdict decision; duplicate
    # submissions of the same candidate_ref return the stored result without
    # re-consuming the queue, budget or lifecycle.
    refit_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # N27: per rule|target occurrence counts of blocking INCONCLUSIVE. VLM
    # responses vary between rounds, so "unchanged evidence" alone cannot stop
    # a collector loop; an absolute per-key cap terminates it instead.
    inconclusive_counts: dict[str, int] = field(default_factory=dict)
    # 2026-10-28 ruling 3: evidence-independent rejection memory. Same action
    # on the same baseline is never re-proposed, however the evidence drifts.
    rejected_stable_keys: set[str] = field(default_factory=set)
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
    state_version: str = "workflow-state@v2"
    last_event_id: str | None = None
    event_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot suitable for persistence and replay."""
        return {
            "object_id": self.object_id,
            "trial_budget": self.trial_budget,
            "trials_used": self.trials_used,
            "rejected_components": sorted(self.rejected_components),
            "rejected_candidate_keys": sorted(self.rejected_candidate_keys),
            "inconclusive_seen": dict(sorted(self.inconclusive_seen.items())),
            "evidence_collector_keys": sorted(self.evidence_collector_keys),
            "terminated_rules": sorted(self.terminated_rules),
            "pending_candidate_queue": copy.deepcopy(self.pending_candidate_queue),
            "refit_decisions": copy.deepcopy(self.refit_decisions),
            "inconclusive_counts": dict(sorted(self.inconclusive_counts.items())),
            "rejected_stable_keys": sorted(self.rejected_stable_keys),
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
            rejected_candidate_keys=set(data.get("rejected_candidate_keys", [])),
            inconclusive_seen=dict(data.get("inconclusive_seen", {})),
            evidence_collector_keys=set(data.get("evidence_collector_keys", [])),
            terminated_rules=set(data.get("terminated_rules", [])),
            pending_candidate_queue=copy.deepcopy(data.get("pending_candidate_queue", [])),
            refit_decisions=copy.deepcopy(data.get("refit_decisions", {})),
            inconclusive_counts=dict(data.get("inconclusive_counts", {})),
            rejected_stable_keys=set(data.get("rejected_stable_keys", [])),
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
    proposal_decision: bool = True,
) -> None:
    """Apply a decision to the replayable object state.

    ``proposal_decision=False`` (refit evaluations) keeps the PROPOSE
    resolution cache (last_raw/last_resolved_decision) intact — the resolve
    path's duplicate submission check replays from that cache and must never
    receive an EVALUATE_REFIT verdict decision.
    """
    state.last_round_id = round_id
    state.last_decision_ref = decision_ref
    if state.baseline_round_id is None:
        state.baseline_round_id = round_id
    state.current_config_ref = config_ref or state.current_config_ref
    if proposal_decision:
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


def _selected_rule_id(decision: Mapping[str, Any]) -> str:
    for candidate in decision.get("candidate_actions") or []:
        if candidate.get("status") == "SELECTED":
            return str(candidate.get("rule_id") or "unknown_rule")
    automation = decision.get("automation") or {}
    if automation.get("resolved_rule_id"):
        return str(automation["resolved_rule_id"])
    for trace in reversed(decision.get("rule_trace") or []):
        if trace.get("rule_id"):
            return str(trace["rule_id"])
    return "unknown_rule"


def apply_stable_rejection_veto(
    decision: dict[str, Any],
    state: PolicyState,
    action: Mapping[str, Any],
) -> dict[str, Any] | None:
    """2026-10-28 ruling 3: veto a model action whose stable key was already
    rejected on this baseline, regardless of evidence drift. After the veto
    the termination gates are re-evaluated: all PASS/NOT_APPLICABLE means the
    workflow may still converge, otherwise it stops for review."""
    baseline_fp = baseline_fingerprint_from_state(state)
    if not baseline_fp:
        return None
    rule_id = _selected_rule_id(decision)
    stable_ref = {
        "action": action,
        "rule_id": rule_id,
        "target": action.get("target_model_label"),
        "baseline_artifact_fingerprint": baseline_fp,
    }
    if compute_stable_candidate_key(stable_ref) not in state.rejected_stable_keys:
        return None
    identity = (
        rule_id,
        _canonical_action(action),
        str(action.get("target_model_label") or ""),
    )
    for q_item in state.pending_candidate_queue:
        if q_item.get("status") in {"PENDING", "SELECTED"} and _queue_item_identity(q_item) == identity:
            q_item["status"] = "REJECTED"
            q_item["rejection_source"] = "rejected_stable_keys"
    checks = decision.get("termination_checks") or []
    if checks and all(item.get("status") in {"PASS", "NOT_APPLICABLE"} for item in checks):
        vetoed = {"action_type": "CONVERGED", "reason_code": "ALL_TERMINATION_GATES_PASS"}
        status = "CONVERGED"
    else:
        vetoed = None
        status = "STOPPED_NEEDS_REVIEW"
    return _with_automation(
        decision,
        action=vetoed,
        resolution="candidate_rejected_stable",
        original_action_type=str(action.get("action_type") or "PROPOSE_ADD"),
        resolved_rule_id=rule_id,
        reason=(
            "This action was already rejected on the current baseline "
            "(evidence-independent stable key); genuine evidence improvements "
            "belong to human review, not automatic retries."
        ),
        workflow_status=status,
        needs_review=True,
    )


def _with_automation(
    decision: dict[str, Any],
    *,
    action: dict[str, Any] | None,
    resolution: str,
    original_action_type: str,
    resolved_rule_id: str | None,
    reason: str,
    workflow_status: str | None = None,
    needs_review: bool = True,
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
        "needs_review": needs_review,
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


def _candidate_key(
    action: Mapping[str, Any],
    *,
    evidence_fingerprint: str,
    baseline_config_checksum: str | None = None,
) -> str:
    payload = {
        "action_type": action.get("action_type"),
        "component": action.get("component"),
        "target_model_label": action.get("target_model_label"),
        "replace_from": action.get("replace_from"),
        "replace_to": action.get("replace_to"),
        "parameter_changes": action.get("parameter_changes", []),
        "baseline_config_checksum": baseline_config_checksum,
        "evidence_fingerprint": evidence_fingerprint,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_candidate_ref(
    *,
    action: Mapping[str, Any],
    rule_id: str,
    baseline_artifact_fingerprint: str,
    evidence_fingerprint: str,
    candidate_id: str | None = None,
    target: str | None = None,
    decision_ref: str | None = None,
) -> dict[str, Any]:
    """Build and validate the server-side identity of one executable candidate.

    The candidate key is derived from the ORIGINAL candidate action, never from
    a refit verdict action; verdict action types are rejected by the schema.
    """
    candidate_ref = {
        "schema_version": CANDIDATE_REF_SCHEMA_VERSION,
        "candidate_id": candidate_id or "",
        "rule_id": rule_id,
        "target": target,
        "action": copy.deepcopy(dict(action)),
        "decision_ref": decision_ref,
        "baseline_artifact_fingerprint": baseline_artifact_fingerprint,
        "evidence_fingerprint": evidence_fingerprint,
    }
    if not candidate_ref["candidate_id"]:
        candidate_ref["candidate_id"] = f"cand-{compute_candidate_key(candidate_ref)[:12]}"
    validate(candidate_ref, "candidate_ref")
    return candidate_ref


def compute_candidate_key(candidate_ref: Mapping[str, Any]) -> str:
    """Compute the canonical candidate key from a schema-valid candidate_ref.

    sha256(canonical_action + rule/target + baseline artifact fingerprint +
    evidence fingerprint), per execution-plan section 5.4. Callers must not
    trust a client-submitted key over this computation.
    """
    action = candidate_ref.get("action") or {}
    payload = {
        "action_type": action.get("action_type"),
        "component": action.get("component"),
        "target_model_label": action.get("target_model_label"),
        "replace_from": action.get("replace_from"),
        "replace_to": action.get("replace_to"),
        "parameter_changes": action.get("parameter_changes", []),
        "rule_id": candidate_ref.get("rule_id"),
        "target": candidate_ref.get("target"),
        "baseline_artifact_fingerprint": candidate_ref.get("baseline_artifact_fingerprint"),
        "evidence_fingerprint": candidate_ref.get("evidence_fingerprint"),
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_baseline_artifact_fingerprint(
    config_file: Any, result_files: Any, summary_file: Any
) -> str:
    """Canonical baseline identity: sha256 of {config, result files, summary}.

    Used by BOTH the runner (candidate_ref construction) and the queue build
    (decision_service) so stable rejection keys computed on either side match.
    """
    payload = {
        "config_file": str(config_file) if config_file else None,
        # Sorted: the runner passes manifest band order while state refs keep
        # MCP return order; identity must not depend on list order (104
        # finding: the stable-rejection key silently missed on re-ordering).
        "result_files": sorted(str(value) for value in (result_files or [])),
        "summary_file": str(summary_file) if summary_file else None,
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def baseline_fingerprint_from_state(state: "PolicyState") -> str | None:
    """Recover the canonical baseline fingerprint from persisted state refs.

    Returns None when the state carries no baseline artifacts yet (first
    round); callers then fall back to the evidence-scoped legacy dedup.
    """
    refs = state.current_result_refs or {}
    result_files = [
        value
        for key, value in sorted(refs.items())
        if (
            key.startswith("result_files")
            or key.startswith("result_fits")
            or key == "optimized_fits_file"
        )
    ]
    summary = refs.get("summary_file") or refs.get("summary_files.0")
    if not state.current_config_ref or (not result_files and not summary):
        return None
    return compute_baseline_artifact_fingerprint(
        state.current_config_ref, result_files, summary
    )


def compute_stable_candidate_key(candidate_ref: Mapping[str, Any]) -> str:
    """Evidence-independent candidate key for rejection memory (2026-10-28
    ruling 3): sha256(canonical_action + rule/target + baseline artifact
    fingerprint). VLM responses vary between rounds, so evidence fingerprints
    never participate in rejection deduplication — an action rejected on a
    baseline is not re-proposed on that baseline regardless of evidence drift;
    genuinely improved evidence goes to human review instead of a retry."""
    action = candidate_ref.get("action") or {}
    payload = {
        "action_type": action.get("action_type"),
        "component": action.get("component"),
        "target_model_label": action.get("target_model_label"),
        "replace_from": action.get("replace_from"),
        "replace_to": action.get("replace_to"),
        "parameter_changes": action.get("parameter_changes", []),
        "rule_id": candidate_ref.get("rule_id"),
        "target": candidate_ref.get("target"),
        "baseline_artifact_fingerprint": candidate_ref.get("baseline_artifact_fingerprint"),
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# The five executable model actions (section 5.2 of the execution plan).
MODEL_ACTION_TYPES = (
    "PROPOSE_ADD",
    "PROPOSE_REPLACE",
    "PROPOSE_REMOVE",
    "PROMOTE_SINGLE_SERSIC_TO_DISK",
    "REFIT_PARAMETERS",
)


def _canonical_action(action: Mapping[str, Any]) -> str:
    return json.dumps(action, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _queue_candidate_id(rule_id: str, action: Mapping[str, Any]) -> str:
    payload = f"{rule_id}|{_canonical_action(action)}".encode("utf-8")
    return f"cq-{hashlib.sha256(payload).hexdigest()[:12]}"


def _queue_item_identity(item: Mapping[str, Any]) -> tuple[str, str, str]:
    action = item.get("action") or {}
    return (
        str(item.get("rule_id") or ""),
        _canonical_action(action),
        str(action.get("target_model_label") or ""),
    )


def _candidate_ref_identity(candidate_ref: Mapping[str, Any]) -> tuple[str, str, str]:
    action = candidate_ref.get("action") or {}
    return (
        str(candidate_ref.get("rule_id") or ""),
        _canonical_action(action),
        str(action.get("target_model_label") or candidate_ref.get("target") or ""),
    )


def rebuild_candidate_queue(
    decision: Mapping[str, Any],
    state: PolicyState,
) -> int:
    """Persist the decision's executable candidates as an ordered queue (N25).

    Queue identity is the stable triple (rule_id, canonical action, target);
    full fingerprint-bearing candidate keys are computed by the server at
    evaluate time from the runner-supplied candidate_ref. The queue replaces
    any previous one: a new proposal on new evidence invalidates old entries.
    Each item carries the baseline fingerprint so evidence-independent
    rejection memory (2026-10-28 ruling 3) matches the evaluate-side keys.
    """
    baseline_fp = baseline_fingerprint_from_state(state)
    items: list[dict[str, Any]] = []
    for candidate in decision.get("candidate_actions") or []:
        action = candidate.get("action") or {}
        if action.get("action_type") not in MODEL_ACTION_TYPES:
            continue
        rule_id = str(candidate.get("rule_id") or "unknown_rule")
        ranking = candidate.get("ranking") or {}
        items.append(
            {
                "candidate_id": _queue_candidate_id(rule_id, action),
                "rule_id": rule_id,
                "action": copy.deepcopy(dict(action)),
                "priority": int(candidate.get("priority", 999)),
                "safety_rank": (
                    int(ranking["safety_rank"])
                    if isinstance(ranking.get("safety_rank"), int)
                    else None
                ),
                "status": "PENDING",
                "barrier": None,
                "baseline_fingerprint": baseline_fp,
            }
        )
    items.sort(
        key=lambda item: (
            item["safety_rank"] if item["safety_rank"] is not None else 99,
            item["priority"],
        )
    )
    barrier_rules = {
        str(trace.get("rule_id"))
        for trace in decision.get("rule_trace") or []
        if trace.get("outcome") == "INCONCLUSIVE" and trace.get("blocking", True)
    }
    for item in items:
        if item["rule_id"] in barrier_rules:
            item["barrier"] = item["rule_id"]
    state.pending_candidate_queue = items
    return len(items)


def _advance_queue(
    state: PolicyState,
    candidate_ref: Mapping[str, Any],
    status: str,
) -> dict[str, Any] | None:
    """Update the queued candidate identified by candidate_ref; return the
    next PENDING non-barrier item (marked SELECTED) or None."""
    identity = _candidate_ref_identity(candidate_ref)
    for item in state.pending_candidate_queue:
        if item.get("status") in {"PENDING", "SELECTED"} and _queue_item_identity(item) == identity:
            item["status"] = status
            break
    if status == "ACCEPTED":
        for item in state.pending_candidate_queue:
            if item.get("status") == "PENDING":
                item["status"] = "INVALIDATED"
                item["invalidation_reason"] = "baseline changed after ACCEPT_REFIT"
        return None
    for item in state.pending_candidate_queue:
        if item.get("status") == "PENDING" and not item.get("barrier"):
            item["status"] = "SELECTED"
            return item
    return None


def build_next_candidate_decision(
    state: PolicyState,
    *,
    round_id: str,
    previous_action_type: str,
    advance: bool = True,
) -> dict[str, Any] | None:
    """Build a schema-valid decision artifact for the next queued candidate.

    Called after a REJECT_REFIT/INCONCLUSIVE verdict; returns None when the
    queue has no selectable candidate left. ``advance=False`` performs a
    read-only lookup for idempotent duplicate submissions.
    """
    def _selectable(item: Mapping[str, Any]) -> bool:
        if item.get("status") not in {"PENDING", "SELECTED"} or item.get("barrier"):
            return False
        if item.get("baseline_fingerprint"):
            key = compute_stable_candidate_key(
                {
                    "action": item.get("action") or {},
                    "rule_id": item.get("rule_id"),
                    "target": (item.get("action") or {}).get("target_model_label"),
                    "baseline_artifact_fingerprint": item["baseline_fingerprint"],
                }
            )
            if key in state.rejected_stable_keys:
                item["status"] = "REJECTED"
                item["rejection_source"] = "rejected_stable_keys"
                return False
        return True

    item = next(
        (
            item
            for item in state.pending_candidate_queue
            if _selectable(item)
        ),
        None,
    )
    if item is None:
        return None
    if advance:
        item["status"] = "SELECTED"
    action = copy.deepcopy(item["action"])
    trace = [
        {
            "rule_id": "CANDIDATE_QUEUE_ADVANCE_V1",
            "outcome": "SATISFIED",
            "inputs": [item["candidate_id"], item["rule_id"]],
            "unmet_conditions": [],
            "detail": (
                f"Advanced from candidate {item['candidate_id']} after "
                f"{previous_action_type}."
            ),
            "blocking": False,
            "collector_id": None,
            "evidence_targets": [],
            "expected_new_fingerprint": None,
        }
    ]
    artifact = {
        "schema_version": "1.1",
        "round_id": round_id,
        "rules_version": "component-rules@v1",
        "thresholds_version": "thresholds@v1",
        "state": "PROPOSE",
        "action": action,
        "raw_decision": {"state": "PROPOSE", "action": copy.deepcopy(action), "rule_trace": trace},
        "candidate_actions": [
            {
                "rule_id": item["rule_id"],
                "priority": item["priority"],
                "status": "SELECTED",
                "action": copy.deepcopy(action),
                "detail": "advanced from the persisted candidate queue",
            }
        ],
        "workflow_status": "CONTINUE",
        "termination_checks": [],
        "rule_trace": trace,
        "evidence_refs": {"numeric_evidence": None, "vlm_evidence": None},
        "automation": {
            "policy_version": POLICY_VERSION,
            "resolution": "candidate_queue_advance",
            "original_action_type": previous_action_type,
            "resolved_rule_id": item["rule_id"],
            "reason": (
                "Previous candidate was rejected or inconclusive; consuming the "
                "next safe candidate from the persisted queue."
            ),
            "needs_review": False,
        },
    }
    validate(artifact, "decision_artifact")
    return artifact


def _collect_evidence_action(
    decision: dict[str, Any],
    *,
    trace: Mapping[str, Any],
    evidence_fingerprint: str,
    state: PolicyState,
) -> dict[str, Any]:
    rule_id = str(trace.get("rule_id") or "unknown_rule")
    collector_id = trace.get("collector_id")
    targets = list(trace.get("evidence_targets") or [])
    if not collector_id or not targets:
        return _conservative(
            decision,
            rule_id,
            "No schema-declared evidence collector or target was available.",
        )
    key = f"{collector_id}|{rule_id}|{evidence_fingerprint}|{','.join(targets)}"
    if key in state.evidence_collector_keys:
        return _conservative(
            decision,
            rule_id,
            "The same evidence collector input and fingerprint were already used.",
        )
    state.evidence_collector_keys.add(key)
    expected = trace.get("expected_new_fingerprint")
    if not expected or expected == evidence_fingerprint:
        expected = f"new:{rule_id}:{evidence_fingerprint[:16]}"
    action = {
        "action_type": "COLLECT_EVIDENCE",
        "continuation_reason": (
            f"Rule {rule_id} is inconclusive; collect a new evidence view before "
            "considering model actions."
        ),
        "next_step": "Run the declared collector and re-evaluate the changed evidence.",
        "next_transition": "COLLECT_EVIDENCE",
        "collector_id": collector_id,
        "evidence_targets": targets,
        "expected_input_change": "collector input or evidence view must change",
        "expected_new_fingerprint": expected,
        "max_attempts": 1,
    }
    return _with_automation(
        decision,
        action=action,
        resolution="collect_evidence",
        original_action_type="INCONCLUSIVE",
        resolved_rule_id=rule_id,
        reason="Evidence collection is explicit and does not execute a model fit.",
        workflow_status="CONTINUE",
    )


def apply_policy(
    decision: dict[str, Any],
    state: PolicyState,
    *,
    evidence_fingerprint: str = "",
    component: str | None = None,
    baseline_config_checksum: str | None = None,
    candidate_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve rule output through a deterministic candidate queue.

    COLLECT_EVIDENCE is the only non-fit continuation. Structural candidates
    remain executable actions; they are never globally blocked just because a
    component name was rejected in a different baseline/evidence context.
    """

    action = decision.get("action")
    action_type = action.get("action_type") if isinstance(action, dict) else None

    if decision["state"] == "EVALUATE_REFIT":
        if action_type == "REJECT_REFIT":
            if candidate_ref is not None:
                # Server-side key from the original candidate identity.
                state.rejected_candidate_keys.add(compute_candidate_key(candidate_ref))
                state.rejected_stable_keys.add(
                    compute_stable_candidate_key(candidate_ref)
                )
                _advance_queue(state, candidate_ref, "REJECTED")
            else:
                state.rejected_candidate_keys.add(
                    _candidate_key(
                        action,
                        evidence_fingerprint=evidence_fingerprint,
                        baseline_config_checksum=baseline_config_checksum,
                    )
                )
            return decision
        if action_type == "ACCEPT_REFIT":
            if candidate_ref is not None:
                _advance_queue(state, candidate_ref, "ACCEPTED")
            return decision
        if action_type != "INCONCLUSIVE":
            return decision
        if action_type != "INCONCLUSIVE":
            return decision
        target = component or (action or {}).get("component")
        if not target:
            target = (
                decision.get("refit_evaluation", {}).get("component")
                or "unknown"
            )
        if candidate_ref is not None:
            state.rejected_candidate_keys.add(compute_candidate_key(candidate_ref))
            _advance_queue(state, candidate_ref, "INCONCLUSIVE")
        else:
            state.rejected_candidate_keys.add(
                _candidate_key(
                    {"action_type": "REJECT_REFIT", "component": target},
                    evidence_fingerprint=evidence_fingerprint,
                    baseline_config_checksum=baseline_config_checksum,
                )
            )
        return _with_automation(
            decision,
            action={"action_type": "REJECT_REFIT", "component": target},
            resolution="reject_fallback",
            original_action_type="INCONCLUSIVE",
            resolved_rule_id=decision["rule_trace"][-1]["rule_id"],
            reason="EVALUATE_REFIT input was inconclusive; candidate was rejected and marked for review.",
            workflow_status="STOPPED_NEEDS_REVIEW",
        )

    if (
        decision["state"] == "PROPOSE"
        and action_type in MODEL_ACTION_TYPES
    ):
        vetoed = apply_stable_rejection_veto(decision, state, action)
        if vetoed is not None:
            return vetoed

    if action_type != "INCONCLUSIVE":
        return decision

    candidate_actions = decision.get("candidate_actions", [])

    # N25: consume the persisted queue first (ranked, pending, not blocked by
    # a higher-priority barrier, not already rejected in this evidence context).
    for item in state.pending_candidate_queue:
        if item.get("status") != "PENDING" or item.get("barrier"):
            continue
        candidate_action = item["action"]
        if item.get("baseline_fingerprint"):
            stable_key = compute_stable_candidate_key(
                {
                    "action": candidate_action,
                    "rule_id": item.get("rule_id"),
                    "target": candidate_action.get("target_model_label"),
                    "baseline_artifact_fingerprint": item["baseline_fingerprint"],
                }
            )
            if stable_key in state.rejected_stable_keys:
                item["status"] = "REJECTED"
                item["rejection_source"] = "rejected_stable_keys"
                continue
        key = _candidate_key(
            candidate_action,
            evidence_fingerprint=evidence_fingerprint,
            baseline_config_checksum=baseline_config_checksum,
        )
        if key in state.rejected_candidate_keys:
            item["status"] = "REJECTED"
            item["rejection_source"] = "rejected_candidate_keys"
            continue
        item["status"] = "SELECTED"
        resolved = _with_automation(
            decision,
            action=copy.deepcopy(candidate_action),
            resolution="candidate_queue",
            original_action_type=action_type or "INCONCLUSIVE",
            resolved_rule_id=item.get("rule_id"),
            reason="Selected the next safe candidate from the persisted candidate queue.",
            workflow_status="CONTINUE",
        )
        for view in resolved.get("candidate_actions", []):
            view["status"] = (
                "SELECTED"
                if view.get("rule_id") == item.get("rule_id")
                and _canonical_action(view.get("action") or {})
                == _canonical_action(candidate_action)
                else view.get("status", "DEFERRED")
            )
        return resolved

    executable = [
        item for item in candidate_actions
        if isinstance(item.get("action"), dict)
        and item["action"].get("action_type") not in {"INCONCLUSIVE", "COLLECT_EVIDENCE"}
    ]
    executable.sort(key=lambda item: (int(item.get("priority", 999)), str(item.get("rule_id", ""))))
    for candidate in executable:
        candidate_action = candidate["action"]
        key = _candidate_key(
            candidate_action,
            evidence_fingerprint=evidence_fingerprint,
            baseline_config_checksum=baseline_config_checksum,
        )
        if key in state.rejected_candidate_keys:
            continue
        resolved = _with_automation(
            decision,
            action=candidate_action,
            resolution="candidate_queue",
            original_action_type=action_type or "INCONCLUSIVE",
            resolved_rule_id=candidate.get("rule_id"),
            reason="Selected the next safe candidate from the deterministic candidate queue.",
            workflow_status="CONTINUE",
        )
        for item in resolved.get("candidate_actions", []):
            item["status"] = "SELECTED" if item.get("rule_id") == candidate.get("rule_id") else item.get("status", "DEFERRED")
        return resolved

    # Only BLOCKING inconclusives drive resolution (collectors, trial fits,
    # termination). Non-blocking gaps (companion, missing-input audits) are
    # audited but never converted into work (2026-10-28 ruling).
    inconclusive_trace = next(
        (
            item
            for item in reversed(decision.get("rule_trace", []))
            if item.get("outcome") == "INCONCLUSIVE" and item.get("blocking", True)
        ),
        None,
    )
    if inconclusive_trace is None:
        return decision
    rule_id = inconclusive_trace["rule_id"]
    # N25: memory keys are scoped by rule, target and baseline so one target's
    # termination never ends the same rule's other targets.
    targets_key = ",".join(
        sorted({str(value) for value in (inconclusive_trace.get("evidence_targets") or [])})
    ) or "*"
    memory_key = f"{rule_id}|{targets_key}"
    memory_value = f"{evidence_fingerprint}|{baseline_config_checksum or ''}"
    occurrences = state.inconclusive_counts.get(memory_key, 0) + 1
    state.inconclusive_counts[memory_key] = occurrences
    repeated = (
        state.inconclusive_seen.get(memory_key) == memory_value
        and memory_value != "|"
    )
    # N27: VLM responses vary between rounds, so an unchanged-fingerprint test
    # alone never fires while a collector keeps regenerating evidence. The
    # absolute per-key cap (3rd occurrence) terminates the loop instead.
    capped = occurrences >= 3
    if memory_key in state.terminated_rules or repeated or capped:
        state.terminated_rules.add(memory_key)
        return _conservative(
            decision,
            rule_id,
            "Repeated INCONCLUSIVE with unchanged evidence for this target/baseline; "
            "rule terminated for this target only."
            if repeated
            else f"Rule hit the blocking-INCONCLUSIVE occurrence cap ({occurrences}) "
            "for this target/baseline despite refreshed evidence; terminated to stop "
            "an unproductive collector loop."
            if capped
            else "Rule already terminated for this target/baseline.",
        )
    state.inconclusive_seen[memory_key] = memory_value

    trace_collector = inconclusive_trace.get("collector_id")
    trace_targets = inconclusive_trace.get("evidence_targets") or []
    template = _TRIAL_FIT_ACTIONS.get(rule_id)
    if trace_collector and trace_targets and template is None:
        return _collect_evidence_action(
            decision,
            trace=inconclusive_trace,
            evidence_fingerprint=evidence_fingerprint,
            state=state,
        )

    if template is not None:
        proposed = template.get("component") or template.get("replace_to")
        if state.trials_used >= state.trial_budget:
            return _conservative(
                decision, rule_id, f"Trial budget exhausted ({state.trial_budget} trial fits)."
            )
        candidate_key = _candidate_key(
            template,
            evidence_fingerprint=evidence_fingerprint,
            baseline_config_checksum=baseline_config_checksum,
        )
        if candidate_key in state.rejected_candidate_keys:
            return _conservative(
                decision,
                rule_id,
                f"Candidate '{proposed}' was already rejected for this evidence context; not re-proposed.",
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
        # Preserve the ORIGINAL numeric+VLM view: the first run saw the failed
        # VLM evidence; the fallback retry must only affect the resolved view.
        with_vlm_view = copy.deepcopy(decision)
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
            "numeric_plus_vlm": {
                "parse_status": vlm_evidence.get("parse_status"),
                "observations": copy.deepcopy(vlm_evidence.get("observations", [])),
                "rule_trace": copy.deepcopy(with_vlm_view.get("rule_trace", [])),
                "action": copy.deepcopy(with_vlm_view.get("action")),
                "candidate_actions": copy.deepcopy(with_vlm_view.get("candidate_actions", [])),
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
            "numeric_plus_vlm": {
                "parse_status": "OK",
                "observations": copy.deepcopy(vlm_evidence.get("observations", [])),
                "rule_trace": copy.deepcopy(decision.get("rule_trace", [])),
                "action": copy.deepcopy(decision.get("action")),
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
    evidence_fingerprint: str = "",
    baseline_config_checksum: str | None = None,
    candidate_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """evaluate_refit plus automation policy: INCONCLUSIVE falls back to reject.

    When a schema-valid ``candidate_ref`` is supplied, the decision is stamped
    as schema v1.2 (EVALUATE_REFIT requires candidate_ref) and the rejection
    key is computed server-side from the original candidate identity instead
    of the deprecated flat fingerprint parameters.
    """
    if candidate_ref is not None:
        candidate_ref = dict(candidate_ref)
        validate(candidate_ref, "candidate_ref")

    decision = evaluate_refit(
        round_id=round_id,
        component=component,
        refit_evaluation=refit_evaluation,
        candidate_action_type=candidate_action_type,
        candidate_reason_code=candidate_reason_code,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
    )
    if candidate_ref is not None:
        decision["candidate_ref"] = copy.deepcopy(candidate_ref)
        decision["schema_version"] = "1.2"
    return apply_policy(
        decision,
        state,
        component=component,
        evidence_fingerprint=evidence_fingerprint,
        baseline_config_checksum=baseline_config_checksum,
        candidate_ref=candidate_ref,
    )
