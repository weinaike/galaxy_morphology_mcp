"""x/y dual cell registration (2026-10-20): record-time registration of both
the input (x) and fitted-output attractor (y) cells, the record-time queue
purge (CELL_PREEMPTED), the floor exemption in enqueue's CELL_DUP, and the
enqueue-time cell refresh."""

import pytest

from beam.candidate_schema import Candidate
from beam.cells import CellLedger, cells_of, primitives_carry_f1
from beam.enqueue import ingest


def cand(prims, sigma=0.6, tag="t", novelty="new structure", motivation="m"):
    return Candidate(primitives=prims, physical_motivation=motivation,
                     expected_C_prime="c", novelty_claim=novelty,
                     expected_behavior_tag=tag, local_benefit_sigma=sigma)


# ------------------------------------------------------------- primitives
def test_primitives_carry_f1():
    assert primitives_carry_f1(
        [{"op": "add", "structure_name": "disk",
          "f1": {"amplitude": 0.05, "phase_deg": 10.0}}])
    assert primitives_carry_f1(
        [{"op": "add", "add": {"structure_name": "disk",
                               "f1": {"amplitude": 0.05, "phase_deg": 10.0}}}])
    assert not primitives_carry_f1(
        [{"op": "add", "structure_name": "disk", "re_px": 12.0}])
    assert not primitives_carry_f1([])


# ------------------------------------------------------------------ ledger
def test_ledger_dual_registration_and_dup():
    led = CellLedger()
    # x lands in cells the fit moved AWAY from; y lands elsewhere
    x = [{"name": "bulge", "type": "sersic", "re": 3.0, "q": 0.7,
          "pa": 10.0, "n": 5.0}]
    y = [{"name": "bulge", "type": "sersic", "re": 12.0, "q": 0.9,
          "pa": 60.0, "n": 1.5}]
    led.register(x, y)
    k = led.to_dict()
    assert len(k) == 1
    slot = next(iter(k.values()))
    assert slot["input"] == {"bulge": [cells_of(x)["bulge"]]}
    assert slot["attractor"] == {"bulge": [cells_of(y)["bulge"]]}
    # a candidate on x's cells is dup (visited input)
    assert led.is_dup(x)
    # a candidate on y's cells is dup (visited attractor — the KILOGAS_197
    # warm-start twin case: the declared change is not a cell dimension)
    assert led.is_dup(y)
    # a candidate in a genuinely new cell is NOT dup
    assert not led.is_dup([{"name": "bulge", "type": "sersic", "re": 3.0,
                            "q": 0.7, "pa": 10.0, "n": 1.5}])   # n bin differs
    # F1 scope: the same inventory carrying F1 never collides with plain
    assert not led.is_dup(y, has_f1=True)


# ------------------------------------------------------- record-time wiring
def test_ledger_survives_json_roundtrip():
    """Regression: JSON persistence turns cell tuples into lists —
    `tuple not in [list]` is silently False, which would disarm the cell
    filter in every cross-process (disk-loaded) ledger."""
    import json

    led = CellLedger()
    x = [{"name": "disk", "type": "expdisk", "re": 30.0, "pa": 10.0}]
    y = [{"name": "disk", "type": "expdisk", "re": 45.0, "pa": 10.0}]
    led.register(x, y)
    # simulate graph.json commit + reload (lists everywhere)
    wire = json.loads(json.dumps(led.to_dict()))
    led2 = CellLedger.from_dict(wire)
    assert led2.is_dup(x) and led2.is_dup(y)
    assert not led2.is_dup([{"name": "disk", "type": "expdisk",
                             "re": 30.0, "pa": 80.0}])   # fresh PA bin


def test_re_projection_no_double_conversion():
    """Regression (KILOGAS_387 A.5): the projection path sets
    re_effective (already an effective radius); _re_eff used to multiply it
    by 1.68 AGAIN, inflating every tune(Re) projection's disk cell a bin
    upward — the dup check went blind exactly on same-basin Re tunes."""
    from beam.cells import comp_cell
    from beam.signature import apply_primitives_to_inventory

    parent = [{"name": "disk", "type": "expdisk", "re": 21.75, "pa": -13.6}]
    prims = [{"op": "tune", "structure_name": "disk",
              "param": "re_px", "value": 48.0}]
    hypo, err = apply_primitives_to_inventory(parent, prims)
    assert err == ""
    # parent's effective Re = 21.75*1.68 ~ 36.5 (bin 5); the tune declares
    # 48 (also bin 5) -> SAME cell, not bin 6
    assert comp_cell(hypo[0]) == comp_cell(parent[0]) == ("disk", 5, 2)


