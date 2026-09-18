"""Offline replay of the proposed physicality checks (Phase A1).

Read-only: walks every galaxy in an experiment root, parses each archived
round's converged galfit.NN (+ input feedme names + .cons bands), evaluates
the six proposed check families as pure functions and aggregates:

  1. fire rates per check (all rounds vs locked rounds — lock-damage assay)
  2. the known lower-BIC-but-vetoed rounds: does a *physical* check now fire?
  3. threshold distributions (bar/lens flux fractions, mu0 margins, q values)

Usage: python scripts/replay_physicality_checks.py <experiment_root> [--out JSON]
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from beam.cons_decode import decode_cons_file, effective_band, number_components  # noqa: E402

LN10 = math.log(10.0)
TOL = 0.02          # onion containment tolerance (2%)

MAIN = {"disk", "edgedisk", "bulge", "bar", "lens", "outerdisk", "singlesersic"}


# --------------------------------------------------------------- pure checks
def _eff_re(c: dict) -> float | None:
    v = c.get("re")
    if v is None:
        return None
    return v * 1.68 if str(c.get("type", "")).lower() == "expdisk" else v


def _sersic_mu0(mag: float, re: float, n: float) -> float | None:
    """Analytic central surface brightness (relative, zp cancels).

    The k~2n-1/3+4/(405n) approximation is valid for n >= 0.5 only; flatter
    profiles (tiny-n lens-likes) are skipped (None) rather than mis-evaluated.
    """
    if n < 0.5 or re <= 0:
        return None
    k = 2.0 * n - 1.0 / 3.0 + 4.0 / (405.0 * n)
    if k <= 0.05:
        return None
    mu_e = mag + 5.0 * math.log10(re) + 2.5 * math.log10(
        2.0 * math.pi * n * math.exp(k) / k ** (2.0 * n))
    return mu_e - 2.5 * k / LN10


def _expdisk_mu0(mag: float, rs: float) -> float:
    return mag + 5.0 * math.log10(rs) + 2.5 * math.log10(2.0 * math.pi)


def _ellipse_r(theta_deg: float, a: float, b: float, pa_deg: float) -> float:
    """Radius of an ellipse (semi-axes a>=b, major-axis PA) along theta_deg (degrees)."""
    d = math.radians(theta_deg - pa_deg)
    den = math.hypot(b * math.cos(d), a * math.sin(d))
    return a * b / den if den > 0 else math.inf


def check_onion(comps: dict[str, dict], q_iso: float | None = None) -> list[str]:
    """2Re ellipse nesting outerdisk > disk > lens > bar > bulge — mirrors
    beam.physicality: (a) always-on AREA ordering (Re^2*q) for any disk q;
    (b) directional containment, skipped in the flat-disk regime (disk q<0.3,
    edgedisk slot) unless the fitted q contradicts the outer-isophote anchor
    q_iso (|q_disk - q_iso| > 0.15 -> disk_shape flag + directional re-arms).
    """
    hits: list[str] = []
    names = set(comps)
    if "edgedisk" in names:
        return []                                    # edge-on slot: nesting undefined
    disk = comps.get("disk")
    dq = (disk or {}).get("ba")
    run_directional = dq is None or dq >= 0.3
    if not run_directional and q_iso is not None:
        if abs(dq - q_iso) > 0.15:
            hits.append(f"disk_shape:q={dq:.2f}_vs_q_iso={q_iso:.2f}")
            run_directional = True
        # else: flat disk corroborated by the anchor — directional skip stands
    chain = [n for n in ("outerdisk", "disk", "lens", "bar", "bulge") if n in names]
    for outer, inner in zip(chain, chain[1:]):
        co, ci = comps[outer], comps[inner]
        qo, qi = (co.get("ba") or 1.0), (ci.get("ba") or 1.0)
        if inner == "bulge" and qi - qo > 0.25:
            continue            # round bulge in a flat outer: normal config
        ao, bo = _eff_re(co) or 0.0, qo * (_eff_re(co) or 0.0)
        ai, bi = _eff_re(ci) or 0.0, qi * (_eff_re(ci) or 0.0)
        if min(ao, ai) <= 0:
            continue
        area_ratio = (ai * bi) / (ao * bo)
        if area_ratio > 1.0 + TOL:
            hits.append(f"onion_area:{inner}_in_{outer}:ratio={area_ratio:.2f}")
        if run_directional:
            po, pi = co.get("pa") or 0.0, ci.get("pa") or 0.0
            worst = max(
                _ellipse_r(t, ai, bi, pi) / _ellipse_r(t, ao, bo, po)
                for t in range(0, 180, 2))
            if worst > 1.0 + TOL:
                hits.append(f"onion:{inner}_in_{outer}:ratio={worst:.2f}")
    return hits


def check_q_priors(comps: dict[str, dict]) -> list[tuple[str, str]]:
    """(check, severity) for bulge/bar/lens axis-ratio priors + q_bar<q_lens."""
    out: list[tuple[str, str]] = []
    b, bar, lens = comps.get("bulge"), comps.get("bar"), comps.get("lens")
    if b is not None:
        q = b.get("ba")
        if q is not None:
            if q < 0.3:
                out.append((f"bulge_q={q:.2f}<0.3", "hard"))
            elif q < 0.4:
                out.append((f"bulge_q={q:.2f}<0.4", "note"))
    if bar is not None:
        q = bar.get("ba")
        if q is not None:
            if q > 0.6:
                out.append((f"bar_q={q:.2f}>0.6", "hard"))
            elif q > 0.5:
                out.append((f"bar_q={q:.2f}>0.5", "note"))
    if lens is not None:
        q = lens.get("ba")
        if q is not None and q <= 0.5:
            out.append((f"lens_q={q:.2f}<=0.5", "hard"))
    if bar is not None and lens is not None:
        qb, ql = bar.get("ba"), lens.get("ba")
        if qb is not None and ql is not None and qb >= ql:
            out.append((f"q_bar={qb:.2f}>=q_lens={ql:.2f}", "hard"))
    return out


def check_n(comps: dict[str, dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    lens, outer = comps.get("lens"), comps.get("outerdisk")
    if lens is not None:
        n = lens.get("n")
        if n is not None:
            if n >= 0.6:
                out.append((f"lens_n={n:.2f}>=0.6", "hard"))
            elif n >= 0.5:
                out.append((f"lens_n={n:.2f}>=0.5", "note"))
    if outer is not None and str(outer.get("type", "")).lower() == "sersic":
        n = outer.get("n")
        if n is not None and n >= 1.0:
            out.append((f"outerdisk_n={n:.2f}>=1", "hard"))
    return out


def check_mu0(comps: dict[str, dict], a_psf: float | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def mu0(c: dict) -> float | None:
        t = str(c.get("type", "")).lower()
        mag, re = c.get("mag"), _eff_re(c)
        if mag is None:
            return None
        if t == "psf":
            return mag + 2.5 * math.log10(a_psf) if a_psf else None   # proxy
        if t == "expdisk":
            return _expdisk_mu0(mag, c.get("re")) if c.get("re") else None
        n = c.get("n")
        if t == "sersic" and re and n:
            return _sersic_mu0(mag, re, n)
        return None

    m_b, m_d, m_bar = (mu0(comps[k]) if k in comps else None
                       for k in ("bulge", "disk", "bar"))
    m_agn = None
    for c in comps.values():
        if str(c.get("name", "")).lower() == "agn":
            m_agn = mu0(c)
    if m_b is not None and m_d is not None and m_b >= m_d:
        out.append((f"mu0_bulge={m_b:.2f}>=mu0_disk={m_d:.2f}", "hard"))
    if m_agn is not None and m_b is not None and m_agn >= m_b:
        out.append((f"mu0_agn={m_agn:.2f}>=mu0_bulge={m_b:.2f}", "hard"))
    if m_bar is not None and m_d is not None and m_bar > m_d / 0.90:
        out.append((f"mu0_bar={m_bar:.2f}>mu0_disk/0.90={m_d / 0.90:.2f}", "hard"))
    return out


def check_flux(comps: dict[str, dict]) -> tuple[list[tuple[str, str]], dict]:
    lums = {k: 10 ** (-0.4 * c["mag"]) for k, c in comps.items()
            if c.get("mag") is not None}
    tot = sum(lums.values())
    fracs = {k: v / tot for k, v in lums.items()} if tot > 0 else {}
    out: list[tuple[str, str]] = []
    fb, fl = fracs.get("bar"), fracs.get("lens")
    if fb is not None:
        if not (0.10 <= fb <= 0.40):
            out.append((f"bar_flux={fb * 100:.1f}% outside 10-40%", "note"))
    if fl is not None:
        if not (0.05 <= fl <= 0.35):
            out.append((f"lens_flux={fl * 100:.1f}% outside 5-35%", "note"))
    if fb is not None and fl is not None and fl >= fb:
        out.append((f"lens_flux={fl * 100:.1f}%>=bar_flux={fb * 100:.1f}%", "hard"))
    return out, fracs


# --------------------------------------------------------------- replay loop
def locked_turn(gdir: str) -> str | None:
    for rep in glob.glob(os.path.join(gdir, "analysis_report_*.md")):
        txt = open(rep, encoding="utf-8", errors="ignore").read()
        m = re.findall(r"```json\s*(\{.*?\})\s*```", txt, re.S)
        if m:
            try:
                return json.loads(m[-1]).get("best_turn")
            except Exception:
                continue
    return None




def replay(root: str) -> dict:
    stats = {
        "galaxies": 0, "rounds": 0, "locked_damage": [], "fire": {},
        "dist": {"bar_frac": [], "lens_frac": [], "disk_q": [], "bulge_q": [],
                 "bar_q": [], "lens_q": [], "lens_n": []},
        "minround_physical": [],
    }
    for gdir in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(os.path.join(gdir, "archives")):
            continue
        stats["galaxies"] += 1
        lock = locked_turn(gdir)
        # --qiso: outer-isophote anchor per galaxy (anchored flat-disk skip);
        # measured once from the root feedme's image
        q_iso = None
        if "--qiso" in sys.argv:
            try:
                from beam.tools import measure_outer_iso_q

                root_feedme = os.path.join(gdir, "galfit.feedme")
                if os.path.isfile(root_feedme):
                    q_iso = measure_outer_iso_q(root_feedme).get("q_iso")
            except Exception:
                q_iso = None
        for arch in sorted(glob.glob(os.path.join(gdir, "archives", "*"))):
            rs_f = os.path.join(arch, "round_status.json")
            if not os.path.isfile(rs_f):
                continue
            try:
                rs = json.load(open(rs_f))
                fs = rs.get("fit_statistics") or {}
            except Exception:
                continue
            if not fs.get("bic_eff"):
                continue
            # run-time absolute paths in round_status are stale (dirs were
            # moved) — resolve artefacts from the archive directory listing
            names = os.listdir(arch)
            fitted_l = sorted([f for f in names if re.fullmatch(r"galfit\.\d+", f)])
            input_l = sorted([f for f in names if re.fullmatch(r"(_?iter\d+\.feedme|[^_]*\.feedme)", f)
                              and not f.startswith("galfit")])
            cons_l = sorted([f for f in names if f.endswith(".cons") or
                             re.fullmatch(r"iter\d+\.cons", f)])
            if not fitted_l or not input_l:
                continue
            fitted_f = os.path.join(arch, fitted_l[-1])
            input_f = os.path.join(arch, input_l[0])
            cons_f = os.path.join(arch, cons_l[0]) if cons_l else None
            fitted = number_components(fitted_f, name_file=input_f)
            inputs = number_components(input_f)
            if not fitted:
                continue
            stats["rounds"] += 1
            by_name = {str(c["name"]).lower(): dict(c) for c in fitted.values()
                       if str(c["name"]).lower() in MAIN or str(c["name"]).lower() == "agn"}
            for num, c in fitted.items():
                c["number"] = num
                by_name.setdefault(str(c["name"]).lower(), dict(c))["number"] = num
            events: list[tuple[str, str]] = []
            for h in check_onion(by_name, q_iso=q_iso):
                events.append((h, "hard"))
            events += check_q_priors(by_name)
            events += check_n(by_name)
            events += check_mu0(by_name, fs.get("a_psf"))
            fl_events, fracs = check_flux(by_name)
            events += fl_events
            for ev, sev in events:
                key = ev.split(":")[0].split("=")[0].split(">")[0].split("<")[0]
                kk = {"mu0_bulge": "mu0_bulge>=disk", "mu0_agn": "mu0_agn>=bulge",
                      "mu0_bar": "mu0_bar>disk/0.95", "bar_flux": "bar_flux_win",
                      "lens_flux": "lens_flux_win"}.get(key, key)
                d = stats["fire"].setdefault(kk, {"hard": 0, "note": 0})
                d[sev] += 1
            # distributions
            if "bar" in by_name and fracs.get("bar") is not None:
                stats["dist"]["bar_frac"].append(fracs["bar"])
            if "lens" in by_name and fracs.get("lens") is not None:
                stats["dist"]["lens_frac"].append(fracs["lens"])
            for k in ("disk", "bulge", "bar", "lens"):
                if k in by_name and by_name[k].get("ba") is not None:
                    stats["dist"][f"{k}_q"].append(by_name[k]["ba"])
            if "lens" in by_name and by_name["lens"].get("n") is not None:
                stats["dist"]["lens_n"].append(by_name["lens"]["n"])
            # lock damage
            if lock and os.path.basename(arch) == lock:
                hard = [e for e, s in events if s == "hard"]
                if hard:
                    stats["locked_damage"].append(
                        {"galaxy": os.path.basename(gdir), "hard": hard})
    return stats


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/jiangbo/experiments/dual_agents_w5"
    stats = replay(root)
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if out:
        json.dump(stats, open(out, "w"), indent=1)
    print(f"galaxies={stats['galaxies']} rounds={stats['rounds']}")
    print(f"{'check':24s} {'hard':>6s} {'note':>6s}")
    for k in sorted(stats["fire"]):
        d = stats["fire"][k]
        print(f"{k:24s} {d['hard']:6d} {d['note']:6d}")
    print(f"\nlocked rounds with NEW hard hits: {len(stats['locked_damage'])}")
    for e in stats["locked_damage"]:
        print(f"  {e['galaxy']}: {e['hard']}")
    dist = stats["dist"]
    for k, v in dist.items():
        if v:
            v = sorted(v)
            q = lambda p: v[min(int(p * len(v)), len(v) - 1)]
            print(f"{k:10s} n={len(v):4d} min={v[0]:.3f} p10={q(0.1):.3f} "
                  f"med={q(0.5):.3f} p90={q(0.9):.3f} max={v[-1]:.3f}")


if __name__ == "__main__":
    main()
