"""Tests for physicality.py (deterministic checks + verdict merge) and the
KILOGAS_319 shakedown fixes B/C/D (repair budget, best-path aging gate,
temporary-constraint lifecycle)."""

import pytest

from beam.digest import build_local_state_description
from beam.enqueue import _reorder_queue, ingest
from beam.graph import BeamGraph
from beam.physicality import compute_mech_checks, merge_verdict

from tests.beam.test_vlm_enqueue import cand  # Candidate factory


def _comp(name, re, q=0.8, x=100.0, y=100.0, ctype="sersic", mag=18.0, n=1.0):
    return {"name": name, "type": ctype, "x": x, "y": y, "mag": mag,
            "re": re, "re_effective": re, "n": n, "q": q, "pa": 0.0,
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
    # bulge prior violation is only a note
    inv = [_comp("disk", 40.0), _comp("bulge", 3.0, q=0.4)]
    checks = _checks(graph, inv)
    assert not _by(_hards(checks), "axis_ratio")
    assert any(c["severity"] == "note" and "bulge q=0.4" in c["detail"]
               for c in _by(checks, "prior"))


def test_containment_hard(mini_graph):
    graph, _ = mini_graph
    graph.g.graph["meta"]["fit_region"] = [1.0, 297.0, 1.0, 297.0]
    inv = [_comp("disk", 100.0, x=149.0, y=149.0)]  # 2*Re=200 -> 349 > 297
    hard = _by(_hards(_checks(graph, inv)), "containment")
    assert hard and "2*Re" in hard[0]["detail"]
    inv = [_comp("disk", 60.0, x=149.0, y=149.0)]   # 298 -> 269 fits... 149+120=269
    assert not _by(_hards(_checks(graph, inv)), "containment")


def test_concentric_deviance_hard(mini_graph):
    graph, _ = mini_graph
    inv = [_comp("disk", 40.0, x=100.0, y=100.0), _comp("bar", 8.0, x=105.0, y=100.0)]
    hard = _by(_hards(_checks(graph, inv)), "concentric")
    assert hard and "bar" in hard[0]["detail"]


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
