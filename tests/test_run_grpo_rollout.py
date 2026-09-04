import asyncio
import json

from data_gen.dataset_utils import _to_physical_id
from eval.run_grpo_rollout import (
    advance_online_parent,
    _prediction_summary,
    build_parser,
    classify_parent_kind,
    collect_parent_records,
    execute_prediction,
    load_physical_ids,
    select_parent_records,
    validate_model_spec_for_execution,
)


def _trajectory():
    return {
        "galaxy_id": "SDSS_gband_Plate0282_MJD51658_Fiber478_g",
        "_source_file": "/tmp/example_trajectory.json",
        "nodes": [
            {
                "node_id": "A",
                "parent_id": None,
                "depth": 0,
                "is_accepted": True,
                "feedme_path": "/tmp/A.feedme",
                "residual_path": "/tmp/A.png",
                "summary_path": "/tmp/A.md",
                "metrics": {"chi2_nu": 1.4, "bic": 100.0},
            },
            {
                "node_id": "B",
                "parent_id": "A",
                "depth": 1,
                "step": 1,
                "status": "success",
                "is_accepted": True,
                "mh_accepted": True,
                "delta_R": -1,
                "feedme_path": "/tmp/B.feedme",
                "residual_path": "/tmp/B.png",
                "summary_path": "/tmp/B.md",
                "metrics": {"chi2_nu": 1.5, "bic": 110.0},
            },
            {
                "node_id": "C",
                "parent_id": "B",
                "depth": 2,
                "step": 2,
                "status": "success",
                "is_accepted": True,
                "mh_accepted": False,
                "delta_R": 1,
                "feedme_path": "/tmp/C.feedme",
                "residual_path": "/tmp/C.png",
                "summary_path": "/tmp/C.md",
                "metrics": {"chi2_nu": 1.2, "bic": 90.0},
            },
            {
                "node_id": "D",
                "parent_id": "C",
                "depth": 3,
                "step": 3,
                "status": "success",
                "is_accepted": True,
                "mh_accepted": False,
                "delta_R": 1,
                "feedme_path": "/tmp/D.feedme",
                "residual_path": "/tmp/D.png",
                "summary_path": "/tmp/D.md",
                "metrics": {"chi2_nu": 1.1, "bic": 80.0},
            },
        ],
    }


def test_sft_input_parents_include_negative_state_b():
    rows, stats = collect_parent_records(
        [_trajectory()], parent_source="sft_inputs", require_files=False
    )
    assert [row["parent_id"] for row in rows] == ["B", "C"]
    assert [row["parent_kind"] for row in rows] == ["negative", "nonnegative"]
    assert rows[0]["parent_metrics"] == {"chi2_nu": 1.5, "bic": 110.0}
    assert stats["available_total"] == 2


def test_collect_parents_can_exclude_final_test_physical_id():
    tree = _trajectory()
    excluded = {_to_physical_id(tree["galaxy_id"])}
    rows, stats = collect_parent_records(
        [tree],
        excluded_physical_ids=excluded,
        parent_source="sft_inputs",
        require_files=False,
    )
    assert rows == []
    assert stats["excluded_test_trajectory"] == 1


def test_parent_kind_is_about_local_improvement_not_search_acceptance():
    negative = {
        "parent_id": "A",
        "depth": 1,
        "is_accepted": True,
        "delta_R": -1,
    }
    assert classify_parent_kind(negative) == "negative"


def test_stratified_parent_selection_is_deterministic_and_covers_kinds():
    rows = [
        {"group_id": f"g{i}", "parent_kind": kind}
        for i, kind in enumerate(
            ["root", "root", "negative", "negative", "nonnegative", "nonnegative"]
        )
    ]
    first = select_parent_records(
        rows, max_parents=3, seed=42, stratify_parent_kind=True
    )
    second = select_parent_records(
        rows, max_parents=3, seed=42, stratify_parent_kind=True
    )
    assert first == second
    assert {row["parent_kind"] for row in first} == {
        "root",
        "negative",
        "nonnegative",
    }


