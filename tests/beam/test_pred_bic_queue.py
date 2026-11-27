"""The pred_bic queue (2026-11-27 simplification): ONE ranking rule —
ascending pred_bic, stable on ties. Cold-start skeleton batches are
normalised mechanically from the VLM's array order; the mech verdict is the
only verdict; code_flags shield from truncation/preemption but never
reorder; the legacy conditional filters (E_CRASH_PATTERN / CLASS_CAP /
CELL_DUP / E_TEMP_CONSTRAINT) are unchanged."""

import json

import pytest

from beam.candidate_schema import Candidate
from beam.enqueue import ingest, _order_queue, _truncate_queue
from beam.graph import BeamGraph


def _cand(prims, pred_bic, tag):
    return Candidate(primitives=prims, physical_motivation="m",
                     expected_C_prime="c", novelty_claim="new",
                     expected_behavior_tag=tag, pred_bic=pred_bic)


def _add(name, re=10.0, n=None, q=0.4, pa=45.0, ctype=None):
    d = {"op": "add", "structure_name": name,
         "component_type": ctype or ("psf" if name == "agn" else "sersic"),
         "re_px": re, "q": q, "pa_deg": pa}
    if n is not None:
        d["n"] = n
    return {"op": "add", "add": {k: v for k, v in d.items() if k != "op"}}


def _tune_pa(value):
    """A disk-PA tune landing in a distinct cell bin (4x45deg; no CELL_DUP)."""
    return [{"op": "tune", "tune": {"structure_name": "disk",
                                    "param": "pa_deg", "value": value}}]


# ------------------------------------------------------------ 1. ordering
def test_queue_orders_by_pred_bic_ascending(mini_graph):
    graph, label = mini_graph
    res = ingest(graph, [
        _cand(_tune_pa(70.0), 500.0, "t_late"),
        _cand(_tune_pa(-70.0), 100.0, "t_first"),
        _cand(_tune_pa(10.0), 300.0, "t_mid"),
    ], session_id="s", parent_label=label)
    assert len(res.enqueued) == 3, res.discarded
    order = [graph.g.graph["pending"][a]["expected_behavior_tag"]
             for a in graph.pending_queue()]
    # the 500/300/100 insertion order becomes [100, 300, 500]
    assert order == ["t_first", "t_mid", "t_late"]


def test_ties_keep_insertion_order(mini_graph):
    graph, label = mini_graph
    ingest(graph, [
        _cand(_tune_pa(-70.0), 100.0, "first_tie"),
        _cand(_tune_pa(70.0), 100.0, "second_tie"),
        _cand(_tune_pa(10.0), 50.0, "leads"),
    ], session_id="s", parent_label=label)
    order = [graph.g.graph["pending"][a]["expected_behavior_tag"]
             for a in graph.pending_queue()]
    # equal pred_bic keep the batch order (Python stable sort)
    assert order == ["leads", "first_tie", "second_tie"]


# ------------------------------------------- 2. skeleton batch normalisation
def test_skeleton_batch_normalised_from_array_order(mini_graph):
    """Declared pred_bic values of round-0 skeleton candidates are IGNORED:
    the code encodes the array order as i-(K-1) (K=3 -> -2, -1, 0)."""
    graph, label = mini_graph
    parent = "A.0" if "A.0" in graph.g.nodes else label
    skel = lambda names, tag, junk: _cand(
        [{"op": "skeleton", "skeleton": {"components": [
            {"structure_name": n,
             "component_type": "sersic",
             "mag": 15.0 + i, "re_px": 30.0 - 5 * i, "n": 1.0,
             "q": 0.6, "pa_deg": 30.0}
            for i, n in enumerate(names)]}}],
        junk, tag)
    res = ingest(graph, [
        skel(["disk", "bulge", "bar"], "top", junk=999.0),    # declared junk
        skel(["disk", "bulge"], "mid", junk=-999.0),
        skel(["disk"], "alt", junk=42.0),
    ], session_id="s", parent_label=parent)
    assert len(res.enqueued) == 3, res.discarded
    q = graph.pending_queue()
    preds = [graph.g.graph["pending"][a]["pred_bic"] for a in q[:3]]
    assert preds == [-2.0, -1.0, 0.0]


