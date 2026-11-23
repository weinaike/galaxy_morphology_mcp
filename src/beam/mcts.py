"""MCTS ablation arm (paper baseline): PUCT selection over the shared beam
machinery — no perception, no VLM.

Comparison contract (what this arm isolates):

* Legality is IDENTICAL to the surveyor arms: enumerated candidates are
  authored in the same SurveyResponse JSON contract, validated by
  ``validate_survey`` (schema + solution-space rules: closed alphabet,
  multiplicity/regime rules, Re-chain adjacency, conversion bundling,
  combo caps, R1/R2 dedup, temporary constraints) and enqueued through the
  same ``ingest`` gates (floors / diversity / aging). Illegal moves are
  impossible twice over: the enumerator only generates moves whose landed
  inventory is legal, and the validator would reject anything else — the
  same gates a VLM candidate must pass.
* The arm differs in exactly two things:
    1. who performs perception + candidate generation — a DETERMINISTIC
       scripted enumerator over a fixed library of macro-actions (1-2
       semantically cohesive primitives, the same action granularity the
       workflow defines), parameterised from the parent state's converged
       values + component-spec defaults (flux split 4:6, Re fractions).
       No image is ever read: companions (which need visual placement), the
       F1 Fourier mode (which needs lopsidedness perception) and the
       edgedisk slot (unused by the main experiments; no transcription-layer
       row semantics) are deliberately outside this arm's action space.
    2. the selection rule — PUCT over per-direction visit/reward statistics
       instead of the six-dimension g score:
           score(s,a) = Q(s,a) + c * P(s,a) * sqrt(ln(N(s)+1) / (1 + n(s,a)))
       with P(s,a) the scripted prior (enumerator sigma), N(s) = 1 + executed
       children of s, and Q the mean shaped reward.
* Evaluation is EXACT: each expansion is one real GALFIT fit, so there are
  no random rollouts (meaningless when the simulator is an expensive exact
  fitter — the honest MCTS mapping for this domain). The reward shapes the
  BIC_eff improvement with the code-computed physicality verdict:
      convergence != ok  -> invalid evidence, no statistic update
      verdict FAIL       -> r = max(0, 0.3*r_bic - 0.1)
      verdict PASS       -> r = 0.4 + 0.6*r_bic,  r_bic = clip(dBIC/1000, 0, 1)
  Backprop updates only the executed edge's (parent, direction) statistic:
  selection runs over the whole frontier, not a root walk, so deeper
  backprop carries no information the frontier already has.

Loop position (mechanised path, mirrors survey_round):
    apply_candidate -> run_galfit -> beam_record_fit -> mcts_round(state)
"""

from __future__ import annotations

import json
import math
from typing import Any

from beam.candidate_schema import DEPTH_COUNTS, validate_candidate

# PUCT exploration constant (sqrt(2), the canonical UCT value)
UCB_C = 1.4
# BIC_eff improvement mapped to [0, 1]: >= 1000 counts as a full reward
REWARD_BIC_SCALE = 1000.0

_RE_EQUIV_FLOOR = 0.2   # AGN admission: bulge Re below this is a collapse hint
_RE_EQUIV_BORDER = 0.5  # AGN admission border zone (0.2-0.5 px)


# --------------------------------------------------------------------- reward
def edge_reward(child_metrics: dict, parent_metrics: dict,
                verdict: dict | None) -> float | None:
    """Shaped reward of one executed edge; None = invalid evidence (skip)."""
    conv = ((child_metrics or {}).get("convergence") or {}).get("flag", "ok")
    if conv != "ok":
        return None  # sub-converged: the optimiser never explored (no stat)
    new = _metric_key(child_metrics or {})
    if new is None:
        return None
    old = _metric_key(parent_metrics or {})
    dbic = (old - new) if old is not None else 0.0  # positive = improvement
    r_bic = max(0.0, min(dbic, REWARD_BIC_SCALE)) / REWARD_BIC_SCALE
    if (verdict or {}).get("verdict") == "FAIL":
        return max(0.0, 0.3 * r_bic - 0.1)
    return 0.4 + 0.6 * r_bic


def _metric_key(metrics: dict) -> float | None:
    for key in ("bic_eff", "bic1d", "chi2_nu"):
        v = metrics.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


# ----------------------------------------------------------------- enumerator
def _companion(name: str) -> bool:
    import re

    return bool(re.match(r"^(companion|comp|secondary|satellite)", name or "", re.I))


