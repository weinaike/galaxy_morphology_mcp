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


def _candidate_absence_watchdog(graph, resp, triggers: dict, label: str,
                                session_id: str) -> list[str]:
    """Log + queue for self-correction every fired trigger whose required
    candidate family is absent without an explicit waiver."""
    from beam.enqueue import _is_bar_direction

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
) -> dict[str, Any]:
    """Run one surveyor round on the (latest or given) fitted state."""
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

    artifacts = state.get("artifacts", {})
    feedme = artifacts.get("feedme")
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
    local_desc, triggers = build_local_state_description(graph, label,
                                                         local_injections or {})
    queue_digest = build_queue_digest(graph)

    # ---- prompt assembly (system message mirrors the legacy generator)
    try:
        from prompts import prompts as prompt_singleton
        from tools.beam_actions_galfit import _build_summary_content_galfit, _galfit_system_message

        system_message = _galfit_system_message()
        component_spec = prompt_singleton.get_component_specification_galfit()
        if component_spec:
            system_message = system_message + "\n\n" + component_spec
        summary_content = _build_summary_content_galfit(
            feedme, fitted, artifacts.get("summary") or "")
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
            directives=directives or "(none)",
        )
    except Exception as e:
        return {"status": "failure", "error": f"prompt assembly failed: {e}"}

    # ---- bounded VLM + validation loop
    feedback = ""
    resp = None
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
        report = validate_survey(resp, graph, label)
        if report.ok:
            break
        resp = None
        attempts_issues.extend({"code": i.code, "message": i.message} for i in report.errors)
        feedback = report.error_feedback()
    if resp is None:
        graph.log_decision({"kind": "survey-validation-exhausted", "state": label,
                            "session": session_id, "issues": attempts_issues})
        graph.commit()
        return {"status": "failure", "error": "validation retries exhausted",
                "issues": attempts_issues, "session_id": session_id,
                "partial_analysis": raw_analysis}

    # ---- settle the verdict on the state (mechanical checks merged in code)
    vlm_verdict = resp.physicality_verdict.model_dump()
    graph.apply_verdict(label, vlm_verdict)
    state = graph.state(label)
    verdict = state.get("verdict") or vlm_verdict  # merged verdict (may mech-veto)

    # ---- archive the raw exchange for audit
    out_base = os.path.splitext(os.path.basename(comparison))[0]
    md_path = os.path.join(os.path.dirname(os.path.abspath(comparison)),
                           f"{out_base}_survey_{label}_{session_id or uuid.uuid4().hex[:8]}.md")
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

    # ---- enqueue legal candidates (floors/diversity/aging overlay)
    result_ingest = ingest(graph, resp.candidates, session_id=session_id,
                           parent_label=label,
                           verdict_fail=(verdict.get("verdict") == "FAIL"),
                           numeric_triggers=triggers)
    absence_notes = _candidate_absence_watchdog(graph, resp, triggers, label,
                                                session_id)
    graph.age_pending()  # pending entries age while a round passes
    graph.log_decision({"kind": "survey-round", "state": label, "session": session_id,
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
