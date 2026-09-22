"""Tests for physicality.py (deterministic checks + verdict merge) and the
KILOGAS_319 shakedown fixes B/C/D (repair budget, best-path aging gate,
temporary-constraint lifecycle)."""

import pytest

from beam.digest import build_local_state_description
from beam.enqueue import _reorder_queue, ingest
from beam.graph import BeamGraph
from beam.physicality import compute_mech_checks, merge_verdict

from tests.beam.test_vlm_enqueue import cand  # Candidate factory


def _comp(name, re, q=0.8, x=100.0, y=100.0, ctype="sersic", mag=18.0, n=1.0,
          pa=0.0):
    return {"name": name, "type": ctype, "x": x, "y": y, "mag": mag,
            "re": re, "re_effective": re, "n": n, "q": q, "pa": pa,
            "toggles": {}}


def _state(inv, zombies=None):
    return {"inventory": inv, "zombies": zombies or [], "artifacts": {}}


def _checks(graph, inv, zombies=None):
    return compute_mech_checks(graph, _state(inv, zombies))


def _hards(checks):
    return [c for c in checks if c["severity"] == "hard"]


def _by(checks, kind):
    return [c for c in checks if c["check"] == kind]


# ------------------------------------------------------------------ checks
def test_re_chain_inversion_hard(mini_graph):
    graph, _ = mini_graph
    inv = [_comp("disk", 4.0), _comp("bulge", 5.0)]
    hard = _by(_hards(_checks(graph, inv)), "re_chain")
    assert hard and "re_bulge" in hard[0]["detail"] and "re_disk" in hard[0]["detail"]


def test_re_chain_clean_passes(mini_graph):
    graph, _ = mini_graph
    inv = [_comp("disk", 40.0), _comp("lens", 20.0), _comp("bar", 8.0),
           _comp("bulge", 3.0), _comp("outerdisk", 80.0)]
    assert not _by(_checks(graph, inv), "re_chain")


def test_axis_ratio_limits(mini_graph):
    graph, _ = mini_graph
    # bar above the hard limit
    inv = [_comp("disk", 40.0), _comp("bar", 8.0, q=0.7)]
    hard = _by(_hards(_checks(graph, inv)), "axis_ratio")
    assert any("bar q=0.7" in c["detail"] for c in hard)
    # thin-line degeneracy at the q floor
    inv = [_comp("disk", 40.0), _comp("companion", 5.0, q=0.04)]
    hard = _by(_hards(_checks(graph, inv)), "axis_ratio")
    assert any("thin-line" in c["detail"] for c in hard)
    # bulge prior violation is only a note (note band 0.3-0.4)
    inv = [_comp("disk", 40.0), _comp("bulge", 3.0, q=0.35)]
    checks = _checks(graph, inv)
    assert not _by(_hards(checks), "axis_ratio")
    assert any(c["severity"] == "note" and "bulge q=0.35" in c["detail"]
               for c in _by(checks, "prior"))


def test_containment_hard(mini_graph):
    graph, _ = mini_graph
    graph.g.graph["meta"]["fit_region"] = [1.0, 297.0, 1.0, 297.0]
    inv = [_comp("disk", 100.0, x=149.0, y=149.0)]  # 2*Re=200 -> 349 > 297
    hard = _by(_hards(_checks(graph, inv)), "containment")
    assert hard and "2*Re" in hard[0]["detail"]
    inv = [_comp("disk", 60.0, x=149.0, y=149.0)]   # 298 -> 269 fits... 149+120=269
    assert not _by(_hards(_checks(graph, inv)), "containment")


def test_containment_pa_q_aware(mini_graph):
    """KILOGAS_319 regression: a flat outerdisk with its major axis on the
    diagonal does NOT leave the region even though 2*Re exceeds the half-side
    (A.10: Re=87.2, q=0.274, PA=-41.3 -> old isotropic check false-FIREd)."""
    graph, _ = mini_graph
    graph.g.graph["meta"]["fit_region"] = [1.0, 297.0, 1.0, 297.0]
    inv = [_comp("outerdisk", 87.2, q=0.274, pa=-41.3, x=148.9, y=148.9)]
    assert not _by(_hards(_checks(graph, inv)), "containment")
    # same size but round (q=1) or axis-aligned (PA=0) still fails
    assert _by(_hards(_checks(graph, [_comp("outerdisk", 87.2, q=1.0, pa=-41.3,
                                            x=148.9, y=148.9)])), "containment")
    assert _by(_hards(_checks(graph, [_comp("outerdisk", 87.2, q=0.274, pa=0.0,
                                            x=148.9, y=148.9)])), "containment")