def _re_eff(c: dict | None) -> float | None:
    """Effective radius (px) of a raw inventory component."""
    if not c:
        return None
    re_eff = c.get("re_effective")
    if re_eff is None and c.get("re") is not None:
        try:
            re_eff = float(c["re"]) * (1.68 if (c.get("type") or "").lower()
                                       in ("expdisk", "edgedisk") else 1.0)
        except (TypeError, ValueError):
            return None
    try:
        return float(re_eff)
    except (TypeError, ValueError):
        return None


def _q_of(c: dict | None) -> float | None:
    if not c:
        return None
    try:
        return float(c.get("q", c.get("ba")))
    except (TypeError, ValueError):
        return None


def _to_plain_prims(prims: list[dict]) -> list[dict]:
    """Pydantic-input shape -> plain-primitive shape (the form consumed by
    signature.apply_primitives_to_inventory / transcribe). Two keys differ:
    ``remove`` (schema field) -> ``target`` (plain), and ``tune`` nests the
    delta under a ``tune`` key in the JSON contract while the plain form is
    flat (mirrors TunePrimitive.to_plain)."""
    out = []
    for p in prims:
        if p.get("op") == "remove" and "target" not in p:
            out.append({"op": "remove", "target": p.get("remove")})
        elif p.get("op") == "tune" and isinstance(p.get("tune"), dict):
            d = dict(p["tune"])
            d["op"] = "tune"
            out.append(d)
        else:
            out.append(p)
    return out


def _pa_trusted(c: dict | None) -> float | None:
    """A parent PA is a trustworthy anchor only when the component is not
    near-round (q <= 0.9) — a round component's PA is optimiser noise
    (E_WEAK_PA_ANCHOR would fire on a copy)."""
    if not c:
        return None
    q = _q_of(c)
    if q is None or q > 0.9 or c.get("pa") is None:
        return None
    try:
        return float(c["pa"])
    except (TypeError, ValueError):
        return None


