"""Unit tests for parse_feedme module using NGC1097 real data."""

import os
from pathlib import Path

import pytest

from tools.parse_feedme import parse_feedme, parse_components


@pytest.fixture
def ngc1097_feedme(test_data_dir):
    return str(test_data_dir / "NGC1097.feedme")


@pytest.fixture
def ngc1097_galfit_params(test_data_dir):
    return str(test_data_dir / "NGC1097_galfit.07")


class TestParseFeedme:
    """Tests for parse_feedme()."""

    def test_parses_input_path(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["input"].endswith("NGC1097.phot.1_nonan.fits")
        assert os.path.isabs(result["input"])

    def test_parses_output_path(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["output"].endswith("NGC1097_galfit.fits")

    def test_parses_sigma_path(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["sigma"].endswith("NGC1097_sigma2014.fits")

    def test_parses_psf_path(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["psf"].endswith("PSF-1.composite.fits")

    def test_parses_mask_path(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["mask"].endswith("NGC1097.1.finmask_nonan.fits")

    def test_parses_constraint_none(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["constraint"] == ""

    def test_parses_fit_region(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        assert result["fit_region"] == (37, 1467, 25, 1455)

    def test_resolves_relative_paths_to_absolute(self, ngc1097_feedme):
        result = parse_feedme(ngc1097_feedme)
        feedme_dir = os.path.dirname(os.path.abspath(ngc1097_feedme))
        assert result["input"].startswith(feedme_dir)


class TestParseComponents:
    """Tests for parse_components()."""

    def test_parses_all_four_components(self, ngc1097_galfit_params):
        comps = parse_components(ngc1097_galfit_params)
        # NGC1097 has: sersic (bulge), expdisk (disk), ferrer (bar), psf (AGN)
        assert len(comps) == 4

    def test_bulge_component(self, ngc1097_galfit_params):
        comps = parse_components(ngc1097_galfit_params)
        bulge = comps[0]
        assert bulge["type"] == "sersic"
        assert abs(bulge["x"] - 752.70) < 0.1
        assert abs(bulge["y"] - 740.25) < 0.1
        assert abs(bulge["n"] - 0.17) < 0.01
        assert abs(bulge["ba"] - 0.91) < 0.01

    def test_disk_component(self, ngc1097_galfit_params):
        comps = parse_components(ngc1097_galfit_params)
        disk = comps[1]
        assert disk["type"] == "expdisk"
        assert abs(disk["x"] - 763.68) < 0.1
        assert abs(disk["y"] - 753.41) < 0.1
        assert abs(disk["re"] - 133.45) < 0.5
        assert abs(disk["ba"] - 0.72) < 0.01

    def test_bar_component(self, ngc1097_galfit_params):
        comps = parse_components(ngc1097_galfit_params)
        bar = comps[2]
        assert bar["type"] == "ferrer"
        assert abs(bar["ba"] - 0.37) < 0.01

    def test_psf_component(self, ngc1097_galfit_params):
        comps = parse_components(ngc1097_galfit_params)
        agn = comps[3]
        assert agn["type"] == "psf"
        assert abs(agn["ba"] - 1.0) < 0.01

    def test_feedme_file_same_components(self, ngc1097_feedme):
        """Feedme (input params) and galfit.07 (output params) have same structure."""
        comps_in = parse_components(ngc1097_feedme)
        assert len(comps_in) == 4
        assert comps_in[0]["type"] == "sersic"
        assert comps_in[1]["type"] == "expdisk"
        assert comps_in[2]["type"] == "ferrer"
        assert comps_in[3]["type"] == "psf"
