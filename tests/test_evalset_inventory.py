"""Focused tests for the read-only evaluation-set inventory."""

from __future__ import annotations

import json
from pathlib import Path

from component_analysis.evalset.config import build_run_config
from component_analysis.evalset.inventory import build_inventory
from schemas import validate


def _write(path: Path, content: str = "fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_inventory_adapts_both_sources_without_mutating_labels(tmp_path: Path) -> None:
    single_root = tmp_path / "single"
    multi_root = tmp_path / "multi"
    output_dir = tmp_path / "out"

    single_labels = {
        "obj1845_s1_f277w": ["elliptical"],
        "obj104_s1_f277w": ["disk", "fourier"],
    }
    single_label_path = single_root / "jwst_32_gt.json"
    single_label_path.parent.mkdir(parents=True)
    single_label_path.write_text(json.dumps(single_labels), encoding="utf-8")
    gadotti_path = single_root / "gadotti" / "Plate0001" / "Gadotti_params.json"
    _write(gadotti_path, json.dumps({"mag_disk": 16.0, "mag_bulge": 0.0, "mag_bar": 17.0}))

    _write(single_root / "jwst_0625" / "obj1845_s1_f277w" / "galfit.01")
    _write(single_root / "jwst_0625" / "obj1845_s1_f277w" / "galfit.feedme")
    _write(single_root / "jwst_0625" / "obj104_s1_f277w" / "galfit.01")
    _write(single_root / "jwst_0625" / "obj104_s1_f277w" / "galfit.02")
    _write(single_root / "gadotti-0531" / "Plate0001" / "galfit.01")
    _write(single_root / "unlabelled" / "obj999" / "galfit.01")

    multi_labels = {
        "schema_version": "expert-final-labels@v1",
        "objects": [
            {"object_id": "104", "label_status": "confirmed", "final_components": ["disk", "fourier_m1"]},
            {"object_id": "999", "label_status": "unconfirmed", "final_components": ["disk"]},
        ],
    }
    multi_label_path = multi_root / "expert-final-labels.json"
    multi_label_path.parent.mkdir(parents=True)
    multi_label_path.write_text(json.dumps(multi_labels), encoding="utf-8")
    _write(multi_root / "jwst0716" / "104" / "obj_104.lyric")
    _write(multi_root / "jwst0716" / "104" / "obj_104_iter2.lyric")
    _write(multi_root / "jwst0716" / "104_2" / "obj_104_iter2.lyric")
    _write(multi_root / "jwst0716" / "999" / "obj_999.lyric")

    before_single = single_label_path.read_bytes()
    before_multi = multi_label_path.read_bytes()
    config = build_run_config(
        "inventory",
        output_root=output_dir,
        single_band_root=single_root,
        multi_band_root=multi_root,
        run_id="evalset-20260910T000000Z",
    )
    result = build_inventory(single_root, multi_root, output_dir, config.as_dict())

    assert single_label_path.read_bytes() == before_single
    assert multi_label_path.read_bytes() == before_multi
    single, multi = result["inventory"]["datasets"]
    assert single["label_count"] == 3
    assert single["eligible_object_instance_count"] == 3
    assert multi["label_count"] == 1
    assert multi["eligible_object_instance_count"] == 2
    assert multi["image_round_count"] == 4
    assert any(item["reason_code"] == "NO_CONFIRMED_EXPERT_LABEL" for item in result["exclusions"])

    jwst_entry = next(item for item in single["objects"] if item["object_id"] == "obj1845")
    assert jwst_entry["label"]["expert_final_components"] == ["single_sersic"]
    assert result["proposal"]["status"] == "PROPOSAL_REQUIRES_USER_CONFIRMATION"
    assert "CORRECT" not in json.dumps(result, ensure_ascii=False)

    validate(result["inventory"], "evaluation_source_inventory")
    validate(result["proposal"], "evaluation_pilot_selection_proposal")
    validate(result["coverage"], "evaluation_coverage_matrix")
    for exclusion in result["exclusions"]:
        validate(exclusion, "evaluation_source_exclusion")