def enumerate_candidates(graph, state_label: str) -> list[dict]:
    """Deterministic macro-action enumeration for one state (expansion).

    Returns SurveyResponse-shaped candidate dicts (already legality-filtered
    per candidate by ``validate_candidate`` — error-level issues drop the
    candidate so the batch ingest never fails on one bad move).
    """
    from beam.digest import build_local_state_description
    from beam.signature import apply_primitives_to_inventory, combo_identity

    state = graph.state(state_label)
    inv = state.get("inventory", [])
    depth = int(state.get("depth", 1))
    names = [str(c.get("name") or "").lower() for c in inv]
    parent_is_single = names == ["singlesersic"]

    _desc, triggers = build_local_state_description(graph, state_label, {})
    stage1_bar = ((graph.g.graph.get("stage1", {})
                   .get("detect_bar_lopsidedness") or {}).get("bar") or {})
    stage1_bar_pa = None
    if stage1_bar.get("detected") and stage1_bar.get("pa_deg") is not None:
        try:
            stage1_bar_pa = float(stage1_bar["pa_deg"])
        except (TypeError, ValueError):
            stage1_bar_pa = None

    def find(name: str):
        return next((c for c in inv if str(c.get("name") or "").lower() == name), None)

    disk = find("disk") or find("edgedisk")
    bulge = find("bulge")
    bar = find("bar")
    lens = find("lens")
    outer = find("outerdisk")
    agn = find("agn")
    anchor = disk or find("singlesersic")

    mags = []
    for c in inv:
        if c.get("mag") is not None:
            try:
                mags.append(float(c["mag"]))
            except (TypeError, ValueError):
                continue
    m0 = min(mags) if mags else 20.0
    centre = None
    if anchor and anchor.get("x") is not None and anchor.get("y") is not None:
        centre = (float(anchor["x"]), float(anchor["y"]))

    disk_re = _re_eff(disk)
    disk_q = _q_of(disk)
    bulge_re = _re_eff(bulge)
    bar_re = _re_eff(bar)
    disk_pa = _pa_trusted(disk)
    bar_pa = _pa_trusted(bar)

    def re_floor() -> float:
        fwhm = graph.g.graph.get("meta", {}).get("psf_fwhm_px")
        return max(0.1, 0.1 * float(fwhm)) if fwhm else 0.1

    candidates: list[dict] = []

    def push(tag: str, sigma: float, prims: list[dict], motivation: str,
             novelty: str = "") -> None:
        hypo, _err = apply_primitives_to_inventory(inv, _to_plain_prims(prims))
        if hypo is None:
            return  # primitives do not land — never propose
        names_hypo = [str(c.get("name") or "").lower() for c in hypo]
        candidates.append({
            "action_id": "",
            "primitives": prims,
            "physical_motivation": motivation,
            "expected_C_prime": "{" + "; ".join(sorted(set(names_hypo))) + "}",
            "novelty_claim": novelty,
            "expected_behavior_tag": tag,
            "local_benefit_sigma": max(0.0, min(1.0, sigma)),
        })

    def add_p(name: str, ctype: str, **kw) -> dict:
        entry = {"structure_name": name, "component_type": ctype}
        entry.update(kw)
        return {"op": "add", "add": entry}

    # fixed-vs-free bulge n follows the solution space (fixed 4 at shallow
    # depth scaffold, free at depth >= 3 — release happens via tune templates)
    bulge_n_free = depth >= 3
    bulge_toggles = {} if bulge_n_free else {"n": 0}

    if parent_is_single:
        re_s = _re_eff(anchor) or 5.0
        convert = {"op": "convert", "convert": {"from_name": "singlesersic",
                                                "to_name": "disk",
                                                "to_type": "expdisk"}}
        # decompose -> bulge (the standard first split)
        push("mcts_decompose_disk_bulge", 0.9,
             [convert, add_p("bulge", "sersic",
                             mag=round(m0 + 1.0, 3),
                             re_px=round(max(1.0, re_s / 4.0), 3),
                             n=4, q=0.9, pa_deg=disk_pa,
                             x_px=None if centre is None else round(centre[0], 3),
                             y_px=None if centre is None else round(centre[1], 3),
                             toggles=dict(bulge_toggles))],
             "Scripted decomposition: convert the single Sersic to an expdisk "
             "disk and add a compact bulge (n=4, flux split 4:6).")
        # decompose -> bar (legal alternative first split; stronger prior on a
        # Stage-1 bar hint — hint-level evidence only)
        bar_sigma = 0.8 if (stage1_bar_pa is not None or stage1_bar.get("detected")) else 0.6
        push("mcts_decompose_disk_bar", bar_sigma,
             [convert, add_p("bar", "sersic",
                             mag=round(m0 + 1.5, 3),
                             re_px=round(min(max(re_s * 0.3, re_floor() + 0.1),
                                             re_s * 0.9), 3),
                             n=0.5, q=0.3,
                             pa_deg=stage1_bar_pa if stage1_bar_pa is not None else disk_pa,
                             x_px=None if centre is None else round(centre[0], 3),
                             y_px=None if centre is None else round(centre[1], 3),
                             toggles={"n": 0})],
             "Scripted decomposition: disk + bar (n=0.5 fixed, high "
             "ellipticity); PA anchored on the Stage-1 bar direction when "
             "available.")
        # NOTE: the edgedisk slot is deliberately NOT in the enumerator's
        # action space — the main (VLM) experiments never landed one and the
        # transcription layer has no edgedisk-aware row semantics
        # (3=Mu(0)/4=h_s/5=R_s), so an edgedisk add would be an unfair,
        # broken move in both arms (KILOGAS_93 fit-#9 Singular-Matrix incident).
        # elliptical + point-core terminal state (legal without conversion)
        push("mcts_add_agn", 0.6,
             [add_p("agn", "psf",
                    mag=round(m0 + 2.5, 3),
                    x_px=None if centre is None else round(centre[0], 3),
                    y_px=None if centre is None else round(centre[1], 3))],
             "Scripted competing variant: single Sersic + psf point core "
             "(central positive residual hypothesis).")
    else:
        if disk is not None and bulge is None:
            push("mcts_add_bulge", 0.9,
                 [add_p("bulge", "sersic",
                        mag=round(m0 + 1.0, 3),
                        re_px=round(max(1.0, (disk_re or 5.0) / 4.0), 3),
                        n=4, q=0.9, pa_deg=disk_pa,
                        x_px=None if centre is None else round(centre[0], 3),
                        y_px=None if centre is None else round(centre[1], 3),
                        toggles=dict(bulge_toggles))],
                 "Scripted central skeleton completion: add a compact bulge "
                 "inside the disk (Re = Re_disk/4, n=4).")
        if disk is not None and bar is None:
            bar_re_init = (disk_re or 5.0) * 0.35
            if bulge_re is not None and bar_re_init <= bulge_re * 1.05:
                bar_re_init = bulge_re * 1.3
            bar_re_init = min(max(bar_re_init, re_floor() + 0.1),
                              (disk_re or 5.0) * 0.9)
            pa = stage1_bar_pa
            if pa is None and bulge is not None and (_q_of(bulge) or 1.0) < 0.5:
                pa = _pa_trusted(bulge)  # a flat bulge's PA marks the bar direction
            if pa is None:
                pa = disk_pa
            push("mcts_add_bar", 0.8 if (stage1_bar_pa is not None) else 0.6,
                 [add_p("bar", "sersic",
                        mag=round(m0 + 1.5, 3),
                        re_px=round(bar_re_init, 3),
                        n=0.5, q=0.3, pa_deg=pa,
                        x_px=None if centre is None else round(centre[0], 3),
                        y_px=None if centre is None else round(centre[1], 3),
                        toggles={"n": 0})],
                 "Scripted bar hypothesis: high-ellipticity n=0.5 sersic "
                 "between bulge and disk radii (quadrupole-residual branch).")
        if bar is not None and lens is None and bar_re is not None and disk_re:
            lens_re = min(max((bar_re + disk_re) / 2.0, bar_re * 1.1),
                          disk_re * 0.9)
            push("mcts_add_lens", 0.6,
                 [add_p("lens", "sersic",
                        mag=round(m0 + 2.0, 3),
                        re_px=round(lens_re, 3),
                        n=0.4, q=0.7,
                        pa_deg=bar_pa if bar_pa is not None else disk_pa,
                        x_px=None if centre is None else round(centre[0], 3),
                        y_px=None if centre is None else round(centre[1], 3))],
                 "Scripted lens hypothesis (bar-anomaly path): flat low-n "
                 "sersic between bar and disk radii.")
        if disk is not None and outer is None and disk_re:
            push("mcts_add_outerdisk", 0.6,
                 [add_p("outerdisk", "sersic",
                        mag=round(m0 + 2.5, 3),
                        re_px=round(disk_re * 2.2, 3),
                        n=0.8, q=_q_of(disk) or 0.5, pa_deg=disk_pa,
                        x_px=None if centre is None else round(centre[0], 3),
                        y_px=None if centre is None else round(centre[1], 3))],
                 "Scripted envelope hypothesis: extended low-n sersic beyond "
                 "2*Re_disk (outer-flux-deficit branch).")
        if agn is None:
            bulge_ok = bulge is None or bulge_re is None
            border_ok = bulge_re is not None and bulge_re <= _RE_EQUIV_BORDER
            if bulge_ok or border_ok:
                sigma = 0.7 if border_ok else 0.5
                if bulge_re is not None and bulge_re <= _RE_EQUIV_FLOOR:
                    sigma = 0.85  # collapsed bulge: mandatory psf replacement
                push("mcts_add_agn", sigma,
                     [add_p("agn", "psf",
                            mag=round(m0 + 2.5, 3),
                            x_px=None if centre is None else round(centre[0], 3),
                            y_px=None if centre is None else round(centre[1], 3))],
                     "Scripted AGN point core (Bulge-Re-collapse / border-zone "
                     "competing variant; numeric admission rule only).")
        if (triggers.get("flat_bulge_bar") and bulge is not None):
            push("mcts_tune_bulge_to_bar", 0.85,
                 [{"op": "tune", "tune": {"structure_name": "bulge",
                                          "param": "n", "value": 0.5,
                                          "toggle": 0}}],
                 "Scripted flat-bulge conversion (trigger values hold): fix "
                 "the elongated bulge's n at 0.5, converting it to a Bar.")
        if (triggers.get("disk_re_bottleneck") and disk is not None
                and disk_re is not None):
            push("mcts_tune_disk_re_growth", 0.85,
                 [{"op": "tune", "tune": {"structure_name": disk.get("name") or "disk",
                                          "param": "re_px",
                                          "value": round(disk_re * 1.3, 3)}}],
                 "Scripted disk-Re growth (bottleneck signal: an extended "
                 "component pinned at its cap while degenerate with the disk).")
        if bulge is not None and ((bulge.get("toggles") or {}).get("n", 1) == 0):
            # prior above removals: the release is a mandatory experiment
            # (classical n~4 vs pseudobulge n~1-2 adjudication) and ingest
            # arms floor_n_release on it automatically
            push("mcts_tune_bulge_n_release", 0.6,
                 [{"op": "tune", "tune": {"structure_name": "bulge",
                                          "param": "n", "toggle": 1}}],
                 "Scripted bulge-n release (classical n~4 vs pseudobulge "
                 "n~1-2 adjudication; mandatory experiment).",
                 # toggle-only experiment: structurally identical landed
                 # inventory — the novelty channel (same use as the surveyor's
                 # "n free axis untested") keeps it out of the R1 ledger hit
                 novelty="bulge-n free axis untested on this inventory "
                         "(toggle-only release; classical vs pseudobulge)")

        # removals (closed-form; R2 dedup discards redundant ones)
        removable = [c for c in inv
                     if str(c.get("name") or "").lower()
                     in {"bar", "lens", "outerdisk", "agn", "bulge"}
                     or _companion(str(c.get("name") or ""))]
        for c in removable:
            target = str(c.get("name") or "").lower()
            remaining = [n for n in names if n != target]
            if len(remaining) > 1 and not ("disk" in remaining
                                           or "edgedisk" in remaining
                                           or "singlesersic" in remaining):
                continue  # would land on a diskless multi-component inventory
            push(f"mcts_remove_{target}", 0.5,
                 [{"op": "remove", "remove": target}],
                 f"Scripted simpler-model competing hypothesis: remove {target} "
                 "(Occam validation; zero-cost-rollback dedup applies).")

    if not candidates:
        return []

    # depth-staged cap (same contract the surveyor's candidate counts follow)
    hi = DEPTH_COUNTS.get(depth, (2, 4))[1]
    candidates.sort(key=lambda d: (-d["local_benefit_sigma"],
                                   d["expected_behavior_tag"]))
    candidates = candidates[:hi]

    # per-candidate legality filter: drop any candidate with an error-level
    # issue so the batch ingest never fails on one illegal scripted move
    combo_counts = graph.combo_counts()
    cap = int(graph.g.graph["meta"].get("per_combo_cap", 4))
    refuted = graph.g.graph.get("refuted_hypotheses", [])
    temp = graph.g.graph.get("temporary_constraints", [])
    ledger = graph.input_ledger_signatures()
    try:
        stage1_bar_pa_v = (((graph.g.graph.get("stage1", {})
                             .get("detect_bar_lopsidedness") or {})
                            .get("bar") or {}).get("pa_deg"))
    except AttributeError:
        stage1_bar_pa_v = None

    legal: list[dict] = []
    from beam.candidate_schema import Candidate

    for cand in candidates:
        parsed = Candidate(**cand)
        issues = validate_candidate(
            parsed, inv, combo_counts, cap, depth, refuted, temp, ledger,
            stage1_bar_pa=stage1_bar_pa_v)
        if any(i.severity == "error" for i in issues):
            graph.log_decision({
                "kind": "mcts-enumerate-illegal", "state": state_label,
                "tag": cand["expected_behavior_tag"],
                "issues": [f"{i.code}: {i.message}" for i in issues
                           if i.severity == "error"],
            })
            continue
        legal.append(cand)

    _combo_guard(graph, legal, state_label)
    return legal


