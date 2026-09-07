"""Tests for src/beam/signature.py (signatures, equivalence, zombies, combo, projection)."""

from beam.signature import (
    apply_primitives_to_inventory,
    canonical_signature,
    combo_identity,
    find_zombies,
    normalize_inventory,
    project_closed_form,
    signature_equivalent,
    strip_zombies,
)


# ------------------------------------------------------------------- fixtures
def inv(*comps):
    return [dict(c) for c in comps]


def comp(name, ctype="sersic", **kw):
    d = {"name": name, "type": ctype, "x": 100.0, "y": 100.0, "mag": 17.0,
         "re": 6.0, "n": 2.0, "ba": 0.8, "pa": 30.0,
         "toggles": {"x": 1, "y": 1, "mag": 1, "re": 1, "n": 1, "ba": 1, "pa": 1}}
    d.update(kw)
    return d


DISK = comp("disk", "expdisk", re=10.0, ba=0.6)   # Rs=10 -> Re_eff=16.8
BULGE = comp("bulge", re=1.5, n=4.0, ba=0.9, mag=18.5)


def test_normalize_expdisk_effective_re():
    norm = normalize_inventory([DISK])[0]
    assert abs(norm["re_effective"] - 16.8) < 1e-9
    assert norm["re"] == 10.0


def test_canonical_signature_ngc1097(test_data_dir):
    from beam.cons_decode import number_components

    comps = number_components(str(test_data_dir / "NGC1097.feedme"))
    sig = canonical_signature(list(comps.values()))
    names = [s["name"] for s in sig["structures"]]
    assert names == sorted(["bulge", "disk", "bar", "agn"])
    by_name = {s["name"]: s for s in sig["structures"]}
    assert by_name["disk"]["type"] == "expdisk"
    # NGC1097 disk Rs=70.8 -> effective 119.0
    assert abs(by_name["disk"]["re_px"] - 119.0) < 0.1


# ------------------------------------------------------------------ equivalence
def test_equivalence_tolerance_bands():
    base = canonical_signature(inv(DISK, BULGE))

    ok = inv(comp("disk", "expdisk", re=10.0, ba=0.6), comp("bulge", re=1.5, n=4.0, ba=0.9, mag=18.5))
    assert signature_equivalent(base, canonical_signature(ok))

    # Re ±20% band: +15% equivalent, +25% not
    sig15 = canonical_signature(inv(comp("disk", "expdisk", re=11.5, ba=0.6), BULGE))
    sig25 = canonical_signature(inv(comp("disk", "expdisk", re=12.5, ba=0.6), BULGE))
    assert signature_equivalent(base, sig15)
    assert not signature_equivalent(base, sig25)

    # q ±0.1
    q_ok = canonical_signature(inv(comp("disk", "expdisk", re=10.0, ba=0.65), BULGE))
    q_bad = canonical_signature(inv(comp("disk", "expdisk", re=10.0, ba=0.75), BULGE))
    assert signature_equivalent(base, q_ok)
    assert not signature_equivalent(base, q_bad)

    # PA ±10 deg, wrap-aware
    pa_ok = canonical_signature(inv(comp("disk", "expdisk", re=10.0, ba=0.6, pa=38.0), BULGE))
    pa_bad = canonical_signature(inv(comp("disk", "expdisk", re=10.0, ba=0.6, pa=45.0), BULGE))
    assert signature_equivalent(base, pa_ok)
    assert not signature_equivalent(base, pa_bad)
    wrap = canonical_signature(inv(comp("disk", "expdisk", re=10.0, ba=0.6, pa=355.0), BULGE))
    base_5 = canonical_signature(inv(comp("disk", "expdisk", re=10.0, ba=0.6, pa=5.0), BULGE))
    assert signature_equivalent(base_5, wrap)  # 10 deg across the wrap

    # mag ±0.5
    m_ok = canonical_signature(inv(DISK, comp("bulge", re=1.5, n=4.0, ba=0.9, mag=18.9)))
    m_bad = canonical_signature(inv(DISK, comp("bulge", re=1.5, n=4.0, ba=0.9, mag=19.2)))
    assert signature_equivalent(base, m_ok)
    assert not signature_equivalent(base, m_bad)

    # n ±0.5
    n_ok = canonical_signature(inv(DISK, comp("bulge", re=1.5, n=4.3, ba=0.9, mag=18.5)))
    n_bad = canonical_signature(inv(DISK, comp("bulge", re=1.5, n=4.8, ba=0.9, mag=18.5)))
    assert signature_equivalent(base, n_ok)
    assert not signature_equivalent(base, n_bad)


def test_equivalence_name_swap_bulge_bar():
    a = canonical_signature(inv(comp("disk", "expdisk", re=10.0),
                                comp("bulge", re=3.0, n=0.5, ba=0.35,
                                     toggles={"n": 0})))
    b = canonical_signature(inv(comp("disk", "expdisk", re=10.0),
                                comp("bar", re=3.0, n=0.5, ba=0.35,
                                     toggles={"n": 0})))
    assert signature_equivalent(a, b)


def test_equivalence_requires_same_count_and_type():
    base = canonical_signature(inv(DISK, BULGE))
    extra = canonical_signature(inv(DISK, BULGE, comp("bar", re=3.0)))
    assert not signature_equivalent(base, extra)
    wrong_type = canonical_signature(inv(comp("disk", "sersic", re=10.0), BULGE))
    assert not signature_equivalent(base, wrong_type)


