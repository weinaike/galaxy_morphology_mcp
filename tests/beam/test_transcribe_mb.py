"""Tests for beam_mb.transcribe_mb — the mechanical P3 transcriber.

Golden sources: the historical KILOGAS_110 iter3->iter4 transition (tune:
bar growth + bulge n release) and the root skeleton (-> iter2-equivalent).
The comparison is SEMANTIC per the transcription contract: declared params
match the historical inits; undeclared params match the PARENT FITTED
values (the historical agent occasionally re-used input values instead —
e.g. iter4's bulge Re carries iter3's INPUT 0.3144, not its fitted 0.131;
our clean-increment contract deliberately differs there).
"""

import os

import pytest

from beam_mb.lyric_doc import parse_lyric_doc
from beam_mb.transcribe_mb import transcribe_mb

BM = {"decam_g": {"pixscale": 0.262, "fwhm_px": 4.58, "region": [1, 746, 1, 746]},
      "decam_r": {"pixscale": 0.262, "fwhm_px": 4.62, "region": [1, 746, 1, 746]},
      "decam_z": {"pixscale": 0.262, "fwhm_px": 3.93, "region": [1, 746, 1, 746]}}


def _t(tmp_path, name, parent, gss, prims):
    out = str(tmp_path / name)
    r = transcribe_mb(parent, gss, prims, out, bands_meta=BM)
    assert r["status"] == "success", r.get("error")
    return r, parse_lyric_doc(out), out


# ------------------------------------------------------------------ skeleton
def test_skeleton_golden(tmp_path, test_data_dir):
    """ROOT obj0 -> {disk, bulge} (the KILOGAS_110 iter2 transition)."""
    p = str(test_data_dir / "mb" / "mb_root.lyric")
    r, doc, out = _t(tmp_path, "_iter2.lyric", p, None, [
        {"op": "skeleton", "components": [
            {"structure_name": "disk", "component_type": "sersic",
             "re_px": 22.85, "q": 0.49, "pa_sky_deg": -54.0,
             "dx_px": 0.0, "dy_px": 0.0, "panel_band": "decam_g"},
            {"structure_name": "bulge", "component_type": "sersic",
             "re_px": 3.0, "q": 0.85, "pa_sky_deg": -54.0,
             "dx_px": 0.0, "dy_px": 0.0, "panel_band": "decam_g"}]}])
    assert [(c.name, c.ptype) for c in doc.components] == \
        [("disk", "sersic"), ("bulge", "sersic")]
    disk, bulge = doc.components
    assert disk.slot_five("re") == (pytest.approx(5.9867, abs=1e-3), 0.121,
                                    97.595, 0.08, 1)
    assert disk.slot_five("n") == (1.0, 0.5, 2.0, 0.1, 0)     # disk mandate
    assert bulge.slot_five("n")[4] == 1                        # bulge free
    assert bulge.slot_five("n")[0] == 4.0                      # free-at-4 prior
    # chained: bulge centres vary=0, disk stays the free anchor
    assert disk.slot_five("xcen")[4] == 1 and bulge.slot_five("xcen")[4] == 0
    # R/I/A carried verbatim: 3 assembled bands unchanged
    assert doc.band_names == ["decam_g", "decam_r", "decam_z"]
    assert doc.galaxies["a"].value(2) == ["a", "b"]
    assert doc.strict_invariant_errors() == []
    # constrain written with the paired binding
    cfile = r["constrain"]
    assert cfile and os.path.exists(cfile)
    text = open(cfile).read()
    assert "pardictlc['bulge_xcen'] = 1 * pardictlc['disk_xcen']" in text
    assert "pardictlc['bulge_ycen'] = 1 * pardictlc['disk_ycen']" in text


