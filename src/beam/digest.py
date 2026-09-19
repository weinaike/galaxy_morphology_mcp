"""Programmatic generation of the surveyor's global/local state descriptions
and the pending-queue digest — replaces the orchestrator's hand distillation
of working_note.md (the graph is the single source of truth).

``build_local_state_description`` also returns the numeric-trigger flags that
arm the enqueue floors (disk_re_bottleneck / lens_relax_d / flat_bulge_bar).
"""

from __future__ import annotations

import math
import re

from beam.signature import normalize_inventory

_COMPANION_RE = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)
CENTRAL = {"disk", "edgedisk", "bulge", "bar", "lens"}


def _fmt(v, digits=4):
    if v is None:
        return "?"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.{digits}g}"


def _comp_line(c: dict) -> str:
    name = c.get("name") or "?"
    re_eff = c.get("re_effective")
    if re_eff is None and c.get("re") is not None:
        # raw parse_components shape: 're' is the 4) row value (Rs for
        # expdisk/edgedisk) — display the effective radius (px contract)
        re_eff = c["re"] * (1.68 if (c.get("type") or "").lower()
                            in ("expdisk", "edgedisk") else 1.0)
    parts = [f"Re={_fmt(re_eff)}px"]
    if c.get("mag") is not None:
        parts.append(f"M={_fmt(c.get('mag'), 4)}")
    if c.get("n") is not None:
        tog = (c.get("toggles") or {}).get("n", 1)
        parts.append(f"n={_fmt(c.get('n'), 3)}{'f' if not tog else ''}")
    if c.get("q") is not None:
        parts.append(f"q={_fmt(c.get('q'), 3)}")
    if c.get("pa") is not None:
        parts.append(f"PA={_fmt(c.get('pa'), 4)}")
    return f"{name}({','.join(parts)})" if parts else name


# --------------------------------------------------------------------- global
def build_global_state_description(g) -> str:
    meta = g.g.graph.get("meta", {})
    lines: list[str] = []

    lines.append("[Meta] Pixel contract: every Re/position below is px, same frame as the "
                 "panels and the feedme rows (no unit conversion exists; arcsec forbidden).")
    lines.append(f"      PSF: FWHM={_fmt(meta.get('psf_fwhm_px'))} px, "
                 f"A_psf={_fmt(meta.get('a_psf_px2'))} px^2 (measured once before the first fit; "
                 "companion psf-vs-sersic area rule).")
    if meta.get("fit_region"):
        r = meta["fit_region"]
        lines.append(f"      Fit region: x[{_fmt(r[0])},{_fmt(r[1])}] y[{_fmt(r[2])},{_fmt(r[3])}] "
                     f"(default Re cap = half region side).")
    best = g.g.graph.get("best_state")
    if best:
        bm = g.state(best).get("metrics", {})
        lines.append(f"      Current best s*: {best} (BIC_eff={_fmt(bm.get('bic_eff'))}, "
                     f"chisq1d_nu={_fmt(bm.get('chisq1d_nu'))}, combo={g.state(best).get('combo_key')}).")

    stage1 = g.g.graph.get("stage1", {})
    lines.append("")
    lines.append("[Stage-1 conclusions] " + _stage1_line(stage1))

    lines.append("")
    lines.append("[State ledger (px)] one line per fitted state — compare your expected_C' "
                 "line by line before generating each candidate:")
    states = sorted(
        ((s, a) for s, a in g.g.nodes(data=True) if a.get("global_iter_id", 0) > 0),
        key=lambda kv: kv[1]["global_iter_id"])
    if not states:
        lines.append("  (no fitted states yet)")
    for label, attrs in states:
        inv = normalize_inventory(attrs.get("inventory", []))
        combo = "+".join(_comp_line(c) for c in inv) or "(empty)"
        metrics = attrs.get("metrics", {})
        verdict = (attrs.get("verdict") or {}).get("verdict", "-")
        notes = []
        if attrs.get("zombies"):
            notes.append("zombie:" + ",".join(attrs["zombies"]))
        conv = (metrics.get("convergence") or {}).get("flag")
        if conv and conv != "ok":
            notes.append(f"[{conv}]")
        lines.append(f"  | {label} | {combo} | BIC_eff={_fmt(metrics.get('bic_eff'))} | "
                     f"{verdict} | {'; '.join(notes) or '-'}")

    lines.append("")
    lines.append("[Rollback edges] confirmed closed-form equivalences (a hit means zero "
                 "new information — do not re-propose):")
    rb = [e for e in g.g.graph.get("decision_log", [])
          if e.get("kind") == "r2-zero-cost-rollback"]
    if not rb:
        lines.append("  (none)")
    for e in rb:
        lines.append(f"  - {e.get('from')} --{e.get('tag', '')}--> = {e.get('rollback_to')}")

    lines.append("")
    lines.append("[Verified basins] parameter ranges validated by fitting (cite, don't "
                 "re-measure, unless a re-verification signal fires):")
    basins = g.g.graph.get("verified_basins", [])
    lines.extend(f"  - {b}" for b in basins) if basins else lines.append("  (none)")

    lines.append("")
    lines.append("[Refuted hypotheses] direction | context | evidence | reopening condition:")
    refuted = g.g.graph.get("refuted_hypotheses", [])
    if not refuted:
        lines.append("  (none)")
    for r in refuted:
        lines.append(f"  - {r.get('tag') or r.get('action')} | {r.get('state')} | "
                     f"{r.get('evidence')} | {r.get('reopening')}")

    lines.append("")
    counts = g.combo_counts()
    combo_txt = "; ".join(f"{k}:{v}/4" for k, v in sorted(counts.items())) or "-"
    lines.append(f"[Budget] n={g.n_total()}/{meta.get('N_max', 15)} fits spent, "
                 f"stagnation={g.counters().get('stagnation', 0)}/{meta.get('stagnation_max', 5)}, "
                 f"queue={len(g.pending_queue())}/{meta.get('W', 5)}. "
                 f"Combos executed: {combo_txt}.")

    tcs = [t for t in g.g.graph.get("temporary_constraints", []) if t.get("active", True)]
    lines.append("")
    lines.append("[Temporary constraints] session-scoped user directives (binding while ACTIVE):")
    if not tcs:
        lines.append("  (none active)")
    for t in tcs:
        lines.append(f"  - ACTIVE {t.get('issued', '?')} ({t.get('text', '')}) "
                     f"forbidden structures: {', '.join(t.get('forbid_structures', [])) or '-'}")

    return "\n".join(lines)


