"""Shadow-mode component analysis primitives.

This package is intentionally independent from the existing fitting workflow.
It exposes deterministic numeric measurements and pure decision rules that can
be exercised before any production integration is attempted.
"""

from .artifact_adapter import (
    RESULT_HDU,
    build_manifest,
    extract_numeric_evidence_from_manifest,
    load_band_arrays,
)
from .derived import (
    derive_fit_features,
    derive_rule_features,
    evaluate_bar_psf_veto,
    measure_radial_residual_systematic,
    merge_wcs_candidate_regions,
    summarize_isophote_profile,
)
from .numeric import (
    BandArrays,
    deconvolve_fwhm,
    detect_local_peaks,
    extract_numeric_evidence,
    measure_aperture_snr,
    measure_azimuthal_modes,
    measure_directional_harmonic_alignment,
    measure_fwhm,
    measure_psf_fwhm,
    measure_weighted_moments,
)
from .policy import (
    POLICY_VERSION,
    PolicyState,
    apply_policy,
    decide_proposal_with_policy,
    evaluate_refit_with_policy,
)
from .provider import OpenAICompatibleVLM
from .candidate_overlay import create_candidate_overlay
from .rules import RuleThresholds, decide_proposal, evaluate_refit
from .vlm import (
    CONTROLLED_LABELS,
    PROMPT_VERSION,
    allowed_target_ids,
    build_vlm_prompt,
    make_unavailable_vlm_evidence,
    parse_vlm_response,
)
from .shadow import run_shadow_round

__all__ = [
    "BandArrays",
    "CONTROLLED_LABELS",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "PolicyState",
    "OpenAICompatibleVLM",
    "create_candidate_overlay",
    "RESULT_HDU",
    "RuleThresholds",
    "allowed_target_ids",
    "apply_policy",
    "build_vlm_prompt",
    "build_manifest",
    "decide_proposal",
    "decide_proposal_with_policy",
    "deconvolve_fwhm",
    "derive_fit_features",
    "derive_rule_features",
    "evaluate_bar_psf_veto",
    "detect_local_peaks",
    "evaluate_refit",
    "evaluate_refit_with_policy",
    "extract_numeric_evidence",
    "measure_aperture_snr",
    "measure_azimuthal_modes",
    "measure_directional_harmonic_alignment",
    "measure_fwhm",
    "measure_psf_fwhm",
    "measure_radial_residual_systematic",
    "measure_weighted_moments",
    "make_unavailable_vlm_evidence",
    "parse_vlm_response",
    "merge_wcs_candidate_regions",
    "summarize_isophote_profile",
    "extract_numeric_evidence_from_manifest",
    "load_band_arrays",
    "run_shadow_round",
]
