"""Tests for the mcts ablation arm (beam.mcts): scripted enumerator legality,
PUCT selection, reward shaping, and the mcts_round loop contract.

The comparison contract under test: the arm shares EVERY legality gate with
the surveyor arms (validate_candidate + ingest) and differs only in the
candidate source (deterministic enumerator, no perception) and the selection
rule (PUCT over per-direction statistics instead of the g score).
"""

import json
import math
import shutil

import pytest

from beam.graph import BeamGraph
from beam.mcts import (
    UCB_C,
    edge_reward,
    enumerate_candidates,
    mcts_round,
    ucb_reorder,
    ucb_score,
)
from beam.tools import apply_candidate, beam_init, beam_record_fit, survey_round

SINGLE_FEEDME = """================================================================================
# IMAGE and GALFIT CONTROL PARAMETERS
A) image_r.fits        # Input data image (FITS file)
B) fit.fits            # Output data image block
C) sigma_r.fits        # Sigma image name
D) PSF-1.composite.fits  # PSF image
E) 1                   # PSF fine sampling factor relative to data
F) mask_r.fits         # Bad pixel mask
G) none                # File with parameter constraints
H) 1   1016  1   1016  # Image region to fit (xmin xmax ymin ymax)
I) 65     65           # Size of the convolution box (x y)
J) 22.5                # Magnitude photometric zeropoint
K) 0.262  0.262        # Plate scale (dx dy)   [arcsec per pixel]
O) regular             # Display type (regular, curses, both)
P) 0                   # Choose: 0=optimize, 1=model, 2/imgblock, 3/subcomps
# INITIAL FITTING PARAMETERS
# ------------------------------------------------------------------------------
# Component 1: Single Sersic
# STRUCTURE: singlesersic
0) sersic                    #  Component type
1) 508.21  508.69  1  1      #  Position x, y
3) 15.17        1            #  Integrated magnitude
4) 29.58        1            #  R_e (effective radius)   [pix]
5) 1.42        1             #  Sersic index n
6) 0.0000      0             #  -----
7) 0.0000      0             #  -----
8) 0.0000      0             #  -----
9) 0.24        1             #  Axis ratio (b/a)
10) 81.3        1            #  Position angle (PA) [deg: Up=0, Left=90]
Z) 0                         #  Skip this model?

# Component 2: Sky background
# STRUCTURE: sky
0) sky                       #  Component type
1) 0.000067      0           #  Sky background [ADUs] - fixed
2) 0.0000      0             #  dsky/dx (sky gradient in x)
3) 0.0000      0             #  dsky/dy (sky gradient in y)
Z) 0                         #  Skip this model?
"""


@pytest.fixture
def single_galaxy(tmp_path, test_data_dir):
    """A single-sersic galaxy dir (KILOGAS_93-style root state) with a fitted
    first fit registered — the mcts arm's starting point."""
    gdir = tmp_path / "SINGLE"
    gdir.mkdir()
    (gdir / "_iter1.feedme").write_text(SINGLE_FEEDME, encoding="utf-8")
    shutil.copy(test_data_dir / "PSF-1.composite.fits",
                gdir / "PSF-1.composite.fits")
    shutil.copy(gdir / "_iter1.feedme", gdir / "galfit.01")
    return gdir


@pytest.fixture
def single_mcts_graph(single_galaxy):
    g = BeamGraph.init(str(single_galaxy), str(single_galaxy / "_iter1.feedme"),
                       stage1={"morphology": "lenticular",
                               "detect_bar_lopsidedness": {
                                   "bar": {"detected": False},
                                   "lopsidedness": {"detected": False}}})
    label = g.record_fit({
        "input_param_file": str(single_galaxy / "_iter1.feedme"),
        "output_param_file": str(single_galaxy / "galfit.01"),
        "image_file": str(single_galaxy / "cmp.png"),
        "summary_file": str(single_galaxy / "summary.md"),
        "round_status_file": str(single_galaxy / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 46031.4, "chisq1d_nu": 26.8,
                           "convergence": {"flag": "ok"}},
    })
    return g, label


