from __future__ import annotations

import json

import pytest

from component_analysis import (
    PolicyState,
    apply_action_to_config,
    build_workflow_analysis_artifact,
    build_workflow_proposal,
    build_single_band_workflow_manifest,
    build_lifecycle_record,
    compare_refit_artifacts,
    complete_workflow_candidate,
    copy_config_for_action,
    evaluate_workflow_refit,
    lock_best_round,
    next_config_path,
    normalize_fit_result,
    preflight_action,
    prepare_mcp_fit_call,
    record_fit_lifecycle,
    resolve_workflow_proposal,
    validate_agent_recommendation,
    verify_best_round,
)
from schemas import validate


def _decision(action, *, workflow_status="CONTINUE", candidates=None):
    selected = candidates or [{
        "rule_id": "TEST_RULE_V1",
        "priority": 1,
        "status": "SELECTED",
        "action": action or {"action_type": "INCONCLUSIVE"},
    }]
    return {
        "schema_version": "1.1",
        "round_id": "round-1",
        "state": "PROPOSE",
        "action": action,
        "raw_decision": {
            "state": "PROPOSE",
            "action": action,
            "rule_trace": [{"rule_id": "TEST_RULE_V1", "outcome": "SATISFIED"}],
        },
        "candidate_actions": selected,
        "workflow_status": workflow_status,
        "rule_trace": [{"rule_id": "TEST_RULE_V1", "outcome": "SATISFIED"}],
        "evidence_refs": {
            "numeric_evidence": "numeric.json",
            "vlm_evidence": "vlm.json",
            "manifest": "manifest.json",
            "previous_round": None,
        },
        "rules_version": "rules@v1",
        "thresholds_version": "thresholds@v1",
        "termination_checks": [],
    }


def _manifest(mode="single-band"):
    return {
        "schema_version": "1.0",
        "workflow_mode": mode,
        "round_id": "round-1",
        "object_id": "object-1",
        "config_file": "/tmp/config.feedme",
        "result_files": ["/tmp/result.fits"],
        "summary_file": "/tmp/summary.txt",
        "comparison_png": None,
        "working_note_file": None,
        "constraint_files": [],
        "bands": [{
            "band": "single",
            "science_fits": "/tmp/science.fits",
            "science_hdu": 0,
            "sigma_fits": None,
            "sigma_hdu": None,
            "mask_fits": None,
            "mask_hdu": None,
            "psf_fits": None,
            "psf_hdu": None,
            "result_fits": "/tmp/result.fits",
            "result_hdus": {"original_hdu": 1, "model_hdu": 2, "residual_hdu": 3},
            "pixscale_arcsec": 0.1,
            "fit_region": [0, 10, 0, 10],
            "validation": {},
        }],
    }


def test_single_band_manifest_uses_explicit_paths_and_detects_result_hdus(tmp_path):
    fits = pytest.importorskip("astropy.io.fits")
    science = tmp_path / "science.fits"
    result = tmp_path / "result.fits"
    summary = tmp_path / "summary.txt"
    comparison = tmp_path / "comparison.png"
    feedme = tmp_path / "source.feedme"
    data = __import__("numpy").ones((4, 5))
    fits.PrimaryHDU(data).writeto(science)
    hdus = [fits.PrimaryHDU(), fits.ImageHDU(data, name="original"),
            fits.ImageHDU(data, name="model"), fits.ImageHDU(data, name="residual")]
    fits.HDUList(hdus).writeto(result)
    summary.write_text("# reduced chisq: 1.0\n", encoding="utf-8")
    comparison.write_bytes(b"png")
    feedme.write_text(
        f"A) {science.name} # input\nB) output.fits # output\n"
        "C) none # sigma\nD) none # psf\nF) none # mask\n"
        "G) none # constraint\nH) 1 5 1 4 # region\nK) 0.1 0.1 # scale\n",
        encoding="utf-8",
    )

    manifest = build_single_band_workflow_manifest(
        config_file=feedme,
        optimized_fits_file=result,
        summary_file=summary,
        comparison_png=comparison,
        object_id="object-1",
        round_id="round-1",
    )

    assert manifest["workflow_mode"] == "single-band"
    assert manifest["bands"][0]["result_hdus"] == {
        "original_hdu": 1, "model_hdu": 2, "residual_hdu": 3
    }
    assert manifest["bands"][0]["fit_region"] == [0, 5, 0, 4]


def test_preflight_keeps_remove_review_only_and_blocks_edge_on():
    remove = _decision({
        "action_type": "PROPOSE_REMOVE",
        "component": "bar",
        "target_model_label": "bar1",
        "reason_code": "COMPONENT_SUPPORT_FALSE",
    })
    result = preflight_action(remove, _manifest(), current_model_labels=["bar1"])
    assert result["ok"] is False
    assert result["reason_code"] == "REMOVE_FEATURE_DISABLED"

    edge = _decision({
        "action_type": "PROPOSE_REPLACE",
        "replace_from": "disk",
        "replace_to": "edge_on_disk",
        "target_model_label": "disk1",
    })
    result = preflight_action(edge, _manifest(), current_model_labels=["disk1"])
    assert result["reason_code"] == "EDGE_ON_DISK_OUT_OF_SCOPE"


def test_preflight_never_returns_fit_call_for_converged_action():
    checks = [{"check_id": f"check-{index}", "status": "PASS"} for index in range(7)]
    decision = _decision(
        {"action_type": "CONVERGED", "termination_checks": checks},
        workflow_status="CONVERGED",
    )

    result = preflight_action(decision, _manifest())

    assert result["ok"] is False
    assert result["reason_code"] == "CONVERGED_DOES_NOT_FIT"
    assert result["should_fit"] is False
    assert "mcp_call" not in result


