from eval.reward_for_rl_v12_3 import compute_rl_reward_v12_3


def _component(model, re=3, q=0.8, n=2):
    return {
        "model": model,
        "x": 128,
        "y": 128,
        "mag": 16,
        "re": re,
        "q": q,
        "pa": 10,
        "n": n,
    }


def _reward(spec, fitted):
    return compute_rl_reward_v12_3(
        old_metrics={"chi2_nu": 1.0, "bic": 200},
        new_metrics={"chi2_nu": 0.8, "bic": 100},
        action_spec=spec,
        fitted_components=fitted,
    )


def test_axis_is_hard_veto():
    spec = {"components": [{"role": "bulge", "model": "sersic"}]}
    assert _reward(spec, [_component("sersic", q=0.17)])["structure_vetoed"]


def test_size_is_hard_veto():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    fitted = [_component("sersic", re=72), _component("expdisk", re=4.3)]
    assert _reward(spec, fitted)["structure_vetoed"]


def test_degeneracy_is_warning_only():
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
    assert not result["structure_vetoed"]
    assert result["reward"] == result["v11_reward"]
    assert any(
        finding.startswith("disk_like_component_degeneracy:")
        for finding in result["structure_warnings"]
    )