# ------------------------------------------------------------------ enumerator
def test_enumerator_singlesersic_bundles_conversion(single_mcts_graph):
    """Every central add from a {singlesersic} parent MUST bundle the
    singlesersic->disk conversion (E_SINGLE_CONVERSION parity) — the
    deterministic enumerator can never propose an illegal bare add(Bar)."""
    g, label = single_mcts_graph
    cands = enumerate_candidates(g, label)
    assert cands, "the enumerator must produce candidates for the root state"
    assert len(cands) <= 2  # depth-1 cap (DEPTH_COUNTS[1] = (1, 2))
    for c in cands:
        prims = c["primitives"]
        central_adds = [p for p in prims
                        if p["op"] == "add"
                        and p["add"]["structure_name"] in
                        {"disk", "edgedisk", "bulge", "bar", "lens"}]
        if central_adds:
            assert any(p["op"] == "convert" for p in prims), \
                f"{c['expected_behavior_tag']}: central add without conversion"
            assert "disk" in c["expected_C_prime"]
    tags = {c["expected_behavior_tag"] for c in cands}
    assert "mcts_decompose_disk_bulge" in tags
    # no perception in this arm: no companion and no F1 candidates ever
    for c in cands:
        assert "companion" not in c["expected_behavior_tag"]
        for p in c["primitives"]:
            entry = p.get("add") or {}
            assert not entry.get("f1")
            assert (entry.get("structure_name") or "") != "companion"


def test_enumerator_singlesersic_never_bar_without_disk(single_mcts_graph):
    """Regime rule (hard): add(Bar) without a disk slot is invalid — the
    enumerator's landed inventories are all legal from a singlesersic parent."""
    g, label = single_mcts_graph
    from beam.signature import apply_primitives_to_inventory

    for c in enumerate_candidates(g, label):
        hypo, err = apply_primitives_to_inventory(g.state(label).get("inventory", []),
                                                  c["primitives"])
        assert hypo is not None, err
        names = [str(x.get("name") or "").lower() for x in hypo]
        if "bar" in names and len(names) > 1:
            assert "disk" in names or "edgedisk" in names


