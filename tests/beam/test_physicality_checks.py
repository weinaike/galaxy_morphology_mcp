"""Tests for the expert-round-2 physicality checks in src/beam/physicality.py.

compute_mech_checks(graph, state) needs only graph.g.graph["meta"] and
state["inventory"] (+ state["artifacts"] for the .cons-dependent lens flat
check) — a tiny stub graph suffices.
"""

from __future__ import annotations

import os

from beam.physicality import compute_mech_checks


class _G:
    def __init__(self, meta=None):
        class _Inner:
            pass

        self.g = _Inner()
        self.g.graph = {"meta": meta or {}}


def _comp(name, ctype="sersic", **kw):
    d = {"number": None, "name": name, "type": ctype, "x": 128.0, "y": 128.0,
         "mag": 17.0, "re": 5.0, "n": None, "q": 0.7, "pa": 0.0, "toggles": {}}
    d.update(kw)
    if d.get("re") is not None:
        d["re_effective"] = d["re"] * (1.68 if ctype == "expdisk" else 1.0)
    return d


def _checks(comps, meta=None, state_extra=None):
    st = {"inventory": comps, "artifacts": {}}
    st.update(state_extra or {})
    return {(c["check"], c["severity"]): c["detail"] for c in compute_mech_checks(_G(meta), st)}


def _has(checks, check, severity="hard", substr=""):
    for (ck, sev), detail in checks.items():
        if ck == check and sev == severity and substr in detail:
            return True
    return False


# ------------------------------------------------------------------- onion
def test_onion_nested_ok():
    comps = [
        _comp("disk", "expdisk", re=12.0, q=0.8, pa=10.0, mag=16.0),
        _comp("lens", q=0.8, pa=10.0, re=8.0, n=0.3, mag=17.0),
        _comp("bar", q=0.35, pa=40.0, re=4.0, n=0.5, mag=17.5),
        _comp("bulge", q=0.8, pa=0.0, re=1.5, n=2.0, mag=18.0),
    ]
    checks = _checks(comps)
    assert not any(ck == "onion" for ck, _ in checks)


def test_onion_crossing_detected():
    # lens nearly as large as the disk with a different PA -> ellipse crossing
    comps = [
        _comp("disk", "expdisk", re=6.0, q=0.85, pa=0.0, mag=16.0),
        _comp("lens", q=0.6, pa=90.0, re=9.5, n=0.3, mag=17.0),
        _comp("bar", q=0.35, pa=40.0, re=4.0, n=0.5, mag=17.5),
        _comp("bulge", q=0.8, re=1.5, n=2.0, mag=18.0),
    ]
    checks = _checks(comps)
    assert _has(checks, "onion", "hard", "lens")


def test_onion_skipped_for_oblique_disk():
    comps = [
        _comp("disk", "expdisk", re=10.0, q=0.25, pa=0.0, mag=16.0),
        _comp("lens", q=0.6, pa=90.0, re=9.5, n=0.3, mag=17.0),
    ]
    checks = _checks(comps)
    assert not any(ck == "onion" for ck, _ in checks)


def test_onion_bulge_mismatch_exempted():
    # a round bulge laterally wider than a needle bar: the normal configuration
    comps = [
        _comp("disk", "expdisk", re=12.0, q=0.9, pa=0.0, mag=16.0),
        _comp("bar", q=0.10, pa=0.0, re=6.0, n=0.5, mag=17.5),
        _comp("bulge", q=0.85, pa=0.0, re=2.0, n=2.0, mag=18.0),
    ]
    checks = _checks(comps)
    assert not any(ck == "onion" for ck, _ in checks)


def test_onion_bar_in_lens_identity_inversion_kept():
    # bar ROUNDER than lens is itself the pathology — must NOT be exempted
    comps = [
        _comp("disk", "expdisk", re=12.0, q=0.9, pa=0.0, mag=16.0),
        _comp("lens", q=0.27, pa=0.0, re=6.0, n=0.3, mag=17.0),
        _comp("bar", q=0.53, pa=0.0, re=5.5, n=0.5, mag=17.5),
    ]
    checks = _checks(comps)
    assert _has(checks, "onion", "hard", "bar")
    assert _has(checks, "shape_order", "hard", "q_bar")


# ------------------------------------------- area ordering (rule B, always on)
def test_onion_area_inversion_flat_disk():
    # KILOGAS_120 A.8 geometry: flat residual disk (7.7% flux) vs the round
    # lens carrying the main body — the area layer fires even though the
    # directional layer is skipped in the flat-disk regime
    comps = [
        _comp("disk", "expdisk", re=15.62, q=0.2575, pa=-49.7, mag=18.49),
        _comp("lens", q=0.846, pa=-35.6, re=23.84, n=0.36, mag=15.91),
    ]
    checks = _checks(comps)
    assert _has(checks, "onion", "hard", "area")
    assert not _has(checks, "onion", "hard", "pokes")  # directional still skipped


