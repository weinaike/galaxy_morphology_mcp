#!/usr/bin/env python3
"""Shadow replay of the x/y DUAL cell registration (2026-10-20) over the
138-galaxy set — the extension of replay_final_scheme.py:

  * every executed fit registers BOTH its input cells (x) and its fitted
    attractor cells (y), scoped per inventory (+F1 flag);
  * a candidate is a DUP iff every component's cell is visited in the
    input set OR the attractor set of the same inventory scope (the
    KILOGAS_197 warm-start twin: declared change not a cell dimension ->
    lands on the parent attractor);
  * candidates whose pending record carries a FLOOR flag (floor_*) are
    EXEMPT from the cell filter (mandated-retention channels), matching
    enqueue.py. Other flags do NOT shelter a cell: the first run with a
    blanket code_flags exemption let historical phantom double-records
    (opening_alternate twins with identical cells) consume class-cap
    quota and killed the real best on KILOGAS_110/269.

Question: above the input-only baseline (77 filtered / 0 true best-kill),
how much extra does attractor matching filter, and does it introduce any
TRUE_BEST-BASE / TRUE_IMPROVED kill?

The record-time queue purge and the enqueue-time cell refresh act on the
pending queue; their executed-fit effect collapses into the same
same-cell-never-executes set this replay measures.
"""
from __future__ import annotations

import json
import os
from collections import Counter

from replay_final_scheme import (
    CLASS_CAPS,
    EPS_TWIN,
    ROOT,
    comp_cell,
    inv_key,
    skeleton_class,
)


def load_fits(gdir):
    import networkx as nx

    f = os.path.join(gdir, "beam_state", "graph.json")
    raw = json.load(open(f))
    g = raw["graph"]
    G = nx.node_link_graph(raw, directed=True, multigraph=False)
    pending = g.get("pending", {})
    meta_f1 = (g.get("meta") or {}).get("f1") or {}
    best = g.get("best_state")
    best_base = meta_f1.get("parent") if meta_f1.get("state") == best else best

    fits = []
    for lbl, a in sorted(G.nodes(data=True),
                          key=lambda kv: (kv[1].get("global_iter_id", 0), kv[0])):
        it = a.get("global_iter_id", 0)
        if it == 0:
            continue
        aid = a.get("action_in") or ""
        rec = pending.get(aid) or {}
        f1_res = bool((rec.get("code_flags") or {}).get("f1_reserved"))
        prims = rec.get("primitives") or []
        has_f1 = any(p.get("f1") or (p.get("add") or {}).get("f1")
                     for p in prims if isinstance(p, dict))
        m = a.get("metrics") or {}
        conv = ((m.get("convergence") or {}).get("flag")) or 'ok'
        inv_in = a.get("input_inventory") or []
        inv_out = a.get("inventory") or []
        fits.append({
            "label": lbl, "it": it, "f1_res": f1_res, "has_f1": has_f1,
            "conv": conv, "bic": m.get("bic_eff"),
            "verdict": (a.get("verdict") or {}).get("verdict"),
            "inv_in": inv_in, "inv_out": inv_out,
            "names": [c.get("name", "?") for c in inv_in],
            "flags": {k: v for k, v in (rec.get("code_flags") or {}).items()
                      if v and k != "f1_reserved"},
            "best_base": (lbl == best_base),
        })
    rb = None
    for ft in fits:
        # 2026-10-21 sub-converged retirement: every executed round is valid
        elig = (ft["verdict"] == "PASS" and ft["bic"] is not None)
        ft["improved"] = bool(elig and (rb is None or ft["bic"] < rb - 1e-9))
        if elig and (rb is None or ft["bic"] < rb - 1e-9):
            rb = ft["bic"]
    return fits


def _cells(inv, has_f1):
    k = inv_key(inv) + (("F1",) if has_f1 else ())
    cells = {}
    for c in inv:
        nm = (c.get("name") or "?").lower()
        cells.setdefault(nm, comp_cell(c))
    return k, cells


def _dup_kind(inv, has_f1, visited):
    k, cells = _cells(inv, has_f1)
    for kind in ("input", "attractor"):
        vis = visited[kind].get(k)
        if vis is None:
            continue
        if all(cell in vis.get(nm, set()) for nm, cell in cells.items()):
            return kind
    return None


def _add_visit(inv, has_f1, visited, kind):
    k, cells = _cells(inv, has_f1)
    vis = visited[kind].setdefault(k, {})
    for nm, cell in cells.items():
        vis.setdefault(nm, set()).add(cell)


def replay_dual(fits):
    visited = {"input": {}, "attractor": {}}
    cls_counts = Counter()
    filt = []
    for ft in fits:
        if ft["f1_res"]:
            continue                       # reserved F1 exempt
        kind = _dup_kind(ft["inv_in"], ft["has_f1"], visited)
        _cell_exempt = any(k.startswith("floor_") for k in ft["flags"])
        if kind and not _cell_exempt:
            filt.append((ft, f"cell-{kind}"))
            continue
        cls = skeleton_class(ft["names"])
        if cls_counts[cls] >= CLASS_CAPS[cls]:
            filt.append((ft, f"cap-{cls}"))
            continue
        _add_visit(ft["inv_in"], ft["has_f1"], visited, "input")
        if ft["inv_out"]:
            _add_visit(ft["inv_out"], ft["has_f1"], visited, "attractor")
        cls_counts[cls] += 1
    return filt


def outcome(ft):
    if ft["best_base"]:
        return "BEST-BASE"
    if ft["improved"]:
        return "IMPROVED"
    return "WORSE"


def main():
    gal = sorted(d for d in os.listdir(ROOT)
                 if os.path.isdir(os.path.join(ROOT, d))
                 and os.path.exists(os.path.join(ROOT, d,
                                                 "beam_state", "graph.json")))
    total = 0
    stats = Counter()
    cases = []
    filt_reasons = Counter()
    for d in gal:
        fits = load_fits(os.path.join(ROOT, d))
        total += len(fits)
        filt = replay_dual(fits)
        filt_labels = {ft["label"] for ft, _ in filt}
        kept = [ft for ft in fits if ft["label"] not in filt_labels]
        for ft, why in filt:
            filt_reasons[why] += 1
            stats["filtered"] += 1
            twin = any(k["bic"] is not None and ft["bic"] is not None
                       and abs(k["bic"] - ft["bic"]) < EPS_TWIN for k in kept)
            if twin:
                stats["twin_harmless"] += 1
                continue
            o = outcome(ft)
            if o == "WORSE":
                stats["saved_worse"] += 1
            else:
                stats[f"TRUE_{o}"] += 1
                cases.append((d, ft["label"], why, o))

    print(f"galaxies: {len(gal)} | executed fits: {total}")
    print("filter reasons:", dict(filt_reasons))
    print(f"{'filtered':>9s} {'twin-harmless':>14s} {'saved':>6s} "
          f"{'TRUE best-kill':>15s} {'TRUE improv-kill':>17s}")
    print(f"{stats['filtered']:9d} {stats['twin_harmless']:14d} "
          f"{stats['saved_worse']:6d} {stats['TRUE_BEST-BASE']:15d} "
          f"{stats['TRUE_IMPROVED']:17d}")
    for c in cases:
        print("   TRUE-DAMAGE:", c)


if __name__ == "__main__":
    main()
