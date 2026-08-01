"""Unit tests for run_galfit.py module using real NGC1097 test data."""

import os

import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import gaussian_filter

from tools.run_galfit import create_comparison_png

# Fit region from NGC1097.feedme: H) 37 1467 25 1455 (1-indexed)
FIT_REGION = (37, 1467, 25, 1455)


@pytest.fixture
def real_galfit_output(tmp_path, test_data_dir):
    """Create a GALFIT-style output FITS from the real NGC1097 input image.

    Reads the actual galaxy image, crops to the fit region, and generates
    model/residual HDUs to simulate GALFIT output.
    """
    input_image = test_data_dir / "NGC1097.phot.1_nonan.fits"
    if not input_image.exists():
        pytest.skip("Real test data not available")

    # Read real input image and crop to fit region (1-indexed, inclusive)
    data_full = fits.getdata(str(input_image))
    xmin, xmax, ymin, ymax = FIT_REGION
    original = data_full[ymin - 1 : ymax, xmin - 1 : xmax].astype(np.float64)

    # Create a simple model (Gaussian-smoothed approximation)
    model = gaussian_filter(original, sigma=5.0)

    # Create residual
    residual = original - model

    # Build GALFIT-style output FITS
    primary_hdu = fits.PrimaryHDU()
    primary_hdu.header["OBJECT"] = "NGC1097"

    orig_hdu = fits.ImageHDU(original, name="PRIMARY")
    orig_hdu.header["OBJECT"] = "NGC1097[1/1][37:1467,25:1455]"

    model_hdu = fits.ImageHDU(model, name="PRIMARY")
    model_hdu.header["OBJECT"] = "model"

    resid_hdu = fits.ImageHDU(residual, name="PRIMARY")
    resid_hdu.header["OBJECT"] = "residual map"

    hdul = fits.HDUList([primary_hdu, orig_hdu, model_hdu, resid_hdu])

    output_path = tmp_path / "NGC1097_galfit.fits"
    hdul.writeto(str(output_path), overwrite=True)
    return str(output_path)


class TestCreateComparisonPng:
    """Tests for create_comparison_png using real NGC1097 data."""

    def test_create_comparison_basic(self, real_galfit_output):
        """Test basic comparison PNG creation with real galaxy data."""
        result = create_comparison_png(real_galfit_output)

        assert result is not None
        assert result.endswith("_comparison.png")
        assert os.path.exists(result)
        assert os.path.getsize(result) > 10000

    def test_create_comparison_with_sigma(self, real_galfit_output, test_data_dir):
        """Test comparison PNG with real sigma normalization."""
        sigma_file = str(test_data_dir / "NGC1097_sigma2014.fits")

        result = create_comparison_png(real_galfit_output, sigma_file=sigma_file)
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_with_mask(self, real_galfit_output, test_data_dir):
        """Test comparison PNG with real mask overlay."""
        mask_file = str(test_data_dir / "NGC1097.1.finmask_nonan.fits")

        result = create_comparison_png(real_galfit_output, mask_file=mask_file)
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_with_all(self, real_galfit_output, test_data_dir):
        """Test comparison PNG with real sigma, mask, fit_region, and param_file."""
        result = create_comparison_png(
            real_galfit_output,
            sigma_file=str(test_data_dir / "NGC1097_sigma2014.fits"),
            mask_file=str(test_data_dir / "NGC1097.1.finmask_nonan.fits"),
            fit_region=FIT_REGION,
            param_file=str(test_data_dir / "NGC1097.feedme"),
        )
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_with_fit_region(self, real_galfit_output):
        """Test comparison PNG with explicit fit_region for coordinate display."""
        result = create_comparison_png(
            real_galfit_output,
            fit_region=FIT_REGION,
        )
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_with_param_file(self, real_galfit_output, test_data_dir):
        """Test comparison PNG with real feedme for component contour overlay."""
        result = create_comparison_png(
            real_galfit_output,
            param_file=str(test_data_dir / "NGC1097.feedme"),
        )
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_mask_cropping(self, real_galfit_output, test_data_dir):
        """Test that real mask (full-frame) is correctly cropped to match FITS dimensions."""
        result = create_comparison_png(
            real_galfit_output,
            mask_file=str(test_data_dir / "NGC1097.1.finmask_nonan.fits"),
            fit_region=FIT_REGION,
        )
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_invalid_fits(self, tmp_path):
        """Test with invalid FITS file."""
        invalid_file = tmp_path / "invalid.fits"
        invalid_file.write_text("not a fits file")

        result = create_comparison_png(str(invalid_file))
        assert result is None

    def test_create_comparison_missing_sigma_mask(self, real_galfit_output, tmp_path):
        """Test with non-existent sigma and mask files (should still succeed)."""
        result = create_comparison_png(
            real_galfit_output,
            sigma_file=str(tmp_path / "nonexistent_sigma.fits"),
            mask_file=str(tmp_path / "nonexistent_mask.fits"),
        )
        assert result is not None
        assert os.path.exists(result)

    def test_full_workflow_with_real_galfit(self, test_data_dir):
        """Test full workflow: run GALFIT on real data then create comparison PNG."""
        import asyncio

        from tools.run_galfit import run_galfit

        feedme = str(test_data_dir / "NGC1097.feedme")
        result = asyncio.run(run_galfit(feedme))
        assert result["status"] == "success"

        fits_out = result.get("optimized_fits_file")
        assert fits_out and os.path.exists(fits_out)

        png = create_comparison_png(
            fits_out,
            sigma_file=str(test_data_dir / "NGC1097_sigma2014.fits"),
            mask_file=str(test_data_dir / "NGC1097.1.finmask_nonan.fits"),
            fit_region=FIT_REGION,
            param_file=feedme,
        )
        assert png is not None
        assert os.path.exists(png)
