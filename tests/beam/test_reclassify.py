"""Reclassify primitive tests (KILOGAS_296 retrospective): identity
reassignment of well-fitted components — schema validation, projection,
transcription, and the dBIC-vs-parent digest fact."""

import json

import pytest

from beam.candidate_schema import parse_survey_response, validate_survey
from beam.graph import BeamGraph
from beam.signature import apply_primitives_to_inventory


def _resp(cands):
    return {"physicality_verdict": {"verdict": "PASS", "failed_checks": [],
                                    "swap_hint": "none"}, "candidates": cands}


def _cand(prims, tag="rc", sigma=0.6):
    return {"primitives": prims, "physical_motivation": "good light, wrong labels",
            "expected_C_prime": "c", "novelty_claim": "identity reassignment",
            "expected_behavior_tag": tag, "local_benefit_sigma": sigma}


def _swap_prims():
    return [{"op": "reclassify",
             "reclassify": {"map": [{"from": "bulge", "to": "bar"},
                                    {"from": "bar", "to": "bulge"}]}}]


@pytest.fixture
def graph3(tmp_path, test_data_dir):
    """A fitted {disk, bulge, bar} graph for validation/projection tests."""
    import shutil

    gdir = tmp_path / "RC"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)  # disk+bulge+bar+agn
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    label = g.record_fit({
        "input_param_file": str(feedme), "output_param_file": "",
        "image_file": "", "summary_file": "", "round_status_file": "",
        "fit_statistics": {"bic_eff": 5000.0, "chisq1d_nu": 40.0,
                           "convergence": {"flag": "ok"}}})
    return g, label


# ------------------------------------------------------------- schema
def test_reclassify_legal_swap(graph3):
    g, label = graph3
    resp, err = parse_survey_response(_resp([_cand(_swap_prims())]))
    assert resp is not None, err
    rep = validate_survey(resp, g, label)
    assert not any(i.code == "E_RECLASSIFY" for i in rep.errors), \
        [i.message for i in rep.errors]


@pytest.mark.parametrize("bad_map,why", [
    ([{"from": "ghost", "to": "bar"}], "unknown source"),
    ([{"from": "bulge", "to": "bar"}, {"from": "bar", "to": "bar"}], "dup target"),
    ([{"from": "bulge", "to": "agn"}], "target outside shaped six"),
    ([{"from": "bulge", "to": "bulge"}], "identity map"),
])
def test_reclassify_schema_rejections(graph3, bad_map, why):
    g, label = graph3
    resp, err = parse_survey_response(
        _resp([_cand([{"op": "reclassify", "reclassify": {"map": bad_map}}])]))
    assert resp is not None, err
    rep = validate_survey(resp, g, label)
    assert any(i.code == "E_RECLASSIFY" for i in rep.errors), why


def test_reclassify_empty_map_rejected_at_parse(graph3):
    resp, err = parse_survey_response(
        _resp([_cand([{"op": "reclassify", "reclassify": {"map": []}}])]))
    assert resp is None and "at least 1 item" in err


def test_reclassify_rejected_on_root(tmp_path, test_data_dir):
    import shutil

    gdir = tmp_path / "RC0"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    resp, _ = parse_survey_response(_resp([_cand(_swap_prims())]))
    rep = validate_survey(resp, g, "A.0")
    assert any(i.code == "E_RECLASSIFY" for i in rep.errors)


def test_reclassify_losing_disk_rejected(graph3):
    # {disk, bulge, bar, agn} with disk->lens and nobody taking the disk slot
    g, label = graph3
    resp, _ = parse_survey_response(_resp([_cand(
        [{"op": "reclassify",
          "reclassify": {"map": [{"from": "disk", "to": "lens"}]}}])]))
    rep = validate_survey(resp, g, label)
    assert any(i.code in ("E_MULTIPLICITY", "E_RECLASSIFY", "E_APPLY")
               for i in rep.errors)


# --------------------------------------------------------- projection
def test_projection_swaps_types_and_mandates():
    parent = [
        {"name": "disk", "type": "expdisk", "re": 30.0, "re_effective": 50.4,
         "mag": 15.0, "q": 0.4, "pa": 30.0, "n": None, "toggles": {}},
        {"name": "bar", "type": "sersic", "re": 12.0, "re_effective": 12.0,
         "mag": 16.0, "q": 0.45, "pa": 80.0, "n": 0.5, "toggles": {"n": 0}},
        {"name": "bulge", "type": "sersic", "re": 3.0, "re_effective": 3.0,
         "mag": 17.0, "q": 0.6, "pa": 30.0, "n": 2.0, "toggles": {"n": 1}},
    ]
    inv, err = apply_primitives_to_inventory(parent, _swap_prims())
    assert err == "" and inv is not None
    by = {c["name"]: c for c in inv}
    # light parameters carried verbatim; types and mandates swapped
    assert by["bulge"]["re_effective"] == 12.0 and by["bulge"]["type"] == "sersic"
    assert by["bulge"]["toggles"]["n"] == 1          # free from creation
    assert by["bar"]["re_effective"] == 3.0
    assert by["bar"]["n"] == 0.5 and by["bar"]["toggles"]["n"] == 0
    assert by["disk"]["re_effective"] == 50.4        # untouched


