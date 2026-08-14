from eval.reward_for_rl_v12_1 import compute_rl_reward_v12_1


def _component(model, x, y, mag, re, q=0.8, pa=10, n=2):
    return {
        "model": model,
        "x": x,
        "y": y,
        "mag": mag,
        "re": re,
        "q": q,
        "pa": pa,
        "n": n,
    }


def _reward(spec, fitted):
    return compute_rl_reward_v12_1(
        old_metrics={"chi2_nu": 1.0, "bic": 200},
        new_metrics={"chi2_nu": 0.8, "bic": 100},
        action_spec=spec,
        fitted_components=fitted,
    )


def test_negligible_flux_is_warning_not_veto():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "nucleus", "model": "psf"},
    ]}
    fitted = [
        _component("sersic", 128, 128, 15, 3),
        _component("psf", 128, 128, 22, 1),
    ]
    result = _reward(spec, fitted)
    assert not result["structure_vetoed"]
    assert any(v.startswith("negligible_flux_component:") for v in result["structure_warnings"])
    assert result["reward"] == result["v11_reward"]


def test_size_hierarchy_is_warning_not_veto():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    fitted = [
        _component("sersic", 128, 128, 16, 40),
        _component("expdisk", 128, 128, 15, 8),
    ]
    result = _reward(spec, fitted)
    assert not result["structure_vetoed"]
    assert any(v.startswith("implausible_size_hierarchy:") for v in result["structure_warnings"])
    assert result["reward"] == result["v11_reward"]


def test_main_center_offset_remains_hard_veto():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    fitted = [
        _component("sersic", 128, 128, 16, 3),
        _component("expdisk", 140, 128, 15, 12),
    ]
    result = _reward(spec, fitted)
    assert result["structure_vetoed"]
    assert any(v.startswith("main_center_offset:") for v in result["structure_violations"])
    assert result["reward"] <= 0


def test_same_role_duplicate_remains_hard_veto():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "bulge", "model": "sersic"},
    ]}
    fitted = [
        _component("sersic", 128, 128, 15, 3, q=0.8, pa=10),
        _component("sersic", 128.2, 128.1, 16, 3.1, q=0.82, pa=12),
    ]
    result = _reward(spec, fitted)
    assert result["structure_vetoed"]
    assert any(v.startswith("near_duplicate_components:") for v in result["structure_violations"])
    assert result["reward"] <= 0