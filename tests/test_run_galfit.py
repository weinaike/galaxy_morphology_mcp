"""Unit tests for run_galfit.py module."""

import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.run_galfit import _parse_galfit_config, create_comparison_png


class TestParseGalfitConfig:
    """Tests for _parse_galfit_config function."""

    def test_parse_full_config(self, tmp_path):
        """Test parsing a complete GALFIT config file."""
        config_content = """================================================================================
# IMAGE and GALFIT CONTROL PARAMETERS
A) /path/to/input.fits          # Input data image
B) /path/to/output.fits         # Output data image
C) /path/to/sigma.fits         # Sigma image
D) /path/to/psf.fits           # PSF image
E) 1                              # PSF sampling
F) /path/to/mask.fits          # Bad pixel mask
G) /path/to/constraints.txt    # Constraints
H) 100   200  100   200         # Region
I) 256   256                    # Convolution box
J) 25.0                         # Zero point
K) 0.06  0.06                   # Plate scale
O) regular                      # Display type
P) 0                            # Optimize
# INITIAL FITTING PARAMETERS
0) sersic
1) 100  100  1  1
3) 18.0  1
Z) 0
================================================================================
"""
        config_file = tmp_path / "test.feedme"
        config_file.write_text(config_content)

        result = _parse_galfit_config(str(config_file))

        assert result["input"] == "/path/to/input.fits"
        assert result["output"] == "/path/to/output.fits"
        assert result["sigma"] == "/path/to/sigma.fits"
        assert result["psf"] == "/path/to/psf.fits"
        assert result["mask"] == "/path/to/mask.fits"
        assert result["constraint"] == "/path/to/constraints.txt"

    def test_parse_with_none_values(self, tmp_path):
        """Test parsing config with 'none' values."""
        config_content = """================================================================================
A) /path/to/input.fits          # Input
B) /path/to/output.fits         # Output
C) none                         # Sigma (none)
D) none                         # PSF (none)
F) none                         # Mask (none)
G) none                         # Constraints (none)
H) 100   200  100   200
I) 256   256
J) 25.0
K) 0.06  0.06
O) regular
P) 0
0) sersic
1) 100  100  1  1
3) 18.0  1
Z) 0
================================================================================
"""
        config_file = tmp_path / "test_none.feedme"
        config_file.write_text(config_content)

        result = _parse_galfit_config(str(config_file))

        assert result["input"] == "/path/to/input.fits"
        assert result["output"] == "/path/to/output.fits"
        assert result["sigma"] == ""
        assert result["psf"] == ""
        assert result["mask"] == ""
        assert result["constraint"] == ""

    def test_parse_minimal_config(self, tmp_path):
        """Test parsing minimal config with only required fields."""
        config_content = """================================================================================
A) /path/to/input.fits          # Input
B) /path/to/output.fits         # Output
C) none                         # Sigma
D) none                         # PSF
F) none                         # Mask
G) none                         # Constraints
H) 100   200  100   200
I) 256   256
J) 25.0
K) 0.06  0.06
O) regular
P) 0
0) sersic
1) 100  100  1  1
3) 18.0  1
Z) 0
================================================================================
"""
        config_file = tmp_path / "test_minimal.feedme"
        config_file.write_text(config_content)

        result = _parse_galfit_config(str(config_file))

        assert result["input"] == "/path/to/input.fits"
        assert result["output"] == "/path/to/output.fits"


