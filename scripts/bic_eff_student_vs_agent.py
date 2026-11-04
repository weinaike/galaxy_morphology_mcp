#!/usr/bin/env python3
"""Rough BIC_eff comparison: astronomy-student best fits (kilogas_138_sources_final)
vs agent best fits (kilogas_138_sources_simple-2).

BIC_eff = chi2/A_psf + k*ln(N/A_psf), N = ndof + nfree  (extract_summary_galfit:594).

- student side: .best_round.json -> archives/<label>/round_status.json
  fit_statistics (chi2/ndof/nfree); fallback parses the summary md
  ("Chi^2 = ..., ndof = ..." + "| N_free ... |" table row) with the same
  regexes our extractor uses.
- A_psf: borrowed 1:1 from OUR run of the same galaxy (same 138 sources,
  same psf_r.fits — galaxy-invariant; the student dirs carry no PSF files).
- self-validation first: recompute OUR stored bic_eff from our own
  chi2/ndof/nfree/a_psf and compare with the archived value per galaxy.

NOT a rigorous benchmark: a few galaxies differ in image/mask between the
two datasets; the user accepts the noise for an overall-level comparison.

Usage: python scripts/bic_eff_student_vs_agent.py \
          [--ours galfits/kilogas_138_sources_simple-2] \
          [--theirs galfits/kilogas_138_sources_final] [--csv out.csv]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

_CHI2_RE = re.compile(r'Chi\^2\s*=\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)')
_NDOF_RE = re.compile(r'ndof\s*=\s*(\d+)')
_NFREE_RE = re.compile(r'N_free \(free parameters\)\s*\|\s*(\d+)')
# the students' report skeleton ends with a JSON block naming the best
# round — fenced (```json ... ```) or BARE (47/138 reports carry it
# unfenced on the last line). The report is the AUTHORITATIVE lock: on
# KILOGAS_184 .best_round.json points at a worse round (chi2 1984295)
# than the report's best_turn (chi2 1966002) — the json went stale.
_BEST_TURN_RE = re.compile(
    r'"best_turn"\s*:\s*"([^"]+)"')


def _best_turn_from_report(gdir: str) -> str | None:
    for md in glob.glob(os.path.join(gdir, "analysis_report_*.md")):
        try:
            text = open(md, encoding="utf-8").read()
        except OSError:
            continue
        m = _BEST_TURN_RE.search(text)
        if m:
            return m.group(1)
    return None


def _stats_from_round_status(rs_path: str) -> dict | None:
    try:
        fs = (json.load(open(rs_path)) or {}).get("fit_statistics") or {}
    except (OSError, json.JSONDecodeError):
        return None
    if fs.get("chi2") is not None and fs.get("ndof") and fs.get("nfree"):
        return {"chi2": float(fs["chi2"]), "ndof": int(fs["ndof"]),
                "nfree": int(fs["nfree"])}
    return None


def _stats_from_summary_md(arch_dir: str) -> dict | None:
    """Fallback: parse the summary md with our extractor's regexes."""
    for md in glob.glob(os.path.join(arch_dir, "*_summary.md")):
        try:
            text = open(md, encoding="utf-8").read()
        except OSError:
            continue
        mc, mn = _CHI2_RE.search(text), _NDOF_RE.search(text)
        mf = _NFREE_RE.search(text)
        if mc and mn and mf:
            return {"chi2": float(mc.group(1)), "ndof": int(mn.group(1)),
                    "nfree": int(mf.group(1))}
    return None


def _bic_eff(chi2: float, ndof: int, nfree: int, a_psf: float) -> float:
    n_data = ndof + nfree
    return chi2 / a_psf + nfree * math.log(n_data / a_psf)


def _ours(gdir: str) -> dict | None:
    """Our best round: metrics (a_psf/bic_eff) + archive stats for
    self-validation. archive_dir in the graph is a stale absolute path —
    remap by basename into our dataset root."""
    gp = os.path.join(gdir, "beam_state", "graph.json")
    if not os.path.exists(gp):
        return None
    g = json.load(open(gp))
    best = (g.get("graph") or {}).get("best_state")
    if not best:
        return None
    node = next((n for n in g.get("nodes", []) if n.get("label") == best), None)
    if node is None:
        return None
    metrics = node.get("metrics") or {}
    arch = node.get("archive_dir") or ""
    arch_local = os.path.join(gdir, "archives", os.path.basename(arch)) \
        if arch else ""
    stats = _stats_from_round_status(os.path.join(arch_local,
                                                  "round_status.json")) \
        if arch_local else None
    if stats is None and arch_local:
        stats = _stats_from_summary_md(arch_local)
    a_psf = metrics.get("a_psf")
    if a_psf is None and arch_local:
        # 6 historical graphs carry a_psf only in the archived
        # round_statistics (node metrics predate the a_psf field) — recover
        # it from the same archive the self-validation reads
        try:
            fs = (json.load(open(os.path.join(arch_local,
                                              "round_status.json")))
                  or {}).get("fit_statistics") or {}
            a_psf = fs.get("a_psf")
        except (OSError, json.JSONDecodeError):
            a_psf = None
    if a_psf is None:
        return None
    return {"a_psf": float(a_psf), "bic_eff_stored": metrics.get("bic_eff"),
            "stats": stats, "best": best}


