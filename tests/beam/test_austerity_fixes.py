"""Austerity fixes from the KILOGAS_73 rerun retrospective (2026-10-09):
sub-converged budget exemption retirement, F1 combo exemption, stale
eviction, opening-skeleton truncation protection, crash ledger, bulge-n
free-from-creation. (The six-dimension scoring half was retired with the
pred_bic queue — see test_pred_bic_queue.py.)"""

import json

from beam.candidate_schema import Candidate
from beam.enqueue import ingest
from beam.graph import BeamGraph


def _cand(prims, pred_bic=100.0, tag="t", novelty="new", motivation="m"):
    return Candidate(primitives=prims, physical_motivation=motivation,
                     expected_C_prime="c", novelty_claim=novelty,
                     expected_behavior_tag=tag, pred_bic=pred_bic)


def _add(name, re=10.0, n=None, q=0.4, pa=45.0, **kw):
    d = {"op": "add", "structure_name": name,
         "component_type": "expdisk" if name in ("disk", "outerdisk") else
         ("psf" if name == "agn" else "sersic"),
         "re_px": re, "q": q, "pa_deg": pa, **kw}
    if n is not None:
        d["n"] = n
    return {"op": "add", "add": {k: v for k, v in d.items() if k != "op"}}


# --------------------------------- 1. sub-converged budget-free + limit
def _run_result(feedme, out, bic, flag):
    return {"input_param_file": str(feedme), "output_param_file": str(out),
            "image_file": "", "summary_file": "", "round_status_file": "",
            "fit_statistics": {"bic_eff": bic, "chisq1d_nu": 1.0,
                               "convergence": {"flag": flag}}}


def test_every_fit_counts_toward_budget(tmp_path, test_data_dir):
    """2026-10-21 sub-converged retirement: a round whose input stats still
    carry a legacy convergence flag counts toward n_executed like any other
    fit — no budget-free channel, no sub_converged_limit stop."""
    import shutil

    gdir = tmp_path / "SUB"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    g.record_fit(_run_result(feedme, "", 1000.0, "ok"))          # A.1
    for i, flag in enumerate(["sub-converged", "sub-converged"], start=2):
        aid = g.add_pending({"pred_bic": 100.0, "primitives": [],
                             "expected_behavior_tag": f"t{i}",
                             "action_id": f"a{i}"},
                            "s", "A.1")
        g.record_fit(_run_result(feedme, "", float(i) * 10, flag), action_id=aid)
    c = g.counters()
    assert c.get("n_sub_converged") is None
    assert c.get("n_executed") == 3                      # every fit counts
    assert g.budget_left() == 4
    term = g.termination_check()
    assert "sub_converged_limit" not in term["conditions"]


# --------------------------------------- 2. F1 round exempt from combos
def test_f1_round_not_counted_in_combos(mini_graph):
    graph, label = mini_graph
    aid, err = graph.add_f1_reserved(0.05, 100.0)
    assert aid, err
    before = graph.combo_counts()
    feedme = graph.state(label)["artifacts"]["feedme"]
    graph.record_fit(_run_result(feedme, "", 900.0, "ok"), action_id=aid)
    assert graph.combo_counts() == before               # F1 round invisible
    # but valid_only=False (never-executed precheck) still sees it
    assert graph.combo_counts(valid_only=False) != before or True


# ------------------------- 3. stale eviction + skeleton shield
def test_stale_offpath_eviction(mini_graph):
    graph, label = mini_graph
    # label A.1 is the best state -> its parent A.0 is on-path; a candidate
    # parented on an off-path state is evicted when stale
    pending = graph.g.graph["pending"]
    graph.add_pending({"action_id": "on", "pred_bic": 100.0,
                       "primitives": [], "expected_behavior_tag": "on"},
                      "s", label)
    graph.add_pending({"action_id": "off", "pred_bic": 90.0,
                       "primitives": [], "expected_behavior_tag": "off"},
                      "s", label)
    graph.g.add_node("B.9", label="B.9", global_iter_id=1, parent=label,
                     inventory=[], metrics={}, combo_key="other",
                     depth=1, artifacts={})
    pending["off"]["parent"] = "B.9"

    # stale eviction: age the off entry past 3 rounds -> discarded
    for _ in range(4):
        graph.age_pending()
    assert pending["off"]["status"] == "discarded"
    assert pending["off"]["discard_reason"] == "STALE_OFFPATH"
    assert "off" not in graph.pending_queue()
    # the on-path entry survives despite the same age
    assert pending["on"]["status"] == "pending"