def _combo_guard(graph, candidates: list[dict], state_label: str) -> None:
    """Keep at most one candidate per expected inventory combination: the
    deterministic enumerator would otherwise re-propose near-identical
    composites from the same parent every round (combo caps handle cross-round
    repeats; this guards within a single expansion batch)."""
    seen: set[str] = set()
    kept: list[dict] = []
    for cand in candidates:
        from beam.signature import apply_primitives_to_inventory, combo_identity

        state = graph.state(state_label)
        hypo, _err = apply_primitives_to_inventory(
            state.get("inventory", []), _to_plain_prims(cand["primitives"]))
        combo = combo_identity(hypo) if hypo is not None else "?"
        if combo in seen:
            graph.log_decision({
                "kind": "mcts-enumerate-dedup", "state": state_label,
                "tag": cand["expected_behavior_tag"], "combo": combo})
            continue
        seen.add(combo)
        kept.append(cand)
    candidates[:] = kept


# ------------------------------------------------------------------ selection
def ucb_score(graph, rec: dict, ucb_c: float = UCB_C) -> float:
    """PUCT score of one pending candidate (frontier-UCT selection rule)."""
    st = graph.mcts_stats().get(_stats_key(graph, rec)) or {}
    n_sa = int(st.get("visits", 0))
    q = float(st.get("total_reward", 0.0)) / n_sa if n_sa else 0.0
    sigma = float(rec.get("sigma") or 0.0)
    n_s = graph.mcts_state_visits(rec.get("parent")) \
        if rec.get("parent") in graph.g.nodes else 1
    return q + ucb_c * sigma * math.sqrt(math.log(n_s + 1) / (1 + n_sa))