def _stage1_line(stage1: dict) -> str:
    morph = (stage1.get("morphology") or "").strip() or "(no morphology summary recorded)"
    d = stage1.get("detect_bar_lopsidedness") or {}
    bar = d.get("bar") or {}
    lop = d.get("lopsidedness") or {}
    bar_txt = (f"bar detected: PA={_fmt(bar.get('pa_deg'))}deg b/a={_fmt(bar.get('b_over_a'))}"
               if bar.get("detected") else
               "bar not detected (zero evidence, non-determinative)")
    lop_txt = (f"lopsidedness detected: phase={_fmt(lop.get('phase_deg'))}deg"
               if lop.get("detected") else
               "lopsidedness not detected (zero evidence, non-determinative)")
    return f"{morph}; {bar_txt}; {lop_txt}."


# ---------------------------------------------------------------------- local
def build_local_state_description(g, state_label: str,
                                  injected: dict | None = None) -> tuple[str, dict]:
    """Objective description of the state being surveyed + trigger flags."""
    state = g.state(state_label)
    inv = normalize_inventory(state.get("inventory", []))
    injected = injected or {}
    triggers: dict[str, bool] = {}
    lines: list[str] = []

    lines.append(f"Parent state {state_label} (depth {state.get('depth')}, "
                 f"combo {state.get('combo_key')}):")
    lines.append("  inventory: " + ("; ".join(_comp_line(c) for c in inv) or "(empty)"))
    metrics = state.get("metrics", {})
    conv = (metrics.get("convergence") or {}).get("flag", "ok")
    if conv != "ok":
        frozen = (metrics.get("convergence") or {}).get("frozen_free_params", [])
        lines.append(f"  [sub-converged] frozen free params: {', '.join(frozen) or '-'} "
                     "(this round is inadmissible as refutation evidence; corrected "
                     "re-run prescriptions: band re-encode / init adjust / mag reapportion)")

    # ---- bound-hit list (effective bands)
    hits = _scan_hits(g, state)
    lines.append("")
    if hits:
        lines.append("Bound-hit parameter list (objective facts, effective bands; no "
                     "candidate-direction suggestion attached):")
        for h in hits:
            note = f"; {h['note']}" if h.get("note") else ""
            lines.append(f"  - {h['comp']} {h['param']}={_fmt(h['fitted'])} at the "
                         f"{h['direction']} bound [{_fmt(h['band'][0])},{_fmt(h['band'][1])}] "
                         f"({h['band_type']}, provenance={h['provenance']}, "
                         f"exempt={h['exempt']}){note}")
    else:
        lines.append("Bound-hit parameter list: none (no parameter sits at an effective "
                     "band edge; unbounded parameters cannot hit).")

    # ---- deterministic mechanical check table (AUTHORITATIVE — code-computed)
    mech = state.get("mech_checks") or []
    lines.append("")
    if mech:
        lines.append("Mechanical physicality check table (computed by code; AUTHORITATIVE "
                     "— do not reclassify, contradict or omit these entries in your "
                     "verdict; they are already counted by the code-side merge):")
        for m in mech:
            lines.append(f"  - [{m.get('severity')}] {m.get('check')}: {m.get('detail')}")
    else:
        lines.append("Mechanical physicality check table: no numeric hard check fired "
                     "(Re chain, axis ratios, containment, concentricity, degeneracy, "
                     "bound pins all clean).")

    # ---- numeric triggers (facts only)
    facts: list[str] = []
    facts.extend(_companion_check(inv))
    facts.extend(_lens_inflation(g, state, inv, triggers))
    facts.extend(_disk_re_bottleneck(g, state, inv, triggers, hits))
    facts.extend(_flux_misallocation(g, state, inv))
    facts.extend(_flat_bulge_trigger(inv, triggers))
    lines.append("")
    if facts:
        lines.append("Numeric-rule delegations (objective facts; generation decisions are "
                     "yours per the trigger rules):")
        lines.extend(f"  - {f}" for f in facts)
    else:
        lines.append("Numeric-rule delegations: none fired this round.")

    # ---- injectable visual/orchestrator fields
    lines.append("")
    lines.append("Current-round visual assessments (fill from the panels; 'not assessed' "
                 "means no data yet):")
    for key, label in (("outer_residual_sign",
                        "outer residual sign at r>2*Re_disk (Data brighter / Model brighter / flat)"),
                       ("central_spike", "central <5px 1D spike present (Y/N + amplitude)"),
                       ("two_peaked_original", "two-peaked original-image centre (Y/N)"),
                       ("embedded_hotspot", "fixed-position compact one-sided residual hot spot (coords)"),
                       ("orchestrator_note", "orchestrator note")):
        lines.append(f"  - {label}: {injected.get(key, 'not assessed')}")

    # ---- central-spike AGN trigger (pre-call half; survey.py parses the
    # VLM's own Phase-1 assessment post-call). An orchestrator-injected
    # affirmative central_spike with no AGN in the inventory arms the same
    # floor_agn_spike flag (KILOGAS_231 / Plate0284 retrospective).
    _inj = str(injected.get("central_spike", "")).lower()
    _names = {(c.get("name") or "").lower() for c in inv}
    if "agn" not in _names and _inj and not any(
            m in _inj for m in ("false", "no", "none", "absent", "not assessed", "weak")):
        triggers["central_spike_agn"] = True

    # ---- consume-once absence notices queued by the previous round's
    # candidate-absence watchdog (survey.py): facts for self-correction,
    # never direction mandates beyond the trigger rules already in the prompt
    pending_notes = g.g.graph.get("survey_notes") or []
    if pending_notes:
        lines.append("")
        for nt in pending_notes:
            lines.append(f"  {nt}")
        g.g.graph["survey_notes"] = []

    return "\n".join(lines), triggers


