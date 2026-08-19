import math

import numpy as np
import pytest
from astropy.io import fits

from eval.eff_bic.metrics import (
    compute_effective_bic,
    compute_effective_bic_from_files,
    gaussian_fwhm_area,
    noise_equivalent_area,
)


def _gaussian(shape=(41, 41), sigma_x=2.0, sigma_y=3.0):
    y, x = np.mgrid[: shape[0], : shape[1]]
    x0 = (shape[1] - 1) / 2
    y0 = (shape[0] - 1) / 2
    return np.exp(-0.5 * (((x - x0) / sigma_x) ** 2 + ((y - y0) / sigma_y) ** 2))


def test_noise_equivalent_area_is_scale_invariant():
    psf = _gaussian()
    assert noise_equivalent_area(psf) == pytest.approx(
        noise_equivalent_area(psf * 123.4), rel=1.0e-12
    )
    assert noise_equivalent_area(psf) == pytest.approx(4.0 * math.pi * 2.0 * 3.0, rel=2.0e-6)


def test_gaussian_fwhm_area_reproduces_document_definition():
    psf = _gaussian(sigma_x=2.0, sigma_y=4.0)
    expected_fwhm = 2.354820045 * (2.0 + 4.0) / 2.0
    expected = math.pi * (expected_fwhm / 2.0) ** 2
    assert gaussian_fwhm_area(psf) == pytest.approx(expected, rel=1.0e-4)


def test_effective_bic_formula_and_direction():
    parent = compute_effective_bic(chi2=10000, ndof=9993, nfree=7, psf_area=10)
    child = compute_effective_bic(chi2=9900, ndof=9988, nfree=12, psf_area=10)
    assert parent["n_pixels"] == 10000
    assert parent["n_effective"] == pytest.approx(1000)
    assert parent["bic_effective"] == pytest.approx(1000 + 7 * math.log(1000))
    assert child["bic_effective"] < parent["bic_effective"]


def test_effective_bic_reads_galfit_header_and_psf(tmp_path):
    output = tmp_path / "output.fits"
    primary = fits.PrimaryHDU()
    model = fits.ImageHDU(np.zeros((8, 8)), name="MODEL")
    model.header["OBJECT"] = "model"
    model.header["CHISQ"] = 100.0
    model.header["NDOF"] = 60
    model.header["NFREE"] = 4
    fits.HDUList([primary, model]).writeto(output)

    psf_path = tmp_path / "psf.fits"
    fits.PrimaryHDU(_gaussian(shape=(21, 21), sigma_x=1.5, sigma_y=1.5)).writeto(psf_path)
    result = compute_effective_bic_from_files(output, psf_path)
    assert result["n_pixels"] == 64
    assert result["psf_area"] > 1
    assert math.isfinite(result["bic_effective"])
