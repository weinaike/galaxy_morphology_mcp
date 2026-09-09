import unittest

from eval.build_reward_best_sft import (
    select_best_rollouts,
    split_group_ids_by_physical_id,
)
from eval.compare_reward_learnability import compare


def _row(group, candidate, reward=None, outcome="success", action="modify"):
    return {
        "group_id": group,
        "candidate_index": candidate,
        "candidate_id": f"{group}-{candidate}",
        "prediction": f"response-{group}-{candidate}",
        "outcome": outcome,
        "raw_reward": reward,
        "action_type": action,
    }


class RewardLearnabilityTest(unittest.TestCase):
    def test_select_best_rollouts_filters_and_records_gap(self):
        rows = [
            _row("g1", 0, 0.1),
            _row("g1", 1, 0.8, action="add"),
            _row("g1", 2, outcome="policy_invalid"),
            _row("g2", 0, -0.2),
        ]
        selected, stats = select_best_rollouts(rows)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["candidate_id"], "g1-1")
        self.assertAlmostEqual(selected[0]["reward_gap"], 0.7)
        self.assertEqual(selected[0]["eligible_candidates"], 2)
        self.assertEqual(stats["skip_too_few_successful_candidates"], 1)

    def test_split_is_by_physical_id_not_parent(self):
        parents = [
            {"group_id": "g1", "physical_id": "p1"},
            {"group_id": "g2", "physical_id": "p1"},
            {"group_id": "g3", "physical_id": "p2"},
        ]
        split = split_group_ids_by_physical_id(
            parents, {"g1", "g2", "g3"}, val_ratio=0.5, seed=42
        )
        self.assertEqual(split["g1"], split["g2"])
        self.assertEqual(set(split.values()), {"train", "val"})

    def test_compare_is_paired_by_group_and_excludes_evaluator_failure(self):
        before = [
            _row("g1", 0, 0.0),
            _row("g1", 1, outcome="policy_invalid"),
            _row("g2", 0, 0.2),
            _row("g2", 1, outcome="evaluator_failure"),
        ]
        after = [
            _row("g1", 0, 0.4),
            _row("g1", 1, 0.2),
            _row("g2", 0, 0.3),
            _row("g2", 1, outcome="evaluator_failure"),
        ]
        report = compare(
            before,
            after,
            threshold=0.05,
            failure_reward=-1.0,
            bootstrap_samples=100,
        )
        overall = report["subsets"]["all"]
        self.assertEqual(overall["groups"], 2)
        # g1: (-0.5 -> 0.3) delta 0.8; g2: (0.2 -> 0.3) delta 0.1
        self.assertAlmostEqual(overall["candidate_mean"]["delta"], 0.45)
        self.assertAlmostEqual(overall["success_rate"]["before"], 0.75)
        self.assertAlmostEqual(overall["success_rate"]["after"], 1.0)


if __name__ == "__main__":
    unittest.main()
