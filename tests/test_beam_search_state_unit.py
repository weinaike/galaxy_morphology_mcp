"""Unit tests for the beam_search_state module (HBSS)."""

import json
import os
import shutil
import tempfile
import unittest

from tools import beam_search_state as bss
from tools.beam_search_state import (
    BeamCandidate,
    BeamSearchState,
    beam_search_state,
    compute_topology_hash,
    _STATES,
)


# ── minimal lyric fixtures ───────────────────────────────────────────────────


LYRIC_DISK_ONLY = """# galfits config file
R1) obj_test
R2) [0.0,0.0]
R3) 0.40

Ia1) [/tmp/x.fits,0]
Ia2) f200w
Ia15) 0

Aa1) 'img list'
Aa2) ['a']

# Sersic function - Disk (Pa)
Pa1) obj0
Pa2) sersic
Pa3) [0.0,-1,1,0.02,1]

Ga1) a
Ga2) ['a']
"""


LYRIC_DISK_BULGE = """# galfits config file
R1) obj_test
R2) [0.0,0.0]
R3) 0.40

Ia1) [/tmp/x.fits,0]
Ia2) f200w
Ia15) 0

Aa1) 'img list'
Aa2) ['a']

# Sersic function - Disk (Pa)
Pa1) obj0
Pa2) sersic
Pa3) [0.0,-1,1,0.02,1]

# Sersic function - Bulge (Pb)
Pb1) obj1
Pb2) sersic
Pb3) [0.0,-1,1,0.02,1]

Ga1) a
Ga2) ['a','b']
"""


LYRIC_DISK_BAR = """# galfits config file
R1) obj_test

Ia1) [/tmp/x.fits,0]
Ia2) f200w
Ia15) 0

Aa1) 'img list'
Aa2) ['a']

# Sersic function - Disk (Pa)
Pa1) disk
Pa2) sersic
Pa3) [0.0,-1,1,0.02,1]

# Ferrer function - Bar (Pb)
Pb1) bar
Pb2) ferrer
Pb3) [0.0,-1,1,0.02,1]

Ga1) a
Ga2) ['a','b']
"""


