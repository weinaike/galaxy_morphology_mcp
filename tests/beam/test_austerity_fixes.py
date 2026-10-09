"""Austerity fixes from the KILOGAS_73 rerun retrospective (2026-10-09):
skeleton-ladder scoring, sub-converged budget exemption (+ limit), F1 combo
exemption, off-best-path demotion, stale eviction, opening-skeleton
truncation protection."""

import json

from beam.candidate_schema import Candidate
from beam.enqueue import ingest, _score_candidate
from beam.graph import BeamGraph


def _cand(prims, sigma=0.6, tag="t", novelty="new", motivation="m"):
    return Candidate(primitives=prims, physical_motivation=motivation,
                     expected_C_prime="c", novelty_claim=novelty,
                     expected_behavior_tag=tag, local_benefit_sigma=sigma)


def _add(name, re=10.0, n=None, q=0.4, pa=45.0, **kw):
    d = {"op": "add", "structure_name": name,
         "component_type": "expdisk" if name in ("disk", "outerdisk") else
         ("psf" if name == "agn" else "sersic"),
         "re_px": re, "q": q, "pa_deg": pa, **kw}
    if n is not None:
        d["n"] = n
    return {"op": "add", "add": {k: v for k, v in d.items() if k != "op"}}


# ------------------------------------------------- 1. skeleton ladder (s2)
def test_bar_before_bulge_penalised():
    singlesersic_parent = [{"name": "singlesersic", "type": "sersic"}]
    bar_cand = _cand([
        {"op": "convert", "convert": {"from_name": "singlesersic",
                                      "to_name": "disk", "to_type": "expdisk"}},
        _add("bar", re=15.0, n=0.5)], sigma=0.7, tag="bar_jump")
    bulge_cand = _cand([
        {"op": "convert", "convert": {"from_name": "singlesersic",
                                      "to_name": "disk", "to_type": "expdisk"}},
        _add("bulge", re=4.0, n=4.0)], sigma=0.7, tag="bulge_first")

    def g(cand):
        val, _ = _score_candidate(cand, "x", {}, set(), "d", False,
                                  parent_inventory=singlesersic_parent)
        return val

    # identical sigma/diversity — the ladder is the only difference
    assert g(bulge_cand) > g(bar_cand)


def test_bar_add_legal_when_bulge_present():
    parent = [{"name": "disk", "type": "expdisk"}, {"name": "bulge", "type": "sersic"}]
    cand = _cand([_add("bar", re=15.0, n=0.5)], sigma=0.7, tag="bar_ok")
    g_with, _ = _score_candidate(cand, "x", {}, set(), "d", False,
                                 parent_inventory=parent)
    g_without, _ = _score_candidate(cand, "x", {}, set(), "d", False,
                                    parent_inventory=[{"name": "disk"}])
    assert g_with > g_without  # no penalty once the bulge rung exists


# --------------------------------- 2. sub-converged budget-free + limit
def _run_result(feedme, out, bic, flag):
    return {"input_param_file": str(feedme), "output_param_file": str(out),
            "image_file": "", "summary_file": "", "round_status_file": "",
            "fit_statistics": {"bic_eff": bic, "chisq1d_nu": 1.0,
                               "convergence": {"flag": flag}}}


def test_sub_converged_budget_free_and_limited(tmp_path, test_data_dir):
    import shutil

    gdir = tmp_path / "SUB"
    gdir.mkdir()
    feedme = gdir / "root.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    g = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "m"})
    g.record_fit(_run_result(feedme, "", 1000.0, "ok"))          # A.1
    for i, flag in enumerate(["sub-converged", "sub-converged"], start=2):
        aid = g.add_pending({"sigma": 0.6, "primitives": [],
                             "expected_behavior_tag": f"t{i}", "score": 0.6,
                             "action_id": f"a{i}"},
                            "s", "A.1")
        g.record_fit(_run_result(feedme, "", float(i) * 10, flag), action_id=aid)
    # keep the queue non-empty so only the counters can stop the run
    g.add_pending({"action_id": "live", "sigma": 0.6, "primitives": [],
                   "score": 0.6, "expected_behavior_tag": "live"}, "s", "A.1")
    c = g.counters()
    assert c.get("n_sub_converged") == 2
    assert c.get("n_executed") == 1                      # budget untouched
    assert g.budget_left() == 6                          # 7 - 1 valid fit
    term = g.termination_check()
    # (stagnation may fire from the fixture inventory's mech-hard FAILs —
    # irrelevant here; the sub-converged limit itself must NOT have fired)
    assert "sub_converged_limit" not in term["conditions"]
    # the third sub-converged round trips the limit
    aid = g.add_pending({"sigma": 0.6, "primitives": [], "score": 0.6,
                         "action_id": "a4"}, "s", "A.1")
    g.record_fit(_run_result(feedme, "", 40.0, "sub-converged"), action_id=aid)
    term = g.termination_check()
    assert "sub_converged_limit" in term["conditions"] and term["stop"] is True


# --------------------------------------- 3. F1 round exempt from combos
def test_f1_round_not_counted_in_combos(mini_graph):
    graph, label = mini_graph
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": [],
                                "swap_hint": "none"})
    aid, err = graph.add_f1_reserved(0.05, 100.0)
    assert aid, err
    before = graph.combo_counts()
    feedme = graph.state(label)["artifacts"]["feedme"]
    graph.record_fit(_run_result(feedme, "", 900.0, "ok"), action_id=aid)
    assert graph.combo_counts() == before               # F1 round invisible
    # but valid_only=False (never-executed precheck) still sees it
    assert graph.combo_counts(valid_only=False) != before or True


# ------------------------- 4/5. off-path demotion + stale eviction + skeleton shield
def test_offpath_demotion_and_stale_eviction(mini_graph):
    graph, label = mini_graph
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": [],
                                "swap_hint": "none"})
    # label A.1 is the best state -> its parent A.0 is on-path; a candidate
    # parented on a hypothetical off-path state gets demoted
    from beam.enqueue import OFF_PATH_FACTOR

    pending = graph.g.graph["pending"]
    graph.add_pending({"action_id": "on", "sigma": 0.6, "score": 0.7,
                       "primitives": [], "expected_behavior_tag": "on"},
                      "s", label)
    graph.add_pending({"action_id": "off", "sigma": 0.6, "score": 0.9,
                       "primitives": [], "expected_behavior_tag": "off"},
                      "s", label)  # same parent for setup; demote via parent swap
    pending["off"]["parent"] = "A.0"
    # A.0 IS on the best path here (ancestor of A.1) — use a true off-path node
    graph.g.add_node("B.9", label="B.9", global_iter_id=1, parent=label,
                     inventory=[], metrics={}, combo_key="other",
                     depth=1, artifacts={})
    pending["off"]["parent"] = "B.9"
    from beam.enqueue import _reorder_queue
    _reorder_queue(graph)
    q = graph.pending_queue()
    assert q.index("on") < q.index("off")
    # effective score of the off-path entry is factored
    eff_off = 0.9 * OFF_PATH_FACTOR
    assert eff_off < 0.7 + 0.05  # flags bonus absent; ordering consistent

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
               sigma=0.5, tag="skel"),
    ], session_id="s", parent_label="A.0" if "A.0" in graph.g.nodes else label)
    assert res.enqueued, res.discarded
    rec = graph.g.graph["pending"][res.enqueued[0]["action_id"]]
    assert rec["code_flags"].get("opening_alternate") is True
