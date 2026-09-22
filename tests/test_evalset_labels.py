"""Focused tests for the frozen Gadotti label reading rule (component-evalset-v2)."""

import json
from pathlib import Path

from component_analysis.evalset.labels import load_gadotti


def _write(tmp_path: Path, name: str, payload: dict) -> None:
    target = tmp_path / "gadotti-json-80"
    target.mkdir(exist_ok=True)
    (target / f"{name}_Gadotti_params.json").write_text(json.dumps(payload), encoding="utf-8")


def test_elliptical_with_bulge_mag_is_single_sersic(tmp_path: Path) -> None:
    _write(tmp_path, "A", {"MType": "elliptical", "mag_disk": 0.0, "mag_bulge": 15.498, "mag_bar": 0.0})
    labels = load_gadotti(tmp_path)
    assert labels["A"]["expert_final_components"] == ["single_sersic"]


def test_non_elliptical_uses_mag_fields(tmp_path: Path) -> None:
    _write(tmp_path, "B", {"MType": "spiral", "mag_disk": 14.2, "mag_bulge": 15.1, "mag_bar": 0.0})
    labels = load_gadotti(tmp_path)
    assert labels["B"]["expert_final_components"] == ["bulge", "disk"]


def test_elliptical_without_bulge_mag_falls_back_to_mag_fields(tmp_path: Path) -> None:
    _write(tmp_path, "C", {"MType": "elliptical", "mag_disk": 14.0, "mag_bulge": 0.0, "mag_bar": 0.0})
    labels = load_gadotti(tmp_path)
    assert labels["C"]["expert_final_components"] == ["disk"]