def _scan_hits(g, state) -> list[dict]:
    from beam.physicality import scan_state_bound_hits

    return scan_state_bound_hits(g, state)


def _re_cap(g, state, number: int) -> tuple[float, float] | None:
    return (state.get("cons_effective") or {}).get(f"{number}.re")


def _default_cap(g) -> float | None:
    meta = g.g.graph.get("meta", {})
    region = meta.get("fit_region")
    if not region:
        return None
    side = max(region[1] - region[0] + 1, region[3] - region[2] + 1)
    return 0.5 * side


def _companion_check(inv) -> list[str]:
    facts = []
    main = [c for c in inv if not _COMPANION_RE.match(c.get("name") or "")]
    comps = [c for c in inv if _COMPANION_RE.match(c.get("name") or "")]
    if not comps or not main:
        return facts
    mags = [c.get("mag") for c in inv if c.get("mag") is not None]
    brightest = min(mags)
    for c in comps:
        if c.get("mag") is None:
            continue
        dmag = c["mag"] - brightest
        ratio = 100 * 10 ** (-0.4 * dmag)
        if ratio <= 1.0:
            facts.append(f"Companion condition A hit (objective facts): {c['name']} flux "
                         f"ratio={ratio:.2f}%, ΔMag={dmag:.2f} vs the brightest component.")
    return facts