# ------------------------------------------------------------------ add
def test_add_label_and_membership(tmp_path, test_data_dir):
    p = str(test_data_dir / "mb" / "mb_iter3.lyric")
    gss = str(test_data_dir / "mb" / "mb_iter3.gssummary")
    _, doc, _ = _t(tmp_path, "_iter5.lyric", p, gss, [
        {"op": "add", "structure_name": "lens", "component_type": "sersic",
         "re_px": 9.0, "q": 0.7, "pa_sky_deg": -54.0,
         "dx_px": 0.0, "dy_px": 0.0, "panel_band": "decam_g"}])
    assert [c.label for c in doc.components] == ["a", "b", "c", "d"]
    assert doc.galaxies["a"].value(2) == ["a", "b", "c", "d"]
    lens = doc.component("lens")
    assert lens.slot_five("re") == (pytest.approx(2.358), 0.121, 97.595, 0.08, 1)
    # SED template rows cloned (P15 bins, P27 0)
    assert lens.value(15) == "bins" and lens.value(27) == 0
    assert doc.strict_invariant_errors() == []


def test_add_agn_n_block(tmp_path, test_data_dir):
    """add(agn) authors the N block with the anti-degeneracy config."""
    p = str(test_data_dir / "mb" / "mb_iter3.lyric")
    gss = str(test_data_dir / "mb" / "mb_iter3.gssummary")
    r, doc, out = _t(tmp_path, "_iter7.lyric", p, gss, [
        {"op": "add", "structure_name": "agn",
         "dx_px": 0.0, "dy_px": 0.0, "panel_band": "decam_g"}])
    assert r["agn_blocks"] == ["a"]
    nb = doc.nuclei["a"]
    assert nb.value(1) == "AGN"
    assert nb.value(10) == [42, 41, 48, 0.1, 0]        # Na10 fixed prior
    assert nb.value(11) == [1, 0, 4, 0.1, 0]           # Na11 fixed alpha
    assert nb.value(18) == 4                           # arbitrary continuum
    assert nb.value(26) == [1.0, 0.2, 5.0, 0.1, 1]     # Na26 sole free flux
    assert doc.strict_invariant_errors() == []
    text = open(r["constrain"]).read()
    assert "pardictlc['xcen_AGN'] = 1 * pardictlc['disk_xcen']" in text


# ------------------------------------------------------------------ tune golden
def test_tune_golden_iter3_to_iter4(tmp_path, test_data_dir):
    """The historical iter3->iter4 transition: bar grows (Re 1.179->2.096,
    q 0.3->0.15, PA ->129.8), bulge n released at 2.0, everything else
    warm-started from iter3's FITTED values."""
    p = str(test_data_dir / "mb" / "mb_iter3.lyric")
    gss = str(test_data_dir / "mb" / "mb_iter3.gssummary")
    _, doc, _ = _t(tmp_path, "_iter4.lyric", p, gss, [
        {"op": "tune", "target": "bar", "params": {
            "re_px": 8.0, "q": 0.15, "pa_sky_deg": 129.84,
            "panel_band": "decam_g"}},
        {"op": "tune", "target": "bulge", "params": {
            "n": 2.0, "pa_sky_deg": 120.55}}])
    disk, bulge, bar = doc.components
    # declared: 8px x 0.262 = 2.096 — EXACT match to the historical bar Re
    assert bar.slot_five("re")[0] == pytest.approx(2.096, abs=1e-3)
    assert bar.slot_five("q")[0] == 0.15
    assert bar.slot_five("pa")[0] == pytest.approx(129.84, abs=0.01)
    assert bulge.slot_five("n") == (2.0, 0.1, 8.0, 0.1, 1)
    # warm-started from iter3 FITTED (gssummary): 6.2057 / -54.6316 / 0.4967
    assert disk.slot_five("re")[0] == pytest.approx(6.2057, abs=1e-3)
    assert disk.slot_five("pa")[0] == pytest.approx(-54.6316, abs=1e-3)
    assert disk.slot_five("q")[0] == pytest.approx(0.4967, abs=1e-3)
    # bulge Re warm-started at the FITTED 0.131 (the historical agent
    # carried the parent INPUT 0.3144 — our contract deliberately differs)
    assert bulge.slot_five("re")[0] == pytest.approx(0.131, abs=1e-3)
    # default bands everywhere: no init pinned at an inherited boundary
    for c in (disk, bulge, bar):
        for slot in ("re", "q"):
            init, lo, hi, _s, _v = c.slot_five(slot)
            assert lo < init < hi, (c.name, slot)
        assert c.slot_five("pa")[1:3] == (-180.0, 180.0)
    # centres: disk free anchor, subordinates chained vary=0
    assert disk.slot_five("xcen")[4] == 1
    assert bulge.slot_five("xcen")[4] == 0 and bar.slot_five("xcen")[4] == 0