def test_projection_disk_target_converts_units():
    parent = [
        {"name": "disk", "type": "expdisk", "re": 30.0, "re_effective": 50.4,
         "mag": 15.0, "q": 0.4, "pa": 30.0, "n": None, "toggles": {}},
        {"name": "bulge", "type": "sersic", "re": 3.0, "re_effective": 3.0,
         "mag": 17.0, "q": 0.6, "pa": 30.0, "n": 2.0, "toggles": {"n": 1}},
    ]
    inv, err = apply_primitives_to_inventory(parent, [
        {"op": "reclassify",
         "reclassify": {"map": [{"from": "disk", "to": "outerdisk"},
                                {"from": "bulge", "to": "disk"}]}}])
    assert err == "" and inv is not None
    by = {c["name"]: c for c in inv}
    # bulge (sersic Re=3) -> disk: expdisk, Rs = 3/1.68
    assert by["disk"]["type"] == "expdisk"
    assert abs(by["disk"]["re"] - 3.0 / 1.68) < 1e-9
    assert by["disk"]["re_effective"] == 3.0
    assert "n" not in by["disk"] or by["disk"].get("n") is None
    # disk (expdisk, exp type legal) -> outerdisk keeps expdisk, Re carried
    assert by["outerdisk"]["type"] == "expdisk"
    assert by["outerdisk"]["re_effective"] == 50.4


# ------------------------------------------------------- transcription
def test_transcribe_reclassify_end_to_end(tmp_path):
    from beam.transcribe import transcribe

    parent = tmp_path / "p.feedme"
    parent.write_text("""================================================================================
A) image.fits
B) out.fits
C) sigma.fits
D) psf.fits
E) 1
F) none
G) none
H) 1 200 1 200
I) 50 50
J) 22.5
K) 0.263 0.263

# Component number: 1
# STRUCTURE: disk
0) expdisk
1) 100.0 100.0 1 1
3) 15.0        1
4) 30.0        1
9) 0.4        1
10) 30.0        1
Z) 0

# Component number: 2
# STRUCTURE: bulge
0) sersic
1) 100.0 100.0 1 1
3) 16.0        1
4) 12.0        1
5) 2.0        1
9) 0.45        1
10) 80.0        1
Z) 0

# Component number: 3
# STRUCTURE: bar
0) sersic
1) 100.0 100.0 1 1
3) 17.0        1
4) 3.0        1
5) 0.5        0
9) 0.6        1
10) 30.0        1
Z) 0

# Component number: 4
# STRUCTURE: sky
0) sky
1) 0.001      0
Z) 0
================================================================================
""")
    out = tmp_path / "c.feedme"
    outc = tmp_path / "c.cons"
    r = transcribe(str(parent), "", _swap_prims(), str(out), str(outc),
                   psf_fwhm_px=4.0)
    assert r.ok, r.notes
    text = out.read_text()
    # names/types swapped; carried params verbatim
    import re as _re

    def block(name):
        seg = text.split(f"# STRUCTURE: {name}\n", 1)[1]
        seg = seg.split("# STRUCTURE:", 1)[0]
        ctype = _re.match(r"0\) (\w+)", seg).group(1)
        return ctype, seg
    t_bar, rows_bar = block("bar")
    assert t_bar == "sersic"
    assert "4) 12" in rows_bar            # old bulge Re carried
    assert _re.search(r"5\) 0\.5\s+0", rows_bar)   # n mandate: 0.5 fixed
    t_bul, rows_bul = block("bulge")
    assert t_bul == "sersic"
    assert "4) 3" in rows_bul              # old bar Re carried
    assert _re.search(r"5\) 0\.5\s+1", rows_bul)   # n released, warm 0.5
    t_disk, _ = block("disk")
    assert t_disk == "expdisk"
    assert "# STRUCTURE: sky" in text and "0.001" in text
    # cons: concentric chain over the three central components
    assert "offset" in outc.read_text()


# ------------------------------------------------------- digest dBIC fact
def test_digest_dbic_fact(graph3):
    from beam.digest import build_local_state_description

    g, label = graph3
    # child with better BIC
    aid = g.add_pending({"action_id": "x", "sigma": 0.6, "score": 0.6,
                         "primitives": [], "expected_behavior_tag": "t"},
                        "s", label)
    child = g.record_fit({
        "input_param_file": g.state(label)["artifacts"]["feedme"],
        "output_param_file": "", "image_file": "", "summary_file": "",
        "round_status_file": "",
        "fit_statistics": {"bic_eff": 4500.0, "chisq1d_nu": 38.0,
                           "convergence": {"flag": "ok"}}}, action_id=aid)
    desc, _tr = build_local_state_description(g, child, {})
    assert "dBIC_eff vs parent = -500.0 (improved" in desc
    # parent A.0 exists but carries no BIC -> the n/a line
    desc_root, _ = build_local_state_description(g, label, {})
    assert "dBIC_eff vs parent: n/a" in desc_root
