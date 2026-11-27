"""Round-2 replay analysis: onion-vs-disk_q cross-tab, bulge_q<0.4 lock damage
detail, and the recalibrated mu0_bar(0.9)/lens-window(35%) fire counts."""

from __future__ import annotations

import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from replay_physicality_checks import (  # noqa: E402
    MAIN, check_flux, check_mu0, check_n, check_onion, check_q_priors,
    cons_caps, locked_turn, _eff_re,
)
from beam.cons_decode import number_components  # noqa: E402

ROOT = "/home/jiangbo/experiments/dual_agents_w5"


def main() -> None:
    onion_fire_q: list[float] = []      # disk_q of rounds where onion FIRED
    onion_pass_q: list[float] = []      # disk_q of rounds where onion ran & passed
    bulge_lock_hits = []                # bulge_q<0.4 on locked rounds, with detail
    mu0bar_hard = 0
    mu0bar_locks = []
    lens_win_notes = 0

    for gdir in sorted(glob.glob(os.path.join(ROOT, "*"))):
        if not os.path.isdir(os.path.join(gdir, "archives")):
            continue
        lock = locked_turn(gdir)
        for arch in sorted(glob.glob(os.path.join(gdir, "archives", "*"))):
            rs_f = os.path.join(arch, "round_status.json")
            if not os.path.isfile(rs_f):
                continue
            try:
                fs = (json.load(open(rs_f)).get("fit_statistics") or {})
            except Exception:
                continue
            if not fs.get("bic_eff"):
                continue
            names = os.listdir(arch)
            fitted_l = sorted(f for f in names if re.fullmatch(r"galfit\.\d+", f))
            input_l = sorted(f for f in names
                             if re.fullmatch(r"(_?iter\d+\.feedme|[^_]*\.feedme)", f)
                             and not f.startswith("galfit"))
            cons_l = sorted(f for f in names if f.endswith(".cons"))
            if not fitted_l or not input_l:
                continue
            fitted = number_components(os.path.join(arch, fitted_l[-1]),
                                       name_file=os.path.join(arch, input_l[0]))
            inputs = number_components(os.path.join(arch, input_l[0]))
            if not fitted:
                continue
            by_name = {}
            for num, c in fitted.items():
                c["number"] = num
                n = str(c["name"]).lower()
                if n in MAIN or n == "agn":
                    by_name[n] = c
            caps = cons_caps(os.path.join(arch, cons_l[0]) if cons_l else "", inputs)

            disk = by_name.get("disk")
            disk_q = (disk or {}).get("ba")
            oh = check_onion(by_name)
            if disk_q is not None and "edgedisk" not in by_name:
                (onion_fire_q if oh else onion_pass_q).append(disk_q)

            ev = ([(h, "hard") for h in oh] + check_q_priors(by_name)
                  + check_n(by_name, caps) + check_mu0(by_name, fs.get("a_psf")))
            fl_ev, _ = check_flux(by_name)
            ev += fl_ev
            lens_win_notes += sum(1 for e, s in fl_ev if e.startswith("lens_flux") and "outside" in e)
            for e, s in ev:
                if e.startswith("mu0_bar"):
                    mu0bar_hard += 1
            if lock and os.path.basename(arch) == lock:
                for e, s in ev:
                    if e.startswith("mu0_bar"):
                        mu0bar_locks.append((os.path.basename(gdir), e))
                    if e.startswith("bulge_q") and s == "hard":
                        b = by_name.get("bulge") or {}
                        bulge_lock_hits.append({
                            "galaxy": os.path.basename(gdir),
                            "archive": os.path.basename(arch),
                            "bic": round(fs["bic_eff"], 2),
                            "bulge_q": round(b.get("ba") or -1, 3),
                            "bulge_Re_px": round(_eff_re(b) or -1, 2),
                            "bulge_n": b.get("n"),
                            "bulge_mag": b.get("mag"),
                            "bulge_PA": b.get("pa"),
                        })

    def bins(v, label):
        v = sorted(v)
        if not v:
            print(f"{label}: none"); return
        edges = [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.01]
        for lo, hi in zip(edges, edges[1:]):
            n = sum(1 for x in v if lo <= x < hi)
            print(f"  {label} q_disk in [{lo:.1f},{hi:.1f}): {n}")

    print("== onion FIRED by disk q (current rule skips q<0.4) ==")
    bins(onion_fire_q, "fire")
    print("== onion RAN & PASSED by disk q ==")
    bins(onion_pass_q, "pass")
    skipped = sum(1 for q in onion_pass_q + onion_fire_q if q < 0.4)
    total = len(onion_fire_q) + len(onion_pass_q)
    print(f"rounds with q_disk<0.4 currently skipped: {skipped}/{total}")

    print(f"\n== mu0_bar tol 0.90: hard fires={mu0bar_hard}, lock damage={len(mu0bar_locks)} ==")
    for g, e in mu0bar_locks:
        print(f"  {g}: {e}")

    print(f"\n== lens window 5-35%: note fires={lens_win_notes} ==")

    print(f"\n== bulge_q<0.4 lock damage detail ({len(bulge_lock_hits)}) ==")
    for d in bulge_lock_hits:
        print(f"  {d['galaxy']:32s} q={d['bulge_q']:.2f} Re={d['bulge_Re_px']:6.2f}px "
              f"n={d['bulge_n']} mag={d['bulge_mag']} PA={d['bulge_PA']} "
              f"BIC={d['bic']} archives/{d['archive']}/")


if __name__ == "__main__":
    main()
