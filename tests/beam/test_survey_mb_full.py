"""P4b+P5 tests: mb prompts, digest arcsec rendering, physicality arcsec
thresholds, and a FULL survey_round on a galfits graph with a mocked VLM."""

import json
import shutil

import pytest

from beam.graph import BeamGraph
from beam_mb.lyric_doc import parse_lyric_doc

BM = {"decam_g": {"pixscale": 0.262, "fwhm_px": 4.58, "region": [1, 746, 1, 746]},
      "decam_r": {"pixscale": 0.262, "fwhm_px": 4.62, "region": [1, 746, 1, 746]},
      "decam_z": {"pixscale": 0.262, "fwhm_px": 3.93, "region": [1, 746, 1, 746]}}

VLM_JSON = json.dumps({
    "physicality_verdict": {"verdict": "PASS", "failed_checks": [],
                            "swap_hint": None},
    "candidates": [{
        "action_id": "", "primitives": [
            {"op": "tune", "target": "bar",
             "params": {"re_px": 20.0, "q": 0.4, "pa_sky_deg": 130.0,
                        "panel_band": "decam_r"}}],
        "physical_motivation": "bar residual quadrupole on decam_r panel",
        "expected_C_prime": "{disk,bulge,bar}",
        "novelty_claim": "cross-cell bar re-seed",
        "expected_behavior_tag": "tune_bar_re",
        "local_benefit_sigma": 0.6,
    }],
})


def _mb_graph(tmp_path, test_data_dir, recorded=True):
    gdir = tmp_path / "MBP45"
    gdir.mkdir()
    shutil.copy(test_data_dir / "mb" / "mb_root.lyric", gdir / "obj.lyric")
    graph = BeamGraph.init(str(gdir), str(gdir / "obj.lyric"),
                           stage1={"morphology": "edge-on disk"},
                           mode="galfits")
    graph.g.graph["meta"]["bands"] = BM
    if recorded:
        graph.record_fit({
            "input_param_file": str(test_data_dir / "mb" / "mb_iter3.lyric"),
            "output_param_file": str(test_data_dir / "mb" / "mb_iter3.gssummary"),
            "image_file": str(test_data_dir / "mb" /
                              "mb_iter3_component_attributes.txt"),
            "fit_statistics": {"bic": 546297.625, "reduced_chisq": 0.5885},
        })
    return graph, str(gdir)


# ------------------------------------------------------------------ prompts
def test_mb_prompt_templates_render(test_data_dir):
    from prompts import prompts as P

    t1 = P.get_survey_visual_extraction_mb(global_state_description="GS-ANCHOR")
    assert "GS-ANCHOR" in t1 and "panel_band" in t1 and "sky-PA" in t1
    t2 = P.get_survey_candidate_generation_mb(
        summary_content="SUM-CONTENT", global_state_description="GS",
        local_state_description="LOCAL", branch_id="A", parent_label="A.1",
        depth=2, queue_digest="Q", directives="(none)")
    for token in ("SUM-CONTENT", "GS", "LOCAL", "A.1", "Q"):
        assert token in t2, token
    # the JSON contract matches the mb schema vocabulary
    assert "panel_band" in t2 and "pa_sky_deg" in t2
    assert "Fourier variants" in t2  # explicitly outside the solution space


def test_mb_opening_refs_and_summary(test_data_dir):
    from beam_mb.survey_mb import (mb_opening_ref_lines,
                                   mb_summary_content)

    refs = mb_opening_ref_lines(str(test_data_dir / "mb" / "mb_root.lyric"))
    assert 'obj0' in refs and 'Re=3.0' in refs and 'sky-PA' in refs
    s = mb_summary_content(str(test_data_dir / "mb" / "mb_iter3.gssummary"))
    assert "disk_Re" in s and "fixed parameters" in s


