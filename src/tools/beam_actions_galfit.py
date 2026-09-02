"""
Beam Search candidate-action generator for GALFIT single-band fitting.

Mirrors the dispatch structure of the GalfitS `beam_actions.py` but operates on
GALFIT artefacts: the input `feedme` (structure + `# STRUCTURE:` naming
comments), the fitted parameter file (`galfit.NN`, where GALFIT drops the
naming comments — names are recovered by block order from the paired feedme)
and the markdown summary written by `run_galfit`. All sizes/positions are in
pixels on both the VLM side and the feedme side — no unit conversion anywhere.

Also provides `check_feedme_file`, the lightweight structural validator used by
the beam-search orchestrator before every `run_galfit` call (the single-band
counterpart of the GalfitS `check_lyric_file` gate).
"""

import os
import re
import uuid
from typing import Annotated, Any

import dotenv

from . import prompt
from .parse_feedme import parse_components, parse_feedme
from .render_original import effective_re, EXPDISK_RE_FACTOR

dotenv.load_dotenv()


# The shared system message (residual_analysis_message.md) is written for the
# GalfitS multi-band flow and mandates a true sky-PA convention ("north = 0°,
# align on the compass"). The single-band beam flow adopts the N=+Y contract
# instead (north assumed aligned with +Y, so sky-PA ≡ the feedme `10)` frame
# and VLM readings are written verbatim). Patch the PA clause at load time —
# the shared file itself stays untouched for GalfitS.
_PA_CLAUSE_RE = re.compile(r"(?m)^3\. \*\*PA convention\*\*:.*$")
_NPY_PA_CLAUSE = (
    "3. **PA convention (N=+Y contract)**: whenever a position angle (PA) is involved — "
    "including a component's PA, a Fourier mode's phase angle, or a suggested correction "
    "direction — always follow this workflow's **N=+Y contract**: **assume the image's "
    "North direction coincides with the +Y axis; 0° = the image's +Y axis (up), "
    "increasing counterclockwise** — read angles aligned with the image's vertical axis. "
    "This convention is numerically identical to the GALFIT feedme `10)` parameter row "
    "(\"+Y axis = 0°\"): PA values you read out or write are inserted **verbatim** by the "
    "orchestrator into the feedme, with no conversion. `detect_bar_lopsidedness` returns "
    "`bar.pa_deg` and `lopsidedness.phase_deg`, which can be used directly under this "
    "contract."
)


def _galfit_system_message() -> str:
    """Residual-analysis system message with the PA clause swapped to N=+Y."""
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE
    patched, n = _PA_CLAUSE_RE.subn(_NPY_PA_CLAUSE, system_message)
    if n == 0:
        # Clause wording drifted — fall back to appending the contract so the
        # beam prompt and the system message never disagree silently.
        system_message = system_message + "\n\n" + _NPY_PA_CLAUSE
    else:
        system_message = patched
    return system_message


def _fmt_toggle(toggles: dict, key: str) -> str:
    """Render a parameter's fit toggle as free/fixed (+toggle)."""
    v = toggles.get(key)
    if v is None:
        return "unknown"
    return "free" if v else "fixed"