class TestCreateComparisonPng:
    """Tests for create_comparison_png function."""

    def _create_mock_fits(self, path: str, shape=(100, 100)):
        """Create a mock GALFIT output FITS file."""
        data = np.random.random(shape) * 100
        primary_hdu = fits.PrimaryHDU()
        primary_hdu.header['OBJECT'] = 'test'

        # Original data
        orig_hdu = fits.ImageHDU(data, name='PRIMARY')
        orig_hdu.header['OBJECT'] = 'TEST[1/1][0:100,0:100]'

        # Model data
        model_hdu = fits.ImageHDU(data * 0.9, name='PRIMARY')
        model_hdu.header['OBJECT'] = 'model'

        # Residual data
        resid_hdu = fits.ImageHDU(data * 0.1, name='PRIMARY')
        resid_hdu.header['OBJECT'] = 'residual map'

        hdul = fits.HDUList([primary_hdu, orig_hdu, model_hdu, resid_hdu])
        hdul.writeto(path, overwrite=True)

    def _create_mask_file(self, path: str, shape=(100, 100)):
        """Create a mock mask file."""
        # Create mask with center region valid (1), edges masked (0)
        mask = np.zeros(shape, dtype=np.int32)
        center_y, center_x = shape[0] // 2, shape[1] // 2
        radius = min(shape) // 3
        y, x = np.ogrid[:shape[0], :shape[1]]
        dist = np.sqrt((y - center_y)**2 + (x - center_x)**2)
        mask[dist <= radius] = 1

        hdu = fits.PrimaryHDU(mask)
        hdu.writeto(path, overwrite=True)

    def _create_sigma_file(self, path: str, shape=(100, 100)):
        """Create a mock sigma file."""
        # Normal sigma values around 0.1, with some bad pixels
        sigma = np.random.random(shape) * 0.1 + 0.05
        # Add some bad pixels
        sigma[0:5, 0:5] = 1e10  # Very large values
        sigma[95:100, 95:100] = 0  # Zero values

        hdu = fits.PrimaryHDU(sigma)
        hdu.writeto(path, overwrite=True)

    def test_create_comparison_basic(self, tmp_path):
        """Test basic comparison PNG creation without sigma/mask."""
        fits_file = tmp_path / "output.fits"
        self._create_mock_fits(str(fits_file))

        result = create_comparison_png(str(fits_file))

        assert result is not None
        assert result.endswith("_comparison.png")
        assert os.path.exists(result)

        # Check file size is reasonable
        size = os.path.getsize(result)
        assert size > 10000  # At least 10KB for a valid PNG

    def test_create_comparison_with_sigma(self, tmp_path):
        """Test comparison PNG creation with sigma normalization."""
        fits_file = tmp_path / "output.fits"
        sigma_file = tmp_path / "sigma.fits"
        self._create_mock_fits(str(fits_file))
        self._create_sigma_file(str(sigma_file))

        result = create_comparison_png(str(fits_file), sigma_file=str(sigma_file))

        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_with_mask(self, tmp_path):
        """Test comparison PNG creation with mask."""
        fits_file = tmp_path / "output.fits"
        mask_file = tmp_path / "mask.fits"
        self._create_mock_fits(str(fits_file))
        self._create_mask_file(str(mask_file))

        result = create_comparison_png(str(fits_file), mask_file=str(mask_file))

        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_with_all(self, tmp_path):
        """Test comparison PNG creation with both sigma and mask."""
        fits_file = tmp_path / "output.fits"
        sigma_file = tmp_path / "sigma.fits"
        mask_file = tmp_path / "mask.fits"
        self._create_mock_fits(str(fits_file))
        self._create_sigma_file(str(sigma_file))
        self._create_mask_file(str(mask_file))

        result = create_comparison_png(
            str(fits_file),
            sigma_file=str(sigma_file),
            mask_file=str(mask_file)
        )

        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_mask_cropping(self, tmp_path):
        """Test that mask is correctly cropped to match FITS dimensions."""
        # Create smaller FITS output
        fits_file = tmp_path / "output.fits"
        small_shape = (50, 50)
        self._create_mock_fits(str(fits_file), shape=small_shape)

        # Create larger mask (simulating full frame mask)
        mask_file = tmp_path / "mask.fits"
        large_shape = (100, 100)
        self._create_mask_file(str(mask_file), shape=large_shape)

        result = create_comparison_png(str(fits_file), mask_file=str(mask_file))

        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_invalid_fits(self, tmp_path):
        """Test with invalid FITS file."""
        invalid_file = tmp_path / "invalid.fits"
        invalid_file.write_text("not a fits file")

        result = create_comparison_png(str(invalid_file))

        # Should return None for invalid input
        assert result is None

    def test_create_comparison_missing_sigma_mask(self, tmp_path):
        """Test with non-existent sigma and mask files."""
        fits_file = tmp_path / "output.fits"
        self._create_mock_fits(str(fits_file))

        result = create_comparison_png(
            str(fits_file),
            sigma_file=str(tmp_path / "nonexistent_sigma.fits"),
            mask_file=str(tmp_path / "nonexistent_mask.fits")
        )

        # Should still work, just skip sigma/mask
        assert result is not None
        assert os.path.exists(result)

    def test_create_comparison_different_shapes(self, tmp_path):
        """Test with sigma/mask of different shapes than FITS data."""
        fits_file = tmp_path / "output.fits"
        sigma_file = tmp_path / "sigma.fits"
        mask_file = tmp_path / "mask.fits"

        self._create_mock_fits(str(fits_file), shape=(80, 80))
        self._create_sigma_file(str(sigma_file), shape=(100, 100))
        self._create_mask_file(str(mask_file), shape=(120, 120))

        result = create_comparison_png(
            str(fits_file),
            sigma_file=str(sigma_file),
            mask_file=str(mask_file)
        )

        assert result is not None
        assert os.path.exists(result)


class TestIntegration:
    """Integration tests for run_galfit workflow."""

    @pytest.mark.slow
    def test_full_workflow_with_real_galfit(self):
        """Test full workflow with real GALFIT execution.

        This test requires:
        - GALFIT to be installed
        - Test data at /home/wnk/code/galfit_example/goodsn_9076/
        """
        pytest.skip("Skip integration test - requires GALFIT and test data")

        # This would be implemented for actual integration testing
        # when GALFIT and test data are available