def test_concentric_deviance_hard(mini_graph):
    graph, _ = mini_graph
    inv = [_comp("disk", 40.0, x=100.0, y=100.0), _comp("bar", 8.0, x=105.0, y=100.0)]
    hard = _by(_hards(_checks(graph, inv)), "concentric")
    assert hard and "bar" in hard[0]["detail"]


def test_concentric_agn_psf_checked_companion_ignored(mini_graph):
    """jwst/104 retrospective: the AGN psf point core is a chain member and its
    fitted centre is checked against the anchor; a psf COMPANION at an offset
    position is not a chain member and is never flagged."""
    graph, _ = mini_graph
    inv = [_comp("disk", 40.0, x=100.0, y=100.0),
           _comp("agn", 0.0, x=100.5, y=100.2, ctype="psf", mag=25.0),
           _comp("companion", 0.0, x=140.0, y=90.0, ctype="psf", mag=24.0)]
    assert not _by(_hards(_checks(graph, inv)), "concentric")
    inv[1] = _comp("agn", 0.0, x=103.5, y=100.0, ctype="psf", mag=25.0)
    hard = _by(_hards(_checks(graph, inv)), "concentric")
    assert hard and "agn" in hard[0]["detail"]


def test_degeneracy_hard(mini_graph):
    graph, _ = mini_graph
    inv = [_comp("disk", 110.0), _comp("outerdisk", 115.0)]
    hard = _by(_hards(_checks(graph, inv)), "degeneracy")
    assert hard and "degeneracy" in hard[0]["detail"]
    inv = [_comp("disk", 110.0), _comp("outerdisk", 160.0)]
    assert not _by(_hards(_checks(graph, inv)), "degeneracy")


def test_zombie_is_note_only(mini_graph):
    graph, _ = mini_graph
    inv = [_comp("disk", 40.0, mag=15.0), _comp("companion", 5.0, mag=23.0)]
    checks = _checks(graph, inv, zombies=["companion"])
    notes = _by(checks, "zombie")
    assert notes and notes[0]["severity"] == "note"
    assert not _by(_hards(checks), "zombie")


# ------------------------------------------------------------------- merge
def test_merge_verdict_hard_vetoes_pass():
    mech = [{"severity": "hard", "check": "axis_ratio", "detail": "bar q=0.7"}]
    merged, vetoed = merge_verdict(mech, {"verdict": "PASS", "failed_checks": []})
    assert vetoed and merged["verdict"] == "FAIL" and merged["mech_veto"] is True
    assert merged["failed_checks"][0].startswith("[mech-hard] bar q=0.7")


def test_merge_verdict_notes_keep_pass():
    mech = [{"severity": "note", "check": "zombie", "detail": "companion"}]
    merged, vetoed = merge_verdict(mech, {"verdict": "PASS", "failed_checks": ["[note] x"]})
    assert not vetoed and merged["verdict"] == "PASS"
    assert merged["failed_checks"] == ["[note] x", "[mech-note] companion"]


def test_merge_verdict_appends_to_vlm_fail():
    mech = [{"severity": "hard", "check": "containment", "detail": "disk 2*Re"}]
    merged, vetoed = merge_verdict(
        mech, {"verdict": "FAIL", "failed_checks": ["[hard] visual onion"]})
    assert vetoed and merged["verdict"] == "FAIL"
    assert merged["failed_checks"][0] == "[hard] visual onion"
    assert merged["failed_checks"][1].startswith("[mech-hard]")


# ------------------------------------------------- graph integration (defect A)
def test_apply_verdict_mech_veto_blocks_best(mini_graph):
    graph, label = mini_graph
    graph.g.nodes[label]["mech_checks"] = [
        {"severity": "hard", "check": "axis_ratio", "detail": "bar q=0.9"}]
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": []})
    v = graph.state(label)["verdict"]
    assert v["verdict"] == "FAIL" and v["mech_veto"] is True
    # the surveyor's original verdict is preserved verbatim
    assert graph.state(label)["verdict_vlm"] == {"verdict": "PASS", "failed_checks": []}
    assert graph.g.graph.get("best_state") is None


def test_record_fit_computes_mech_checks(mini_graph):
    graph, label = mini_graph
    assert isinstance(graph.state(label).get("mech_checks"), list)


