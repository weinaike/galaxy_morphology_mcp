"""Experimental 2D effective-BIC calculations for PSF-correlated images.

This module deliberately lives outside ``src``.  The effective sample-size
approximation is a research hypothesis to validate against the independent VLM
labels; it must not silently replace the production 1D BIC.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
from astropy.io import fits

PsfAreaMethod = Literal["noise_equivalent", "gaussian_fwhm"]


def _clean_psf(psf: np.ndarray) -> np.ndarray:
    data = np.asarray(np.squeeze(psf), dtype=np.float64)
    if data.ndim != 2:
        raise ValueError(f"PSF must be two-dimensional after squeeze, got {data.shape}")
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    data = np.maximum(data, 0.0)
    if not np.any(data > 0):
        raise ValueError("PSF contains no positive finite flux")
    return data


def noise_equivalent_area(psf: np.ndarray) -> float:
    """Return ``(sum(P)**2) / sum(P**2)`` in pixel units.

    This quantity is invariant to the absolute normalization of the PSF.  It is
    also commonly called the noise-equivalent area.  Here it is only used as an
    approximation to the correlation area and is retained as an explicit field
    in every result for later auditing.
    """

    data = _clean_psf(psf)
    flux = float(np.sum(data))
    squared_flux = float(np.sum(np.square(data)))
    if flux <= 0 or squared_flux <= 0:
        raise ValueError("PSF normalization is not positive")
    return (flux * flux) / squared_flux


def gaussian_fwhm_area(psf: np.ndarray) -> float:
    """Fit a 2D Gaussian and return the document's circular FWHM area.

    The supplied proposal defines ``A_psf = pi * (mean(FWHM_x, FWHM_y)/2)^2``.
    It is intentionally implemented as a separate method because it is not
    equivalent to the noise-equivalent area and may reverse pairwise labels.
    """

    from astropy.modeling import models
    from astropy.modeling.fitting import LevMarLSQFitter

    data = _clean_psf(psf)
    y_peak, x_peak = np.unravel_index(np.argmax(data), data.shape)
    y_grid, x_grid = np.mgrid[: data.shape[0], : data.shape[1]]
    initial_stddev = max(1.0, min(data.shape) / 8.0)
    initial = models.Gaussian2D(
        amplitude=float(data.max()),
        x_mean=float(x_peak),
        y_mean=float(y_peak),
        x_stddev=initial_stddev,
        y_stddev=initial_stddev,
    )
    initial.x_stddev.bounds = (1.0e-3, float(max(data.shape)))
    initial.y_stddev.bounds = (1.0e-3, float(max(data.shape)))
    fitted = LevMarLSQFitter()(initial, x_grid, y_grid, data)
    fwhm_x = abs(float(fitted.x_stddev.value)) * 2.354820045
    fwhm_y = abs(float(fitted.y_stddev.value)) * 2.354820045
    fwhm_mean = (fwhm_x + fwhm_y) / 2.0
    area = math.pi * (fwhm_mean / 2.0) ** 2
    if not math.isfinite(area) or area <= 0:
        raise ValueError(f"invalid Gaussian FWHM PSF area: {area}")
    return area


def calculate_psf_area(psf: np.ndarray, method: PsfAreaMethod) -> float:
    if method == "noise_equivalent":
        return noise_equivalent_area(psf)
    if method == "gaussian_fwhm":
        return gaussian_fwhm_area(psf)
    raise ValueError(f"unsupported PSF area method: {method!r}")


def compute_effective_bic(
    *,
    chi2: float,
    ndof: int,
    nfree: int,
    psf_area: float,
) -> dict[str, float | int]:
    """Compute the experimental PSF-corrected 2D BIC.

    GALFIT stores ``NDOF = N - k`` and ``NFREE = k``.  Consequently the number
    of fitted pixels is reconstructed as ``N = NDOF + NFREE``.  The tested
    approximation is

        BIC_eff = chi2 / A_psf + k * log(N / A_psf).

    The mathematically correct factored form is

        (chi2 + A_psf * k * log(N / A_psf)) / A_psf,

    not the third equality written in the supplied note.
    """

    chi2 = float(chi2)
    ndof = int(ndof)
    nfree = int(nfree)
    psf_area = float(psf_area)
    if not math.isfinite(chi2) or chi2 < 0:
        raise ValueError(f"chi2 must be finite and non-negative, got {chi2}")
    if ndof <= 0:
        raise ValueError(f"ndof must be positive, got {ndof}")
    if nfree < 0:
        raise ValueError(f"nfree must be non-negative, got {nfree}")
    if not math.isfinite(psf_area) or psf_area <= 0:
        raise ValueError(f"psf_area must be finite and positive, got {psf_area}")

    n_pixels = ndof + nfree
    n_effective = n_pixels / psf_area
    if n_effective <= 1:
        raise ValueError(
            f"effective sample count must exceed one, got N_eff={n_effective}"
        )
    bic_2d = chi2 + nfree * math.log(n_pixels)
    bic_effective = chi2 / psf_area + nfree * math.log(n_effective)
    return {
        "chi2": chi2,
        "ndof": ndof,
        "nfree": nfree,
        "n_pixels": n_pixels,
        "psf_area": psf_area,
        "n_effective": n_effective,
        "bic_2d": bic_2d,
        "bic_effective": bic_effective,
    }


def read_galfit_statistics(output_fits: str | Path) -> dict[str, float | int]:
    """Read CHISQ, NDOF and NFREE from the GALFIT model HDU."""

    with fits.open(output_fits, memmap=False) as hdul:
        for hdu in hdul:
            if str(hdu.header.get("OBJECT", "")).strip().lower() == "model":
                missing = [
                    key for key in ("CHISQ", "NDOF", "NFREE") if key not in hdu.header
                ]
                if missing:
                    raise KeyError(f"GALFIT model header missing {missing}: {output_fits}")
                return {
                    "chi2": float(hdu.header["CHISQ"]),
                    "ndof": int(hdu.header["NDOF"]),
                    "nfree": int(hdu.header["NFREE"]),
                }
    raise ValueError(f"GALFIT model HDU not found: {output_fits}")


def compute_effective_bic_from_files(
    output_fits: str | Path,
    psf_fits: str | Path,
    *,
    method: PsfAreaMethod = "noise_equivalent",
) -> dict[str, Any]:
    statistics = read_galfit_statistics(output_fits)
    psf = fits.getdata(psf_fits, memmap=False)
    psf_area = calculate_psf_area(psf, method)
    result = compute_effective_bic(**statistics, psf_area=psf_area)
    result.update(
        psf_area_method=method,
        output_fits=str(Path(output_fits).resolve()),
        psf_fits=str(Path(psf_fits).resolve()),
    )
    return result
