"""survey_round: the single-function surveyor trigger.

One call per successful fit: builds the digests from the state graph,
dispatches the configured VLM backend (cc / acp / vlm — same dispatch as
generate_galfit_beam_actions), extracts and validates the JSON response with
a bounded retry loop that feeds structured error codes back to the model,
attaches the Physicality Verdict to the state, enqueues the legal candidates
(floors / diversity / aging overlay), and returns the queue snapshot with the
next candidate to execute.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from beam.candidate_schema import parse_survey_response, validate_survey
from beam.digest import (
    build_global_state_description,
    build_local_state_description,
    build_queue_digest,
)
from beam.enqueue import ingest
from beam.vlm_extract import extract_survey_json

# required-candidate semantics of the numeric triggers: when the trigger
# holds, the mapped candidate family is REQUIRED (or an explicit waiver in
# physical_motivation) — a silent omission is logged and fed back into the
# NEXT round's supplement for self-correction (workflow d.ii absence rule;
# Plate0300 incident: lens_relax_d held, no path-D candidate, and the expert
# annotation later showed the true lens Re=18.5 hidden behind the
# self-imposed 17 px cap).
_WAIVER_MARKERS = ("waiv", "not applicable", "inapplicable")


def _lens_re_cap(graph, label: str) -> float | None:
    state = graph.state(label)
    lens = next((c for c in state.get("inventory", [])
                 if (c.get("name") or "").lower() == "lens"), None)
    if not lens or not lens.get("number"):
        return None
    band = (state.get("cons_effective") or {}).get(f"{lens['number']}.re")
    return band[1] if band else None


def _is_lens_relax_candidate(cand, cap: float | None) -> bool:
    """True path-D relaxation: the proposed re band's upper edge EXCEEDS the
    current cap (a tightened band is path A, not D)."""
    for p in cand.to_plain_primitives():
        if p.get("op") != "tune" or (p.get("structure_name") or "").lower() != "lens":
            continue
        band = (p.get("cons_bounds") or {}).get("re")
        if band and band[1] and cap and band[1] > cap + 1e-6:
            return True
    return False


def _spike_present_in_analysis(raw: str) -> bool:
    """Classify the surveyor's Phase-1 `central_spike` assessment lines
    (KILOGAS_231 / Plate0284 retrospective: the spike was recorded nearly
    every round yet never converted into an AGN candidate). Absence markers
    win over presence markers ('no unresolved positive core spike' must
    classify absent despite the word 'positive')."""
    import re as _re
    if not raw:
        return False
    absent_re = _re.compile(r"\b(false|no|none|absent|not assessed|weak)\b", _re.I)
    present_re = _re.compile(r"\b(true|yes|present|mild|minor|positive|excess)\b", _re.I)
    for line in raw.splitlines():
        low = line.lower()
        if "central_spike" not in low or len(line) > 400:
            continue
        if absent_re.search(line):
            continue
        if present_re.search(line):
            return True
    return False


def _bulge_n_at_cap(state: dict) -> bool:
    """A free bulge n pinned at its n upper bound (default 8): the Sérsic
    wants a cuspier-than-n=8 core — numeric point-source evidence of the
    same family as the central spike (Plate0284 A.5 retrospective: bulge
    n=8.0 pinned, band [0.1,8.0], no AGN candidate ever proposed). A fixed
    n cannot 'hit' the cap and never fires."""
    cons = state.get("cons_effective") or {}
    for c in state.get("inventory", []):
        if (c.get("name") or "").lower() != "bulge" or c.get("n") is None:
            continue
        toggles = c.get("toggles") or {}
        try:
            t_n = toggles.get("n", 1)
            t_n = 1 if t_n is None else t_n
            if int(t_n) != 1:
                continue
        except (TypeError, ValueError):
            continue
        band = cons.get(f"{c.get('number')}.n")
        cap = band[1] if band else 8.0
        try:
            if float(c["n"]) >= 0.98 * float(cap):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _central_spike_agn_trigger(state: dict, raw: str) -> bool:
    """Fire when the model has no AGN yet AND either (a) this round's own
    Phase-1 assessment reports a central <5px spike (a healthy bulge does
    not close the door — residual_analysis: 'an obvious positive-residual
    leftover within 0-5 px of the 1D profile that the Bulge cannot absorb'
    is AGN evidence; 'the Bulge cannot be retained -> an AGN must be tried
    to compensate'), or (b) a free bulge n is pinned at the n cap — the
    n -> cusp mimicry branch of the same point-source hypothesis."""
    names = {(c.get("name") or "").lower() for c in state.get("inventory", [])}
    if "agn" in names:
        return False
    return _spike_present_in_analysis(raw) or _bulge_n_at_cap(state)


def _candidate_absence_watchdog(graph, resp, triggers: dict, label: str,
                                session_id: str) -> list[str]:
    """Log + queue for self-correction every fired trigger whose required
    candidate family is absent without an explicit waiver."""
    from beam.enqueue import _is_agn_add, _is_bar_direction

    watch = {
        "lens_relax_d": {
            "classify": lambda c: _is_lens_relax_candidate(c, _lens_re_cap(graph, label)),
            "describe": "path-D lens re_max relaxation (tune(lens, re_max = hit x 1.3))",
            "waiver_kw": ("relax", "re_max", "path d", "cap"),
        },
        "flat_bulge_bar": {
            "classify": _is_bar_direction,
            "describe": "a Bar-direction candidate (tune(Bulge->Bar) / add(Bar))",
            "waiver_kw": ("bar",),
        },
        "central_spike_agn": {
            "classify": _is_agn_add,
            "describe": "an AGN point-core candidate (add(agn, psf) / "
                        "remove(bulge)+add(agn) collapsed-bulge replacement)",
            "waiver_kw": ("agn", "point", "spike"),
        },
    }
    notes: list[str] = []
    if not triggers:
        return notes
    motivations = " ".join(c.physical_motivation or "" for c in resp.candidates).lower()
    waived = any(m in motivations for m in _WAIVER_MARKERS)
    for trig, spec in watch.items():
        if not triggers.get(trig):
            continue
        if any(spec["classify"](c) for c in resp.candidates):
            continue
        if waived and any(k in motivations for k in spec["waiver_kw"]):
            continue
        note = (f"Previous-round candidate-absence notice (objective fact): trigger "
                f"'{trig}' held but no {spec['describe']} was proposed and no waiver "
                f"was stated (state {label}). Under new evidence either generate the "
                f"candidate or file the explicit waiver in physical_motivation.")
        notes.append(note)
        graph.log_decision({"kind": "vlm-candidate-absence", "state": label,
                            "session": session_id, "missing": trig,
                            "describe": spec["describe"]})
    if notes:
        graph.g.graph.setdefault("survey_notes", []).extend(notes)
    return notes


def global_desc_for(graph) -> str:
    """Global-state digest, or the ablation-arm marker when disabled.

    Arm ``no_global_state`` (paper ablation): the cross-round digest is
    withheld from BOTH prompt occurrences — the surveyor must rely on the
    image, the parameter summary and the current-round supplement only.
    """
    if (graph.g.graph.get("meta", {}).get("ablations") or {}).get("no_global_state"):
        return ("(disabled for this run — ablation arm no_global_state: the "
                "cross-round state digest is withheld; judge from the image, "
                "the parameter summary and the current-round supplement only)")
    return build_global_state_description(graph)


def _dispatch(system_prompt: str, turns: list[str], image_path: str):
    """Mirror of generate_galfit_beam_actions' ANALYSIS_MODE dispatch."""
    analysis_mode = os.environ.get("ANALYSIS_MODE", "vlm").lower()
    session_id = ""
    analysis: str | None = None
    error: str | None = None

    if analysis_mode == "cc":
        from tools.cc_analysis import run_component_analysis_cc

        session_id = str(uuid.uuid4())
        analysis, error = run_component_analysis_cc(
            system_prompt=system_prompt, analysis_prompts=turns, session_id=session_id)
        return analysis, session_id, error

    if analysis_mode == "acp":
        from tools.acp_analysis import run_component_analysis_acp

        mega = (
            "You are the surveyor of the mechanised GALFIT beam search. Given the "
            "fitting results, produce the physicality verdict and 1-4 candidate "
            "actions as one JSON object.\n\n"
            "During this process you may only use the read_file and write_file tools.\n\n"
            f"[Input image file]: {os.path.abspath(image_path)}\n"
            "(2x3 layout: DATA LOW/HIGH DR | MODEL // RESIDUAL | RESIDUAL ZOOM | 1D SB profile)\n\n"
            "After reading the file above with read_file, carry out the phases in order. "
            "Phase 1 must remain strictly objective.\n\n" + "\n\n".join(turns)
        )
        analysis, session_id, error = run_component_analysis_acp(
            system_prompt=system_prompt, analysis_prompts=[mega])
        return analysis, session_id, error

    from tools.openai_analysis import run_openai_analysis

    deferred_system = os.environ.get("VLM_DEFERRED_SYSTEM", "0") == "1"
    analysis, session_id, error, _timing = run_openai_analysis(
        system_prompt=system_prompt, analysis_prompts=turns,
        image_path=os.path.abspath(image_path), deferred_system=deferred_system)
    return analysis, session_id, error


def survey_round(
    galaxy_dir: str,
    state_label: str = "",
    local_injections: dict | None = None,
    directives: str = "",
    max_retries: int = 2,
    opening_image: str = "",
) -> dict[str, Any]:
    """Run one surveyor round on the (latest or given) fitted state.

    Round-0 OPENING mode (2026-10-09): when the target state is the unfitted
    root A.0 (global_iter_id == 0), the surveyor is sent the RENDERED ORIGINAL
    image (``opening_image``) instead of a comparison panel and asked for the
    ranked opening skeleton candidates (single ``skeleton`` primitives seeded
    from the input feedme's reference parameters). No verdict is settled (no
    fit exists yet); the candidates are enqueued parented A.0 and the loop
    begins by applying the top-ranked one.
    """
    try:
        from beam.graph import BeamGraph

        graph = BeamGraph.load(galaxy_dir)
    except Exception as e:
        return {"status": "failure", "error": f"cannot load beam graph: {e}"}

    if (graph.g.graph.get("meta", {}).get("ablations") or {}).get("single_agent"):
        return {"status": "failure",
                "error": "E_ARM: this run is in the single_agent ablation arm — the "
                         "VLM surveyor is disabled; author the survey response "
                         "yourself and register it with beam_enqueue_candidates"}

    label = state_label or graph.latest_state()
    if not label or label not in graph.g.nodes:
        return {"status": "failure", "error": f"state '{label}' not found in the graph"}
    state = graph.state(label)

    is_opening = int(state.get("global_iter_id", 0)) == 0
    artifacts = state.get("artifacts", {})
    feedme = artifacts.get("feedme")
    if is_opening:
        # round-0: no galfit.NN / comparison exist; the rendered original is
        # the survey image and the root feedme is the header/sky/reference
        # source
        comparison = opening_image
        missing = [k for k, v in (("feedme", feedme), ("opening_image", comparison))
                   if not v or not os.path.exists(v)]
        if missing:
            return {"status": "failure",
                    "error": f"opening round is missing inputs: {missing}; "
                             "run render_original and pass opening_image"}
    else:
        fitted = artifacts.get("galfit_nn")
        comparison = artifacts.get("comparison_png")
        missing = [k for k, v in (("feedme", feedme), ("galfit_nn", fitted),
                                  ("comparison_png", comparison)) if not v or not os.path.exists(v)]
        if missing:
            return {"status": "failure",
                    "error": f"state {label} is missing artefacts: {missing}; "
                             "re-run run_galfit and beam_record_fit first"}

    # ---- digests from the graph
    global_desc = global_desc_for(graph)
    queue_digest = build_queue_digest(graph)
    if is_opening:
        triggers = {}
        ref_lines = ""
        try:
            from tools.beam_actions_galfit import check_feedme_file as _cff

            refs = _cff(feedme).get("components", [])
            ref_lines = "; ".join(
                f"{c.get('name')} ({c.get('type')}): Re_eff="
                f"{(c.get('params') or {}).get('re_effective_px')}px, mag="
                f"{(c.get('params') or {}).get('mag')}, q={(c.get('params') or {}).get('q')}, "
                f"PA={(c.get('params') or {}).get('pa_deg')}deg, centre=("
                f"{(c.get('params') or {}).get('x_px')},"
                f"{(c.get('params') or {}).get('y_px')})px" for c in refs) or "(unparsed)"
        except Exception as e:  # reference formatting is best-effort
            ref_lines = f"(input feedme reference parse failed: {e})"
        local_desc = (
            "Round-0 OPENING survey: NO fit exists yet — the image is the RENDERED "
            "ORIGINAL (asinh stretch + isophote contours), NOT a comparison panel; "
            "there are no model/residual/1D panels and no fitted parameters. "
            f"Input-feedme reference parameters (seeding references, NOT a model to "
            f"copy verbatim): {ref_lines}. Task: judge the morphology and return 1-3 "
            "RANKED opening skeleton candidates, each a single skeleton primitive "
            "(components from {disk(expdisk), bulge(sersic, n FREE with initial 4 or 1), "
            "bar(sersic, n=0.5 FIXED)} — or singlesersic alone for an elliptical opening). "
            "Seed every mag/re/q from the references and your image reading. "
            "**PA measurement rule (N=+Y; mind the sign trap)**: PA is measured from the "
            "UPWARD axis, increasing COUNTERCLOCKWISE (toward the left). The two "
            "diagonals are 90 deg apart, not the same axis: a major axis pointing "
            "toward the UPPER-RIGHT ('/') has PA -45 (== 135); one pointing toward "
            "the UPPER-LEFT ('\\') has PA +45. To read a bar PA: take the direction "
            "from the galaxy centre toward one TIP of the bar major axis, then rotate "
            "it counterclockwise from 'up'. PA is mod 180 (theta == theta+180) but "
            "theta and -theta are DIFFERENT orientations. **Independent anchors**: each "
            "elongated component's PA is measured from its OWN axis — never copy "
            "another component's PA onto it (bar PA != bulge PA != disk PA a priori). "
            "A near-round component (seed q > 0.8, typically the bulge) has an "
            "ill-determined PA — seed it with the disk PA (or 0) and let the fit decide; "
            "do not invent a direction. The top-ranked candidate becomes the first fit.")
    else:
        local_desc, triggers = build_local_state_description(graph, label,
                                                             local_injections or {})

    # ---- prompt assembly (system message mirrors the legacy generator)
    try:
        from prompts import prompts as prompt_singleton
        from tools.beam_actions_galfit import _build_summary_content_galfit, _galfit_system_message

        system_message = _galfit_system_message()
        component_spec = prompt_singleton.get_component_specification_galfit()
        if component_spec:
            system_message = system_message + "\n\n" + component_spec
        if is_opening:
            summary_content = ("(round-0 opening: no fitted parameters — seed from the "
                               "input-feedme references in the local supplement)")
        else:
            summary_content = _build_summary_content_galfit(
                feedme, artifacts.get("galfit_nn"), artifacts.get("summary") or "")
        turn1 = prompt_singleton.get_survey_visual_extraction(
            global_state_description=global_desc)
        turn2 = prompt_singleton.get_survey_candidate_generation(
            summary_content=summary_content,
            global_state_description=global_desc,
            local_state_description=local_desc,
            branch_id=label.split(".")[0],
            parent_label=state.get("parent") or label,
            depth=int(state.get("depth", 1)),
            queue_digest=queue_digest,
            directives=directives or ("(round-0 opening — skeleton primitives only; "
                                      "return physicality_verdict PASS as a "
                                      "placeholder)" if is_opening else "(none)"),
        )
        if is_opening:
            turn2 = (
                "## OPENING ROUND (round-0; the parent is the unfitted root A.0)\n"
                "Return 1-3 RANKED opening skeleton candidates. Each candidate uses "
                "EXACTLY ONE primitive: {\"op\": \"skeleton\", \"skeleton\": "
                "{\"components\": [<AddParams-shaped declarations for the WHOLE "
                "luminous model>]}}. The alphabet: singlesersic ALONE (elliptical "
                "opening) XOR any subset of {disk (expdisk), bulge (sersic), bar "
                "(sersic with n=0.5 and toggles.n=0)} — lens/outerdisk/edgedisk/AGN/"
                "companion/F1 are OUTSIDE the opening alphabet (they enter later "
                "through residual-driven candidates). Seed parameters from the "
                "input-feedme references (mag split ~3:7 bulge:disk for two "
                "components, bar between; disk Re from the reference Re, bulge Re "
                "1/5-1/3 of it, bar Re between the two; disk q/PA/centre from the "
                "references).\n"
                "**PA measurement rule (N=+Y; KILOGAS_13 rerun — the sign trap)**: "
                "PA is measured from the UPWARD axis, increasing COUNTERCLOCKWISE "
                "(toward the left). The two diagonals are 90° apart, not the same "
                "axis: a major axis pointing toward the UPPER-RIGHT ('/') has PA "
                "−45° (≡135°); one pointing toward the UPPER-LEFT ('\\') has PA "
                "+45°. To read a bar's PA: take the direction from the galaxy centre "
                "toward one TIP of the bar's major axis, then rotate counterclockwise "
                "from 'up'. PA is mod 180° (θ ≡ θ+180°) but θ and −θ are DIFFERENT "
                "orientations. **Independent anchors**: each elongated component's PA "
                "is measured from its OWN axis — never copy another component's PA "
                "onto it (KILOGAS_13: the bar and bulge were both seeded 45°; the "
                "bar's true PA was −45° and the wrong seed locked the fit in a "
                "rotated basin). A near-round component (seed q ≳ 0.8, typically the "
                "bulge) has an ill-determined PA — seed it with the disk's PA (or 0) "
                "and let the fit decide.\n"
                "**Bar vs Bulge — judge the centre on the HIGH-DR panel** "
                "(KILOGAS_13/101 retrospectives): the opening image has TWO panels "
                "— left = LOW DR (vmax=99.5th pctl; shows the extended exponential "
                "outer disk traced by the outer green isophotes, but the centre "
                "saturates into a blob), right = HIGH DR (vmax=99.99th pctl; the "
                "bright centre is layered without saturation). A round, "
                "circularly symmetric innermost concentration = BULGE. An "
                "obviously ELONGATED / rectangular-like bright structure with a "
                "clear major axis traversing the centre = BAR — never dismiss the "
                "centre as 'bulge only' from the LOW-DR blob; the HIGH-DR layers "
                "decide. When the centre is elongated but uncertain, HEDGE: "
                "include BOTH the with-bar and the without-bar skeletons among "
                "your 1-3 candidates, ranked by confidence.\n"
                "**Skeleton priority is EVIDENCE-CONDITIONAL (hard ranking rule)**: "
                "multi-component skeletons rank ABOVE singlesersic ONLY on "
                "DISK-LIKE envelope evidence — an elongated/flattened outer "
                "envelope (outer isophotes q \u2248 0.6 or flatter, clearly "
                "non-round), spiral-arm/dust texture, a bar-like inner elongation, "
                "or a strong visible asymmetry. A two-zone brightness profile "
                "(compact bright core + extended envelope) with a ROUND envelope "
                "does NOT count — that is exactly what a concentrated single "
                "S\u00e9rsic (n \u2248 2-4) looks like; there singlesersic ranks "
                "FIRST (the elliptical/concentrated-single hypothesis is the best "
                "prior) and the multi-component skeletons become the low-\u03c3 "
                "alternates.\n"
                "Rank by your posterior sigma (fresh skeleton ~0.5; converging visual "
                "evidence up to ~0.7; never above 0.9). Return "
                "physicality_verdict PASS (placeholder — nothing is fitted yet).\n\n"
                + turn2)
    except Exception as e:
        return {"status": "failure", "error": f"prompt assembly failed: {e}"}

    # ---- bounded VLM + validation loop
    feedback = ""
    resp = None
    last_parsed = None   # last schema-valid parse — the verdict-salvage source
    session_id = ""
    raw_analysis = ""
    attempts_issues: list[dict] = []
    for _attempt in range(max_retries + 1):
        turns = [turn1, turn2 + (f"\n\n## Your previous output was rejected — fix and return the corrected JSON only:\n{feedback}" if feedback else "")]
        analysis, session_id, error = _dispatch(system_message, turns, comparison)
        if error:
            result: dict[str, Any] = {"status": "failure", "error": error}
            if session_id:
                result["session_id"] = session_id
            if analysis:
                result["partial_analysis"] = analysis
            return result
        raw_analysis = analysis or ""
        payload, perr = extract_survey_json(raw_analysis)
        if payload is None:
            attempts_issues.append({"code": "E_SCHEMA", "message": perr})
            feedback = f"E_SCHEMA: {perr}. Return ONE fenced ```json object with the physicality_verdict and candidates keys, nothing else."
            continue
        resp, verr = parse_survey_response(payload)
        if resp is None:
            attempts_issues.append({"code": "E_SCHEMA", "message": verr})
            feedback = f"E_SCHEMA: {verr}. Fix the field types/shapes per the contract."
            continue
        last_parsed = resp
        report = validate_survey(resp, graph, label)
        if report.ok:
            break
        resp = None
        attempts_issues.extend({"code": i.code, "message": i.message} for i in report.errors)
        feedback = report.error_feedback()
    if resp is None:
        # ---- verdict salvage (KILOGAS_386 A.4 retrospective, 2026-10-14)
        # The Physicality Verdict judges the FIT and is independent of the
        # candidates' legality. The old path returned here BEFORE
        # apply_verdict, so a round whose every candidate was legality-gated
        # (combo cap / AGN admission / ...) hung forever without a verdict —
        # barred from s* by the verdict gate while possibly carrying the
        # graph's best BIC. Salvage: settle the verdict from the LAST
        # schema-valid response (mech checks still merge; apply_verdict is
        # idempotent); the candidates stay discarded (unchanged behaviour).
        salvaged_verdict = None
        if last_parsed is not None and not is_opening:
            vlm_verdict = last_parsed.physicality_verdict.model_dump()
            graph.apply_verdict(label, vlm_verdict)
            state = graph.state(label)
            salvaged_verdict = state.get("verdict") or vlm_verdict
        entry = {"kind": "survey-validation-exhausted", "state": label,
                 "session": session_id, "issues": attempts_issues}
        if salvaged_verdict is not None:
            entry["verdict_salvaged"] = True
        graph.log_decision(entry)
        graph.commit()
        result = {"status": "failure", "error": "validation retries exhausted",
                  "issues": attempts_issues, "session_id": session_id,
                  "partial_analysis": raw_analysis}
        if salvaged_verdict is not None:
            result["verdict"] = salvaged_verdict
            result["verdict_salvaged"] = True
            # archive the raw exchange for audit even on the exhausted path
            md_path = ""
            if comparison and os.path.exists(comparison):
                out_base = os.path.splitext(os.path.basename(comparison))[0]
                md_path = os.path.join(os.path.dirname(os.path.abspath(comparison)),
                                       f"{out_base}_survey_{label}_{session_id or uuid.uuid4().hex[:8]}.md")
                try:
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(f"# survey_round {label} (session {session_id}) — "
                                f"candidates rejected, verdict salvaged\n\n"
                                f"## Verdict (merged: surveyor + mechanical checks; "
                                f"settled from the last schema-valid response)\n"
                                f"{json.dumps(salvaged_verdict, indent=1, default=str)}\n\n"
                                f"## Mechanical check table (code-computed)\n"
                                + "".join(f"- [{m.get('severity')}] {m.get('check')}: {m.get('detail')}\n"
                                          for m in (state.get("mech_checks") or []))
                                + "\n## Rejected-candidate issues\n"
                                + "".join(f"- {i.get('code')}: {i.get('message')}\n"
                                          for i in attempts_issues)
                                + f"\n## Raw VLM output\n\n{raw_analysis}\n")
                except OSError:
                    md_path = ""
            result["candidates_file"] = md_path
        return result

    # ---- settle the verdict on the state (mechanical checks merged in code)
    vlm_verdict = resp.physicality_verdict.model_dump()
    if is_opening:
        # round-0: nothing is fitted — the verdict is a placeholder and is NOT
        # settled onto the unfitted root A.0 (the physicality gate engages from
        # the first fitted state on, via its own survey_round)
        verdict = vlm_verdict
    else:
        graph.apply_verdict(label, vlm_verdict)
        state = graph.state(label)
        verdict = state.get("verdict") or vlm_verdict  # merged verdict (may mech-veto)

    # ---- archive the raw exchange for audit
    out_base = os.path.splitext(os.path.basename(comparison))[0]
    md_path = os.path.join(os.path.dirname(os.path.abspath(comparison)),
                           f"{out_base}_{'opening_' if is_opening else 'survey_'}"
                           f"{label}_{session_id or uuid.uuid4().hex[:8]}.md")
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# survey_round {label} (session {session_id})\n\n"
                    f"## Verdict (merged: surveyor + mechanical checks)\n"
                    f"{json.dumps(verdict, indent=1, default=str)}\n\n"
                    f"## Surveyor verdict (as returned by the VLM)\n"
                    f"{json.dumps(vlm_verdict, indent=1, default=str)}\n\n"
                    f"## Mechanical check table (code-computed)\n"
                    + "".join(f"- [{m.get('severity')}] {m.get('check')}: {m.get('detail')}\n"
                              for m in (state.get("mech_checks") or []))
                    + f"\n## Raw VLM output\n\n{raw_analysis}\n")
    except OSError:
        md_path = ""

    # ---- central-spike AGN trigger (KILOGAS_231 / Plate0284 retrospective:
    # the <5px central spike was recorded round after round but never became
    # an AGN candidate). Parsed from THIS round's Phase-1 assessment; arms
    # the floor_agn_spike flag for any add(agn) candidate in this same
    # response and the candidate-absence watchdog when the VLM stays silent.
    if is_opening:
        # write the surveyor's morphology judgement back into the graph
        # stage1 block so the working_note header and the digest render it
        mot = "; ".join((c.physical_motivation or "").strip()
                        for c in resp.candidates[:2] if c.physical_motivation)
        graph.g.graph.setdefault("stage1", {})["morphology"] = (
            "(round-0 opening survey) " + (mot[:400] or "(no rationale returned)"))
    if not is_opening:
        triggers["central_spike_agn"] = _central_spike_agn_trigger(state, raw_analysis)

    # ---- enqueue legal candidates (floors/diversity/aging overlay)
    result_ingest = ingest(graph, resp.candidates, session_id=session_id,
                           parent_label=label,
                           verdict_fail=(verdict.get("verdict") == "FAIL" and not is_opening),
                           numeric_triggers=triggers)
    absence_notes = [] if is_opening else _candidate_absence_watchdog(
        graph, resp, triggers, label, session_id)
    graph.age_pending()  # pending entries age while a round passes
    graph.log_decision({"kind": "opening-round" if is_opening else "survey-round",
                        "state": label, "session": session_id,
                        "enqueued": [e["action_id"] for e in result_ingest.enqueued],
                        "discarded": result_ingest.discarded})
    graph.commit()

    return {
        "status": "success",
        "state": label,
        "verdict": verdict,
        "enqueued": result_ingest.enqueued,
        "discarded": result_ingest.discarded,
        "protected_directions": result_ingest.protected,
        "numeric_triggers": triggers,
        "absence_notices": absence_notes,
        "queue": [
            {"action_id": aid, **{k: graph.pending_record(aid).get(k)
                                  for k in ("parent", "sigma", "score",
                                            "expected_behavior_tag", "code_flags")}}
            for aid in graph.pending_queue()
        ],
        "next_candidate": graph.next_action(),
        "best_state": graph.g.graph.get("best_state"),
        "termination": graph.termination_check(),
        "session_id": session_id,
        "candidates_file": md_path,
    }