def test_digest_renders_authoritative_mech_table(mini_graph):
    graph, label = mini_graph
    graph.g.nodes[label]["mech_checks"] = [
        {"severity": "hard", "check": "axis_ratio", "detail": "bar q=0.9 > 0.6"}]
    text, _triggers = build_local_state_description(graph, label)
    assert "AUTHORITATIVE" in text and "bar q=0.9 > 0.6" in text


# --------------------------------------------------- repair budget (defect B)
def test_mark_failure_does_not_bump_stagnation(mini_graph):
    graph, label = mini_graph
    graph.add_pending({"action_id": "x", "sigma": 0.5, "score": 0.5,
                       "primitives": []}, "s1", label)
    before = graph.counters().get("stagnation", 0)
    graph.mark_failed("x", "crash")
    assert graph.counters()["n_failed"] == 1
    assert graph.counters().get("stagnation", 0) == before


def test_repair_budget_grant_and_cap(mini_graph):
    graph, label = mini_graph
    graph.g.graph["meta"]["N_max"] = graph.n_total()  # budget exhausted
    term = graph.termination_check()
    assert "budget_exhausted" in term["conditions"] and term["budget_left"] == 0

    rb = graph.grant_repair_budget(2, reason="verifier FAIL: bound pins")
    assert rb["granted"] == 2 and graph.budget_left() == 2
    assert "budget_exhausted" not in graph.termination_check()["conditions"]

    # cumulative cap 4: requesting 10 more yields only 2 extra (granted 2 -> 4)
    rb = graph.grant_repair_budget(10, reason="more")
    assert rb["granted"] == 4 and graph.budget_left() == 4  # N_max(1)+4-n_total(1)


def test_repair_budget_migration_for_old_graphs(mini_graph):
    import os

    graph, _ = mini_graph
    galaxy_dir = os.path.dirname(os.path.dirname(graph.path))
    del graph.g.graph["repair_budget"]
    graph.commit()
    g2 = BeamGraph.load(galaxy_dir)
    assert g2.g.graph["repair_budget"]["granted"] == 0
    assert g2.budget_left() == g2.g.graph["meta"]["N_max"] - g2.n_total()


# ------------------------------------------------- best-path aging (defect C)
def _add_pending(graph, parent, aid, score, age):
    graph.add_pending({"action_id": aid, "sigma": 0.6, "score": score,
                       "primitives": []}, "s", parent)
    graph.g.graph["pending"][aid]["age_counter"] = age


def test_aging_bonus_only_on_best_path(mini_graph):
    graph, label = mini_graph
    # second state off the best branch: A.2 with parent A.0
    a2 = graph.record_fit({
        "input_param_file": graph.state(label)["artifacts"]["feedme"],
        "output_param_file": graph.state(label)["artifacts"]["galfit_nn"],
        "image_file": "cmp.png", "summary_file": "s.md",
        "round_status_file": "r.json",
        "fit_statistics": {"bic_eff": 2000.0, "convergence": {"flag": "ok"}},
    }, parent_label="A.0")
    # best path = latest chain fallback: A.2 -> A.0 (no best_state yet)
    assert graph.best_path_labels() == {"A.2", "A.0"}

    # off-path candidate has the HIGHER base score and age; on-path wins via aging
    _add_pending(graph, "A.2", "on_path", 0.50, age=10)   # eff 0.50 + 0.10
    _add_pending(graph, "A.1", "off_path", 0.55, age=10)  # eff 0.55 (no bonus)
    _reorder_queue(graph)
    assert graph.pending_queue()[0] == "on_path"

    # settle a best on the other branch: aging follows the best path
    graph.apply_verdict("A.2", {"verdict": "FAIL", "failed_checks": []})
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": []})
    assert graph.g.graph["best_state"] == label  # A.1 (BIC 1000 < 2000... FAIL A.2)
    _reorder_queue(graph)
    assert graph.pending_queue()[0] == "off_path"  # now on the best path


def test_snapshot_flags_off_best_path(mini_graph):
    graph, label = mini_graph
    _add_pending(graph, "A.0", "root_child", 0.6, age=0)
    snap = graph.snapshot()
    entry = next(q for q in snap["queue"] if q["action_id"] == "root_child")
    # latest state is A.1 -> path {A.1, A.0}: parent A.0 IS on the path
    assert entry["off_best_path"] is False