def test_preflight_returns_existing_mode_specific_mcp_contract(tmp_path):
    config = tmp_path / "config.feedme"
    config.write_text("A) input.fits # input\n", encoding="utf-8")
    manifest = _manifest()
    manifest["config_file"] = str(config)
    action = _decision({"action_type": "PROPOSE_ADD", "component": "disk"})
    single = preflight_action(action, manifest)
    assert single["ok"] is True
    assert prepare_mcp_fit_call(manifest, preflight=single)["tool"] == "run_galfit"

    multi_manifest = _manifest("multi-band")
    multi_manifest["config_file"] = str(config)
    multi_manifest["bands"][0]["band"] = "f200w"
    multi_action = _decision({"action_type": "PROPOSE_ADD", "component": "lens"})
    multi = preflight_action(multi_action, multi_manifest)
    assert multi["ok"] is True
    assert prepare_mcp_fit_call(multi_manifest, preflight=multi)["tool"] == "run_galfits_image_fitting"


def test_agent_recommendation_can_only_rank_existing_candidates():
    candidate = {
        "rule_id": "RULE_A",
        "priority": 1,
        "status": "DEFERRED",
        "action": {"action_type": "PROPOSE_ADD", "component": "lens"},
    }
    recommendation = {
        "schema_version": "agent-recommendation@v1",
        "recommended_rule_id": "RULE_A",
        "candidate_priorities": [{"rule_id": "RULE_A", "priority": 1}],
        "rationale": "shape evidence is compatible",
        "evidence_refs": ["vlm.json"],
        "uncertainty": "medium",
        "suggested_action": candidate["action"],
    }
    assert validate_agent_recommendation(recommendation, [candidate])["execution_authority"] == "policy.resolved_decision"
    with pytest.raises(ValueError, match="outside candidate set"):
        validate_agent_recommendation(
            {**recommendation, "recommended_rule_id": "RULE_B"}, [candidate]
        )


def test_analysis_adapter_keeps_markdown_as_evidence_only(tmp_path):
    analysis = tmp_path / "component_analysis.md"
    note = tmp_path / "working_note.md"
    analysis.write_text("建议删除 Bar，但这不是机器动作来源。\n", encoding="utf-8")
    note.write_text("Round 1\n", encoding="utf-8")
    decision = _decision({"action_type": "PROPOSE_ADD", "component": "disk"})
    artifact = build_workflow_analysis_artifact(
        workflow_manifest=_manifest(),
        decision_artifact=decision,
        component_analysis_file=analysis,
        working_note_file=note,
    )
    assert artifact["execution_authority"] == "policy.resolved_decision"
    assert artifact["resolved_decision"]["action"]["action_type"] == "PROPOSE_ADD"
    assert artifact["narrative_refs"]["component_analysis"] == str(analysis.resolve())


def test_lifecycle_stopped_with_valid_fit_is_complete_but_unlocked(tmp_path):
    actionless = _decision(None, workflow_status="STOPPED_NEEDS_REVIEW")
    actionless["automation"] = {
        "policy_version": "automation-policy@v1",
        "resolution": "rule_terminated",
        "original_action_type": "INCONCLUSIVE",
        "resolved_rule_id": "TEST_RULE_V1",
        "reason": "review",
        "needs_review": True,
    }
    state = PolicyState(object_id="object-1")
    lifecycle = build_lifecycle_record(
        workflow_manifest=_manifest(),
        raw_decision=actionless,
        resolved_decision=actionless,
        policy_state=state,
        fit_result={
            "status": "success",
            "input_param_file": "/tmp/config.feedme",
            "optimized_fits_file": "/tmp/result.fits",
            "summary_file": "/tmp/summary.txt",
            "image_file": "/tmp/comparison.png",
        },
    )
    assert lifecycle["fit_completion_status"] == "FIT_AVAILABLE"
    assert lifecycle["best_round_status"] == "UNLOCKED"
    assert lifecycle["needs_review"] is True

    state_file = tmp_path / "state.json"
    lifecycle_file = tmp_path / "lifecycle.json"
    saved = record_fit_lifecycle(
        state=state,
        workflow_manifest=_manifest(),
        raw_decision=actionless,
        resolved_decision=actionless,
        fit_result={
            "status": "success",
            "input_param_file": "/tmp/config.feedme",
            "optimized_fits_file": "/tmp/result.fits",
            "summary_file": "/tmp/summary.txt",
            "image_file": "/tmp/comparison.png",
        },
        state_file=state_file,
        lifecycle_file=lifecycle_file,
    )
    assert saved["fit_completion_status"] == "FIT_AVAILABLE"
    assert json.loads(state_file.read_text())['last_round_id'] == "round-1"


def test_lifecycle_stopped_without_fit_is_failed_needs_review():
    actionless = _decision(None, workflow_status="STOPPED_NEEDS_REVIEW")
    actionless["automation"] = {
        "policy_version": "automation-policy@v1",
        "resolution": "rule_terminated",
        "original_action_type": "INCONCLUSIVE",
        "resolved_rule_id": "TEST_RULE_V1",
        "reason": "review",
        "needs_review": True,
    }
    lifecycle = build_lifecycle_record(
        workflow_manifest=_manifest(),
        raw_decision=actionless,
        resolved_decision=actionless,
        policy_state=PolicyState(object_id="object-1"),
    )
    assert lifecycle["fit_completion_status"] == "FAILED_NEEDS_REVIEW"
    assert lifecycle["downstream_handoff"]["sed_joint_eligible"] is False


def test_new_action_config_path_and_copy_never_overwrite_source(tmp_path):
    source = tmp_path / "object.feedme"
    source.write_text("source\n", encoding="utf-8")
    target = next_config_path(source)
    assert target.endswith("object_iter1.feedme")
    copied = copy_config_for_action(source, target)
    assert copied == target
    assert source.read_text(encoding="utf-8") == "source\n"


def _single_feedme():
    return """A) input.fits
B) output.fits
C) none
D) none
F) none
G) none
H) 1 20 1 20
K) 0.1 0.1

# Object number: 1
0) sersic
1) 10 10 1 1
3) 20 1
4) 2 1
5) 2 1
6) 0 0
7) 0 0
8) 0 0
9) 0.8 1
10) 0 1
Z) 0

# Object number: 2
0) sky
1) 0 1
2) 0 0
3) 0 0
Z) 0
"""