def _theirs(gdir: str) -> dict | None:
    # AUTHORITATIVE: the analysis report's best_turn (the report is the
    # locked conclusion; .best_round.json can go stale — KILOGAS_184).
    label = _best_turn_from_report(gdir)
    if label is None:
        br = os.path.join(gdir, ".best_round.json")
        if not os.path.exists(br):
            return None
        label = (json.load(open(br)) or {}).get("round_label")
    if not label:
        return None
    arch = os.path.join(gdir, "archives", label)
    stats = _stats_from_round_status(os.path.join(arch, "round_status.json"))
    if stats is None:
        stats = _stats_from_summary_md(arch)
    if stats is None:
        return None
    return {"stats": stats, "label": label}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", default="galfits/kilogas_138_sources_simple-2")
    ap.add_argument("--theirs", default="galfits/kilogas_138_sources_final")
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    galaxies = sorted(os.path.basename(d) for d in
                      glob.glob(os.path.join(args.ours, "KILOGAS_*"))
                      if os.path.isdir(d))
    rows, self_bad = [], []
    n_missing_ours = n_missing_theirs = 0
    for gname in galaxies:
        ours = _ours(os.path.join(args.ours, gname))
        theirs = _theirs(os.path.join(args.theirs, gname))
        if ours is None:
            n_missing_ours += 1
            continue
        if theirs is None:
            n_missing_theirs += 1
            continue
        # self-validation (recompute OUR stored bic_eff)
        if ours["stats"] and ours["bic_eff_stored"] is not None:
            s = ours["stats"]
            rec = _bic_eff(s["chi2"], s["ndof"], s["nfree"], ours["a_psf"])
            if abs(rec - ours["bic_eff_stored"]) > 1e-6 * max(1.0, abs(rec)):
                self_bad.append((gname, rec, ours["bic_eff_stored"]))
        t = theirs["stats"]
        theirs_bic = _bic_eff(t["chi2"], t["ndof"], t["nfree"], ours["a_psf"])
        ours_bic = ours["bic_eff_stored"]
        if ours_bic is None and ours["stats"]:
            o = ours["stats"]
            ours_bic = _bic_eff(o["chi2"], o["ndof"], o["nfree"], ours["a_psf"])
        if ours_bic is None:
            n_missing_ours += 1
            continue
        rows.append({"galaxy": gname, "ours_best": ours["best"],
                     "ours_bic_eff": ours_bic,
                     "theirs_bic_eff": theirs_bic,
                     "delta": theirs_bic - ours_bic,   # <0 student better
                     "theirs_k": t["nfree"], "theirs_N": t["ndof"] + t["nfree"],
                     "a_psf": ours["a_psf"]})

    if self_bad:
        print(f"!! self-validation FAILED on {len(self_bad)} galaxies "
              "(recomputed != stored) — results unreliable:")
        for g, rec, sto in self_bad[:5]:
            print(f"   {g}: recomputed {rec:.4f} vs stored {sto:.4f}")
        return 1
    print(f"self-validation OK on all {len(rows)} galaxies "
          "(recomputed == stored BIC_eff)\n")

    deltas = sorted(r["delta"] for r in rows)
    n = len(deltas)
    student_wins = sum(1 for d in deltas if d < -10)
    agent_wins = sum(1 for d in deltas if d > 10)
    ties = n - student_wins - agent_wins

    def q(p):
        return deltas[min(n - 1, int(p * n))]

    print(f"galaxies compared: {n} "
          f"(skipped: {n_missing_ours} ours missing, "
          f"{n_missing_theirs} theirs missing)")
    print(f"student better (ΔBIC_eff < -10): {student_wins}")
    print(f"agent   better (ΔBIC_eff > +10): {agent_wins}")
    print(f"ties (|Δ| <= 10):                {ties}")
    print(f"\nΔBIC_eff (student - ours; negative = student better):")
    print(f"  min {deltas[0]:+.1f} | p25 {q(.25):+.1f} | median "
          f"{q(.5):+.1f} | p75 {q(.75):+.1f} | max {deltas[-1]:+.1f}")
    print(f"  mean {sum(deltas) / n:+.1f}")

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nper-galaxy table written: {args.csv}")

    print("\nbest-for-student (top 5) / best-for-agent (top 5):")
    byd = sorted(rows, key=lambda r: r["delta"])
    for r in byd[:5]:
        print(f"  {r['galaxy']:>14s}: Δ {r['delta']:+10.1f} "
              f"(student {r['theirs_bic_eff']:.1f} vs ours {r['ours_bic_eff']:.1f})")
    for r in byd[-5:]:
        print(f"  {r['galaxy']:>14s}: Δ {r['delta']:+10.1f} "
              f"(student {r['theirs_bic_eff']:.1f} vs ours {r['ours_bic_eff']:.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