def test_prediction_summary_reports_group_diversity():
    rows = [
        {"group_id": "a", "prediction": "x", "parse_ok": True, "action_type": "add"},
        {"group_id": "a", "prediction": "x", "parse_ok": True, "action_type": "add"},
        {"group_id": "b", "prediction": "y", "parse_ok": False, "action_type": "unknown"},
        {"group_id": "b", "prediction": "z", "parse_ok": True, "action_type": "modify"},
    ]
    report = _prediction_summary(rows)
    assert report["num_groups"] == 2
    assert report["parse_rate"] == 0.75
    assert report["mean_unique_responses_per_group"] == 1.5
    assert report["groups_with_all_unique_responses"] == 1


def test_invalid_model_response_is_policy_invalid(tmp_path):
    prediction = {
        "group_id": "g",
        "group_index": 0,
        "candidate_id": "c0",
        "candidate_index": 0,
        "galaxy_id": "galaxy",
        "physical_id": "physical",
        "parent_id": "A",
        "parent_kind": "root",
        "next_step": 1,
        "prediction": "not json",
        "pred_spec": None,
        "action_type": "unknown",
    }
    result = asyncio.run(
        execute_prediction(
            prediction,
            {"feedme_path": str(tmp_path / "A.feedme")},
            work_root=str(tmp_path),
            max_iter=100,
            use_vlm=False,
            vlm_model=None,
            api_key=None,
        )
    )
    assert result["outcome"] == "policy_invalid"
    assert result["failure_reason"] == "no_valid_json_spec"


def test_incomplete_component_spec_is_policy_invalid_before_evaluator(tmp_path):
    parent_feedme = tmp_path / "A.feedme"
    parent_feedme.write_text("not needed: validation must run first", encoding="utf-8")
    prediction = {
        "group_id": "g",
        "group_index": 0,
        "candidate_id": "c0",
        "candidate_index": 0,
        "prediction": "",
        "pred_spec": {
            "components": [
                {
                    "role": "disk",
                    "model": "expdisk",
                    "mag": 16.0,
                    "re": None,
                    "q": 0.7,
                    "pa": 30.0,
                }
            ]
        },
        "action_type": "modify",
    }
    result = asyncio.run(
        execute_prediction(
            prediction,
            {"feedme_path": str(parent_feedme)},
            work_root=str(tmp_path),
            max_iter=100,
            use_vlm=False,
            vlm_model=None,
            api_key=None,
        )
    )
    assert result["outcome"] == "policy_invalid"
    assert result["failure_stage"] == "response_validation"
    assert result["failure_reason"] == "invalid_model_spec: component_0_missing_or_null:re"


def test_advance_online_parent_uses_successful_galfit_artifacts():
    parent = {
        "group_id": "galaxy::root",
        "parent_id": "root",
        "parent_depth": 0,
        "next_step": 1,
        "feedme_path": "/old.feedme",
    }
    rollout = {
        "outcome": "success",
        "candidate_id": "candidate-1",
        "model_feedme_path": "/new.feedme",
        "model_residual_path": "/new.png",
        "model_summary_path": "/new.md",
        "model_metrics": {"chi2_nu": 1.1},
    }

    next_parent = advance_online_parent(parent, rollout)

    assert next_parent["group_id"] == parent["group_id"]
    assert next_parent["parent_id"] == "candidate-1"
    assert next_parent["parent_depth"] == 1
    assert next_parent["next_step"] == 2
    assert next_parent["feedme_path"] == "/new.feedme"


def test_advance_online_parent_ends_failed_trajectory():
    assert advance_online_parent({}, {"outcome": "policy_invalid"}) is None


def test_execution_spec_validation_accepts_model_specific_fields():
    spec = {
        "components": [
            {"role": "nucleus", "model": "psf", "x": None, "y": None, "mag": 18.0},
            {
                "role": "disk",
                "model": "expdisk",
                "x": None,
                "y": None,
                "mag": 16.0,
                "re": 10.0,
                "q": 0.7,
                "pa": 30.0,
            },
        ],
        "sky": {"value": None, "fix": 0},
    }
    assert validate_model_spec_for_execution(spec) is None


def test_load_physical_ids_supports_report_dict(tmp_path):
    path = tmp_path / "split.json"
    path.write_text(
        json.dumps({"test_physical_ids": ["p1", "p2"]}), encoding="utf-8"
    )
    assert load_physical_ids(path) == {"p1", "p2"}

def test_prepare_subcommand_has_no_sampling_only_arguments():
    args = build_parser().parse_args(
        ["prepare", "--input-dir", "input", "--output", "parents.jsonl"]
    )
    assert args.command == "prepare"
    assert not hasattr(args, "num_candidates")
