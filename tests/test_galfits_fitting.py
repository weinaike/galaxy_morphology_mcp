"""Regression tests for the existing GalfitS SED execution helper."""

import subprocess
import sys
from unittest.mock import patch

from tools.galfits_fitting import ImageFitting


def test_image_fitting_uses_current_python_interpreter():
    completed = subprocess.CompletedProcess(args=[], returncode=0)
    with patch("tools.galfits_fitting.subprocess.run", return_value=completed) as run:
        result = ImageFitting(
            lyric_file="/tmp/example/pure_sed.lyric",
            workplace="/tmp/example/result",
        )

    assert result["status"] == "success"
    command = run.call_args.args[0]
    assert command[:3] == [sys.executable, "-m", "galfits.galfitS"]
