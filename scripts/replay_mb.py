#!/usr/bin/env python3
"""P2 acceptance replay: rebuild beam graphs over the historical kilogas_mb
runs and verify the engine records them faithfully (docs/design_mb_beam.md
P2 acceptance: all old runs enter the graph; the state BIC equals the
gssummary BIC).

Read-only over the dataset: graphs are built under a scratch directory;
the recorded artifacts point back at the original files.

Usage: python scripts/replay_mb.py [--root galfits/kilogas_mb] [--scratch DIR]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from beam.graph import BeamGraph, _metric_key  # noqa: E402
from beam_mb.lyric_doc import parse_lyric_doc  # noqa: E402


def _gss_stats(gssummary: str) -> dict:
    stats: dict = {}
    with open(gssummary, encoding="utf-8") as f:
        text = f.read()
    for key, pat in (("reduced_chisq", r"# reduced chisq:\s*([\d.eE+-]+)"),
                     ("bic", r"# BIC:\s*([\d.eE+-]+)"),
                     ("n_free", r"# free parameters \(k\):\s*(\d+)"),
                     ("n_data", r"# dof \(N - k\):\s*([\d.]+)")):
        m = re.search(pat, text)
        if m:
            stats[key] = float(m.group(1))
    return stats


def replay_galaxy(galaxy_dir: str, scratch_root: str) -> dict:
    from beam.tools import beam_init

    gname = os.path.basename(galaxy_dir.rstrip("/"))
    roots = sorted(glob.glob(os.path.join(galaxy_dir, f"{gname}.lyric")))
    if not roots:
        return {"galaxy": gname, "status": "no root lyric"}

    scratch = os.path.join(scratch_root, gname)
    os.makedirs(scratch, exist_ok=True)
    res = beam_init(galaxy_dir=scratch, root_feedme=roots[0],
                    stage1_morphology="(P2 replay)", mode="galfits")
    if res.get("status") != "success":
        return {"galaxy": gname, "status": "init failed",
                "error": res.get("error", "")[:200]}
    graph = BeamGraph.load(scratch)
    bands = graph.g.graph["meta"].get("bands", {})

    out_dirs = sorted(d for d in
                      glob.glob(os.path.join(galaxy_dir, "output", "*__*"))
                      if os.path.isdir(d))
    n_fit = n_skip = bic_ok = bic_bad = 0
    invariant_hits: list[str] = []
    mech_hard: dict[str, int] = {}
    labels: list[str] = []
    for d in out_dirs:
        lyrics = glob.glob(os.path.join(d, "_iter*.lyric"))
        gsss = glob.glob(os.path.join(d, "*.gssummary"))
        if not lyrics or not gsss:
            n_skip += 1
            continue
        lyric, gss = lyrics[0], gsss[0]
        try:
            errs = parse_lyric_doc(lyric).strict_invariant_errors()
            invariant_hits.extend(f"{os.path.basename(d)}: {e}" for e in errs)
        except Exception as e:  # malformed historical file — skip, count
            invariant_hits.append(f"{os.path.basename(d)}: parse error {e}")
            n_skip += 1
            continue
        label = graph.record_fit({
            "input_param_file": lyric,
            "output_param_file": gss,
            "image_file": os.path.join(d, "all_bands_comparison.png"),
            "round_status_file": os.path.join(d, "round_status.json"),
            "fit_statistics": _gss_stats(gss),
        })
        labels.append(label)
        n_fit += 1
        node = graph.state(label)
        ref_bic = _gss_stats(gss).get("bic")
        node_bic = node.get("metrics", {}).get("bic")
        if ref_bic is not None and node_bic is not None \
                and abs(node_bic - ref_bic) < 1e-6 * max(1.0, abs(ref_bic)):
            bic_ok += 1
        else:
            bic_bad += 1
        for m in node.get("mech_checks") or []:
            if m.get("severity") == "hard":
                mech_hard[m.get("check", "?")] = \
                    mech_hard.get(m.get("check", "?"), 0) + 1

    best = max(labels, key=lambda l: _metric_key(graph.state(l)
                                                 .get("metrics", {}))
               or float("inf"), default=None)
    best_bic = _metric_key(graph.state(best).get("metrics", {})) if best else None
    return {"galaxy": gname, "status": "ok", "n_outdirs": len(out_dirs),
            "n_fit": n_fit, "n_skip": n_skip, "bic_ok": bic_ok,
            "bic_bad": bic_bad, "bands": {b: round(v.get("fwhm_px") or 0, 2)
                                          for b, v in bands.items()},
            "invariant_hits": invariant_hits, "mech_hard": mech_hard,
            "best": best, "best_bic": best_bic}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="galfits/kilogas_mb")
    ap.add_argument("--scratch",
                    default=os.path.join(tempfile.gettempdir(), "mb_replay"))
    args = ap.parse_args()

    galaxies = sorted(d for d in glob.glob(os.path.join(args.root, "KILOGAS_*"))
                      if os.path.isdir(d))
    print(f"replaying {len(galaxies)} galaxies from {args.root} "
          f"(graphs under {args.scratch})\n")
    tot_fit = tot_bic_ok = 0
    for gdir in galaxies:
        r = replay_galaxy(gdir, args.scratch)
        tot_fit += r.get("n_fit", 0)
        tot_bic_ok += r.get("bic_ok", 0)
        if r["status"] != "ok":
            print(f"{r['galaxy']}: {r['status']} — {r.get('error', '')}")
            continue
        print(f"{r['galaxy']}: fits {r['n_fit']}/{r['n_outdirs']} "
              f"(skip {r['n_skip']}) | BIC match {r['bic_ok']} "
              f"(bad {r['bic_bad']}) | bands {r['bands']} | "
              f"best {r['best']} BIC {r['best_bic']}")
        if r["invariant_hits"]:
            print(f"   invariant notes ({len(r['invariant_hits'])}):")
            for h in r["invariant_hits"][:6]:
                print(f"     - {h[:120]}")
        if r["mech_hard"]:
            print(f"   mech-hard totals: {r['mech_hard']}")
    print(f"\nTOTAL: {tot_fit} fits recorded, {tot_bic_ok} BIC-verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
