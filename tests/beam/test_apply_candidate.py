"""Closed-loop tests for apply_candidate (mock VLM survey, no GALFIT run)."""

import asyncio
import importlib
import json
import shutil
import sys
from unittest.mock import patch

import pytest

from beam.graph import BeamGraph
from beam.survey import survey_round
from beam.tools import apply_candidate, beam_init, beam_record_fit, beam_status

VALID_MD = """```json
{"physicality_verdict": {"verdict": "PASS", "failed_checks": [], "swap_hint": "none"},
 "candidates": [
  {"primitives": [{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                          "toggle": 1, "value": 2.0,
                                          "cons_bounds": {"n": [0.5, 8.0]}}}],
   "physical_motivation": "quadrupole residuals persist",
   "expected_C_prime": "{disk,bulge,bar,agn}",
   "novelty_claim": "n free axis untested",
   "expected_behavior_tag": "bulge_n_free",
   "local_benefit_sigma": 0.55}
 ]}
```
"""


def _touch(path):
    """Record fail-fast requires every referenced path to exist on disk."""
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("{}")
    return path


@pytest.fixture
def galaxy(tmp_path, test_data_dir):
    gdir = tmp_path / "LOOP"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    shutil.copy(test_data_dir / "NGC1097_clean.07", gdir / "galfit.01")
    shutil.copy(test_data_dir / "PSF-1.composite.fits", gdir / "PSF-1.composite.fits")
    shutil.copy(test_data_dir / "NGC1097_comparison.png", gdir / "cmp.png")
    shutil.copy(test_data_dir / "NGC1097_summary.md", gdir / "summary.md")
    r = beam_init(str(gdir), str(gdir / "_iter1.feedme"),
                  stage1_morphology="barred spiral")
    assert r["status"] == "success"
    (gdir / "archives" / "x").mkdir(parents=True)   # record fail-fast: paths must exist
    (gdir / "archives" / "x" / "round_status.json").write_text("{}")
    rr = beam_record_fit(str(gdir), json.dumps({
        "input_param_file": str(gdir / "_iter1.feedme"),
        "output_param_file": str(gdir / "galfit.01"),
        "image_file": str(gdir / "cmp.png"),
        "summary_file": str(gdir / "summary.md"),
        "round_status_file": str(gdir / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    }))
    assert rr["status"] == "success" and rr["new_state"] == "A.1"
    return gdir


def _mock_vlm(md):
    def fake(system_prompt, turns, image_path):
        return md, "sess-1", None
    return fake


def test_closed_loop_apply_and_record(galaxy, monkeypatch):
    # 1. survey (mocked VLM) -> pending candidate
    monkeypatch.setattr("beam.survey._dispatch", _mock_vlm(VALID_MD))
    r = survey_round(str(galaxy))
    assert r["status"] == "success", r.get("error")
    aid = r["next_candidate"]
    assert aid

    # 2. apply -> feedme + cons written, iter consumed
    ap = apply_candidate(str(galaxy), aid)
    assert ap["status"] == "success", ap.get("error")
    assert ap["iter_id"] == 2
    assert (galaxy / "_iter2.feedme").exists() and (galaxy / "iter2.cons").exists()
    with open(ap["feedme"], encoding="utf-8") as f:
        text = f.read()
    assert "G) iter2.cons" in text
    # NGC1097 has 4 luminous components -> concentric chain of 4 (disk anchored first)
    with open(ap["cons"], encoding="utf-8") as f:
        cons = f.read()
    assert "x   offset" in cons and "y   offset" in cons

    # 3. record the (simulated) fit reusing the consumed iter id
    rr = beam_record_fit(str(galaxy), json.dumps({
        "input_param_file": str(galaxy / "_iter2.feedme"),
        "output_param_file": str(galaxy / "galfit.01"),  # fixture output reused
        "image_file": str(galaxy / "cmp.png"),
        "summary_file": str(galaxy / "summary.md"),
        "round_status_file": _touch(str(galaxy / "archives" / "y" / "round_status.json")),
        "fit_statistics": {"bic_eff": 900.0, "chisq1d_nu": 0.8,
                           "convergence": {"flag": "ok"}},
    }), action_id=aid)
    assert rr["status"] == "success"
    assert rr["new_state"] == "A.2"
    g = BeamGraph.load(str(galaxy))
    assert g.state("A.2")["global_iter_id"] == 2  # reused, not double-incremented
    assert g.counters()["global_iter_id"] == 2
    assert g.pending_record(aid)["status"] == "executed"


def test_apply_unknown_or_nonpending(galaxy, monkeypatch):
    monkeypatch.setattr("beam.survey._dispatch", _mock_vlm(VALID_MD))
    r = survey_round(str(galaxy))
    aid = r["next_candidate"]
    assert apply_candidate(str(galaxy), "NOPE")["status"] == "failure"
    # consume it, then re-apply must fail (not pending)
    ap = apply_candidate(str(galaxy), aid)
    assert ap["status"] == "success"
    again = apply_candidate(str(galaxy), aid)
    assert again["status"] == "failure" and "not pending" in again["error"]


def test_apply_discards_on_class_a_conflict(galaxy, monkeypatch):
    # inject a pending candidate whose tune target does not exist
    g = BeamGraph.load(str(galaxy))
    aid = g.add_pending(
        {"sigma": 0.5, "expected_behavior_tag": "bad",
         "primitives": [{"op": "tune", "tune": {"structure_name": "lens",
                                                "param": "q", "value": 0.8}}]},
        source_session="s", parent_label="A.1")
    g.commit()
    ap = apply_candidate(str(galaxy), aid)
    assert ap["status"] == "failure"
    assert any("not found" in n for n in ap["notes"])
    g2 = BeamGraph.load(str(galaxy))
    assert g2.pending_record(aid)["status"] == "discarded"
    assert g2.pending_record(aid)["discard_reason"].startswith("E_TRANSCRIBE")


def test_registration_includes_apply():
    with patch.dict(sys.modules, {}), patch.dict(
        "os.environ", {"GALFIT_BIN": "/usr/bin/true"}
    ):
        sys.path.insert(0, "src")
        try:
            import mcp_server

            mcp_server = importlib.reload(mcp_server)
            tools = asyncio.run(mcp_server.app.list_tools())
            names = {t.name for t in tools}
            assert {"beam_init", "beam_record_fit", "beam_mark_failure",
                    "beam_status", "survey_round", "apply_candidate"} <= names
        finally:
            if "src" in sys.path:
                sys.path.remove("src")


def test_apply_candidate_rechecks_class_cap(galaxy):
    """Regression (Plate0295 A.12; 2026-10-17 class caps): a pending
    candidate enqueued while its skeleton class was fresh must be discarded
    at apply time once the class reached its attempt cap. Floor flags do
    NOT exempt the class cap (only the cell re-check)."""
    from beam.cells import skeleton_class

    g = BeamGraph.load(str(galaxy))
    cls = skeleton_class(g.state("A.1")["inventory"])
    cap = int(g.g.graph["meta"]["class_caps"][cls])
    for i in range(cap):
        g.add_pending({"sigma": 0.4,
                       "primitives": [{"op": "tune", "structure_name": "bulge",
                                       "param": "q", "value": 0.7, "toggle": 1}],
                       "expected_behavior_tag": f"filler{i}"},
                      source_session=f"s{i}", parent_label="A.1")
        aid = g.pending_queue()[-1]
        g.record_fit({
            "input_param_file": str(galaxy / "_iter1.feedme"),
            "output_param_file": str(galaxy / "galfit.01"),
            "image_file": str(galaxy / "cmp.png"),
            "summary_file": str(galaxy / "summary.md"),
            "fit_statistics": {"bic_eff": 1010.0 + i, "chisq1d_nu": 0.9,
                               "convergence": {"flag": "ok"}},
        }, action_id=aid)
    g.commit()
    g2 = BeamGraph.load(str(galaxy))
    # A.1 (the fixture's first fit) + cap filler rounds all share the class
    assert g2.class_attempt_counts().get(cls) == cap + 1

    # a pending candidate landing on the exhausted class (enqueued late,
    # bypassing survey validation, e.g. a stale floor-flagged entry)
    g2.add_pending({"action_id": "stale-floor", "sigma": 0.3,
                    "code_flags": {"floor_n_release": True},
                    "primitives": [{"op": "tune", "structure_name": "bulge",
                                    "param": "n", "value": 4.0, "toggle": 1}],
                    "expected_behavior_tag": "bulge_n_free"},
                   source_session="late", parent_label="A.1")
    g2.commit()

    r = apply_candidate(str(galaxy), "stale-floor")
    assert r["status"] == "discarded", r
    assert r["class"] == cls
    g3 = BeamGraph.load(str(galaxy))
    rec = g3.pending_record("stale-floor")
    assert rec["status"] == "discarded"
    assert "CLASS_EXHAUSTED" in rec["discard_reason"]


def test_apply_candidate_rechecks_cell_dup_input_side(galaxy):
    """The apply-time CELL re-check's independent coverage: a pending
    candidate whose cells match another fit's INPUT cells (not the latest
    attractor — the record-time purge misses it) is discarded at apply."""
    from beam.cells import CellLedger
    from beam.signature import apply_primitives_to_inventory

    g = BeamGraph.load(str(galaxy))
    prims = [{"op": "tune", "structure_name": "bulge",
              "param": "q", "value": 0.31, "toggle": 1}]
    # the twin's executed fit registered these INPUT cells (simulated via
    # the ledger API — the unit under test is the apply-time re-check)
    hypo, _err = apply_primitives_to_inventory(
        g.state("A.1")["inventory"], prims)
    led = CellLedger.from_graph(g)
    led.register(hypo, hypo, False)
    g.g.graph[CellLedger.GRAPH_KEY] = led.to_dict()
    # the stale sibling stays pending (its cells were fresh at enqueue
    # before the twin ran)
    aid_stale = g.add_pending({"action_id": "stale-cell", "sigma": 0.4,
                               "primitives": prims,
                               "expected_behavior_tag": "stale"},
                              source_session="s1", parent_label="A.1")
    g.commit()

    r = apply_candidate(str(galaxy), "stale-cell")
    assert r["status"] == "discarded", r
    rec = BeamGraph.load(str(galaxy)).pending_record("stale-cell")
    assert rec["status"] == "discarded"
    assert "CELL_DUP" in rec["discard_reason"]
