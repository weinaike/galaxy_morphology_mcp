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

Expert-round-2 additions (thresholds validated by the offline replay over
dual_agents_w5, 925 rounds — scripts/replay_physicality_checks.py):
  onion           hard  2Re ellipse nesting outerdisk > disk > lens > bar >
                        bulge, in two layers: (a) AREA ordering (Re^2*q of
                        adjacent chain pairs) — always on for any disk q;
                        (b) directional non-crossing (sampled over position
                        angles) — skipped in the flat-disk regime (disk q <
                        0.3 or the edgedisk slot) only when the flat disk is
                        corroborated by image-level evidence (Stage-1
                        edge-on/dust-lane text or consistency with the
                        outer-isophote anchor q_iso; KILOGAS_120 defect: a
                        degenerate fit flattened the disk below the old
                        unconditional skip and self-immunized against the
                        nesting check); per-pair exempt (both layers) when
                        the inner is a bulge rounder than the outer by > 0.25
                        (a round bulge sits laterally wider than a flat bar —
                        the normal configuration, not a violation)
  disk_shape_inconsistency hard  fitted disk q far flatter than the image's
                        outer-isophote anchor q_iso (|q_disk - q_iso| >
                        ONION_Q_ISO_TOL) with no Stage-1 edge-on corroboration
                        — suspected disk-role collapse (flux usurpation), not
                        a real edge-on geometry
  shape_order     hard  q_bar >= q_lens when both exist (a bar must be more
                        elongated than the lens it sits inside)
  profile_prior   hard  lens n >= 0.6 (not a lens profile; 0.5-0.6 note);
                        outerdisk (sersic variant) n >= 1.0
                        (the former lens flat-degeneration sub-check — n <= 0.1
                        AND Re >= 0.9 x its .cons re cap — was removed with
                        candidate-declared bands: with the default bound set
                        only, self-imposed caps no longer exist to hit)
  mu0_order       hard  central surface brightness (analytic): mu0_bulge >=
                        mu0_disk, mu0_bar > mu0_disk/0.90 (bar may be fainter
                        only within the 0.90 tolerance), mu0_agn >= mu0_bulge
                        (psf peak via the A_psf proxy)
  flux_share      hard  f_lens >= f_bar (a lens must not outshine its bar)
                  note  bar flux fraction outside 10-40%; lens outside 5-35%
  shape_prior     hard  bulge q < 0.3 (0.3-0.4 stays a note); lens q <= 0.5;
                        bar q in (0.5, 0.6] becomes a note (round-bar watch)

