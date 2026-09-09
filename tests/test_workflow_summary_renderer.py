"""Tests for deterministic workflow Markdown rendering."""

import json
from pathlib import Path

from component_analysis.workflow_summary_renderer import (
    render_final_report,
    render_image_round,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_renderer_indexes_valid_artifacts_and_marks_missing_fields(tmp_path):
    round_dir = tmp_path / "round_000"
    summary_dir = tmp_path / "summaries"
    _write_json(
        round_dir / "workflow_manifest.json",
        {
            "schema_version": "1.0",
            "workflow_mode": "multi-band",
            "round_id": "obj104_workflow_round0",
            "object_id": "104",
            "config_file": "/data/104/base.lyric",
            "result_files": ["/data/104/F200W/result.fits"],
            "summary_file": "/data/104/F200W/result.gssummary",
            "comparison_png": "/data/104/all_bands_comparison.png",
            "bands": [
                {
                    "band": "F200W",
                    "science_fits": "/data/104/F200W/science.fits",
                    "science_hdu": 0,
                    "result_fits": "/data/104/F200W/result.fits",
                    "result_hdus": {"original_hdu": 4, "model_hdu": 3, "residual_hdu": 0},
                    "fit_region": [0, 100, 0, 100],
                }
            ],
        },
    )
    _write_json(
        round_dir / "proposal" / "workflow_proposal.json",
        {
            "schema_version": "1.0",
            "workflow_mode": "multi-band",
            "object_id": "104",
            "round_id": "obj104_workflow_round0",
            "manifest_ref": "/data/104/workflow_manifest.json",
            "numeric_evidence": {"features": []},
            "vlm_evidence": {"observations": []},
            "evidence_fingerprint": "fixture",
            "rule_decision": {},
            "raw_decision": {},
            "candidate_actions": [],
            "rule_trace": [],
            "termination_checks": [],
            "current_components": ["sersic"],
            "provider": {"status": "DISABLED", "attempts": []},
        },
    )

    target = render_image_round(
        round_dir=round_dir,
        summary_dir=summary_dir,
        state={"object_id": "104", "termination_reason": "Image did not converge"},
    )

    text = target.read_text(encoding="utf-8")
    assert target == summary_dir / "round_obj104_workflow_round0_component_analysis.md"
    assert "Round 0：原图成分预测" in text
    assert "detect_bar_lopsidedness：unavailable" in text
    assert "detected_features：unavailable" in text
    assert "当前 profile：[\"sersic\"]" in text
    assert "provider status：DISABLED" in text
    assert "result FITS `/data/104/F200W/result.fits`" in text
    assert "收敛：未采集" in text
    assert "下一步动作或停止原因：Image did not converge" in text
    assert "artifact index：未采集" in text
    assert "fixture_only：unavailable" in text

    final = render_final_report(
        object_id="104",
        summary_dir=summary_dir,
        state={
            "status": "COMPLETED_WITH_REVIEW",
            "needs_review": True,
            "termination_reason": "Image did not converge",
            "downstream": {
                "image": "FIT_AVAILABLE",
                "sed": "NOT_RUN",
                "image_sed": "NOT_RUN",
            },
        },
    )
    final_text = final.read_text(encoding="utf-8")
    assert "SED：NOT_RUN" in final_text
    assert "Image-SED：NOT_RUN" in final_text
    assert "Image 停止原因：Image did not converge" in final_text
    assert "historical_pre_fix：unavailable" in final_text


def test_renderer_rejects_invalid_manifest_as_unavailable(tmp_path):
    round_dir = tmp_path / "round_001"
    summary_dir = tmp_path / "summaries"
    _write_json(round_dir / "workflow_manifest.json", {"object_id": "1071"})

    target = render_image_round(round_dir=round_dir, summary_dir=summary_dir)
    text = target.read_text(encoding="utf-8")

    assert "manifest：schema validation failed" in text
    assert "波段产物路径" in text
