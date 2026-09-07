"""End-to-end (mocked VLM) tests for survey_round: happy path, retry path,
retry exhaustion, and FastMCP registration."""

import asyncio
import importlib
import json
import shutil
import sys
from unittest.mock import patch

import pytest

from beam.graph import BeamGraph
from beam.survey import survey_round


VALID_MD = """Analysis prose.
```json
{"physicality_verdict": {"verdict": "PASS", "failed_checks": ["[note] areas similar"], "swap_hint": "none"},
 "candidates": [
  {"primitives": [{"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                          "toggle": 1, "value": 2.0,
                                          "cons_bounds": {"n": [0.5, 8.0], "re": null, "q": null, "center_window": null}}}],
   "physical_motivation": "quadrupole residuals persist at PA~45",
   "expected_C_prime": "{disk,bulge,bar,agn}",
   "novelty_claim": "equiv A.1 but n free axis untested",
   "expected_behavior_tag": "bulge_n_free",
   "local_benefit_sigma": 0.55}
 ]}
```
"""

INVALID_MD = "I forgot the JSON, sorry."
SCHEMA_BAD_MD = '```json\n{"physicality_verdict": {"verdict": "MAYBE"}}\n```'


@pytest.fixture
def galaxy(tmp_path, test_data_dir):
    gdir = tmp_path / "SURVEY"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    shutil.copy(test_data_dir / "NGC1097_clean.07", gdir / "galfit.01")
    shutil.copy(test_data_dir / "NGC1097_comparison.png", gdir / "cmp.png")
    shutil.copy(test_data_dir / "NGC1097_summary.md", gdir / "summary.md")
    graph = BeamGraph.init(str(gdir), str(gdir / "_iter1.feedme"),
                           stage1={"morphology": "barred spiral"})
    label = graph.record_fit({
        "input_param_file": str(gdir / "_iter1.feedme"),
        "output_param_file": str(gdir / "galfit.01"),
        "image_file": str(gdir / "cmp.png"),
        "summary_file": str(gdir / "summary.md"),
        "round_status_file": str(gdir / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    })
    return gdir, label


def _mock_dispatch(responses):
    calls = []

    def fake(system_prompt, turns, image_path):
        calls.append({"n_turns": len(turns), "feedback": "rejected" in turns[-1]})
        resp = responses[min(len(calls) - 1, len(responses) - 1)]
        return resp, f"sess-{len(calls)}", None

    return fake, calls


def test_survey_round_happy_path(galaxy, monkeypatch):
    gdir, label = galaxy
    fake, calls = _mock_dispatch([VALID_MD])
    monkeypatch.setattr("beam.survey._dispatch", fake)
    r = survey_round(str(gdir))
    assert r["status"] == "success", r.get("error")
    assert r["verdict"]["verdict"] == "PASS"
    assert r["best_state"] == label  # verdict settled the best
    assert len(r["enqueued"]) == 1
    entry = r["enqueued"][0]
    assert "floor_n_release" in entry["flags"]
    assert r["next_candidate"] == entry["action_id"]
    assert r["queue"][0]["expected_behavior_tag"] == "bulge_n_free"
    assert r["candidates_file"] and "survey" in r["candidates_file"]
    # the archive md contains the raw exchange
    with open(r["candidates_file"], encoding="utf-8") as f:
        md = f.read()
    assert "physicality_verdict" in md and "Analysis prose" in md
    assert calls[0]["feedback"] is False


def test_survey_round_retry_path(galaxy, monkeypatch):
    gdir, _ = galaxy
    fake, calls = _mock_dispatch([INVALID_MD, SCHEMA_BAD_MD, VALID_MD])
    monkeypatch.setattr("beam.survey._dispatch", fake)
    r = survey_round(str(gdir))
    assert r["status"] == "success"
    assert len(calls) == 3
    # from the 2nd call on, the feedback block is appended to the prompt
    assert calls[1]["feedback"] is True and calls[2]["feedback"] is True
    assert r["enqueued"]


def test_survey_round_retry_exhaustion(galaxy, monkeypatch):
    gdir, _ = galaxy
    fake, calls = _mock_dispatch([INVALID_MD])
    monkeypatch.setattr("beam.survey._dispatch", fake)
    r = survey_round(str(gdir), max_retries=1)
    assert r["status"] == "failure"
    assert r["error"] == "validation retries exhausted"
    assert r["issues"] and all("code" in i for i in r["issues"])
    assert len(calls) == 2  # initial + 1 retry
    from beam.graph import BeamGraph as BG

    g = BG.load(str(gdir))
    kinds = [e["kind"] for e in g.g.graph["decision_log"]]
    assert "survey-validation-exhausted" in kinds


def test_survey_round_missing_artifacts(galaxy, monkeypatch):
    gdir, label = galaxy
    (gdir / "cmp.png").unlink()
    fake, calls = _mock_dispatch([VALID_MD])
    monkeypatch.setattr("beam.survey._dispatch", fake)
    r = survey_round(str(gdir))
    assert r["status"] == "failure" and "comparison_png" in r["error"]
    assert calls == []


def test_survey_round_bad_graph(tmp_path):
    r = survey_round(str(tmp_path / "nowhere"))
    assert r["status"] == "failure"


def test_fastmcp_registration_includes_survey():
    with patch.dict(sys.modules, {}), patch.dict(
        "os.environ", {"GALFIT_BIN": "/usr/bin/true"}
    ):
        sys.path.insert(0, "src")
        try:
            import mcp_server

            mcp_server = importlib.reload(mcp_server)
            tools = asyncio.run(mcp_server.app.list_tools())
            names = {t.name for t in tools}
            assert "survey_round" in names
        finally:
            if "src" in sys.path:
                sys.path.remove("src")