def test_equivalence_toggles_and_bands():
    base = canonical_signature(inv(DISK, BULGE))
    toggled = canonical_signature(
        inv(comp("disk", "expdisk", re=10.0, ba=0.6, toggles={"ba": 0}), BULGE))
    assert not signature_equivalent(base, toggled)
    assert signature_equivalent(base, toggled, ignore_toggles=True)

    banded = canonical_signature(inv(DISK, BULGE), cons_bands={("bulge", "n"): (0.5, 8.0)})
    assert not signature_equivalent(base, banded)
    assert signature_equivalent(base, banded, ignore_bands=True)


# ---------------------------------------------------------------------- zombies
def test_find_zombies_relative_criterion():
    comps = inv(comp("disk", "expdisk", re=10.0, mag=16.0),
                comp("bulge", re=1.5, mag=18.0),
                comp("companion", re=1.0, mag=23.0))  # 7 mag fainter than disk
    assert find_zombies(comps) == ["companion"]
    comps2 = inv(comp("disk", "expdisk", re=10.0, mag=16.0),
                 comp("bulge", re=1.5, mag=21.0))  # 5 mag: not zombie
    assert find_zombies(comps2) == []


def test_zombie_equivalence():
    a = canonical_signature(inv(DISK, BULGE))
    b = canonical_signature(inv(DISK, BULGE, comp("companion", re=1.0, mag=24.0)))
    zombies = ["companion"]
    assert signature_equivalent(a, strip_zombies(b, zombies))


# ---------------------------------------------------------------- combo identity
def test_combo_identity_naming_swap_and_companions():
    c1 = combo_identity(inv(comp("disk", "expdisk", re=10.0),
                            comp("bulge", re=3.0, n=0.5, toggles={"n": 0}),
                            comp("companion", re=1.0)))
    c2 = combo_identity(inv(comp("disk", "expdisk", re=10.0),
                            comp("bar", re=5.0, n=0.5),
                            comp("companion2", re=2.0)))
    assert c1 == c2 == "bar+companion*+disk"
    # a genuinely free-n bulge is a different combination
    c3 = combo_identity(inv(comp("disk", "expdisk", re=10.0),
                            comp("bulge", re=3.0, n=4.0, toggles={"n": 1})))
    assert c3 != c1


# ------------------------------------------------------------------ projection
def test_projection_remove_only():
    parent = inv(DISK, BULGE, comp("bar", re=3.0))
    proj = project_closed_form(parent, [{"op": "remove", "target": "bar"}])
    assert proj is not None and proj.kind == "remove-only"
    assert sorted(c["name"] for c in proj.inventory) == ["bulge", "disk"]


def test_projection_black_box():
    parent = inv(DISK, BULGE)
    assert project_closed_form(parent, [{"op": "add", "structure_name": "bar"}]) is None
    assert project_closed_form(
        parent, [{"op": "tune", "structure_name": "bulge", "param": "n", "value": 2.0}]
    ) is None  # no history: not provably a revert


def test_projection_param_revert_and_bound_restore():
    parent = inv(DISK, BULGE)
    hist = {("bulge", "n"): [(4.0, 1)]}
    proj = project_closed_form(
        parent, [{"op": "tune", "structure_name": "bulge", "param": "n", "value": 4.0}],
        param_history=hist,
    )
    assert proj is not None and proj.kind == "param-revert"

    from beam.cons_decode import BoundContext

    tune = {"op": "tune", "structure_name": "disk", "param": None, "value": None,
            "cons_bounds": {"re": (2.0, 148.5), "n": None, "q": None}}
    ctx = BoundContext(psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    proj2 = project_closed_form(parent, [tune], bound_ctx=ctx)
    assert proj2 is not None and proj2.kind == "bound-restore"


# --------------------------------------------------- hypothetical application
def test_apply_primitives_add_tune_convert():
    parent = inv(comp("singlesersic", "sersic", re=12.0, n=2.5, mag=14.0))

    # mandatory bundled conversion when adding the 2nd central component
    prims = [
        {"op": "convert", "convert": {"from_name": "singlesersic", "to_name": "disk",
                                       "to_type": "expdisk"}},
        {"op": "add", "structure_name": "bulge", "component_type": "sersic",
         "re_px": 1.5, "n": 4.0, "q": 0.9, "pa_deg": 30.0, "mag": 16.5,
         "toggles": {"n": 0}},
    ]
    inv2, err = apply_primitives_to_inventory(parent, prims)
    assert err == "" and inv2 is not None
    by_name = {c["name"]: c for c in inv2}
    assert by_name["disk"]["type"] == "expdisk"
    assert abs(by_name["disk"]["re_effective"] - 12.0) < 1e-9
    assert abs(by_name["disk"]["re"] - 12.0 / 1.68) < 1e-9
    assert "n" not in by_name["disk"]
    assert by_name["bulge"]["re_effective"] == 1.5

    # tune: VLM Re on an expdisk is the effective radius
    inv3, _ = apply_primitives_to_inventory(
        inv2, [{"op": "tune", "structure_name": "disk", "param": "re_px", "value": 20.0}])
    by_name3 = {c["name"]: c for c in inv3}
    assert by_name3["disk"]["re_effective"] == 20.0
    assert abs(by_name3["disk"]["re"] - 20.0 / 1.68) < 1e-9


def test_apply_primitives_errors():
    parent = inv(DISK, BULGE)
    out, err = apply_primitives_to_inventory(
        parent, [{"op": "remove", "target": "lens"}])
    assert out is None and "not found" in err
    out, err = apply_primitives_to_inventory(
        parent, [{"op": "add", "structure_name": "disk", "component_type": "expdisk"}])
    assert out is None and "duplicate" in err
    out, err = apply_primitives_to_inventory(parent, [{"op": "frobnicate"}])
    assert out is None and "unknown op" in err
