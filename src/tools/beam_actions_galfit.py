"""
Beam-search support for GALFIT single-band fitting.

Provides `check_feedme_file`, the lightweight structural validator used by
the beam-search orchestrator before every `run_galfit` call (the single-band
counterpart of the GalfitS `check_lyric_file` gate), plus the
`_galfit_system_message` / `_build_summary_content_galfit` prompt helpers
reused by beam.survey. The legacy `generate_galfit_beam_actions` tool was
retired — candidate generation lives solely in survey_round's VLM.
"""

import os
import re
from typing import Annotated, Any

import dotenv

from . import prompt
from .parse_feedme import parse_components, parse_feedme
from .render_original import effective_re, EXPDISK_RE_FACTOR

dotenv.load_dotenv()


# ── Branch solution-space restriction (restrict-disk-bulge-bar-agn) ─────────
# Only Disk (sersic with n=1 FIXED — 2026-11-17 expert alignment; the legacy
# expdisk encoding is no longer valid) / Bulge / Bar / AGN (psf) / singlesersic
# (+ F1 + fixed sky) are legal on this branch. Companion, Lens, OuterDisk
# (envelope / 2nd disk) and edgedisk are outside the solution space. Enforced
# mechanically here (check_feedme_file rejects them) in addition to the
# prompt-level Restricted Action Space note in beam_survey_prompt_galfit.md.
BRANCH_ALLOWED_COMPONENTS = "disk(sersic n=1 fixed)/bulge/bar/agn(psf)/singlesersic (+F1, fixed sky)"
BRANCH_FORBIDDEN_NOTICE = (
    "outside the solution space on branch restrict-disk-bulge-bar-agn "
    f"(allowed: {BRANCH_ALLOWED_COMPONENTS}; forbidden: Companion/Lens/OuterDisk/edgedisk)"
)
_FORBIDDEN_NAME_PREFIXES = (
    "companion", "comp", "secondary", "satellite",
    "lens", "outerdisk", "outer_disk", "outer", "envelope",
)
# The shared system message (residual_analysis_message.md) is written for the
# GalfitS multi-band flow and mandates a true sky-PA convention ("north = 0°,
# align on the compass"). The single-band beam flow adopts the N=+Y contract
# instead (north assumed aligned with +Y, so sky-PA ≡ the feedme `10)` frame
# and VLM readings are written verbatim). Patch the PA clause at load time —
# the shared file itself stays untouched for GalfitS.
_PA_CLAUSE_RE = re.compile(r"(?m)^3\. \*\*PA convention\*\*:.*$")
# Companion-ish STRUCTURE names (same pattern family as beam.candidate_schema's
# _COMPANION_RE): field contaminants, exempt from the singlesersic co-tenant
# rule and from the concentric offset chain.
_COMPANION_NAME_RE = re.compile(r"^(companion|comp|secondary|satellite)",
                                re.IGNORECASE)
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


