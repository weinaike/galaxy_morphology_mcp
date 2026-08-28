import numpy as np
import pytest

from eval.residual_noise_likeness import (
    RegionConfig,
    compute_noise_likeness_badness,
    compute_noise_likeness_deltas,
    fixed_regions,
)


def _inputs(seed=7, shape=(128, 128)):
    rng = np.random.default_rng(seed)
    sigma = np.ones(shape)
    mask = np.zeros(shape, dtype=np.uint8)
    return rng.normal(size=shape), sigma, mask


def test_fixed_regions_do_not_depend_on_candidate_residual():
    roi1, sky1 = fixed_regions((128, 128))
    roi2, sky2 = fixed_regions((128, 128))
    assert np.array_equal(roi1, roi2)
    assert np.array_equal(sky1, sky2)
    assert not np.any(roi1 & sky1)


def test_structured_central_residual_has_acf_and_blob_badness():
    residual, sigma, mask = _inputs()
    yy, xx = np.indices(residual.shape)
    structured = residual.copy()
    structured[(xx - 64) ** 2 + (yy - 64) ** 2 <= 8**2] += 6.0
    features = compute_noise_likeness_badness(structured, sigma, mask)
    assert features["sky_acf_excess"] > 0
    assert features["sky_blob_area_excess"] > 0
    assert features["sky_blob_significance_excess"] > 0


def test_removing_structure_produces_positive_improvement_delta():
    child, sigma, mask = _inputs()
    parent = child.copy()
    parent[55:73, 55:73] += 6.0
    parent_features = compute_noise_likeness_badness(parent, sigma, mask)
    child_features = compute_noise_likeness_badness(child, sigma, mask)
    delta = compute_noise_likeness_deltas(parent_features, child_features)
    assert delta["sky_acf_excess"] > 0
    assert delta["sky_blob_area_excess"] > 0
    assert delta["sky_blob_significance_excess"] > 0


def test_invalid_region_configuration_is_rejected():
    with pytest.raises(ValueError, match="ROI"):
        fixed_regions(
            (128, 128),
            RegionConfig(roi_radius_fraction=0.40, sky_inner_fraction=0.36),
        )