def test_opening_alternates_negative_encoding(tmp_path, test_data_dir):
    """The beam_init opening_candidates_json path encodes the list order as
    i-(K-1) per entry: with K=3 the entries get -2/-1/0; rank 1 (the first
    fit) is not enqueued, so the alternates carry -1 and 0."""
    import shutil

    gdir = tmp_path / "ALT"
    gdir.mkdir()
    feedme = gdir / "_iter1.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    openings = {"candidates": [
        {"inventory": ["disk", "bulge", "bar"],
         "rationale": "root"},
        {"inventory": ["disk", "bulge", "agn"],
         "rationale": "alt2",
         "primitives": [{"op": "remove", "target": "bar"}]},
        {"inventory": ["disk", "bar", "agn"],
         "rationale": "alt3",
         "primitives": [{"op": "remove", "target": "bulge"}]},
    ]}
    g = BeamGraph.init(str(gdir), str(feedme),
                       stage1={"morphology": "m",
                               "opening_candidates": openings})
    g.record_fit({
        "input_param_file": str(feedme), "output_param_file": "",
        "image_file": "", "summary_file": "", "round_status_file": "",
        "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 1.0,
                           "convergence": {"flag": "ok"}},
    })
    alts = g.enqueue_opening_alternates("A.1")
    assert len(alts) == 2
    preds = [g.g.graph["pending"][a["action_id"]]["pred_bic"] for a in alts]
    assert preds == [-1.0, 0.0]          # i=1 -> -1, i=2 -> 0 (K=3)


# ------------------------------------------------------------- 3. F1 head
def test_f1_pred_bic_zero_lands_at_queue_head(mini_graph):
    graph, label = mini_graph
    ingest(graph, [_cand([_add("bar", re=6.0, n=0.5)], 50.0, "cheap"),
                   _cand([_add("agn", ctype="psf")], 10.0, "cheaper")],
           session_id="s", parent_label=label)
    aid, err = graph.add_f1_reserved(0.05, 90.0)
    assert aid and not err
    rec = graph.g.graph["pending"][aid]
    assert rec["pred_bic"] == 0.0
    # ascending order: 0.0 is smaller than every positive pred_bic, so the
    # reserved F1 slot naturally takes the queue head
    assert graph.next_action() == aid
    assert graph.pending_queue()[0] == aid


# ------------------------------------------------- 4. mech-only verdict path
def test_mech_hard_settles_fail_without_vlm(mini_graph):
    graph, label = mini_graph
    graph.g.nodes[label]["verdict"] = None       # un-settle (was auto-settled)
    graph.g.nodes[label]["mech_checks"] = [
        {"severity": "hard", "check": "axis_ratio", "detail": "bar q=0.9"}]
    graph.g.graph["best_state"] = None
    graph._settle_mech_verdict(
        label, graph.g.nodes[label]["mech_checks"],
        graph.g.nodes[label].get("inventory") or [],
        parent_label=graph.g.nodes[label].get("parent"))
    v = graph.state(label)["verdict"]
    assert v["verdict"] == "FAIL" and v["mech_veto"] is True
    assert "verdict_vlm" not in graph.g.nodes[label]
    assert graph.g.graph.get("best_state") is None


def test_clean_mech_table_settles_pass(mini_graph):
    graph, label = mini_graph
    v = graph.state(label)["verdict"]
    assert v["verdict"] == "PASS"
    assert not v.get("mech_veto")
    assert graph.g.graph["best_state"] == label


# --------------------------------------------------- 5. truncation protects
def test_truncation_keeps_code_flagged_entries(mini_graph):
    graph, label = mini_graph
    from beam.candidate_schema import Candidate as C
    # one floor-flagged candidate (n-release arm) + plain candidates
    ingest(graph, [
        C(primitives=[
            {"op": "tune", "tune": {"structure_name": "bulge",
                                    "param": "n", "toggle": 1, "value": 4.0}}],
          physical_motivation="m", expected_C_prime="c",
          novelty_claim="n", expected_behavior_tag="floor_n",
          pred_bic=900.0),
    ], session_id="s1", parent_label=label)
    floor_aids = [a for a, r in graph.g.graph["pending"].items()
                  if r.get("code_flags")]
    assert floor_aids
    for i in range(6):
        ingest(graph, [_cand([_add("agn", ctype="psf")], 100.0 + i,
                             f"plain{i}")],
               session_id=f"s{i + 2}", parent_label=label)
    _truncate_queue(graph)
    survivors = graph.pending_queue()
    assert all(a in survivors for a in floor_aids)   # flags shield from eviction
    # the eviction order among the unprotected: highest pred_bic goes first
    evicted = [a for a, r in graph.g.graph["pending"].items()
               if r.get("discard_reason") == "QUEUE_TRUNCATION"]
    evicted_preds = {graph.g.graph["pending"][a]["pred_bic"] for a in evicted}
    kept_preds = [graph.g.graph["pending"][a]["pred_bic"] for a in survivors
                  if a not in floor_aids]
    if evicted_preds and kept_preds:
        assert max(kept_preds) <= min(evicted_preds)