def test_record_fit_persists_dual_ledger(mini_graph):
    graph, label = mini_graph
    stored = graph.g.graph.get("cell_visited")
    assert isinstance(stored, dict) and stored
    st = graph.state(label)
    slot = next(iter(stored.values()))
    assert slot["input"] == {"bulge": [cells_of(st["input_inventory"])["bulge"]],
                             "disk": [cells_of(st["input_inventory"])["disk"]],
                             "bar": [cells_of(st["input_inventory"])["bar"]],
                             "agn": [cells_of(st["input_inventory"])["agn"]]}
    assert "attractor" in slot


def _same_attractor_prims(parent_inventory):
    """A tune whose declared change is NOT a cell dimension (disk mag):
    the projection lands exactly on the parent's attractor cells."""
    disk = next(c for c in parent_inventory if c.get("name") == "disk")
    return [{"op": "tune", "tune": {"structure_name": "disk", "param": "mag",
                                    "value": float(disk.get("mag", 9.0)) + 0.2}}]


def test_record_fit_purges_same_attractor_queue_entries(mini_graph):
    graph, label = mini_graph
    # 1. enqueue a FRESH-cell candidate (bulge Re 12.1 -> 3.0 crosses bins:
    # at enqueue time its cells are unvisited, so it legitimately queues)
    prims = [{"op": "tune", "tune": {"structure_name": "bulge", "param": "re_px",
                                     "value": 3.0}}]
    res = ingest(graph, [cand(prims, sigma=0.7, tag="fresh_then_dead")],
                 session_id="s1", parent_label=label)
    assert res.enqueued
    aid = res.enqueued[0]["action_id"]
    assert graph.pending_record(aid)["status"] == "pending"

    # 2. a later fit CONVERGES into that exact cell (bulge Re -> 3.0):
    # synthesise galfit.02 from galfit.01 with the bulge Re row rewritten
    import pathlib, re as _re
    gdir = pathlib.Path(graph.g.graph["meta"]["galaxy_dir"])
    txt = (gdir / "galfit.01").read_text()
    txt2 = _re.sub(r"12\.1046", "3.0000", txt, count=1)
    assert txt2 != txt, "fixture bulge Re value not found"
    out2 = gdir / "galfit.02"
    out2.write_text(txt2)
    feedme2 = gdir / "_iter2.feedme"
    feedme2.write_text((gdir / "_iter1.feedme").read_text())
    graph.record_fit({
        "input_param_file": str(feedme2),
        "output_param_file": str(out2),
        "fit_statistics": {"bic_eff": 1001.0, "convergence": {"flag": "ok"}},
    })

    # the queued candidate now projects onto the new fit's attractor cells
    # -> purged as a same-basin re-entry
    rec = graph.pending_record(aid)
    assert rec["status"] == "discarded"
    assert rec["discard_reason"] == "CELL_PREEMPTED"
    assert aid not in graph.pending_queue()
    kinds = [d.get("kind") for d in graph.g.graph.get("decision_log", [])]
    assert "cell-preempt" in kinds


def test_f1_reserved_round_is_exempt_from_registration(mini_graph):
    graph, label = mini_graph
    before = graph.g.graph.get("cell_visited")
    aid, err = graph.add_f1_reserved(0.05, 15.0)
    assert aid, err
    st = graph.state(graph.g.graph["best_state"])
    # simulate executing the F1 round: record_fit with the f1_reserved flag
    import shutil, pathlib
    gdir = pathlib.Path(graph.g.graph["meta"]["galaxy_dir"])
    out = gdir / "galfit.03"
    shutil.copy(gdir / "galfit.01", out)
    fm = gdir / "_iter3.feedme"
    shutil.copy(gdir / "_iter1.feedme", fm)
    graph.record_fit({
        "input_param_file": str(fm), "output_param_file": str(out),
        "fit_statistics": {"bic_eff": 999.0, "convergence": {"flag": "ok"}},
    }, action_id=aid)
    # ledger unchanged by the reserved round
    assert graph.g.graph.get("cell_visited") == before


# ---------------------------------------------------------- enqueue behaviour
def test_floor_exempt_from_cell_dup(mini_graph):
    """The acknowledged ordering bug: CELL_DUP used to run BEFORE the floor
    flags were computed, so an n-release tune landing on visited attractor
    cells was killed. Floors are mandated-retention channels — exempt."""
    graph, label = mini_graph
    st = graph.state(label)
    fitted = st["inventory"]
    bulge = next(c for c in fitted if c.get("name") == "bulge")
    # n-release tune whose other cells sit exactly on the parent attractor
    prims = [{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                     "toggle": 1, "value": 4.0}}]
    # the plain version IS a cell dup (n not yet moved bins? n IS a bin:
    # fitted n=0.17 bin 0; value 4.0 bin 1 -> NOT dup). Force the dup case
    # instead with a mag-only tune carrying no floor: must be CELL_DUP.
    res_plain = ingest(graph, [
        cand(_same_attractor_prims(fitted), sigma=0.7, tag="mag_only"),
    ], session_id="s1", parent_label=label)
    assert res_plain.discarded and res_plain.discarded[0]["reason"] == "CELL_DUP"

    # a floor candidate on the SAME attractor cells is enqueued, not dupped
    res_floor = ingest(graph, [
        cand(_same_attractor_prims(fitted) +
             [{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                      "toggle": 1, "value": 4.0}}],
             sigma=0.3, tag="floor_twin"),
    ], session_id="s2", parent_label=label)
    assert res_floor.enqueued, res_floor.discarded
    assert any("floor_n_release" in e["flags"] for e in res_floor.enqueued)


