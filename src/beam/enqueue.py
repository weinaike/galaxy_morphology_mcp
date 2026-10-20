"""Enqueue engine: legality gates (R0/R1/R2, temporary constraints) + the
code-side overlay on surveyor scores (floors, diversity, starvation aging,
persistent-candidate protection) + queue truncation.

Design contract ("surveyor orders, code decides legality"):
* the surveyor's local_benefit_sigma is a POSTERIOR probability — p(this
  candidate's direction becomes the final best fit | everything seen so far),
  redefined 2026-10-09 (the old "expected chi^2 fraction" semantics decayed
  monotonically with round number and systematically under-scored cheap
  high-information experiments): it starts ~0.5 for genuinely new directions
  and must NOT decay merely because the round index grows;
* mandatory-retention floors, path diversity (weight x2), aging and the
  persistent-candidate protection are computed here — structural
  counterweights to any residual VLM miscalibration;
* floor-flagged candidates are exempt from g_min discards and from queue
  truncation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from beam.candidate_schema import Candidate
from beam.signature import (
    apply_primitives_to_inventory,
    canonical_signature,
    combo_identity,
    project_closed_form,
    signature_equivalent,
    strip_zombies,
)

G_MIN = 0.3
FLOOR_G = 0.5
PERSISTENT_G = 0.6
# persistent-protection trigger under the POSTERIOR sigma semantics: a fresh
# direction starts at ~0.5, so the old >=0.5 trigger would arm protection on
# the first mention of anything; require a genuinely above-prior posterior
# (>=0.6, matching the PERSISTENT_G lift) in >=2 calls instead
PERSISTENT_SIGMA = 0.6
AGING_BONUS = 0.02
AGING_CAP = 0.10
# surveyor rank-1 pin: matches the maximum floor(0.05)+aging(0.10) bonus, so
# the surveyor's explicit pick leads in ordering while floor entries keep
# their eviction protection and their base-score priority
SURVEYOR_PIN_BONUS = 0.15
# off-best-path demotion factor (ordering + truncation): candidates parented
# on superseded states rank at ~2/3 of their nominal score
OFF_PATH_FACTOR = 0.65

# weights: dimension 3 (path diversity) x2, cf. workflow §Deduplication & Ranking
W = [1.0, 1.0, 2.0, 1.0, 1.0]


@dataclass
class IngestResult:
    enqueued: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)


def _parent_bulge_q(parent_inventory: list[dict]) -> float | None:
    bulge = next((c for c in parent_inventory if c.get("name") == "bulge"), None)
    if bulge is None:
        return None
    q = bulge.get("q", bulge.get("ba"))
    try:
        return float(q)
    except (TypeError, ValueError):
        return None


def _primitive_kinds(primitives: list[dict]) -> str:
    kinds = []
    for p in primitives:
        op = p.get("op", "?")
        if op == "add":
            kinds.append(f"add:{(p.get('structure_name') or '').lower()}")
        elif op == "remove":
            kinds.append(f"remove:{(p.get('target') or '').lower()}")
        elif op == "tune":
            kinds.append(f"tune:{(p.get('structure_name') or '').lower()}."
                         f"{(p.get('param') or 'bound')}")
        elif op == "convert":
            kinds.append("convert")
        elif op == "skeleton":
            comps = (p.get("skeleton") or {}).get("components") or p.get("components") or []
            kinds.append("skeleton:" + "+".join(
                sorted((c.get("structure_name") or "?").lower() for c in comps)))
    return "+".join(sorted(kinds))


def prims_are_n_release(primitives: list[dict], parent_re: float | None = None) -> bool:
    for p in primitives:
        if p.get("op") == "tune":
            t = p
            if (t.get("structure_name") or "").lower() == "bulge" and t.get("param") == "n" \
                    and (t.get("toggle") == 1 or t.get("toggle") is None and t.get("value")):
                return True
    return False


def prims_are_bar_direction(primitives: list[dict], parent_re: float | None = None) -> bool:
    for p in primitives:
        if p.get("op") == "add" and (p.get("structure_name") or "").lower() == "bar":
            return True
        if p.get("op") == "convert":
            to = ((p.get("convert") or {}).get("to_name") or "").lower()
            if to == "bar":
                return True
    return False


def prims_are_disk_re_growth(primitives: list[dict],
                             parent_re: float | None = None) -> bool:
    for p in primitives:
        if p.get("op") != "tune":
            continue
        if (p.get("structure_name") or "").lower() in {"disk", "edgedisk"} \
                and p.get("param") == "re_px" and p.get("value"):
            if parent_re is None or float(p["value"]) > float(parent_re):
                return True
    return False


def prims_are_lens_relax(primitives: list[dict], parent_re: float | None = None) -> bool:
    for p in primitives:
        if p.get("op") != "tune":
            continue
        if (p.get("structure_name") or "").lower() == "lens" and p.get("cons_bounds"):
            cb = p["cons_bounds"] or {}
            if cb.get("re") and cb["re"][1] and cb["re"][1] > (cb["re"][0] or 0):
                return True
    return False


def prims_are_agn_add(primitives: list[dict], parent_re: float | None = None) -> bool:
    """An AGN point-core candidate: any add(agn, psf) — including the
    collapsed-bulge replacement composite remove(bulge)+add(agn)."""
    for p in primitives:
        if p.get("op") == "add" and (p.get("structure_name") or "").lower() == "agn":
            return True
    return False


# floor flag -> primitive-level predicate, uniform (primitives, parent_re)
# signature (only disk-Re growth consumes parent_re). parent_re is the Re of
# the disk slot in the round's OWN parent inventory — required so a
# disk-Re SHRINK executed round never discharges a floor_disk_re blocker
# (graph._discharge_satisfied_floors passes it per executed record).
FLOOR_PREDICATES = {
    "floor_n_release": prims_are_n_release,
    "floor_bar_direction": prims_are_bar_direction,
    "floor_disk_re": prims_are_disk_re_growth,
    "floor_lens_relax_d": prims_are_lens_relax,
    "floor_agn_spike": prims_are_agn_add,
}


def _is_n_release(cand: Candidate) -> bool:
    return prims_are_n_release(cand.to_plain_primitives())


def _is_bar_direction(cand: Candidate) -> bool:
    return prims_are_bar_direction(cand.to_plain_primitives())


def _is_disk_re_growth(cand: Candidate, parent_re: float | None) -> bool:
    return prims_are_disk_re_growth(cand.to_plain_primitives(), parent_re)


def _is_lens_relax(cand: Candidate) -> bool:
    return prims_are_lens_relax(cand.to_plain_primitives())


def _is_agn_add(cand: Candidate) -> bool:
    return prims_are_agn_add(cand.to_plain_primitives())


def _score_candidate(cand: Candidate, hypo_combo: str, combo_counts: dict[str, int],
                     queued_directions: set[str], direction_key: str,
                     verdict_fail: bool,
                     parent_inventory: list[dict] | None = None) -> tuple[float, dict]:
    """Deterministic six-dimension score; returns (g, flags)."""
    sigma = float(cand.local_benefit_sigma)
    prims = cand.to_plain_primitives()

    # 1 residual-improvement potential (surveyor sigma)
    s1 = sigma
    # 2 physical-plausibility prior (coarse addition-order conformance)
    adds = [p for p in prims if p.get("op") == "add"]
    if not adds:
        s2 = 0.9
    else:
        names = {(p.get("structure_name") or "").lower() for p in adds}
        if names <= {"bulge", "bar", "f1"} or names <= {"companion"}:
            s2 = 1.0
            # Skeleton ladder (Disk -> Bulge -> Bar; KILOGAS_73 austerity
            # rerun: the disk+bar jump burned fit 2 at dBIC +4578 while the
            # disk+bulge foundation candidate idled in the queue): a BAR
            # added while the parent carries NO bulge (and none is being
            # added) skips a rung — demote it below the bulge-first order.
            parent_names = {(c.get("name") or "").lower()
                            for c in (parent_inventory or [])}
            if "bar" in names and "bulge" not in names \
                    and "bulge" not in parent_names:
                s2 = 0.8
        elif "lens" in names or "outerdisk" in names:
            s2 = 0.6
        elif "agn" in names:
            s2 = 0.7
        else:
            s2 = 0.8
    # 3 path diversity (x2): untried inventory scores high
    executed = combo_counts.get(hypo_combo, 0)
    if executed == 0:
        s3 = 1.0
    elif executed == 1:
        s3 = 0.6
    elif executed == 2:
        s3 = 0.4
    else:
        s3 = 0.25
    if direction_key in queued_directions:
        s3 = min(s3, 0.3)
    # 4 degeneracy penalty: on a FAIL parent, repair tunes rank above adds
    if verdict_fail:
        s4 = 0.9 if not adds else 0.5
    else:
        s4 = 0.8
    # 5 historical consistency: a real novelty claim is required
    s5 = 1.0 if cand.novelty_claim.strip() else 0.7

    g = (W[0] * s1 + W[1] * s2 + W[2] * s3 + W[3] * s4 + W[4] * s5) / sum(W)
    return max(0.0, min(1.0, g)), {}


def ingest(graph, candidates: list[Candidate], session_id: str, parent_label: str,
           verdict_fail: bool = False,
           numeric_triggers: dict | None = None) -> IngestResult:
    """Legality gates + overlay + enqueue. Mutates the graph (pending records,
    decision log, direction history); the caller commits.

    numeric_triggers: {"disk_re_bottleneck": bool, "lens_relax_d": bool,
    "flat_bulge_bar": bool} from the digest module — they arm the
    corresponding floors.
    """
    result = IngestResult()
    triggers = numeric_triggers or {}
    crash_kinds = graph.crash_kinds() if hasattr(graph, "crash_kinds") else {}
    from beam.cells import (CellLedger, cells_of, primitives_carry_f1,
                            skeleton_class)
    from beam.graph import CLASS_CAPS
    cell_ledger = CellLedger.from_graph(graph)
    cls_counts = graph.class_attempt_counts()
    parent = graph.state(parent_label)
    parent_inventory = parent.get("inventory", [])
    combo_counts = graph.combo_counts()
    ledger = graph.input_ledger_signatures()
    zombie_map = {label: graph.state(label).get("zombies", [])
                  for label in graph.result_ledger_states()}
    queued_directions = {
        _primitive_kinds(r.get("primitives", []))
        for r in graph.g.graph.get("pending", {}).values()
        if r.get("status") == "pending"
    }
    direction_history: list[dict] = graph.g.graph.setdefault("direction_history", [])

    # validated queue_reorder requests (pending, non-floor) ride on candidates
    reorder_reqs = [(qr.action_id, qr.new_rank)
                    for cand in candidates for qr in (cand.queue_reorder or [])]

    def _prim_kind(p):
        op = p.get("op", "?")
        inner = p.get(op) if isinstance(p.get(op), dict) else {}
        name = (p.get("structure_name") or inner.get("structure_name")) or ""
        if op == "add":
            return f"add:{(name or '?').lower()}"
        if op == "remove":
            return f"remove:{(p.get('target') or inner.get('remove') or '?').lower()}"
        if op == "tune":
            return f"tune:{(name or '?').lower()}.{p.get('param') or inner.get('param') or 'bound'}"
        return op

    for cand in candidates:
        prims = cand.to_plain_primitives()
        # ---- crash-pattern suppression: a primitive kind that already crashed
        # twice is banned for the rest of the run (KILOGAS_221 rerun: 4/7 fits
        # lost to repeated add:bar/add:bulge singular-matrix crashes)
        crashed_kinds = {k for k in (_prim_kind(p) for p in prims)
                         if int((crash_kinds.get(k) or {}).get("count", 0)) >= 2}
        if crashed_kinds:
            result.discarded.append({"reason": "E_CRASH_PATTERN",
                                     "kinds": sorted(crashed_kinds),
                                     "tag": cand.expected_behavior_tag})
            graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                "reason": f"E_CRASH_PATTERN: {sorted(crashed_kinds)}",
                                "tag": cand.expected_behavior_tag})
            continue
        disk = next((c for c in parent_inventory if c.get("name") in {"disk", "edgedisk"}), None)
        # effective Re (px): raw inventories store Rs for expdisk/edgedisk —
        # convert, else the floor_disk_re growth test compares a Re-units
        # tune value against Rs (1.68x over-lenient; true shrinks pass)
        if disk is None:
            parent_re = None
        else:
            parent_re = disk.get("re_effective")
            if parent_re is None:
                parent_re = disk.get("re")
                if (disk.get("type") or "").lower() in ("expdisk", "edgedisk") \
                        and parent_re is not None:
                    parent_re = float(parent_re) * 1.68

        hypo, err = apply_primitives_to_inventory(parent_inventory, prims)
        if hypo is None:
            result.discarded.append({"reason": "E_APPLY", "detail": err,
                                     "tag": cand.expected_behavior_tag})
            graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                "reason": f"E_APPLY: {err}",
                                "tag": cand.expected_behavior_tag})
            continue

        hypo_combo = combo_identity(hypo)
        # ---- R0 (2026-10-17 finalized scheme): skeleton-class caps replace
        # the retired per-combo cap (the CELL filter moved below the code
        # flags — floors are exempt from it).
        # Class cap: skeleton-family budget a=2 / b=4 / c=5
        cls = skeleton_class(hypo)
        if cls_counts.get(cls, 0) >= CLASS_CAPS[cls]:
            result.discarded.append({"reason": "CLASS_CAP", "class": cls,
                                     "tag": cand.expected_behavior_tag})
            graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                "reason": f"CLASS_CAP: class {cls} exhausted "
                                          f"{CLASS_CAPS[cls]} attempts",
                                "tag": cand.expected_behavior_tag})
            continue

        # ---- R1 input-ledger equivalence (strict bands-aware form)
        hypo_sig = canonical_signature(hypo, cons_bands=parent.get("cons_effective"))
        dup = False
        for led_sig in ledger:
            if signature_equivalent(hypo_sig, led_sig) and not cand.novelty_claim.strip():
                result.discarded.append({"reason": "R1_LEDGER_EQUIVALENT",
                                         "tag": cand.expected_behavior_tag})
                graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                    "reason": "R1_LEDGER_EQUIVALENT",
                                    "tag": cand.expected_behavior_tag})
                dup = True
                break
        if dup:
            continue

        # ---- R2 closed-form projection onto the result ledger (zombie-aware)
        proj = project_closed_form(parent_inventory, prims,
                                   bound_ctx=_bound_ctx(graph))
        if proj is not None:
            proj_sig = canonical_signature(proj.inventory)
            hit = None
            for label in graph.result_ledger_states():
                ledger_sig = graph.state(label).get("signature", {})
                stripped = strip_zombies(ledger_sig, zombie_map.get(label, []))
                if signature_equivalent(proj_sig, stripped, ignore_toggles=True,
                                        ignore_bands=True):
                    hit = label
                    break
            if hit is not None:
                result.discarded.append({"reason": "R2_EXACT_HIT", "rollback_to": hit,
                                         "tag": cand.expected_behavior_tag})
                graph.log_decision({"kind": "r2-zero-cost-rollback", "session": session_id,
                                    "from": parent_label, "rollback_to": hit,
                                    "tag": cand.expected_behavior_tag})
                continue

        # ---- temporary constraints (active forbid lists)
        violated = _temp_constraint_violation(graph, prims)
        if violated:
            result.discarded.append({"reason": "E_TEMP_CONSTRAINT", "detail": violated})
            graph.log_decision({"kind": "temporary-constraint-discard",
                                "session": session_id, "detail": violated})
            continue

        # ---- code flags (floors) + persistent-candidate protection
        flags: dict[str, bool] = {}
        if any(p.get("op") == "skeleton" for p in prims):
            # round-0 opening skeletons are the run's fallback foundations —
            # never lost to queue truncation (KILOGAS_73 rerun: the disk+bulge
            # alternate was truncated at A.2 and only survived in spirit)
            flags["opening_alternate"] = True
        if _is_n_release(cand):
            flags["floor_n_release"] = True
        if _is_bar_direction(cand) and (triggers.get("flat_bulge_bar")
                                        or _parent_bulge_q(parent_inventory) is not None
                                        and _parent_bulge_q(parent_inventory) < 0.5):
            flags["floor_bar_direction"] = True
        if _is_disk_re_growth(cand, parent_re) and triggers.get("disk_re_bottleneck"):
            flags["floor_disk_re"] = True
        if _is_lens_relax(cand) and triggers.get("lens_relax_d"):
            flags["floor_lens_relax_d"] = True
        if _is_agn_add(cand) and triggers.get("central_spike_agn"):
            flags["floor_agn_spike"] = True

        direction_key = f"{hypo_combo}|{_primitive_kinds(prims)}"
        prior = [h for h in direction_history
                 if h.get("direction") == direction_key]
        # the current call counts as one of the >=2 proposals
        sessions = {h.get("session") for h in prior
                    if h.get("sigma", 0) >= PERSISTENT_SIGMA}
        sessions.add(session_id)
        never_executed = not any(h.get("executed") for h in prior)
        persistent = len(sessions) >= 2 and never_executed \
            and cand.local_benefit_sigma >= PERSISTENT_SIGMA
        if persistent:
            flags["persistent_protection"] = True
            result.protected.append(direction_key)

        # ---- R0 cell filter (2026-10-17 finalized scheme; 2026-10-20 dual
        # registration): a candidate whose EVERY component cell is already
        # visited (input OR attractor set, same inventory scope) is a
        # same-basin re-entry — discard (138-galaxy replay: zero true
        # best-kill). FLOOR candidates alone are EXEMPT: mandated-retention
        # channels land in visited cells deliberately (a disk-Re growth tune
        # must survive a same-bin revisit to escape a bottleneck). Other
        # flags do NOT shelter a cell: an opening_alternate skeleton twin
        # with fully-visited cells is redundant (KILOGAS_110/269 replay —
        # a sheltered phantom twin consumed class-cap quota and killed the
        # real best).
        _cell_exempt = any(k.startswith("floor_") for k in flags)
        if not _cell_exempt and cell_ledger.is_dup(hypo, primitives_carry_f1(prims)):
            result.discarded.append({"reason": "CELL_DUP",
                                     "tag": cand.expected_behavior_tag})
            graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                "reason": "CELL_DUP (every component cell visited "
                                          "in this inventory)",
                                "tag": cand.expected_behavior_tag})
            continue

        g, _ = _score_candidate(cand, hypo_combo, combo_counts, queued_directions,
                                direction_key, verdict_fail,
                                parent_inventory=parent_inventory)
        if flags:
            g = max(g, FLOOR_G)
        if persistent:
            g = max(g, PERSISTENT_G)

        if g < G_MIN and not flags:
            result.discarded.append({"reason": "BELOW_GMIN", "g": round(g, 3),
                                     "tag": cand.expected_behavior_tag})
            graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                "reason": f"g < {G_MIN} (g={g:.3f})",
                                "tag": cand.expected_behavior_tag})
            continue

        # ---- cell refresh (2026-10-20): a queued non-protected twin whose
        # projected cells are IDENTICAL to this candidate's is replaced by
        # the fresher one (a later VLM call scores the same cell more
        # accurately — the sigma carries the newer evidence, and the
        # projection rides the newer parent state). A PROTECTED twin keeps
        # its slot and shelters the cell: the new candidate is the
        # redundant one.
        cand_has_f1 = primitives_carry_f1(prims)
        new_cells = cells_of(hypo)
        _pending = graph.g.graph.get("pending", {})
        _queue = [a for a in graph.g.graph.get("queue", []) if a in _pending]
        twin, twin_protected = None, False
        for _aid in _queue:
            _rec = _pending.get(_aid) or {}
            if _rec.get("status") != "pending":
                continue
            _qprims = _rec.get("primitives") or []
            if primitives_carry_f1(_qprims) != cand_has_f1:
                continue  # different inventory scope
            _qpar = _rec.get("parent")
            if not _qpar:
                continue
            _qh, _e = apply_primitives_to_inventory(
                graph.state(_qpar).get("inventory", []), _qprims)
            if _qh is None:
                continue
            if cells_of(_qh) == new_cells:
                twin = _aid
                # only floor channels (and the reserved F1 slot) shelter a
                # cell — surveyor_rank_pin is a per-batch ordering artifact,
                # and opening_alternate/persistent shelter queue survival,
                # not cell ownership
                twin_protected = any(
                    k == "f1_reserved" or k.startswith("floor_")
                    for k in (_rec.get("code_flags") or {}))
                break
        if twin is not None and twin_protected:
            result.discarded.append({"reason": "CELL_QUEUED_PROTECTED",
                                     "queued_twin": twin,
                                     "tag": cand.expected_behavior_tag})
            graph.log_decision({"kind": "enqueue-discard", "session": session_id,
                                "reason": f"CELL_QUEUED_PROTECTED (protected twin "
                                          f"{twin} occupies this cell)",
                                "tag": cand.expected_behavior_tag})
            continue

        action_id = graph.add_pending(
            {"action_id": cand.action_id, "sigma": cand.local_benefit_sigma,
             "expected_behavior_tag": cand.expected_behavior_tag,
             "expected_C_prime": cand.expected_C_prime,
             "novelty_claim": cand.novelty_claim, "primitives": prims,
             "code_flags": flags, "score": round(g, 4),
             "combo_key": hypo_combo},
            source_session=session_id, parent_label=parent_label)
        result.enqueued.append({"action_id": action_id, "g": round(g, 4),
                                "flags": sorted(k for k, v in flags.items() if v),
                                "tag": cand.expected_behavior_tag})
        direction_history.append({"direction": direction_key, "session": session_id,
                                  "sigma": cand.local_benefit_sigma,
                                  "action_id": action_id, "executed": False})
        queued_directions.add(direction_key)
        if twin is not None:
            # non-protected twin: the fresher candidate takes over the cell
            old = _pending[twin]
            old["status"] = "discarded"
            old["discard_reason"] = "CELL_REFRESHED"
            old["replaced_by"] = action_id
            # re-read the queue — add_pending just appended the successor
            graph.g.graph["queue"] = [a for a in graph.g.graph.get("queue", [])
                                      if a != twin]
            graph.log_decision({"kind": "cell-refresh", "session": session_id,
                                "replaced": twin, "by": action_id,
                                "tag": cand.expected_behavior_tag})

    # defect E fix: pin this batch's highest-g new candidate (the code-ranked
    # pick) so queue truncation — stale floors/aged entries occupying all W
    # slots — cannot silently discard the freshest top direction
    if result.enqueued:
        best = max(result.enqueued, key=lambda e: e["g"])
        graph.g.graph["pending"][best["action_id"]]["surveyor_rank_pin"] = 1

    _truncate_queue(graph)
    _reorder_queue(graph)
    # apply validated queue_reorder requests as persistent pins (defect E:
    # they were checked but never applied, so the eff re-sort buried them)
    pending = graph.g.graph.get("pending", {})
    pinned_any = False
    for aid, new_rank in reorder_reqs:
        rec = pending.get(aid)
        if rec and rec.get("status") == "pending" and not rec.get("code_flags"):
            rec["surveyor_rank_pin"] = int(new_rank)
            pinned_any = True
    if pinned_any:
        _truncate_queue(graph)
        _reorder_queue(graph)
    return result


def _temp_constraint_violation(graph, prims: list[dict]) -> str | None:
    import re

    companion_re = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)
    for tc in graph.g.graph.get("temporary_constraints", []):
        if not tc.get("active", True):
            continue
        for struct in tc.get("forbid_structures", []):
            for p in prims:
                name = None
                if p.get("op") == "add":
                    name = (p.get("structure_name") or "").lower()
                elif p.get("op") == "tune":
                    name = (p.get("structure_name") or "").lower()
                if name and (name == struct.lower()
                             or companion_re.match(name) and companion_re.match(struct.lower())):
                    return f"{tc.get('text', 'temporary constraint')} forbids '{name}'"
    return None


def _bound_ctx(graph):
    from beam.cons_decode import BoundContext

    meta = graph.g.graph.get("meta", {})
    region = meta.get("fit_region")
    return BoundContext(psf_fwhm_px=meta.get("psf_fwhm_px"),
                        fit_region=tuple(region) if region else None)


def _truncate_queue(graph) -> None:
    """Keep W entries; floor-flagged and surveyor-pinned candidates are never
    evicted unless W protected entries overflow (then lowest score goes)."""
    W = int(graph.g.graph["meta"].get("W", 5))
    pending = graph.g.graph.get("pending", {})
    queue = [a for a in graph.g.graph.get("queue", []) if a in pending]
    if len(queue) <= W:
        return
    on_path = graph.best_path_labels()

    def rank(aid):
        rec = pending[aid]
        protected = bool(rec.get("code_flags")) or bool(rec.get("surveyor_rank_pin"))
        score = rec.get("score") or 0.0
        if rec.get("parent") not in on_path:
            score *= OFF_PATH_FACTOR  # evict off-path entries first
        return (0 if protected else 1,
                -score,
                -rec.get("age_counter", 0))
    ordered = sorted(queue, key=rank)
    keep, evict = ordered[:W], ordered[W:]
    graph.g.graph["queue"] = keep
    for aid in evict:
        pending[aid]["status"] = "discarded"
        pending[aid]["discard_reason"] = "QUEUE_TRUNCATION"
        graph.log_decision({"kind": "queue-truncation", "action_id": aid})


def _reorder_queue(graph) -> None:
    """Order by effective score (g + bounded aging bonus), floors float up.

    The starvation-aging bonus applies ONLY to candidates whose parent lies on
    the current best path (defect C: an aged candidate from a long-superseded
    parent — e.g. a pre-bar model — outranked fresh repairs and wasted a fit).
    Off-path candidates keep their base score (and any floor flag bonus).
    """
    pending = graph.g.graph.get("pending", {})
    queue = [a for a in graph.g.graph.get("queue", []) if a in pending
             and pending[a].get("status") == "pending"]
    on_path = graph.best_path_labels()
    def order(aid):
        rec = pending[aid]
        eff = rec.get("score") or 0.0
        if rec.get("parent") not in on_path:
            # off-best-path demotion (KILOGAS_73 rerun: add_agn_core on the
            # superseded bar+disk state survived 3 rounds and burned fit 7 at
            # dBIC +5715) — fresh repairs on the best branch lead
            eff *= OFF_PATH_FACTOR
        else:
            eff += min(AGING_BONUS * rec.get("age_counter", 0), AGING_CAP)
        if rec.get("code_flags"):
            eff += 0.05
        if rec.get("surveyor_rank_pin") == 1:
            eff += SURVEYOR_PIN_BONUS
        return (-eff, aid)
    graph.g.graph["queue"] = sorted(queue, key=order)