# ------------------------------------------- constraint lifecycle (defect D)
def test_set_constraint_add_blocks_and_remove_allows(mini_graph):
    graph, label = mini_graph
    out = graph.set_temporary_constraint("add", "companion exclusion",
                                         ["companion"], issued="2026-09-04")
    assert out["active"] and out["active"][0]["forbid_structures"] == ["companion"]

    prims = [{"op": "add", "add": {"structure_name": "companion",
                                   "component_type": "sersic", "re_px": 2.0,
                                   "x_px": 200.0, "y_px": 100.0}}]
    res = ingest(graph, [cand(prims, sigma=0.7, tag="comp_add")],
                 session_id="s1", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "E_TEMP_CONSTRAINT"

    graph.set_temporary_constraint("remove", "companion exclusion")
    res = ingest(graph, [cand(prims, sigma=0.7, tag="comp_add2")],
                 session_id="s1", parent_label=label)
    assert not any(d["reason"] == "E_TEMP_CONSTRAINT" for d in res.discarded)
    assert any(e["tag"] == "comp_add2" for e in res.enqueued)

    snap = graph.snapshot()
    assert snap["temporary_constraints"] == []  # deactivated ones are hidden


def test_set_constraint_add_is_idempotent(mini_graph):
    graph, _ = mini_graph
    graph.set_temporary_constraint("add", "x-exclusion", ["lens"])
    graph.set_temporary_constraint("add", "x-exclusion", ["lens"])
    active = [t for t in graph.g.graph["temporary_constraints"] if t.get("active")]
    assert len(active) == 1


# --------------------------------------- floor suspension (KILOGAS_296 A.2-c1)
def _add_floor(graph, parent, aid):
    return graph.add_pending(
        {"action_id": aid, "sigma": 0.2, "score": 0.5,
         "code_flags": {"floor_n_release": True},
         "primitives": [{"op": "tune", "tune": {"structure_name": "bulge",
                                                "param": "n", "toggle": 1}}]},
        "s1", parent)


def _hit_stagnation(graph):
    graph.counters()["stagnation"] = int(
        graph.g.graph["meta"].get("stagnation_max", 5))


def test_stagnation_suspended_by_pending_floor(mini_graph):
    graph, label = mini_graph
    graph.add_pending({"action_id": "plain_head", "sigma": 0.5, "score": 0.9,
                       "primitives": []}, "s1", label)
    _add_floor(graph, label, "floor_nrel")  # queue position 2, below the head
    _hit_stagnation(graph)
    term = graph.termination_check()
    assert "stagnation" in term["conditions"]
    assert term["suspended_by_floor"] is True and term["stop"] is False
    assert term["floor_blockers"] == ["floor_nrel"]
    # the mandatory floor outranks the plain queue head
    assert graph.next_action() == "floor_nrel"


def test_floor_suspension_lifts_after_execution(mini_graph):
    graph, label = mini_graph
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": []})  # settle best
    graph.add_pending({"action_id": "plain_tail", "sigma": 0.5, "score": 0.4,
                       "primitives": []}, "s1", label)  # keep the queue non-empty
    aid = _add_floor(graph, label, "floor_nrel")
    _hit_stagnation(graph)
    assert graph.termination_check()["suspended_by_floor"] is True
    graph.record_fit({
        "input_param_file": graph.state(label)["artifacts"]["feedme"],
        "output_param_file": graph.state(label)["artifacts"]["galfit_nn"],
        "image_file": "cmp.png", "summary_file": "s.md",
        "round_status_file": "r.json",
        "fit_statistics": {"bic_eff": 2000.0, "convergence": {"flag": "ok"}},
    }, action_id=aid, verdict={"verdict": "PASS"})  # BIC-worse PASS: stagnation stays
    term = graph.termination_check()
    assert term["suspended_by_floor"] is False and term["floor_blockers"] == []
    assert term["stop"] is True and "stagnation" in term["conditions"]


def test_budget_exhaustion_beats_floor(mini_graph):
    graph, label = mini_graph
    _add_floor(graph, label, "floor_nrel")
    _hit_stagnation(graph)
    graph.g.graph["meta"]["N_max"] = graph.n_total()  # budget_left -> 0
    term = graph.termination_check()
    assert "budget_exhausted" in term["conditions"]
    assert term["suspended_by_floor"] is False and term["stop"] is True


def test_stagnation_stop_without_floor_unchanged(mini_graph):
    graph, label = mini_graph
    graph.add_pending({"action_id": "plain_head", "sigma": 0.5, "score": 0.9,
                       "code_flags": {"diversity": True},
                       "primitives": []}, "s1", label)
    _hit_stagnation(graph)
    term = graph.termination_check()
    assert term["stop"] is True and term["suspended_by_floor"] is False
    assert graph.next_action() == "plain_head"  # plain queue-head behaviour
