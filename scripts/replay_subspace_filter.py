#!/usr/bin/env python3
"""Shadow replay of the two proposed dedup filters over the 138-galaxy set
(galfits/kilogas_138_sources_simple-2).

Filter A — parameter-subspace cells (user-specified bins):
  the pre-transcribed INPUT of every executed fit is mapped to a cell key;
  a later fit whose key is already visited is considered a same-basin
  duplicate (would have been filtered). Visited set includes sub-converged
  inputs; the reserved F1 round is exempt; F1 presence is a key component.

Filter B — skeleton-class attempt caps (a/b/c = 2/3/4):
  (a) singlesersic regime, (b) disk-bearing no bar, (c) disk-bearing bar.
  Sub-converged rounds don't count; F1 reserved exempt; per-combo cap 2 kept.

Metrics: filtered counts/rates, and for every would-be-filtered fit its real
outcome — BEST-BASE (final best or its pre-F1 base), IMPROVED (updated the
running best), or WORSE. False kills = filtered BEST-BASE/IMPROVED; saved =
filtered WORSE.
"""
from __future__ import annotations

import bisect
import json
import os
import sys
from collections import Counter

ROOT = "/home/jiangbo/galaxy_morphology_mcp/galfits/kilogas_138_sources_simple-2"

# ---------------------------------------------------------------- bins
PA_B = [-90.0, -30.0, 30.0]
RE_BULGE = [0.5, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
RE_BAR = [1.0, 4.0, 8.0, 16.0, 32.0, 64.0]
RE_DISK = [1.0, 4.0, 8.0, 16.0, 32.0, 64.0]
Q_BULGE = [0.25, 0.5, 0.75]
Q_BAR = [0.1, 0.2, 0.4]
N_B = [2.0, 4.0]


def _bin(v, bounds):
    if v is None:
        return "x"
    return bisect.bisect_right(bounds, v)


def _pa_norm(pa):
    if pa is None:
        return None
    return ((pa + 90.0) % 180.0) - 90.0


def comp_key(c):
    """Cell contribution of one input-inventory component (Re in EFFECTIVE px)."""
    name = (c.get("name") or "?").lower()
    t = (c.get("type") or "").lower()
    re = c.get("re_effective", c.get("re"))
    if re is not None and t in ("expdisk", "edgedisk"):
        re = float(re) * 1.68
    q = c.get("q", c.get("ba"))
    pa = _pa_norm(c.get("pa"))
    if name == "disk" or t == "expdisk":
        return ("disk", _bin(re, RE_DISK), _bin(pa, PA_B))
    if name == "bar":
        return ("bar", _bin(re, RE_BAR), _bin(q, Q_BAR), _bin(pa, PA_B))
    if name == "bulge" or name == "singlesersic" or name == "sersic" \
            or name == "sersic2":
        return (name, _bin(re, RE_BULGE), _bin(q, Q_BULGE),
                _bin(pa, PA_B), _bin(c.get("n"), N_B))
    # agn / psf / unknown: presence only
    return (name or t or "?",)


def cell_key(inv, has_f1):
    k = tuple(sorted(comp_key(c) for c in inv))
    return (k, bool(has_f1))


def skeleton_class(inv_names):
    s = set(inv_names)
    if "singlesersic" in s:
        return "a"
    if "bar" in s:
        return "c"
    return "b"


CLASS_CAPS = {"a": 2, "b": 3, "c": 4}


# ---------------------------------------------------------------- replay
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
    # running-best + outcome labels (mirror the graph's best-update rule)
    rb = None
    for ft in fits:
        elig = (ft["verdict"] == "PASS" and ft["conv"] != "sub-converged"
                and ft["bic"] is not None)
        ft["improved"] = bool(elig and (rb is None or ft["bic"] < rb - 1e-9))
        if elig and (rb is None or ft["bic"] < rb - 1e-9):
            rb = ft["bic"]
    return fits


def replay(fits, use_cells, use_caps):
    """Returns (filtered_labels, n_executed_after). Shadow replay in order."""
    visited, cls_counts = set(), Counter()
    filt = []
    for ft in fits:
        if ft["f1_res"]:
            continue  # exempt: reserved F1 always runs
        if use_cells:
            k = cell_key(ft["inv_in"], ft["has_f1"])
            if k in visited:
                filt.append((ft, "cell"))
                continue
        if use_caps and ft["conv"] != "sub-converged":
            cls = skeleton_class(ft["names"])
            if cls_counts[cls] >= CLASS_CAPS[cls]:
                filt.append((ft, f"cap-{cls}"))
                continue
        # executes
        if use_cells:
            visited.add(cell_key(ft["inv_in"], ft["has_f1"]))
        if ft["conv"] != "sub-converged":
            cls_counts[skeleton_class(ft["names"])] += 1
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
                 and os.path.exists(os.path.join(ROOT, d, "beam_state", "graph.json")))
    total = 0
    scen = {name: Counter() for name in ("cells", "caps", "both")}
    best_kills = {name: [] for name in scen}
    imp_kills = {name: [] for name in scen}
    cls_hist = Counter()
    for d in gal:
        fits = load_fits(os.path.join(ROOT, d))
        total += len(fits)
        for ft in fits:
            if ft["conv"] != "sub-converged" and not ft["f1_res"]:
                cls_hist[skeleton_class(ft["names"])] += 1
        for name, (uc, uk) in {"cells": (True, False),
                               "caps": (False, True),
                               "both": (True, True)}.items():
            filt = replay(fits, uc, uk)
            s = scen[name]
            s["filtered"] += len(filt)
            for ft, why in filt:
                o = outcome(ft)
                s[o] += 1
                if o == "BEST-BASE":
                    best_kills[name].append((d, ft["label"], why))
                elif o == "IMPROVED":
                    imp_kills[name].append((d, ft["label"], why))

    print(f"galaxies: {len(gal)} | executed fits (incl F1/sub-conv): {total}")
    print("attempts per class (non-subconv, non-F1):", dict(cls_hist))
    hdr = f"{'scenario':8s} {'filtered':>8s} {'rate':>6s} {'BEST-KILL':>10s} {'IMPROV-KILL':>12s} {'saved(WORSE)':>13s}"
    print(hdr)
    for name in ("cells", "caps", "both"):
        s = scen[name]
        print(f"{name:8s} {s['filtered']:8d} {s['filtered']/total:6.1%} "
              f"{s['BEST-BASE']:10d} {s['IMPROVED']:12d} {s['WORSE']:13d}")
    for name in ("cells", "caps", "both"):
        if best_kills[name]:
            print(f"\n!! {name}: BEST-BASE kills ({len(best_kills[name])}):")
            for d, lbl, why in best_kills[name][:20]:
                print(f"   {d} {lbl} [{why}]")
        if imp_kills[name]:
            imp = imp_kills[name]
            print(f"~  {name}: IMPROVED kills: {len(imp)} "
                  f"(showing first 12: {[(d, l) for d, l, _ in imp[:12]]})")


if __name__ == "__main__":
    main()