def _multi_lyric():
    return """Pa1) disk
Pa2) sersic
Pa3) [0,-5,5,0.1,1]
Pa4) [0,-5,5,0.1,1]
Pa5) [2.0,0.5,5,0.1,1]
Pa6) [1,0.5,3,0.1,1]
Pa7) [0,-90,90,1,1]
Pa8) [0.6,0.2,1,0.01,1]
Pa9) [[0,-8,0,0.1,1]]
Pa10) [0,0,1,0.1,0]
Pa11) [[0.02,0.001,0.04,0.001,1]]
Pa12) [[0,0,5,0.1,1]]
Pa13) [100,40,200,1,0]
Pa14) [10,8,12,0.1,1]
Pa15) bins
Pa16) [-2,-4,-2,0.1,0]

# Bar profile required for the Lens size-order validation.
Pc1) bar
Pc2) sersic
Pc3) [0,-5,5,0.1,1]
Pc4) [0,-5,5,0.1,1]
Pc5) [0.5,0.1,2,0.1,1]
Pc6) [0.5,0.1,8,0.1,0]
Pc7) [0,-90,90,1,1]
Pc8) [0.3,0.05,1,0.01,1]
Pc9) [[0,-8,0,0.1,1]]
Pc10) [0,0,1,0.1,0]
Pc11) [[0.02,0.001,0.04,0.001,1]]
Pc12) [[0,0,5,0.1,1]]
Pc13) [100,40,200,1,0]
Pc14) [10,8,12,0.1,1]
Pc15) bins
Pc16) [-2,-4,-2,0.1,0]

Ga1) mygal
Ga2) ['a', 'c']
Ga3) [0.1,0,1,0.1,0]
Ga4) 0
Ga5) [1,0.5,2,0.05,0]
Ga6) []
Ga7) 1
"""


def test_single_refit_and_add_write_new_config_only(tmp_path):
    source = tmp_path / "object.feedme"
    source.write_text(_single_feedme(), encoding="utf-8")
    original = source.read_text(encoding="utf-8")
    manifest = _manifest()
    manifest["config_file"] = str(source)

    refit = _decision({
        "action_type": "REFIT_PARAMETERS",
        "reason_code": "DISK_N_NOT_FIXED",
        "parameter_changes": [{
            "target_model_label": "obj0",
            "parameter": "n",
            "operation": "FIX_VALUE",
            "value": 1.0,
        }],
    })
    refit_target = tmp_path / "object_iter1.feedme"
    result = apply_action_to_config(refit, manifest, target_config=refit_target)
    assert result["ok"] is True
    assert result["mcp_call"] == {
        "tool": "run_galfit",
        "arguments": {"config_file": str(refit_target.resolve()), "options": []},
        "execute_by": "workflow_agent",
        "executed_here": False,
    }
    assert "5) 1 0" in refit_target.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == original


def test_single_unknown_candidate_label_is_rejected_before_copy(tmp_path):
    source = tmp_path / "object.feedme"
    source.write_text(_single_feedme(), encoding="utf-8")
    original = source.read_text(encoding="utf-8")
    manifest = _manifest()
    manifest["config_file"] = str(source)
    decision = _decision({
        "action_type": "REFIT_PARAMETERS",
        "parameter_changes": [{
            "target_model_label": "candidate_1",
            "parameter": "n",
            "operation": "FIX_VALUE",
            "value": 1,
        }],
    })
    target = tmp_path / "object_iter1.feedme"
    with pytest.raises(ValueError, match="unambiguous component mapping"):
        apply_action_to_config(decision, manifest, target_config=target)
    assert not target.exists()

    add = _decision({"action_type": "PROPOSE_ADD", "component": "bar"})
    add_target = tmp_path / "object_iter2.feedme"
    added = apply_action_to_config(add, manifest, target_config=add_target)
    assert added["ok"] is True
    assert "0) sersic" in add_target.read_text(encoding="utf-8")
    assert "5) 0.5" in add_target.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == original


def test_multiband_lens_plan_is_bounded_and_updates_galaxy_membership(tmp_path):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    original = source.read_text(encoding="utf-8")
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    manifest["bands"][0]["band"] = "f200w"
    action = _decision({"action_type": "PROPOSE_ADD", "component": "lens"})
    recommendation = {
        "schema_version": "agent-recommendation@v1",
        "recommended_rule_id": "TEST_RULE_V1",
        "candidate_priorities": [{"rule_id": "TEST_RULE_V1", "priority": 1}],
        "rationale": "the candidate is compatible with the current model",
        "evidence_refs": ["numeric.json", "vlm.json"],
        "uncertainty": "medium",
        "suggested_action": action["action"],
        "parameter_plan": {
            "new_prefix": "b",
            "profile_name": "lens",
            "profile_type": "sersic",
            "template_model_label": "disk",
            "galaxy_label": "a",
            "parameters": {
                "re": {"value": [1.2, 0.5, 2.0, 0.1, 1]},
                "n": {"value": [0.3, 0.1, 0.49, 0.05, 1]},
                "q": {"value": [0.7, 0.51, 1.0, 0.01, 1]},
            },
        },
    }
    target = tmp_path / "object_iter1.lyric"
    result = apply_action_to_config(
        action,
        manifest,
        target_config=target,
        agent_recommendation=recommendation,
    )
    assert result["ok"] is True
    text = target.read_text(encoding="utf-8")
    assert "Pb1) lens" in text
    assert "Pb5) [1.2, 0.5, 2.0, 0.1, 1]" in text
    assert "Pb6) [0.3, 0.1, 0.49, 0.05, 1]" in text
    assert "Pb8) [0.7, 0.51, 1.0, 0.01, 1]" in text
    assert "Ga2) ['a', 'c', 'b']" in text
    assert text.count("Ga1) mygal") == 1
    assert source.read_text(encoding="utf-8") == original
    assert result["mcp_call"]["arguments"]["extra_args"] == ["--fit_method", "ES"]


