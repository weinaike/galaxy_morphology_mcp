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


def test_init_beam_params_and_ablation_arm_persist(galaxy):
    """Ablation arm (paper): beam params + arm flags live in graph meta and
    survive persistence — every later call reads the same arm."""
    g = BeamGraph.init(str(galaxy), str(galaxy / "_iter1.feedme"), stage1={},
                       beam_width=3, n_max=7, stagnation_max=2,
                       ablations={"no_global_state": True, "single_agent": True})
    m = g.g.graph["meta"]
    assert (m["W"], m["N_max"], m["stagnation_max"]) == (3, 7, 2)
    assert m["ablations"] == {"no_global_state": True, "single_agent": True}

    g2 = BeamGraph.load(str(galaxy))
    m2 = g2.g.graph["meta"]
    assert (m2["W"], m2["N_max"], m2["stagnation_max"]) == (3, 7, 2)
    assert m2["ablations"]["no_global_state"] is True
    snap = g2.snapshot()
    assert snap["config"]["W"] == 3 and snap["config"]["N_max"] == 7
    assert snap["ablations"]["no_global_state"] is True


def test_snapshot_config_defaults_without_ablations(galaxy):
    g = _init(galaxy)
    snap = g.snapshot()
    assert snap["ablations"] == {}
    assert snap["config"]["W"] == 5 and snap["config"]["g_min"] == 0.3


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


def test_no_verdict_gate_ablation_arm(galaxy):
    """Arm no_verdict_gate (paper): metric-only best selection — a FAIL round
    with better BIC takes s*, but the verdict is still recorded verbatim."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    g.g.graph["meta"]["ablations"] = {"no_verdict_gate": True}
    g.add_pending({"sigma": 0.5, "expected_behavior_tag": "lens_add",
                   "primitives": [{"op": "add", "structure_name": "lens",
                                   "component_type": "sersic"}]},
                  source_session="s2", parent_label="A.1")
    aid = list(g.g.graph["pending"])[0]
    label = g.record_fit(_run_result(galaxy, bic_eff=900.0), action_id=aid,
                         verdict={"verdict": "FAIL", "failed_checks": ["re_inversion"],
                                  "swap_hint": "none"})
    assert g.g.graph["best_state"] == label          # FAIL took s* under the arm
    assert g.state(label)["verdict"]["verdict"] == "FAIL"  # verdict still recorded
    assert g.counters()["stagnation"] == 0           # best update reset stagnation


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


# ---------------------------------------- Plate0295 incident 2026-09-17
def test_combo_counts_exclude_subconverged(galaxy):
    """Cap accounting follows the Refutation validity rule: a [sub-converged]
    round is not a counted attempt, so a corrected re-run stays legal."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    combo = g.state("A.1")["combo_key"]
    g.add_pending({"sigma": 0.4,
                   "primitives": [{"op": "tune", "structure_name": "bulge",
                                   "param": "n", "value": 2.0, "toggle": 1}]},
                  source_session="s", parent_label="A.1")
    aid = g.pending_queue()[0]
    g.record_fit(_run_result(galaxy, bic_eff=1010.0, conv_flag="sub-converged"),
                 action_id=aid)
    assert g.combo_counts().get(combo) == 1                # valid rounds only
    assert g.combo_counts(valid_only=False).get(combo) == 2  # every fit that ran
    # the precheck still sees the combo as executed (a fit did run)
    assert all(e["combo"] != combo for e in g.never_executed_precheck())


