"""Code-side deterministic physicality checks on a FITTED state.

Why this module exists (KILOGAS_319 shakedown, defect A): the same numeric
condition was classified differently across surveyor rounds (bar q=0.6 pinned
at its band was a ``[note]`` in A.9's PASS verdict but a ``[hard]`` FAIL in
A.12) because the numeric checks lived in the VLM prompt. The verdict gates
best-state eligibility, so its numeric part must be mechanical.

Contract:
* ``compute_mech_checks(graph, state)`` returns ``[{"severity": "hard"|"note",
  "check": ..., "detail": ...}, ...]`` — deterministic, file-backed facts only
  (fitted inventory, effective .cons bands via the provenance-aware bound-hit
  scanner, graph meta). Visual judgements stay with the surveyor VLM.
* ``merge_verdict(state, vlm_verdict)`` merges the mechanical table into the
  surveyor verdict: any ``hard`` entry forces FAIL (``mech_veto``), mechanical
  entries are prefixed ``[mech-hard]`` / ``[mech-note]`` and appended after
  the surveyor's verbatim ``failed_checks``; the original VLM verdict is
  preserved in ``verdict_vlm``.

Check set (each grounded in a KILOGAS_319 round where the VLM applied it):
  re_chain        hard  central Re total order re_disk > re_lens > re_bar >
                        re_bulge (outerdisk above re_disk); survivors only
  axis_ratio      hard  bar q > 0.6; any shaped component q < 0.05
                        (thin-line degeneracy) or q > 1.0
  containment     hard  2*Re of a shaped component leaving the fit region
                        from its centre (A.5/A.7/A.11/A.13 disk 2*Re)
  concentric      hard  a chained central component's centre deviating
                        > 2 px from the disk/anchor centre (A.10 outerdisk)
  degeneracy      hard  two shaped main-galaxy components with
                        min/max(Re) > 0.95 and both > PSF FWHM
                        (A.10 disk 109.8px vs outerdisk 115.2px)
  bound_pin       hard  a non-exempt bound hit whose band is self-imposed
                        (candidate tightening: the candidate premise failed);
                        original/default-band pins and standing exemptions
                        (q<=1 domain edge, Re pinned at the mandatory
                        PSF-scale Re floor — any provenance, bar n prior)
                        are notes
  zombie          note  flux < 0.5% of the brightest (dedup criterion only)
  priors          note  bulge q < 0.5 / lens q < 0.5 prior violations
"""

from __future__ import annotations

import math
import os
import re

from beam.signature import normalize_inventory

_COMPANION_RE = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)

# Re-chain rank (mirrors candidate_schema._check_re_chain; higher = outer)
RE_RANK = {"bulge": 0, "bar": 1, "lens": 2, "disk": 3, "edgedisk": 3, "outerdisk": 4}
CENTRAL_CHAIN = {"disk", "edgedisk", "bulge", "bar", "lens", "outerdisk"}

BAR_Q_HARD_MAX = 0.6
THIN_LINE_Q = 0.05
CONCENTRIC_TOL_PX = 2.0
DEGENERACY_RATIO = 0.95
RE_CHAIN_EPS_PX = 0.01
# containment tolerance: ignore sub-pixel overshoot at the panel edge
CONTAIN_TOL_PX = 2.0


