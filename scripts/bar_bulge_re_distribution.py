#!/usr/bin/env python3
"""Distribution of fitted component Re over the KILOGAS best (locked) rounds.

For every galaxy directory under <root> (default: the 138-source final set):

  <galaxy>/.best_round.json              -> round_label = archive id of the locked round
  <galaxy>/archives/<label>/             -> round_status.json -> outputs.output_param
                                            (absolute paths are stale /home/huhaowen/...;
                                             resolve by basename inside the archive dir)
  <galaxy>/archives/<label>/*_summary.md -> component NAMES, from the embedded input
                                            feedme headers (three legacy formats)
  <galaxy>/archives/<label>/galfit.NN    -> FITTED values (the output param file;
                                            the summary's embedded feedme carries only
                                            INITIAL seeds — never read values from there)

Name header formats handled:
  # STRUCTURE: BULGE                       (new format)
  # Component 1: Bulge (Sersic) -- note    (legacy A)
  # Component number: 1  (SINGLE SERSIC — edge-on main body; +F1 R3a)   (legacy B)

Re extraction: sersic -> row 4 as-is; expdisk -> 1.678 * row 4 (Rs -> Re);
psf -> no size (point source, reported as 0 with a flag).

Usage:
  python3 scripts/bar_bulge_re_distribution.py [root_dir] [--comps bar bulge] [--csv out.csv]
"""

import argparse
import csv
import glob
import json
import os
import re
import sys

EXPDISK_RE_FACTOR = 1.678  # Rs -> Re

_HEADER = re.compile(r"^# Component (?:number:\s*\d+|\d+:)\s*(.*)$", re.M)
_ROW = re.compile(r"^\s*(\d+)\)\s*(-?[\d.]+(?:[eE][-+]?\d+)?)", re.M)
_COMP_START = re.compile(r"^\s*0\)\s*(\S+)", re.M)
_ANN = re.compile(r"^#\s*(?:STRUCTURE:\s*|Component\s+\d+:\s*)(.+?)\s*$", re.M)


def name_head(name: str) -> str:
    """Leading semantic token of a component name: '# STRUCTURE: BULGE (n free)'
    -> 'bulge'; 'BAR (Sersic n=0.5, ... constrained to Bulge)' -> 'bar'."""
    s = name.strip().lstrip("#").strip()
    s = re.sub(r"^STRUCTURE:\s*", "", s, flags=re.I)
    m = re.match(r"([A-Za-z]+)", s)
    return (m.group(1) if m else s).lower()


def parse_named_components(summary_text: str) -> list[dict]:
    """Ordered [{'name': str, 'type': str}, ...] from the summary's embedded
    input feedme. Component blocks are located by their '0) type' rows (headers
    are unreliable: annotated + bare header lines may be paired (KILOGAS_173),
    some components carry no header at all (sky blocks, KILOGAS_114/268)); the
    name is the closest preceding annotation ('# STRUCTURE:' marker or
    '# Component N: <name>' legacy header), falling back to the bare type.
    """
    m = re.search(r"## Init\. par\. file Content(.*?)(?=\n## )", summary_text, re.S)
    sec = m.group(1) if m else summary_text
    starts = list(_COMP_START.finditer(sec))
    anns = [(a.start(), a.group(1).strip()) for a in _ANN.finditer(sec)]
    comps = []
    for i, st in enumerate(starts):
        seg_start = starts[i - 1].end() if i else 0
        prev = [t for p, t in anns if seg_start <= p < st.start()]
        ctype = st.group(1).lower()
        comps.append({"name": prev[-1] if prev else ctype, "type": ctype})
    return comps


def parse_fitted(output_text: str) -> list[dict]:
    """Fitted components from the galfit.NN output param file.

    Returns [{'idx': 1, 'type': 'sersic', 'rows': {'1': x, '3': mag, '4': re, ...}}, ...]
    """
    blocks = re.split(r"^# Component number:\s*\d+\s*$", output_text, flags=re.M)
    comps = []
    for blk in blocks[1:]:
        m = re.search(r"^\s*0\)\s*(\S+)", blk, re.M)
        ctype = m.group(1).lower() if m else "?"
        rows = {num: float(val) for num, val in _ROW.findall(blk)}
        comps.append({"type": ctype, "rows": rows})
    return comps


def re_px(comp: dict) -> tuple[float | None, str]:
    """(Re in px, note) for one fitted component."""
    r4 = comp["rows"].get("4")
    if comp["type"] == "psf":
        return 0.0, "psf point source (no Re)"
    if comp["type"] == "expdisk":
        return (EXPDISK_RE_FACTOR * r4 if r4 is not None else None), "expdisk Rs*1.678"
    if r4 is None:
        return None, f"no row-4 in {comp['type']}"
    return r4, comp["type"]


def match_comp(name: str, key: str) -> bool:
    head = name_head(name)
    aliases = {"bar": {"bar", "棒"}, "bulge": {"bulge", "核球"}}.get(key.lower(), {key.lower()})
    return head in aliases