def _validate_cons_file(cons_file: str) -> tuple[list[str], list[str]]:
    """Validate a GALFIT ``.cons`` constraint file's row encodings.

    Enforces the empirically verified encoding contract (this machine's
    GALFIT 3.0.5; see CLAUDE.md's ``.cons`` numeric-band semantics note):
    a soft-constraint row with **two bare numbers** is parsed as offsets
    relative to the component's INPUT value ([input-a, input+b]) — a band
    written absolute-style with bare numbers is silently never enforced as
    written. Absolute bands for ``re``/``rs``/``n``/``q`` MUST use the ``to``
    keyword form. Position windows (``x``/``y``) and ``mag`` relative bands
    are intentionally relative, so bare numbers are legal there.

    Also verifies that chained ``offset`` rows bind x and y in pairs (binding
    only one is strictly forbidden).

    Returns ``(errors, warnings)``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    # params whose bands are absolute-intent in this workflow -> 'to' form required
    absolute_intent = {"re", "rs", "n", "q"}
    offset_chains: dict[str, set[str]] = {}

    try:
        with open(cons_file, encoding="utf-8") as f:
            raw_lines = f.readlines()
    except OSError as e:
        return ([f"Could not read constraint file {cons_file}: {e}"], [])

    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        # Operator (hard-constraint) rows: "<chain> <param> offset|ratio"
        if tokens[-1].lower() in {"offset", "ratio"}:
            if len(tokens) != 3:
                warnings.append(
                    f"cons L{lineno}: unrecognised operator row '{line}' (expected '<chain> <param> offset|ratio')"
                )
                continue
            comp_spec, param = tokens[0], tokens[1].lower()
            if param in {"x", "y"} and tokens[-1].lower() == "offset":
                offset_chains.setdefault(comp_spec, set()).add(param)
            continue
        # 'to' rows: "<N> <param> <a> to <b>" — enforced absolute band, legal for any param
        if "to" in tokens[2:]:
            continue
        # Bare two-number rows: "<N> <param> <a> <b>"
        if len(tokens) == 4:
            comp_spec, param, a, b = tokens[0], tokens[1].lower(), tokens[2], tokens[3]
            try:
                float(a), float(b)
            except ValueError:
                warnings.append(
                    f"cons L{lineno}: could not parse numbers in '{line}' — row ignored by this check"
                )
                continue
            if param in absolute_intent:
                errors.append(
                    f"cons L{lineno}: row '{line}' bounds '{param}' with two BARE numbers — this "
                    "GALFIT build parses them as INPUT-RELATIVE offsets ([input-a, input+b]), so "
                    "the intended absolute band is silently never enforced as written. Rewrite "
                    f"absolute bands in the 'to' keyword form: '{comp_spec} {param} <min> to <max>' "
                    "(real incidents: KILOGAS_432 A.10 and KILOGAS_353 A.6 — '2 n 0.5 8' with "
                    "n_init=4 decoded to [3.5, 12.0] and invalidated both refutations). Bare "
                    "numbers stay legal only for x/y windows and mag relative bands."
                )
            continue
        warnings.append(f"cons L{lineno}: unrecognised row '{line}' — verify against GALFIT .cons syntax")

    for comp_spec, params in offset_chains.items():
        missing = {"x", "y"} - params
        if missing:
            errors.append(
                f"cons: chained offset constraint '{comp_spec}' binds only "
                f"{sorted(params)} — x and y must be bound TOGETHER (binding only "
                "one is strictly forbidden; the chain silently fails otherwise)"
            )
    return errors, warnings


def check_feedme_file(
    feedme_file: Annotated[str, "Absolute path to the GALFIT feedme configuration file"],
) -> dict[str, Any]:
    """Validate a GALFIT feedme file and return its structured component inventory.

    The single-band counterpart of the GalfitS ``check_lyric_file`` gate: the
    beam-search orchestrator calls this on every ``_iter{n}.feedme`` before
    handing it to ``run_galfit``. It verifies the file parses, that every
    non-sky component carries a semantic ``# STRUCTURE:`` name (GALFIT drops
    them in its output files, so they must be maintained on the input side),
    validates the ``G)`` constraint file's row encodings (absolute ``re``/``n``/``q``
    bands must use the ``to`` keyword form; chained ``offset`` rows must bind x
    and y in pairs), and returns the canonical component inventory used for
    beam-state signatures (name / type / params / free-fixed toggles, all in px).
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

    # Constraint-file encoding validation (G) pointer): absolute re/n/q bands
    # MUST use the 'to' keyword form on this GALFIT build; chained offset rows
    # must bind x and y in pairs. A violating .cons silently enforces a
    # different band than written — it must be fixed BEFORE the fit runs.
    cons_path = paths.get("constraint")
    if cons_path:
        if os.path.exists(cons_path):
            cons_errors, cons_warnings = _validate_cons_file(cons_path)
            errors.extend(cons_errors)
            warnings.extend(cons_warnings)
        else:
            warnings.append(
                f"G) constraint file not found: {cons_path} — GALFIT will run unconstrained "
                "(silently ignoring the pointer); confirm the path or clear the G) item"
            )

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
    # disk slot must be a `sersic` named `disk` with n==1 FIXED (2026-11-17
    # expert alignment — `5) 1 0`; the legacy expdisk encoding is rejected);
    # the `singlesersic` identity (sersic with free n) is legal ONLY as the
    # sole luminous component or paired with a single AGN (psf) point core —
    # {singlesersic, agn} is the elliptical+point-core terminal state
    # (KILOGAS_221 retrospective).
    # This is where a missed SingleSersic->Disk conversion is caught BEFORE the
    # fit wastes budget (the verifier's 5a only audits at lock time).
    names = {c["name"] for c in components}
    if len(components) >= 2:
        for c in inventory:
            if c["name"] == "disk":
                n_val = c["params"].get("n")
                n_fixed = c.get("free_fixed", {}).get("n") == 0
                if c["type"] != "sersic" or n_val is None or abs(float(n_val) - 1.0) > 1e-6 \
                        or not n_fixed:
                    errors.append(
                        f"Component '{c['name']}' (number {c['number']}) has type "
                        f"'{c['type']}' with n={n_val} (fixed={n_fixed}) — the disk "
                        "slot in a multi-component model must be a `sersic` with "
                        "n=1 FIXED (`0) sersic`, `5) 1 0`); the legacy `expdisk` "
                        "encoding is no longer valid (2026-11-17 expert alignment). "
                        "The SingleSersic->Disk conversion was missed: convert to "
                        "sersic n=1 fixed named `disk` (Re kept verbatim) and refit."
                    )
        illegal_co_tenants = sorted(n for n in names
                                    if n != "singlesersic" and n != "agn"
                                    and not _COMPANION_NAME_RE.match(n))
        for c in inventory:
            if "singlesersic" in c["name"] and illegal_co_tenants:
                errors.append(
                    f"Component '{c['name']}' (number {c['number']}) carries the "
                    "single-Sersic identity (sersic with free n) alongside "
                    f"{illegal_co_tenants} — the singlesersic slot is legal ONLY as "
                    "the sole luminous component, alongside an AGN (psf) point core "
                    "and/or companions (field contaminants). With any other "
                    "component, convert it to a `sersic` with n=1 fixed "
                    "named `disk` (Re kept verbatim) when the decomposition "
                    "begins."
                )
        # Disk-required rule (multi-component regime): without a singlesersic,
        # a multi-component model must carry a disk/edgedisk slot — a diskless
        # inventory (e.g. {bulge, companion}, {bar, agn}) belongs to the
        # singlesersic regime, not the decomposition regime.
        if "singlesersic" not in names and not ({"disk", "edgedisk"} & names):
            errors.append(
                "The multi-component model carries no disk/edgedisk slot — a "
                "multi-component inventory must contain a disk or edgedisk "
                "(the decomposition regime is disk-bearing by definition); a "
                "diskless model belongs to the singlesersic regime "
                "({singlesersic} / {singlesersic, agn} / + companions)."
            )
    elif len(components) == 1 and "disk" in names and components[0]["type"] == "sersic":
        warnings.append(
            "The sole component is a sersic named 'disk' — in the "
            "single-component regime it should be named 'singlesersic' (the disk "
            "name is reserved for the sersic n=1-fixed slot of multi-component "
            "models); rename it to keep ledger signatures unambiguous."
        )

    # Branch solution-space restriction (restrict-disk-bulge-bar-agn): Companion,
    # Lens, OuterDisk (envelope / 2nd expdisk) and edgedisk are rejected outright.
    # Keyed on the semantic `# STRUCTURE:` name (prefix match, catching the
    # disambiguated suffixes companion2/…) and on the feedme component type.
    for c in inventory:
        lname = str(c["name"]).lower()
        if lname.startswith(_FORBIDDEN_NAME_PREFIXES):
            errors.append(
                f"Component '{c['name']}' (number {c['number']}, type '{c['type']}') is "
                f"{BRANCH_FORBIDDEN_NOTICE} — remove it / revert to a legal inventory "
                "before fitting."
            )
        if str(c["type"]).lower() == "edgedisk":
            errors.append(
                f"Component '{c['name']}' (number {c['number']}) has type 'edgedisk' — "
                f"the edgedisk slot is {BRANCH_FORBIDDEN_NOTICE}; fit an edge-on-looking "
                "galaxy with the ordinary sersic (n=1 fixed) Disk with free q."
            )
    n_expdisk = sum(1 for c in inventory if str(c["type"]).lower() == "expdisk")
    if n_expdisk >= 1:
        # Hard cut (2026-11-17): the Disk is a sersic with n=1 FIXED; the
        # legacy expdisk encoding is no longer valid anywhere on this branch
        # (the OuterDisk/envelope slot is forbidden independently).
        errors.append(
            f"{n_expdisk} expdisk component(s) found — expdisk is the LEGACY Disk "
            f"encoding and is {BRANCH_FORBIDDEN_NOTICE} (the Disk must be a "
            "`sersic` with n=1 fixed (`5) 1 0`), Re in the `4)` row). Restart "
            "graphs authored with expdisk disks."
        )
    n_psf = sum(1 for c in inventory if str(c["type"]).lower() == "psf")
    if n_psf > 1:
        errors.append(
            f"{n_psf} psf components found — the AGN is the sole allowed point-source "
            f"component; companion-type psf components are {BRANCH_FORBIDDEN_NOTICE}."
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
    # The workflow uses FWHM_PSF for the default Re lower bound (and A_psf enters
    # the [Meta] line of global_state_description).
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
            "(when present) characterise the PSF: record them in the graph stage1 meta "
            "and the [Meta] line of global_state_description (psf_fwhm feeds the default "
            "Re lower bound)."
        ),
    }
