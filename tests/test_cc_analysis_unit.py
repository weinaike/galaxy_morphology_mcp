"""Unit tests for the cc_analysis module."""

import os
import unittest
from unittest.mock import patch

from tools.cc_analysis import (
    _get_agent_model,
    run_component_analysis_cc,
)


class TestCcAnalysis(unittest.TestCase):
    """Tests for cc_analysis.py."""

    def test_get_agent_model_from_env(self):
        with patch.dict(os.environ, {"CLAUDECODE_MODEL": "test-model"}):
            self.assertEqual(_get_agent_model(), "test-model")

    def test_get_agent_model_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_get_agent_model())

    @staticmethod
    def _close_coro_return(value):
        """Side effect that closes the coroutine to avoid RuntimeWarning."""
        def side_effect(coro):
            coro.close()
            return value
        return side_effect

    @staticmethod
    def _close_coro_raise(exc):
        """Side effect that closes the coroutine then raises."""
        def side_effect(coro):
            coro.close()
            raise exc
        return side_effect

    @patch("tools.cc_analysis._run_async",
           side_effect=_close_coro_return("Great analysis!"))
    def test_run_component_analysis_cc_success(self, mock_run_async):
        analysis, error = run_component_analysis_cc(
            system_prompt="system",
            analysis_prompts=["analysis"],
            session_id="test-session-id",
        )

        self.assertEqual(analysis, "Great analysis!")
        self.assertIsNone(error)
        mock_run_async.assert_called_once()

    @patch("tools.cc_analysis._run_async",
           side_effect=_close_coro_return("  "))
    def test_run_component_analysis_cc_empty_response(self, mock_run_async):
        analysis, error = run_component_analysis_cc(
            system_prompt="system",
            analysis_prompts=["analysis"],
            session_id="test-session-id",
        )

        self.assertIsNone(analysis)
        self.assertEqual(error, "Agent returned empty analysis")

    @patch("tools.cc_analysis._run_async",
           side_effect=_close_coro_raise(Exception("SDK error")))
    def test_run_component_analysis_cc_exception(self, mock_run_async):
        analysis, error = run_component_analysis_cc(
            system_prompt="system",
            analysis_prompts=["analysis"],
            session_id="test-session-id",
        )

        self.assertIsNone(analysis)
        self.assertIn("Agent SDK error: SDK error", error)

    @patch("tools.cc_analysis._run_async",
           side_effect=_close_coro_return("Multi-turn result"))
    def test_run_component_analysis_cc_multi_prompts(self, mock_run_async):
        analysis, error = run_component_analysis_cc(
            system_prompt="system",
            analysis_prompts=["first question", "second question", "third question"],
            session_id="test-session-id",
        )

        self.assertEqual(analysis, "Multi-turn result")
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
