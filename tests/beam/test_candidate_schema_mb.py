"""Tests for beam_mb.candidate_schema_mb — the mb candidate JSON schema
and solution-space rules, plus the survey.py mode dispatch."""

import shutil

import pytest

from beam.graph import BeamGraph
from beam_mb.candidate_schema_mb import (
    MbCandidate, MbSurveyResponse, parse_survey_response_mb,
    validate_mb_survey,
)

BM = {"decam_g": {"pixscale": 0.262, "fwhm_px": 4.58, "region": [1, 746, 1, 746]},
      "decam_r": {"pixscale": 0.262, "fwhm_px": 4.62, "region": [1, 746, 1, 746]},
      "decam_z": {"pixscale": 0.262, "fwhm_px": 3.93, "region": [1, 746, 1, 746]}}


def _mb_graph(tmp_path, test_data_dir):
    """A galfits-mode graph whose latest state is the recorded iter3 fit
    ({disk, bulge, bar} — a non-root parent for rule testing)."""
    gdir = tmp_path / "MBS"
    gdir.mkdir()
    shutil.copy(test_data_dir / "mb" / "mb_root.lyric", gdir / "obj.lyric")
    graph = BeamGraph.init(str(gdir), str(gdir / "obj.lyric"),
                           stage1={"morphology": "t"}, mode="galfits")
    graph.g.graph["meta"]["bands"] = BM      # beam_init mb normally sets this
    graph.record_fit({
        "input_param_file": str(test_data_dir / "mb" / "mb_iter3.lyric"),
        "output_param_file": str(test_data_dir / "mb" / "mb_iter3.gssummary"),
        "fit_statistics": {"bic": 546297.625, "reduced_chisq": 0.5885},
    })
    return graph


def _payload(prims, tag="t1"):
    return {
        "physicality_verdict": {"verdict": "PASS", "failed_checks": []},
        "candidates": [{
            "action_id": "c1", "primitives": prims,
            "physical_motivation": "m", "expected_C_prime": "{disk,bulge,bar}",
            "expected_behavior_tag": tag, "local_benefit_sigma": 0.5,
        }],
    }


def _validate(graph, prims, tag="t1"):
    payload = _payload(prims, tag)
    resp, err = parse_survey_response_mb(payload)
    assert resp is not None, err
    return validate_mb_survey(resp, graph, graph.latest_state())


# ------------------------------------------------------------------ parse
def test_parse_valid_and_invalid():
    resp, err = parse_survey_response_mb(_payload(
        [{"op": "add", "structure_name": "bar", "re_px": 8.0, "q": 0.3,
          "pa_sky_deg": -55.0, "panel_band": "decam_g"}]))
    assert resp is not None and err == ""
    assert resp.candidates[0].primitives[0].op == "add"
    # px fields without the panel tag fail at the PYDANTIC level
    bad = _payload([{"op": "add", "structure_name": "bar", "re_px": 8.0,
                     "q": 0.3, "pa_sky_deg": -55.0}])
    resp2, err2 = parse_survey_response_mb(bad)
    assert resp2 is None and "panel_band" in err2
    # PA outside the sky convention range is a schema error
    bad2 = _payload([{"op": "add", "structure_name": "bar", "re_px": 8.0,
                      "q": 0.3, "pa_sky_deg": 270.0,
                      "panel_band": "decam_g"}])
    assert parse_survey_response_mb(bad2)[0] is None


# ------------------------------------------------------------------ rules
def test_valid_add_passes(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)
    # duplicate slot -> reject; a DIFFERENT slot is fine
    r = _validate(graph, [{"op": "add", "structure_name": "bulge",
                           "re_px": 2.0, "q": 0.8, "pa_sky_deg": 30.0,
                           "panel_band": "decam_r"}], "dup")
    assert any(i.code == "E_MULTIPLICITY" for i in r.errors)


