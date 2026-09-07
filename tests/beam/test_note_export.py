"""Tests for working_note export and the v2 workflow prompt registration."""

import asyncio
import importlib
import json
import sys
from unittest.mock import patch

import pytest

from beam.note_export import build_working_note, export_working_note
from beam.tools import beam_export_note


@pytest.fixture
def seeded_graph(mini_graph):
    """A mini graph with a survey round, a FAIL state and a discard."""
    graph, label = mini_graph
    from beam.candidate_schema import Candidate
    from beam.enqueue import ingest

    ingest(graph, [
        Candidate(primitives=[{"op": "tune", "tune": {"structure_name": "bulge",
                                                      "param": "n", "toggle": 1,
                                                      "value": 2.0}}],
                  physical_motivation="m", expected_C_prime="c", novelty_claim="n axis",
                  expected_behavior_tag="n_free", local_benefit_sigma=0.5),
        Candidate(primitives=[{"op": "remove", "remove": "agn"}],
                  physical_motivation="m", expected_C_prime="c", novelty_claim="new",
                  expected_behavior_tag="rm_agn", local_benefit_sigma=0.3),
    ], session_id="sess-1", parent_label=label)
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": ["[note] x"], "swap_hint": "none"})
    graph.commit()
    return graph


def test_working_note_template_sections(seeded_graph, tmp_path):
    gdir = tmp_path / "NOTE"
    gdir.mkdir()
    path = export_working_note(seeded_graph, str(gdir))
    assert path == str(gdir / "working_note.md")
    text = open(path, encoding="utf-8").read()
    for section in ("## Basic information", "## Beam-state snapshot",
                    "### Current best s*", "### Current priority queue Q",
                    "### Global fit counters", "## State ledgers",
                    "### Input ledger", "### Result ledger", "### Rollback edges",
                    "## Branch A", "### A.1", "## Branch: failure archive",
                    "## Cross-branch decision log"):
        assert section in text, f"missing {section}"
    # queue table lists the enqueued candidate with its floor flag
    assert "n_free" in text and "floor_n_release" in text
    # stage-1 zero-evidence wording
    assert "zero evidence, non-determinative" in text
    # verdict recorded verbatim with the note
    assert "PASS" in text and "[note] x" in text


def test_working_note_fail_verdict_marked(seeded_graph, tmp_path):
    seeded_graph.apply_verdict("A.1", {"verdict": "FAIL",
                                       "failed_checks": ["[hard] inversion"],
                                       "swap_hint": "disk_bulge_swap"})  # no-op: settled
    gdir = tmp_path / "NOTE2"
    gdir.mkdir()
    text = build_working_note(seeded_graph, str(gdir))
    # A.1 was settled PASS earlier; idempotence keeps it
    assert "VLM physicality verdict: PASS" in text


def test_beam_export_note_tool(seeded_graph, tmp_path):
    gdir = tmp_path / "NOTE3"
    gdir.mkdir()
    seeded_graph.g.graph["meta"]["galaxy_dir"] = str(gdir)
    # tool loads from disk: commit into the mini graph's own dir first
    seeded_graph.commit()
    real_dir = tmp_path / "MINI"  # mini_graph fixture location
    r = beam_export_note(str(real_dir))
    assert r["status"] == "success"
    assert "working_note.md" in r["working_note"]


def test_v2_prompt_renders_argument():
    from prompts import prompts

    class _Fake:
        pass

    # prompt object renders via the registered MCP prompt function
    from tools.prompt import workflow_galfit_v2

    text = workflow_galfit_v2.fn(argument="/some/galaxy/dir")
    assert "/some/galaxy/dir" in text
    assert "survey_round" in text and "beam_record_fit" in text
    assert "never hand-edit" in text.lower() or "never do any of it" in text.lower()


def test_registration_includes_export_note_and_v2_prompt():
    with patch.dict(sys.modules, {}), patch.dict(
        "os.environ", {"GALFIT_BIN": "/usr/bin/true"}
    ):
        sys.path.insert(0, "src")
        try:
            import mcp_server

            mcp_server = importlib.reload(mcp_server)
            tools = asyncio.run(mcp_server.app.list_tools())
            names = {t.name for t in tools}
            assert "beam_export_note" in names
            prompts_list = asyncio.run(mcp_server.app.list_prompts())
            pnames = {p.name for p in prompts_list}
            assert "workflow_galfit_v2" in pnames
            assert "workflow_galfit" in pnames  # v1 coexists
        finally:
            if "src" in sys.path:
                sys.path.remove("src")