def ucb_reorder(graph, ucb_c: float = UCB_C) -> list[dict]:
    """Order the pending queue by PUCT score (mcts-arm selection rule).

    Returns the scored queue snapshot (for audit/return payloads). Untried
    directions compete through their prior term only; tried ones add their
    mean reward, and the log-growth exploration term re-arms under-visited
    directions as the parent accumulates fits — the canonical UCT balance.
    """
    pending = graph.g.graph.get("pending", {})
    queue = [a for a in graph.g.graph.get("queue", [])
             if a in pending and pending[a].get("status") == "pending"]
    scored = []
    for aid in queue:
        rec = pending[aid]
        score = ucb_score(graph, rec, ucb_c=ucb_c)
        scored.append({"action_id": aid, "ucb": round(score, 4),
                       "sigma": rec.get("sigma"),
                       "n_sa": int((graph.mcts_stats().get(_stats_key(graph, rec))
                                    or {}).get("visits", 0))})
    scored.sort(key=lambda d: (-d["ucb"], -(d["sigma"] or 0.0), d["action_id"]))
    graph.g.graph["queue"] = [d["action_id"] for d in scored]
    return scored


def _stats_key(graph, rec: dict) -> str:
    from beam.enqueue import _primitive_kinds

    combo = rec.get("combo_key") or "?"
    return f"{rec.get('parent')}|{combo}|{_primitive_kinds(rec.get('primitives', []))}"