@pytest.mark.parametrize("action", [
    {
        "action_type": "PROMOTE_SINGLE_SERSIC_TO_DISK",
        "component": "disk",
        "target_model_label": "disk",
        "semantic_transition": "single_sersic_to_disk",
    },
    {
        "action_type": "PROPOSE_REPLACE",
        "replace_from": "bar",
        "replace_to": "bulge",
        "target_model_label": "bar",
    },
    {
        "action_type": "PROPOSE_REMOVE",
        "component": "bar",
        "target_model_label": "bar",
    },
])
def test_multiband_structural_actions_write_distinct_lyrics(tmp_path, action):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    target = tmp_path / f"{action['action_type']}.lyric"
    decision = _decision(action)
    result = apply_action_to_config(
        decision,
        manifest,
        target_config=target,
        allow_remove=True,
        remove_pilot_passed=True,
    )
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") != source.read_text(encoding="utf-8")
    if action["action_type"] == "PROMOTE_SINGLE_SERSIC_TO_DISK":
        text = target.read_text(encoding="utf-8")
        assert text.count("Pa1) ") == 1
        assert "Pa2) sersic" in text
        assert "Pa6) [1.0, 0.5, 3, 0.1, 0]" in text
    elif action["action_type"] == "PROPOSE_REPLACE":
        assert "Pc1) bulge" in target.read_text(encoding="utf-8")
    else:
        text = target.read_text(encoding="utf-8")
        assert "Pc1) bar" not in text
        assert "Ga2) ['a']" in text


@pytest.mark.parametrize(
    ("operation", "value", "expected"),
    [
        ("SET_INITIAL", 1.5, "[1.5, 0.5, 3, 0.1, 1]"),
        ("FIX_VALUE", 1.0, "[1.0, 0.5, 3, 0.1, 0]"),
        ("FREE_VALUE", 1.0, "[1.0, 0.5, 3, 0.1, 1]"),
    ],
)
def test_multiband_refit_operations_edit_profile_tuple(tmp_path, operation, value, expected):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    action = _decision({
        "action_type": "REFIT_PARAMETERS",
        "parameter_changes": [{
            "target_model_label": "disk",
            "parameter": "n",
            "operation": operation,
            "value": value,
        }],
    })
    target = tmp_path / f"object_{operation}.lyric"
    result = apply_action_to_config(action, manifest, target_config=target)
    assert result["ok"] is True
    assert f"Pa6) {expected}" in target.read_text(encoding="utf-8")


def test_multiband_refit_merges_constraints_and_removes_one_constraint(tmp_path):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    existing = tmp_path / "source.constrain"
    existing.write_text(
        "def Update_Constraints(pardictlc):\n"
        "    pardictlc['Mag_disk'] = 1\n"
        "    pardictlc['n_bar'] = 2\n",
        encoding="utf-8",
    )
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    manifest["constraint_files"] = [str(existing)]
    action = _decision({
        "action_type": "REFIT_PARAMETERS",
        "parameter_changes": [
            {
                "target_model_label": "bar",
                "parameter": "x,y",
                "operation": "LINK_CENTER",
                "reference_model_label": "disk",
            },
            {
                "target_model_label": "bar",
                "parameter": "n",
                "operation": "REMOVE_NONREQUIRED_CONSTRAINT",
            },
        ],
    })
    target = tmp_path / "object_iter1.lyric"
    result = apply_action_to_config(action, manifest, target_config=target)
    constraint = target.with_suffix(".constrain")
    text = constraint.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert text.count("def Update_Constraints(pardictlc):") == 1
    assert "pardictlc['Mag_disk'] = 1" in text
    assert "n_bar" not in text
    assert "bar_xcen" in text and "bar_ycen" in text
    assert result["constraint_files"] == [str(constraint)]


def test_multiband_companion_is_an_independent_galaxy(tmp_path):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    action = _decision({"action_type": "PROPOSE_ADD", "component": "companion"})
    target = tmp_path / "object_iter1.lyric"
    result = apply_action_to_config(action, manifest, target_config=target)
    text = target.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "Pb1) companion" in text
    assert "Gb1) companion" in text
    assert "Gb2) ['b']" in text
    assert "Ga2) ['a', 'c']" in text


def test_multiband_validator_accepts_existing_single_profile_galaxy(tmp_path):
    from component_analysis.workflow_bridge import _validate_generated_config

    source = tmp_path / "object.lyric"
    source.write_text(
        _multi_lyric().replace("Ga2) ['a', 'c']", "Ga2) a"),
        encoding="utf-8",
    )
    _validate_generated_config(source, "multi-band")


