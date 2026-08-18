"""Tests for rule-facing facts derived from primitive numeric evidence."""

import numpy as np
import pytest

from component_analysis import (
    BandArrays,
    derive_fit_features,
    measure_radial_residual_systematic,
    merge_wcs_candidate_regions,
    summarize_isophote_profile,
)


class LinearWCS:
    """Small zero-based linear WCS used without astropy in unit tests."""

    def __init__(self, *, x_offset=0.0, y_offset=0.0):
        self.x_offset = x_offset
        self.y_offset = y_offset

    def all_pix2world(self, x, y, origin):
        assert origin == 0
        return 10.0 + (x + self.x_offset) / 3600.0, 20.0 + (
            y + self.y_offset
        ) / 3600.0


def _numeric_with_peaks():
    source = {"file": None, "hdu": 0, "frame": "pixel", "region": "fit_region"}
    return {
        "schema_version": "1.0",
        "round_id": "r1",
        "manifest_ref": "manifest.json",
        "features": [
            {
                "feature_id": "f200w_peaks",
                "name": "residual_local_peaks",
                "status": "AVAILABLE",
                "value": 1,
                "source": {"band": "f200w", **source},
                "quality_flags": [],
                "candidate_regions": [
                    {
                        "region_id": "f200w:candidate_1",
                        "band": "f200w",
                        "x_pix": 10.0,
                        "y_pix": 12.0,
                        "radius_pix": 2.0,
                        "ra_deg": None,
                        "dec_deg": None,
                        "local_snr": 12.0,
                        "detected_in_bands": ["f200w"],
                    }
                ],
            },
            {
                "feature_id": "f444w_peaks",
                "name": "residual_local_peaks",
                "status": "AVAILABLE",
                "value": 1,
                "source": {"band": "f444w", **source},
                "quality_flags": [],
                "candidate_regions": [
                    {
                        "region_id": "f444w:candidate_1",
                        "band": "f444w",
                        "x_pix": 20.0,
                        "y_pix": 7.0,
                        "radius_pix": 2.0,
                        "ra_deg": None,
                        "dec_deg": None,
                        "local_snr": 9.0,
                        "detected_in_bands": ["f444w"],
                    }
                ],
            },
        ],
        "band_quality": [
            {"band": "f200w", "passed": True},
            {"band": "f444w", "passed": True},
        ],
    }


def test_radial_residual_requires_consecutive_significant_annuli():
    shape = (61, 61)
    yy, xx = np.indices(shape, dtype=float)
    radius = np.hypot(xx - 30.0, yy - 30.0)
    residual = np.where((radius >= 8.0) & (radius <= 25.0), 2.0, 0.0)
    result = measure_radial_residual_systematic(
        residual,
        np.ones(shape),
        center=(30.0, 30.0),
        inner_radius=8.0,
        outer_radius=25.0,
        bins=5,
    )
    assert result["status"] == "AVAILABLE"
    assert result["value"]["systematic"] is True
    assert result["value"]["sign"] == "positive"
    assert result["value"]["max_consecutive"] == 5


def test_isophote_summary_handles_pa_wrap_and_bar_profile():
    table = {
        "sma_pix": np.arange(1.0, 11.0),
        "eps": np.array([0.1, 0.15, 0.2, 0.3, 0.4, 0.45, 0.42, 0.4, 0.39, 0.38]),
        "pa_deg": np.array([170.0, 175.0, 178.0, 179.0, 1.0, 2.0, 1.0, 0.0, 2.0, 1.0]),
        "x0_pix": np.full(10, 30.0),
        "y0_pix": np.full(10, 30.0),
    }
    bar_result = {
        "bar_detected": True,
        "peak_idx": 5,
        "outer_idx": 8,
        "e_max": 0.45,
        "peak_sma_pix": 6.0,
        "bar_pa_var": 4.0,
        "bar_pa_mean": 1.0,
    }
    result = summarize_isophote_profile(table, psf_fwhm=2.0, bar_result=bar_result)
    assert result["status"] == "AVAILABLE"
    assert result["outer_geometry"]["pa_scatter_deg"] < 2.0
    assert result["outer_geometry"]["q_range"] == pytest.approx(0.04)
    assert result["source_extent_psf_ratio"] == pytest.approx(5.0)
    assert result["bar_profile"]["scale_psf_ratio"] == pytest.approx(3.0)
    assert result["bar_profile"]["outer_ellipticity_drop"] == pytest.approx(0.06)


def test_wcs_candidates_merge_to_one_stable_target_id():
    shape = (32, 32)
    zeros = np.zeros(shape)
    bands = [
        BandArrays(
            "f200w",
            zeros,
            zeros,
            np.ones(shape),
            None,
            wcs=LinearWCS(),
        ),
        BandArrays(
            "f444w",
            zeros,
            zeros,
            np.ones(shape),
            None,
            wcs=LinearWCS(x_offset=-10.0, y_offset=5.0),
        ),
    ]
    evidence = merge_wcs_candidate_regions(
        _numeric_with_peaks(),
        bands,
        match_radius_arcsec=0.05,
    )
    regions = [
        region
        for feature in evidence["features"]
        for region in feature.get("candidate_regions", [])
    ]
    assert {region["region_id"] for region in regions} == {"candidate_1"}
    assert all(region["detected_in_bands"] == ["f200w", "f444w"] for region in regions)
    assert all(region["ra_deg"] is not None for region in regions)


def test_fit_features_report_single_sersic_and_bar_disk_relation():
    single = derive_fit_features(
        [{"name": "galaxy", "type": "sersic", "n": 2.1, "n_at_boundary": False}]
    )
    assert single["single_sersic_n"]["value"] == {
        "n": 2.1,
        "at_boundary": False,
    }
    assert single["bar_fit_parameters"]["status"] == "UNAVAILABLE"

    split = derive_fit_features(
        [
            {"name": "disk", "type": "sersic", "re": 20.0, "ba": 0.7},
            {"name": "bar", "type": "sersic", "re": 8.0, "ba": 0.3},
        ]
    )
    assert split["bar_fit_parameters"]["value"] == {
        "re_bar_over_re_disk": 0.4,
        "q_bar": 0.3,
    }
