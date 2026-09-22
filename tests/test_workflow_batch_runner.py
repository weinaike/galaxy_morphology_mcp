"""Tests for the stage-H batch manifest and state contracts."""

import asyncio
import json
from pathlib import Path

import pytest

from schemas import validate
from tools.workflow_batch_runner import (
    BatchManifestError,
    batch_summary,
    build_batch_manifest,
    discover_objects,
    load_object_state,
    main,
    save_object_state,
    state_path,
    write_batch_manifest,
)
from tools.workflow_batch_runner import (
    _action_component,
    _action_baseline_fingerprint,
    _action_attempt_fingerprint,
    _gpu_oom_object_ids,
    _is_gpu_oom,
    _prepare_cpu_fallback,
    _terminal_review_decision,
    _write_lifecycle_artifact,
)


def _write_fixture_object(root: Path, object_id: str = "obj1") -> Path:
    galaxy = root / object_id
    galaxy.mkdir(parents=True)
    (galaxy / "science.fits").write_bytes(b"science")
    (galaxy / "mask.fits").write_bytes(b"mask")
    (galaxy / "psf.fits").write_bytes(b"psf")
    (galaxy / "obj1.lyric").write_text(
        "\n".join(
            [
                f"R1) {object_id}",
                "Ia1) [science.fits,0]",
                "Ia2) nircam_f200w",
                "Ia3) [none,0]",
                "Ia4) [psf.fits,0]",
                "Ia6) [mask.fits,0]",
                "Ia8) 25.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return galaxy


def test_manifest_discovers_explicit_inputs_and_validates(tmp_path):
    root = tmp_path / "batch"
    _write_fixture_object(root)
    manifest = build_batch_manifest(root, tmp_path / "run", pilot=True, use_vlm=True)

    validate(manifest, "workflow_batch_manifest")
    assert manifest["objects"][0]["preflight_status"] == "READY"
    assert manifest["objects"][0]["bands"] == ["nircam_f200w"]
    roles = {item["role"] for item in manifest["objects"][0]["input_files"]}
    assert roles == {"config_file", "nircam_f200w:science", "nircam_f200w:psf", "nircam_f200w:mask"}
    assert any("sigma" in issue and "UNAVAILABLE" in issue for issue in manifest["objects"][0]["issues"])


def test_missing_required_input_is_preflight_failure(tmp_path):
    root = tmp_path / "batch"
    galaxy = _write_fixture_object(root)
    (galaxy / "mask.fits").unlink()

    objects = discover_objects(root)
    assert objects[0]["preflight_status"] == "PREFLIGHT_FAILED"
    assert any("mask" in issue and "file not found" in issue for issue in objects[0]["issues"])


def test_duplicate_base_lyrics_are_rejected(tmp_path):
    root = tmp_path / "batch"
    galaxy = _write_fixture_object(root)
    (galaxy / "second.lyric").write_text(
        (galaxy / "obj1.lyric").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    objects = discover_objects(root)
    assert objects[0]["preflight_status"] == "PREFLIGHT_FAILED"
    assert "found 2" in objects[0]["issues"][0]


def test_jwst0831_manifest_is_limited_to_authorized_three_objects(tmp_path):
    root = tmp_path / "jwst0831"
    for object_id in ("104", "1071", "1118", "1182"):
        _write_fixture_object(root, object_id)

    objects = discover_objects(root, mode="multi-band")

    assert [item["object_id"] for item in objects] == ["104", "1071", "1118"]
    with pytest.raises(BatchManifestError, match="outside the authorized allowlist"):
        discover_objects(root, mode="multi-band", object_id="1182")


def test_state_roundtrip_and_structured_summary(tmp_path):
    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir)
    write_batch_manifest(manifest)
    state = load_object_state(run_dir, "obj1", preflight_status="READY")
    state["status"] = "COMPLETED_WITH_REVIEW"
    state["action_summary"]["resolved"] = {"action_type": "REFIT_PARAMETERS"}
    save_object_state(run_dir, state)

    restored = load_object_state(run_dir, "obj1")
    summary = batch_summary(manifest)
    assert restored["status"] == "COMPLETED_WITH_REVIEW"
    assert summary["counts"] == {"COMPLETED_WITH_REVIEW": 1}
    assert summary["provider_timing"]["rounds"] == 0
    assert state_path(run_dir, "obj1").exists()
    json.loads((run_dir / "batch_manifest.json").read_text(encoding="utf-8"))


def test_batch_summary_aggregates_downstream_and_provider_timing(tmp_path):
    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir)
    write_batch_manifest(manifest)
    state = load_object_state(run_dir, "obj1")
    state["status"] = "COMPLETED_WITH_REVIEW"
    state["downstream"] = {
        "image": "COMPLETED",
        "sed": "COMPLETED",
        "image_sed": "COMPLETED",
    }
    save_object_state(run_dir, state)
    proposal_dir = run_dir / "objects" / "obj1" / "round_000" / "proposal"
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "workflow_proposal.json").write_text(
        json.dumps(
            {
                "provider": {"status": "USED"},
                "timing": {
                    "duration_s": 3.5,
                    "fallback_status": "OK",
                    "timing_log_ref": "/data/obj1/timing_log.md",
                    "attempts": [
                        {"parse_status": "PARSE_FAILED"},
                        {"parse_status": "OK"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    summary = batch_summary(manifest)

    assert summary["downstream_counts"] == {
        "image": {"COMPLETED": 1},
        "sed": {"COMPLETED": 1},
        "image_sed": {"COMPLETED": 1},
    }
    assert summary["provider_timing"] == {
        "rounds": 1,
        "attempts": 2,
        "duration_s": 3.5,
        "provider_status_counts": {"USED": 1},
        "parse_status_counts": {"PARSE_FAILED": 1, "OK": 1},
        "fallback_status_counts": {"OK": 1},
        "timing_log_refs": ["/data/obj1/timing_log.md"],
    }


def test_cli_dry_run_writes_manifest_and_summary(tmp_path):
    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"

    assert main(["--root", str(root), "--run-dir", str(run_dir), "--pilot", "1", "--dry-run"]) == 0
    assert (run_dir / "batch_manifest.json").exists()
    assert (run_dir / "batch_summary.json").exists()


def test_unsafe_state_object_id_is_rejected(tmp_path):
    with pytest.raises(BatchManifestError):
        state_path(tmp_path, "../escape")


def test_refit_action_component_uses_rule_reason():
    assert _action_component(
        {"action_type": "REFIT_PARAMETERS", "reason_code": "DISK_N_NOT_FIXED"},
        {"workflow_mode": "multi-band"},
    ) == "disk"


def test_gpu_oom_detection_is_specific():
    assert _is_gpu_oom({"status": "failure", "log": "CUDA out of memory"})
    assert _is_gpu_oom("XLA runtime error: RESOURCE_EXHAUSTED")
    assert not _is_gpu_oom({"status": "failure", "error": "invalid lyric"})


def test_cpu_fallback_records_resource_switch(tmp_path):
    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resource_profile="serial-gpu")
    write_batch_manifest(manifest)
    state = load_object_state(run_dir, "obj1")
    state["status"] = "FAILED_NEEDS_REVIEW"
    state["needs_review"] = True
    state["termination_reason"] = (
        "GPUOutOfMemoryError: GPU OOM during baseline image fit"
    )
    save_object_state(run_dir, state)

    object_ids = _gpu_oom_object_ids(manifest)
    _prepare_cpu_fallback(manifest, object_ids)

    assert object_ids == ["obj1"]
    restored = load_object_state(run_dir, "obj1")
    assert restored["status"] == "RUNNING"
    assert restored["resource_profile"] == "serial-cpu"
    assert restored["resource_switches"][0]["from"] == "serial-gpu"
    assert restored["resource_switches"][0]["to"] == "serial-cpu"


class _FakeMCPClient:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = []
        self.fit_count = 0
        self.refit_verdict = "ACCEPTED"
        self.verifier_pass = False

    async def call_tool(self, name, arguments=None, **kwargs):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "workflow_capabilities":
            return {
                "pilot_enabled": True,
                "workflow_modes": ["multi-band"],
                "timing_log_enabled": True,
            }
        if name == "run_galfits_image_fitting":
            self.fit_count += 1
            return {
                "status": "success",
                "input_param_file": arguments["config_file"],
                "workplace": f"workplace-{self.fit_count}",
            }
        if name == "build_workflow_round_manifest":
            return {
                "schema_version": "1.0",
                "workflow_mode": "multi-band",
                "object_id": arguments["object_id"],
                "round_id": arguments["round_id"],
                "config_file": arguments["config_file"],
                "result_files": ["result.fits"],
                "summary_file": "summary.gssummary",
                "comparison_png": None,
                "working_note_file": None,
                "constraint_files": [],
                "parameter_file": None,
                "parameter_files": [],
                "bands": [{"band": "F200W", "science_fits": "science.fits", "science_hdu": 0, "result_fits": "result.fits", "result_hdus": {"original_hdu": 4, "model_hdu": 3, "residual_hdu": 0}, "fit_region": [0, 9, 0, 9]}],
            }
        if name == "workflow_propose_round":
            manifest = arguments["workflow_manifest"]
            return {
                "schema_version": "1.0",
                "workflow_mode": "multi-band",
                "object_id": manifest["object_id"],
                "round_id": manifest["round_id"],
                "manifest_ref": manifest["config_file"],
                "numeric_evidence": {},
                "vlm_evidence": {},
                "evidence_fingerprint": f"fixture-{manifest['round_id']}",
                "rule_decision": {},
                "raw_decision": {},
                "candidate_actions": [],
                "rule_trace": [],
                "termination_checks": [],
                "current_components": [],
                "provider": {"status": "DISABLED", "attempts": []},
            }
        if name == "workflow_resolve_round":
            decision = next(self.decisions)
            return {
                "decision": decision,
                "action_summary": {
                    "raw_action_type": decision["action"]["action_type"],
                    "resolved_action_type": decision["action"]["action_type"],
                    "candidate_action_types": [decision["action"]["action_type"]],
                    "executed_action_type": None,
                    "refit_verdict": None,
                    "fallback": None,
                    "needs_review": False,
                },
                "decision_ref": "decision-ref",
            }
        if name == "workflow_analysis_artifact":
            return {}
        if name == "record_workflow_fit_lifecycle":
            manifest = arguments["workflow_manifest"]
            decision = arguments["resolved_decision"]
            return {
                "schema_version": "1.0",
                "bridge_version": "workflow-bridge@v1",
                "lifecycle_version": "workflow-lifecycle@v1",
                "workflow_mode": "multi-band",
                "object_id": manifest["object_id"],
                "round_id": manifest["round_id"],
                "manifest_ref": manifest["config_file"],
                "raw_decision": arguments.get("raw_decision", decision),
                "resolved_decision": decision,
                "policy_state": {},
                "fit_result": {"valid": True, "refs": {}, "raw": arguments["fit_result"]},
                "fit_completion_status": "FIT_AVAILABLE",
                "best_round_status": "PENDING_VERIFIER",
                "needs_review": False,
                "next_step": "evaluate_refit_or_analyze_next_round",
                "downstream_handoff": {"image_result_available": True, "sed_joint_eligible": False, "best_round_status": "PENDING_VERIFIER", "preserve_sed_joint_logic": True},
                "action_summary": arguments.get("action_summary") or {"raw_action_type": None, "resolved_action_type": None, "candidate_action_types": [], "executed_action_type": None, "refit_verdict": None, "fallback": None, "needs_review": False},
            }
        if name == "workflow_action_preflight":
            action_type = arguments["decision_artifact"]["action"]["action_type"]
            if action_type == "REFIT_PARAMETERS":
                return {
                    "ok": True,
                    "action": arguments["decision_artifact"]["action"],
                    "mcp_call": {
                        "tool": "run_galfits_image_fitting",
                        "arguments": {
                            "config_file": "generated.lyric",
                            "extra_args": [],
                        },
                    },
                }
            return {
                "ok": False,
                "reason_code": "REMOVE_FEATURE_DISABLED",
                "action_summary": {},
            }
        if name == "workflow_action_config":
            return {
                "ok": True,
                "config_file": "generated.lyric",
                "mcp_call": {
                    "tool": "run_galfits_image_fitting",
                    "arguments": {
                        "config_file": "generated.lyric",
                        "extra_args": [],
                    },
                },
            }
        if name == "workflow_complete_candidate":
            return {
                "action_summary": {"refit_verdict": self.refit_verdict},
                "evaluation": {
                    "candidate_action_type": "REFIT_PARAMETERS",
                    "fit_converged": "yes",
                    "residual_outcome": "improved",
                    "parameters_physical": "yes",
                    "boundary_hits": [],
                    "degeneracy_warnings": [],
                    "bic": None,
                    "reduced_chisq": None,
                },
            }
        if name == "workflow_evaluate_refit":
            return {"decision": {}, "action_summary": {"refit_verdict": self.refit_verdict}}
        if name == "check_lyric_file":
            return {"status": "success"}
        if name == "workflow_verify_best_round":
            manifest = arguments["workflow_manifest"]
            verdict = "PASS" if self.verifier_pass else "INCONCLUSIVE"
            return {
                "schema_version": "workflow-verifier@v1", "workflow_mode": "multi-band",
                "object_id": manifest["object_id"], "round_id": manifest["round_id"],
                "verdict": verdict, "lockable": self.verifier_pass,
                "hard_gates": {key: {"status": "PASS", "evidence_refs": [], "detail": "fixture"} for key in ("artifact_integrity", "parameter_health", "constraint_integrity", "comparable_metrics", "lifecycle_integrity")},
                "assessment_status": "MISSING", "verifier_assessment": None,
                "evidence_refs": {"workflow_manifest": "manifest", "lifecycle": "lifecycle", "fit_artifact": "fit", "component_analysis": "component"},
                "generated_by": {"name": "workflow-verifier", "version": "workflow-verifier@v1"},
            }
        if name == "workflow_lock_best_round":
            return {"status": "LOCKED"}
        if name == "run_galfits_sed_fitting":
            return {"status": "success", "new_lyric_file": "sed.lyric"}
        if name == "run_galfits_image_sed_fitting":
            return {"status": "success"}
        raise AssertionError(f"unexpected fake MCP tool: {name}")


def _fake_decision(object_id, action_type, **action_fields):
    action = {"action_type": action_type, **action_fields}
    if action_type == "REFIT_PARAMETERS" and not isinstance(action.get("parameter_changes"), list):
        action["parameter_changes"] = [{"target_model_label": "obj0", "parameter": "n", "operation": "FIX_VALUE", "value": 1.0}]
    if action_type == "PROPOSE_REMOVE":
        action.setdefault("target_model_label", "obj0")
    if action_type == "COLLECT_EVIDENCE":
        action.setdefault("continuation_reason", "collect more evidence")
        action.setdefault("next_step", "analyze changed evidence")
        action.setdefault("next_transition", "COLLECT_EVIDENCE")
        action.setdefault("collector_id", "refresh_numeric_and_vlm_evidence")
        action.setdefault("evidence_targets", ["residual_profile"])
        action.setdefault("expected_new_fingerprint", "new:fingerprint")
    if action_type == "CONVERGED":
        action["termination_checks"] = [{"check_id": "check", "status": "PASS"}] * 7

    return {
        "schema_version": "1.1",
        "round_id": f"{object_id}-round",
        "rules_version": "rules@v1",
        "thresholds_version": "thresholds@v1",
        "state": "PROPOSE",
        "action": action,
        "raw_decision": {
            "state": "PROPOSE",
            "action": action,
            "rule_trace": [{"rule_id": "TEST_RULE", "outcome": "SATISFIED"}],
        },
        "rule_trace": [{"rule_id": "TEST_RULE", "outcome": "SATISFIED"}],
        "candidate_actions": [],
        "evidence_refs": {"numeric_evidence": None, "vlm_evidence": None},
        "termination_checks": [{"check_id": "check", "status": "PASS"}] * 7,
        "workflow_status": "CONVERGED" if action_type == "CONVERGED" else "CONTINUE",
    }


def test_coordinator_consumes_resolved_action_and_runs_handoff(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    fake = _FakeMCPClient(
        [
            _fake_decision("obj1", "REFIT_PARAMETERS", component="disk", parameter_changes={"re": 1.0}),
            _fake_decision("obj1", "CONVERGED"),
        ]
    )
    fake.verifier_pass = True

    result = asyncio.run(
        WorkflowBatchCoordinator(manifest, fake, max_rounds=3).run()
    )

    names = [name for name, _ in fake.calls]
    assert names.count("run_galfits_image_fitting") == 2
    assert "workflow_action_config" in names
    assert "workflow_complete_candidate" in names
    assert "workflow_verify_best_round" in names
    assert "run_galfits_sed_fitting" in names
    assert "workflow_evaluate_refit" in names
    assert "run_galfits_image_sed_fitting" in names
    assert result["summary"]["counts"] == {"COMPLETED": 1}
    state = load_object_state(run_dir, "obj1")
    baseline = Path(state["baseline_config_file"])
    assert baseline.parent.name == "configs"
    assert baseline.name.endswith("_baseline.lyric")
    assert state["current_config_file"]
    assert state["current_config_file"].endswith(".lyric")
    run_index = Path(state["run_index_file"])
    index = json.loads(run_index.read_text(encoding="utf-8"))
    assert len(index["rounds"]) == 2
    assert all(item["baseline"]["config_file"] for item in index["rounds"])


def test_collect_evidence_advances_to_next_image_round(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    fake = _FakeMCPClient(
        [
            _fake_decision("obj1", "COLLECT_EVIDENCE"),
            _fake_decision("obj1", "CONVERGED"),
        ]
    )
    fake.verifier_pass = True

    result = asyncio.run(
        WorkflowBatchCoordinator(manifest, fake, max_rounds=3).run()
    )

    names = [name for name, _ in fake.calls]
    assert names.count("run_galfits_image_fitting") == 1
    assert names.count("workflow_propose_round") == 2
    assert "workflow_verify_best_round" in names
    assert "run_galfits_sed_fitting" in names
    assert result["summary"]["counts"] == {"COMPLETED": 1}

def test_remove_is_preflighted_but_never_auto_executed(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    fake = _FakeMCPClient([_fake_decision("obj1", "PROPOSE_REMOVE", component="bar")])

    result = asyncio.run(WorkflowBatchCoordinator(manifest, fake).run())

    names = [name for name, _ in fake.calls]
    assert "workflow_action_preflight" in names
    assert "workflow_action_config" not in names
    assert "workflow_complete_candidate" not in names
    assert result["summary"]["counts"] == {"COMPLETED_WITH_REVIEW": 1}


def test_rejected_action_is_not_repeated_on_unchanged_baseline(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    action = _fake_decision(
        "obj1",
        "REFIT_PARAMETERS",
        component="disk",
        parameter_changes={"n": 1.0},
    )
    fake = _FakeMCPClient([action, action])
    fake.refit_verdict = "REJECTED"

    result = asyncio.run(
        WorkflowBatchCoordinator(manifest, fake, max_rounds=3).run()
    )

    names = [name for name, _ in fake.calls]
    assert names.count("run_galfits_image_fitting") == 2
    assert names.count("workflow_action_config") == 1
    assert names.count("workflow_complete_candidate") == 1
    assert result["summary"]["counts"] == {"COMPLETED_WITH_REVIEW": 1}
    restored = load_object_state(run_dir, "obj1")
    assert restored["termination_reason"] == (
        "duplicate rejected action on unchanged baseline"
    )
    lifecycle_calls = [
        arguments
        for name, arguments in fake.calls
        if name == "record_workflow_fit_lifecycle"
    ]
    terminal = lifecycle_calls[-1]["resolved_decision"]
    assert terminal["workflow_status"] == "STOPPED_NEEDS_REVIEW"
    assert terminal["action"] is None
    assert terminal["automation"]["needs_review"] is True
    assert lifecycle_calls[-1]["lifecycle_file"].endswith(
        "round_001/terminal_lifecycle.json"
    )


def test_rejected_action_baseline_fingerprint_ignores_changed_evidence():
    action = _fake_decision(
        "obj1",
        "REFIT_PARAMETERS",
        component="disk",
        parameter_changes={"n": 1.0},
    )["action"]
    manifest = {
        "config_file": "/tmp/obj1.lyric",
        "result_files": ["/tmp/f200w.fits"],
        "summary_file": "/tmp/f200w.txt",
    }
    first = dict(_fake_decision("obj1", "REFIT_PARAMETERS"), evidence_fingerprint="one")
    second = dict(_fake_decision("obj1", "REFIT_PARAMETERS"), evidence_fingerprint="two")

    assert _action_attempt_fingerprint(action, manifest, first) != _action_attempt_fingerprint(
        action, manifest, second
    )
    assert _action_baseline_fingerprint(action, manifest) == _action_baseline_fingerprint(
        action, manifest
    )


def test_terminal_review_decision_preserves_raw_action():
    decision = _fake_decision(
        "obj1",
        "REFIT_PARAMETERS",
        component="disk",
        parameter_changes={"n": 1.0},
    )

    terminal = _terminal_review_decision(
        decision,
        reason="duplicate rejected action on unchanged baseline",
    )

    assert terminal["action"] is None
    assert terminal["raw_decision"]["action"]["action_type"] == "REFIT_PARAMETERS"
    assert terminal["workflow_status"] == "STOPPED_NEEDS_REVIEW"
    assert terminal["automation"]["resolution"] == "rule_terminated"


def test_lifecycle_writer_removes_mcp_return_metadata(tmp_path):
    target = tmp_path / "lifecycle.json"
    result = {
        "schema_version": "1.0",
        "bridge_version": "workflow-bridge@v1",
        "lifecycle_version": "workflow-lifecycle@v1",
        "workflow_mode": "multi-band",
        "object_id": "obj1",
        "round_id": "round-0",
        "manifest_ref": "manifest",
        "raw_decision": {},
        "resolved_decision": {"workflow_status": "STOPPED_NEEDS_REVIEW"},
        "policy_state": {},
        "fit_result": {"valid": True, "refs": {}, "raw": {}},
        "fit_completion_status": "FIT_AVAILABLE",
        "best_round_status": "UNLOCKED",
        "needs_review": True,
        "next_step": "review",
        "downstream_handoff": {"image_result_available": True, "sed_joint_eligible": False, "best_round_status": "UNLOCKED", "preserve_sed_joint_logic": True},
        "action_summary": {"raw_action_type": None, "resolved_action_type": None, "candidate_action_types": [], "executed_action_type": None, "refit_verdict": None, "fallback": None, "needs_review": True},
        "lifecycle_file": str(target),
        "fit_valid": True,
    }

    artifact = _write_lifecycle_artifact(target, result)

    assert "lifecycle_file" not in artifact
    assert "fit_valid" not in artifact
    assert json.loads(target.read_text(encoding="utf-8")) == artifact


def test_resume_does_not_repeat_terminal_object(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    state = load_object_state(run_dir, "obj1")
    state["status"] = "COMPLETED"
    save_object_state(run_dir, state)
    fake = _FakeMCPClient([])

    asyncio.run(WorkflowBatchCoordinator(manifest, fake).run())

    assert [name for name, _ in fake.calls] == ["workflow_capabilities"]


def test_resume_retries_only_incomplete_downstream(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    object_dir = run_dir / "objects" / "obj1"
    object_dir.mkdir(parents=True)
    fit_file = object_dir / "current_fit_result.json"
    fit_file.write_text(
        json.dumps({"status": "success", "workplace": "image-workplace"}),
        encoding="utf-8",
    )
    round_dir = object_dir / "round_000"
    round_dir.mkdir(parents=True)
    workflow_manifest_file = round_dir / "workflow_manifest.json"
    workflow_manifest_file.write_text(
        json.dumps({"config_file": str(root / "obj1" / "obj1.lyric")}),
        encoding="utf-8",
    )
    state = load_object_state(run_dir, "obj1")
    state.update(
        {
            "status": "COMPLETED_WITH_REVIEW",
            "current_fit_file": str(fit_file),
            "current_manifest_file": str(workflow_manifest_file),
            "downstream": {
                "image": "COMPLETED",
                "sed": "FAILED_NEEDS_REVIEW",
                "image_sed": "NOT_RUN",
            },
        }
    )
    save_object_state(run_dir, state)
    fake = _FakeMCPClient([])

    asyncio.run(WorkflowBatchCoordinator(manifest, fake).run())

    names = [name for name, _ in fake.calls]
    assert names == ["workflow_capabilities"]
    restored = load_object_state(run_dir, "obj1")
    assert restored["status"] == "COMPLETED_WITH_REVIEW"
    assert restored["downstream"] == {
        "image": "FIT_AVAILABLE",
        "sed": "NOT_RUN",
        "image_sed": "NOT_RUN",
        "reason": "Image best round is not verifier-authorized and LOCKED",
    }


def test_resume_recovers_interrupted_running_object_downstream(tmp_path):
    from tools.workflow_batch_runner import WorkflowBatchCoordinator

    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=True)
    write_batch_manifest(manifest)
    object_dir = run_dir / "objects" / "obj1"
    object_dir.mkdir(parents=True)
    fit_file = object_dir / "current_fit_result.json"
    fit_file.write_text(
        json.dumps({"status": "success", "workplace": "image-workplace"}),
        encoding="utf-8",
    )
    round_dir = object_dir / "round_000"
    round_dir.mkdir(parents=True)
    workflow_manifest_file = round_dir / "workflow_manifest.json"
    workflow_manifest_file.write_text(
        json.dumps({"config_file": str(root / "obj1" / "obj1.lyric")}),
        encoding="utf-8",
    )
    state = load_object_state(run_dir, "obj1")
    state.update(
        {
            "status": "RUNNING",
            "current_fit_file": str(fit_file),
            "current_manifest_file": str(workflow_manifest_file),
            "downstream": {
                "image": "COMPLETED",
                "sed": "FAILED_NEEDS_REVIEW",
                "image_sed": "NOT_RUN",
            },
        }
    )
    save_object_state(run_dir, state)
    fake = _FakeMCPClient([])

    asyncio.run(WorkflowBatchCoordinator(manifest, fake).run())

    names = [name for name, _ in fake.calls]
    assert names == ["workflow_capabilities"]


def test_cli_resume_overrides_manifest_resume_option(tmp_path, monkeypatch):
    root = tmp_path / "batch"
    _write_fixture_object(root)
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=False)
    write_batch_manifest(manifest)

    captured = {}

    class CaptureClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class CaptureCoordinator:
        def __init__(self, manifest, client, **kwargs):
            captured["manifest"] = manifest

        async def run(self, *, object_ids=None):
            captured["object_ids"] = object_ids
            return {"summary": {"counts": {}}}

    monkeypatch.setattr("tools.workflow_batch_runner.MCPToolClient", CaptureClient)
    monkeypatch.setattr(
        "tools.workflow_batch_runner.WorkflowBatchCoordinator",
        CaptureCoordinator,
    )

    assert main(
        ["--root", str(root), "--run-dir", str(run_dir), "--resume", "--pilot", "1"]
    ) == 0
    assert captured["manifest"]["options"]["resume"] is True
    assert captured["object_ids"] is None
    assert manifest["options"]["resume"] is False


def test_cli_resume_object_id_limits_existing_manifest(tmp_path, monkeypatch):
    root = tmp_path / "batch"
    _write_fixture_object(root, "obj1")
    _write_fixture_object(root, "obj2")
    run_dir = tmp_path / "run"
    manifest = build_batch_manifest(root, run_dir, resume=False)
    write_batch_manifest(manifest)

    captured = {}

    class CaptureClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class CaptureCoordinator:
        def __init__(self, manifest, client, **kwargs):
            pass

        async def run(self, *, object_ids=None):
            captured["object_ids"] = object_ids
            return {"summary": {"counts": {}}}

    monkeypatch.setattr("tools.workflow_batch_runner.MCPToolClient", CaptureClient)
    monkeypatch.setattr(
        "tools.workflow_batch_runner.WorkflowBatchCoordinator",
        CaptureCoordinator,
    )

    assert main(
        [
            "--root",
            str(root),
            "--run-dir",
            str(run_dir),
            "--resume",
            "--object-id",
            "obj2",
            "--pilot",
            "1",
        ]
    ) == 0
    assert captured["object_ids"] == {"obj2"}
