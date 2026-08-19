"""Experimental effective-BIC utilities kept outside the production ``src`` tree."""

from .metrics import (
    compute_effective_bic,
    compute_effective_bic_from_files,
    gaussian_fwhm_area,
    noise_equivalent_area,
)

__all__ = [
    "compute_effective_bic",
    "compute_effective_bic_from_files",
    "gaussian_fwhm_area",
    "noise_equivalent_area",
]