def test_onion_area_ok_nested():
    comps = [
        _comp("disk", "expdisk", re=12.0, q=0.8, pa=10.0, mag=16.0),
        _comp("lens", q=0.8, pa=10.0, re=8.0, n=0.3, mag=17.0),
    ]
    checks = _checks(comps)
    assert not _has(checks, "onion", "hard", "area")


def test_onion_area_bulge_exempt():
    # thick bulge covering more projected area than a thin disk: the normal
    # edge-on configuration — the rounder-bulge exemption covers BOTH layers
    comps = [
        _comp("disk", "expdisk", re=10.0, q=0.25, pa=0.0, mag=16.0),
        _comp("bulge", q=0.9, pa=0.0, re=8.0, n=2.0, mag=17.0),
    ]
    checks = _checks(comps)
    assert not any(ck == "onion" for ck, _ in checks)


# --------------------------------------- anchored flat-disk skip (rule A)
def test_flat_disk_contradicted_by_q_iso_anchor():
    # fitted q=0.26 vs image anchor q_iso=0.79: the skip is lifted, the
    # inconsistency is flagged (note-level since jwst/1071 — q_iso is a
    # coarse anchor, no PASS veto) and the directional layer re-arms
    comps = [
        _comp("disk", "expdisk", re=15.62, q=0.2575, pa=-49.7, mag=18.49),
        _comp("lens", q=0.846, pa=-35.6, re=23.84, n=0.36, mag=15.91),
    ]
    checks = _checks(comps, meta={"q_iso_outer": 0.79})
    assert _has(checks, "disk_shape_inconsistency", "note")
    assert _has(checks, "onion", "hard", "pokes")


def test_flat_disk_corroborated_by_q_iso_anchor():
    # genuinely flat galaxy (anchor q_iso=0.28): skip stands, no flag; the
    # area layer stays armed but the small lens does not fire it
    comps = [
        _comp("disk", "expdisk", re=15.62, q=0.2575, pa=-49.7, mag=16.0),
        _comp("lens", q=0.6, pa=-35.6, re=5.0, n=0.3, mag=18.5),
    ]
    checks = _checks(comps, meta={"q_iso_outer": 0.28})
    assert not _has(checks, "disk_shape_inconsistency")
    assert not _has(checks, "onion", "hard", "pokes")
    assert not _has(checks, "onion", "hard", "area")


def test_flat_disk_corroborated_by_stage1_edge_on_text():
    # explicit Stage-1 edge-on classification corroborates even when the
    # anchor disagrees
    g = _G({"q_iso_outer": 0.79})
    g.g.graph["stage1"] = {"morphology": "edge-on disk with a dust lane"}
    st = {"inventory": [
        _comp("disk", "expdisk", re=15.62, q=0.2575, pa=-49.7, mag=16.0),
        _comp("lens", q=0.6, pa=0.0, re=5.0, n=0.3, mag=18.5),
    ], "artifacts": {}}
    checks = {(c["check"], c["severity"]) for c in compute_mech_checks(g, st)}
    assert all(c["check"] != "disk_shape_inconsistency" for c in compute_mech_checks(g, st))


def test_flat_disk_legacy_graph_without_anchor():
    # graphs initialised before q_iso existed: no anchor -> legacy skip
    comps = [
        _comp("disk", "expdisk", re=15.62, q=0.2575, pa=-49.7, mag=18.49),
        _comp("lens", q=0.846, pa=-35.6, re=23.84, n=0.36, mag=15.91),
    ]
    g = _G({})
    g.g.graph["stage1"] = {"morphology": "disk galaxy, face-on, spiral arms"}
    st = {"inventory": comps, "artifacts": {}}
    raw = compute_mech_checks(g, st)
    # no anchor: no inconsistency flag, directional stays skipped — but the
    # always-on area layer still catches the inversion (rule B backstop)
    assert not any(c["check"] == "disk_shape_inconsistency" for c in raw)
    assert not any("pokes out" in c["detail"] for c in raw if c["check"] == "onion")
    assert any("area exceeds" in c["detail"] for c in raw if c["check"] == "onion")


# ------------------------------------------------------------- q priors
def test_bulge_q_bands():
    # hard below 0.3 (was 0.4), note 0.3-0.4 (was 0.4-0.5)
    assert _has(_checks([_comp("bulge", q=0.25, re=2.0, n=2.0)]), "shape_prior", "hard", "bulge q")
    c35 = _checks([_comp("bulge", q=0.35, re=2.0, n=2.0)])
    assert _has(c35, "prior", "note", "bulge q") and not _has(c35, "shape_prior", "hard", "bulge q")
    for q_clean in (0.45, 0.55):
        c = _checks([_comp("bulge", q=q_clean, re=2.0, n=2.0)])
        assert not _has(c, "shape_prior", "hard", "bulge q")
        assert not _has(c, "prior", "note", "bulge q")