# ----------------------------------------------------------------- the round
def mcts_round(galaxy_dir: str, state_label: str = "",
               ucb_c: float = UCB_C) -> dict[str, Any]:
    """One MCTS round on a fitted state (mcts ablation arm's survey_round).

    Settles the code-only physicality verdict (mechanical checks merged),
    backprops the executed edge's reward, expands the state through the
    scripted enumerator (validated + enqueued through the SHARED gates), and
    re-orders the frontier by PUCT. Returns the survey_round contract shape
    plus mcts bookkeeping.
    """
    try:
        from beam.graph import BeamGraph
        from beam.survey import orchestrator_round

        graph = BeamGraph.load(galaxy_dir)
    except Exception as e:
        return {"status": "failure", "error": f"cannot load beam graph: {e}"}

    ablations = graph.g.graph.get("meta", {}).get("ablations") or {}
    if not ablations.get("mcts"):
        return {"status": "failure",
                "error": "E_ARM: mcts_round requires the mcts ablation arm "
                         "(beam_init ablations_json={\"mcts\": true})"}

    label = state_label or graph.latest_state()
    if not label or label not in graph.g.nodes:
        return {"status": "failure", "error": f"state '{label}' not found in the graph"}
    state = graph.state(label)
    if int(state.get("global_iter_id", 0)) == 0:
        return {"status": "failure",
                "error": f"state {label} is the unfitted root — run the first fit "
                         "and beam_record_fit first"}

    # 1. settle the code-only verdict (merge_verdict injects mech results;
    #    no VLM anywhere in this arm)
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": [],
                                "swap_hint": "none"})
    verdict = graph.state(label).get("verdict") or {}

    # 2. backprop the executed edge (child metrics vs parent metrics)
    backprop = _backprop_edge(graph, label, verdict)
    crash_seed = _seed_crash_penalties(graph)
    graph.commit()

    # 3. expansion: scripted candidates through the shared validation+ingest
    try:
        candidates = enumerate_candidates(graph, label)
    except Exception as e:
        return {"status": "failure",
                "error": f"mcts enumeration failed: {e}",
                "backprop": backprop}
    if candidates:
        response = {
            "physicality_verdict": {"verdict": "PASS", "failed_checks": [],
                                    "swap_hint": "none"},
            "candidates": candidates,
        }
        orch = orchestrator_round(galaxy_dir, json.dumps(response),
                                  state_label=label)
        if orch.get("status") != "success":
            orch.setdefault("backprop", backprop)
            return orch
    else:
        graph.log_decision({"kind": "mcts-expansion-empty", "state": label,
                            "note": "no legal scripted macro-action for this state"})
        graph.commit()
        orch = {"status": "success", "enqueued": [], "discarded": []}

    # 4. PUCT selection over the frontier (fresh load: ingest committed)
    graph = BeamGraph.load(galaxy_dir)
    scored_queue = ucb_reorder(graph, ucb_c=ucb_c)
    graph.commit()

    return {
        "status": "success",
        "state": label,
        "verdict": verdict,
        "backprop": backprop,
        "enqueued": orch.get("enqueued", []),
        "discarded": orch.get("discarded", []),
        "protected_directions": orch.get("protected_directions", []),
        "numeric_triggers": orch.get("numeric_triggers", {}),
        "absence_notices": [],
        "queue": scored_queue,
        "next_candidate": graph.next_action(),
        "best_state": graph.g.graph.get("best_state"),
        "termination": graph.termination_check(),
    }