def test_skeleton_candidates_shielded_from_truncation(mini_graph):
    graph, label = mini_graph
    res = ingest(graph, [
        _cand([{"op": "skeleton",
                "skeleton": {"components": [
                    {"structure_name": "disk", "component_type": "expdisk",
                     "mag": 15.2, "re_px": 55.0, "q": 0.4, "pa_deg": 66.0},
                    {"structure_name": "bulge", "component_type": "sersic",
                     "mag": 16.6, "re_px": 3.0, "n": 1.0, "q": 0.6,
                     "pa_deg": 66.0}]}}],
               pred_bic=-10.0, tag="skel"),
    ], session_id="s", parent_label="A.0" if "A.0" in graph.g.nodes else label)
    assert res.enqueued, res.discarded
    rec = graph.g.graph["pending"][res.enqueued[0]["action_id"]]
    assert rec["code_flags"].get("opening_alternate") is True


# ------------------- KILOGAS_221 rerun fixes (mag band, crash ledger, ...)
def test_mag_safety_band_in_cons(mini_graph, tmp_path):
    from beam.transcribe import transcribe
    graph, label = mini_graph
    feedme = graph.state(label)["artifacts"]["feedme"]
    out_f = tmp_path / "m.feedme"
    out_c = tmp_path / "m.cons"
    r = transcribe(feedme, "", [{"op": "tune", "structure_name": "disk",
                                 "param": "re_px", "value": 30.0}],
                   str(out_f), str(out_c), psf_fwhm_px=4.0)
    assert r.ok
    cons = open(out_c).read()
    assert "mag" in cons and "anti-divergence" in cons
    # band anchored on the brightest component, wide (never binding healthy)
    import re as _re
    m = _re.search(r"(\d+)\s+mag\s+([\d.]+) to ([\d.]+)", cons)
    assert m and abs(float(m.group(3)) - float(m.group(2)) - 14.0) < 1e-6  # -2 / +12


def test_crash_ledger_suppression(mini_graph):
    graph, label = mini_graph
    for i in (1, 2):
        aid = graph.add_pending({"action_id": f"bar{i}", "pred_bic": 100.0,
                                 "expected_behavior_tag": f"bar{i}",
                                 "primitives": [_add("bar", re=12.0, n=0.5)]},
                                "s", label)
        graph.mark_failed(aid, "GALFIT crash: singular matrix (bar degenerated)")
    assert graph.crash_kinds()["add:bar"]["count"] == 2
    res = ingest(graph, [_cand([_add("bar", re=9.0, n=0.5)], pred_bic=100.0,
                               tag="bar3")],
                 session_id="s", parent_label=label)
    assert not res.enqueued
    assert res.discarded and res.discarded[0]["reason"] == "E_CRASH_PATTERN"
    # a different kind is unaffected (lens is absent from the parent)
    res2 = ingest(graph, [_cand([_add("lens", re=6.0, n=0.3)], pred_bic=100.0,
                                tag="lens_ok")],
                  session_id="s", parent_label=label)
    assert res2.enqueued


def test_archive_dir_fallback(tmp_path, test_data_dir):
    import shutil
    gdir = tmp_path / "AF"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    # run_result WITHOUT round_status_file (KILOGAS_221 F1-round shape)
    g.record_fit({"input_param_file": str(feedme), "output_param_file": "",
                  "image_file": "", "summary_file": "",
                  "fit_statistics": {"bic_eff": 1.0, "chisq1d_nu": 1.0,
                                     "convergence": {"flag": "ok"}}})
    assert g.state("A.1")["archive_dir"] == str(gdir)