def test_lens_q_hard():
    assert _has(_checks([_comp("disk", "expdisk", re=12.0, mag=16.0),
                         _comp("lens", q=0.45, re=6.0, n=0.3)]),
                "shape_prior", "hard", "lens q")


def test_bar_q_note_band():
    c = _checks([_comp("bar", q=0.55, re=4.0, n=0.5)])
    assert _has(c, "shape_prior", "note", "round-bar") and not _has(c, "axis_ratio", "hard")
    # 0.5 < q <= 0.7 is the round-bar watch band (hard limit relaxed 0.6 -> 0.7)
    c65 = _checks([_comp("bar", q=0.65, re=4.0, n=0.5)])
    assert _has(c65, "shape_prior", "note", "round-bar") and not _has(c65, "axis_ratio", "hard")
    assert _has(_checks([_comp("bar", q=0.75, re=4.0, n=0.5)]), "axis_ratio", "hard")


def test_q_bar_vs_q_lens():
    comps = [_comp("lens", q=0.7, re=6.0, n=0.3), _comp("bar", q=0.75, re=4.0, n=0.5)]
    # bar q=0.75 > 0.7 also hard axis_ratio; shape_order must fire independently
    assert _has(_checks(comps), "shape_order", "hard", "q_bar")
    assert not _has(_checks([_comp("lens", q=0.7, re=6.0, n=0.3),
                             _comp("bar", q=0.4, re=4.0, n=0.5)]), "shape_order", "hard")


# ------------------------------------------------------------- n priors
def test_lens_n_bands():
    disk = _comp("disk", "expdisk", re=12.0, mag=16.0)
    assert _has(_checks([disk, _comp("lens", re=6.0, n=0.7)]), "profile_prior", "hard", "lens n")
    c = _checks([disk, _comp("lens", re=6.0, n=0.55)])
    assert _has(c, "profile_prior", "note") and not _has(c, "profile_prior", "hard", "lens n")
    assert not _has(_checks([disk, _comp("lens", re=6.0, n=0.3)]), "profile_prior", "hard")


def test_outerdisk_n_hard():
    assert _has(_checks([_comp("disk", "expdisk", re=12.0, mag=16.0),
                         _comp("outerdisk", q=0.8, re=25.0, n=1.2)]),
                "profile_prior", "hard", "outerdisk n")


def test_lens_flat_degeneration_removed():
    # the lens flat-degeneration sub-check (n<=0.1 AND Re>=0.9*cap) was removed
    # together with candidate-declared bands — with the default bound set only,
    # self-imposed caps no longer exist to hit
    comps = [_comp("disk", "expdisk", re=12.0, mag=16.0),
             _comp("lens", number=1, re=5.5, n=0.1, q=0.7)]
    out = compute_mech_checks(_G({}), {"inventory": comps, "artifacts": {}})
    assert not any("flat degeneration" in c["detail"] for c in out)


# ------------------------------------------------------------- mu0 order
def test_mu0_bulge_disk_inversion():
    # a faint, low-n bulge is DIMMER at the centre than a compact bright disk
    comps = [
        _comp("disk", "expdisk", re=2.0, mag=14.0, q=0.8),   # compact, bright
        _comp("bulge", q=0.8, re=2.5, n=1.0, mag=20.0),      # faint, extended
    ]
    assert _has(_checks(comps), "mu0_order", "hard", "mu0_bulge")


def test_mu0_bar_tolerance():
    # bar slightly fainter than disk at centre passes; far fainter (over the
    # /0.90 tolerance) fails
    comps_ok = [
        _comp("disk", "expdisk", re=12.0, mag=16.5, q=0.8),
        _comp("bar", q=0.35, re=5.0, n=0.5, mag=16.6),
        _comp("bulge", q=0.8, re=1.5, n=2.0, mag=18.0),
    ]
    assert not _has(_checks(comps_ok), "mu0_order", "hard", "mu0_bar")
    comps_bad = [
        _comp("disk", "expdisk", re=4.0, mag=16.0, q=0.8),
        _comp("bar", q=0.35, re=15.0, n=0.5, mag=18.0),
        _comp("bulge", q=0.8, re=1.0, n=2.0, mag=19.0),
    ]
    assert _has(_checks(comps_bad), "mu0_order", "hard", "mu0_bar")


