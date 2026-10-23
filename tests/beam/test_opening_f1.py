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


# ---------------------------------------------- VLM-authored opening round
def _skel(components, tag, sigma):
    return {
        "primitives": [{"op": "skeleton", "skeleton": {"components": components}}],
        "physical_motivation": "opening morphology judgement",
        "expected_C_prime": "{" + ",".join(sorted(c["structure_name"]
                                                  for c in components)) + "}",
        "novelty_claim": "untried opening skeleton",
        "expected_behavior_tag": tag,
        "local_benefit_sigma": sigma,
    }


_D3 = [
    {"structure_name": "disk", "component_type": "expdisk", "mag": 15.2,
     "re_px": 55.0, "q": 0.4, "pa_deg": 66.0},
    {"structure_name": "bulge", "component_type": "sersic", "mag": 16.6,
     "re_px": 3.0, "n": 1.0, "q": 0.6, "pa_deg": 66.0},
    {"structure_name": "bar", "component_type": "sersic", "mag": 15.9,
     "re_px": 11.0, "n": 0.5, "q": 0.45, "pa_deg": 82.0,
     "toggles": {"n": 0}},
]
_D2 = [c for c in _D3 if c["structure_name"] in ("disk", "bulge")]

OPENING_MD = """```json
{"physicality_verdict": {"verdict": "PASS", "failed_checks": [], "swap_hint": "none"},
 "candidates": [
  """ + json.dumps(_skel(_D3, "skel_barred", 0.65)) + """,
  """ + json.dumps(_skel(_D2, "skel_bd", 0.5)) + """
 ]}
```"""


def test_opening_round_vlm_authored_end_to_end(tmp_path, test_data_dir, monkeypatch):
    """Flow: beam_init (root verbatim) -> survey_round(A.0, opening_image)
    -> skeleton candidates enqueued -> apply top on A.0 (no galfit.NN)
    -> _iter1.feedme + concentric cons -> beam_record_fit -> A.1."""
    import shutil

    from beam.survey import survey_round
    from beam.tools import apply_candidate, beam_init, beam_record_fit

    gdir = tmp_path / "OPEN2"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "root.feedme")
    (gdir / "orig.png").write_bytes(b"\x89PNG\r\n\x1a\n stub")

    r = beam_init(str(gdir), str(gdir / "root.feedme"))
    assert r["status"] == "success", r.get("error")

    def fake_dispatch(system_prompt, turns, image_path):
        return OPENING_MD, "sess-open", None

    monkeypatch.setattr("beam.survey._dispatch", fake_dispatch)
    rr = survey_round(str(gdir), state_label="A.0",
                      opening_image=str(gdir / "orig.png"))
    assert rr["status"] == "success", rr.get("error")
    assert len(rr["enqueued"]) == 2, rr.get("discarded")
    # no verdict settled on the unfitted root
    from beam.graph import BeamGraph

    g = BeamGraph.load(str(gdir))
    assert (g.state("A.0").get("verdict") or {}).get("verdict") is None
    # both skeletons are parented A.0 and the 3-component one leads (sigma)
    q = rr["queue"]
    assert all(e["parent"] == "A.0" for e in q)
    assert rr["next_candidate"] == q[0]["action_id"]
    skel = g.opening_skeletons()
    assert skel and skel[0]["inventory"] == ["bar", "bulge", "disk"]

    # apply the top skeleton on A.0 — no parent galfit.NN exists
    ap = apply_candidate(str(gdir), rr["next_candidate"])
    assert ap["status"] == "success", ap.get("error")
    assert ap["iter_id"] == 1
    text = open(ap["feedme"], encoding="utf-8").read()
    assert "# STRUCTURE: disk" in text and "# STRUCTURE: bulge" in text \
        and "# STRUCTURE: bar" in text
    # the fixture root carries NO sky block — the skeleton path preserves
    # the root verbatim (header survives; nothing invented)
    assert "A) NGC1097.phot.1_nonan.fits" in text
    cons = open(ap["cons"], encoding="utf-8").read()
    assert "x   offset" in cons and "y   offset" in cons  # concentric chain

    # record the first fit -> A.1, combo bar+bulge+disk (fail-fast record:
    # every referenced path must exist — stub the artefacts the fake run
    # would have produced; the output parse may reuse the input feedme)
    (gdir / "archives" / "open").mkdir(parents=True, exist_ok=True)
    (gdir / "archives" / "open" / "round_status.json").write_text("{}")
    rec = beam_record_fit(str(gdir), json.dumps({
        "input_param_file": ap["feedme"], "output_param_file": ap["feedme"],
        "image_file": str(gdir / "orig.png"), "summary_file": str(gdir / "orig.png"),
        "round_status_file": str(gdir / "archives" / "open" / "round_status.json"),
        "fit_statistics": {"bic_eff": 13844.0, "chisq1d_nu": 86.0,
                           "convergence": {"flag": "ok"}},
    }), action_id=rr["next_candidate"])
    assert rec["status"] == "success", rec.get("error")
    assert rec["new_state"] == "A.1"
    assert rec["combo_key"] == "bar+bulge+disk"
    # the alternate skeleton remains queued as an untried direction
    assert rec["termination"]["stop"] is False
    assert g.next_action() is not None


def test_opening_skeleton_rejected_off_root(mini_graph, monkeypatch):
    """skeleton primitives are legal ONLY on the unfitted root A.0."""
    from beam.candidate_schema import parse_survey_response, validate_survey

    graph, label = mini_graph  # label A.1 is fitted
    payload = json.loads(OPENING_MD.replace("```json", "").replace("```", ""))
    resp, err = parse_survey_response(payload)
    assert resp is not None, err
    report = validate_survey(resp, graph, label)
    assert not report.ok
    assert any(i.code == "E_OPENING" for i in report.errors)