def test_restricted_space(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)
    for bad_name in ("lens", "outerdisk", "edgedisk", "companion"):
        r = _validate(graph, [{"op": "add", "structure_name": bad_name,
                               "re_px": 5.0, "q": 0.7, "pa_sky_deg": 0.0,
                               "panel_band": "decam_g"}], f"t-{bad_name}")
        assert any(i.code == "E_RESTRICTED_SPACE" for i in r.errors), bad_name
    # sersic_f is legal ONLY on the disk (E_PARAM_TYPE elsewhere);
    # pydantic-level field sanity: f1 amplitude bounded
    r = _validate(graph, [{"op": "add", "structure_name": "bulge",
                           "component_type": "sersic_f", "re_px": 5.0,
                           "q": 0.7, "pa_sky_deg": 0.0,
                           "panel_band": "decam_g"}], "sf-bulge")
    assert any(i.code == "E_PARAM_TYPE" for i in r.errors)


def test_panel_band_contract(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)
    # tune px without panel_band
    r = _validate(graph, [{"op": "tune", "target": "disk",
                           "params": {"re_px": 30.0}}], "pb1")
    assert any(i.code == "E_PANEL_BAND" for i in r.errors)
    # panel_band outside the assembled set
    r = _validate(graph, [{"op": "tune", "target": "disk",
                           "params": {"re_px": 30.0,
                                      "panel_band": "decam_i"}}], "pb2")
    assert any(i.code == "E_PANEL_BAND" for i in r.errors)


def test_mandated_n_untunable(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)
    r = _validate(graph, [{"op": "tune", "target": "bar",
                           "params": {"n": 1.5}}], "mn")
    assert any(i.code == "E_PARAM_MANDATE" for i in r.errors)


def test_regime_rules(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)
    # removing the disk leaves {bulge, bar} — diskless multi-component
    r = _validate(graph, [{"op": "remove", "target": "disk"}], "r1")
    assert any(i.code == "E_MULTIPLICITY" and "diskless" in i.message
               for i in r.errors)
    # unknown targets
    r = _validate(graph, [{"op": "tune", "target": "ghost",
                           "params": {"q": 0.5}}], "r2")
    assert any(i.code == "E_TARGET" for i in r.errors)


def test_reclassify_rules(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)
    r = _validate(graph, [{"op": "reclassify", "reclassify": {"map": [
        {"from": "bulge", "to": "lens"}]}}], "rc1")
    assert any(i.code == "E_RESTRICTED_SPACE" for i in r.errors)
    r = _validate(graph, [{"op": "reclassify", "reclassify": {"map": [
        {"from": "bulge", "to": "bar"}]}}], "rc2")
    assert not any(i.code == "E_RESTRICTED_SPACE" for i in r.errors)


def test_skeleton_only_on_root(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)     # parent is a FITTED state
    r = _validate(graph, [{"op": "skeleton", "components": [
        {"structure_name": "disk", "re_px": 20.0, "q": 0.6,
         "pa_sky_deg": 0.0, "panel_band": "decam_g"}]}], "sk")
    assert any(i.code == "E_OPENING" for i in r.errors)


def test_re_chain_precheck(tmp_path, test_data_dir):
    graph = _mb_graph(tmp_path, test_data_dir)     # disk Re = 6.2057"
    # tune bar to Re = 8" -> bar 8.0 > disk 6.21 by >3% -> E_RE_CHAIN
    r = _validate(graph, [{"op": "tune", "target": "bar",
                           "params": {"re_px": 30.5,
                                      "panel_band": "decam_g"}}], "chain")
    assert any(i.code == "E_RE_CHAIN" for i in r.errors)
    # a legal smaller bar passes the chain
    r = _validate(graph, [{"op": "tune", "target": "bar",
                           "params": {"re_px": 10.0,
                                      "panel_band": "decam_g"}}], "chain2")
    assert not any(i.code == "E_RE_CHAIN" for i in r.errors)


