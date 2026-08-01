"""Unit tests for run_galfits.py directory selection behavior."""

import asyncio
import re
from pathlib import Path
from unittest.mock import patch

from tools.run_galfits import run_galfits


def _successful_proc():
    return type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()


def _extract_workplace(cmd):
    return Path(cmd[cmd.index("--workplace") + 1])


def test_run_galfits_reuses_valid_timestamp_workflow_directory(tmp_path):
    galaxy_dir = tmp_path / "obj6414"
    output_dir = galaxy_dir / "output"
    workplace_dir = output_dir / "20260429_170957_obj6414_s1_iter2"
    workplace_dir.mkdir(parents=True)
    config_file = workplace_dir / "obj6414_s1_iter2.lyric"
    config_file.write_text("R1) obj6414\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        assert kwargs["cwd"] == str(galaxy_dir)
        assert "--workplace" in cmd
        workplace = _extract_workplace(cmd)
        assert workplace == workplace_dir
        (workplace / "result.gssummary").write_text("BIC 123\n", encoding="utf-8")
        return _successful_proc()

    with patch("tools.run_galfits.subprocess.run", side_effect=fake_run):
        result = asyncio.run(run_galfits(str(config_file)))

    assert result["status"] == "success"
    assert Path(result["workplace"]) == workplace_dir
    assert (workplace_dir / "run.log").exists()
    assert sorted(p.name for p in output_dir.iterdir()) == [workplace_dir.name]

    fitting_log = galaxy_dir / "fitting_log.md"
    assert fitting_log.exists()
    fitting_log_text = fitting_log.read_text(encoding="utf-8")
    assert f"output/{workplace_dir.name}/{config_file.name}" in fitting_log_text
    assert f"output/{workplace_dir.name}/result.gssummary" in fitting_log_text


def test_run_galfits_reuses_nearest_output_parent_as_workflow_root(tmp_path):
    nested_root = tmp_path / "output" / "project"
    galaxy_dir = nested_root / "obj6414"
    output_dir = galaxy_dir / "output"
    workplace_dir = output_dir / "20260429_170957_obj6414_s1_iter2"
    workplace_dir.mkdir(parents=True)
    config_file = workplace_dir / "obj6414_s1_iter2.lyric"
    config_file.write_text("R1) obj6414\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        assert kwargs["cwd"] == str(galaxy_dir)
        workplace = _extract_workplace(cmd)
        assert workplace == workplace_dir
        (workplace / "result.gssummary").write_text("BIC 123\n", encoding="utf-8")
        return _successful_proc()

    with patch("tools.run_galfits.subprocess.run", side_effect=fake_run):
        result = asyncio.run(run_galfits(str(config_file)))

    assert result["status"] == "success"
    assert Path(result["workplace"]) == workplace_dir


def test_run_galfits_rejects_legacy_round_directory_under_output(tmp_path):
    galaxy_dir = tmp_path / "obj6414"
    legacy_dir = galaxy_dir / "output" / "20260429_round2_obj6414_s1_iter2"
    legacy_dir.mkdir(parents=True)
    config_file = legacy_dir / "obj6414_s1_iter2.lyric"
    config_file.write_text("R1) obj6414\n", encoding="utf-8")

    with patch("tools.run_galfits.subprocess.run", return_value=_successful_proc()) as mock_run:
        result = asyncio.run(run_galfits(str(config_file)))

    assert result["status"] == "failure"
    assert "20260429_round2_obj6414_s1_iter2" in result["error"]
    assert "YYYYMMDD_HHMMSS_<basename>" in result["error"]
    mock_run.assert_not_called()


def test_run_galfits_creates_timestamped_output_for_standalone_config(tmp_path):
    galaxy_dir = tmp_path / "obj6414"
    galaxy_dir.mkdir()
    config_file = galaxy_dir / "obj6414_s1.lyric"
    config_file.write_text("R1) obj6414\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        assert kwargs["cwd"] == str(galaxy_dir)
        workplace = _extract_workplace(cmd)
        assert workplace.parent == galaxy_dir / "output"
        assert re.match(r"\d{8}_\d{6}_obj6414_s1$", workplace.name)
        assert (workplace / "obj6414_s1.lyric").exists()
        (workplace / "result.gssummary").write_text("BIC 123\n", encoding="utf-8")
        return _successful_proc()

    with patch("tools.run_galfits.subprocess.run", side_effect=fake_run):
        result = asyncio.run(run_galfits(str(config_file)))

    assert result["status"] == "success"
    assert Path(result["workplace"]).parent == galaxy_dir / "output"
