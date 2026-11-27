"""Enqueue engine: legality gates (R0/R1/R2, temporary constraints, cells)
+ the single pred_bic queue ordering + queue truncation.

Design contract ("surveyor gives pred_bic, code decides legality"):
* the surveyor's ``pred_bic`` (predicted attainable BIC_eff) is the ONLY
  ranking input — the queue is ordered ascending by pred_bic (Python stable
  sort: ties keep insertion order). No score overlay, no floors-on-score,
  no aging, no pins, no off-path demotion exists;
* mandatory-retention channels survive as ``code_flags`` (floor_*, opening
  alternate, f1_reserved): they never gate ordering, they only shield their
  entries from queue truncation and cell preemption;
* round-0 opening skeleton batches are NORMALISED at ingest: the code
  ignores the declared values and encodes the array order (the VLM's own
  ranking) mechanically as pred_bic = i - (K-1) — the top-ranked skeleton
  is most negative and executes first.
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


@dataclass
class IngestResult:
    enqueued: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)


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


def prims_are_bulge_rebuild(primitives: list[dict], parent_re: float | None = None) -> bool:
    """A four-axis bulge reseed (user rule 2026-11-17): the remove(bulge) +
    add(bulge) rebuild composite — the only form that redeclares
    Re/n/q/PA/mag jointly in one fit under the 2-primitive cap."""
    has_rm = has_add = False
    for p in primitives:
        if p.get("op") == "remove":
            tgt = (p.get("target") or p.get("structure_name")
                   or (p.get("remove") if isinstance(p.get("remove"), str) else "") or "").lower()
            if tgt == "bulge":
                has_rm = True
        if p.get("op") == "add" and (p.get("structure_name") or "").lower() == "bulge":
            has_add = True
    return has_rm and has_add


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
    "floor_bulge_reseed": prims_are_bulge_rebuild,
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


def _is_bulge_rebuild(cand: Candidate) -> bool:
    return prims_are_bulge_rebuild(cand.to_plain_primitives())


def ingest(graph, candidates: list[Candidate], session_id: str, parent_label: str,
           numeric_triggers: dict | None = None) -> IngestResult:
    """Legality gates + enqueue. Mutates the graph (pending records, decision
    log, direction history); the caller commits.

    numeric_triggers: {"disk_re_bottleneck": bool, "lens_relax_d": bool,
    "flat_bulge_bar": bool} from the digest module — they arm the
    corresponding floor code_flags (truncation protection, never ordering).
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
    direction_history: list[dict] = graph.g.graph.setdefault("direction_history", [])

    # round-0 skeleton batch normalisation: the array order IS the VLM's
    # ranking — encode it mechanically as i-(K-1) (K=3 -> -2, -1, 0) so the
    # top-ranked skeleton is most negative and lands at the queue head. The
    # declared pred_bic values of skeleton candidates are ignored.
    skel_pred = {id(c): i - (len(candidates) - 1)
                 for i, c in enumerate(candidates)
                 if any(p.op == "skeleton" for p in c.primitives)}

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

        if (graph.g.graph.get("meta", {}).get("mode") or "galfit").lower() \
                == "galfits":
            # mb projection (px+panel_band primitives; galfit's projector
            # does not know the mb tune keys and would leave Re unmoved —
            # every mb tune would then look same-cell and be CELL_DUP'd)
            from beam_mb.candidate_schema_mb import project_mb_inventory

            hypo = project_mb_inventory(
                parent_inventory, prims,
                graph.g.graph.get("meta", {}).get("bands") or {})
            err = None
        else:
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
        if _is_bulge_rebuild(cand) and triggers.get("small_bulge_reseed"):
            flags["floor_bulge_reseed"] = True

        direction_key = f"{hypo_combo}|{_primitive_kinds(prims)}"

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

        # ---- cell refresh (2026-10-20): a queued non-protected twin whose
        # projected cells are IDENTICAL to this candidate's is replaced by
        # the fresher one (a later VLM call assesses the same cell with the
        # newer evidence — the pred_bic carries it, and the projection rides
        # the newer parent state). A PROTECTED twin keeps its slot and
        # shelters the cell: the new candidate is the redundant one.
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
                # cell — opening_alternate shelters queue survival, not
                # cell ownership
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

        pred_bic = float(skel_pred.get(id(cand), cand.pred_bic))
        action_id = graph.add_pending(
            {"action_id": cand.action_id, "pred_bic": pred_bic,
             "expected_behavior_tag": cand.expected_behavior_tag,
             "expected_C_prime": cand.expected_C_prime,
             "novelty_claim": cand.novelty_claim, "primitives": prims,
             "code_flags": flags,
             "combo_key": hypo_combo},
            source_session=session_id, parent_label=parent_label)
        result.enqueued.append({"action_id": action_id, "pred_bic": pred_bic,
                                "flags": sorted(k for k, v in flags.items() if v),
                                "tag": cand.expected_behavior_tag})
        direction_history.append({"direction": direction_key, "session": session_id,
                                  "pred_bic": pred_bic,
                                  "action_id": action_id, "executed": False})
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

    _truncate_queue(graph)
    _order_queue(graph)
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
                elif p.get("op") == "skeleton":
                    # opening skeletons declare whole inventories — a forbidden
                    # component inside one violates the constraint equally
                    # (2026-10-23 expert-guided s1: T's complement must bind
                    # the round-0 skeletons, not just later add/tune moves)
                    comps = ((p.get("skeleton") or {}).get("components")
                             or p.get("components") or [])
                    names = {(c.get("structure_name") or "").lower() for c in comps}
                    hit = next((n for n in names
                                if n == struct.lower()
                                or companion_re.match(n)
                                and companion_re.match(struct.lower())), None)
                    if hit:
                        return f"{tc.get('text', 'temporary constraint')} forbids " \
                               f"skeleton component '{hit}'"
                    continue
                elif p.get("op") == "reclassify":
                    # identity reassignment to a forbidden slot smuggles the
                    # structure in under a new name — the map's targets are
                    # subject to the forbid list like any add
                    mapping = ((p.get("reclassify") or {}).get("map")
                               or p.get("map") or [])
                    hit = next((m.get("to", "").lower() for m in mapping
                                if (m.get("to") or "").lower() == struct.lower()),
                               None)
                    if hit:
                        return f"{tc.get('text', 'temporary constraint')} forbids " \
                               f"reclassify target '{hit}'"
                    continue
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
    """Keep W entries; code-flagged candidates (floors / opening alternates /
    the reserved F1) are never evicted. Among the unprotected remainder the
    HIGHEST pred_bic (least promising) is evicted first; older entries go
    before younger ones at equal pred_bic."""
    W = int(graph.g.graph["meta"].get("W", 5))
    pending = graph.g.graph.get("pending", {})
    queue = [a for a in graph.g.graph.get("queue", []) if a in pending]
    if len(queue) <= W:
        return

    def rank(aid):
        rec = pending[aid]
        protected = bool(rec.get("code_flags"))
        pred = rec.get("pred_bic")
        pred = float(pred) if pred is not None else 0.0
        return (0 if protected else 1,
                pred,
                -rec.get("age_counter", 0))
    ordered = sorted(queue, key=rank)
    keep, evict = ordered[:W], ordered[W:]
    graph.g.graph["queue"] = keep
    for aid in evict:
        pending[aid]["status"] = "discarded"
        pending[aid]["discard_reason"] = "QUEUE_TRUNCATION"
        graph.log_decision({"kind": "queue-truncation", "action_id": aid})


def _order_queue(graph) -> None:
    """Order the queue by pred_bic ascending — the single ranking rule.

    Python's stable sort keeps insertion order on ties (the batch order the
    surveyor emitted). Code flags do NOT reorder (they only shield from
    truncation/preemption).
    """
    pending = graph.g.graph.get("pending", {})
    queue = [a for a in graph.g.graph.get("queue", []) if a in pending
             and pending[a].get("status") == "pending"]

    def order(aid):
        pred = pending[aid].get("pred_bic")
        return float(pred) if pred is not None else 0.0

    graph.g.graph["queue"] = sorted(queue, key=order)