def test_multiband_validator_rejects_malformed_single_profile_galaxy(tmp_path):
    from component_analysis.workflow_bridge import _validate_generated_config

    source = tmp_path / "object.lyric"
    source.write_text(
        _multi_lyric().replace("Ga2) " + repr(["a", "c"]), "Ga2) a b"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"G[A-Za-z]2\) is not parseable"):
        _validate_generated_config(source, "multi-band")


def test_multiband_agn_uses_independent_n_block(tmp_path):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    action = _decision({"action_type": "PROPOSE_ADD", "component": "agn"})
    target = tmp_path / "object_iter1.lyric"
    result = apply_action_to_config(action, manifest, target_config=target)
    text = target.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "Na1) AGN" in text
    assert "Na27)" in text
    assert "AGN" not in text.split("Ga1)", 1)[-1]


def test_multiband_fourier_m1_modifies_disk_in_place(tmp_path):
    source = tmp_path / "object.lyric"
    source.write_text(_multi_lyric(), encoding="utf-8")
    manifest = _manifest("multi-band")
    manifest["config_file"] = str(source)
    action = _decision({"action_type": "PROPOSE_ADD", "component": "fourier_m1"})
    target = tmp_path / "object_iter1.lyric"
    result = apply_action_to_config(action, manifest, target_config=target)
    text = target.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "Pa2) sersic_f" in text
    assert "Pa21) 1" in text
    assert "Pb1)" not in text


def test_single_band_constraint_operations_preserve_and_remove_source_constraint(tmp_path):
    source = tmp_path / "object.feedme"
    constraint = tmp_path / "source.cons"
    constraint.write_text("1 n 0.5 3\n", encoding="utf-8")
    source.write_text(_single_feedme().replace("G) none", f"G) {constraint} # constraints"), encoding="utf-8")
    manifest = _manifest()
    manifest["config_file"] = str(source)
    action = _decision({
        "action_type": "REFIT_PARAMETERS",
        "parameter_changes": [{
            "target_model_label": "obj0",
            "parameter": "n",
            "operation": "REMOVE_NONREQUIRED_CONSTRAINT",
        }],
    })
    target = tmp_path / "object_iter1.feedme"
    result = apply_action_to_config(action, manifest, target_config=target)
    assert result["ok"] is True
    assert "1 n" not in target.with_suffix(".cons").read_text(encoding="utf-8")
    assert f"G) {target.with_suffix('.cons')}" in target.read_text(encoding="utf-8")


def test_lifecycle_action_summary_records_executed_refit_verdict():
    action = {
        "action_type": "REFIT_PARAMETERS",
        "reason_code": "PARAMETER_BOUNDARY_HIT",
        "parameter_changes": [{
            "target_model_label": "obj0",
            "parameter": "n",
            "operation": "SET_BOUNDS",
            "lower": 0.5,
            "upper": 3.0,
        }],
    }
    decision = _decision(action)
    decision["refit_evaluation"] = {
        "candidate_action_type": "REFIT_PARAMETERS",
        "fit_converged": "yes",
        "residual_outcome": "improved",
        "parameters_physical": "yes",
    }
    lifecycle = build_lifecycle_record(
        workflow_manifest=_manifest(),
        raw_decision=decision,
        resolved_decision=decision,
        policy_state=PolicyState(object_id="object-1"),
        fit_result={
            "status": "success",
            "input_param_file": "/tmp/config.feedme",
            "optimized_fits_file": "/tmp/result.fits",
            "summary_file": "/tmp/summary.txt",
            "image_file": "/tmp/comparison.png",
        },
    )
    assert lifecycle["action_summary"]["executed_action_type"] == "REFIT_PARAMETERS"
    assert lifecycle["action_summary"]["refit_verdict"] == "ACCEPTED"


def test_lifecycle_accepts_preflight_action_summary(tmp_path):
    source = tmp_path / "object.feedme"
    source.write_text(_single_feedme(), encoding="utf-8")
    manifest = _manifest()
    manifest["config_file"] = str(source)
    decision = _decision({
        "action_type": "REFIT_PARAMETERS",
        "parameter_changes": [{
            "target_model_label": "obj0",
            "parameter": "n",
            "operation": "FIX_VALUE",
            "value": 1,
        }],
    })
    preflight = apply_action_to_config(
        decision,
        manifest,
        target_config=tmp_path / "object_iter1.feedme",
    )
    lifecycle = record_fit_lifecycle(
        state=PolicyState(object_id="object-1"),
        workflow_manifest=manifest,
        raw_decision=decision,
        resolved_decision=decision,
        fit_result={
            "status": "success",
            "input_param_file": str(source),
            "optimized_fits_file": str(tmp_path / "result.fits"),
            "summary_file": str(tmp_path / "summary.txt"),
            "image_file": str(tmp_path / "comparison.png"),
        },
        action_summary=preflight["action_summary"],
    )
    assert lifecycle["action_summary"]["action_validated"] is True


def test_evaluate_workflow_refit_persists_acceptance_and_action_summary(tmp_path):
    state_file = tmp_path / "policy-state.json"
    state = PolicyState(object_id="object-1")
    result = evaluate_workflow_refit(
        state=state,
        round_id="round-2",
        component="disk",
        candidate_action_type="REFIT_PARAMETERS",
        refit_evaluation={
            "candidate_action_type": "REFIT_PARAMETERS",
            "fit_converged": "yes",
            "residual_outcome": "improved",
            "parameters_physical": "yes",
        },
        state_file=state_file,
        decision_ref="decision-2",
        config_ref="/tmp/object_iter1.feedme",
    )
    assert result["decision"]["action"]["action_type"] == "ACCEPT_REFIT"
    assert result["action_summary"] == {
        "raw_action_type": "ACCEPT_REFIT",
        "resolved_action_type": "ACCEPT_REFIT",
        "candidate_action_types": ["ACCEPT_REFIT"],
        "executed_action_type": "REFIT_PARAMETERS",
        "refit_verdict": "ACCEPTED",
        "fallback": None,
        "needs_review": False,
    }
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["last_round_id"] == "round-2"
    assert saved["last_decision_ref"] == "decision-2"


def test_evaluate_workflow_refit_rejects_inconclusive_optional_bic(tmp_path):
    state = PolicyState(object_id="object-1")
    result = evaluate_workflow_refit(
        state=state,
        round_id="round-2",
        component="lens",
        candidate_action_type="PROPOSE_ADD",
        refit_evaluation={
            "candidate_action_type": "PROPOSE_ADD",
            "fit_converged": "yes",
            "residual_outcome": "improved",
            "parameters_physical": "yes",
            "bic": {
                "bic_simple": 100.0,
                "bic_complex": 99.0,
                "bic_gain": 1.0,
                "comparable": True,
            },
        },
        state_file=tmp_path / "policy-state.json",
    )
    assert result["decision"]["action"]["action_type"] == "REJECT_REFIT"
    assert result["action_summary"]["refit_verdict"] == "REJECTED"
    assert "lens" in state.rejected_components


def test_evaluate_workflow_refit_stops_on_inconclusive_evidence(tmp_path):
    state = PolicyState(object_id="object-1")
    result = evaluate_workflow_refit(
        state=state,
        round_id="round-2",
        component="bar",
        refit_evaluation={
            "candidate_action_type": "PROPOSE_ADD",
            "fit_converged": "inconclusive",
            "residual_outcome": "improved",
            "parameters_physical": "yes",
        },
        state_file=tmp_path / "policy-state.json",
    )
    assert result["decision"]["workflow_status"] == "STOPPED_NEEDS_REVIEW"
    assert result["decision"]["action"]["action_type"] == "REJECT_REFIT"
    assert result["action_summary"]["refit_verdict"] == "REJECTED"


def _fit_artifact_fixture(tmp_path, name, residual_value, bic, round_id):
    from astropy.io import fits

    import numpy as np

    science = tmp_path / f"{name}.science.fits"
    result = tmp_path / f"{name}.result.fits"
    config = tmp_path / f"{name}.feedme"
    summary = tmp_path / f"{name}.summary.md"
    comparison = tmp_path / f"{name}.comparison.png"
    original = np.ones((20, 20), dtype=float)
    residual = np.full_like(original, residual_value)
    model = original - residual
    fits.PrimaryHDU(original).writeto(science)
    fits.HDUList([
        fits.PrimaryHDU(original),
        fits.ImageHDU(model, name="MODEL"),
        fits.ImageHDU(residual, name="RESIDUAL"),
    ]).writeto(result)
    config.write_text(_single_feedme(), encoding="utf-8")
    summary.write_text(
        f"# reduced chisq: 1.0\n# BIC: {bic}\n",
        encoding="utf-8",
    )
    comparison.write_bytes(b"comparison")
    manifest = _manifest()
    manifest.update({
        "round_id": round_id,
        "object_id": "object-1",
        "config_file": str(config),
        "result_files": [str(result)],
        "summary_file": str(summary),
        "comparison_png": str(comparison),
    })
    manifest["bands"][0].update({
        "science_fits": str(science),
        "result_fits": str(result),
        "result_hdus": {"original_hdu": 0, "model_hdu": 1, "residual_hdu": 2},
    })
    fit_result = {
        "status": "success",
        "input_param_file": str(config),
        "optimized_fits_file": str(result),
        "summary_file": str(summary),
        "image_file": str(comparison),
        "output_param_file": str(config),
    }
    return manifest, fit_result


def _multi_fit_artifact_fixture(tmp_path, name, residual_value, bic, round_id):
    manifest, fit_result = _fit_artifact_fixture(
        tmp_path, name, residual_value, bic, round_id
    )
    config = tmp_path / f"{name}.lyric"
    config.write_text(_multi_lyric(), encoding="utf-8")
    summary = tmp_path / f"{name}.gssummary"
    summary.write_text(
        f"# reduced chisq: 1.0\n# BIC: {bic}\n"
        "disk_xcen 10\n"
        "disk_ycen 10\n"
        "disk_Re 2\n"
        "disk_n 1\n"
        "disk_ang 0\n"
        "disk_axrat 0.8\n"
        "bar_xcen 10\n"
        "bar_ycen 10\n"
        "bar_Re 0.5\n"
        "bar_n 0.5\n"
        "bar_ang 0\n"
        "bar_axrat 0.7\n",
        encoding="utf-8",
    )
    manifest.update({
        "workflow_mode": "multi-band",
        "config_file": str(config),
        "summary_file": str(summary),
        "parameter_file": None,
        "parameter_files": [str(config)],
    })
    manifest["bands"][0].update({"band": "f200w"})
    fit_result = {
        "status": "success",
        "input_param_file": str(config),
        "result_fits": [manifest["bands"][0]["result_fits"]],
        "summary_files": [str(summary)],
        "comparison_png": manifest["comparison_png"],
        "params_files": [str(config)],
        "constrain_files": [],
    }
    return manifest, fit_result


def _proposal_with_action(manifest, output_dir, action):
    proposal = build_workflow_proposal(manifest, output_dir=output_dir)
    rule_decision = _decision(action)
    proposal.update({
        "rule_decision": rule_decision,
        "raw_decision": rule_decision["raw_decision"],
        "candidate_actions": rule_decision["candidate_actions"],
        "rule_trace": rule_decision["rule_trace"],
        "termination_checks": rule_decision["termination_checks"],
    })
    validate(proposal, "workflow_proposal")
    return proposal


def _recommendation(action):
    return {
        "schema_version": "agent-recommendation@v1",
        "recommended_rule_id": "TEST_RULE_V1",
        "candidate_priorities": [{"rule_id": "TEST_RULE_V1", "priority": 1}],
        "rationale": "the bounded mock candidate is selected for the lifecycle test",
        "evidence_refs": ["numeric.json"],
        "uncertainty": "medium",
        "suggested_action": action,
    }


@pytest.mark.parametrize("mode", ["single-band", "multi-band"])
def test_mock_workflow_full_lifecycle_for_each_mode(tmp_path, mode):
    fixture = (
        _fit_artifact_fixture
        if mode == "single-band"
        else _multi_fit_artifact_fixture
    )
    baseline_manifest, baseline_fit = fixture(
        tmp_path, "e2e-baseline", 0.1, 100.0, "round-1"
    )
    candidate_manifest, candidate_fit = fixture(
        tmp_path, "e2e-candidate", 0.05, 90.0, "round-2"
    )
    candidate_manifest["bands"][0]["science_fits"] = baseline_manifest["bands"][0]["science_fits"]
    action = {
        "action_type": "REFIT_PARAMETERS",
        "parameter_changes": [{
            "target_model_label": "obj0" if mode == "single-band" else "disk",
            "parameter": "n",
            "operation": "FIX_VALUE",
            "value": 1.0,
        }],
    }
    proposal = _proposal_with_action(
        baseline_manifest, tmp_path / "proposal", action
    )
    state = PolicyState(object_id="object-1")
    resolved = resolve_workflow_proposal(
        proposal,
        state=state,
        agent_recommendation=_recommendation(action),
    )
    assert resolved["next_transition"] == "RUN_FIT"
    target_config = tmp_path / (
        "e2e-action.lyric" if mode == "multi-band" else "e2e-action.feedme"
    )
    prepared = apply_action_to_config(
        resolved["decision"],
        baseline_manifest,
        target_config=target_config,
    )
    assert prepared["mcp_call"]["tool"] == (
        "run_galfits_image_fitting" if mode == "multi-band" else "run_galfit"
    )
    candidate_manifest["config_file"] = str(target_config)
    candidate_fit["input_param_file"] = str(target_config)
    if mode == "multi-band":
        candidate_fit["params_files"] = [str(target_config)]

    state_file = tmp_path / f"{mode}.state.json"
    lifecycle_file = tmp_path / f"{mode}.lifecycle.json"
    completed = complete_workflow_candidate(
        state=state,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit_result=baseline_fit,
        candidate_fit_result=candidate_fit,
        component="disk",
        candidate_action_type="REFIT_PARAMETERS",
        state_file=state_file,
        lifecycle_file=lifecycle_file,
        evidence_file=tmp_path / f"{mode}.refit.json",
    )
    assert completed["action_summary"]["refit_verdict"] == "ACCEPTED"

    converged_action = {
        "action_type": "CONVERGED",
        "termination_checks": [
            {"check_id": f"check-{index}", "status": "PASS"}
            for index in range(7)
        ],
    }
    converged = _decision(converged_action, workflow_status="CONVERGED")
    component_analysis = tmp_path / f"{mode}.component_analysis.md"
    component_analysis.write_text("mock component analysis evidence\n", encoding="utf-8")
    lifecycle = record_fit_lifecycle(
        state=state,
        workflow_manifest=candidate_manifest,
        raw_decision=converged,
        resolved_decision=converged,
        fit_result=candidate_fit,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
    )
    assert lifecycle["best_round_status"] == "PENDING_VERIFIER"
    verifier_file = tmp_path / f"{mode}.verifier.json"
    verifier = verify_best_round(
        workflow_manifest=candidate_manifest,
        lifecycle_file=lifecycle_file,
        component_analysis_file=component_analysis,
        output_file=verifier_file,
        verifier_assessment=_verifier_assessment("mock-client"),
        baseline_manifest=baseline_manifest,
        baseline_fit_result=baseline_fit,
    )
    assert verifier["verdict"] == "PASS"
    locked = lock_best_round(
        verifier_artifact_ref=verifier_file,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
    )
    assert locked["status"] == "LOCKED"


def test_normalize_fit_result_rejects_workplace_only_return(tmp_path):
    manifest, _ = _fit_artifact_fixture(tmp_path, "baseline", 0.1, 100.0, "round-1")
    artifact = normalize_fit_result(
        manifest,
        {"status": "success", "workplace": str(tmp_path)},
    )
    assert artifact["valid"] is False
    assert "result_files" in artifact["missing_fields"]
    assert "summary_files" in artifact["missing_fields"]
    assert "comparison_png" in artifact["missing_fields"]


def test_parameter_health_uses_profile_specific_required_parameters():
    from pathlib import Path

    from component_analysis.refit_comparator import _parameter_health

    manifest = _manifest()
    manifest["config_file"] = str(
        Path(__file__).parent / "test_data" / "NGC1097.feedme"
    )
    health, boundary_hits, warnings = _parameter_health(manifest)
    assert health == "yes"
    assert boundary_hits == []
    assert warnings == []


def test_compare_refit_artifacts_uses_fits_residuals_and_summary_bic(tmp_path):
    baseline_manifest, baseline_fit = _fit_artifact_fixture(
        tmp_path, "baseline", 0.1, 100.0, "round-1"
    )
    candidate_manifest, candidate_fit = _fit_artifact_fixture(
        tmp_path, "candidate", 0.05, 90.0, "round-2"
    )
    candidate_manifest["bands"][0]["science_fits"] = baseline_manifest["bands"][0]["science_fits"]
    result = compare_refit_artifacts(
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit=baseline_fit,
        candidate_fit=candidate_fit,
        candidate_action_type="REFIT_PARAMETERS",
    )
    assert result["evidence"]["baseline_artifact"]["valid"] is True
    assert result["evidence"]["candidate_artifact"]["valid"] is True
    assert result["evaluation"]["residual_outcome"] == "improved"
    assert result["evaluation"]["parameters_physical"] == "yes"
    assert result["evaluation"]["bic"]["bic_gain"] == 10.0
    assert result["evidence"]["residual_score"]["candidate"] < result["evidence"]["residual_score"]["baseline"]


def test_complete_workflow_candidate_accepts_improved_real_artifact(tmp_path):
    baseline_manifest, baseline_fit = _fit_artifact_fixture(
        tmp_path, "baseline", 0.1, 100.0, "round-1"
    )
    candidate_manifest, candidate_fit = _fit_artifact_fixture(
        tmp_path, "candidate", 0.05, 90.0, "round-2"
    )
    candidate_manifest["bands"][0]["science_fits"] = baseline_manifest["bands"][0]["science_fits"]
    state = PolicyState(object_id="object-1")
    result = complete_workflow_candidate(
        state=state,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit_result=baseline_fit,
        candidate_fit_result=candidate_fit,
        component="disk",
        candidate_action_type="REFIT_PARAMETERS",
        state_file=tmp_path / "state.json",
        lifecycle_file=tmp_path / "lifecycle.json",
        evidence_file=tmp_path / "evidence.json",
    )
    assert result["decision"]["action"]["action_type"] == "ACCEPT_REFIT"
    assert result["rollback"]["applied"] is False
    assert state.current_config_ref == candidate_manifest["config_file"]
    assert result["action_summary"]["refit_verdict"] == "ACCEPTED"
    assert result["lifecycle"]["fit_result"]["valid"] is True


def test_complete_workflow_candidate_rejects_worse_artifact_and_restores_baseline(tmp_path):
    baseline_manifest, baseline_fit = _fit_artifact_fixture(
        tmp_path, "baseline", 0.1, 100.0, "round-1"
    )
    candidate_manifest, candidate_fit = _fit_artifact_fixture(
        tmp_path, "candidate", 0.2, 110.0, "round-2"
    )
    candidate_manifest["bands"][0]["science_fits"] = baseline_manifest["bands"][0]["science_fits"]
    state = PolicyState(object_id="object-1")
    result = complete_workflow_candidate(
        state=state,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit_result=baseline_fit,
        candidate_fit_result=candidate_fit,
        component="disk",
        candidate_action_type="REFIT_PARAMETERS",
        state_file=tmp_path / "state.json",
        lifecycle_file=tmp_path / "lifecycle.json",
        evidence_file=tmp_path / "evidence.json",
    )
    assert result["decision"]["action"]["action_type"] == "REJECT_REFIT"
    assert result["rollback"] == {
        "applied": True,
        "config_ref": baseline_manifest["config_file"],
    }
    assert state.current_config_ref == baseline_manifest["config_file"]
    assert state.current_result_refs["result_files[0]"] == baseline_manifest["result_files"][0]
    assert state.pending_action is None
    assert result["evidence"]["candidate_artifact"]["valid"] is True
    persisted = json.loads((tmp_path / "lifecycle.json").read_text(encoding="utf-8"))
    validate(persisted, "workflow_lifecycle")
    assert "lifecycle_file" not in persisted
    assert "fit_valid" not in persisted


def _converged_lifecycle_fixture(tmp_path):
    manifest, fit_result = _fit_artifact_fixture(
        tmp_path, "converged", 0.05, 90.0, "round-3"
    )
    action = {
        "action_type": "CONVERGED",
        "termination_checks": [
            {"check_id": f"check-{index}", "status": "PASS"}
            for index in range(7)
        ],
    }
    decision = _decision(action, workflow_status="CONVERGED")
    state_file = tmp_path / "converged.state.json"
    lifecycle_file = tmp_path / "converged.lifecycle.json"
    lifecycle = record_fit_lifecycle(
        state=PolicyState(object_id="object-1"),
        workflow_manifest=manifest,
        raw_decision=decision,
        resolved_decision=decision,
        fit_result=fit_result,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
    )
    component_analysis = tmp_path / "converged.component_analysis.md"
    component_analysis.write_text("residual analysis evidence\n", encoding="utf-8")
    return (
        manifest,
        lifecycle,
        fit_result,
        state_file,
        lifecycle_file,
        component_analysis,
    )


def _verifier_assessment(client_name="codex"):
    return {
        "schema_version": "verifier-assessment@v1",
        "dimensions": {
            "validation": "PASS",
            "components": "PASS",
            "fit": "PASS",
            "physical": "PASS",
            "parameters": "PASS",
            "metrics": "PASS",
        },
        "rationale": "structured assessment backed by the supplied evidence package",
        "evidence_refs": ["converged.component_analysis.md"],
        "client": {"name": client_name, "version": "1"},
        "model": {"name": "assessment-model", "version": "1"},
        "prompt_version": "verifier-prompt@v1",
    }


def test_verify_best_round_requires_structured_assessment_before_lock(tmp_path):
    manifest, _, _, state_file, lifecycle_file, component_analysis = _converged_lifecycle_fixture(tmp_path)
    verifier_file = tmp_path / "missing-assessment.verifier.json"
    verifier = verify_best_round(
        workflow_manifest=manifest,
        lifecycle_file=lifecycle_file,
        component_analysis_file=component_analysis,
        output_file=verifier_file,
    )
    assert verifier["verdict"] == "INCONCLUSIVE"
    assert verifier["lockable"] is False
    with pytest.raises(ValueError, match="not lockable"):
        lock_best_round(
            verifier_artifact_ref=verifier_file,
            state_file=state_file,
            lifecycle_file=lifecycle_file,
        )


def test_verifier_and_lock_use_structured_artifact_and_are_client_independent(tmp_path):
    manifest, _, _, state_file, lifecycle_file, component_analysis = _converged_lifecycle_fixture(tmp_path)
    first_file = tmp_path / "codex.verifier.json"
    second_file = tmp_path / "claude.verifier.json"
    first = verify_best_round(
        workflow_manifest=manifest,
        lifecycle_file=lifecycle_file,
        component_analysis_file=component_analysis,
        output_file=first_file,
        verifier_assessment=_verifier_assessment("codex"),
    )
    second = verify_best_round(
        workflow_manifest=manifest,
        lifecycle_file=lifecycle_file,
        component_analysis_file=component_analysis,
        output_file=second_file,
        verifier_assessment=_verifier_assessment("claude-code"),
    )
    assert first["verdict"] == second["verdict"] == "PASS"
    assert {
        key: (value["status"], value["detail"])
        for key, value in first["hard_gates"].items()
    } == {
        key: (value["status"], value["detail"])
        for key, value in second["hard_gates"].items()
    }
    assert first["lockable"] is True
    locked = lock_best_round(
        verifier_artifact_ref=first_file,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
    )
    assert locked["status"] == "LOCKED"
    assert json.loads(state_file.read_text(encoding="utf-8"))["best_round_status"] == "LOCKED"
    assert json.loads(lifecycle_file.read_text(encoding="utf-8"))["best_round_status"] == "LOCKED"
    with pytest.raises(FileNotFoundError):
        lock_best_round(
            verifier_artifact_ref="PASS",
            state_file=state_file,
            lifecycle_file=lifecycle_file,
        )

def test_initial_single_sersic_is_not_classified_as_disk(tmp_path):
    from component_analysis import workflow_fit_components

    source = tmp_path / "single.feedme"
    source.write_text(_single_feedme(), encoding="utf-8")
    manifest = _manifest()
    manifest["config_file"] = str(source)

    components = workflow_fit_components(manifest)

    assert len(components) == 1
    assert components[0]["type"].lower().startswith("sersic")
    assert components[0]["component"] is None


def test_inline_semantic_label_is_preserved_for_next_round_profile(tmp_path):
    from component_analysis.artifact_adapter import _parse_profile_definitions

    source = tmp_path / "promoted.lyric"
    source.write_text(
        "Pa1) obj0 # semantic_label: disk\nPa2) sersic\n"
        "Pa6) [1.0, 0.2, 8, 0.1, 0]\n",
        encoding="utf-8",
    )

    profile = _parse_profile_definitions(source)[0]

    assert "semantic_label: disk" in profile["comments"].lower()

def test_multiband_result_files_are_aligned_by_band_token(tmp_path):
    manifest, fit_result = _multi_fit_artifact_fixture(
        tmp_path, "band-order", 0.1, 100.0, "round-1"
    )
    f115w = tmp_path / "object_nircam_f115w_result.fits"
    f444w = tmp_path / "object_nircam_f444w_result.fits"
    f115w.write_bytes(b"f115w")
    f444w.write_bytes(b"f444w")
    template = manifest["bands"][0]
    manifest["bands"] = [
        {**template, "band": "nircam_f444w", "result_fits": str(f444w)},
        {**template, "band": "nircam_f115w", "result_fits": str(f115w)},
    ]
    manifest["result_files"] = [str(f444w), str(f115w)]
    fit_result["result_fits"] = [str(f115w), str(f444w)]

    artifact = normalize_fit_result(manifest, fit_result)

    assert artifact["valid"] is True
    assert [item["result_fits"] for item in artifact["bands"]] == [
        str(f444w),
        str(f115w),
    ]
    assert artifact["result_files"] == [str(f444w), str(f115w)]
