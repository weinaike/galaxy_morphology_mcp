"""Integration test: component_analysis with ANALYSIS_MODE=cc (Claude Code Agent SDK).

Requires:
  - CLAUDECODE_API_KEY / CLAUDECODE_BASE_URL / CLAUDECODE_MODEL set in .env
  - claude-agent-sdk installed
  - Test data in tests/data/

Run with:
  pytest tests/test_agent_integration.py -m integration
"""

import os
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

load_dotenv(override=True)

from tools.residual_analysis import component_analysis


def _has_agent_sdk():
    try:
        from claude_agent_sdk import query  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _has_agent_sdk(), reason="claude-agent-sdk not installed")
@pytest.mark.skipif(not os.environ.get("CLAUDECODE_API_KEY"), reason="No API key configured")
def test_cc_component_analysis(test_image, test_summary_file):
    """Verify cc mode returns a successful analysis for single-band data."""
    with patch.dict(os.environ, {"ANALYSIS_MODE": "cc"}):
        result = component_analysis(
            image_file=test_image,
            summary_file=test_summary_file,
            mode="single-band",
            custom_instructions="Check if there is evidence of a bar or spiral structure.",
        )

    if result["status"] == "failure" and "empty" in result.get("error", "").lower():
        # Retry once on empty response (proxy flakiness)
        with patch.dict(os.environ, {"ANALYSIS_MODE": "cc"}):
            result = component_analysis(
                image_file=test_image,
                summary_file=test_summary_file,
                mode="single-band",
                custom_instructions="Check if there is evidence of a bar or spiral structure.",
            )

    assert result["status"] == "success", f"CC analysis failed: {result.get('error')}"
    assert result.get("analysis"), "Analysis text should not be empty"