def test_floor_discharge_after_valid_execution(galaxy):
    """Regression (Plate0295 A.12): a floor-flagged pending candidate must not
    suspend termination once the SAME combo has a valid (convergence ok)
    executed round testing the SAME floor hypothesis; a sub-converged round
    does not discharge it."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    n_release = [{"op": "tune", "structure_name": "bulge",
                  "param": "n", "value": 4.0, "toggle": 1}]
    g.add_pending({"sigma": 0.3, "code_flags": {"floor_n_release": True},
                   "primitives": n_release},
                  source_session="s", parent_label="A.1")
    floor_aid = g.pending_queue()[0]
    assert g.floor_blockers() == [floor_aid]

    # executor round lands sub-converged: floor must stay armed
    g.add_pending({"sigma": 0.3, "primitives": n_release},
                  source_session="s2", parent_label="A.1")
    sub_aid = g.pending_queue()[-1]
    g.record_fit(_run_result(galaxy, bic_eff=1005.0, conv_flag="sub-converged"),
                 action_id=sub_aid)
    assert g.floor_blockers() == [floor_aid]
    assert g.pending_record(floor_aid)["status"] == "pending"

    # a valid n-release on the same combo discharges the floor
    g.add_pending({"sigma": 0.3, "primitives": n_release},
                  source_session="s3", parent_label="A.1")
    ok_aid = g.pending_queue()[-1]
    g.record_fit(_run_result(galaxy, bic_eff=1002.0), action_id=ok_aid)
    assert g.floor_blockers() == []
    rec = g.pending_record(floor_aid)
    assert rec["status"] == "discarded"
    assert "FLOOR_SATISFIED" in rec["discard_reason"]
    kinds = [e["kind"] for e in g.g.graph["decision_log"]]
    assert "floor-discharge" in kinds
    # stagnation is not bumped by the discharge itself
    assert g.counters()["stagnation"] == 0


def test_floor_disk_re_discharge_requires_growth(galaxy):
    """floor_disk_re is satisfied only by a valid executed round that GREW
    the disk Re past its own parent's value — a shrink tune must leave the
    floor armed (parent_re=None would weaken the predicate to any re tune)."""
    g = _init(galaxy)
    g.record_fit(_run_result(galaxy, bic_eff=1000.0), verdict={"verdict": "PASS"})
    disk = next(c for c in g.state("A.1")["inventory"]
                if c.get("name") in {"disk", "edgedisk"})
    # effective Re (the raw inventory stores Rs for expdisk/edgedisk)
    re0_raw = disk.get("re_effective")
    if re0_raw is None:
        re0_raw = disk.get("re")
        if (disk.get("type") or "").lower() in ("expdisk", "edgedisk") \
                and re0_raw is not None:
            re0_raw = float(re0_raw) * 1.68
    re0 = float(re0_raw)

    def re_tune(value):
        return [{"op": "tune", "structure_name": "disk",
                 "param": "re_px", "value": value, "toggle": 1}]

    g.add_pending({"sigma": 0.4, "code_flags": {"floor_disk_re": True},
                   "primitives": re_tune(re0 * 1.3)},
                  source_session="s", parent_label="A.1")
    floor_aid = g.pending_queue()[0]
    assert g.floor_blockers() == [floor_aid]

    # valid executed round that SHRANK the disk Re: floor must stay armed
    g.add_pending({"sigma": 0.4, "primitives": re_tune(re0 * 0.7)},
                  source_session="s2", parent_label="A.1")
    shrink_aid = g.pending_queue()[-1]
    g.record_fit(_run_result(galaxy, bic_eff=1005.0), action_id=shrink_aid)
    assert g.floor_blockers() == [floor_aid]
    assert g.pending_record(floor_aid)["status"] == "pending"

    # window case: for an expdisk parent the raw inventory stores Rs, but a
    # Re-units tune in (Rs, Re) is a TRUE SHRINK of the effective radius —
    # it must NOT discharge the floor (the naive Rs comparison would)
    is_exp = (disk.get("type") or "").lower() in ("expdisk", "edgedisk")
    if is_exp:
        rs0 = float(disk.get("re"))
        window_val = rs0 * 1.3          # between Rs and Re = Rs*1.68
        g.add_pending({"sigma": 0.4, "primitives": re_tune(window_val)},
                      source_session="s2b", parent_label="A.1")
        window_aid = g.pending_queue()[-1]
        g.record_fit(_run_result(galaxy, bic_eff=1006.0), action_id=window_aid)
        assert g.floor_blockers() == [floor_aid], \
            "a tune inside (Rs, Re) is a true shrink — floor must stay armed"

    # valid executed round that GREW the disk Re: floor discharged
    g.add_pending({"sigma": 0.4, "primitives": re_tune(re0 * 1.3)},
                  source_session="s3", parent_label="A.1")
    grow_aid = g.pending_queue()[-1]
    g.record_fit(_run_result(galaxy, bic_eff=1002.0), action_id=grow_aid)
    assert g.floor_blockers() == []
    assert "FLOOR_SATISFIED" in g.pending_record(floor_aid)["discard_reason"]