def _write_lyric(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _candidate_json(
    action_type="Add",
    component="Bulge",
    rank=1,
    h_score=0.8,
    target_topology_hash="topo_xxx",
    lyric_path="/tmp/dummy.lyric",
    candidate_id="",
):
    return json.dumps({
        "candidate_id": candidate_id,
        "action_type": action_type,
        "action_component": component,
        "rank": rank,
        "h_score": h_score,
        "target_topology_hash": target_topology_hash,
        "lyric_path": lyric_path,
        "reason": "test",
    })


class TestBeamSearchState(unittest.TestCase):
    """Tests for beam_search_state.py."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="bss_test_")
        self.galaxy_dir = self._tmp
        # Reset in-memory registry between tests
        with bss._LOCK:
            _STATES.clear()

    def tearDown(self):
        with bss._LOCK:
            _STATES.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── init ──────────────────────────────────────────────────────────────

    def test_init_creates_state_and_seed_visited(self):
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        res = beam_search_state(
            action="init",
            galaxy_dir=self.galaxy_dir,
            lyric_file=lyric,
            budget=10,
            beam_width=3,
        )
        self.assertEqual(res["status"], "initialized")
        self.assertEqual(res["phase"], "running")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertIsNotNone(state)
        self.assertEqual(state.budget, 10)
        self.assertEqual(state.beam_width, 3)
        self.assertEqual(len(state.visited_topology), 1)
        # Persistence file written
        self.assertTrue(os.path.exists(os.path.join(self.galaxy_dir, ".beam_search.json")))

    def test_init_resume_loads_existing(self):
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        # Simulate progress: bump consumed_fits and persist
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        state.consumed_fits = 7
        state.phase = "running"
        bss._set_state(state.galaxy_key, state)
        # Drop memory to force lazy-load from disk
        with bss._LOCK:
            _STATES.clear()

        res = beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        self.assertEqual(res["status"], "resumed")
        self.assertEqual(res["data"]["consumed_fits"], 7)

    # ── depth & breadth-first behavior ────────────────────────────────────

    def test_init_creates_seed_candidate_with_depth_0(self):
        """init should create a seed BeamCandidate at depth=0 as state.best."""
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        res = beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        self.assertEqual(res["seed_candidate_id"], "seed")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertIsNotNone(state.best)
        self.assertEqual(state.best.candidate_id, "seed")
        self.assertEqual(state.best.depth, 0)
        self.assertEqual(state.best.status, "done")
        self.assertEqual(state.best.action_type, "NoOp")

    def test_enqueue_inherits_depth_from_state_best(self):
        """New candidates get depth = state.best.depth + 1 automatically."""
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        # Seed is depth=0, so first-level candidates must be depth=1
        res = beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_depth1"),
        )
        self.assertEqual(res["depth"], 1)

        # Promote a depth=1 candidate to best, then enqueue again — depth must be 2
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        state.best = state.queue[0]  # promote the depth=1 candidate
        state.best.depth = 1
        bss._set_state(state.galaxy_key, state)

        res2 = beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_depth2"),
        )
        self.assertEqual(res2["depth"], 2)

    def test_breadth_first_same_rank_shallow_depth_pops_first(self):
        """When ranks tie, shallower candidate is popped first.

        This is the core anti-bias guarantee: a rank=1 candidate from a deep
        branch cannot monopolize the queue over a rank=1 candidate from a
        shallower branch, even if the deep one has higher h_score.
        """
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        # Manually craft two rank=1 candidates at different depths
        from tools.beam_search_state import BeamCandidate, _now_iso
        shallow = BeamCandidate(
            candidate_id="shallow", parent_id="seed",
            source_topology_hash="", target_topology_hash="t_shallow",
            action_type="Add", action_component="AGN",
            action_detail="", init_params={},
            rank=1, h_score=0.3, reason="shallow branch",
            lyric_path="/tmp/shallow.lyric", status="pending",
            depth=1, generated_at=_now_iso(),
        )
        deep = BeamCandidate(
            candidate_id="deep", parent_id="some_parent",
            source_topology_hash="", target_topology_hash="t_deep",
            action_type="Add", action_component="Bar",
            action_detail="", init_params={},
            rank=1, h_score=0.95, reason="deep branch, high confidence",
            lyric_path="/tmp/deep.lyric", status="pending",
            depth=3, generated_at=_now_iso(),
        )
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        state.queue.append(shallow)
        state.queue.append(deep)
        bss._set_state(state.galaxy_key, state)

        res = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["candidate"]["candidate_id"], "shallow",
                         "shallow rank=1 must pop before deep rank=1 despite lower h_score")

    def test_update_best_on_seed_does_not_pollute_streak(self):
        """update_best(seed_id, metrics) is a self-refresh, not a comparison.

        no_improve_streak must stay at 0 and best_history must not record a
        spurious 'replacement' when the seed's metrics are first filled in.
        """
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        res = beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": "seed",
                "fitness_metrics": {"bic": 1000.0, "reduced_chisq": 0.85, "converged": True},
            }),
        )
        self.assertEqual(res["status"], "updated")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertEqual(state.no_improve_streak, 0)
        self.assertEqual(state.best.candidate_id, "seed")
        self.assertEqual(state.best.fitness_metrics["bic"], 1000.0)
        # best_history should NOT log a self-refresh
        self.assertEqual(len(state.best_history), 0)

    def test_breadth_first_same_rank_across_depths(self):
        """Within the same rank, shallower candidates are popped before deeper
        ones — even when the deeper one has much higher h_score.

        This is the core anti-bias guarantee of the (rank, depth, -h) sort key.
        It prevents a single branch from monopolizing the queue as long as the
        VLM produces parallel candidates at the same rank tier.
        """
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, beam_width=4)

        # Two rank=1 candidates at depth=1 (VLM gave two equally-top actions)
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(
                action_type="Add", component="Bulge",
                rank=1, h_score=0.5, target_topology_hash="t_db",
            ),
        )
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(
                action_type="Add", component="Bar",
                rank=1, h_score=0.4, target_topology_hash="t_dbar",
            ),
        )

        # Pop Bulge (depth=1, rank=1) and promote to best
        pop1 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(pop1["candidate"]["action_component"], "Bulge")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        state.best = next(c for c in state.queue if c.candidate_id == pop1["candidate"]["candidate_id"])
        state.best.depth = 1
        bss._set_state(state.galaxy_key, state)

        # Enqueue a depth=2 candidate with the SAME rank=1 but much higher h
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(
                action_type="Add", component="AGN",
                rank=1, h_score=0.99,  # very high — would monopolize under (rank, -h)
                target_topology_hash="t_db_agn",
            ),
        )

        # Next pop MUST be the depth=1 rank=1 candidate (Bar), NOT depth=2
        pop2 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(pop2["candidate"]["depth"], 1,
                         "same-rank shallower candidate must pop before deeper one")
        self.assertEqual(pop2["candidate"]["action_component"], "Bar")

        # Then the depth=2 candidate is allowed
        pop3 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(pop3["candidate"]["depth"], 2)

    def test_rank_priority_over_depth_when_ranks_differ(self):
        """When ranks differ, the lower-rank candidate wins regardless of depth.

        This is the documented trade-off of the (rank, depth, -h) sort: we trust
        the VLM's local rank judgement over global breadth-first. A depth=2
        rank=1 candidate will preempt a depth=1 rank=2 candidate. The breadth-
        first guarantee only applies WITHIN the same rank tier.
        """
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, beam_width=4)

        # depth=1 rank=2 candidate
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(
                action_type="Add", component="Bar",
                rank=2, h_score=0.6, target_topology_hash="t_dbar",
            ),
        )
        # depth=1 rank=1 candidate (would be popped first to advance depth)
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(
                action_type="Add", component="Bulge",
                rank=1, h_score=0.7, target_topology_hash="t_db",
            ),
        )

        # Pop Bulge (rank=1, depth=1) and promote
        pop1 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(pop1["candidate"]["action_component"], "Bulge")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        state.best = next(c for c in state.queue if c.candidate_id == pop1["candidate"]["candidate_id"])
        state.best.depth = 1
        bss._set_state(state.galaxy_key, state)

        # Enqueue depth=2 rank=1 candidate
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(
                action_type="Add", component="AGN",
                rank=1, h_score=0.5, target_topology_hash="t_db_agn",
            ),
        )

        # rank=1 depth=2 preempts rank=2 depth=1 — documented trade-off
        pop2 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(pop2["candidate"]["depth"], 2)
        self.assertEqual(pop2["candidate"]["action_component"], "AGN")

    # ── enqueue ───────────────────────────────────────────────────────────

    def test_mark_visited_updates_last_fitted_candidate(self):
        """mark_visited should advance last_fitted_candidate_id so subsequent
        enqueues use the just-fitted state as parent, not state.best."""
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_db"),
        )
        pop = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid = pop["candidate"]["candidate_id"]

        res = beam_search_state(
            action="mark_visited", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid,
                "fit_round_label": "round1",
                "lyric_path": "/galaxy/db_beam_d1.lyric",
            }),
        )
        self.assertEqual(res["last_fitted_candidate_id"], cid)
        status = beam_search_state(action="get_status", galaxy_dir=self.galaxy_dir)
        self.assertEqual(status["last_fitted_candidate_id"], cid)

    def test_enqueue_inherits_parent_lyric_from_last_fitted_not_best(self):
        """When last_fitted differs from state.best, enqueue must use
        last_fitted.lyric_path as parent_lyric_path.

        This is the regression test for the 's_best lyric' bug: after fitting
        a worse branch, new candidates must still parent on the just-fitted
        state (so the action is materialized on the correct lyric), not on
        the better-but-older state.best.
        """
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)

        # Round 1: fit a strong candidate C1 (BIC=1000) — becomes state.best
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_strong"),
        )
        pop1 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid1 = pop1["candidate"]["candidate_id"]
        beam_search_state(
            action="mark_visited", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid1, "fit_round_label": "r1",
                "lyric_path": "/galaxy/strong_d1.lyric",
            }),
        )
        beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid1,
                "fitness_metrics": {"bic": 1000.0, "converged": True},
            }),
        )
        status = beam_search_state(action="get_status", galaxy_dir=self.galaxy_dir)
        self.assertEqual(status["best"]["candidate_id"], cid1,
                         "C1 should be state.best after round 1")

        # Round 2: fit a weaker candidate C2 (BIC=1200) — state.best stays C1
        beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_weak"),
        )
        pop2 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid2 = pop2["candidate"]["candidate_id"]
        beam_search_state(
            action="mark_visited", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid2, "fit_round_label": "r2",
                "lyric_path": "/galaxy/weak_d1.lyric",
            }),
        )
        beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid2,
                "fitness_metrics": {"bic": 1200.0, "converged": True},
            }),
        )
        status = beam_search_state(action="get_status", galaxy_dir=self.galaxy_dir)
        self.assertEqual(status["best"]["candidate_id"], cid1,
                         "state.best must still be C1 (C2 is worse)")
        self.assertEqual(status["last_fitted_candidate_id"], cid2,
                         "last_fitted must be C2 (just fitted)")

        # Now enqueue C3 — its parent must be C2 (last_fitted), NOT C1 (best)
        res = beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_next"),
        )
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        c3 = next(c for c in state.queue if c.candidate_id == res["candidate_id"])
        self.assertEqual(c3.parent_lyric_path, "/galaxy/weak_d1.lyric",
                         "C3 must inherit parent_lyric from last_fitted (C2), not state.best (C1)")
        self.assertEqual(c3.depth, 3,
                         "C3 depth must derive from last_fitted.depth + 1 = 2 + 1")

    def test_seed_lyric_is_initial_parent(self):
        """After init, the first enqueue must inherit the seed's lyric as parent."""
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        res = beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="t_first"),
        )
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        c = next(c for c in state.queue if c.candidate_id == res["candidate_id"])
        self.assertEqual(c.parent_lyric_path, os.path.abspath(lyric),
                         "first candidate must inherit seed lyric as parent")
        self.assertEqual(c.depth, 1)

    def test_enqueue_orders_by_rank_then_h(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        # Enqueue in shuffled order
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=3, h_score=0.9,
                                                    target_topology_hash="t3"))
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=1, h_score=0.5,
                                                    target_topology_hash="t1"))
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=2, h_score=0.7,
                                                    target_topology_hash="t2"))
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        ranks = [c.rank for c in state.queue]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(state.queue[0].rank, 1)

    def test_enqueue_truncates_to_beam_width(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, beam_width=4)
        for i in range(6):
            beam_search_state(
                action="enqueue", galaxy_dir=self.galaxy_dir,
                candidate=_candidate_json(rank=i + 1, h_score=0.5,
                                          target_topology_hash=f"t{i}"),
            )
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        # 4 pending kept, 2 pruned
        self.assertEqual(len([c for c in state.queue if c.status == "pending"]), 4)
        self.assertEqual(len([c for c in state.queue if c.status == "pruned"]), 2)

    def test_enqueue_dedups_visited_topology(self):
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        # Seed topology is in visited; try to enqueue a candidate targeting it
        seed_hash = compute_topology_hash(lyric)
        res = beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash=seed_hash),
        )
        self.assertEqual(res["status"], "deduplicated")

    # ── pop_next ──────────────────────────────────────────────────────────

    def test_pop_next_returns_highest_rank_pending(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=2, target_topology_hash="t2"))
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=1, target_topology_hash="t1"))
        res = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["candidate"]["rank"], 1)
        self.assertEqual(res["candidate"]["status"], "fitting")
        # Next pop returns rank=2
        res2 = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(res2["candidate"]["rank"], 2)

    def test_pop_next_empty_queue(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        res = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        self.assertEqual(res["status"], "empty")

    # ── mark_visited ──────────────────────────────────────────────────────

    def test_mark_visited_records_topology_and_increments_counter(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, budget=15)
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(target_topology_hash="t_new"))
        res_pop = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid = res_pop["candidate"]["candidate_id"]

        res = beam_search_state(
            action="mark_visited", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid,
                "fit_round_label": "20260720_120000_round1",
                "fit_summary_path": "/tmp/summary.md",
            }),
        )
        self.assertEqual(res["status"], "visited")
        self.assertEqual(res["consumed_fits"], 1)
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertIn("t_new", state.visited_topology)

    # ── update_best ───────────────────────────────────────────────────────

    def test_update_best_replaces_on_better_fitness(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        # Enqueue two candidates with different topologies
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(target_topology_hash="tA"))
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=2, target_topology_hash="tB"))

        # First fit: mediocre fitness
        pop_a = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid_a = pop_a["candidate"]["candidate_id"]
        beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                          candidate=json.dumps({"candidate_id": cid_a}))
        res_a = beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid_a,
                "fitness_metrics": {"bic": 1000.0, "converged": True},
            }),
        )
        self.assertEqual(res_a["status"], "updated")
        self.assertTrue(res_a["replaced_best"])

        # Second fit: better fitness
        pop_b = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid_b = pop_b["candidate"]["candidate_id"]
        beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                          candidate=json.dumps({"candidate_id": cid_b}))
        res_b = beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid_b,
                "fitness_metrics": {"bic": 800.0, "converged": True},
            }),
        )
        self.assertEqual(res_b["status"], "updated")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertEqual(state.best.candidate_id, cid_b)
        self.assertEqual(state.no_improve_streak, 0)

    def test_update_best_increments_no_improve_streak(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(target_topology_hash="tA"))
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=2, target_topology_hash="tB"))

        pop_a = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid_a = pop_a["candidate"]["candidate_id"]
        beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                          candidate=json.dumps({"candidate_id": cid_a}))
        beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid_a,
                "fitness_metrics": {"bic": 1000.0},
            }),
        )

        # Worse fitness second round
        pop_b = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid_b = pop_b["candidate"]["candidate_id"]
        beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                          candidate=json.dumps({"candidate_id": cid_b}))
        res = beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid_b,
                "fitness_metrics": {"bic": 1200.0},
            }),
        )
        self.assertEqual(res["status"], "retained")
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertEqual(state.no_improve_streak, 1)

    # ── early stop & budget ───────────────────────────────────────────────

    def test_early_stop_triggers_after_k_no_improve(self):
        beam_search_state(
            action="init", galaxy_dir=self.galaxy_dir,
            early_stop_k=2, early_stop_eps=0.9,
        )
        # Seed a strong best
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(rank=1, h_score=0.95,
                                                    target_topology_hash="tA"))
        pop = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid = pop["candidate"]["candidate_id"]
        beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                          candidate=json.dumps({"candidate_id": cid}))
        beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid,
                "fitness_metrics": {"bic": 100.0},
            }),
        )

        # Two worse rounds; queue max h must be < eps for early stop
        for i, topo in enumerate(["tB", "tC"]):
            beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                              candidate=_candidate_json(rank=1, h_score=0.1,
                                                        target_topology_hash=topo))
            pop = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
            cid = pop["candidate"]["candidate_id"]
            beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                              candidate=json.dumps({"candidate_id": cid}))
            beam_search_state(
                action="update_best", galaxy_dir=self.galaxy_dir,
                candidate=json.dumps({
                    "candidate_id": cid,
                    "fitness_metrics": {"bic": 500.0 + i},
                }),
            )

        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertEqual(state.phase, "early_stopped")

    def test_budget_exhausted(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, budget=2)
        # Run two fits
        for topo in ["tA", "tB"]:
            beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                              candidate=_candidate_json(target_topology_hash=topo))
            pop = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
            cid = pop["candidate"]["candidate_id"]
            beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                              candidate=json.dumps({"candidate_id": cid}))
            beam_search_state(
                action="update_best", galaxy_dir=self.galaxy_dir,
                candidate=json.dumps({
                    "candidate_id": cid,
                    "fitness_metrics": {"bic": 100.0},
                }),
            )
        state = bss.get_state(os.path.abspath(self.galaxy_dir))
        self.assertEqual(state.consumed_fits, 2)
        self.assertEqual(state.phase, "budget_exhausted")

    # ── finalize ──────────────────────────────────────────────────────────

    def test_finalize_freezes_and_rejects_enqueue(self):
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir)
        beam_search_state(action="enqueue", galaxy_dir=self.galaxy_dir,
                          candidate=_candidate_json(target_topology_hash="tA"))
        pop = beam_search_state(action="pop_next", galaxy_dir=self.galaxy_dir)
        cid = pop["candidate"]["candidate_id"]
        beam_search_state(action="mark_visited", galaxy_dir=self.galaxy_dir,
                          candidate=json.dumps({"candidate_id": cid}))
        beam_search_state(
            action="update_best", galaxy_dir=self.galaxy_dir,
            candidate=json.dumps({
                "candidate_id": cid,
                "fitness_metrics": {"bic": 50.0},
            }),
        )

        res = beam_search_state(action="finalize", galaxy_dir=self.galaxy_dir)
        self.assertEqual(res["status"], "finalized")
        self.assertIsNotNone(res["best"])

        # Post-finalize enqueue rejected
        res2 = beam_search_state(
            action="enqueue", galaxy_dir=self.galaxy_dir,
            candidate=_candidate_json(target_topology_hash="tZ"),
        )
        self.assertEqual(res2["status"], "rejected")

    # ── topology hash ─────────────────────────────────────────────────────

    def test_topology_hash_stable_across_param_changes(self):
        lyric_a = _write_lyric(self._tmp, "a.lyric", LYRIC_DISK_ONLY)
        # Same structure, different parameter value
        variant = LYRIC_DISK_ONLY.replace("[0.0,-1,1,0.02,1]", "[0.5,-2,2,0.05,1]")
        lyric_b = _write_lyric(self._tmp, "b.lyric", variant)
        self.assertEqual(compute_topology_hash(lyric_a), compute_topology_hash(lyric_b))

    def test_topology_hash_differs_across_structures(self):
        lyric_disk = _write_lyric(self._tmp, "disk.lyric", LYRIC_DISK_ONLY)
        lyric_db = _write_lyric(self._tmp, "db.lyric", LYRIC_DISK_BULGE)
        lyric_dbar = _write_lyric(self._tmp, "dbar.lyric", LYRIC_DISK_BAR)
        h_disk = compute_topology_hash(lyric_disk)
        h_db = compute_topology_hash(lyric_db)
        h_dbar = compute_topology_hash(lyric_dbar)
        self.assertNotEqual(h_disk, h_db)
        self.assertNotEqual(h_disk, h_dbar)
        self.assertNotEqual(h_db, h_dbar)

    # ── persistence toggle ────────────────────────────────────────────────

    def test_persistence_disabled_by_env(self):
        with unittest.mock.patch.dict(os.environ, {"BEAM_SEARCH_PERSIST": "0"}):
            lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
            beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
            self.assertFalse(
                os.path.exists(os.path.join(self.galaxy_dir, ".beam_search.json"))
            )

    def test_clear_memory_vs_persisted(self):
        lyric = _write_lyric(self._tmp, "obj.lyric", LYRIC_DISK_ONLY)
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        state_file = os.path.join(self.galaxy_dir, ".beam_search.json")
        self.assertTrue(os.path.exists(state_file))

        # Memory-only clear: file survives
        beam_search_state(action="clear", galaxy_dir=self.galaxy_dir, purge_disk=False)
        self.assertTrue(os.path.exists(state_file))
        with bss._LOCK:
            self.assertNotIn(os.path.abspath(self.galaxy_dir), _STATES)

        # Reload from disk to confirm state survives
        beam_search_state(action="init", galaxy_dir=self.galaxy_dir, lyric_file=lyric)
        self.assertIn(os.path.abspath(self.galaxy_dir), _STATES)

        # Disk + memory clear
        beam_search_state(action="clear", galaxy_dir=self.galaxy_dir, purge_disk=True)
        self.assertFalse(os.path.exists(state_file))


# unittest.mock is used inside test_persistence_disabled_by_env
import unittest.mock  # noqa: E402


if __name__ == "__main__":
    unittest.main()
