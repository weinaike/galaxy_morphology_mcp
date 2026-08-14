from eval.reward_for_rl_v12_4 import compute_rl_reward_v12_4


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
    return compute_rl_reward_v12_4(
        old_metrics={"chi2_nu": 1.0, "bic": 200},
        new_metrics={"chi2_nu": 0.8, "bic": 100},
        action_spec=spec,
        fitted_components=fitted,
    )


def test_tighter_bulge_axis_threshold():
    spec = {"components": [{"role": "bulge", "model": "sersic"}]}
    assert _reward(spec, [_component("sersic", q=0.17)])["structure_vetoed"]
    assert not _reward(spec, [_component("sersic", q=0.18)])["structure_vetoed"]


def test_tighter_bar_axis_threshold():
    spec = {"components": [{"role": "bar", "model": "sersic"}]}
    assert _reward(spec, [_component("sersic", q=0.07, n=0.5)])["structure_vetoed"]
    assert not _reward(spec, [_component("sersic", q=0.08, n=0.5)])["structure_vetoed"]


def test_tighter_size_threshold():
    spec = {"components": [
        {"role": "bulge", "model": "sersic"},
        {"role": "disk", "model": "expdisk"},
    ]}
    assert _reward(spec, [
        _component("sersic", re=72),
        _component("expdisk", re=4.3),
    ])["structure_vetoed"]
    assert not _reward(spec, [
        _component("sersic", re=40),
        _component("expdisk", re=4.3),
    ])["structure_vetoed"]
