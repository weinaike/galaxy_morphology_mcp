"""Tests for vlm_extract.py and enqueue.py."""

import pytest

from beam.candidate_schema import Candidate
from beam.enqueue import ingest
from beam.vlm_extract import extract_survey_json

# ------------------------------------------------------------- vlm_extract
GOOD = {"physicality_verdict": {"verdict": "PASS", "failed_checks": []},
        "candidates": [
            {"primitives": [{"op": "tune", "tune": {"structure_name": "bulge",
                                                    "param": "n", "toggle": 1, "value": 4.0}}],
             "physical_motivation": "m", "expected_C_prime": "{d,b}",
             "expected_behavior_tag": "t", "local_benefit_sigma": 0.6}]}


def test_extract_fenced():
    md = "Some prose.\n```json\n" + __import__("json").dumps(GOOD) + "\n```\nTrailing."
    payload, err = extract_survey_json(md)
    assert payload is not None and err == ""
    assert payload["physicality_verdict"]["verdict"] == "PASS"


def test_extract_fenced_plain_and_second_block():
    import json

    md = "```json\n{\"unrelated\": 1}\n```\nmiddle\n```\n" + json.dumps(GOOD) + "\n```"
    payload, _ = extract_survey_json(md)
    assert payload is not None and "candidates" in payload


def test_extract_unfenced_embedded():
    import json

    md = "Here is my output:\n" + json.dumps(GOOD) + "\nthanks."
    payload, _ = extract_survey_json(md)
    assert payload is not None


def test_extract_failures():
    assert extract_survey_json("")[0] is None
    assert extract_survey_json("no json at all")[0] is None
    assert extract_survey_json('```json\n{"broken": }\n```')[0] is None
    assert extract_survey_json('```json\n{"other": 1}\n```')[0] is None


# ---------------------------------------------------------------- enqueue
def cand(prims, sigma=0.6, tag="t", novelty="new structure", motivation="m"):
    return Candidate(primitives=prims, physical_motivation=motivation,
                     expected_C_prime="c", novelty_claim=novelty,
                     expected_behavior_tag=tag, local_benefit_sigma=sigma)


def test_ingest_happy_path_orders_queue(mini_graph):
    graph, label = mini_graph
    res = ingest(graph, [
        cand([{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                      "toggle": 1, "value": 4.0}}], sigma=0.4, tag="n_free"),
        cand([{"op": "add", "add": {"structure_name": "lens", "component_type": "sersic",
                                    "re_px": 60.0}}], sigma=0.8, tag="lens_add"),
    ], session_id="s1", parent_label=label)
    assert len(res.enqueued) == 2
    q = graph.pending_queue()
    assert len(q) == 2
    # higher-scoring lens add (sigma .8, untried inventory) ranks first
    assert graph.pending_record(q[0])["expected_behavior_tag"] == "lens_add"
    graph.commit()


def test_n_release_floor_beats_sigma_bias(mini_graph):
    """Regression: n-release proposals got sigma 0.1-0.4 and were crowded out
    by structural adds at 0.5-0.8 that then failed catastrophically."""
    graph, label = mini_graph
    res = ingest(graph, [
        cand([{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                      "toggle": 1, "value": 4.0}}],
             sigma=0.10, tag="bulge_n_free", novelty=""),
    ], session_id="s1", parent_label=label)
    assert len(res.enqueued) == 1
    entry = res.enqueued[0]
    assert "floor_n_release" in entry["flags"]
    assert entry["g"] >= 0.5
    rec = graph.pending_record(graph.pending_queue()[0])
    assert rec["code_flags"]["floor_n_release"] is True


def test_floor_survives_truncation(mini_graph):
    graph, label = mini_graph
    cands = [cand([{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                           "toggle": 1, "value": 4.0}}],
                  sigma=0.10, tag="bulge_n_free")]
    cands += [cand([{"op": "add", "add": {"structure_name": "lens",
                                          "component_type": "sersic", "re_px": 60.0}}],
                    sigma=0.9, tag=f"lens{i}") for i in range(6)]
    ingest(graph, cands, session_id="s1", parent_label=label)
    q = graph.pending_queue()
    assert len(q) <= 5
    tags = [graph.pending_record(a)["expected_behavior_tag"] for a in q]
    assert "bulge_n_free" in tags  # floor-protected: never evicted


