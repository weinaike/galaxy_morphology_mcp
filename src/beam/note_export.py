"""Programmatic working_note.md export from the beam state graph.

The graph is the single source of truth; the working note is its human- and
verifier-readable projection, following the §Multi-Branch working_note
Template of workflow_galfit.md (header + overwrite snapshot + append-only
ledgers/branch sections/failure archive/decision log).
"""

from __future__ import annotations

import os

from beam.digest import _comp_line, _fmt  # reuse formatting helpers


def build_working_note(g, galaxy_dir: str) -> str:
    meta = g.g.graph.get("meta", {})
    stage1 = g.g.graph.get("stage1", {})
    best = g.g.graph.get("best_state")
    counters = g.counters()
    pending = g.g.graph.get("pending", {})

    lines: list[str] = []
    gid = os.path.basename(os.path.abspath(galaxy_dir))
    lines.append(f"# Galaxy {gid} Beam Search Working Note (GALFIT single-band, mechanised v2)")
    lines.append("")
    lines.append("## Basic information")
    region = meta.get("fit_region")
    lines.append(f"- Galaxy ID: {gid}; fitting region H): "
                 f"{[_fmt(v) for v in region] if region else '?'}; single band")
    lines.append(f"- Beam width W = {meta.get('W', 5)}; global budget N_max = "
                 f"{meta.get('N_max', 15)}; per-combination cap = {meta.get('per_combo_cap', 4)}")
    lines.append("- PA convention: N=+Y (+Y up = 0° counterclockwise, same frame as the "
                 "feedme 10) row); unit contract: pixels only (expdisk Rs conversion "
                 "Re=1.68·Rs applied by code)")
    lines.append(f"- PSF: FWHM = {_fmt(meta.get('psf_fwhm_px'))} px, "
                 f"A_psf = {_fmt(meta.get('a_psf_px2'))} px² (measured once by beam_init)")
    lines.append(f"- Stage-1 conclusions: {(stage1.get('morphology') or '(none recorded)').strip()}")
    d = stage1.get("detect_bar_lopsidedness") or {}
    bar, lop = d.get("bar") or {}, d.get("lopsidedness") or {}
    if bar.get("detected"):
        lines.append(f"  - bar detected: PA={_fmt(bar.get('pa_deg'))}°, b/a={_fmt(bar.get('b_over_a'))} "
                     "(N=+Y; initial guess only — the fit is the arbiter)")
    else:
        lines.append("  - bar not detected (zero evidence, non-determinative)")
    if lop.get("detected"):
        lines.append(f"  - lopsidedness detected: phase={_fmt(lop.get('phase_deg'))}°")
    else:
        lines.append("  - lopsidedness not detected (zero evidence, non-determinative)")
    for tc in g.g.graph.get("temporary_constraints", []):
        if tc.get("active", True):
            lines.append(f"- Temporary constraint ACTIVE {tc.get('issued', '?')}: "
                         f"{tc.get('text', '')} (forbidden: "
                         f"{', '.join(tc.get('forbid_structures', [])) or '-'})")

    # ------------------------------------------------ snapshot (overwrite section)
    lines.append("")
    lines.append("## Beam-state snapshot (overwritten after each round; do not append)")
    lines.append("### Current best s*")
    if best:
        s = g.state(best)
        m = s.get("metrics", {})
        lines.append(f"- Branch / round: {best} (depth {s.get('depth')}, combo "
                     f"{s.get('combo_key')})")
        lines.append(f"- reduced_χ² / BIC: chisq1d_nu={_fmt(m.get('chisq1d_nu'))} / "
                     f"BIC_eff={_fmt(m.get('bic_eff'))} (BIC convention: bic_eff first)")
        v = s.get("verdict") or {}
        lines.append(f"- VLM physicality verdict: {v.get('verdict', '(pending)')}"
                     + (f" ({'; '.join(v.get('failed_checks', []))[:200]})"
                        if v.get("failed_checks") else ""))
        lines.append(f"- Corresponding archives directory: {s.get('archive_dir')}")
        lines.append(f"- Corresponding feedme: {(s.get('artifacts', {}) or {}).get('feedme')}")
    else:
        lines.append("- (no PASS state settled yet)")
    lines.append("")
    lines.append("### Current priority queue Q (by effective score, at most W entries)")
    lines.append("| rank | action_id | parent | action tag | σ | g | flags | age |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, aid in enumerate(g.pending_queue(), start=1):
        rec = pending.get(aid) or {}
        flags = ",".join(sorted(k for k, v in (rec.get("code_flags") or {}).items() if v)) or "-"
        lines.append(f"| {i} | {aid} | {rec.get('parent')} | "
                     f"{rec.get('expected_behavior_tag')} | {_fmt(rec.get('sigma'), 3)} | "
                     f"{_fmt(rec.get('score'), 3)} | {flags} | {rec.get('age_counter', 0)} |")
    lines.append("")
    lines.append("### Global fit counters")
    lines.append(f"- n = {g.n_total()} / {meta.get('N_max', 15)} (executed "
                 f"{counters.get('n_executed', 0)}, failed {counters.get('n_failed', 0)})")
    lines.append(f"- stagnation = {counters.get('stagnation', 0)} / "
                 f"{meta.get('stagnation_max', 5)}; global_iter_id = "
                 f"{counters.get('global_iter_id', 0)}")
    combos = "; ".join(f"{k}:{v}" for k, v in sorted(g.combo_counts().items())) or "-"
    lines.append(f"- Combos executed: {combos}")
    term = g.termination_check()
    lines.append(f"- Termination check: stop={term['stop']}, conditions={term['conditions']}, "
                 f"never-executed blockers={term['never_executed_blockers']}")

    lines.append("")
    lines.append("### Execution trajectory (traversal order, one row per iter id)")
    lines.append("| iter | state | parent | action [tag] | combo | BIC_eff | verdict | best |")
    lines.append("|---|---|---|---|---|---|---|---|")
    best_marker = "**s\\***"
    for e in g.traversal():
        state_txt = e["label"] or "(crash)"
        action_txt = (f"{e['action']} [{e['tag']}]" if e["action"]
                      else "(root feedme first fit)" if e["iter"] == 0 else "-")
        lines.append(f"| {e['iter']} | {state_txt} | {e['parent'] or '-'} | {action_txt} | "
                     f"{e['combo'] or '-'} | {_fmt(e['bic_eff'])} | {e['verdict'] or '-'} | "
                     f"{best_marker if e['is_best'] else ''} |")

    # ------------------------------------------------------------- state ledgers
    states = sorted(((lbl, a) for lbl, a in g.g.nodes(data=True)
                     if a.get("global_iter_id", 0) > 0),
                    key=lambda kv: kv[1]["global_iter_id"])
    lines.append("")
    lines.append("## State ledgers (append-only)")
    lines.append("### Input ledger (canonical forms of the executed feedmes)")
    lines.append("| round | input signature (structure × toggles × cons bands, px) | feedme |")
    lines.append("|---|---|---|")
    for lbl, a in states:
        inv = a.get("input_inventory") or a.get("inventory") or []
        sig = "; ".join(_comp_line(c) for c in inv) or "(empty)"
        bands = a.get("cons_effective") or {}
        band_txt = ("; ".join(f"{k}:[{_fmt(v[0])},{_fmt(v[1])}]"
                              for k, v in sorted(bands.items())[:12])) if bands else "-"
        feedme = os.path.basename((a.get("artifacts", {}) or {}).get("feedme") or "?")
        lines.append(f"| {lbl} | {sig} | bands: {band_txt} | {feedme} |")
    lines.append("")
    lines.append("### Result ledger (fitted-state signatures + outcomes)")
    lines.append("| round | state signature (px) | BIC_eff | verdict | zombie/bounds |")
    lines.append("|---|---|---|---|---|")
    for lbl, a in states:
        inv = a.get("inventory") or []
        sig = "; ".join(_comp_line(c) for c in inv) or "(empty)"
        v = (a.get("verdict") or {}).get("verdict", "(pending)")
        notes = []
        if a.get("zombies"):
            notes.append("zombie:" + ",".join(a["zombies"]))
        conv = ((a.get("metrics", {}).get("convergence") or {}).get("flag"))
        if conv and conv != "ok":
            notes.append(f"[{conv}]")
        lines.append(f"| {lbl} | {sig} | {_fmt((a.get('metrics') or {}).get('bic_eff'))} | "
                     f"{v} | {'; '.join(notes) or '-'} |")
    lines.append("")
    lines.append("### Rollback edges (closed-form equivalences)")
    rb = [e for e in g.g.graph.get("decision_log", [])
          if e.get("kind") == "r2-zero-cost-rollback"]
    if rb:
        for e in rb:
            lines.append(f"- {e.get('from')} --[{e.get('tag', '')}]--> ≡{e.get('rollback_to')}")
    else:
        lines.append("- (none)")

    # ------------------------------------------------------------ branch rounds
    branches: dict[str, list] = {}
    for lbl, a in states:
        branches.setdefault(lbl.split(".")[0], []).append((lbl, a))
    for branch, rounds in sorted(branches.items()):
        lines.append("")
        lines.append(f"## Branch {branch}")
        for lbl, a in rounds:
            _round_section(lines, g, lbl, a, pending)

    # ---------------------------------------------------------- failure archive
    lines.append("")
    lines.append("## Branch: failure archive")
    failed = [(aid, r) for aid, r in pending.items() if r.get("status") == "failed"]
    if failed:
        for aid, r in failed:
            lines.append(f"### {aid} (parent = {r.get('parent')}, failed)")
            lines.append(f"- Action tag: {r.get('expected_behavior_tag')}")
            lines.append(f"- Failure reason: {r.get('discard_reason')}")
    else:
        lines.append("- (none)")

    # ------------------------------------------------------------- decision log
    lines.append("")
    lines.append("## Cross-branch decision log (append-only)")
    for e in g.g.graph.get("decision_log", []):
        lines.append(f"- [{e.get('at', '')}] {e.get('kind')}: "
                     + ", ".join(f"{k}={v}" for k, v in e.items()
                                 if k not in ("at", "kind")))
    return "\n".join(lines) + "\n"


def _round_section(lines: list[str], g, lbl: str, a: dict, pending: dict) -> None:
    m = a.get("metrics", {})
    v = a.get("verdict") or {}
    feedme = os.path.basename((a.get("artifacts", {}) or {}).get("feedme") or "?")
    action_in = a.get("action_in") or "(first fit of the root feedme)"
    lines.append(f"### {lbl} (fit #{a.get('global_iter_id')}, feedme: {feedme})")
    lines.append(f"- Action: {action_in}"
                 + (f" [{(pending.get(action_in) or {}).get('expected_behavior_tag')}]"
                    if action_in in pending else ""))
    primitives = (pending.get(action_in) or {}).get("primitives")
    if primitives:
        lines.append(f"- Primitives: `{primitives}`")
    inv = a.get("inventory") or []
    lines.append("- Components: " + ("; ".join(_comp_line(c) for c in inv) or "(empty)"))
    lines.append(f"- reduced_χ² / BIC: chisq1d_nu={_fmt(m.get('chisq1d_nu'))}, "
                 f"chi2_nu={_fmt(m.get('chi2_nu'))} / BIC_eff={_fmt(m.get('bic_eff'))}")
    if a.get("zombies"):
        lines.append(f"- Zombies: {', '.join(a['zombies'])}")
    verdict_txt = v.get("verdict", "(pending survey)")
    fc = v.get("failed_checks") or []
    if verdict_txt == "FAIL":
        veto = " [mechanical veto: code-side hard check]" if v.get("mech_veto") else ""
        vlm_original = a.get("verdict_vlm") or {}
        if v.get("mech_veto") and vlm_original:
            veto += (f" (surveyor originally said {vlm_original.get('verdict')}"
                     + (f": {'; '.join(str(x) for x in vlm_original.get('failed_checks', []))}"
                        if vlm_original.get("failed_checks") else "") + ")")
        lines.append(f"- **Physicality verdict: FAIL (physicality veto — barred from "
                     f"s\\*)**{veto}: {'; '.join(str(x) for x in fc)}")
    else:
        notes = [str(x) for x in fc
                 if str(x).startswith(("[note]", "[mech-note]"))]
        lines.append(f"- VLM physicality verdict: {verdict_txt}"
                     + (f" (notes: {'; '.join(notes)})" if notes else ""))
    # candidates generated from this round + their fate
    children = [(aid, r) for aid, r in pending.items()
                if r.get("source_round") == lbl]
    if children:
        fate = []
        for aid, r in children:
            fate.append(f"{aid}[{r.get('expected_behavior_tag')}→{r.get('status')}"
                        + (f":{r.get('discard_reason')}" if r.get("discard_reason") else "")
                        + (f", g={_fmt(r.get('score'), 3)}" if r.get("score") else "") + "]")
        lines.append("- survey candidates and fate: " + ", ".join(fate))
    lines.append("")


def export_working_note(g, galaxy_dir: str) -> str:
    """Write <galaxy_dir>/working_note.md from the graph; returns the path."""
    path = os.path.join(galaxy_dir, "working_note.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_working_note(g, galaxy_dir))
    return path
