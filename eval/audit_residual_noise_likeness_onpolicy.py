"""Audit fixed-region noise-likeness scores on grouped on-policy rollouts."""

from eval import audit_residual_v12_5_onpolicy as audit
from eval.residual_noise_likeness import (
    FEATURE_NAMES,
    compute_noise_likeness_badness,
    compute_noise_likeness_deltas,
)


def main():
    audit.FEATURE_NAMES = FEATURE_NAMES
    audit.compute_residual_badness_features = compute_noise_likeness_badness
    audit.compute_residual_feature_deltas = compute_noise_likeness_deltas
    # The generic auditor expects the same schema regardless of feature family.
    # Pass noise_likeness_model_config.json as --model-config.
    audit.main()


if __name__ == "__main__":
    main()