# ------------------------------------------------------------------ remove / reclassify
def test_remove_and_reclassify(tmp_path, test_data_dir):
    p = str(test_data_dir / "mb" / "mb_iter3.lyric")
    gss = str(test_data_dir / "mb" / "mb_iter3.gssummary")
    _, doc, _ = _t(tmp_path, "_iter6.lyric", p, gss, [
        {"op": "remove", "target": "bar"},
        {"op": "reclassify", "reclassify": {"map": [
            {"from": "bulge", "to": "bar"}]}}])
    assert [(c.name, c.ptype) for c in doc.components] == [("disk", "sersic"),
                                                            ("bar", "sersic")]
    assert doc.galaxies["a"].value(2) == ["a", "b"]
    # reclassify INTO the bar slot applies the n=0.5 mandate
    assert doc.component("bar").slot_five("n") == (0.5, 0.4, 0.6, 0.1, 0)
    assert doc.strict_invariant_errors() == []


def test_remove_agn(tmp_path, test_data_dir):
    p = str(test_data_dir / "mb" / "mb_iter7_agn.lyric")
    r = transcribe_mb(p, None, [{"op": "remove", "target": "agn"}],
                      str(tmp_path / "_iter8.lyric"), bands_meta=BM)
    # the iter7 parent carries the dangling-band pathology — inherited
    # violations are refused (engine outputs must be clean even when the
    # historical parent was not)
    assert r["status"] == "failure"
    assert any("dangling I" in e for e in r.get("errors", []))


# ------------------------------------------------------------------ failures
def test_failure_paths(tmp_path, test_data_dir):
    p = str(test_data_dir / "mb" / "mb_iter3.lyric")
    gss = str(test_data_dir / "mb" / "mb_iter3.gssummary")
    out = str(tmp_path / "x.lyric")
    r = transcribe_mb(p, gss, [{"op": "explode"}], out, bands_meta=BM)
    assert r["status"] == "failure" and "unknown op" in r["error"]
    # mandated n is untunable
    r = transcribe_mb(p, gss, [{"op": "tune", "target": "bar",
                                "params": {"n": 1.0}}], out, bands_meta=BM)
    assert r["status"] == "failure" and "type-mandated" in r["error"]
    # duplicate add
    r = transcribe_mb(p, gss, [{"op": "add", "structure_name": "bulge"}],
                      out, bands_meta=BM)
    assert r["status"] == "failure" and "already exists" in r["error"]
    # unknown remove target
    r = transcribe_mb(p, gss, [{"op": "remove", "target": "ghost"}],
                      out, bands_meta=BM)
    assert r["status"] == "failure" and "unknown target" in r["error"]
    # clean outputs never written on failure
    assert not os.path.exists(out)


def test_output_passes_check_lyric_file(tmp_path, test_data_dir):
    """The apply-time gate: a transcribed output must survive the static
    validator (five-tuple semantics, identifier lint, section ranges)."""
    from tools.modify_lyric import check_lyric_file

    p = str(test_data_dir / "mb" / "mb_iter3.lyric")
    gss = str(test_data_dir / "mb" / "mb_iter3.gssummary")
    _, _, out = _t(tmp_path, "_iter4.lyric", p, gss, [
        {"op": "tune", "target": "bar", "params": {
            "re_px": 8.0, "q": 0.15, "pa_sky_deg": 129.84,
            "panel_band": "decam_g"}}])
    check = check_lyric_file(out)
    assert check["status"] == "success", check.get("message")