def test_combo_exhausted_discard(mini_graph):
    graph, label = mini_graph
    cap = graph.g.graph["meta"]["per_combo_cap"]
    combo = graph.state(label)["combo_key"]
    # burn the cap by re-recording equivalent fits is costly; patch counts directly
    for lbl in [f"X.{i}" for i in range(cap)]:
        graph.g.add_node(lbl, combo_key=combo, global_iter_id=100 + int(lbl.split(".")[1]))
    res = ingest(graph, [
        cand([{"op": "tune", "tune": {"structure_name": "bulge", "param": "mag",
                                      "value": 18.5}}], tag="mag_tune"),
    ], session_id="s1", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "COMBO_EXHAUSTED"


def test_r2_zero_cost_rollback_not_enqueued(tmp_path, test_data_dir):
    import shutil

    from beam.graph import BeamGraph

    gdir = tmp_path / "R2"
    gdir.mkdir()
    lens_feedme = gdir / "lens.feedme"
    lens_feedme.write_text(_LENS_FEEDME, encoding="utf-8")
    diskbulge = gdir / "db.feedme"
    diskbulge.write_text(_LENS_FEEDME.replace(_LENS_BLOCK, ""), encoding="utf-8")
    graph = BeamGraph.init(str(gdir), str(lens_feedme), stage1={})

    def rr(inp, out, bic):
        return {"input_param_file": str(inp), "output_param_file": str(out),
                "image_file": "", "summary_file": "",
                "round_status_file": str(gdir / "a" / "round_status.json"),
                "fit_statistics": {"bic_eff": bic, "convergence": {"flag": "ok"}}}

    a1 = graph.record_fit(rr(lens_feedme, lens_feedme, 1000.0), parent_label="A.0")
    a2 = graph.record_fit(rr(diskbulge, diskbulge, 990.0), parent_label=a1)
    assert a1 == "A.1" and a2 == "A.2"
    res = ingest(graph, [
        cand([{"op": "remove", "remove": "lens"}], tag="remove_lens"),
    ], session_id="s1", parent_label=a1)
    assert res.discarded and res.discarded[0]["reason"] == "R2_EXACT_HIT"
    assert res.discarded[0]["rollback_to"] == "A.2"


_LENS_BLOCK = """# Component number: 3
# STRUCTURE: lens
0) sersic
1) 148.0 148.0 1 1
3) 18.5 1
4) 5.0 1
5) 0.3 1
9) 0.85 1
10) 25.0 1
Z) 0

"""
_LENS_FEEDME = """A) image.fits
B) out.fits
G) none
H) 1 297 1 297
# Component number: 1
# STRUCTURE: disk
0) expdisk
1) 148.0 148.0 1 1
3) 16.0 1
4) 12.0 1
9) 0.7 1
10) 25.0 1
Z) 0

# Component number: 2
# STRUCTURE: bulge
0) sersic
1) 148.0 148.0 1 1
3) 18.0 1
4) 2.0 1
5) 4.0 1
9) 0.9 1
10) 25.0 1
Z) 0

""" + _LENS_BLOCK + """# Component number: 4
0) sky
1) 0.0004 0
Z) 0
"""


def test_temporary_constraint_discard(mini_graph):
    graph, label = mini_graph
    graph.g.graph["temporary_constraints"] = [
        {"issued": "2026-09-04", "text": "companion exclusion", "active": True,
         "forbid_structures": ["companion"]}]
    res = ingest(graph, [
        cand([{"op": "add", "add": {"structure_name": "companion",
                                    "component_type": "sersic", "re_px": 2.0,
                                    "x_px": 200.0, "y_px": 100.0}}], tag="comp_add"),
    ], session_id="s1", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "E_TEMP_CONSTRAINT"


def test_persistent_candidate_protection(mini_graph):
    graph, label = mini_graph
    prims = [{"op": "add", "add": {"structure_name": "lens",
                                   "component_type": "sersic", "re_px": 60.0}}]
    res1 = ingest(graph, [cand(prims, sigma=0.7, tag="lens_add")],
                  session_id="s1", parent_label=label)
    assert not res1.protected
    res2 = ingest(graph, [cand(prims, sigma=0.7, tag="lens_add2")],
                  session_id="s2", parent_label=label)
    assert res2.protected
    entry = next(e for e in res2.enqueued if e["tag"] == "lens_add2")
    assert entry["g"] >= 0.6
    assert "persistent_protection" in entry["flags"]


def test_below_gmin_branch(monkeypatch, mini_graph):
    import beam.enqueue as enq

    graph, label = mini_graph
    monkeypatch.setattr(enq, "G_MIN", 0.95)  # raise the bar to exercise the branch
    res = enq.ingest(graph, [
        cand([{"op": "tune", "tune": {"structure_name": "agn", "param": "mag",
                                      "value": 12.5}}], sigma=0.4, tag="agn_mag"),
    ], session_id="s1", parent_label=label)
    assert res.discarded and res.discarded[0]["reason"] == "BELOW_GMIN"
