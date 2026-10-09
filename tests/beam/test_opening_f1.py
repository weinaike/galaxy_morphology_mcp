"""Austerity-profile tests: the reserved F1 arbitration slot (7+1) and the
Stage-1 opening-skeleton alternates (2026-10-09 KILOGAS_73 retrospective)."""

import json

import pytest

from beam.graph import BeamGraph


# ------------------------------------------------------------- F1 reserved
def test_f1_reserved_once_outside_budget(mini_graph):
    graph, label = mini_graph
    # settle A.1's verdict so the state becomes best (verdict-gated update)
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": [],
                                "swap_hint": "none"})
    assert graph.g.graph.get("best_state") == label  # first fit is best
    aid, err = graph.add_f1_reserved(0.08, 246.0)
    assert aid and not err, err
    rec = graph.g.graph["pending"][aid]
    assert rec["parent"] == label                      # on the BEST state
    assert rec["code_flags"]["f1_reserved"] is True
    assert rec["primitives"][0]["op"] == "remove"      # remove+add rebuild w/ F1
    assert rec["primitives"][1]["f1"]["amplitude"] == 0.08
    assert graph.next_action() == aid                  # pinned to the head

    # record the F1 round: counted in n_f1_reserved, NOT n_executed
    n0 = graph.counters().get("n_executed", 0)
    feedme = graph.state(label)["artifacts"]["feedme"]
    out = graph.state(label)["artifacts"]["galfit_nn"]
    graph.record_fit({
        "input_param_file": feedme, "output_param_file": out,
        "image_file": "", "summary_file": "",
        "round_status_file": "",
        "fit_statistics": {"bic_eff": 900.0, "chisq1d_nu": 0.8,
                           "convergence": {"flag": "ok"}},
    }, action_id=aid)
    c = graph.counters()
    assert c.get("n_f1_reserved") == 1
    assert c.get("n_executed", 0) == n0                # budget untouched
    assert graph.g.graph["meta"]["f1"]["used"] is True

    # once-only: a second F1 round is refused
    aid2, err2 = graph.add_f1_reserved(0.05, 100.0)
    assert aid2 is None and "already consumed" in err2


def test_f1_reserved_refused_without_best(tmp_path, test_data_dir):
    gdir = tmp_path / "NOBEST"
    gdir.mkdir()
    import shutil

    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    graph = BeamGraph.init(str(gdir), str(gdir / "_iter1.feedme"),
                           stage1={"morphology": "x"})
    aid, err = graph.add_f1_reserved(0.02, 0.0)
    assert aid is None and "best_state" in err


# ------------------------------------------------------- opening alternates
def test_opening_alternates_enqueued_after_first_fit(tmp_path, test_data_dir):
    import shutil

    gdir = tmp_path / "OPEN"
    gdir.mkdir()
    feedme = gdir / "_iter1.feedme"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    openings = {"candidates": [
        {"inventory": ["disk", "bulge", "bar"], "sigma": 0.5,
         "rationale": "strong bar visual"},                       # rank 1 = root
        # the fixture root also carries an AGN, so the projected alternate is
        # {disk, bulge, agn} — the declared inventory must match the projection
        {"inventory": ["disk", "bulge", "agn"], "sigma": 0.4,
         "rationale": "bar-less fallback",
         "primitives": [{"op": "remove", "target": "bar"}]},
        {"inventory": ["disk", "bulge", "bar", "lens"], "sigma": 0.3,
         "rationale": "declared mismatch",                        # invalid: prim mismatch
         "primitives": [{"op": "remove", "target": "bar"}]},
    ]}
    graph = BeamGraph.init(str(gdir), str(feedme),
                           stage1={"morphology": "m",
                                   "opening_candidates": openings})
    label = graph.record_fit({
        "input_param_file": str(feedme),
        "output_param_file": "",
        "image_file": "", "summary_file": "", "round_status_file": "",
        "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    })
    alts = graph.enqueue_opening_alternates(label)
    # the valid alternate (rank 2) is enqueued parented on the first fit;
    # rank 3 declared {disk,bulge,bar,lens} but its primitives project
    # {disk,bulge} -> skipped with a decision-log entry
    assert len(alts) == 1
    rec = graph.g.graph["pending"][alts[0]["action_id"]]
    assert rec["parent"] == label
    assert rec["code_flags"].get("opening_alternate") is True
    assert sorted(alts[0]["inventory"]) == ["agn", "bulge", "disk"]
    kinds = {e.get("kind") for e in graph.g.graph["decision_log"]}
    assert "opening-alternate-skip" in kinds
    # idempotent: a second call enqueues nothing
    assert graph.enqueue_opening_alternates(label) == []