Administrative disable switch (temporary experimentation): the env var
``GALMCP_DISABLE_MECH_CHECKS`` holds a comma-separated list of check families
to drop from the table — a bare family name disables all its severities,
``<family>:hard`` / ``<family>:note`` only that severity. Filtering happens at
the single choke point ``compute_mech_checks``, so the merge, the record_fit
fail-safe and the prompt digest never see disabled entries.
"""

from __future__ import annotations

import math
import os
import re
import sys

from beam.signature import normalize_inventory

_COMPANION_RE = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)

# Re-chain rank (mirrors candidate_schema._check_re_chain; higher = outer)
RE_RANK = {"bulge": 0, "bar": 1, "lens": 2, "disk": 3, "edgedisk": 3, "outerdisk": 4}
CENTRAL_CHAIN = {"disk", "edgedisk", "bulge", "bar", "lens", "outerdisk"}

BAR_Q_HARD_MAX = 0.6
BAR_Q_NOTE_MAX = 0.5
BULGE_Q_HARD_MIN = 0.3
BULGE_Q_NOTE_MIN = 0.4
LENS_Q_HARD_MIN = 0.5
LENS_N_HARD_MIN = 0.6
LENS_N_NOTE_MIN = 0.5
OUTERDISK_N_HARD_MAX = 1.0
THIN_LINE_Q = 0.05
CONCENTRIC_TOL_PX = 2.0
DEGENERACY_RATIO = 0.95
RE_CHAIN_EPS_PX = 0.01
# containment tolerance: ignore sub-pixel overshoot at the panel edge
CONTAIN_TOL_PX = 2.0
# onion nesting (expert round 2, replay-validated rule B)
ONION_TOL = 0.02
ONION_SKIP_Q = 0.3          # edge-on regime: directional nesting undefined
ONION_BULGE_MISMATCH = 0.25  # round bulge in a flat bar/lens: normal config
# anchored flat-disk skip (KILOGAS_120 defect): a fitted disk q below
# ONION_SKIP_Q only earns the directional-skip when the IMAGE is that flat —
# |q_disk - q_iso| <= tolerance against the outer-isophote anchor measured
# once at beam_init (graph meta q_iso_outer), or an explicit Stage-1
# edge-on/dust-lane classification. The fitter's own q never corroborates
# itself: degenerate flattening must not self-immunize against the check.
ONION_Q_ISO_TOL = 0.15
_EDGE_ON_RE = re.compile(r"edge[- ]?on|dust[- ]lane", re.IGNORECASE)
# central surface brightness ordering
MU0_BAR_TOL = 0.90
# flux-share windows (fractions of the total model light)
BAR_FLUX_WIN = (0.10, 0.40)
LENS_FLUX_WIN = (0.05, 0.35)

# ---- administrative disable switch (GALMCP_DISABLE_MECH_CHECKS) -----------
DISABLE_ENV = "GALMCP_DISABLE_MECH_CHECKS"
_MECH_CHECK_NAMES = frozenset({
    "re_chain", "axis_ratio", "containment", "concentric", "degeneracy",
    "bound_pin", "onion", "shape_order", "profile_prior", "mu0_order",
    "flux_share", "shape_prior", "prior", "zombie", "internal",
    "disk_shape_inconsistency",
})
_warned_unknown: set[str] = set()
_disable_announced = False


def _disabled_checks() -> dict[str, set[str]]:
    """{check_name: {severities to drop}} from the env var ({} = none).

    Tokens are comma-separated; a bare ``<family>`` disables all severities,
    ``<family>:hard`` / ``<family>:note`` only that severity. Unknown family
    names warn once (typo guard) and are ignored.
    """
    global _disable_announced
    raw = (os.environ.get(DISABLE_ENV) or "").strip()
    out: dict[str, set[str]] = {}
    if not raw:
        return out
    for token in raw.replace(";", ",").replace(" ", ",").split(","):
        token = token.strip()
        if not token:
            continue
        name, _, sev = token.partition(":")
        name = name.strip().lower()
        if name not in _MECH_CHECK_NAMES:
            if name not in _warned_unknown:
                _warned_unknown.add(name)
                print(f"[physicality] WARNING: unknown check family '{name}' in "
                      f"{DISABLE_ENV} (valid: {', '.join(sorted(_MECH_CHECK_NAMES))})",
                      file=sys.stderr)
            continue
        sev = sev.strip().lower()
        sevs = {sev} if sev in {"hard", "note"} else {"hard", "note"}
        out.setdefault(name, set()).update(sevs)
    if out and not _disable_announced:
        _disable_announced = True
        desc = ", ".join(f"{n}({'/'.join(sorted(s))})" for n, s in sorted(out.items()))
        print(f"[physicality] NOTE: mech-check families disabled by "
              f"{DISABLE_ENV}: {desc}", file=sys.stderr)
    return out


def _f(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


_LN10 = math.log(10.0)


def _sersic_mu0(mag: float, re: float, n: float) -> float | None:
    """Analytic central surface brightness of a Sersic profile (zeropoint-free:
    only differences between components are used). None when n < 0.5 — the
    k ~ 2n - 1/3 + 4/(405n) approximation is invalid for flatter profiles."""
    if n < 0.5 or re <= 0:
        return None
    k = 2.0 * n - 1.0 / 3.0 + 4.0 / (405.0 * n)
    if k <= 0.05:
        return None
    mu_e = mag + 5.0 * math.log10(re) + 2.5 * math.log10(
        2.0 * math.pi * n * math.exp(k) / k ** (2.0 * n))
    return mu_e - 2.5 * k / _LN10


def _comp_mu0(c: dict, a_psf: float | None) -> float | None:
    """Central surface brightness of one fitted component; the psf peak uses
    the A_psf proxy (total flux spread over one PSF area)."""
    mag, t = _f(c.get("mag")), (c.get("type") or "").lower()
    if mag is None:
        return None
    if t == "psf":
        return mag + 2.5 * math.log10(a_psf) if a_psf else None
    if t == "expdisk":
        rs = _f(c.get("re"))
        return mag + 5.0 * math.log10(rs) + 2.5 * math.log10(2.0 * math.pi) if rs else None
    n, re = _f(c.get("n")), _f(c.get("re_effective"))
    if t == "sersic" and n and re:
        return _sersic_mu0(mag, re, n)
    return None


def _ellipse_r(theta_deg: float, a: float, b: float, pa_deg: float) -> float:
    """Radius of an ellipse (semi-axes a >= b, major-axis PA) along the
    direction theta_deg (degrees from +Y, N=+Y contract)."""
    d = math.radians(theta_deg - pa_deg)
    den = math.hypot(b * math.cos(d), a * math.sin(d))
    return a * b / den if den > 0 else math.inf


_ONION_ANGLES = list(range(0, 180, 2))  # degrees, 2-degree steps


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


def _flat_disk_corroborated(graph, dq: float | None) -> bool | None:
    """Image-level corroboration for a flat fitted disk (q < ONION_SKIP_Q).

    Returns True (flat disk corroborated — the directional nesting skip
    stands), False (flat q contradicts the image — degeneration suspected,
    re-arm the directional check and flag disk_shape_inconsistency) or None
    (no anchor information — legacy behaviour: the skip stands, so graphs
    initialised before q_iso existed replay unchanged).

    Anchors, in order: an explicit Stage-1 edge-on/dust-lane classification;
    then consistency with the outer-isophote axis ratio q_iso measured once
    at beam_init (graph meta ``q_iso_outer``). The fitter's own q never
    counts — the optimiser controls it (KILOGAS_120: disk q collapsed
    0.79 -> 0.26 in exactly the round the lens usurped 86.5% of the flux).
    """
    try:
        stage1 = str((graph.g.graph.get("stage1") or {}).get("morphology") or "")
    except Exception:
        stage1 = ""
    if stage1 and _EDGE_ON_RE.search(stage1):
        return True
    q_iso = _f((graph.g.graph.get("meta") or {}).get("q_iso_outer"))
    if q_iso is not None and dq is not None:
        return abs(dq - q_iso) <= ONION_Q_ISO_TOL
    return None


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
        elif name == "bar" and q > BAR_Q_NOTE_MAX:
            checks.append({"severity": "note", "check": "shape_prior",
                           "detail": f"bar q={q:g} in (0.5,0.6] (round-bar watch: "
                                     "bar/lens identity confusion risk)"})
        if name == "bulge":
            if q < BULGE_Q_HARD_MIN:
                checks.append({"severity": "hard", "check": "shape_prior",
                               "detail": f"bulge q={q:g} < {BULGE_Q_HARD_MIN:g} "
                                         "(not a bulge: bar/lens/sliver identity question)"})
            elif q < BULGE_Q_NOTE_MIN:
                checks.append({"severity": "note", "check": "prior",
                               "detail": f"bulge q={q:g} < {BULGE_Q_NOTE_MIN:g} prior "
                                         "(bar/lens confusion risk)"})
        if name == "lens" and q <= LENS_Q_HARD_MIN:
            checks.append({"severity": "hard", "check": "shape_prior",
                           "detail": f"lens q={q:g} <= {LENS_Q_HARD_MIN:g} "
                                     "(lens requires q > 0.5)"})

    # ---- containment (hard): the 2*Re ellipse leaves the fit region.
    # The reach toward each edge is the ellipse support radius along the
    # cardinal directions (N=+Y contract: 0 deg = +y, 90 deg = +x), i.e. PA
    # and q both matter — a flat (q<<1) component whose major axis runs
    # diagonally reaches the edges far less than 2*Re. Isotropic fallback
    # (2*Re circle) only when q/pa are unavailable.
    if region:
        xmin, xmax, ymin, ymax = (float(v) for v in region)
        for c in shaped:
            re2 = _f(c.get("re_effective"))
            x, y = _f(c.get("x")), _f(c.get("y"))
            if re2 is None or x is None or y is None:
                continue
            re2 *= 2.0
            q = _f(c.get("q"))
            pa = _f(c.get("pa"))
            if q is not None and pa is not None and 0.0 < q <= 1.0 + 1e-6:
                rx = _ellipse_r(90.0, re2, re2 * q, pa)  # reach toward ±x edges
                ry = _ellipse_r(0.0, re2, re2 * q, pa)   # reach toward ±y edges
                geom = f"2*Re ({re2:g}px, q={q:g}, PA={pa:g}deg) reach " \
                       f"x±{rx:g}px / y±{ry:g}px"
            else:
                rx = ry = re2
                geom = f"2*Re ({re2:g}px, isotropic) reach x±{rx:g}px / y±{ry:g}px"
            if (x + rx > xmax + CONTAIN_TOL_PX or x - rx < xmin - CONTAIN_TOL_PX
                    or y + ry > ymax + CONTAIN_TOL_PX or y - ry < ymin - CONTAIN_TOL_PX):
                checks.append({"severity": "hard", "check": "containment",
                               "detail": f"outermost containment: {c.get('name')} {geom} "
                                         f"leaves the fit region "
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

    # ---- onion nesting (hard; expert round 2, replay-validated rule B):
    # 2Re ellipses of the central chain must nest, in two layers:
    #   (a) AREA ordering — always on, any disk q: the inner 2*Re ellipse
    #       AREA (Re^2*q, semi-axes Re and q*Re) must stay below the outer's.
    #       Well defined even for flat disks, where directional containment is
    #       geometrically degenerate along the minor axis.
    #   (b) DIRECTIONAL containment (sampled over position angles) — skipped
    #       in the flat-disk regime (disk q < 0.3 or the edgedisk slot) ONLY
    #       when the flat disk is corroborated by image-level evidence
    #       (_flat_disk_corroborated); a fitted q below the threshold without
    #       corroboration is itself flagged (disk_shape_inconsistency) and the
    #       directional layer re-arms.
    # Both layers per-pair exempt when the inner is a bulge rounder than the
    # outer by > 0.25 (a round bulge sits laterally wider than a flat bar /
    # covers more projected area than a thin disk — the normal configuration;
    # this also protects genuine edge-on decompositions' thick bulges).
    by = {c["name"]: c for c in main_shaped if c.get("name") in CENTRAL_CHAIN}
    onion_chain = [n for n in ("outerdisk", "disk", "lens", "bar", "bulge")
                   if n in by]
    if "edgedisk" not in by and onion_chain:
        dq = _f(by["disk"].get("q")) if "disk" in by else None
        run_directional = dq is None or dq >= ONION_SKIP_Q
        if not run_directional and _flat_disk_corroborated(graph, dq) is False:
            q_iso = _f((graph.g.graph.get("meta") or {}).get("q_iso_outer"))
            anchor_txt = f"q_iso={q_iso:.2f}" if q_iso is not None else "q_iso unavailable"
            checks.append({
                "severity": "hard", "check": "disk_shape_inconsistency",
                "detail": f"fitted disk q={dq:g} is far flatter than the image-level "
                          f"outer-isophote anchor ({anchor_txt}, |delta| > "
                          f"{ONION_Q_ISO_TOL:g}) and Stage-1 does not classify the "
                          "galaxy edge-on — suspected degenerate flattening (disk-role "
                          "collapse / flux usurpation), not a real edge-on geometry"})
            run_directional = True
        for outer, inner in zip(onion_chain, onion_chain[1:]):
            co, ci = by[outer], by[inner]
            qo, qi = _f(co.get("q")), _f(ci.get("q"))
            if inner == "bulge" and qo is not None and qi is not None \
                    and qi - qo > ONION_BULGE_MISMATCH:
                continue
            ao = _f(co.get("re_effective"))
            ai = _f(ci.get("re_effective"))
            if not ao or not ai:
                continue
            bo, bi = (qo or 1.0) * ao, (qi or 1.0) * ai
            area_ratio = (ai * bi) / (ao * bo)
            if area_ratio > 1.0 + ONION_TOL:
                checks.append({"severity": "hard", "check": "onion",
                               "detail": f"{inner} 2*Re ellipse area exceeds "
                                         f"{outer} (Re^2*q ratio {area_ratio:.2f}; "
                                         f"q_{inner}={qi or 1:g}, "
                                         f"q_{outer}={qo or 1:g})"})
            if run_directional:
                po = _f(co.get("pa")) or 0.0
                pi = _f(ci.get("pa")) or 0.0
                worst = max(_ellipse_r(t, ai, bi, pi) / _ellipse_r(t, ao, bo, po)
                            for t in _ONION_ANGLES)
                if worst > 1.0 + ONION_TOL:
                    checks.append({"severity": "hard", "check": "onion",
                                   "detail": f"{inner} 2*Re ellipse pokes out of "
                                             f"{outer} (max radius ratio {worst:.2f}; "
                                             f"q_{inner}={qi or 1:g}, q_{outer}={qo or 1:g})"})

    # ---- shape order (hard): a bar must be more elongated than its lens
    bar_c, lens_c = by.get("bar"), by.get("lens")
    if bar_c and lens_c:
        qb, ql = _f(bar_c.get("q")), _f(lens_c.get("q"))
        if qb is not None and ql is not None and qb >= ql:
            checks.append({"severity": "hard", "check": "shape_order",
                           "detail": f"q_bar={qb:g} >= q_lens={ql:g} "
                                     "(a bar must be flatter than the lens)"})

    # ---- profile priors (hard): lens/outerdisk n
    if lens_c:
        ln = _f(lens_c.get("n"))
        if ln is not None:
            if ln >= LENS_N_HARD_MIN:
                checks.append({"severity": "hard", "check": "profile_prior",
                               "detail": f"lens n={ln:g} >= {LENS_N_HARD_MIN:g} "
                                         "(not a lens profile: identity question)"})
            elif ln >= LENS_N_NOTE_MIN:
                checks.append({"severity": "note", "check": "profile_prior",
                               "detail": f"lens n={ln:g} in [0.5,0.6) (lens prior is n < 0.5)"})
    outer_c = by.get("outerdisk")
    if outer_c and (outer_c.get("type") or "") == "sersic":
        on = _f(outer_c.get("n"))
        if on is not None and on >= OUTERDISK_N_HARD_MAX:
            checks.append({"severity": "hard", "check": "profile_prior",
                           "detail": f"outerdisk n={on:g} >= 1.0 (outer envelope "
                                     "must be n < 1)"})

    # ---- bulge n at the cap (note; Plate0284 retrospective): a free bulge n
    # pinned at the default upper bound wants a cuspier-than-n=8 core —
    # usually an unfitted central point source (compact bulge) or envelope
    # flux-grabbing (large-Re bulge). The pin is a diagnostic fact, never a
    # veto (original default band); the prompt repair menu maps the competing
    # candidates (add(agn)+n re-warm / disk role reapportionment / giant-
    # elliptical waiver). Raising the cap or freezing n=8 is NOT a repair.
    bulge_c = by.get("bulge")
    if bulge_c:
        bn = _f(bulge_c.get("n"))
        t_n = (bulge_c.get("toggles") or {}).get("n", 1)
        t_n = 1 if t_n is None else t_n
        try:
            n_free = int(t_n) == 1
        except (TypeError, ValueError):
            n_free = True
        if bn is not None and n_free:
            num = str(bulge_c.get("number") or "")
            band = (state.get("cons_effective") or {}).get(f"{num}.n")
            cap = _f(band[1]) if band else None
            if (cap is not None and bn >= 0.98 * cap) or (cap is None and bn >= 7.84):
                checks.append({"severity": "note", "check": "profile_prior",
                               "detail": f"bulge n={bn:g} at the n upper bound "
                                         f"{cap if cap is not None else 8.0:g} "
                                         "(point-source/envelope identity question: a "
                                         "cuspier core usually compensates an unfitted "
                                         "central point source; a large-Re high-n wing "
                                         "grabs envelope flux)"})

    # ---- mu0 ordering (hard; analytic central surface brightness)
    a_psf = _f(meta.get("a_psf_px2"))
    m_disk = _comp_mu0(by["disk"], a_psf) if "disk" in by else None
    m_bulge = _comp_mu0(by["bulge"], a_psf) if "bulge" in by else None
    m_bar = _comp_mu0(by["bar"], a_psf) if "bar" in by else None
    agn = next((c for c in inv if c.get("name") == "agn"), None)
    m_agn = _comp_mu0(agn, a_psf) if agn else None
    if m_bulge is not None and m_disk is not None and m_bulge >= m_disk:
        checks.append({"severity": "hard", "check": "mu0_order",
                       "detail": f"mu0_bulge={m_bulge:.2f} >= mu0_disk={m_disk:.2f} "
                                 "(central surface-brightness inversion: flux "
                                 "misallocation or bulge/disk identity swap)"})
    if m_agn is not None and m_bulge is not None and m_agn >= m_bulge:
        checks.append({"severity": "hard", "check": "mu0_order",
                       "detail": f"mu0_agn={m_agn:.2f} >= mu0_bulge={m_bulge:.2f} "
                                 "(the AGN peak is not the brightest central source "
                                 "— A_psf proxy)"})
    if m_bar is not None and m_disk is not None and m_bar > m_disk / MU0_BAR_TOL:
        checks.append({"severity": "hard", "check": "mu0_order",
                       "detail": f"mu0_bar={m_bar:.2f} > mu0_disk/{MU0_BAR_TOL:g}="
                                 f"{m_disk / MU0_BAR_TOL:.2f} (bar far fainter than "
                                 "the disk at centre — diffuse-bar identity question)"})

    # ---- flux share: fractions of the total model light
    lum_list = [10.0 ** (-0.4 * _f(c.get("mag")))
                for c in inv if _f(c.get("mag")) is not None]
    named_lum = {c.get("name"): 10.0 ** (-0.4 * _f(c.get("mag")))
                 for c in inv if c.get("name") in ("bar", "lens")
                 and _f(c.get("mag")) is not None}
    tot = sum(lum_list)
    if tot > 0 and named_lum:
        fb = named_lum.get("bar", 0.0) / tot
        fl = named_lum.get("lens", 0.0) / tot
        if "bar" in named_lum and not (BAR_FLUX_WIN[0] <= fb <= BAR_FLUX_WIN[1]):
            checks.append({"severity": "note", "check": "flux_share",
                           "detail": f"bar flux fraction {fb * 100:.1f}% outside "
                                     f"{BAR_FLUX_WIN[0] * 100:.0f}-{BAR_FLUX_WIN[1] * 100:.0f}%"})
        if "lens" in named_lum and not (LENS_FLUX_WIN[0] <= fl <= LENS_FLUX_WIN[1]):
            checks.append({"severity": "note", "check": "flux_share",
                           "detail": f"lens flux fraction {fl * 100:.1f}% outside "
                                     f"{LENS_FLUX_WIN[0] * 100:.0f}-{LENS_FLUX_WIN[1] * 100:.0f}%"})
        if "bar" in named_lum and "lens" in named_lum and fl >= fb:
            checks.append({"severity": "hard", "check": "flux_share",
                           "detail": f"lens flux fraction {fl * 100:.1f}% >= bar "
                                     f"{fb * 100:.1f}% (a lens must not outshine its bar)"})

    # ---- zombies (note only — never a removal ground by itself)
    for z in state.get("zombies", []) or []:
        checks.append({"severity": "note", "check": "zombie",
                       "detail": f"{z}: flux < 0.5% of the brightest component "
                                 "(zombie flag — dedup criterion only)"})

    # ---- administrative disable filter (GALMCP_DISABLE_MECH_CHECKS): drop
    # the configured families before anything consumes the table (merge,
    # record_fit fail-safe, prompt digest).
    disabled = _disabled_checks()
    if disabled:
        checks = [c for c in checks
                  if c.get("severity") not in disabled.get(c.get("check"), ())]

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