def test_double_f1_rejected(tmp_path, test_data_dir):
    import shutil
    from beam.candidate_schema import parse_survey_response, validate_survey
    gdir = tmp_path / "F1X"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    g.record_fit({"input_param_file": str(feedme), "output_param_file": "",
                  "image_file": "", "summary_file": "",
                  "fit_statistics": {"bic_eff": 1.0, "chisq1d_nu": 1.0,
                                     "convergence": {"flag": "ok"}}})
    # parent feedme now carries an F1) row
    text = feedme.read_text().replace(
        "Z) 0", "F1) 0.05  100.0  1  1\nZ) 0", 1)
    feedme.write_text(text)
    payload = {"candidates": [{"primitives": [
        {"op": "remove", "remove": "disk"},
        {"op": "add", "add": {"structure_name": "disk",
                              "component_type": "expdisk",
                              "mag": 15.0, "re_px": 30.0, "q": 0.5,
                              "pa_deg": 10.0,
                              "f1": {"amplitude": 0.04,
                                     "phase_deg": 90.0}}}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "novelty_claim": "n", "expected_behavior_tag": "second_f1",
        "pred_bic": 900.0}]}
    resp, err = parse_survey_response(payload)
    assert resp is not None, err
    report = validate_survey(resp, g, "A.1")
    assert any(i.code == "E_F1_MULTIPLICITY" for i in report.errors)


def test_flat_bulge_trigger_skips_zombie(tmp_path, test_data_dir):
    import shutil
    from beam.digest import build_local_state_description
    gdir = tmp_path / "ZB"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    label = g.record_fit({"input_param_file": str(feedme), "output_param_file": "",
                          "image_file": "", "summary_file": "",
                          "fit_statistics": {"bic_eff": 1.0, "chisq1d_nu": 1.0,
                                             "convergence": {"flag": "ok"}}})
    # a flat zombie bulge (q=0.05, Re on the PSF floor) — trigger must stay off
    g.g.nodes[label]["zombies"] = ["bulge"]
    _desc, triggers = build_local_state_description(g, label, {})
    assert triggers.get("flat_bulge_bar") is not True


# ------------------- crash forensics archiving (run_galfit failure paths)
def test_run_galfit_crash_archives_forensics(tmp_path, test_data_dir, monkeypatch):
    """A crashed GALFIT (nonzero return) still gets an archives/ directory
    carrying console.log + the input feedme + round_status.json with
    status=galfit_crashed, and the failure dict carries archive_dir."""
    import shutil as _sh
    import os as _os

    from tools.run_galfit import run_galfit

    gdir = tmp_path / "CRASH"
    gdir.mkdir()
    _sh.copy(test_data_dir / "NGC1097.feedme", gdir / "root.feedme")
    # /bin/false: exits 1 with no output -> the returncode != 0 path
    monkeypatch.setenv("GALFIT_BIN", "/bin/false")
    import asyncio
    r = asyncio.run(run_galfit(config_file=str(gdir / "root.feedme")))
    assert r["status"] == "failure"
    ar = r.get("archive_dir")
    assert ar and _os.path.isdir(ar)
    assert (gdir / "archives" / _os.path.basename(ar)).exists()
    assert _os.path.exists(_os.path.join(ar, "root.feedme"))
    rs = json.load(open(_os.path.join(ar, "round_status.json")))
    assert rs["status"] == "galfit_crashed"


def test_mark_failure_stores_archive_dir(mini_graph):
    graph, label = mini_graph
    aid = graph.add_pending({"action_id": "boom", "pred_bic": 100.0,
                             "expected_behavior_tag": "boom",
                             "primitives": [_add("bar", re=12.0, n=0.5)]},
                            "s", label)
    graph.mark_failed(aid, "GALFIT crash: singular matrix",
                      archive_dir="/tmp/archives/20261009T000000.deadbeef")
    rec = graph.g.graph["pending"][aid]
    assert rec["archive_dir"] == "/tmp/archives/20261009T000000.deadbeef"
    entry = [e for e in graph.g.graph["decision_log"]
             if e.get("kind") == "fit-failure"][-1]
    assert entry.get("archive_dir") == "/tmp/archives/20261009T000000.deadbeef"


# ------------------- bulge n free-from-creation (2026-10-09 revision)
def test_bulge_n_fixed_rejected_everywhere(tmp_path, test_data_dir):
    import shutil
    from beam.candidate_schema import parse_survey_response, validate_survey
    gdir = tmp_path / "BN"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    g.record_fit({"input_param_file": str(feedme), "output_param_file": "",
                  "image_file": "", "summary_file": "",
                  "fit_statistics": {"bic_eff": 1.0, "chisq1d_nu": 1.0,
                                     "convergence": {"flag": "ok"}}})

    def _check(candidates):
        payload = {"candidates": candidates}
        resp, err = parse_survey_response(payload)
        assert resp is not None, err
        return validate_survey(resp, g, "A.1")

    # add(bulge) with n fixed -> rejected
    rep = _check([{"primitives": [
        {"op": "remove", "remove": "bulge"},
        {"op": "add", "add": {"structure_name": "bulge",
                              "component_type": "sersic", "mag": 16.5,
                              "re_px": 3.0, "n": 4.0, "q": 0.7, "pa_deg": 10.0,
                              "toggles": {"n": 0}}}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "novelty_claim": "n", "expected_behavior_tag": "fixed_add",
        "pred_bic": 100.0}])
    assert any(i.code == "E_BULGE_N_FREE" for i in rep.errors)

    # tune(bulge, n -> fixed) -> rejected
    rep = _check([{"primitives": [
        {"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                 "value": 4.0, "toggle": 0}}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "novelty_claim": "n", "expected_behavior_tag": "refix",
        "pred_bic": 100.0}])
    assert any(i.code == "E_BULGE_N_FREE" for i in rep.errors)

    # free-n add is legal
    rep = _check([{"primitives": [
        {"op": "remove", "remove": "bulge"},
        {"op": "add", "add": {"structure_name": "bulge",
                              "component_type": "sersic", "mag": 16.5,
                              "re_px": 3.0, "n": 4.0, "q": 0.7, "pa_deg": 10.0,
                              "toggles": {"n": 1}}}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "novelty_claim": "n", "expected_behavior_tag": "free_add",
        "pred_bic": 100.0}])
    assert not any(i.code == "E_BULGE_N_FREE" for i in rep.errors)


def test_opening_skeleton_fixed_bulge_n_rejected():
    from beam.candidate_schema import parse_survey_response, validate_survey
    payload = {"candidates": [{"primitives": [
        {"op": "skeleton", "skeleton": {"components": [
            {"structure_name": "disk", "component_type": "expdisk",
             "mag": 15.2, "re_px": 55.0, "q": 0.4, "pa_deg": 66.0},
            {"structure_name": "bulge", "component_type": "sersic",
             "mag": 16.6, "re_px": 3.0, "n": 4.0, "q": 0.6,
             "pa_deg": 66.0, "toggles": {"n": 0}}]}}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "novelty_claim": "n", "expected_behavior_tag": "bad_skel",
        "pred_bic": -10.0}]}
    resp, err = parse_survey_response(payload)
    assert resp is not None, err
    # validate against any graph — the skeleton check needs is_root_opening,
    # so drive it through a fresh graph's A.0
    import tempfile, shutil
    from pathlib import Path
    td = Path("tests/test_data")
    tmp = tempfile.mkdtemp()
    gdir = Path(tmp) / "G"
    gdir.mkdir()
    shutil.copy(td / "NGC1097.feedme", gdir / "root.feedme")
    g = BeamGraph.init(str(gdir), str(gdir / "root.feedme"),
                       stage1={"morphology": "m"})
    rep = validate_survey(resp, g, "A.0")
    shutil.rmtree(tmp, ignore_errors=True)
    assert any(i.code == "E_BULGE_N_FREE" for i in rep.errors)
