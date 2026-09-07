"""Tests for src/beam/graph.py (BeamGraph state machine + persistence)."""

import json
import shutil

import pytest

from beam.graph import BeamGraph


@pytest.fixture
def galaxy(tmp_path, test_data_dir):
    """A fake galaxy dir with NGC1097 fixtures as input/output artefacts."""
    gdir = tmp_path / "GALAXY"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    shutil.copy(test_data_dir / "NGC1097_clean.07", gdir / "galfit.01")
    return gdir


def _run_result(galaxy, *, bic_eff, conv_flag="ok"):
    return {
        "status": "success",
        "input_param_file": str(galaxy / "_iter1.feedme"),
        "output_param_file": str(galaxy / "galfit.01"),
        "image_file": str(galaxy / "cmp.png"),
        "summary_file": str(galaxy / "summary.md"),
        "round_status_file": str(galaxy / "archives" / "20260904T000000.deadbeef" / "round_status.json"),
        "fit_statistics": {
            "chi2_nu": 1.1,
            "chisq1d_nu": 0.9,
            "bic1d": bic_eff + 100,
            "bic_eff": bic_eff,
            "convergence": {"flag": conv_flag, "frozen_free_params": [], "n_frozen": 0},
        },
    }


def _init(galaxy):
    return BeamGraph.init(
        str(galaxy), str(galaxy / "_iter1.feedme"),
        stage1={"morphology": "spiral", "bar": {"detected": False}},
        psf_fwhm_px=4.0, a_psf_px2=12.57,
    )


# ------------------------------------------------------------------- init
def test_init_creates_root_and_persists(galaxy):
    g = _init(galaxy)
    assert (galaxy / "beam_state" / "graph.json").exists()
    root = g.state("A.0")
    assert root["combo_key"] == "agn+bar+bulge+disk"
    assert root["depth"] == 0
    assert g.g.graph["meta"]["psf_fwhm_px"] == 4.0
    assert tuple(g.g.graph["meta"]["fit_region"]) == (37.0, 1467.0, 25.0, 1455.0)
    assert g.n_total() == 0

    g2 = BeamGraph.load(str(galaxy))
    assert "A.0" in g2.g.nodes
    assert g2.state("A.0")["combo_key"] == "agn+bar+bulge+disk"


# -------------------------------------------------------------- record fit
def test_record_first_fit_sets_best(galaxy):
    g = _init(galaxy)
    label = g.record_fit(_run_result(galaxy, bic_eff=1000.0),
                         verdict={"verdict": "PASS"})
    assert label == "A.1"
    s = g.state("A.1")
    assert s["parent"] == "A.0" and s["depth"] == 1
    assert s["is_best"] is True
    assert g.g.graph["best_state"] == "A.1"
    assert g.counters()["n_executed"] == 1
    assert g.counters()["stagnation"] == 0
    # fitted inventory from galfit.01 with names recovered from the feedme
    names = sorted(c["name"] for c in s["inventory"])
    assert names == ["agn", "bar", "bulge", "disk"]


def test_verdict_settles_after_record(galaxy):
    """The natural order fit -> record -> survey: verdict arrives later via
    apply_verdict and settles the best exactly once."""
    g = _init(galaxy)
    label = g.record_fit(_run_result(galaxy, bic_eff=1000.0))
    assert g.g.graph["best_state"] is None  # pending verdict
    g.apply_verdict(label, {"verdict": "PASS"})
    assert g.g.graph["best_state"] == "A.1"
    g.apply_verdict(label, {"verdict": "FAIL"})  # idempotent
    assert g.g.graph["best_state"] == "A.1"
    assert g.counters()["stagnation"] == 0


def test_record_fit_worse_bic_refutes_and_stagnates(galaxy):
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    g.add_pending({"sigma": 0.6, "expected_behavior_tag": "bar_add",
                   "expected_C_prime": "{disk,bulge,bar,agn}",
                   "primitives": [{"op": "add", "structure_name": "bar",
                                   "component_type": "sersic"}]},
                  source_session="s1", parent_label="A.1")
    aid = g.pending_queue()[0] if g.pending_queue() else list(g.g.graph["pending"])[0]
    label = g.record_fit(_run_result(galaxy, bic_eff=1050.0), action_id=aid,
                         verdict={"verdict": "PASS"})
    assert label == "A.2"
    assert g.g.graph["best_state"] == "A.1"  # no best update
    assert g.counters()["stagnation"] == 1
    refuted = g.g.graph["refuted_hypotheses"]
    assert any(r["reason"] == "bic_worse" and r["state"] == "A.2" for r in refuted)
    assert g.pending_record(aid)["status"] == "executed"
    assert aid not in g.pending_queue()


