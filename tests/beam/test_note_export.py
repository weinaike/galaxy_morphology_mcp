"""Tests for working_note export and the workflow prompt registration."""

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
    # stage-1: detection retired (2026-10-09); the header states the retirement
    # (legacy graphs with a detect block would render the zero-evidence wording)
    assert "detection retired" in text
    # 2026-10-17 mech-only verdict: settled at record time (PASS);
    # VLM-verdict notes are retired — mech notes land via the record merge
    assert "PASS" in text


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


def test_registration_includes_export_note_and_prompts():
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
            assert "workflow_galfit" in pnames
            assert "workflow_galfit_s1" in pnames
            assert "workflow_galfit_v2" not in pnames  # retired (merged into v1)
        finally:
            if "src" in sys.path:
                sys.path.remove("src")


def test_registration_galfits_env_includes_galfits_s1_prompt():
    """GALFITS_BIN-only session registers workflow_galfits(_s1) but not the
    single-band s1 arm. Runs in a SUBPROCESS: a second in-process
    importlib.reload of mcp_server (as the galfit test above performs)
    trips a pydantic generic-model re-creation KeyError, unrelated to the
    registration logic under test."""
    import json
    import os
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    code = (
        "import asyncio, json, sys\n"
        "sys.path.insert(0, 'src')\n"
        "import mcp_server\n"
        "prompts = asyncio.run(mcp_server.app.list_prompts())\n"
        "print(json.dumps([p.name for p in prompts]))\n"
    )
    with patch.dict("os.environ", {"GALFITS_BIN": "/usr/bin/true"}):
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(repo_root),
            env=dict(os.environ), capture_output=True, text=True,
        )
    assert proc.returncode == 0, proc.stderr[-2000:]
    pnames = set(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert "workflow_galfits" in pnames
    assert "workflow_galfits_s1" in pnames
    assert "workflow_galfit_s1" not in pnames  # single-band arm not
    # registered in a GALFITS_BIN-only session
