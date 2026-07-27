"""GRPO-facing shaping for the final v11 rule-based reward.

The v11 raw formula is identical to v8.  This module deliberately keeps
``compute_rl_reward`` unchanged and adds:

1. rollout-level failure attribution and coarse reward levels;
2. a bounded, low-weight continuous margin;
3. same-parent group gating for the first GRPO stage.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

import numpy as np


V11_THRESHOLD = 0.05139489475137804
DEFAULT_MARGIN_WEIGHT = 0.2
DEFAULT_MARGIN_SCALE = 1.0

OUTCOME_SUCCESS = "success"
OUTCOME_POLICY_INVALID = "policy_invalid"
OUTCOME_POLICY_EXECUTION_FAILURE = "policy_execution_failure"
OUTCOME_EVALUATOR_FAILURE = "evaluator_failure"

SUPPORTED_OUTCOMES = {
    OUTCOME_SUCCESS,
    OUTCOME_POLICY_INVALID,
    OUTCOME_POLICY_EXECUTION_FAILURE,
    OUTCOME_EVALUATOR_FAILURE,
}


def calibrate_margin_scale(
    raw_rewards: Iterable[float],
    *,
    method: str = "iqr",
    min_scale: float = 1e-6,
) -> float:
    """Estimate a robust reward scale from a fixed validation split.

    ``method="iqr"`` returns Q75-Q25. ``method="mad"`` returns the
    normal-consistent MAD (1.4826 * median absolute deviation).
    """

    values = np.asarray(list(raw_rewards), dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("raw_rewards must contain at least one finite value")
    if min_scale <= 0 or not np.isfinite(min_scale):
        raise ValueError("min_scale must be finite and > 0")

    if method == "iqr":
        scale = float(np.percentile(values, 75) - np.percentile(values, 25))
    elif method == "mad":
        median = float(np.median(values))
        scale = 1.4826 * float(np.median(np.abs(values - median)))
    else:
        raise ValueError("method must be 'iqr' or 'mad'")

    return max(scale, min_scale)


def shape_grpo_reward(
    raw_result: Mapping[str, Any] | None,
    *,
    outcome: str = OUTCOME_SUCCESS,
    threshold: float = V11_THRESHOLD,
    margin_scale: float = DEFAULT_MARGIN_SCALE,
    margin_weight: float = DEFAULT_MARGIN_WEIGHT,
    failure_reason: str | None = None,
) -> Dict[str, Any]:
    """Convert one v11 result into a GRPO rollout reward record.

    Outcome semantics:
      - ``success``: executable; coarse level is 1 iff raw > threshold.
      - ``policy_invalid`` / ``policy_execution_failure``: coarse level -1.
      - ``evaluator_failure``: masked out; the policy receives no signal.

    ``margin_weight`` must stay below 0.5. Before group normalization this
    guarantees that the bounded margin cannot reverse coarse 1 > coarse 0.
    UI-S1 stage one should additionally use ``mean_norm`` and group gating.
    """

    if outcome not in SUPPORTED_OUTCOMES:
        raise ValueError(f"unsupported outcome: {outcome!r}")
    if not np.isfinite(threshold):
        raise ValueError("threshold must be finite")
    if margin_scale <= 0 or not np.isfinite(margin_scale):
        raise ValueError("margin_scale must be finite and > 0")
    if not 0 <= margin_weight < 0.5:
        raise ValueError("margin_weight must satisfy 0 <= weight < 0.5")

    raw_reward = None
    if raw_result is not None:
        candidate = raw_result.get("reward")
        if candidate is not None:
            try:
                candidate = float(candidate)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None and np.isfinite(candidate):
                raw_reward = candidate

    base = {
        "reward_version": "v11",
        "raw_formula": "v8_equivalent",
        "threshold": float(threshold),
        "margin_scale": float(margin_scale),
        "margin_weight": float(margin_weight),
        "outcome": outcome,
        "failure_reason": failure_reason,
        "raw_reward": raw_reward,
        "coarse_reward": None,
        "margin": 0.0,
        "shaped_reward": 0.0,
        "sample_train_mask": False,
        "group_train_mask": False,
    }

    if outcome == OUTCOME_EVALUATOR_FAILURE:
        return base

    if outcome in {OUTCOME_POLICY_INVALID, OUTCOME_POLICY_EXECUTION_FAILURE}:
        base.update(
            coarse_reward=-1.0,
            shaped_reward=-1.0,
            sample_train_mask=True,
        )
        return base

    if raw_reward is None:
        base.update(
            outcome=OUTCOME_EVALUATOR_FAILURE,
            failure_reason=failure_reason or "missing_or_nonfinite_raw_reward",
        )
        return base

    coarse = 1.0 if raw_reward > threshold else 0.0
    margin = float(np.clip((raw_reward - threshold) / margin_scale, -1.0, 1.0))
    shaped = coarse + margin_weight * margin
    base.update(
        coarse_reward=coarse,
        margin=margin,
        shaped_reward=float(shaped),
        sample_train_mask=True,
    )
    return base


def gate_grpo_group(
    rollout_results: Sequence[Mapping[str, Any]],
    *,
    min_valid_candidates: int = 2,
) -> Dict[str, Any]:
    """Gate one same-parent/step group using only validated coarse order.

    Stage one trains a group only when at least two unmasked candidates exist
    and their coarse levels are not all identical. Evaluator failures never
    count toward the valid-candidate threshold.
    """

    if min_valid_candidates < 2:
        raise ValueError("min_valid_candidates must be >= 2")

    copied: List[Dict[str, Any]] = [dict(result) for result in rollout_results]
    valid_indices = [
        index
        for index, result in enumerate(copied)
        if bool(result.get("sample_train_mask"))
        and result.get("coarse_reward") is not None
    ]
    coarse_levels = sorted(
        {float(copied[index]["coarse_reward"]) for index in valid_indices}
    )

    if len(valid_indices) < min_valid_candidates:
        trainable = False
        reason = "too_few_valid_candidates"
    elif len(coarse_levels) < 2:
        trainable = False
        reason = "homogeneous_coarse_level"
    else:
        trainable = True
        reason = "mixed_coarse_levels"

    for index, result in enumerate(copied):
        result["group_train_mask"] = bool(trainable and index in valid_indices)

    return {
        "group_train_mask": trainable,
        "gate_reason": reason,
        "num_candidates": len(copied),
        "num_valid_candidates": len(valid_indices),
        "coarse_levels": coarse_levels,
        "rollout_results": copied,
    }