def test_record_fit_fail_verdict_never_best(galaxy):
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    g.add_pending({"sigma": 0.5, "expected_behavior_tag": "lens_add",
                   "primitives": [{"op": "add", "structure_name": "lens",
                                   "component_type": "sersic"}]},
                  source_session="s2", parent_label="A.1")
    aid = list(g.g.graph["pending"])[0]
    g.record_fit(_run_result(galaxy, bic_eff=900.0), action_id=aid,
                 verdict={"verdict": "FAIL", "failed_checks": ["re_inversion"], "swap_hint": "none"})
    # better BIC but FAIL verdict: must NOT take the best
    assert g.g.graph["best_state"] == "A.1"
    assert any(r["reason"] == "physicality_fail" for r in g.g.graph["refuted_hypotheses"])


def test_subconverged_round_is_quarantined(galaxy):
    """Regression (KILOGAS_353 A.6): a sub-converged round grounds no refutation,
    does not count as combo evidence, and does not update s*."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    g.add_pending({"sigma": 0.5, "expected_behavior_tag": "bulge_n_free",
                   "primitives": [{"op": "tune", "structure_name": "bulge",
                                   "param": "n", "value": 4.0, "toggle": 1}]},
                  source_session="s3", parent_label="A.1")
    aid = list(g.g.graph["pending"])[0]
    label = g.record_fit(
        _run_result(galaxy, bic_eff=1200.0, conv_flag="sub-converged"), action_id=aid)
    g.apply_verdict(label, {"verdict": "PASS"})
    assert g.g.graph["best_state"] == "A.1"
    assert g.g.graph["refuted_hypotheses"] == []
    kinds = [e["kind"] for e in g.g.graph["decision_log"]]
    assert "sub-converged-quarantine" in kinds


# --------------------------------------------------------- failure / discard
def test_mark_failed_and_discarded(galaxy):
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0))
    g.add_pending({"sigma": 0.4, "primitives": [{"op": "add", "structure_name": "agn",
                                                 "component_type": "psf"}]},
                  source_session="s", parent_label="A.1")
    aid = g.pending_queue()[0] if g.pending_queue() else list(g.g.graph["pending"])[0]
    g.mark_failed(aid, "galfit_timeout")
    assert g.pending_record(aid)["status"] == "failed"
    assert g.counters()["n_failed"] == 1 and g.n_total() == 2
    stag0 = g.counters()["stagnation"]
    g.add_pending({"sigma": 0.9, "primitives": []}, source_session="s", parent_label="A.1")
    aid2 = list(g.g.graph["pending"])[-1]
    g.mark_discarded(aid2, "R1_LEDGER_EQUIVALENT")
    assert g.pending_record(aid2)["status"] == "discarded"
    assert g.counters()["stagnation"] == stag0 + 1


# ------------------------------------------------------------- combo / precheck
LENS_FEEDME = "\n".join([
    "A) image.fits",
    "B) out.fits",
    "G) none",
    "H) 1 297 1 297",
    "# Component number: 1",
    "# STRUCTURE: disk",
    "0) expdisk",
    "1) 148.0 148.0 1 1",
    "3) 16.0 1",
    "4) 12.0 1",
    "9) 0.7 1",
    "10) 25.0 1",
    "Z) 0",
    "# Component number: 2",
    "# STRUCTURE: bulge",
    "0) sersic",
    "1) 148.0 148.0 1 1",
    "3) 18.0 1",
    "4) 2.0 1",
    "5) 4.0 1",
    "9) 0.9 1",
    "10) 25.0 1",
    "Z) 0",
    "# Component number: 3",
    "# STRUCTURE: lens",
    "0) sersic",
    "1) 148.0 148.0 1 1",
    "3) 18.5 1",
    "4) 5.0 1",
    "5) 0.3 1",
    "9) 0.85 1",
    "10) 25.0 1",
    "Z) 0",
    "# Component number: 4",
    "0) sky",
    "1) 0.0004 0",
    "Z) 0",
])


def test_combo_counts_and_never_executed_precheck(galaxy):
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0))
    combo = "bulge+disk+lens"
    out = galaxy / "galfit.02"
    out.write_text(LENS_FEEDME, encoding="utf-8")
    for i in range(2):
        g.add_pending({"sigma": 0.7, "combo_key": combo,
                       "primitives": [{"op": "add", "structure_name": "lens",
                                       "component_type": "sersic"}]},
                      source_session=f"s{i}", parent_label="A.1")
    pending = [a for a, r in g.g.graph["pending"].items() if r["status"] == "pending"]
    assert g.g.graph["proposal_counts"][combo] == 2
    assert {"combo": combo, "proposed": 2} in g.never_executed_precheck()
    # executing one clears the precheck entry (fitted inventory carries the combo)
    rr = _run_result(galaxy, bic_eff=990.0)
    rr["output_param_file"] = str(out)
    rr["input_param_file"] = str(out)
    g.record_fit(rr, action_id=pending[0])
    assert all(e["combo"] != combo for e in g.never_executed_precheck())
    assert g.combo_counts().get(combo) == 1


# ------------------------------------------------------------------ rollback
def test_rollback_edges_detected(galaxy):
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0))
    g.add_pending({"sigma": 0.3, "primitives": [{"op": "remove", "target": "bar"}]},
                  source_session="s", parent_label="A.1")
    aid = list(g.g.graph["pending"])[0]
    g.record_fit(_run_result(galaxy, bic_eff=1005.0), action_id=aid)
    edges = g.rollback_edges()
    assert any(e["action_id"] == aid and e["kind"] == "remove-only" for e in edges)


# ----------------------------------------------------------------- persistence
def test_corrupt_json_falls_back_to_bak(galaxy):
    """``.bak`` holds the previous revision; corruption recovers to it."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0),
                 verdict={"verdict": "PASS"})             # bak <- A.0-only rev
    g.record_fit(_run_result(galaxy, bic_eff=995.0),
                 verdict={"verdict": "PASS"})             # bak <- A.1 rev
    graph_file = galaxy / "beam_state" / "graph.json"
    graph_file.write_text("{corrupted", encoding="utf-8")
    g2 = BeamGraph.load(str(galaxy))
    assert "A.0" in g2.g.nodes and "A.1" in g2.g.nodes  # recovered previous rev
    assert g2.g.graph["best_state"] == "A.2"