def _lens_inflation(g, state, inv, triggers) -> list[str]:
    facts = []
    lens = next((c for c in inv if c.get("name") == "lens"), None)
    disk = next((c for c in inv if c.get("name") in {"disk", "edgedisk"}), None)
    if lens is None or disk is None:
        return facts
    re_lens = lens.get("re_effective")
    re_disk = disk.get("re_effective")
    cap = _re_cap(g, state, lens.get("number")) if lens.get("number") else None
    cap_hit = cap is not None and re_lens is not None and re_lens >= 0.98 * cap[1]
    inversion = re_lens is not None and re_disk is not None and re_lens >= re_disk
    if not cap_hit and not inversion:
        return facts
    ratio = re_lens / re_disk if re_lens and re_disk else None
    dmag = (lens["mag"] - disk["mag"]) if lens.get("mag") is not None and disk.get("mag") is not None else None
    default = _default_cap(g)
    self_imposed = cap is not None and default is not None and cap[1] < default - 1e-6
    degeneracy = (ratio is not None and ratio >= 0.85) or (dmag is not None and dmag <= 0.2)
    facts.append(f"Lens Re inflation signal (objective facts): lens_Re={_fmt(re_lens)}px "
                 f"[cap hit={'yes' if cap_hit else 'no'}; Re-order inversion="
                 f"{'yes' if inversion else 'no'}], disk_Re={_fmt(re_disk)}px, "
                 f"Re_lens/Re_disk={_fmt(ratio)}, lens_M−disk_M={_fmt(dmag)}, "
                 f"bound provenance={'self-imposed' if self_imposed else 'original/unconstrained'}.")
    triggers["lens_relax_d"] = bool(cap_hit and self_imposed and not degeneracy)
    return facts


def _disk_re_bottleneck(g, state, inv, triggers, hits) -> list[str]:
    facts = []
    ext = next((c for c in inv if c.get("name") in {"lens", "bar"}), None)
    disk = next((c for c in inv if c.get("name") in {"disk", "edgedisk"}), None)
    if ext is None or disk is None:
        return facts
    cap = _re_cap(g, state, ext.get("number")) if ext.get("number") else None
    if cap is None or ext.get("re_effective") is None:
        return facts
    at_cap = ext["re_effective"] >= 0.98 * cap[1]
    disk_cap = _re_cap(g, state, disk.get("number")) if disk.get("number") else None
    disk_hit = disk_cap is not None and disk.get("re_effective") and \
        disk["re_effective"] >= 0.98 * disk_cap[1]
    if not at_cap or disk_hit:
        return facts
    ratio = ext["re_effective"] / disk["re_effective"]
    dmag = (ext["mag"] - disk["mag"]) if ext.get("mag") is not None and disk.get("mag") is not None else None
    sub_a = ratio >= 0.85
    sub_b = dmag is not None and dmag <= 0.2
    if not (sub_a or sub_b):
        return facts
    facts.append(f"Disk-Re bottleneck signal (objective facts): {ext['name']}_Re="
                 f"{_fmt(ext['re_effective'])}px at the cap (re_max={_fmt(cap[1])}px), "
                 f"Re_ratio={_fmt(ratio)} [{'≥0.85 Re-degeneracy hit' if sub_a else '<0.85'}], "
                 f"{ext['name']}_M={_fmt(ext.get('mag'))} [{'≤ disk_M+0.2 flux-approaching hit' if sub_b else 'above'}], "
                 f"disk_Re={_fmt(disk.get('re_effective'))}px (not bound-hit).")
    triggers["disk_re_bottleneck"] = True
    return facts


