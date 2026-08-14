from eval.reward_for_rl_v12 import check_fitted_structure, compute_rl_reward_v12


def _spec(*components):
    return {"components": list(components)}


def _component(model, x, y, mag, re, q=0.8, pa=10, n=2):
    return {"model": model, "x": x, "y": y, "mag": mag,
            "re": re, "q": q, "pa": pa, "n": n}


def test_plausible_bulge_disk_structure_passes():
    spec = _spec({"role": "bulge", "model": "sersic"},
                 {"role": "disk", "model": "expdisk"})
    fitted = [_component("sersic", 128, 128, 16, 3),
              _component("expdisk", 128.5, 127.5, 15, 12)]
    ok, violations, _ = check_fitted_structure(spec, fitted)
    assert ok
    assert violations == []


def test_main_offset_and_bad_size_hierarchy_are_rejected():
    spec = _spec({"role": "bulge", "model": "sersic"},
                 {"role": "disk", "model": "expdisk"})
    fitted = [_component("sersic", 128, 128, 16, 40),
              _component("expdisk", 140, 128, 15, 8)]
    ok, violations, _ = check_fitted_structure(spec, fitted)
    assert not ok
    assert any(v.startswith("main_center_offset:") for v in violations)
    assert any(v.startswith("implausible_size_hierarchy:") for v in violations)


def test_negligible_flux_component_is_rejected():
    spec = _spec({"role": "bulge", "model": "sersic"},
                 {"role": "companion", "model": "sersic"})
    fitted = [_component("sersic", 128, 128, 15, 3),
              _component("sersic", 180, 180, 20.1, 3)]
    ok, violations, _ = check_fitted_structure(spec, fitted)
    assert not ok
    assert any(v.startswith("negligible_flux_component:") for v in violations)


def test_different_roles_are_not_called_duplicates():
    spec = _spec({"role": "bulge", "model": "sersic"},
                 {"role": "companion", "model": "sersic"})
    fitted = [_component("sersic", 128, 128, 15, 3),
              _component("sersic", 128.2, 128.2, 16, 3.1)]
    ok, violations, _ = check_fitted_structure(spec, fitted)
    assert ok
    assert not any(v.startswith("near_duplicate_components:") for v in violations)


def test_structure_veto_cannot_raise_negative_v11_reward():
    spec = _spec({"role": "bulge", "model": "sersic"},
                 {"role": "disk", "model": "expdisk"})
    fitted = [_component("sersic", 128, 128, 15, 50),
              _component("expdisk", 128, 128, 15, 5)]
    result = compute_rl_reward_v12(
        old_metrics={"chi2_nu": 1.0, "bic": 100},
        new_metrics={"chi2_nu": 2.0, "bic": 200},
        action_spec=spec,
        fitted_components=fitted,
    )
    assert result["structure_vetoed"]
    assert result["reward"] <= 0
    assert result["reward"] <= result["v11_reward"]