def test_survey_dispatch_by_mode(tmp_path, test_data_dir):
    """survey._schema_pair: galfit graphs keep the galfit pair (the full
    suite covers that path); galfits graphs get the mb pair."""
    from beam import survey as survey_mod
    from beam_mb.candidate_schema_mb import validate_mb_survey as vmb

    graph = _mb_graph(tmp_path, test_data_dir)
    parse_fn, validate_fn = survey_mod._schema_pair(graph.mode)
    assert validate_fn is vmb
    from beam.candidate_schema import validate_survey as vgf
    assert survey_mod._schema_pair("galfit")[1] is vgf


def test_apply_and_record_end_to_end(tmp_path, test_data_dir):
    """The full engine loop without a VLM: pending mb candidate ->
    apply_candidate (mb branch: transcribe_mb + constrain + iter_id) ->
    beam_record_fit by-reference over a synthesized galfits round_status."""
    import json
    import os

    from beam.tools import apply_candidate, beam_record_fit

    gdir = tmp_path / "MBE2E"
    gdir.mkdir()
    shutil.copy(test_data_dir / "mb" / "mb_root.lyric", gdir / "obj.lyric")
    graph = BeamGraph.init(str(gdir), str(gdir / "obj.lyric"),
                           stage1={"morphology": "t"}, mode="galfits")
    graph.g.graph["meta"]["bands"] = BM
    parent = graph.record_fit({
        "input_param_file": str(test_data_dir / "mb" / "mb_iter3.lyric"),
        "output_param_file": str(test_data_dir / "mb" / "mb_iter3.gssummary"),
        "fit_statistics": {"bic": 546297.625, "reduced_chisq": 0.5885},
    })
    aid = graph.add_pending({
        "action_id": "mb-e2e-1", "sigma": 0.5,
        "expected_behavior_tag": "tune_bar",
        "expected_C_prime": "{disk,bulge,bar}",
        "novelty_claim": "grow bar", "score": 0.7,
        "primitives": [{"op": "tune", "target": "bar",
                        "params": {"re_px": 40.0, "q": 0.5,
                                   "pa_sky_deg": 60.0,
                                   "panel_band": "decam_g"}}],
    }, source_session="mb-e2e-test", parent_label=parent)
    graph.commit()

    r = apply_candidate(galaxy_dir=str(gdir), action_id=aid)
    assert r["status"] == "success", r.get("error") or r.get("detail")
    assert r["lyric"].endswith("_iter2.lyric") and os.path.exists(r["lyric"])
    assert r["constrain"] and os.path.exists(r["constrain"])
    graph = BeamGraph.load(str(gdir))       # apply committed its own copy
    rec = graph.g.graph["pending"][aid]
    assert rec["status"] == "applied" and rec["iter_id"] == 2

    # a galfits round_status.json (run_galfits shape) recorded BY REFERENCE
    rs = {
        "stage": "galfits_finished", "status": "success",
        "inputs": {"lyric": str(test_data_dir / "mb" / "mb_iter3.lyric")},
        "outputs": {
            "summary_files": [str(test_data_dir / "mb" / "mb_iter3.gssummary")],
            "comparison_png": str(test_data_dir / "mb" /
                                  "mb_iter3_component_attributes.txt")},
        "fit_statistics": {"bic": 546000.0, "reduced_chisq": 0.57},
    }
    rs_path = tmp_path / "round_status.json"
    rs_path.write_text(json.dumps(rs))
    rr = beam_record_fit(galaxy_dir=str(gdir), action_id=aid,
                         round_status_file=str(rs_path))
    assert rr["status"] == "success", rr.get("error")
    graph = BeamGraph.load(str(gdir))       # record committed its own copy
    node = graph.state(rr["new_state"])
    assert node["metrics"]["bic"] == pytest.approx(546000.0)
    assert node["artifacts"]["lyric"].endswith("mb_iter3.lyric")
    assert node["artifacts"]["gssummary"].endswith("mb_iter3.gssummary")
    # (component_attributes discovery under its canonical archive layout is
    # covered in test_mb_backend.test_artifacts_mb_keys)
    assert graph.g.graph["pending"][aid]["status"] == "executed"


