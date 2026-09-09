from __future__ import annotations

import json

import numpy as np
from astropy.io import fits

from component_analysis import (
    PolicyState,
    build_workflow_proposal,
    resolve_workflow_proposal,
)
from schemas import validate


def _workflow_fixture(tmp_path):
    shape = (24, 24)
    yy, xx = np.indices(shape, dtype=float)
    original = np.exp(-((xx - 12) ** 2 + (yy - 12) ** 2) / 18.0)
    model = original * 0.9
    residual = original - model
    sigma = np.ones(shape)
    mask = np.zeros(shape)
    psf = np.exp(-((np.indices((9, 9))[0] - 4) ** 2 + (np.indices((9, 9))[1] - 4) ** 2) / 4.0)
    science = tmp_path / "science.fits"
    result = tmp_path / "result.fits"
    sigma_file = tmp_path / "sigma.fits"
    mask_file = tmp_path / "mask.fits"
    psf_file = tmp_path / "psf.fits"
    fits.PrimaryHDU(original).writeto(science)
    fits.HDUList([
        fits.PrimaryHDU(original),
        fits.ImageHDU(model, name="model"),
        fits.ImageHDU(residual, name="residual"),
    ]).writeto(result)
    fits.PrimaryHDU(sigma).writeto(sigma_file)
    fits.PrimaryHDU(mask).writeto(mask_file)
    fits.PrimaryHDU(psf).writeto(psf_file)
    config = tmp_path / "source.feedme"
    config.write_text(
        f"A) {science} # input\nB) output.fits # output\n"
        "C) none # sigma\nD) none # psf\nF) none # mask\n"
        "G) none # constraint\nH) 1 24 1 24 # region\nK) 0.1 0.1 # scale\n",
        encoding="utf-8",
    )
    summary = tmp_path / "summary.txt"
    summary.write_text("# reduced chisq: 1.0\n", encoding="utf-8")
    comparison = tmp_path / "comparison.png"
    comparison.write_bytes(b"PNG placeholder")
    return {
        "schema_version": "1.0",
        "workflow_mode": "single-band",
        "round_id": "round-1",
        "object_id": "object-1",
        "config_file": str(config),
        "result_files": [str(result)],
        "summary_file": str(summary),
        "comparison_png": str(comparison),
        "working_note_file": None,
        "constraint_files": [],
        "parameter_file": None,
        "parameter_files": [],
        "bands": [{
            "band": "single",
            "science_fits": str(science),
            "science_hdu": 0,
            "sigma_fits": str(sigma_file),
            "sigma_hdu": 0,
            "mask_fits": str(mask_file),
            "mask_hdu": 0,
            "psf_fits": str(psf_file),
            "psf_hdu": 0,
            "result_fits": str(result),
            "result_hdus": {
                "original_hdu": 0,
                "model_hdu": 1,
                "residual_hdu": 2,
            },
            "pixscale_arcsec": 0.1,
            "fit_region": [0, 24, 0, 24],
            "validation": {"paths_explicit": True},
        }],
    }


def test_formal_service_builds_and_resolves_without_client(tmp_path):
    manifest = _workflow_fixture(tmp_path)
    proposal_dir = tmp_path / "proposal"
    proposal = build_workflow_proposal(manifest, output_dir=proposal_dir)

    validate(proposal, "workflow_proposal")
    assert proposal["provider"]["status"] == "DISABLED"
    assert (proposal_dir / "workflow_proposal.json").is_file()
    resolved = resolve_workflow_proposal(
        proposal,
        state=PolicyState(object_id="object-1"),
        output_dir=proposal_dir,
    )

    validate(resolved["decision"], "decision_artifact")
    assert resolved["policy_state"]["object_id"] == "object-1"
    assert resolved["next_transition"] in {
        "RUN_FIT", "REVIEW", "COLLECT_EVIDENCE", "VERIFY_BEST_ROUND",
        "FAILED_NEEDS_REVIEW", "REPORT_AND_HANDOFF",
    }
    assert json.loads((proposal_dir / "resolution.json").read_text())[
        "decision"]["round_id"] == "round-1"


def test_resolve_retry_is_idempotent_for_policy_state(tmp_path):
    proposal = build_workflow_proposal(_workflow_fixture(tmp_path))
    state = PolicyState(object_id="object-1")

    first = resolve_workflow_proposal(proposal, state=state)
    trials_after_first = state.trials_used
    history_after_first = list(state.event_history)
    second = resolve_workflow_proposal(proposal, state=state)

    assert second["event_id"] == first["event_id"]
    assert second["decision"] == first["decision"]
    assert state.trials_used == trials_after_first
    assert state.event_history == history_after_first


def test_resolve_creates_new_output_directory(tmp_path):
    proposal = build_workflow_proposal(_workflow_fixture(tmp_path))
    output_dir = tmp_path / "new-resolution"

    result = resolve_workflow_proposal(
        proposal,
        state=PolicyState(object_id="object-1"),
        output_dir=output_dir,
    )

    assert result["decision"]["round_id"] == "round-1"
    assert (output_dir / "decision_artifact.json").is_file()


def test_formal_service_retries_vlm_once_and_preserves_raw_response(tmp_path):
    manifest = _workflow_fixture(tmp_path)
    valid = json.dumps({
        "schema_version": "1.0",
        "round_id": "round-1",
        "parse_status": "OK",
        "observations": [],
    })
    responses = iter(["{ malformed", valid])
    prompts = []

    def callback(image_path, prompt):
        assert image_path.endswith("candidate_overlay.png")
        assert "candidate_overlay" not in prompt
        prompts.append(prompt)
        return next(responses)

    callback.model_id = "test-vlm"
    proposal = build_workflow_proposal(
        manifest,
        output_dir=tmp_path / "vlm",
        vlm_callback=callback,
    )

    assert proposal["provider"]["status"] == "USED"
    assert len(proposal["provider"]["attempts"]) == 2
    assert prompts[0] != prompts[1]
    assert proposal["provider"]["attempts"][0]["finish_reason"] == "unavailable"
    assert proposal["provider"]["prompt_version"] == "component-analysis-vlm@v1.3"
    assert proposal["provider"]["attempts"][0]["prompt_version"] == "component-analysis-vlm@v1.3"
    attempts = json.loads(
        (tmp_path / "vlm" / "vlm_response.attempts.json").read_text()
    )["attempts"]
    assert attempts[0]["raw_response"] == "{ malformed"
    assert (tmp_path / "vlm" / "vlm_response.raw.json").read_text() == valid