def test_atomic_commit_leaves_valid_json(galaxy):
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0))
    with open(galaxy / "beam_state" / "graph.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["directed"] is True
    labels = {n["id"] for n in data["nodes"]}
    assert {"A.0", "A.1"} <= labels


# ----------------------------------------------------------------- traversal
def test_traversal_orders_states_and_crashes(galaxy):
    """Execution-ordered walk: root first, states by iter id, crashes (no
    node) interleaved at their consumed iter position."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0),
                 verdict={"verdict": "PASS"})              # A.1, iter 1
    # simulate an applied-then-crashed candidate at iter 2 (apply_candidate
    # consumes the iter id into the counters before the fit runs)
    g.add_pending({"action_id": "crashy", "sigma": 0.5, "score": 0.5,
                   "primitives": [], "expected_behavior_tag": "boom"}, "s1", "A.1")
    rec = g.g.graph["pending"]["crashy"]
    rec["iter_id"] = 2
    g.counters()["global_iter_id"] = 2
    g.mark_failed("crashy", "gaussj: Singular Matrix-2")
    g.record_fit(_run_result(galaxy, bic_eff=990.0),
                 verdict={"verdict": "PASS"})              # A.2, iter 3

    t = g.traversal()
    assert [e["iter"] for e in t] == [0, 1, 2, 3]
    assert t[0]["label"] == "A.0" and t[0]["action"] == ""
    assert t[1]["label"] == "A.1" and t[1]["is_best"] is False
    assert t[2]["kind"] == "failed" and t[2]["verdict"] == "CRASH"
    assert t[2]["action"] == "crashy" and t[2]["tag"] == "boom"
    assert t[3]["label"] == "A.2" and t[3]["is_best"] is True
    # surfaced in the beam_status snapshot as well
    assert g.snapshot()["traversal"] == t