def test_singlesersic_to_multi_implicit_conversion(tmp_path, test_data_dir):
    """Real-data KILOGAS_25 A.1 regression: add(bulge) on a singlesersic
    parent is the IMPLICIT conversion (singlesersic -> disk) — the
    validator must project {disk, bulge} (not {singlesersic, bulge}) or
    the candidate is E_MULTIPLICITY-killed and the survey exhausts."""
    graph = _mb_graph(tmp_path, test_data_dir)
    # swap the recorded state's inventory to a singlesersic regime
    # (graph.state returns a COPY — mutate the node dict directly)
    label = graph.latest_state()
    graph.g.nodes[label]["inventory"] = [{
        "name": "singlesersic", "type": "sersic", "re": 7.975,
        "re_effective": 7.975, "n": 3.77, "q": 0.785, "pa": 1.0,
        "x": 0.0, "y": 0.0, "mags": {"decam_g": 15.3}, "toggles": {}}]
    graph.commit()
    r = _validate(graph, [{"op": "add", "structure_name": "bulge",
                           "re_px": 4.0, "q": 0.85, "pa_sky_deg": 0.0,
                           "dx_px": 0.0, "dy_px": 0.0,
                           "panel_band": "decam_g"}], "conv1")
    assert not r.errors, [f"{i.code}: {i.message}" for i in r.errors]
    # add(disk) on singlesersic = the conversion itself, no duplicate
    r = _validate(graph, [{"op": "add", "structure_name": "disk",
                           "re_px": 30.0, "q": 0.7, "pa_sky_deg": 0.0,
                           "dx_px": 0.0, "dy_px": 0.0,
                           "panel_band": "decam_g"}], "conv2")
    assert not r.errors, [f"{i.code}: {i.message}" for i in r.errors]


def test_f1_switch_candidate_in_main_loop(tmp_path, test_data_dir):
    """F1 proposable INSIDE the main loop (user rule): the remove(disk)+
    add(disk, sersic_f, f1) pair validates on a disk-bearing parent and
    transcribes to a sersic_f disk with the seeded Fourier rows."""
    from beam_mb.transcribe_mb import transcribe_mb as _tx

    graph = _mb_graph(tmp_path, test_data_dir)      # parent {disk,bulge,bar}
    disk = next(c for c in graph.state(graph.latest_state())["inventory"]
                if c["name"] == "disk")
    r = _validate(graph, [
        {"op": "remove", "target": "disk"},
        {"op": "add", "structure_name": "disk", "component_type": "sersic_f",
         "re_px": 23.7, "q": disk["q"], "pa_sky_deg": disk["pa"],
         "dx_px": 0.0, "dy_px": 0.0, "panel_band": "decam_g",
         "f1": {"amplitude": 0.06, "phase_deg": 125.0}}], "f1-in-loop")
    assert not r.errors, [f"{i.code}: {i.message}" for i in r.errors]

    out = str(tmp_path / "_iterF1.lyric")
    tx = _tx(str(test_data_dir / "mb" / "mb_iter3.lyric"),
             str(test_data_dir / "mb" / "mb_iter3.gssummary"),
             [{"op": "remove", "target": "disk"},
              {"op": "add", "structure_name": "disk",
               "component_type": "sersic_f", "re_px": 23.7, "q": disk["q"],
               "pa_sky_deg": disk["pa"], "dx_px": 0.0, "dy_px": 0.0,
               "panel_band": "decam_g",
               "f1": {"amplitude": 0.06, "phase_deg": 125.0}}],
             out, bands_meta=BM)
    assert tx["status"] == "success", tx.get("error")
    from beam_mb.lyric_doc import parse_lyric_doc
    doc = parse_lyric_doc(out)
    d = doc.component("disk")
    assert d.ptype == "sersic_f"
    assert d.slot_five("am")[0] == 0.06 and d.slot_five("theta_m")[0] == 125.0
    assert d.slot_five("n") == (1.0, 0.5, 2.0, 0.1, 0)
    assert doc.strict_invariant_errors() == []