def test_mu0_agn_proxy():
    comps = [
        _comp("disk", "expdisk", re=12.0, mag=16.0, q=0.8),
        _comp("bulge", q=0.8, re=1.5, n=2.0, mag=16.5),
        _comp("agn", "psf", mag=20.0),                        # very faint AGN
    ]
    checks = _checks(comps, meta={"a_psf_px2": 4.25})
    assert _has(checks, "mu0_order", "hard", "mu0_agn")


# ------------------------------------------------------------- flux share
def test_flux_windows_and_lens_bar_order():
    # bar ~17% share (in window); lens brighter than bar -> hard
    comps = [
        _comp("disk", "expdisk", re=12.0, mag=16.0, q=0.8),
        _comp("lens", q=0.7, re=7.0, n=0.3, mag=16.8),
        _comp("bar", q=0.35, re=4.0, n=0.5, mag=17.3),
        _comp("bulge", q=0.8, re=1.5, n=2.0, mag=18.0),
    ]
    checks = _checks(comps)
    assert _has(checks, "flux_share", "hard", "lens flux fraction")
    assert not _has(checks, "flux_share", "note", "bar flux fraction")


def test_flux_duplicate_companion_names_counted():
    # companion + companion2: the total must include BOTH (dict overwrite bug)
    comps = [
        _comp("disk", "expdisk", re=12.0, mag=16.0, q=0.8),
        _comp("bar", q=0.35, re=4.0, n=0.5, mag=17.0),
        {"name": "companion", "type": "psf", "x": 90, "y": 90, "mag": 17.0,
         "re": None, "re_effective": None, "n": None, "q": None, "pa": None, "toggles": {}},
        {"name": "companion2", "type": "psf", "x": 95, "y": 95, "mag": 17.0,
         "re": None, "re_effective": None, "n": None, "q": None, "pa": None, "toggles": {}},
    ]
    # bar mag 17 + two companions mag 17 + disk 16: f_bar = 1/(2*1+2*1)= 0.25? ->
    # disk L=2.512, others 1 each: tot=5.512, f_bar=0.181 (inside window, no note)
    checks = _checks(comps)
    assert not _has(checks, "flux_share", "note", "bar flux fraction")


# --------------------------------------------- administrative disable switch
def test_disable_mech_checks_env(monkeypatch):
    # NOTE: the _checks dict helper collapses same-(check,severity) entries and
    # this fixture fires TWO mu0_order/hard entries (bulge + bar) — assert on
    # the raw list instead.
    def _raw(comps):
        st = {"inventory": comps, "artifacts": {}}
        return [(c["check"], c["severity"], c["detail"])
                for c in compute_mech_checks(_G({}), st)]

    def _present(raw, check, severity="hard", substr=""):
        return any(ck == check and sev == severity and substr in d
                   for ck, sev, d in raw)

    # inventory that fires mu0_order(hard, twice), flux_share hard + note,
    # re_chain(hard)
    inv = [
        _comp("disk", "expdisk", re=2.0, mag=14.0, q=0.8),   # compact bright disk
        _comp("bulge", q=0.8, re=2.5, n=1.0, mag=20.0),      # mu0_bulge >= mu0_disk
        _comp("lens", q=0.7, re=7.0, n=0.3, mag=16.8),       # re >= disk Re_eff, bright
        _comp("bar", q=0.35, re=4.0, n=0.5, mag=21.0),       # faint: f_bar ~0.15%, fl >= fb
    ]
    monkeypatch.delenv("GALMCP_DISABLE_MECH_CHECKS", raising=False)
    base = _raw(inv)
    assert _present(base, "mu0_order", "hard", "mu0_bulge")
    assert _present(base, "flux_share", "hard", "lens flux fraction")
    assert _present(base, "flux_share", "note", "bar flux fraction")
    assert _present(base, "re_chain", "hard", "re inversion")

    # production setting: whole mu0_order family + flux_share hard severity only
    monkeypatch.setenv("GALMCP_DISABLE_MECH_CHECKS", "mu0_order,flux_share:hard")
    disabled = _raw(inv)
    assert not any(ck == "mu0_order" for ck, _, _ in disabled)
    assert not _present(disabled, "flux_share", "hard")
    assert _present(disabled, "flux_share", "note", "bar flux fraction")  # notes kept
    assert _present(disabled, "re_chain", "hard", "re inversion")         # others intact

    # bare family name disables both severities
    monkeypatch.setenv("GALMCP_DISABLE_MECH_CHECKS", "flux_share")
    gone = _raw(inv)
    assert not any(ck == "flux_share" for ck, _, _ in gone)

    # unknown family names are ignored (warned once), nothing disabled
    monkeypatch.setenv("GALMCP_DISABLE_MECH_CHECKS", "bogus_family")
    assert _present(_raw(inv), "mu0_order", "hard", "mu0_bulge")
