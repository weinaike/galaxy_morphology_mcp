from eval.reward_for_rl_v12_2 import compute_rl_reward_v12_2


def _component(model, x=128, y=128, mag=16, re=3, q=0.8, pa=10, n=2):
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
    return compute_rl_reward_v12_2(
        old_metrics={"chi2_nu": 1.0, "bic": 200},
        new_metrics={"chi2_nu": 0.8, "bic": 100},
        action_spec=spec,
        fitted_components=fitted,
    )


def test_normal_bulge_disk_is_not_vetoed():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    fitted = [
        _component("sersic", re=3, q=0.7, n=2),
        _component("expdisk", re=10, q=0.3),
    ]
    result = _reward(spec, fitted)
    assert not result["structure_vetoed"]
    assert result["reward"] == result["v11_reward"]


def test_extremely_flat_bulge_is_vetoed():
    spec = {"components": [{"role": "bulge", "model": "sersic"}]}
    result = _reward(spec, [_component("sersic", q=0.17)])
    assert result["structure_vetoed"]
    assert any(v.startswith("extreme_role_axis_ratio:") for v in result["structure_violations"])


def test_extremely_flat_bar_is_vetoed_but_edge_on_disk_is_not():
    bar_spec = {"components": [{"role": "bar", "model": "sersic"}]}
    assert _reward(bar_spec, [_component("sersic", q=0.07, n=0.5)])["structure_vetoed"]

    disk_spec = {"components": [{"role": "disk", "model": "expdisk"}]}
    assert not _reward(disk_spec, [_component("expdisk", q=0.07)])["structure_vetoed"]


def test_only_extreme_size_inversion_is_vetoed():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    extreme = [
        _component("sersic", re=72),
        _component("expdisk", re=4.3),
    ]
    moderate = [
        _component("sersic", re=20),
        _component("expdisk", re=8),
    ]
    assert _reward(spec, extreme)["structure_vetoed"]
    assert not _reward(spec, moderate)["structure_vetoed"]


def test_three_disk_like_roles_are_vetoed():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "bar", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    fitted = [
        _component("sersic", n=0.44),
        _component("sersic", n=0.5),
        _component("expdisk", re=15),
    ]
    result = _reward(spec, fitted)
    assert result["structure_vetoed"]
    assert any(
        v.startswith("disk_like_component_degeneracy:")
        for v in result["structure_violations"]
    )


def test_two_component_pseudobulge_disk_is_not_vetoed():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    fitted = [
        _component("sersic", n=0.5, q=0.7),
        _component("expdisk", re=15),
    ]
    assert not _reward(spec, fitted)["structure_vetoed"]