# ------------------------------------------------------------------ digest
def test_digest_mb_arcsec_rendering(tmp_path, test_data_dir):
    from beam.digest import build_global_state_description, \
        build_local_state_description

    graph, gdir = _mb_graph(tmp_path, test_data_dir)
    gd = build_global_state_description(graph)
    assert "ARCSEC" in gd and "panel_band" in gd and "decam_g" in gd
    ld, _trig = build_local_state_description(graph, graph.latest_state())
    assert 'Re=6.206"' in ld and "=23.7px @decam_g" in ld
    assert "Mag[decam_g=16.3,decam_r=15.6,decam_z=15]" in ld
    assert "skyPA=-54.63" in ld


# ------------------------------------------------------------------ physicality
def test_physicality_mb_arcsec_thresholds(tmp_path, test_data_dir):
    from beam.physicality import compute_mech_checks

    graph, _ = _mb_graph(tmp_path, test_data_dir)
    label = graph.latest_state()
    node = graph.state(label)
    # psf = widest band FWHM arcsec = 4.62 * 0.262 = 1.21": iter3's bulge
    # (0.131") and bar (2.096") are NOT degenerate vs it (ratio 0.063);
    # the flux floors still fire (reference-band starved bulge/bar)
    mech = compute_mech_checks(graph, node)
    kinds = {(m["check"], m["severity"]) for m in mech}
    assert ("flux_share", "hard") in kinds
    assert not any(m["check"] == "degeneracy" for m in mech)

    # bar_identity arcsec: bar 0.5" (<< 4px*0.262=1.048"), q 0.45, no bulge
    node2 = dict(node)
    inv = [dict(c) for c in node["inventory"]]
    for c in inv:
        if c["name"] == "bar":
            c["re_effective"] = c["re"] = 0.5
            c["q"] = 0.45
        if c["name"] == "bulge":
            c["re_effective"] = c["re"] = 0.01
    node2 = dict(node2, inventory=inv)
    # drop the bulge to test the no-bulge condition properly
    inv_nb = [c for c in inv if c["name"] != "bulge"] + [
        {"name": "x", "type": "sersic", "re": None, "re_effective": None}]
    notes = [m for m in compute_mech_checks(
        graph, dict(node2, inventory=[c for c in inv if c["name"] != "bulge"]))
        if m["check"] == "bar_identity"]
    assert notes and "arcsec" in notes[0]["detail"]


# ------------------------------------------------------------------ full round
def test_full_survey_round_mb_mocked_vlm(tmp_path, test_data_dir,
                                         monkeypatch):
    """survey_round on a galfits graph with a mocked _dispatch: the mb
    prompt pair is assembled, the mb schema validates, candidates enqueue."""
    from beam import survey as survey_mod

    graph, gdir = _mb_graph(tmp_path, test_data_dir)
    label = graph.latest_state()

    captured: dict = {}

    def _fake_dispatch(system_prompt, turns, image_path):
        captured["system"] = system_prompt
        captured["turns"] = turns
        captured["image"] = image_path
        return "visual notes...\n\n```json\n" + VLM_JSON + "\n```", \
            "sess-mb-test", None

    monkeypatch.setattr(survey_mod, "_dispatch", _fake_dispatch)
    res = survey_mod.survey_round(gdir, state_label=label)
    assert res.get("status") == "success", res.get("error")
    # the mb prompt pair was used (unit contract present, galfit wording not)
    assert "panel_band" in captured["turns"][0]
    assert "arcsec forbidden" in captured["turns"][1] \
        or "never write arcsec" in captured["turns"][1]
    # candidate enqueued on the graph
    graph = BeamGraph.load(gdir)
    pend = graph.g.graph["pending"]
    tuned = [r for r in pend.values()
             if r.get("expected_behavior_tag") == "tune_bar_re"]
    assert tuned, pend.keys()
    assert tuned[0]["primitives"][0]["params"]["panel_band"] == "decam_r"
    # verdict settled on the state (mech table merged)
    v = graph.state(label).get("verdict") or {}
    assert v.get("verdict") in ("PASS", "FAIL")
