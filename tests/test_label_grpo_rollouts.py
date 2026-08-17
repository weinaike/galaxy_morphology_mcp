import asyncio

from eval.label_grpo_rollouts import (
    build_parser,
    build_vlm_alignment_report,
    label_rollout_with_vlm,
)
from eval.validate_grpo_reward import build_replay_report


def test_label_successful_rollout_calls_step_vlm(monkeypatch, tmp_path):
    paths = {}
    for name in ("parent.png", "parent.md", "child.png", "child.md"):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        paths[name] = str(path)

    seen = {}

    def fake_vlm(**kwargs):
        seen.update(kwargs)
        return {"improvement": 1, "reason": "better"}

    monkeypatch.setattr("eval.vlm_reward.vlm_reward_for_step", fake_vlm)
    result = asyncio.run(
        label_rollout_with_vlm(
            {
                "outcome": "success",
                "model_residual_path": paths["child.png"],
                "model_summary_path": paths["child.md"],
            },
            {
                "residual_path": paths["parent.png"],
                "summary_path": paths["parent.md"],
            },
            model_name="test-model",
            api_key=None,
        )
    )
    assert result["vlm_improvement"] == 1
    assert result["vlm_label_status"] == "labeled"
    assert seen["parent_residual_image_path"] == paths["parent.png"]
    assert seen["model_new_residual_image_path"] == paths["child.png"]


def test_non_successful_rollout_is_not_sent_to_vlm(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("VLM must not be called")

    monkeypatch.setattr("eval.vlm_reward.vlm_reward_for_step", fail_if_called)
    result = asyncio.run(
        label_rollout_with_vlm(
            {"outcome": "policy_execution_failure"},
            {},
            model_name="test-model",
            api_key=None,
        )
    )
    assert result["vlm_improvement"] is None
    assert result["vlm_label_status"] == "skipped_non_success"


def test_alignment_report_includes_pairwise_and_all_negative_group():
    raw = [
        {
            "group_id": "g1",
            "candidate_index": 0,
            "outcome": "success",
            "raw_reward": 0.8,
            "vlm_improvement": 1,
            "action_type": "add",
            "parent_kind": "root",
        },
        {
            "group_id": "g1",
            "candidate_index": 1,
            "outcome": "success",
            "raw_reward": 0.0,
            "vlm_improvement": 0,
            "action_type": "modify",
            "parent_kind": "root",
        },
        {
            "group_id": "g2",
            "candidate_index": 0,
            "outcome": "success",
            "raw_reward": 0.9,
            "vlm_improvement": 0,
            "action_type": "add",
            "parent_kind": "negative",
        },
        {
            "group_id": "g2",
            "candidate_index": 1,
            "outcome": "success",
            "raw_reward": -0.2,
            "vlm_improvement": 0,
            "action_type": "modify",
            "parent_kind": "negative",
        },
    ]
    base, shaped = build_replay_report(raw)
    report = build_vlm_alignment_report(shaped, base, vlm_model="test-model")

    binary = report["candidate_binary_alignment"]
    assert (binary["tp"], binary["fp"], binary["tn"], binary["fn"]) == (1, 1, 2, 0)
    pairwise = report["trainable_groups_only_pairwise_shaped_reward"]
    assert pairwise["pairs"] == 1
    assert pairwise["wins"] == 1
    group = report["trainable_group_alignment"]
    assert group["mixed_vlm_labels"] == 1
    assert group["all_vlm_negative"] == 1
    assert group["top_reward_has_vlm_positive_rate"] == 0.5


def test_parser_defaults_to_resumable_label_only_inputs():
    args = build_parser().parse_args(
        ["--parents", "parents.jsonl", "--rollouts", "rollouts.jsonl", "--output", "x.jsonl"]
    )
    assert args.vlm_concurrency == 2
    assert args.max_candidates == 0


def test_replay_report_preserves_selected_reward_version():
    report, rows = build_replay_report([
        {
            "group_id": "g1",
            "outcome": "success",
            "raw_reward": 0.8,
            "vlm_improvement": 1,
            "reward_version": "v12.4",
        },
        {
            "group_id": "g1",
            "outcome": "success",
            "raw_reward": 0.0,
            "vlm_improvement": 0,
            "reward_version": "v12.4",
        },
    ])
    assert report["reward_version"] == "v12.4"
    assert {row["reward_version"] for row in rows} == {"v12.4"}