def _f(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def scan_state_bound_hits(graph, state: dict) -> list[dict]:
    """Bound-hit dicts (provenance-aware) for a fitted state; [] when the
    artefacts/cons file are unavailable (old states, missing files)."""
    artifacts = state.get("artifacts", {})
    feedme = artifacts.get("feedme")
    fitted = artifacts.get("galfit_nn")
    if not feedme or not fitted or not os.path.exists(feedme) or not os.path.exists(fitted):
        return []
    meta = graph.g.graph.get("meta", {})
    try:
        from tools.parse_feedme import parse_feedme

        cons_rel = parse_feedme(feedme).get("constraint")
    except Exception:
        return []
    if not cons_rel or str(cons_rel).lower() == "none":
        return []
    cons_file = cons_rel if os.path.isabs(cons_rel) else \
        os.path.join(os.path.dirname(feedme), cons_rel)
    region = meta.get("fit_region")
    from beam.cons_decode import scan_cons_bound_hits

    hits, _warn = scan_cons_bound_hits(
        cons_file, feedme, fitted,
        psf_fwhm_px=meta.get("psf_fwhm_px"),
        fit_region=tuple(region) if region else None)
    return hits


def compute_mech_checks(graph, state: dict) -> list[dict]:
    """Deterministic numeric check table for one fitted state."""
    checks: list[dict] = []
    inv = normalize_inventory(state.get("inventory", []))
    if not inv:
        return checks
    meta = graph.g.graph.get("meta", {})
    psf = _f(meta.get("psf_fwhm_px")) or 1.0
    region = meta.get("fit_region")

    main = [c for c in inv if not _COMPANION_RE.match(c.get("name") or "")]
    shaped = [c for c in inv if (c.get("type") or "") != "psf"]  # companions included
    main_shaped = [c for c in main if (c.get("type") or "") != "psf"]

    # ---- re_chain (hard): strict decrease with rank among existing central comps
    chain = [c for c in shaped if c.get("name") in RE_RANK and _f(c.get("re_effective"))]
    chain.sort(key=lambda c: RE_RANK[c["name"]])
    for a, b in zip(chain, chain[1:]):
        ra, rb = _f(a.get("re_effective")), _f(b.get("re_effective"))
        # strict decrease outward required: inner Re must stay below outer Re
        if ra >= rb - RE_CHAIN_EPS_PX:
            checks.append({
                "severity": "hard", "check": "re_chain",
                "detail": f"re inversion: re_{a['name']}({ra:g}px) >= "
                          f"re_{b['name']}({rb:g}px) (required strict decrease "
                          f"bulge < bar < lens < disk < outerdisk)",
            })

    # ---- axis_ratio (hard; all shaped components incl. companions) + priors (note)
    for c in shaped:
        q = _f(c.get("q"))
        if q is None:
            continue
        name = c.get("name") or "?"
        if q < THIN_LINE_Q:
            checks.append({"severity": "hard", "check": "axis_ratio",
                           "detail": f"{name} q={q:g} <= {THIN_LINE_Q:g}: thin-line "
                                     "degeneracy (infinitely thin component)"})
        if q > 1.0 + 1e-6:
            checks.append({"severity": "hard", "check": "axis_ratio",
                           "detail": f"{name} q={q:g} > 1.0 (impossible geometry)"})
    for c in main_shaped:
        q = _f(c.get("q"))
        if q is None:
            continue
        name = c.get("name") or "?"
        if name == "bar" and q > BAR_Q_HARD_MAX:
            checks.append({"severity": "hard", "check": "axis_ratio",
                           "detail": f"bar q={q:g} > {BAR_Q_HARD_MAX:g} (axis-ratio hard limit)"})
        if name == "bulge" and q < 0.5:
            checks.append({"severity": "note", "check": "prior",
                           "detail": f"bulge q={q:g} < 0.5 prior (bar/lens confusion risk)"})
        if name == "lens" and q < 0.5:
            checks.append({"severity": "note", "check": "prior",
                           "detail": f"lens q={q:g} < 0.5 prior (lens requires q > 0.5)"})

    # ---- containment (hard): 2*Re leaves the fit region from the centre
    if region:
        xmin, xmax, ymin, ymax = (float(v) for v in region)
        for c in shaped:
            re2 = _f(c.get("re_effective"))
            x, y = _f(c.get("x")), _f(c.get("y"))
            if re2 is None or x is None or y is None:
                continue
            re2 *= 2.0
            if (x + re2 > xmax + CONTAIN_TOL_PX or x - re2 < xmin - CONTAIN_TOL_PX
                    or y + re2 > ymax + CONTAIN_TOL_PX or y - re2 < ymin - CONTAIN_TOL_PX):
                checks.append({"severity": "hard", "check": "containment",
                               "detail": f"outermost containment: {c.get('name')} 2*Re "
                                         f"({re2:g}px) leaves the fit region "
                                         f"[{xmin:g},{xmax:g}]x[{ymin:g},{ymax:g}]"})

    # ---- concentric (hard): chained central components vs the anchor centre
    chained = [c for c in shaped if c.get("name") in CENTRAL_CHAIN
               and _f(c.get("x")) is not None and _f(c.get("y")) is not None]
    anchor = next((c for c in chained if c.get("name") in {"disk", "edgedisk"}), None)
    if anchor is None and chained:
        anchor = min(chained, key=lambda c: _f(c.get("mag")) or 99.0)
    if anchor is not None:
        ax, ay = _f(anchor.get("x")), _f(anchor.get("y"))
        for c in chained:
            if c is anchor:
                continue
            dev = math.hypot(_f(c.get("x")) - ax, _f(c.get("y")) - ay)
            if dev > CONCENTRIC_TOL_PX:
                checks.append({"severity": "hard", "check": "concentric",
                               "detail": f"{c.get('name')} centre deviates {dev:.2f}px "
                                         f"(> {CONCENTRIC_TOL_PX:g}px) from the "
                                         f"{anchor.get('name')} anchor centre"})

    # ---- degeneracy (hard): near-identical Re pairs (main galaxy only —
    # a companion at a different position with similar Re is not degeneracy)
    for i, a in enumerate(main_shaped):
        for b in main_shaped[i + 1:]:
            ra, rb = _f(a.get("re_effective")), _f(b.get("re_effective"))
            if ra is None or rb is None or min(ra, rb) <= psf:
                continue
            if min(ra, rb) / max(ra, rb) > DEGENERACY_RATIO:
                checks.append({"severity": "hard", "check": "degeneracy",
                               "detail": f"{a.get('name')} ({ra:g}px) and {b.get('name')} "
                                         f"({rb:g}px) degeneracy (Re ratio > "
                                         f"{DEGENERACY_RATIO:g}, both > PSF FWHM)"})

    # ---- bound pins (provenance-aware severity)
    seen_pins: set[tuple] = set()
    for h in scan_state_bound_hits(graph, state):
        key = (h.get("comp"), h.get("param"), h.get("direction"))
        if key in seen_pins:
            continue
        seen_pins.add(key)
        band = h.get("band") or [None, None]
        if h.get("exempt"):
            checks.append({"severity": "note", "check": "bound_pin",
                           "detail": f"{h.get('comp')} {h.get('param')}={h.get('fitted'):g} "
                                     f"at the {h.get('direction')} bound (exempt: {h.get('note')})"})
        elif h.get("provenance") == "self-imposed":
            checks.append({"severity": "hard", "check": "bound_pin",
                           "detail": f"{h.get('comp')} {h.get('param')}={h.get('fitted'):g} pinned "
                                     f"at the {h.get('direction')} bound "
                                     f"[{band[0]:g},{band[1]:g}] (self-imposed band: the "
                                     "candidate premise failed — relax or repair)"})
        else:
            checks.append({"severity": "note", "check": "bound_pin",
                           "detail": f"{h.get('comp')} {h.get('param')}={h.get('fitted'):g} at the "
                                     f"{h.get('direction')} bound "
                                     f"[{band[0]:g},{band[1]:g}] "
                                     f"({h.get('provenance')} band)"})

    # ---- zombies (note only — never a removal ground by itself)
    for z in state.get("zombies", []) or []:
        checks.append({"severity": "note", "check": "zombie",
                       "detail": f"{z}: flux < 0.5% of the brightest component "
                                 "(zombie flag — dedup criterion only)"})

    return checks


def merge_verdict(mech_checks: list[dict], vlm_verdict: dict) -> tuple[dict, bool]:
    """Merge the mechanical table into the surveyor verdict.

    Returns ``(merged_verdict, vetoed)``. The surveyor's own checks are kept
    verbatim and prepended-by-authority: mechanical hard entries force FAIL.
    """
    hard = [m for m in mech_checks if m.get("severity") == "hard"]
    notes = [m for m in mech_checks if m.get("severity") != "hard"]
    merged = dict(vlm_verdict)
    mech_lines = ([f"[mech-hard] {m['detail']}" for m in hard]
                  + [f"[mech-note] {m['detail']}" for m in notes])
    if mech_lines:
        merged["failed_checks"] = list(vlm_verdict.get("failed_checks") or []) + mech_lines
    vetoed = bool(hard)
    if vetoed:
        merged["verdict"] = "FAIL"
        merged["mech_veto"] = True
    return merged, vetoed
