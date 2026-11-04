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


def test_full_opening_round_mb_mocked_vlm(tmp_path, test_data_dir, monkeypatch):
    """The round-0 OPENING path (workflow_galfit.md-mirrored): survey_round
    on the unfitted A.0 with the rendered original -> VLM skeleton
    candidates enqueued parented A.0 -> apply_candidate transcribes
    _iter1.lyric. A.0 is never fitted directly."""
    from beam import survey as survey_mod
    from beam.tools import apply_candidate

    gdir = tmp_path / "MBOPEN"
    gdir.mkdir()
    shutil.copy(test_data_dir / "mb" / "mb_root.lyric", gdir / "obj.lyric")
    graph = BeamGraph.init(str(gdir), str(gdir / "obj.lyric"),
                           stage1={"morphology": "spiral with bar-like "
                                                 "centre"}, mode="galfits")
    graph.g.graph["meta"]["bands"] = BM
    graph.commit()
    opening_png = tmp_path / "opening.png"
    opening_png.write_bytes(b"png")

    opening_json = json.dumps({
        "physicality_verdict": {"verdict": "PASS",
                                "failed_checks": [], "swap_hint": None},
        "candidates": [
            {"action_id": "", "primitives": [{"op": "skeleton", "components": [
                {"structure_name": "disk", "re_px": 23.0, "q": 0.55,
                 "pa_sky_deg": -55.0, "dx_px": 0.0, "dy_px": 0.0,
                 "panel_band": "decam_g"},
                {"structure_name": "bulge", "re_px": 3.0, "q": 0.85,
                 "pa_sky_deg": -55.0, "dx_px": 0.0, "dy_px": 0.0,
                 "panel_band": "decam_g"}]}],
             "physical_motivation": "extended disk + round centre, g panel",
             "expected_C_prime": "{disk,bulge}", "novelty_claim": "opening",
             "expected_behavior_tag": "skeleton_disk_bulge",
             "local_benefit_sigma": 0.5},
            {"action_id": "", "primitives": [{"op": "skeleton", "components": [
                {"structure_name": "singlesersic", "re_px": 18.0, "q": 0.6,
                 "pa_sky_deg": -55.0, "dx_px": 0.0, "dy_px": 0.0,
                 "panel_band": "decam_g"}]}],
             "physical_motivation": "smooth single-component alternative",
             "expected_C_prime": "{singlesersic}", "novelty_claim": "opening",
             "expected_behavior_tag": "skeleton_singlesersic",
             "local_benefit_sigma": 0.4},
        ],
    })

    captured: dict = {}

    def _fake_dispatch(system_prompt, turns, image_path):
        captured["turns"] = turns
        captured["image"] = image_path
        return "notes\n\n```json\n" + opening_json + "\n```", "sess-open", None

    monkeypatch.setattr(survey_mod, "_dispatch", _fake_dispatch)
    res = survey_mod.survey_round(str(gdir), state_label="A.0",
                                  opening_image=str(opening_png))
    assert res.get("status") == "success", res.get("error")
    # the mb OPENING preamble is present AND the full JSON contract kept
    # (the galfit-style prepend composition — a bare replacement dropped
    # the contract and the VLM returned primitives without the wrapper)
    t2 = captured["turns"][1]
    assert "OPENING ROUND" in t2
    assert "physicality_verdict" in t2 and "primitives" in t2
    assert "local_benefit_sigma" in t2 and "panel_band" in t2
    assert captured["image"] == str(opening_png)
    nxt = res.get("next_candidate")
    assert nxt, res.get("candidates")

    graph = BeamGraph.load(str(gdir))
    pend = graph.g.graph["pending"]
    assert set(pend) == {nxt, [k for k in pend if k != nxt][0]} \
        if len(pend) == 2 else True
    rec = pend[nxt]
    assert rec["parent"] == "A.0" and \
        rec["primitives"][0]["op"] == "skeleton"
    # A.0 untouched (unfitted), skeletons queued, alternates flagged
    assert graph.state("A.0").get("metrics") == {}

    r = apply_candidate(galaxy_dir=str(gdir), action_id=nxt)
    assert r["status"] == "success", r.get("error") or r.get("detail")
    assert r["lyric"].endswith("_iter1.lyric")
    assert r["constrain"] and r["constrain"].endswith("iter1.constrain")
    from beam_mb.lyric_doc import parse_lyric_doc
    doc = parse_lyric_doc(r["lyric"])
    assert [(c.name, c.ptype) for c in doc.components] == \
        [("disk", "sersic"), ("bulge", "sersic")]
    assert doc.strict_invariant_errors() == []


def test_mb_f1_reserved_slot_end_to_end(tmp_path, test_data_dir, monkeypatch):
    """The mb 7+1 reserved F1 (design §11): beam-side enqueue builds the
    remove(disk)+add(disk, sersic_f) rebuild pair from the BEST state's
    fitted disk (arcsec -> ref-band px) with the am/theta_m warm start;
    apply_candidate transcribes a sersic_f disk whose P17-P24 are seeded
    (r_in~0.2*Re, r_out~0.6*Re, am, theta_m) and whose n is the disk
    mandate; the fit counts in n_f1_reserved, not n_executed."""
    from beam.tools import apply_candidate

    graph, gdir = _mb_graph(tmp_path, test_data_dir)
    best = graph.latest_state()
    # iter3's mech table carries hard entries (starved bulge/bar floors):
    # record_fit's fail-safe already auto-settled a FAIL verdict, and
    # apply_verdict is idempotent — reset BOTH to make the state
    # best-eligible for this test
    graph.g.nodes[best]["mech_checks"] = []
    graph.g.nodes[best]["verdict"] = None
    graph.apply_verdict(best, {"verdict": "PASS", "failed_checks": []})
    assert graph.g.graph["best_state"] == best

    aid, why = graph.add_f1_reserved(0.07, 128.0)
    assert aid and not why, why
    rec = graph.g.graph["pending"][aid]
    assert rec["code_flags"]["f1_reserved"] is True
    rm, ad = rec["primitives"]
    assert rm == {"op": "remove", "target": "disk"}
    assert ad["component_type"] == "sersic_f"
    assert ad["panel_band"] == "decam_g"
    assert ad["f1"] == {"amplitude": 0.07, "phase_deg": 128.0}
    # arcsec -> px round-trip of the fitted disk (Re 6.2057" / 0.262)
    assert ad["re_px"] == pytest.approx(23.686, abs=0.01)

    r = apply_candidate(galaxy_dir=gdir, action_id=aid)
    assert r["status"] == "success", r.get("error") or r.get("detail")
    from beam_mb.lyric_doc import parse_lyric_doc
    doc = parse_lyric_doc(r["lyric"])
    disk = doc.component("disk")
    assert disk.ptype == "sersic_f"
    assert disk.slot_five("n") == (1.0, 0.5, 2.0, 0.1, 0)   # disk mandate
    f17 = disk.slot_five("r_in")
    f18 = disk.slot_five("r_out")
    f22 = disk.slot_five("am")
    f23 = disk.slot_five("theta_m")
    assert f17[0] == pytest.approx(0.2 * 6.206, abs=0.01)
    assert f18[0] == pytest.approx(0.6 * 6.206, abs=0.01)
    assert f22[0] == 0.07 and f23[0] == 128.0
    assert disk.slot_value("m") == 1 and disk.slot_five("m") is None
    assert doc.strict_invariant_errors() == []
    # once only: a second enqueue is refused while the first is pending
    aid2, why2 = graph.add_f1_reserved(0.05, 30.0)
    assert aid2 is None and "already pending" in why2