def _seed_crash_penalties(graph) -> list[str]:
    """Seed a near-floor reward onto the directions of FAILED fits (GALFIT
    crash / no output — beam_mark_failure leaves no state, hence no backprop).

    A crash is the strongest negative evidence about a direction's reliability,
    and it is caused by the landed INVENTORY's parameter degeneracy — so the
    penalty propagates COMBO-wide: every pending (parent, direction) whose
    expected combo matches a crashed combo is seeded, not just the exact
    crashed edge. Without this the PUCT would keep treating the crashy
    direction as perpetually untried and re-select it (KILOGAS_93 A.2-c3
    incident: the same add_bar direction re-proposed after a Singular-Matrix
    crash). Idempotent via the stats key.
    """
    CRASH_REWARD = 0.02
    from beam.enqueue import _primitive_kinds

    stats = graph.mcts_stats()
    seeded: list[str] = []
    failed_combos: set[str] = set()
    for rec in graph.g.graph.get("pending", {}).values():
        if rec.get("status") == "failed" and rec.get("combo_key"):
            failed_combos.add(rec["combo_key"])
    if not failed_combos:
        return seeded
    for rec in graph.g.graph.get("pending", {}).values():
        if rec.get("status") != "pending" or rec.get("combo_key") not in failed_combos:
            continue
        key = f"{rec.get('parent')}|{rec.get('combo_key')}" \
              f"|{_primitive_kinds(rec.get('primitives', []))}"
        if key in stats:
            continue
        stats[key] = {"visits": 1, "total_reward": CRASH_REWARD}
        seeded.append(key)
        graph.log_decision({"kind": "mcts-crash-penalty", "parent": rec.get("parent"),
                            "direction": key, "reward": CRASH_REWARD,
                            "note": "failed-fit seeding (no state, no backprop "
                                    "exists — crash counted as near-floor reward, "
                                    "combo-wide propagation)"})
    return seeded


def _backprop_edge(graph, label: str, verdict: dict) -> dict:
    """Update the (parent, direction) statistic of the executed edge."""
    state = graph.state(label)
    dk = graph.state_direction_key(label)
    if dk is None:
        return {"updated": False,
                "reason": "no executed action (deterministic first fit)"}
    parent, key = dk
    parent_metrics = graph.state(parent).get("metrics", {}) \
        if parent in graph.g.nodes else {}
    reward = edge_reward(state.get("metrics", {}), parent_metrics, verdict)
    if reward is None:
        graph.log_decision({
            "kind": "mcts-backprop-skip", "state": label, "direction": key,
            "note": "invalid evidence (sub-converged or unmeasurable) — "
                    "no statistic update (Refutation validity parity)"})
        return {"updated": False, "reason": "invalid evidence (reward skipped)"}
    rec = graph.mcts_update_stats(parent, key, reward)
    graph.log_decision({"kind": "mcts-backprop", "state": label,
                        "parent": parent, "direction": key,
                        "reward": round(reward, 4),
                        "visits": rec["visits"],
                        "mean_q": round(rec["total_reward"] / rec["visits"], 4)})
    return {"updated": True, "parent": parent, "direction": key,
            "reward": round(reward, 4), "visits": rec["visits"]}
