from __future__ import annotations

import asyncio

from mcp.server import FastMCP

from schemas import iter_errors, validate
from tools.workflow_lifecycle import (
    _order_multiband_results,
    workflow_complete_candidate,
    workflow_capabilities,
    workflow_lock_best_round,
    workflow_verify_best_round,
)


def test_multiband_result_files_are_ordered_by_explicit_band_name():
    infos = [{"band": "nircam_f444w"}, {"band": "nircam_f115w"}]
    returned = [
        "/tmp/obj_nircam_f115w_result.fits",
        "/tmp/obj_nircam_f444w_result.fits",
    ]
    assert _order_multiband_results(returned, infos) == [
        "/tmp/obj_nircam_f444w_result.fits",
        "/tmp/obj_nircam_f115w_result.fits",
    ]


def test_workflow_capabilities_is_client_independent(monkeypatch):
    monkeypatch.setenv("COMPONENT_ANALYSIS_WORKFLOW_PILOT", "1")
    result = workflow_capabilities()

    assert result["contract_version"] == "workflow-contract@v1"
    assert result["client_independent"] is True
    assert result["pilot_enabled"] is True
    assert result["workflow_modes"] == ["single-band", "multi-band"]
    assert result["fitting_entrypoints"]["single-band"] == "run_galfit"
    assert result["fitting_entrypoints"]["multi-band"] == "run_galfits_image_fitting"
    assert result["agent_authority"]["can_write_config"] is False
    assert result["agent_authority"]["can_bypass_policy"] is False


def test_agent_recommendation_schema_is_registered_and_bounded():
    recommendation = {
        "schema_version": "agent-recommendation@v1",
        "recommended_rule_id": "RULE_A",
        "candidate_priorities": [{"rule_id": "RULE_A", "priority": 1}],
        "rationale": "candidate is supported by the supplied evidence",
        "evidence_refs": ["numeric.json"],
        "uncertainty": "medium",
        "suggested_action": {"action_type": "PROPOSE_ADD", "component": "lens"},
        "parameter_plan": None,
    }

    validate(recommendation, "agent_recommendation")
    assert iter_errors(recommendation, "agent_recommendation") == []


def test_agent_recommendation_schema_rejects_execution_fields():
    recommendation = {
        "schema_version": "agent-recommendation@v1",
        "recommended_rule_id": "RULE_A",
        "candidate_priorities": [{"rule_id": "RULE_A", "priority": 1}],
        "rationale": "candidate is supported by the supplied evidence",
        "evidence_refs": ["numeric.json"],
        "uncertainty": "medium",
        "mcp_call": {"tool": "run_galfit"},
    }

    assert iter_errors(recommendation, "agent_recommendation")


def test_fastmcp_contract_exposes_logical_verifier_and_lock_tools():
    app = FastMCP("workflow-contract")
    for tool in (
        workflow_capabilities,
        workflow_complete_candidate,
        workflow_verify_best_round,
        workflow_lock_best_round,
    ):
        app.add_tool(tool)

    listed = asyncio.run(app.list_tools())
    by_name = {tool.name: tool for tool in listed}
    assert {
        "workflow_capabilities",
        "workflow_complete_candidate",
        "workflow_verify_best_round",
        "workflow_lock_best_round",
    } <= set(by_name)
    lock_schema = by_name["workflow_lock_best_round"].inputSchema
    assert "verifier_artifact_ref" in lock_schema["properties"]
    assert "verdict" not in lock_schema["properties"]
    verify_schema = by_name["workflow_verify_best_round"].inputSchema
    assert "verifier_assessment" in verify_schema["properties"]
    assert "output_file" in verify_schema["required"]

    _, capabilities = asyncio.run(app.call_tool("workflow_capabilities", {}))
    assert capabilities["client_independent"] is True
    assert capabilities["fitting_entrypoints"] == {
        "single-band": "run_galfit",
        "multi-band": "run_galfits_image_fitting",
    }