def stats_line(vals: list[float]) -> str:
    s = sorted(vals)
    n = len(s)

    def pct(p):
        k = max(0, min(n - 1, int(round(p / 100 * (n - 1)))))
        return s[k]

    return (f"n={n}  min={s[0]:.2f}  p25={pct(25):.2f}  median={pct(50):.2f}  "
            f"p75={pct(75):.2f}  p90={pct(90):.2f}  max={s[-1]:.2f}  "
            f"mean={sum(s)/n:.2f}")


def ascii_hist(vals: list[float], title: str, width: int = 44) -> None:
    s = sorted(vals)
    lo, hi = s[0], s[-1]
    raw = (hi - lo) / 12 or 1.0
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = min(m for m in (1, 2, 5, 10) if m * mag >= raw) * mag
    bins = {}
    for v in s:
        bins.setdefault(int(v // step), []).append(v)
    kmax = max(bins)
    print(f"\n{title} (bin width {step:g} px)")
    for k in range(0, kmax + 1):
        c = len(bins.get(k, []))
        bar = "#" * max(c, 1) if c else ""
        print(f"  [{k*step:>6g}, {(k+1)*step:>6g})  {c:>3}  {bar[:width]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=os.path.join(
        os.path.dirname(__file__), "..", "galfits", "kilogas_138_sources_final"),
        help="directory containing the KILOGAS_* galaxy dirs")
    ap.add_argument("--comps", nargs="+", default=["bar", "bulge"],
                    help="component name keys to extract (default: bar bulge)")
    ap.add_argument("--csv", default="", help="optional CSV export path")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    rows_out, problems = [], []
    galaxies = sorted(d for d in glob.glob(os.path.join(root, "KILOGAS_*"))
                      if os.path.isdir(d))
    for gdir in galaxies:
        gname = os.path.basename(gdir)
        try:
            label = json.load(open(os.path.join(gdir, ".best_round.json")))["round_label"]
            ad = os.path.join(gdir, "archives", label)
            rs = json.load(open(os.path.join(ad, "round_status.json")))
            op_name = os.path.basename((rs.get("outputs") or {}).get("output_param") or "")
            op_path = os.path.join(ad, op_name)
            sums = glob.glob(os.path.join(ad, "*_summary.md"))
            if not (op_name and os.path.exists(op_path)) or len(sums) != 1:
                raise FileNotFoundError(f"archive artifacts incomplete ({label})")
            named = parse_named_components(
                open(sums[0], encoding="utf-8", errors="replace").read())
            fitted = parse_fitted(open(op_path, encoding="utf-8", errors="replace").read())
            if not named or len(named) != len(fitted):
                raise ValueError(f"name/value pairing failed: {len(named)} names vs "
                                 f"{len(fitted)} components")
            rec = {"galaxy": gname, "round": label,
                   "chi2nu": json.load(open(os.path.join(gdir, ".best_round.json")))
                   .get("reduced_chisq")}
            for key in args.comps:
                hits = [(n["name"], f) for n, f in zip(named, fitted)
                        if match_comp(n["name"], key)]
                if not hits:
                    rec[key] = None
                    continue
                if len(hits) > 1:
                    problems.append(f"{gname}: {len(hits)} components match '{key}', "
                                    f"using the first: {[h[0] for h in hits]}")
                name, comp = hits[0]
                re_val, note = re_px(comp)
                rec[key] = re_val
                rec[key + "_note"] = note
                rec[key + "_n"] = comp["rows"].get("5")  # sersic n (context)
            rows_out.append(rec)
        except Exception as e:
            problems.append(f"{gname}: {e}")

    # ---- per-galaxy table
    hdr = f"{'galaxy':<14}" + "".join(
        f"{k+' Re(px)':>14}{k+' n':>9}" for k in args.comps) + f"{'chi2/nu':>10}"
    print(hdr)
    print("-" * len(hdr))
    for rec in rows_out:
        line = f"{rec['galaxy']:<14}"
        for k in args.comps:
            v = rec.get(k)
            n_val = rec.get(k + "_n")
            re_str = f"{v:.2f}" if isinstance(v, float) else "--"
            n_str = f"{n_val:.2f}" if isinstance(n_val, float) else "--"
            line += f"{re_str:>14}{n_str:>9}"
        chi_str = f"{rec['chi2nu']:.3f}" if rec.get("chi2nu") else "--"
        line += f"{chi_str:>10}"
        print(line)

    # ---- distributions
    for k in args.comps:
        vals = [r[k] for r in rows_out if isinstance(r.get(k), float)]
        n_with = sum(1 for r in rows_out if r.get(k) is not None)
        print(f"\n===== {k}: present in {n_with}/{len(rows_out)} galaxies =====")
        if vals:
            print(stats_line(vals))
            ascii_hist(vals, f"{k} Re distribution")

    if args.csv:
        cols = ["galaxy", "round", "chi2nu"]
        for k in args.comps:
            cols += [k, k + "_n", k + "_note"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows_out)
        print(f"\nCSV written: {args.csv}")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(" ", p)
        sys.exit(1)


if __name__ == "__main__":
    main()