def _flux_misallocation(g, state, inv) -> list[str]:
    facts = []
    disk = next((c for c in inv if c.get("name") in {"disk", "edgedisk"}), None)
    if disk is not None and disk.get("mag") is not None:
        for c in inv:
            if c.get("name") in {"disk", "edgedisk"} or c.get("mag") is None:
                continue
            if _COMPANION_RE.match(c.get("name") or ""):
                continue
            dmag = c["mag"] - disk["mag"]
            if dmag > 3:
                ratio = 100 * 10 ** (-0.4 * dmag)
                facts.append(f"Flux-misallocation signal A: {c['name']} ΔMag vs disk="
                             f"{dmag:.2f} (flux ratio {ratio:.2f}%).")
    # signal B: monotonic mag drift across the result ledger
    series: dict[str, list] = {}
    for label, attrs in sorted(((s, a) for s, a in g.g.nodes(data=True)
                                if a.get("global_iter_id", 0) > 0),
                               key=lambda kv: kv[1]["global_iter_id"]):
        for c in normalize_inventory(attrs.get("inventory", [])):
            if c.get("mag") is not None:
                series.setdefault(c.get("name"), []).append(
                    (label, float(c["mag"])))
    for name, pts in series.items():
        if len(pts) < 3:
            continue
        tail = pts[-3:]
        d1, d2 = tail[1][1] - tail[0][1], tail[2][1] - tail[1][1]
        # monotone same-direction with average >=0.3 mag/round over >=2 rounds
        # (workflow's canonical example: 17.52 -> 17.96 -> 18.14 = +0.4/+0.2)
        if ((d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0)) and abs(d1 + d2) / 2 >= 0.3:
            facts.append(f"Flux-misallocation signal B: {name} mag trajectory "
                         f"{tail[0][1]:.2f}->{tail[1][1]:.2f}->{tail[2][1]:.2f} "
                         f"({tail[0][0]}/{tail[1][0]}/{tail[2][0]}; monotonic, "
                         f"avg {abs(d1 + d2) / 2:.2f} mag/round x2).")
    conv = (state.get("metrics", {}).get("convergence") or {}).get("flag")
    if conv and conv != "ok":
        facts.append("Flux-misallocation signal D: round flagged [sub-converged]; mag "
                     "initial-value reapportionment is available in the corrected re-run.")
    return facts


def _flat_bulge_trigger(inv, triggers) -> list[str]:
    bulge = next((c for c in inv if c.get("name") == "bulge"), None)
    disk = next((c for c in inv if c.get("name") in {"disk", "edgedisk"}), None)
    if bulge is None or disk is None:
        return []
    bq = bulge.get("q")
    dq = disk.get("q")
    pa_diff = None
    if bulge.get("pa") is not None and disk.get("pa") is not None:
        d = abs(bulge["pa"] - disk["pa"]) % 360.0
        pa_diff = min(d, 360.0 - d)
    n = bulge.get("n")
    n_free = (bulge.get("toggles") or {}).get("n", 1) == 1
    holds = (bq is not None and bq < 0.5 and pa_diff is not None and pa_diff > 20
             and (not n_free or (n is not None and 0.5 < n < 2.5))
             and dq is not None and dq > 0.5)
    triggers["flat_bulge_bar"] = bool(holds)
    return [f"Flat-Bulge->Bar trigger values: bulge_q={_fmt(bq)} [<0.5: "
            f"{'hit' if bq is not None and bq < 0.5 else 'no'}], |bulge_PA−disk_PA|="
            f"{_fmt(pa_diff)}deg [>20: {'hit' if pa_diff is not None and pa_diff > 20 else 'no'}], "
            f"bulge_n={_fmt(n)} (free={n_free}) [0.5<n<2.5: "
            f"{'hit' if n_free or (n is not None and 0.5 < n < 2.5) else 'no'}], "
            f"disk_q={_fmt(dq)} [>0.5: {'hit' if dq is not None and dq > 0.5 else 'no'}] "
            f"=> joint condition {'HOLDS' if holds else 'does not hold'}."]


# ----------------------------------------------------------------- queue digest
def build_queue_digest(g) -> str:
    pending = g.g.graph.get("pending", {})
    rows = []
    for i, aid in enumerate(g.pending_queue(), start=1):
        rec = pending.get(aid)
        if not rec or rec.get("status") != "pending":
            continue
        flags = ",".join(sorted(k for k, v in (rec.get("code_flags") or {}).items() if v))
        rows.append(f"  {i}. {aid} (parent {rec.get('parent')}, tag={rec.get('expected_behavior_tag')}, "
                    f"sigma={_fmt(rec.get('sigma'), 3)}, score={_fmt(rec.get('score'), 3)}, "
                    f"age={rec.get('age_counter', 0)}, flags=[{flags or '-'}])")
    if not rows:
        return "(queue empty — no pending candidates)"
    header = ("Pending queue (ordered by effective score; floor-protected entries carry "
              "flags and may not be demoted in queue_reorder):")
    return header + "\n" + "\n".join(rows)
