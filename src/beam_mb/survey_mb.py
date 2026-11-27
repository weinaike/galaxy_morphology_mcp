"""Multi-band surveyor inputs: system message, summary content, opening
references and the opening-round candidate-generation override (P4b).

The mb surveyor differs from the galfit one in exactly three places:
* the **system message keeps the sky-PA/compass convention** (the base
  residual-analysis message targets the multi-band flow; the galfit
  N=+Y patch is NOT applied);
* the **summary content** renders the per-band fitted table — the
  component_attributes.txt (per-band px + arcsec + sky-PA) when the
  runner produced it, else the gssummary free+fixed parameter table;
* the **unit contract** (px tagged with panel_band, arcsec forbidden) —
  carried by beam_survey_prompt_galfits.md.
"""

from __future__ import annotations

import os


def mb_system_message() -> str:
    """Base residual-analysis system message, sky-PA convention intact."""
    from tools import prompt

    return prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE


def mb_summary_content(gssummary: str, component_attributes: str = "") -> str:
    """Per-component fitted-parameter summary for the stateless VLM.

    Prefers component_attributes.txt (per-band px + arcsec + sky-PA +
    per-band mag — exactly what the panels show); falls back to the
    gssummary parameter table (free + fixed sections, structural values
    arcsec, per-band logNorm) when the runner did not produce it.
    """
    if component_attributes and os.path.exists(component_attributes):
        try:
            with open(component_attributes, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    if not gssummary or not os.path.exists(gssummary):
        return "(no fitted summary available)"
    lines: list[str] = ["Fitted parameters (gssummary; structural values "
                        "arcsec / sky-PA, per-band logNorm):"]
    capture = False
    with open(gssummary, encoding="utf-8") as f:
        for raw in f:
            s = raw.rstrip("\n")
            if s.startswith("# free parameters") or \
                    s.startswith("# fixed parameters"):
                lines.append("")
                lines.append(s)
                capture = True
                continue
            if s.startswith("#"):
                continue
            if capture and s.strip():
                lines.append(s)
    return "\n".join(lines)


def mb_opening_ref_lines(root_lyric: str) -> str:
    """Round-0 seeding references from the root lyric's initial
    five-tuples (arcsec structural values + vary flags)."""
    try:
        from beam_mb.lyric_doc import parse_lyric_doc

        doc = parse_lyric_doc(root_lyric)
        parts = []
        for c in doc.components:
            f = {p: c.slot_five(p) for p in ("re", "n", "pa", "q")}
            x = (c.value(3) or [None])[0]
            y = (c.value(4) or [None])[0]
            parts.append(
                f"{c.name} ({c.ptype}): Re={f['re'][0] if f['re'] else '?'}\", "
                f"n={f['n'][0] if f['n'] else '?'}"
                f"{' (fixed)' if f['n'] and not f['n'][4] else ''}, "
                f"q={f['q'][0] if f['q'] else '?'}, "
                f"sky-PA={f['pa'][0] if f['pa'] else '?'}°, "
                f"centre-offset=({x}, {y})\"")
        for lbl, nb in doc.nuclei.items():
            parts.append(f"AGN '{nb.value(1)}' (N block) at band set")
        return "; ".join(parts) or "(root lyric carries no components)"
    except Exception as e:
        return f"(root lyric reference parse failed: {e})"


MB_OPENING_TURN2 = (
    "## OPENING ROUND (round-0; the parent is the unfitted root A.0)\n"
    "Return 1-2 RANKED opening skeleton candidates. Each candidate uses "
    "EXACTLY ONE primitive: {\"op\": \"skeleton\", \"components\": "
    "[<add-shaped declarations for the WHOLE luminous model>]}. The "
    "alphabet: singlesersic ALONE (elliptical opening, n free) XOR any "
    "subset of {disk, bulge, bar} that CONTAINS the disk (a decomposition "
    "is disk-bearing by definition; bulge n is code-free with prior 4, "
    "bar n=0.5 code-fixed, disk n=1 code-fixed). lens / outerdisk / "
    "edgedisk / AGN / companion / Fourier variants are OUTSIDE the "
    "opening alphabet (AGN enters via add(agn) once a fit exists and the "
    "point-source question arises). Seed every re_px/q/pa_sky_deg/dx_px/"
    "dy_px from your panel reading and name the panel_band of every "
    "measurement; positions are centre-relative (dx right, dy up). "
    "Structural parameters are shared across bands — declare each "
    "component ONCE, measured on your best panel. Order the candidates "
    "array by confidence (most promising FIRST): your array order is the "
    "ranking — the code assigns pred_bic mechanically from it (i-(K-1)) "
    "and the queue runs ascending, so your top candidate becomes the "
    "first fit; the pred_bic values you declare are ignored in this round."
)