def orchestrator_round(galaxy_dir: str, response_json: str,
                       state_label: str = "") -> dict[str, Any]:
    """Single-agent ablation arm: validate + ingest an orchestrator-authored
    SurveyResponse (``{"physicality_verdict": {...}, "candidates": [...]}``,
    the same JSON contract the surveyor returns) with NO VLM call.

    Everything else is identical to survey_round: the mechanical checks still
    merge into the verdict, the same legality gates (schema + solution-space
    rules, floors / diversity / aging) apply, and an audit artefact is
    archived next to the comparison image — so the two arms differ ONLY in
    who performs the perception + candidate generation. Validation is
    single-shot: errors are returned for the orchestrator to fix and re-call.
    """
    try:
        from beam.graph import BeamGraph

        graph = BeamGraph.load(galaxy_dir)
    except Exception as e:
        return {"status": "failure", "error": f"cannot load beam graph: {e}"}

    label = state_label or graph.latest_state()
    if not label or label not in graph.g.nodes:
        return {"status": "failure", "error": f"state '{label}' not found in the graph"}
    state = graph.state(label)

    try:
        payload = json.loads(response_json)
    except json.JSONDecodeError as e:
        return {"status": "failure", "error": f"E_SCHEMA: response_json is not valid JSON: {e}"}
    resp, verr = parse_survey_response(payload)
    if resp is None:
        return {"status": "failure", "error": f"E_SCHEMA: {verr}"}
    report = validate_survey(resp, graph, label)
    if not report.ok:
        return {"status": "failure", "error": "validation failed (fix the flagged "
                                              "fields and re-call)",
                "issues": [{"code": i.code, "message": i.message} for i in report.errors]}

    # verdict + mechanical merge (idempotent if beam_record_fit already set it)
    vlm_verdict = resp.physicality_verdict.model_dump()
    graph.apply_verdict(label, vlm_verdict)
    state = graph.state(label)
    verdict = state.get("verdict") or vlm_verdict

    # numeric triggers stay code-computed so floors apply identically in both arms
    _local_desc, triggers = build_local_state_description(graph, label, {})

    session_id = f"orchestrator-{uuid.uuid4().hex[:8]}"
    comparison = state.get("artifacts", {}).get("comparison_png") or ""
    md_path = ""
    if comparison and os.path.exists(comparison):
        out_base = os.path.splitext(os.path.basename(comparison))[0]
        md_path = os.path.join(os.path.dirname(os.path.abspath(comparison)),
                               f"{out_base}_orchestrator_{label}_{uuid.uuid4().hex[:8]}.md")
        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# orchestrator_round {label} (single-agent arm, no VLM)\n\n"
                        f"## Verdict (merged: orchestrator + mechanical checks)\n"
                        f"{json.dumps(verdict, indent=1, default=str)}\n\n"
                        f"## Orchestrator-provided response\n"
                        f"{json.dumps(payload, indent=1, default=str)}\n\n"
                        f"## Mechanical check table (code-computed)\n"
                        + "".join(f"- [{m.get('severity')}] {m.get('check')}: "
                                  f"{m.get('detail')}\n"
                                  for m in (state.get("mech_checks") or [])))
        except OSError:
            md_path = ""

    result_ingest = ingest(graph, resp.candidates, session_id=session_id,
                           parent_label=label,
                           verdict_fail=(verdict.get("verdict") == "FAIL"),
                           numeric_triggers=triggers)
    absence_notes = _candidate_absence_watchdog(graph, resp, triggers, label,
                                                session_id)
    graph.age_pending()
    graph.log_decision({"kind": "orchestrator-round", "state": label, "session": session_id,
                        "enqueued": [e["action_id"] for e in result_ingest.enqueued],
                        "discarded": result_ingest.discarded})
    graph.commit()

    return {
        "status": "success",
        "state": label,
        "verdict": verdict,
        "enqueued": result_ingest.enqueued,
        "discarded": result_ingest.discarded,
        "protected_directions": result_ingest.protected,
        "numeric_triggers": triggers,
        "absence_notices": absence_notes,
        "queue": [
            {"action_id": aid, **{k: graph.pending_record(aid).get(k)
                                  for k in ("parent", "sigma", "score",
                                            "expected_behavior_tag", "code_flags")}}
            for aid in graph.pending_queue()
        ],
        "next_candidate": graph.next_action(),
        "best_state": graph.g.graph.get("best_state"),
        "termination": graph.termination_check(),
        "session_id": session_id,
        "candidates_file": md_path,
    }
