"""Focused tests for the S8 approval gate and full-selection extraction (A0/A1)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from component_analysis.evalset.approval import (
    current_design_sha256,
    validate_approval_for_full,
    write_pilot_approval,
)
from component_analysis.evalset.config import build_run_config, json_dump
from component_analysis.evalset.full import build_full_selection, enumerate_rounds, prepare_full
from component_analysis.evalset.inventory import build_inventory
from schemas import validate


def _write(path: Path, content: str, *, mtime: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _entry_for(inventory: dict, mode: str, object_id: str, batch: str) -> dict:
    dataset = next(item for item in inventory["datasets"] if item["mode"] == mode)
    return next(item for item in dataset["objects"] if item["object_id"] == object_id and item["batch"] == batch)


def _build_fixture_inventory(tmp_path: Path) -> dict:
    single_root = tmp_path / "single"
    multi_root = tmp_path / "multi"
    (single_root / "jwst_32_gt.json").parent.mkdir(parents=True, exist_ok=True)
    (single_root / "jwst_32_gt.json").write_text(json.dumps({"obj1845": ["elliptical"]}), encoding="utf-8")

    obj = single_root / "jwst_0625" / "obj1845"
    _write(obj / "image_f277w.fits", "x", mtime=1_000_000_000)
    _write(obj / "galfit.feedme", "A) image_f277w.fits\n0) sersic\n", mtime=1_000_000_000)
    _write(obj / "galfit.01", "A) image_f277w.fits\n0) sersic\n", mtime=1_000_000_001)  # root copy must be ignored
    _write(obj / "archives" / "20260701T000001.aaa" / "galfit.01", "A) ../../image_f277w.fits\n0) sersic\n", mtime=1_000_000_002)
    _write(obj / "archives" / "20260701T000002.bbb" / "galfit.02", "A) ../../image_f277w.fits\n0) sersic\nF1) 0.2 45 1 1\n", mtime=1_000_000_003)
    _write(obj / "analysis_report.md", 'summary\n{"best_turn": "20260701T000002"}\n', mtime=1_000_000_004)

    multi_label = multi_root / "expert-final-labels.json"
    multi_label.parent.mkdir(parents=True, exist_ok=True)
    multi_label.write_text(
        json.dumps({
            "schema_version": "expert-final-labels@v1",
            "objects": [{"object_id": "104", "label_status": "confirmed", "final_components": ["disk", "bulge"]}],
        }),
        encoding="utf-8",
    )
    obj104 = multi_root / "jwst0716" / "104"
    _write(obj104 / "obj_104.lyric", "Pa1) obj0\nPa2) sersic\n", mtime=1_000_000_000)  # root copy must be ignored
    _write(obj104 / "output" / "20260702_000001_obj_104" / "obj_104.lyric", "Ia1) [none,0]\nPa1) obj0\nPa2) sersic\n", mtime=1_000_000_002)
    _write(obj104 / "output" / "20260702_000001_obj_104" / "obj_104_result.fits", "x", mtime=1_000_000_002)
    _write(obj104 / "output" / "20260702_000002_obj_104_iter2" / "obj_104_iter2.lyric", "Ia1) [none,0]\nPa1) disk\nPa2) sersic\nPb1) bulge\nPb2) sersic\n", mtime=1_000_000_003)
    _write(obj104 / "output" / "20260702_000002_obj_104_iter2" / "obj_104_iter2_result.fits", "x", mtime=1_000_000_003)
    _write(obj104 / "output" / "20260702_000002_obj_104_iter2" / "obj_104_for_image_sed_fitting.lyric", "Pa1) disk\n", mtime=1_000_000_004)
    _write(obj104 / "analysis_report.md", 'summary\n{"best_turn": "20260702_000002"}\n', mtime=1_000_000_005)

    obj104_2 = multi_root / "jwst0716" / "104_2"  # same-batch _2 variant: independent rerun
    _write(obj104_2 / "output" / "20260702_000003_obj_104" / "obj_104.lyric", "Ia1) [none,0]\nPa1) obj0\nPa2) sersic\n", mtime=1_000_000_006)
    _write(obj104_2 / "output" / "20260702_000003_obj_104" / "obj_104_result.fits", "x", mtime=1_000_000_006)
    _write(obj104_2 / "output" / "20260702_000004_obj_104_iter2" / "obj_104_iter2.lyric", "Ia1) [none,0]\nPa1) disk\nPa2) sersic\n", mtime=1_000_000_007)
    _write(obj104_2 / "output" / "20260702_000004_obj_104_iter2" / "obj_104_iter2_result.fits", "x", mtime=1_000_000_007)

    config = build_run_config(
        "inventory",
        output_root=tmp_path / "inventory-run",
        single_band_root=single_root,
        multi_band_root=multi_root,
        run_id="evalset-20260917T000000Z",
    )
    result = build_inventory(single_root, multi_root, tmp_path / "inventory-run", config.as_dict())
    return result["inventory"]


def test_enumerate_rounds_prefers_archive_and_output_copies(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    single = _entry_for(inventory, "single_band", "obj1845", "jwst_0625")
    assert enumerate_rounds(single, "single_band") == [
        "archives/20260701T000001.aaa/galfit.01",
        "archives/20260701T000002.bbb/galfit.02",
    ]
    multi = _entry_for(inventory, "multi_band", "104", "jwst0716")
    assert enumerate_rounds(multi, "multi_band") == [
        "output/20260702_000001_obj_104/obj_104.lyric",
        "output/20260702_000002_obj_104_iter2/obj_104_iter2.lyric",
    ]


def test_enumerate_rounds_root_fallbacks(tmp_path: Path) -> None:
    single_root = tmp_path / "single"
    obj = single_root / "batch" / "obj1"
    _write(obj / "galfit.01", "0) sersic\n", mtime=1)
    _write(obj / "galfit.02", "0) sersic\n", mtime=2)
    entry = {"path": str(obj), "files": [
        {"path": str(obj / "galfit.01"), "role": "fit_parameter_round", "scope": "image", "mtime_ns": 1},
        {"path": str(obj / "galfit.02"), "role": "fit_parameter_round", "scope": "image", "mtime_ns": 2},
    ]}
    assert enumerate_rounds(entry, "single_band") == ["galfit.01", "galfit.02"]

    multi = tmp_path / "multi" / "batch" / "7"
    _write(multi / "obj_7.lyric", "Pa1) disk\n", mtime=1)
    _write(multi / "obj_7_iter2.lyric", "Pa1) disk\n", mtime=2)
    entry = {"path": str(multi), "files": [
        {"path": str(multi / "obj_7.lyric"), "role": "configuration", "scope": "image", "mtime_ns": 1},
        {"path": str(multi / "obj_7_iter2.lyric"), "role": "configuration", "scope": "image", "mtime_ns": 2},
    ]}
    assert enumerate_rounds(entry, "multi_band") == ["obj_7.lyric", "obj_7_iter2.lyric"]


def test_enumerate_rounds_orders_by_directory_timestamp_not_mtime(tmp_path: Path) -> None:
    # Archives written post-hoc: mtimes are scrambled, directory timestamps are the chronology.
    single_root = tmp_path / "single"
    obj = single_root / "batch" / "obj2"
    _write(obj / "archives" / "20260528T175209.aaa" / "galfit.03", "0) sersic\n", mtime=1)
    _write(obj / "archives" / "20260528T173222.bbb" / "galfit.01", "0) sersic\n", mtime=2)
    _write(obj / "archives" / "20260528T174207.ccc" / "galfit.02", "0) sersic\n", mtime=3)
    entry = {"path": str(obj), "files": [
        {"path": str(obj / "archives" / "20260528T175209.aaa" / "galfit.03"), "role": "fit_parameter_round", "scope": "image", "mtime_ns": 1},
        {"path": str(obj / "archives" / "20260528T173222.bbb" / "galfit.01"), "role": "fit_parameter_round", "scope": "image", "mtime_ns": 2},
        {"path": str(obj / "archives" / "20260528T174207.ccc" / "galfit.02"), "role": "fit_parameter_round", "scope": "image", "mtime_ns": 3},
    ]}
    assert enumerate_rounds(entry, "single_band") == [
        "archives/20260528T173222.bbb/galfit.01",
        "archives/20260528T174207.ccc/galfit.02",
        "archives/20260528T175209.aaa/galfit.03",
    ]


def test_full_selection_covers_every_eligible_instance(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    selection, skipped = build_full_selection(inventory)
    assert skipped == []
    assert selection["status"] == "USER_AUTHORIZED_FULL"
    assert selection["inventory_run_id"] == inventory["run_id"]
    assert [(item["mode"], item["object_id"], item["batch"]) for item in selection["objects"]] == [
        ("single_band", "obj1845", "jwst_0625"),
        ("multi_band", "104", "jwst0716"),
        ("multi_band", "104", "jwst0716:104_2"),
    ]
    assert selection["objects"][2]["coverage_tags"] == ["full_extraction", "instance_disambiguation"]
    validate(selection, "evaluation_pilot_selection")


def _pilot_run_fixture(tmp_path: Path, *, status: str = "PILOT_REVIEW_REQUIRED", validation_status: str = "PASS") -> Path:
    run_dir = tmp_path if tmp_path.name.startswith("evalset-") else tmp_path / "pilot-run"
    run_dir.mkdir(parents=True)
    json_dump(run_dir / "run-config.json", {
        "schema_version": "evaluation-run-config@v1",
        "run_id": "evalset-20260916T064856Z",
        "mode": "pilot",
        "design_document": "docs/component-analysis/evaluation-set-design.md",
        "design_sha256": "0" * 64,
        "adjudication_rule_version": "component-evalset-v2",
        "single_band_root": "/single",
        "multi_band_root": "/multi",
        "output_root": "/out",
    })
    json_dump(run_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": "evalset-20260916T064856Z", "status": status})
    json_dump(run_dir / "pilot-validation-report.json", {"status": validation_status})
    (run_dir / "evaluation-action-adjudications.jsonl").write_text('{"sample_id": "x"}\n', encoding="utf-8")
    return run_dir


def test_write_pilot_approval_anchors_current_design_and_new_inventory(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"
    pilot_dir = _pilot_run_fixture(tmp_path)

    approval = write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "approval.json", approval_id="evalset-approval-test", approved_at="2026-09-17T00:00:00Z")
    validate(approval, "evaluation_pilot_approval")
    assert approval["pilot_run_id"] == "evalset-20260916T064856Z"
    assert approval["design_sha256"] == current_design_sha256()
    assert approval["design_sha256"] != json.loads((pilot_dir / "run-config.json").read_text(encoding="utf-8"))["design_sha256"]
    assert json.loads((pilot_dir / "run-status.json").read_text(encoding="utf-8"))["status"] == "PILOT_APPROVED"

    # The full gate accepts the approval against the same inventory.
    validated = validate_approval_for_full(tmp_path / "approval.json", pilot_dir, inventory_path, inventory)
    assert validated["approval_id"] == "evalset-approval-test"


def test_write_pilot_approval_rejects_unvalidated_or_stale_inventory(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"

    unvalidated = _pilot_run_fixture(tmp_path / "unvalidated", validation_status="FAIL")
    with pytest.raises(ValueError, match="not PASS"):
        write_pilot_approval(unvalidated, inventory_path, "小鱼儿", tmp_path / "a.json")

    pilot_dir = _pilot_run_fixture(tmp_path / "ok")
    write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "approval.json")
    stale = json.loads(inventory_path.read_text(encoding="utf-8"))
    stale["design_sha256"] = "0" * 64
    stale_path = tmp_path / "stale-inventory.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(ValueError, match="design_sha256"):
        write_pilot_approval(pilot_dir, stale_path, "小鱼儿", tmp_path / "b.json")


def test_validate_approval_rejects_tampered_inventory_checksum(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"
    pilot_dir = _pilot_run_fixture(tmp_path)
    write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "approval.json")

    tampered = json.loads(inventory_path.read_text(encoding="utf-8"))
    tampered["datasets"][0]["label_count"] += 1
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="source_inventory_sha256"):
        validate_approval_for_full(tmp_path / "approval.json", pilot_dir, tampered_path, tampered)


def test_prepare_full_extracts_full_run_artifacts(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"
    pilot_dir = _pilot_run_fixture(tmp_path)
    approval = write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "approval.json", approval_id="evalset-approval-test")

    output_dir = tmp_path / "full-run"
    output_dir.mkdir()
    result = prepare_full(tmp_path / "approval.json", pilot_dir, inventory_path, output_dir, {"run_id": "evalset-full-test"})

    candidates = [json.loads(line) for line in (output_dir / "evaluation-decision-candidates.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    sample_ids = [item["sample_id"] for item in candidates]
    assert sample_ids == [
        "full-single_band-obj1845-jwst_0625-01",
        "full-single_band-obj1845-jwst_0625-cv",
        "full-multi_band-104-jwst0716-01",
        "full-multi_band-104-jwst0716-cv",
        "full-multi_band-104-jwst0716:104_2-01",
    ]
    by_id = {item["sample_id"]: item for item in candidates}
    assert by_id["full-single_band-obj1845-jwst_0625-01"]["canonical_action"] == {
        "action_type": "PROPOSE_ADD", "component": "fourier_m1", "replace_from": None, "replace_to": None,
    }
    assert by_id["full-multi_band-104-jwst0716:104_2-01"]["canonical_action"]["action_type"] == "PROMOTE_SINGLE_SERSIC_TO_DISK"
    assert by_id["full-multi_band-104-jwst0716-01"]["canonical_action"]["compound_reason"] == "NO_INTERMEDIATE_ROUND"
    assert by_id["full-multi_band-104-jwst0716-cv"]["terminal_consistency"] == "match"
    for item in candidates:
        validate(item, "evaluation_decision_candidate")
    assert json.loads((output_dir / "leakage-report.json").read_text(encoding="utf-8"))["status"] == "PASS"
    assert (output_dir / "full-review.md").exists()
    assert (output_dir / "full-selection.json").exists()
    assert json.loads((output_dir / "full-skip-list.json").read_text(encoding="utf-8"))["skipped"] == []
    assert result["aliases"] == []


def test_prepare_full_requires_valid_approval(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"
    pilot_dir = _pilot_run_fixture(tmp_path, status="PILOT_REVIEW_REQUIRED")  # never approved
    output_dir = tmp_path / "full-run"
    output_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        prepare_full(tmp_path / "missing-approval.json", pilot_dir, inventory_path, output_dir, {"run_id": "evalset-full-test"})


def test_selection_schema_accepts_user_authorized_full() -> None:
    selection = {
        "schema_version": "evaluation-pilot-selection@v1",
        "status": "USER_AUTHORIZED_FULL",
        "inventory_run_id": "evalset-x",
        "design_sha256": "0" * 64,
        "adjudication_rule_version": "component-evalset-v2",
        "selection_policy": "full",
        "objects": [{
            "mode": "multi_band",
            "object_id": "104",
            "batch": "jwst0716",
            "rounds": ["output/a/obj.lyric"],
            "selection_reason": "full",
            "coverage_tags": ["full_extraction"],
        }],
    }
    validate(selection, "evaluation_pilot_selection")


def test_approval_falls_back_to_prior_approval_chain(tmp_path: Path) -> None:
    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"
    pilot_dir = _pilot_run_fixture(tmp_path / "evalset-20260916T064856Z")  # dir name must equal the run id
    first = write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "evalset-approval-a1.json", approval_id="evalset-approval-a1")

    # Pilot directory cleaned up: the gate must fall back to the prior approval.
    saved = {name: (pilot_dir / name).read_text(encoding="utf-8") for name in ["run-config.json", "run-status.json", "pilot-validation-report.json", "evaluation-action-adjudications.jsonl"]}
    for name in saved:
        (pilot_dir / name).unlink()
    second = write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "evalset-approval-a2.json", approval_id="evalset-approval-a2")
    assert second["pilot_run_id"] == first["pilot_run_id"]
    validate_approval_for_full(tmp_path / "evalset-approval-a2.json", pilot_dir, inventory_path, inventory)

    # No prior approval and no directory -> hard failure.
    (tmp_path / "evalset-approval-a1.json").unlink()
    (tmp_path / "evalset-approval-a2.json").unlink()
    with pytest.raises(ValueError, match="no prior approval"):
        write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "approval3.json")


def test_freeze_full_builds_three_pools_and_manifest(tmp_path: Path) -> None:
    from component_analysis.evalset.freeze import freeze_full

    inventory = _build_fixture_inventory(tmp_path)
    inventory_path = tmp_path / "inventory-run" / "evaluation-source-inventory.json"
    pilot_dir = _pilot_run_fixture(tmp_path / "evalset-20260916T064856Z")
    write_pilot_approval(pilot_dir, inventory_path, "小鱼儿", tmp_path / "evalset-approval-a1.json", approval_id="evalset-approval-a1")
    output_dir = tmp_path / "full-run"
    output_dir.mkdir()
    result = prepare_full(tmp_path / "evalset-approval-a1.json", pilot_dir, inventory_path, output_dir, {
        "run_id": "evalset-full-test", "design_sha256": inventory["design_sha256"], "adjudication_rule_version": inventory["adjudication_rule_version"],
    })
    from component_analysis.evalset.adjudication_full import adjudicate_run
    adjudicate_run(output_dir, redo=True)
    json_dump(output_dir / "pilot-validation-report.json", {"status": "PASS", "failures": []})
    json_dump(output_dir / "run-config.json", {
        "schema_version": "evaluation-run-config@v1", "run_id": "evalset-full-test", "mode": "full",
        "design_sha256": inventory["design_sha256"], "adjudication_rule_version": inventory["adjudication_rule_version"],
    })
    json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": "evalset-full-test", "status": "FULL_REVIEW_REQUIRED"})

    frozen = freeze_full(output_dir, inventory_path)
    manifest = frozen["manifest"]
    validate(manifest, "evaluation_set_manifest")
    assert manifest["sample_count"] == sum(1 for _ in open(output_dir / "evaluation-set-v1.jsonl"))
    pools = {}
    for name in ["evaluation-set-v1", "evaluation-audit-v1", "evaluation-excluded-v1"]:
        pools[name] = [json.loads(line) for line in open(output_dir / f"{name}.jsonl") if line.strip()]
        for sample in pools[name]:
            validate(sample, "evaluation_set_sample")
    total = sum(len(v) for v in pools.values())
    assert total == len(result["candidates"])
    assert json.loads((output_dir / "run-status.json").read_text(encoding="utf-8"))["status"] == "FROZEN"
    # 冻结后重复 freeze 必须失败（状态已过审阅门）
    with pytest.raises(ValueError, match="review gate"):
        freeze_full(output_dir, inventory_path)