def _parse_stats_from_summary(summary_file: str) -> str:
    """Extract the fitting-statistics lines from a run_galfit summary md.

    Matches the current markdown statistics table (``| χ²/ν ... |``) as well as
    legacy plain-text ``Chi^2/nu = ...`` lines embedded from fit.log.
    """
    try:
        with open(summary_file, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ""
    lines = []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("|") and any(
            token in s for token in ("χ²", "BIC", "Sky Background", "N_dof", "N_free",
                                     "PSF FWHM", "A_psf")
        ):
            lines.append(s)
        elif re.search(r"Chi\^?2", s) and "=" in s:
            lines.append(s)
    return "\n".join(lines)


def _build_summary_content_galfit(
    feedme_file: str,
    fitted_param_file: str,
    summary_file: str | None = None,
) -> str:
    """Render the per-component fitted-parameter summary for the stateless VLM.

    Component names come from the `# STRUCTURE:` comments of the input feedme
    (GALFIT output files drop them); fitted values come from `galfit.NN`.
    Everything is in pixels, matching the comparison-PNG panels one-to-one.
    """
    components = parse_components(fitted_param_file, name_file=feedme_file)
    if not components:
        return ""

    out: list[str] = []
    out.append("=== GALFIT single-band fitted-parameter summary (all values in pixels, same reference frame as the comparison panels) ===\n")
    for i, comp in enumerate(components, start=1):
        out.append(f"- Component {i}: {comp['name']}")
        out.append(f"  - Type: {comp['type']}")
        toggles = comp.get("toggles", {})
        out.append(
            f"  - xcen: {comp['x']:.3f} px ({_fmt_toggle(toggles, 'x')}); "
            f"ycen: {comp['y']:.3f} px ({_fmt_toggle(toggles, 'y')})"
        )
        out.append(f"  - Mag: {comp['mag']:.3f} ({_fmt_toggle(toggles, 'mag')})")
        if comp["type"] == "expdisk":
            rs = comp["re"]
            out.append(
                f"  - Rs: {rs:.3f} px ({_fmt_toggle(toggles, 're')}) "
                f"[effective radius Re = {EXPDISK_RE_FACTOR}·Rs = {effective_re(comp):.3f} px]"
            )
        else:
            out.append(f"  - Re: {comp['re']:.3f} px ({_fmt_toggle(toggles, 're')})")
        if comp.get("n") is not None:
            out.append(f"  - n: {comp['n']:.3f} ({_fmt_toggle(toggles, 'n')})")
        if comp["type"] != "psf":
            out.append(f"  - q (b/a): {comp['ba']:.3f} ({_fmt_toggle(toggles, 'ba')})")
            out.append(
                f"  - PA: {comp['pa']:.2f}° ({_fmt_toggle(toggles, 'pa')}) "
                "[N=+Y contract: 0° = +Y axis (up), counterclockwise; same frame as the feedme 10) row]"
            )
        else:
            out.append("  - (psf component: no n/q/PA/Re shape parameters)")
        out.append("")

    if summary_file:
        stats = _parse_stats_from_summary(summary_file)
        if stats:
            out.append("=== Fitting Statistics ===")
            out.append(stats)

    return "\n".join(out)


def generate_galfit_beam_actions(
    feedme_file: Annotated[str, "Absolute path to the parent state's input feedme file (carries the '# STRUCTURE:' component names)"],
    fitted_param_file: Annotated[str, "Absolute path to the parent state's fitted parameter file (galfit.NN, e.g. the archived output_param_file returned by run_galfit)"],
    comparison_file: Annotated[str, "Absolute path to the 2x3 comparison PNG produced by the parent state's run_galfit call"],
    summary_file: Annotated[str, "Absolute path to the markdown summary produced by the parent state's run_galfit call (only its statistics table is parsed; may be empty)"] = "",
    global_state_description: Annotated[str, "Cross-round stable facts for the stateless VLM, distilled by the orchestrator from working_note.md (NOT the raw note). Fixed schema per workflow_galfit.md 'Generation spec for global_state_description / local_state_description': [Meta (pixel contract)]/[Stage-1 conclusions]/[State ledger (px)]/[Rollback edges]/[Verified basins]/[Refuted hypotheses]/[Budget]. Keep <= ~50 lines."] = "",
    local_state_description: Annotated[str, "Current-round objective description: parent component inventory C + key params, bound-hit parameters (⚠️ + values vs .cons bounds), residual features, identity anomalies, and orchestrator numeric-rule delegations (companion flux check / disk-Re bottleneck / lens inflation / flat-bulge trigger values). Must NOT suggest candidate directions."] = "",
    branch_id: Annotated[str, "Current beam branch identifier (e.g. 'A', 'B'). Used in candidate action_ids."] = "A",
    parent_label: Annotated[str, "Parent round label inside the branch (e.g. 'A.3'). Used in candidate action_ids."] = "",
    depth: Annotated[int, "Depth of the parent state in the search tree. 1 = after the first fit on the input feedme. Controls candidate count via the prompt: depth=1 → 1-2 candidates (phase-one driven), depth=2 → 2-3, depth>=3 → 2-4."] = 1,
) -> dict[str, Any]:
    """Generate depth-aware candidate composite actions for GALFIT Beam Search.

    Called by the orchestrator agent after each successful ``run_galfit`` fit.
    The output is a structured Markdown list of candidates (with a leading
    ``## Physicality Verdict`` block); the agent performs semantic deduplication
    and global heuristic ranking (per workflow_galfit.md "Deduplication and Ranking") before
    enqueuing into the priority queue of width W=5.

    Unlike the GalfitS variant there is **no unit conversion**: the VLM reads
    pixels off the comparison PNG and those same pixel values are written
    verbatim into the child feedme.

    Returns
    -------
    dict with keys:
        - status: "success" | "failure"
        - candidates: str (the VLM-generated Markdown, only on success)
        - candidates_file: str | None (path to the saved .md file)
        - branch_id, parent_label, depth: echoed back for traceability
        - error: str (only on failure)
    """
    # ── Validate inputs ───────────────────────────────────────────────
    for path, label in [
        (feedme_file, "Feedme file"),
        (fitted_param_file, "Fitted parameter file"),
        (comparison_file, "Comparison file"),
    ]:
        if not os.path.exists(path):
            return {"status": "failure", "error": f"{label} not found: {path}"}
    if summary_file and not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    # ── Build parameter summary (names from feedme, values from galfit.NN)
    summary_content = _build_summary_content_galfit(feedme_file, fitted_param_file,
                                                    summary_file)
    if not summary_content:
        return {"status": "failure",
                "error": f"Failed to parse components from fitted param file: {fitted_param_file}"}

    # ── Build system message (residual analysis system message with the PA
    #    clause patched to the N=+Y contract + GALFIT component specification)
    system_message = _galfit_system_message()
    component_spec = prompt.get_component_specification_galfit()
    if component_spec:
        system_message = system_message + "\n\n" + component_spec

    # ── Build two-turn prompts from the galfit beam prompt phases ────
    turn1 = prompt.get_galfit_beam_visual_extraction(
        global_state_description=global_state_description,
    )
    turn2 = prompt.get_galfit_beam_candidate_generation(
        summary_content=summary_content,
        global_state_description=global_state_description,
        local_state_description=local_state_description,
        branch_id=branch_id,
        parent_label=parent_label,
        depth=depth,
    )

    # ── Dispatch to the configured analysis backend ───────────────────
    analysis_mode = os.environ.get("ANALYSIS_MODE", "vlm").lower()
    session_id = ""
    analysis: str | None = None
    error: str | None = None

    if analysis_mode == "cc":
        if not os.environ.get("CLAUDECODE_API_KEY"):
            return {"status": "failure",
                    "error": "ANALYSIS_MODE=cc requires CLAUDECODE_API_KEY to be set"}
        from .cc_analysis import run_component_analysis_cc
        session_id = str(uuid.uuid4())
        analysis, error = run_component_analysis_cc(
            system_prompt=system_message,
            analysis_prompts=[turn1, turn2],
            session_id=session_id,
        )

    elif analysis_mode == "acp":
        from .acp_analysis import run_component_analysis_acp
        # ACP uses a single mega-prompt concatenating all phases.
        mega = (
            "You are the candidate-action generator for the GALFIT single-band beam search. "
            "Given the GALFIT fitting results, produce the physicality verdict and 2-4 "
            "candidate composite actions through a two-phase chain of thought.\n\n"
            "During this process you may only use the read_file and write_file tools and no others.\n\n"
            f"[Input image file]: {os.path.abspath(comparison_file)}\n"
            "(2x3 layout: DATA LOW/HIGH DR | MODEL // RESIDUAL | RESIDUAL ZOOM | 1D SB profile)\n\n"
            "After reading the file above with read_file, carry out the following 2 phases in order. "
            "In Phase 1 you must remain strictly objective.\n\n"
            f"{turn1}\n\n{turn2}"
        )
        analysis, session_id, error = run_component_analysis_acp(
            system_prompt=system_message,
            analysis_prompts=[mega],
        )

    else:
        # vlm mode: multi-turn via OpenAI SDK
        from .openai_analysis import run_openai_analysis
        deferred_system = os.environ.get("VLM_DEFERRED_SYSTEM", "0") == "1"
        analysis, session_id, error, _timing = run_openai_analysis(
            system_prompt=system_message,
            analysis_prompts=[turn1, turn2],
            image_path=os.path.abspath(comparison_file),
            deferred_system=deferred_system,
        )

    if error:
        result: dict[str, Any] = {"status": "failure", "error": error}
        if session_id:
            result["session_id"] = session_id
        if analysis:
            result["partial_analysis"] = analysis
        return result

    assert analysis is not None, "analysis must not be None when error is None"

    # ── Persist the candidate list alongside other fitting artefacts ─
    base_name = os.path.splitext(os.path.basename(comparison_file))[0]
    if session_id:
        out_file = os.path.join(
            os.path.dirname(comparison_file),
            f"{base_name}_beam_actions_{branch_id}_{session_id}.md",
        )
    else:
        out_file = os.path.join(
            os.path.dirname(comparison_file),
            f"{base_name}_beam_actions_{branch_id}.md",
        )
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(analysis)
        print(f"[beam_actions_galfit] candidates saved to: {out_file}")
    except OSError as e:
        print(f"[beam_actions_galfit] warning: failed to save candidates file: {e}")
        out_file = None

    # ── Hand-off note for the orchestrator agent ──────────────────────
    handoff = (
        "\n\n---\n"
        "# Candidate-enqueue rules (orchestrator's responsibility)\n"
        "- First parse the `## Physicality Verdict` block at the top of the returned Markdown "
        "(verdict / failed_checks / swap_hint): a parent state with verdict=FAIL must not take part "
        "in s* updates (workflow_galfit.md Step e physicality gate), and protected recovery "
        "candidates must be generated per the Recovery Protocol for Non-Physical Results; when "
        "swap_hint=disk_bulge_swap, confirm the candidates include one in the disk <-> bulge "
        "label-swap direction. Record the verdict block verbatim in working_note; do not rewrite it.\n"
        "- Apply semantic deduplication to every candidate (compare against the (s_j, a_j) already "
        "in Q; if equivalent, keep the one with the higher g).\n"
        "- Equivalence criteria (all three must hold):\n"
        "  1) expected_C' is equivalent in physical identity (bulge/bar naming swaps allowed, etc.);\n"
        "  2) expected parameters agree within the tolerance bands (Re ±20%, n ±0.5, q ±0.1, "
        "PA ±10° (N=+Y contract), mag ±0.5);\n"
        "  3) expected_behavior_tag is identical.\n"
        "- Graph-search cycle detection (before semantic dedup, sharing the same signature criteria "
        "as the in-Q dedup): besides comparing against Q, compare against the **execution history** — "
        "(R1) transcribe the candidate into the canonical form of a hypothetical feedme (structure × "
        "free/fixed configuration × `.cons` bound bands × initial-value bands, all in px) and compare "
        "with the **input ledger**; equivalence within the bands -> discard (rerunning the same input "
        "yields no new information); (R2) for closed-form transitions (remove-only / parameter revert "
        "/ bound restoration), the resulting state can be projected exactly — compare the projected "
        "signature with the **result ledger** (zombie-aware: states differing only by [zombie] "
        "components are equivalent); exact hit -> do not enqueue (zero-cost rollback); structure-only "
        "match -> tag '[suspected near-duplicate]' (score 0 on the degeneracy-penalty dimension). "
        "Check every candidate's novelty_claim against the ledgers: a candidate equivalent in "
        "structure with no new parameter axis is dropped entirely.\n"
        "- Score each survivor on the six dimensions (residual-improvement potential, physical "
        "plausibility, path diversity, degeneracy penalty, historical consistency, BIC threshold), "
        "each 0-1, and take the weighted average as g: path diversity carries weight x2, the rest x1 "
        "(diversity is judged mainly against the globally executed candidates, secondarily against "
        "the elements in Q; repeated directions on the same component structure / parameter axis "
        "score low on this dimension); sort by g descending and truncate to W=5 when enqueuing into Q.\n"
        "- A candidate's σ is advisory only and is not used directly for ranking.\n"
    )

    return {
        "status": "success",
        "candidates": analysis + handoff,
        "candidates_file": out_file,
        "branch_id": branch_id,
        "parent_label": parent_label,
        "depth": depth,
        "session_id": session_id,
    }


def check_feedme_file(
    feedme_file: Annotated[str, "Absolute path to the GALFIT feedme configuration file"],
) -> dict[str, Any]:
    """Validate a GALFIT feedme file and return its structured component inventory.

    The single-band counterpart of the GalfitS ``check_lyric_file`` gate: the
    beam-search orchestrator calls this on every ``_iter{n}.feedme`` before
    handing it to ``run_galfit``. It verifies the file parses, that every
    non-sky component carries a semantic ``# STRUCTURE:`` name (GALFIT drops
    them in its output files, so they must be maintained on the input side),
    and returns the canonical component inventory used for beam-state
    signatures (name / type / params / free-fixed toggles, all in px).
    """
    if not os.path.exists(feedme_file):
        return {"status": "failure", "error": f"Feedme file not found: {feedme_file}"}

    errors: list[str] = []
    warnings: list[str] = []

    try:
        paths = parse_feedme(feedme_file)
    except Exception as e:
        return {"status": "failure", "error": f"Failed to parse feedme header: {e}"}

    if not paths.get("input"):
        errors.append("A) input image path is missing")
    if not paths.get("output"):
        errors.append("B) output image path is missing")

    try:
        with open(feedme_file, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {"status": "failure", "error": f"Failed to read feedme: {e}"}

    # Structural sanity: at least one component block and a sky block.
    n_component_headers = len(re.findall(r"(?m)^\s*#\s*(Component|Object)\s*number:", content))
    n_type_lines = len(re.findall(r"(?m)^\s*0\)\s*\w+", content))
    if n_type_lines == 0:
        errors.append("No component blocks found (no '0) <type>' lines)")
    if "sky" not in content.lower():
        warnings.append("No sky component found - GALFIT will not fit the background")

    # Sky policy: the sky ADU is never fitted in this workflow — the sky block
    # (value + toggle) must be carried verbatim from the input feedme's manually
    # provided setting. Surface a warning when the sky's `1)` toggle is free so
    # the orchestrator notices a misconfigured input (the tool never edits it).
    sky_m = re.search(
        r"(?im)^\s*0\)\s*sky\b.*\n\s*1\)\s*([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+([01])",
        content,
    )
    if sky_m:
        sky_value, sky_toggle = sky_m.group(1), sky_m.group(2)
        if sky_toggle == "1":
            warnings.append(
                "The sky `1)` toggle is 1 (free) — this workflow never fits the sky; "
                "it must stay fixed (toggle 0) at the manually provided ADU setting "
                f"(current value {sky_value}). Fix the input feedme and re-validate."
            )

    # Semantic names: recover which blocks carry a '# STRUCTURE:' comment.
    structure_names = re.findall(r"(?im)^\s*#\s*STRUCTURE:\s*(\S+)", content)
    components = parse_components(feedme_file)
    if components and len(structure_names) < len(components):
        warnings.append(
            f"Only {len(structure_names)} of {len(components)} non-sky components carry a "
            "'# STRUCTURE:' naming comment — unnamed components fall back to type-based "
            "names (legend/ledger consistency degrades)"
        )

    inventory = []
    for i, comp in enumerate(components, start=1):
        toggles = comp.get("toggles", {})
        entry = {
            "number": i,
            "name": comp["name"],
            "type": comp["type"],
            "params": {
                "x_px": comp["x"], "y_px": comp["y"], "mag": comp["mag"],
                "re_px": comp["re"],
                "re_effective_px": effective_re(comp) if comp["type"] == "expdisk" else comp["re"],
                "n": comp["n"], "q": comp["ba"], "pa_deg": comp["pa"],
            },
            "free_fixed": dict(toggles) if toggles else {},
            "sky": False,
        }
        inventory.append(entry)

    # Disk-type enforcement (solution space): in a multi-component model the
    # disk slot must be `expdisk` named `disk` (n==1 by type); the `singlesersic`
    # identity (sersic with free n) is legal ONLY as the sole luminous component.
    # This is where a missed SingleSersic->Disk conversion is caught BEFORE the
    # fit wastes budget (the verifier's 5a only audits at lock time).
    names = {c["name"] for c in components}
    if len(components) >= 2:
        for c in inventory:
            if c["name"] == "disk" and c["type"] != "expdisk":
                errors.append(
                    f"Component '{c['name']}' (number {c['number']}) has type "
                    f"'{c['type']}' — the disk slot in a multi-component model must be "
                    "`expdisk` (n==1 guaranteed by type). The SingleSersic->Disk "
                    "conversion was missed: convert to expdisk named `disk` "
                    "(Rs = fitted Re / 1.68) and refit."
                )
        for c in inventory:
            if "singlesersic" in c["name"]:
                errors.append(
                    f"Component '{c['name']}' (number {c['number']}) carries the "
                    "single-Sersic identity (sersic with free n) alongside other "
                    "components — the singlesersic slot is legal ONLY as the sole "
                    "luminous component. Convert it to `expdisk` named `disk` "
                    "(Rs = fitted Re / 1.68) when the decomposition begins."
                )
    elif len(components) == 1 and "disk" in names and components[0]["type"] == "sersic":
        warnings.append(
            "The sole component is a sersic named 'disk' with free n — in the "
            "single-component regime it should be named 'singlesersic' (the disk "
            "name is reserved for the expdisk slot of multi-component models); "
            "rename it to keep ledger signatures unambiguous."
        )

    if errors:
        return {
            "status": "failure",
            "errors": errors,
            "warnings": warnings,
            "fit_region": paths.get("fit_region"),
            "constraint": paths.get("constraint") or None,
        }

    # PSF characterisation (available from the FIRST check, before any fit): a 2D
    # Gaussian fit to the feedme's D) PSF image gives FWHM and A_psf = pi*(FWHM/2)^2.
    # The VLM uses the blob-area / A_psf ratio to decide companion psf-vs-sersic
    # (see the beam prompt's companion profile-type selection rule); the workflow
    # also uses FWHM_PSF for the default Re lower bound.
    psf_info: dict[str, Any] = {}
    psf_file = paths.get("psf") or None
    if psf_file and os.path.exists(psf_file):
        from .extract_summary_galfit import compute_psf_area
        psf_result = compute_psf_area(psf_file)
        if psf_result is not None:
            psf_info = {"psf_fwhm_px": psf_result[0], "a_psf_px2": psf_result[1]}
        else:
            warnings.append("Failed to fit the PSF (D) image); psf_fwhm/a_psf unavailable")
    else:
        warnings.append("No usable PSF file (feedme D) item); psf_fwhm/a_psf unavailable")

    return {
        "status": "success",
        "components": inventory,
        "n_components": len(inventory),
        "fit_region": paths.get("fit_region"),
        "constraint": paths.get("constraint") or None,
        **psf_info,
        "warnings": warnings,
        "message": (
            "Feedme structure check passed. 'components' is the canonical component "
            "inventory (px units, PA in the N=+Y contract); use it directly as the source for "
            "beam-state signatures and warm-start backfill. psf_fwhm_px / a_psf_px2 "
            "(when present) characterise the PSF: record them in the working_note header "
            "and the [Meta] line of global_state_description — the VLM needs A_psf for the "
            "companion psf-vs-sersic selection rule."
        ),
    }
