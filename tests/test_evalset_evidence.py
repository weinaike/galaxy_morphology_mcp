"""Focused tests for evalset evidence extraction (fit health and best turn)."""

from pathlib import Path

from component_analysis.evalset.evidence import (
    best_turn_from_report,
    fit_health_from_galfit_output,
    fit_health_from_gssummary,
    match_round_id,
)


def test_gssummary_health_parses_chisq_and_bic(tmp_path: Path) -> None:
    path = tmp_path / "obj.gssummary"
    path.write_text("# target: obj104\n# chisq: 70963.8\n# reduced chisq: 0.36772\n# BIC: 71292.4\n", encoding="utf-8")
    health = fit_health_from_gssummary(path)
    assert health is not None
    assert health["reduced_chisq"] == 0.36772
    assert health["bic"] == 71292.4
    assert health["fit_converged"] is None


def test_gssummary_missing_returns_none(tmp_path: Path) -> None:
    assert fit_health_from_gssummary(tmp_path / "absent.gssummary") is None


def test_single_band_health_uses_last_galfit_output(tmp_path: Path) -> None:
    (tmp_path / "galfit.01").write_text("#  Chi^2/nu = 2.429,  Chi^2 = 12361.493", encoding="utf-8")
    (tmp_path / "galfit.02").write_text("#  Chi^2/nu = 1.649,  Chi^2 = 8389.844", encoding="utf-8")
    health = fit_health_from_galfit_output(tmp_path)
    assert health is not None
    assert health["reduced_chisq"] == 1.649
    assert health["bic"] is None


def test_best_turn_json_line_is_parsed(tmp_path: Path) -> None:
    report = tmp_path / "analysis_report_obj104.md"
    report.write_text("## 详情\n正文\n{\"best_turn\":\"20260716_183809_obj_104_iter6\",\"components\":[\"Disk\",\"Fourier\"]}\n", encoding="utf-8")
    payload = best_turn_from_report(tmp_path)
    assert payload == {"best_turn": "20260716_183809_obj_104_iter6", "components": ["Disk", "Fourier"]}


def test_match_round_id_by_directory_name() -> None:
    rounds = ["output/20260716_175416_obj_104/obj_104.lyric", "output/20260716_180334_obj_104_iter2/obj_104_iter2.lyric"]
    assert match_round_id(rounds, "20260716_180334_obj_104_iter2").endswith("obj_104_iter2.lyric")
    assert match_round_id(rounds, "19700101_000000_missing") is None