def test_cell_refresh_replaces_unprotected_twin(mini_graph):
    graph, label = mini_graph
    st = graph.state(label)
    fitted = st["inventory"]
    # cross-bin tune so the cell is fresh but reproducible: bulge Re 12 -> 3
    prims = [{"op": "tune", "tune": {"structure_name": "bulge", "param": "re_px",
                                     "value": 3.0}}]
    res1 = ingest(graph, [cand(prims, sigma=0.5, tag="v1")],
                  session_id="s1", parent_label=label)
    assert res1.enqueued
    v1 = res1.enqueued[0]["action_id"]

    # the SAME cell proposed again in a later session: the fresher candidate
    # replaces the queued twin (fresh sigma), old one discarded
    res2 = ingest(graph, [cand(prims, sigma=0.85, tag="v2")],
                  session_id="s2", parent_label=label)
    assert res2.enqueued
    v2 = res2.enqueued[0]["action_id"]
    assert graph.pending_record(v1)["status"] == "discarded"
    assert graph.pending_record(v1)["discard_reason"] == "CELL_REFRESHED"
    assert graph.pending_record(v1)["replaced_by"] == v2
    assert v1 not in graph.pending_queue()
    assert v2 in graph.pending_queue()
    assert graph.pending_record(v2)["expected_behavior_tag"] == "v2"


def test_record_fit_idempotent_on_same_action(mini_graph):
    """beam_record_fit called twice on one action (the KILOGAS_266 A.6/A.7
    phantom twins) must not add a second state node."""
    import shutil
    graph, label = mini_graph
    st = graph.state(label)
    prims = [{"op": "tune", "tune": {"structure_name": "bulge", "param": "re_px",
                                     "value": 3.0}}]
    res = ingest(graph, [cand(prims, sigma=0.7, tag="x")],
                 session_id="s1", parent_label=label)
    assert res.enqueued
    aid = res.enqueued[0]["action_id"]

    import pathlib
    gdir = pathlib.Path(graph.g.graph["meta"]["galaxy_dir"])
    out = gdir / "galfit.09"
    shutil.copy(gdir / "galfit.01", out)
    run = {"input_param_file": str(gdir / "_iter1.feedme"),
           "output_param_file": str(out),
           "fit_statistics": {"bic_eff": 998.0, "convergence": {"flag": "ok"}}}
    first = graph.record_fit(dict(run), action_id=aid)
    n = graph.g.number_of_nodes()
    again = graph.record_fit(dict(run), action_id=aid)
    assert again == first
    assert graph.g.number_of_nodes() == n


def test_cell_refresh_protected_twin_shelters(mini_graph):
    graph, label = mini_graph
    # v1: floor-protected (n-release bundles the flag; n=4.0 sits in bin 2
    # under bisect_right semantics), landing in cell (re 3.0 bin 2, n bin 2)
    floor_prims = [{"op": "tune", "tune": {"structure_name": "bulge",
                                           "param": "re_px", "value": 3.0}},
                   {"op": "tune", "tune": {"structure_name": "bulge",
                                           "param": "n", "toggle": 1,
                                           "value": 4.0}}]
    res1 = ingest(graph, [cand(floor_prims, sigma=0.5, tag="v1")],
                  session_id="s1", parent_label=label)
    assert res1.enqueued and res1.enqueued[0]["flags"]

    # v2: plain tune landing in the SAME cell (n=4.2 shares bin 2 with 4.0;
    # toggle 0 keeps it off the n-release floor predicate)
    plain_prims = [{"op": "tune", "tune": {"structure_name": "bulge",
                                           "param": "re_px", "value": 3.0}},
                   {"op": "tune", "tune": {"structure_name": "bulge",
                                           "param": "n", "toggle": 0,
                                           "value": 4.2}}]
    res2 = ingest(graph, [cand(plain_prims, sigma=0.9, tag="v2")],
                  session_id="s2", parent_label=label)
    assert res2.discarded and res2.discarded[0]["reason"] == "CELL_QUEUED_PROTECTED"
    v1 = res1.enqueued[0]["action_id"]
    assert graph.pending_record(v1)["status"] == "pending"
