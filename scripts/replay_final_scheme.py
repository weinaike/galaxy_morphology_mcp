#!/usr/bin/env python3
"""Shadow replay of the FINALIZED dedup scheme over the 138-galaxy set
(galfits/kilogas_138_sources_simple-2). Validates before implementation.

Scheme (user-frozen 2026-10-17):
  * PA bins 4x45deg: [-90,-45],[-45,0],[0,45],[45,90] (PA normalized mod 180)
  * Skeleton-class caps a/b/c = 2/4/5 (total N_max = 7 unchanged);
    per-combo cap RETIRED (cells take over same-inventory dedup)
  * Cell filter: per-component JOINT bin-tuple (bulge: Re,q,PA,n; bar:
    Re,q,PA; disk: Re,PA; singlesersic treated as bulge-family; agn/psf
    presence-only). Visited sets are scoped per EXACT inventory and
    accumulated from every EXECUTED fit (incl. sub-converged). A candidate
    is a DUP iff EVERY component's cell is already visited within its
    inventory; ONE new component cell anywhere = meaningful.
  * Reserved F1 rounds exempt from everything; filtered candidates do not
    execute, do not add cells, do not consume class quota; sub-converged
    kept fits DO add cells but do NOT count toward class caps.
  * Twin discounting in damage accounting (kept fit with |dBIC| < 1.0).
"""
from __future__ import annotations

import bisect
import json
import os
from collections import Counter

ROOT = "/home/jiangbo/galaxy_morphology_mcp/galfits/kilogas_138_sources_simple-2"

# ---------------------------------------------------------------- bins
PA_B = [-90.0, -45.0, 0.0, 45.0]          # 4 bins x 45deg
RE_BULGE = [0.5, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
RE_BAR = [1.0, 4.0, 8.0, 16.0, 32.0, 64.0]
RE_DISK = [1.0, 4.0, 8.0, 16.0, 32.0, 64.0]
Q_BULGE = [0.25, 0.5, 0.75]
Q_BAR = [0.1, 0.2, 0.4]
N_B = [2.0, 4.0]

CLASS_CAPS = {"a": 2, "b": 4, "c": 5}
EPS_TWIN = 1.0


def _bin(v, bounds):
    if v is None:
        return "x"
    return bisect.bisect_right(bounds, v)


def _pa_norm(pa):
    if pa is None:
        return None
    return ((pa + 90.0) % 180.0) - 90.0


def _re_eff(c):
    re = c.get("re_effective", c.get("re"))
    if re is not None and (c.get("type") or "").lower() in ("expdisk", "edgedisk"):
        re = float(re) * 1.68
    return re


def comp_cell(c):
    """The JOINT bin-tuple of ONE component (the per-component cell)."""
    name = (c.get("name") or "?").lower()
    t = (c.get("type") or "").lower()
    re = _re_eff(c)
    q = c.get("q", c.get("ba"))
    pa = _pa_norm(c.get("pa"))
    if name == "disk" or t == "expdisk":
        return ("disk", _bin(re, RE_DISK), _bin(pa, PA_B))
    if name == "bar":
        return ("bar", _bin(re, RE_BAR), _bin(q, Q_BAR), _bin(pa, PA_B))
    if name in ("bulge", "singlesersic", "sersic", "sersic2"):
        return (name, _bin(re, RE_BULGE), _bin(q, Q_BULGE),
                _bin(pa, PA_B), _bin(c.get("n"), N_B))
    return (name or t or "?",)          # agn / psf / unknown: presence


def inv_key(inv):
    return tuple(sorted((c.get("name") or "?").lower() for c in inv))


def skeleton_class(names):
    s = set(names)
    if "singlesersic" in s:
        return "a"
    if "bar" in s:
        return "c"
    return "b"


# ---------------------------------------------------------------- loading
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
        conv = ((m.get("convergence") or {}).get("flag")) or "ok"
        inv_in = a.get("input_inventory") or []
        fits.append({
            "label": lbl, "it": it, "f1_res": f1_res, "has_f1": has_f1,
            "conv": conv, "bic": m.get("bic_eff"),
            "verdict": (a.get("verdict") or {}).get("verdict"),
            "inv_in": inv_in, "names": [c.get("name", "?") for c in inv_in],
            "best_base": (lbl == best_base),
        })
    rb = None
    for ft in fits:
        elig = (ft["verdict"] == "PASS" and ft["conv"] != "sub-converged"
                and ft["bic"] is not None)
        ft["improved"] = bool(elig and (rb is None or ft["bic"] < rb - 1e-9))
        if elig and (rb is None or ft["bic"] < rb - 1e-9):
            rb = ft["bic"]
    return fits


def cells_of(ft):
    """(inventory-key, {comp_name: joint-cell}) — F1 flag folded into the
    inventory key so F1-bearing and plain variants never collide."""
    k = inv_key(ft["inv_in"]) + (("F1",) if ft["has_f1" ] else ())
    cells = {}
    for c in ft["inv_in"]:
        nm = (c.get("name") or "?").lower()
        cells.setdefault(nm, comp_cell(c))
    return k, cells


def is_dup(ft, visited):
    k, cells = cells_of(ft)
    vis = visited.get(k)
    if vis is None:
        return False                       # inventory never tried: meaningful
    for nm, cell in cells.items():
        if cell not in vis.get(nm, set()):
            return False                   # >=1 new component cell: meaningful
    return True                            # every component cell visited


def add_visit(ft, visited):
    k, cells = cells_of(ft)
    vis = visited.setdefault(k, {})
    for nm, cell in cells.items():
        vis.setdefault(nm, set()).add(cell)


# ---------------------------------------------------------------- replay
def replay(fits):
    visited = {}
    cls_counts = Counter()
    filt = []
    for ft in fits:
        if ft["f1_res"]:
            continue                       # reserved F1 exempt
        if is_dup(ft, visited):
            filt.append((ft, "cell"))
            continue
        cls = skeleton_class(ft["names"])
        if ft["conv"] != "sub-converged" and cls_counts[cls] >= CLASS_CAPS[cls]:
            filt.append((ft, f"cap-{cls}"))
            continue
        add_visit(ft, visited)             # executes (incl sub-conv)
        if ft["conv"] != "sub-converged":
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
        filt = replay(fits)
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