# ------------------------------------------- 6. verdict-free survey parsing
def test_survey_response_parses_without_verdict_galfit():
    from beam.candidate_schema import parse_survey_response
    payload = {"candidates": [{
        "primitives": [{"op": "remove", "remove": "bulge"}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "expected_behavior_tag": "t", "pred_bic": 1.0}]}
    resp, err = parse_survey_response(payload)
    assert resp is not None and err == ""
    assert not hasattr(resp, "physicality_verdict")


def test_survey_response_parses_without_verdict_mb():
    from beam_mb.candidate_schema_mb import parse_survey_response_mb
    payload = {"candidates": [{
        "primitives": [{"op": "remove", "target": "disk"}],
        "physical_motivation": "m", "expected_C_prime": "c",
        "expected_behavior_tag": "t", "pred_bic": 1.0}]}
    resp, err = parse_survey_response_mb(payload)
    assert resp is not None and err == ""
    assert not hasattr(resp, "physicality_verdict")


# ------------------------------------- 7. conditional filters not regressed
def test_crash_pattern_still_discards(mini_graph):
    graph, label = mini_graph
    for i in (1, 2):
        aid = graph.add_pending(
            {"action_id": f"bar{i}", "pred_bic": 100.0,
             "expected_behavior_tag": f"bar{i}",
             "primitives": [_add("bar", re=12.0, n=0.5)]},
            "s", label)
        graph.mark_failed(aid, "GALFIT crash: singular matrix")
    res = ingest(graph, [_cand([_add("bar", re=9.0, n=0.5)], 100.0, "bar3")],
                 session_id="s", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "E_CRASH_PATTERN"


def test_class_cap_still_discards(mini_graph):
    graph, label = mini_graph
    from beam.cells import skeleton_class
    from beam.graph import CLASS_CAPS
    inv = graph.state(label).get("inventory") or []
    cls = skeleton_class(inv)
    graph.g.graph["meta"].setdefault("class_caps", dict(CLASS_CAPS))
    # burn the class budget by faking executed attempts
    graph.g.add_node("Z.99", label="Z.99", global_iter_id=99, parent=label,
                     inventory=inv, metrics={"bic_eff": 1.0}, combo_key="k",
                     depth=1, artifacts={})
    for _ in range(CLASS_CAPS[cls]):
        graph.g.add_node(f"Z.{100 + _}", label=f"Z.{100 + _}",
                         global_iter_id=101 + _, parent=label,
                         inventory=inv, metrics={"bic_eff": 1.0},
                         combo_key="k", depth=1, artifacts={})
    res = ingest(graph, [_cand([{"op": "tune",
                                 "tune": {"structure_name": "disk",
                                          "param": "mag", "value": 12.0}}],
                                100.0, "same_class")],
                 session_id="s", parent_label=label)
    assert res.discarded and any(d["reason"] == "CLASS_CAP" for d in res.discarded)


def test_cell_dup_still_discards(mini_graph):
    graph, label = mini_graph
    fitted = graph.state(label)["inventory"]
    # a mag-only tune is not a cell dimension -> same-basin re-entry
    disk = next(c for c in fitted if c.get("name") == "disk")
    prims = [{"op": "tune", "tune": {"structure_name": "disk",
                                     "param": "mag",
                                     "value": float(disk.get("mag", 9.0)) + 0.2}}]
    res = ingest(graph, [_cand(prims, 100.0, "mag_only")],
                 session_id="s", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "CELL_DUP"


def test_temp_constraint_still_discards(mini_graph):
    graph, label = mini_graph
    graph.set_temporary_constraint("add", "companion exclusion", ["companion"])
    prims = [{"op": "add", "add": {"structure_name": "companion",
                                   "component_type": "sersic", "re_px": 2.0,
                                   "x_px": 200.0, "y_px": 100.0}}]
    res = ingest(graph, [_cand(prims, 100.0, "comp_add")],
                 session_id="s", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "E_TEMP_CONSTRAINT"


# --------------------------------------------------- 8. schema version gate
def test_schema_version_rejects_old_graph(tmp_path, test_data_dir):
    """SCHEMA_VERSION 2: a v1 graph.json (sigma/score queue semantics) is
    refused on load."""
    import shutil

    gdir = tmp_path / "OLD"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    g.g.graph["schema_version"] = 1
    g.commit()
    with pytest.raises(ValueError, match="schema version"):
        BeamGraph.load(str(gdir))