def test_enumerator_disk_bearing_directions(tmp_path, test_data_dir):
    """From a disk-bearing state the enumerator proposes the solution-space
    directions (lens/outerdisk additions, removals, n-release) and stays
    within the depth cap."""
    gdir = tmp_path / "DISKBEAR"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    shutil.copy(test_data_dir / "NGC1097_clean.07", gdir / "galfit.01")
    # pin the fixture bulge's n (toggle 1 -> 0) so the n-release template has
    # something to release (the shipped fixture's n is already free); the
    # fitted inventory's toggles come from the galfit-output side, so the .07
    # row (different value/format from the feedme) must be patched too
    p = gdir / "_iter1.feedme"
    p.write_text(p.read_text(encoding="utf-8").replace(
        " 5) 2.0         1          #  Sersic index n (fixed to avoid collapse)",
        " 5) 2.0         0          #  Sersic index n (fixed to avoid collapse)",
        1), encoding="utf-8")
    p = gdir / "galfit.01"
    p.write_text(p.read_text(encoding="utf-8").replace(
        " 5) 0.1729      1          #  Sersic index n (de Vaucouleurs n=4)",
        " 5) 0.1729      0          #  Sersic index n (de Vaucouleurs n=4)",
        1), encoding="utf-8")
    g = BeamGraph.init(str(gdir), str(gdir / "_iter1.feedme"), stage1={})
    label = g.record_fit({
        "input_param_file": str(gdir / "_iter1.feedme"),
        "output_param_file": str(gdir / "galfit.01"),
        "image_file": str(gdir / "cmp.png"),
        "summary_file": str(gdir / "summary.md"),
        "round_status_file": str(gdir / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    })
    assert g.state(label)["combo_key"] == "agn+bar+bulge+disk"

    cands = enumerate_candidates(g, label)
    assert len(cands) <= 2  # depth-1 cap
    tags = [c["expected_behavior_tag"] for c in cands]
    assert len(tags) == len(set(tags))  # tag uniqueness (E_TAG_DUP parity)
    # the parent already has bulge+bar+agn: no re-adds of those slots
    assert all(t not in {"mcts_add_bulge", "mcts_add_bar", "mcts_add_agn"}
               for t in tags)

    # deeper states get the wider cap (depth >= 3 -> up to 4)
    g.g.nodes[label]["depth"] = 3
    deep = enumerate_candidates(g, label)
    assert len(deep) <= 4
    deep_tags = {c["expected_behavior_tag"] for c in deep}
    assert "mcts_add_lens" in deep_tags          # bar present, lens absent
    assert "mcts_add_outerdisk" in deep_tags
    assert "mcts_tune_bulge_n_release" in deep_tags  # fixture bulge n is fixed
    assert any(t.startswith("mcts_remove_") for t in deep_tags)


def test_enumerator_respects_stage1_bar_hint(single_mcts_graph):
    """Stage-1 bar detection raises the bar-direction prior (hint-level
    positive evidence, per the scoring rules) but never blocks other moves."""
    g, label = single_mcts_graph
    g.g.graph["stage1"]["detect_bar_lopsidedness"] = {
        "bar": {"detected": True, "pa_deg": 81.5, "b_over_a": 0.27},
        "lopsidedness": {"detected": False}}
    cands = enumerate_candidates(g, label)
    by_tag = {c["expected_behavior_tag"]: c for c in cands}
    if "mcts_decompose_disk_bar" in by_tag:
        assert by_tag["mcts_decompose_disk_bar"]["local_benefit_sigma"] == 0.8
        bar_add = next(p for p in by_tag["mcts_decompose_disk_bar"]["primitives"]
                       if p["op"] == "add")
        assert bar_add["add"]["pa_deg"] == pytest.approx(81.5)  # N=+Y, verbatim


# --------------------------------------------------------------------- reward
def test_edge_reward_shaping():
    parent = {"bic_eff": 1000.0, "convergence": {"flag": "ok"}}
    # dBIC = 500 -> r_bic = 0.5 -> PASS reward 0.4 + 0.6*0.5
    assert edge_reward({"bic_eff": 500.0, "convergence": {"flag": "ok"}},
                       parent, {"verdict": "PASS"}) == pytest.approx(0.7)
    # full-scale improvement (dBIC >= REWARD_BIC_SCALE) caps at 1.0
    assert edge_reward({"bic_eff": 0.0, "convergence": {"flag": "ok"}},
                       parent, {"verdict": "PASS"}) == pytest.approx(1.0)
    # modest improvement
    r = edge_reward({"bic_eff": 950.0, "convergence": {"flag": "ok"}},
                    parent, {"verdict": "PASS"})
    assert r == pytest.approx(0.4 + 0.6 * 0.05)
    # physicality FAIL caps the reward well below any PASS state
    r_fail = edge_reward({"bic_eff": 500.0, "convergence": {"flag": "ok"}},
                         parent, {"verdict": "FAIL"})
    assert r_fail == pytest.approx(max(0.0, 0.3 * 0.5 - 0.1))
    assert r_fail < 0.4
    # sub-converged rounds are invalid evidence -> no statistic update
    assert edge_reward({"bic_eff": 10.0, "convergence": {"flag": "sub-converged"}},
                       parent, {"verdict": "PASS"}) is None
    # a worsening fit yields the PASS floor, never a negative reward
    r_worse = edge_reward({"bic_eff": 1500.0, "convergence": {"flag": "ok"}},
                          parent, {"verdict": "PASS"})
    assert r_worse == pytest.approx(0.4)


# ------------------------------------------------------------------ selection
def _pending(graph, label, tag, sigma, combo, kinds_prims):
    return graph.add_pending({
        "action_id": f"{label}-{tag}", "sigma": sigma,
        "expected_behavior_tag": tag, "expected_C_prime": "{x}",
        "novelty_claim": "", "primitives": kinds_prims,
        "code_flags": {}, "score": 0.5, "combo_key": combo,
    }, source_session="test", parent_label=label)


def test_puct_ordering_tried_vs_untried(single_mcts_graph):
    """Frontier-UCT: a tried direction with a high mean reward outranks an
    untried one; a low-Q tried direction loses to a strong untried prior."""
    g, label = single_mcts_graph
    # pending records carry PLAIN primitives (what ingest stores in production)
    prims = [{"op": "tune", "structure_name": "singlesersic",
              "param": "n", "value": 2.0}]
    _pending(g, label, "high_q", 0.5, "singlesersic", prims)
    _pending(g, label, "fresh", 0.9, "singlesersic+agn",
             [{"op": "add", "structure_name": "agn",
               "component_type": "psf", "mag": 17.7}])
    # one executed fit from label in the high_q direction with Q = 0.9
    key = f"{label}|singlesersic|tune:singlesersic.n"
    g.mcts_stats()[key] = {"visits": 1, "total_reward": 0.9}

    scored = ucb_reorder(g)
    order = [d["action_id"] for d in scored]
    n_s = g.mcts_state_visits(label)  # 1 (no executed children yet)
    explore = UCB_C * math.sqrt(math.log(n_s + 1))
    assert explore == pytest.approx(1.4 * math.sqrt(math.log(2.0)))
    # tried Q=0.9 beats prior-only 0.9*explore? 0.9 + 0.5*explore vs 0.9*explore
    tried_score = 0.9 + 0.5 * explore
    fresh_score = 0.9 * explore
    expected_first = f"{label}-high_q" if tried_score > fresh_score \
        else f"{label}-fresh"
    assert order[0] == expected_first

    # now depress the tried direction's reward: the strong untried prior wins
    g.mcts_stats()[key] = {"visits": 1, "total_reward": 0.05}
    order2 = [d["action_id"] for d in ucb_reorder(g)]
    assert order2[0] == f"{label}-fresh"


def test_puct_exploration_grows_with_parent_visits(single_mcts_graph):
    """The log-growth exploration term re-arms under-visited directions as the
    parent accumulates fits (canonical UCT balance)."""
    g, label = single_mcts_graph
    prims_a = [{"op": "tune", "structure_name": "singlesersic",
                "param": "n", "value": 2.0}]
    aid_a = _pending(g, label, "once_tried", 0.5, "singlesersic", prims_a)
    aid_b = _pending(g, label, "never_tried", 0.5, "singlesersic+agn",
                     [{"op": "add", "structure_name": "agn",
                       "component_type": "psf", "mag": 17.7}])
    key = f"{label}|singlesersic|tune:singlesersic.n"
    g.mcts_stats()[key] = {"visits": 1, "total_reward": 0.5}
    rec_a = g.pending_record(aid_a)
    rec_b = g.pending_record(aid_b)
    # before any child executes: N(s) = 1
    assert g.mcts_state_visits(label) == 1
    score_b_before = ucb_score(g, rec_b)
    assert score_b_before == pytest.approx(UCB_C * 0.5 * math.sqrt(math.log(2.0)))
    # simulated: one child executed from label -> N(s) = 2
    g.g.add_node("A.2", **{"label": "A.2", "global_iter_id": 2, "parent": label,
                           "action_in": "", "depth": 2, "metrics": {},
                           "combo_key": "x"})
    g.g.add_edge(label, "A.2", action_id="x")
    assert g.mcts_state_visits(label) == 2
    # the untried direction's exploration bonus GROWS with parent visits
    # (canonical UCT log-growth): sqrt(ln 3) > sqrt(ln 2)
    score_b_after = ucb_score(g, rec_b)
    assert score_b_after == pytest.approx(UCB_C * 0.5 * math.sqrt(math.log(3.0)))
    assert score_b_after > score_b_before
    # ...while the tried direction adds its mean reward on top
    score_a = ucb_score(g, rec_a)
    assert score_a == pytest.approx(0.5 + UCB_C * 0.5 * math.sqrt(math.log(3.0) / 2))
    assert score_a > score_b_after  # a healthy Q still wins exploitation


# ---------------------------------------------------------------------- round
def test_mcts_round_end_to_end_and_arm_guards(single_galaxy):
    """The mcts_round loop contract: verdict settled (code-only), first-fit
    backprop skipped, candidates enqueued through the shared gates, frontier
    returned with next_candidate — and survey_round hard-refuses in this arm."""
    r = beam_init(str(single_galaxy), str(single_galaxy / "_iter1.feedme"),
                  stage1_morphology="lenticular",
                  stage1_bar_lop_json=json.dumps(
                      {"bar": {"detected": False},
                       "lopsidedness": {"detected": False}}),
                  ablations_json=json.dumps({"mcts": True}))
    assert r["status"] == "success" and r["ablations"] == {"mcts": True}
    rr = beam_record_fit(str(single_galaxy), json.dumps({
        "input_param_file": str(single_galaxy / "_iter1.feedme"),
        "output_param_file": str(single_galaxy / "galfit.01"),
        "image_file": str(single_galaxy / "cmp.png"),
        "summary_file": str(single_galaxy / "summary.md"),
        "round_status_file": str(single_galaxy / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 46031.4, "chisq1d_nu": 26.8,
                           "convergence": {"flag": "ok"}},
    }))
    assert rr["status"] == "success"

    # arm guard: survey_round refuses, mcts_round requires the arm
    out = survey_round(str(single_galaxy))
    assert out["status"] == "failure" and "E_ARM" in out["error"]
    wrong = BeamGraph.load(str(single_galaxy))
    wrong.g.graph["meta"]["ablations"] = {}
    wrong.commit()
    out2 = mcts_round(str(single_galaxy))
    assert out2["status"] == "failure" and "E_ARM" in out2["error"]
    # restore the arm before exercising the round flow
    restore = BeamGraph.load(str(single_galaxy))
    restore.g.graph["meta"]["ablations"] = {"mcts": True}
    restore.commit()

    out3 = mcts_round(str(single_galaxy), state_label="A.1")
    assert out3["status"] == "success"
    assert out3["verdict"]["verdict"] == "PASS"  # code-only verdict (mech merged)
    assert out3["backprop"]["updated"] is False  # deterministic first fit
    assert out3["enqueued"], "expansion must enqueue the decompose directions"
    assert out3["next_candidate"]
    # stats key layout (audit): no stats entry for the first fit
    g = BeamGraph.load(str(single_galaxy))
    assert g.mcts_stats() == {}
    # the enqueued candidates landed on legal decompose inventories
    tags = {e.get("tag") or "" for e in out3["enqueued"]}
    assert any("decompose" in t for t in tags)

    # from here the mechanised loop proceeds: apply_candidate on next_candidate
    ap = apply_candidate(str(single_galaxy), out3["next_candidate"])
    assert ap["status"] == "success", ap.get("error") or ap.get("notes")
    assert (single_galaxy / "_iter2.feedme").exists()
    body = (single_galaxy / "_iter2.feedme").read_text(encoding="utf-8")
    assert "# STRUCTURE: disk" in body  # conversion was transcribed
    assert "# STRUCTURE: bulge" in body


def test_backprop_after_executed_fit(single_galaxy):
    """A non-first fit backprops its shaped reward onto the direction stats."""
    r = beam_init(str(single_galaxy), str(single_galaxy / "_iter1.feedme"),
                  stage1_morphology="lenticular",
                  ablations_json=json.dumps({"mcts": True}))
    assert r["status"] == "success"
    rr = beam_record_fit(str(single_galaxy), json.dumps({
        "input_param_file": str(single_galaxy / "_iter1.feedme"),
        "output_param_file": str(single_galaxy / "galfit.01"),
        "image_file": str(single_galaxy / "cmp.png"),
        "summary_file": str(single_galaxy / "summary.md"),
        "round_status_file": str(single_galaxy / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 46031.4, "chisq1d_nu": 26.8,
                           "convergence": {"flag": "ok"}},
    }))
    assert rr["status"] == "success"
    out = mcts_round(str(single_galaxy), state_label="A.1")
    assert out["status"] == "success"
    nxt = out["next_candidate"]
    ap = apply_candidate(str(single_galaxy), nxt)
    assert ap["status"] == "success", ap.get("error") or ap.get("notes")
    rr2 = beam_record_fit(str(single_galaxy), json.dumps({
        "input_param_file": ap["feedme"],
        "output_param_file": str(single_galaxy / "galfit.01"),
        "image_file": str(single_galaxy / "cmp2.png"),
        "summary_file": str(single_galaxy / "summary2.md"),
        "round_status_file": str(single_galaxy / "archives" / "y" / "round_status.json"),
        "fit_statistics": {"bic_eff": 39573.6, "chisq1d_nu": 17.5,
                           "convergence": {"flag": "ok"}},
    }), action_id=nxt)
    assert rr2["status"] == "success"
    out2 = mcts_round(str(single_galaxy), state_label=rr2["new_state"])
    assert out2["status"] == "success"
    assert out2["backprop"]["updated"] is True
    assert out2["backprop"]["reward"] == pytest.approx(1.0)  # dBIC ~6458 >= scale
    g = BeamGraph.load(str(single_galaxy))
    stats = g.mcts_stats()
    assert any(v["visits"] == 1 and v["total_reward"] > 0 for v in stats.values())
    snap = g.snapshot()
    assert snap["mcts_stats"] == stats
