"""Shadow-mode component analysis primitives.

This package is intentionally independent from the existing fitting workflow.
It exposes deterministic numeric measurements and pure decision rules that can
be exercised before any production integration is attempted.
"""

from .numeric import (
    BandArrays,
    deconvolve_fwhm,
    detect_local_peaks,
    extract_numeric_evidence,
    measure_aperture_snr,
    measure_azimuthal_modes,
    measure_fwhm,
    measure_weighted_moments,
)
from .policy import (
    POLICY_VERSION,
    PolicyState,
    apply_policy,
    decide_proposal_with_policy,
    evaluate_refit_with_policy,
)
from .rules import RuleThresholds, decide_proposal, evaluate_refit

__all__ = [
    "BandArrays",
    "POLICY_VERSION",
    "PolicyState",
    "RuleThresholds",
    "apply_policy",
    "decide_proposal",
    "decide_proposal_with_policy",
    "deconvolve_fwhm",
    "detect_local_peaks",
    "evaluate_refit",
    "evaluate_refit_with_policy",
    "extract_numeric_evidence",
    "measure_aperture_snr",
    "measure_azimuthal_modes",
    "measure_fwhm",
    "measure_weighted_moments",
]
