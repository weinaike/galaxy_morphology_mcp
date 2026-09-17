"""MCP tool wrappers for the mechanised beam search (stage-1 set).

The orchestrator protocol (workflow_galfit_v2):

    beam_init -> run_galfit(first fit) -> beam_record_fit -> survey_round ->
    apply_candidate(action_id from survey's next_candidate) -> run_galfit ->
    beam_record_fit -> survey_round -> ... -> beam_status/termination

beam tools never run GALFIT themselves; they only maintain the state graph.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any


def _parse_json(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _load_graph(galaxy_dir: str):
    from beam.graph import BeamGraph

    return BeamGraph.load(galaxy_dir)


def beam_init(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory "
                               "(the beam graph is created at <galaxy_dir>/beam_state/graph.json)"],
    root_feedme: Annotated[str, "Absolute path of the root feedme (usually "
                                "_iter1.feedme, carrying # STRUCTURE: names and the "
                                "verbatim sky block); fitted as the first state"],
    stage1_morphology: Annotated[str, "Stage-1 VLM morphology judgement (free text; "
                                     "recorded verbatim in the graph header for digest)"] = "",
    stage1_bar_lop_json: Annotated[str, "JSON string of the detect_bar_lopsidedness "
                                       "result, e.g. {\"bar\": {\"detected\": false}, "
                                       "\"lopsidedness\": {\"detected\": true, "
                                       "\"phase_deg\": 30}}"] = "",
    temporary_constraints_json: Annotated[str, "Optional JSON list of session-scoped user "
                                              "directives, e.g. [{\"issued\": \"2026-09-04\", "
                                              "\"text\": \"companion exclusion\", "
                                              "\"forbid_structures\": [\"companion\"], "
                                              "\"active\": true}]"] = "",
    beam_width: Annotated[int, "Beam width W (queue truncation size); 0 = default 5. "
                               "Paper sensitivity study: W=1 is the greedy best-first "
                               "baseline"] = 0,
    n_max: Annotated[int, "Fit budget N_max; 0 = default 15"] = 0,
    stagnation_max: Annotated[int, "Stagnation stop threshold; 0 = default 5"] = 0,
    ablations_json: Annotated[str, "Optional JSON object selecting the ablation arm for "
                                   "this run, persisted in graph meta and enforced by "
                                   "code in every later call: {\"no_global_state\": true} "
                                   "(withhold the cross-round digest from the surveyor), "
                                   "{\"no_verdict_gate\": true} (metric-only best "
                                   "selection, physicality FAIL rounds may take s*), "
                                   "{\"single_agent\": true} (provenance marker: candidates "
                                   "come from beam_enqueue_candidates, no VLM)"] = "",
) -> dict[str, Any]:
    """Create (or reset) the beam-search state graph for a galaxy.

    Parses the root feedme into the root state A.0, validates it via
    check_feedme_file (whose warnings are returned, not fatal), measures the
    PSF once (FWHM / A_psf feed the default Re floor and the digest [Meta]),
    and persists the graph atomically. Stage-1 conclusions are stored for the
    per-round digest generation. Beam parameters (W / N_max / stagnation_max)
    and the ablation arm are written to graph meta — the single source of
    truth every later call reads, so an arm cannot drift mid-run.
    """
    try:
        from beam.graph import BeamGraph

        if not os.path.isdir(galaxy_dir):
            return {"status": "failure", "error": f"galaxy dir not found: {galaxy_dir}"}
        if not os.path.exists(root_feedme):
            return {"status": "failure", "error": f"root feedme not found: {root_feedme}"}

        psf_fwhm_px = a_psf_px2 = None
        warnings: list[str] = []
        try:
            from tools.beam_actions_galfit import check_feedme_file

            check = check_feedme_file(root_feedme)
            warnings = list(check.get("warnings", []))
            if check.get("status") == "failure":
                errors = check.get("errors") or [check.get("error", "unknown")]
                return {"status": "failure", "error": "check_feedme_file rejected the feedme",
                        "errors": errors, "warnings": warnings}
            psf_fwhm_px = check.get("psf_fwhm_px")
            a_psf_px2 = check.get("a_psf_px2")
        except Exception as e:  # PSF measurement is best-effort
            warnings.append(f"check_feedme_file/PSF measurement failed: {e}")

        stage1 = {
            "morphology": stage1_morphology,
            "detect_bar_lopsidedness": _parse_json(stage1_bar_lop_json, {}),
        }
        ablations = _parse_json(ablations_json, None)
        if ablations_json and ablations is None:
            return {"status": "failure", "error": "ablations_json is not valid JSON"}
        if ablations is not None and not isinstance(ablations, dict):
            return {"status": "failure", "error": "ablations_json must be a JSON object"}

        graph = BeamGraph.init(
            galaxy_dir, root_feedme, stage1=stage1,
            psf_fwhm_px=psf_fwhm_px, a_psf_px2=a_psf_px2,
            temporary_constraints=_parse_json(temporary_constraints_json, []),
            beam_width=beam_width or None,
            n_max=n_max or None,
            stagnation_max=stagnation_max or None,
            ablations=ablations,
        )
        meta = graph.g.graph["meta"]
        return {
            "status": "success",
            "graph_file": graph.path,
            "root_state": "A.0",
            "psf_fwhm_px": psf_fwhm_px,
            "a_psf_px2": a_psf_px2,
            "config": {k: meta.get(k) for k in
                       ("W", "N_max", "stagnation_max", "per_combo_cap", "g_min")},
            "ablations": meta.get("ablations", {}),
            "warnings": warnings,
            "message": "beam graph initialised; run the first fit (run_galfit on the root "
                       "feedme) and register it with beam_record_fit (action_id empty)",
        }
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_record_fit(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    run_galfit_result_json: Annotated[str, "JSON-serialised return dict of a successful "
                                          "run_galfit call (needs input_param_file, "
                                          "output_param_file, image_file, summary_file, "
                                          "round_status_file, fit_statistics)"],
    action_id: Annotated[str, "action_id of the executed pending candidate (empty string "
                              "for the deterministic first fit of the root feedme)"] = "",
    verdict_json: Annotated[str, "Optional JSON of the surveyor Physicality Verdict "
                                 "({\"verdict\": \"PASS|FAIL\", \"failed_checks\": [...], "
                                 "\"swap_hint\": \"none|disk_bulge_swap\"}); normally the "
                                 "verdict arrives via survey_round instead"] = "",
) -> dict[str, Any]:
    """Register a successful fit as a new state node in the beam graph.

    Creates the child state (label branch.local_round), parses the fitted
    galfit.NN inventory (names recovered from the input feedme), decodes
    effective .cons bands, computes zombie flags, updates counters /
    stagnation / mechanical best (verdict- and sub-convergence-gated), grounds
    refuted-hypothesis entries only on converged rounds, and commits.
    """
    try:
        run_result = _parse_json(run_galfit_result_json, None)
        if not isinstance(run_result, dict) or not run_result.get("fit_statistics"):
            return {"status": "failure",
                    "error": "run_galfit_result_json is not a valid run_galfit return dict"}
        verdict = _parse_json(verdict_json, None)
        graph = _load_graph(galaxy_dir)
        label = graph.record_fit(run_result, action_id=action_id, verdict=verdict)
        state = graph.state(label)
        return {
            "status": "success",
            "new_state": label,
            "metrics": state.get("metrics"),
            "zombies": state.get("zombies"),
            "combo_key": state.get("combo_key"),
            "is_best": state.get("is_best"),
            "best_state": graph.g.graph.get("best_state"),
            "counters": graph.counters(),
            "termination": graph.termination_check(),
        }
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_mark_failure(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    action_id: Annotated[str, "action_id of the candidate whose fit failed "
                              "(tool error / no comparison image / no summary)"],
    reason: Annotated[str, "Short failure reason (tool error, timeout, "
                           "check_feedme_file rejection, ...)"] = "",
) -> dict[str, Any]:
    """Record a failed fit (b.5 branch): marks the edge failed, bumps counters."""
    try:
        graph = _load_graph(galaxy_dir)
        graph.mark_failed(action_id, reason or "unspecified")
        graph.commit()
        return {"status": "success", "action_id": action_id,
                "counters": graph.counters(), "termination": graph.termination_check()}
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def apply_candidate(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    action_id: Annotated[str, "action_id of the pending candidate to transcribe "
                              "(from survey_round's next_candidate / queue)"],
) -> dict[str, Any]:
    """Mechanically transcribe a pending candidate into the next round's input.

    Parent feedme (structure template) + parent galfit.NN (warm-start backfill)
    + the candidate's primitives -> ``_iter{n}.feedme`` + ``iter{n}.cons``
    (concentric offset chain + default bound set + candidate tightenings),
    validated by check_feedme_file. The global iter id is consumed here
    (write time) and reused by beam_record_fit. On success, call
    ``run_galfit(config_file=<feedme>)`` and register the result with
    ``beam_record_fit(action_id=<action_id>)``.

    Status values: ``success`` (feedme written, run the fit), ``failure``
    (fatal; nothing was consumed), ``discarded`` (normal non-fatal outcome —
    the candidate died at a dequeue-time legality gate, e.g. the apply-time
    combo-cap re-check; treat it as "continue with the next queue entry",
    NOT as an error).
    """
    try:
        from beam.transcribe import transcribe

        graph = _load_graph(galaxy_dir)
        rec = graph.g.graph.get("pending", {}).get(action_id)
        if rec is None:
            return {"status": "failure", "error": f"unknown action_id {action_id}"}
        if rec.get("status") != "pending":
            return {"status": "failure",
                    "error": f"candidate {action_id} is '{rec.get('status')}', not pending"}

        parent = graph.state(rec["parent"])
        artifacts = parent.get("artifacts", {})
        p_feedme = artifacts.get("feedme")
        p_nn = artifacts.get("galfit_nn")
        missing = [k for k, v in (("feedme", p_feedme), ("galfit_nn", p_nn))
                   if not v or not os.path.exists(v)]
        if missing:
            return {"status": "failure",
                    "error": f"parent state {rec['parent']} lacks artefacts {missing}"}

        meta = graph.g.graph.get("meta", {})

        # R0 re-check at dequeue time (Plate0295 incident 2026-09-17): the
        # candidate was validated at enqueue, but attempts may have executed
        # since; a pending record enqueued when its combo was fresh must not
        # run after the combo hit the per-combination cap. Floor flags do
        # NOT exempt it — floor discharge is hypothesis-based
        # (graph._discharge_satisfied_floors), not cap-exempt.
        from beam.signature import apply_primitives_to_inventory, combo_identity

        cap = int(meta.get("per_combo_cap", 4))
        hypo_inv, _err = apply_primitives_to_inventory(
            parent.get("inventory", []), rec.get("primitives", []))
        if hypo_inv is not None:
            hypo_combo = combo_identity(hypo_inv)
            if graph.combo_counts().get(hypo_combo, 0) >= cap:
                graph.mark_discarded(
                    action_id,
                    f"COMBO_EXHAUSTED: {hypo_combo} reached {cap} attempts "
                    f"(apply-time R0 re-check)")
                graph.log_decision({"kind": "combo-exhausted-discard",
                                    "action_id": action_id, "combo": hypo_combo,
                                    "stage": "apply"})
                graph.commit()
                return {"status": "discarded", "action_id": action_id,
                        "combo": hypo_combo,
                        "message": "combo hit the per-combination attempt cap "
                                   "after enqueue — candidate discarded; "
                                   "continue with the next queue entry"}

        region = meta.get("fit_region")
        # Class-B mag prior for adds without a declared magnitude:
        # ~1.5 mag fainter than the parent's brightest component.
        mags = [c.get("mag") for c in parent.get("inventory", []) if c.get("mag") is not None]
        hint = (min(mags) + 1.5) if mags else None

        c = graph.counters()
        iter_id = int(c.get("global_iter_id", 0)) + 1
        out_feedme = os.path.join(galaxy_dir, f"_iter{iter_id}.feedme")
        out_cons = os.path.join(galaxy_dir, f"iter{iter_id}.cons")

        result = transcribe(
            p_feedme, p_nn, rec.get("primitives", []), out_feedme, out_cons,
            psf_fwhm_px=meta.get("psf_fwhm_px"),
            fit_region=tuple(region) if region else None,
            default_mag_hint=hint)
        if not result.ok:
            graph.mark_discarded(action_id, "E_TRANSCRIBE: " + "; ".join(result.notes))
            graph.commit()
            return {"status": "failure", "error": "transcription rejected the candidate "
                                                  "(Class-A conflict)",
                    "notes": result.notes, "action_id": action_id}

        # structural gate (mandatory before any fit)
        from tools.beam_actions_galfit import check_feedme_file
        check = check_feedme_file(out_feedme)
        if check.get("status") != "success":
            errors = check.get("errors") or [check.get("error", "unknown")]
            graph.mark_discarded(action_id, "E_CHECK_FEEDME: " + "; ".join(str(e) for e in errors))
            graph.commit()
            return {"status": "failure", "error": "check_feedme_file rejected the output",
                    "errors": errors, "feedme": out_feedme, "cons": out_cons,
                    "action_id": action_id}

        # consume the iter id, pin it on the record and mark it in-flight
        c["global_iter_id"] = iter_id
        rec["iter_id"] = iter_id
        rec["status"] = "applied"   # in-flight: awaiting run_galfit + beam_record_fit
        graph._drop_from_queue(action_id)
        graph.log_decision({"kind": "apply-candidate", "action_id": action_id,
                            "iter_id": iter_id, "feedme": out_feedme,
                            "notes": result.notes})
        graph.commit()
        return {
            "status": "success",
            "action_id": action_id,
            "iter_id": iter_id,
            "feedme": out_feedme,
            "cons": out_cons,
            "component_numbers": result.numbers,
            "check_warnings": check.get("warnings", []),
            "notes": result.notes,
            "message": "next: run_galfit(config_file=<feedme>), then "
                       "beam_record_fit(action_id=..., run_galfit_result_json=...)",
        }
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_export_note(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
) -> dict[str, Any]:
    """Regenerate working_note.md from the state graph (audit projection).

    The graph is the single source of truth; the note follows the §Multi-Branch
    working_note template (header + overwrite snapshot + ledgers + branch
    rounds + failure archive + decision log) for the best-round-verifier and
    the paper trail.
    """
    try:
        from beam.note_export import export_working_note

        graph = _load_graph(galaxy_dir)
        path = export_working_note(graph, galaxy_dir)
        return {"status": "success", "working_note": path}
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_grant_repair_budget(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    rounds: Annotated[int, "Extra fits to grant (clamped to the remaining cumulative "
                           "cap of 4)"] = 2,
    reason: Annotated[str, "Why the grant is made (verbatim into the decision log; "
                           "normally the best-round-verifier FAIL blocking issues)"] = "",
) -> dict[str, Any]:
    """Grant bounded extra fits after a best-round-verifier FAIL (defect B fix).

    The Stage-3 repair path ('fix per blocking issues, refit, re-audit') is
    otherwise dead when N_max is exhausted. Cumulative grants are capped;
    crash rounds no longer count toward stagnation, so a repair loop keeps
    moving while repair budget remains.
    """
    try:
        graph = _load_graph(galaxy_dir)
        rb = graph.grant_repair_budget(rounds, reason or "unspecified")
        graph.commit()
        return {"status": "success", "repair_budget": rb,
                "termination": graph.termination_check(),
                "message": "repair budget granted; resume the main loop "
                           "(survey_round for repair candidates if the queue is empty)"}
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_set_constraint(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    action: Annotated[str, "'add' to activate a session-scoped user constraint, "
                           "'remove' to deactivate it (by text)"],
    text: Annotated[str, "Constraint identifier/description, e.g. 'companion exclusion' "
                         "(unique key; add is idempotent on it, remove matches it)"],
    forbid_structures_json: Annotated[str, "JSON array of structure names the constraint "
                                           "forbids in candidate generation, e.g. "
                                           "[\"companion\"] (add only)"] = "[]",
    issued: Annotated[str, "Issue date (YYYY-MM-DD; defaults to today)"] = "",
) -> dict[str, Any]:
    """Add or revoke a temporary user constraint (defect D fix).

    Enforced at enqueue time: add/tune candidates touching a forbidden
    structure are discarded with E_TEMP_CONSTRAINT and logged. Without this
    registration an ACTIVE user constraint (e.g. companion exclusion) is
    invisible to the code and companion candidates enter the queue freely.
    """
    try:
        graph = _load_graph(galaxy_dir)
        forbid = _parse_json(forbid_structures_json, [])
        if not isinstance(forbid, list):
            return {"status": "failure",
                    "error": "forbid_structures_json must be a JSON array of names"}
        out = graph.set_temporary_constraint(action, text,
                                             forbid_structures=[str(s) for s in forbid],
                                             issued=issued)
        graph.commit()
        return {"status": "success", "action": action, "text": text,
                "active_constraints": out["active"]}
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_status(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
) -> dict[str, Any]:
    """Full beam-state snapshot: best state, queue (with code flags), counters,
    combo counts, refuted hypotheses, never-executed blockers, termination."""
    try:
        graph = _load_graph(galaxy_dir)
        snap = graph.snapshot()
        snap["status"] = "success"
        snap["termination"] = graph.termination_check()
        # verdict-gate health warnings (KILOGAS_231 2026-09-16 incident: a
        # driver that never settles verdicts bypasses the physicality gate)
        warnings = []

        def _has_fit(s):
            return (s.get("bic") is not None
                    or (s.get("metrics") or {}).get("bic_eff") is not None)

        fitted = [s for s in snap.get("states", []) if _has_fit(s)]

        def _settled(s):
            v = s.get("verdict")
            if isinstance(v, dict):
                return bool(v.get("verdict"))
            return bool(v)  # snapshot form: verdict is a plain string

        unsettled = [s.get("label") for s in fitted if not _settled(s)]
        if unsettled:
            warnings.append(
                f"verdict gate bypassed: {len(unsettled)} fitted state(s) without "
                f"a settled Physicality Verdict ({', '.join(map(str, unsettled[:6]))}"
                f"{'…' if len(unsettled) > 6 else ''}) — call survey_round after "
                "every beam_record_fit (the single_agent arm uses "
                "beam_enqueue_candidates instead)")
        if fitted and not snap.get("best_state"):
            warnings.append(
                "no admissible best state (best_state is null): no verdict-PASS "
                "round exists yet — locking is forbidden; settle the pending "
                "states' verdicts first")
        if warnings:
            snap["warnings"] = warnings
        return snap
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def survey_round(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    state_label: Annotated[str, "Round label to survey (e.g. 'A.2'); empty = the "
                                "most recently executed state"] = "",
    local_injections_json: Annotated[str, "Optional JSON of current-round visual "
                                          "assessments the code cannot see "
                                          "({outer_residual_sign, central_spike, "
                                          "two_peaked_original, embedded_hotspot, "
                                          "orchestrator_note})"] = "{}",
    directives: Annotated[str, "Optional orchestrator hard-constraint directives "
                               "carried verbatim into the prompt (repair restarts, "
                               "Occam validation, ...)"] = "",
    max_retries: Annotated[int, "Bounded validation-retry budget (default 2)"] = 2,
) -> dict[str, Any]:
    """Run ONE surveyor round on a fitted state (single-function VLM trigger).

    Internally: digests from the state graph -> VLM call (ANALYSIS_MODE
    dispatch) -> JSON extraction -> schema + solution-space validation with
    structured error feedback and bounded retries -> verdict settled on the
    state -> legal candidates enqueued (floors/diversity/aging overlay) ->
    returns the queue snapshot and the next candidate to execute.
    """
    try:
        from beam.survey import survey_round as _survey_round

        return _survey_round(
            galaxy_dir,
            state_label=state_label,
            local_injections=_parse_json(local_injections_json, {}),
            directives=directives,
            max_retries=max_retries,
        )
    except Exception as e:
        return {"status": "failure", "error": str(e)}


def beam_enqueue_candidates(
    galaxy_dir: Annotated[str, "Absolute path of the galaxy home directory"],
    response_json: Annotated[str, "JSON object following the surveyor contract: "
                                  "{\"physicality_verdict\": {\"verdict\": \"PASS|FAIL\", "
                                  "\"failed_checks\": [...], \"swap_hint\": \"none\"}, "
                                  "\"candidates\": [<candidate objects, 1-4, same shapes "
                                  "as the beam JSON contract>]} — authored by the "
                                  "orchestrator (who read the comparison image itself) "
                                  "in the single-agent ablation arm"],
    state_label: Annotated[str, "Round label the response refers to (e.g. 'A.2'); "
                                "empty = the most recently executed state"] = "",
) -> dict[str, Any]:
    """Single-agent ablation arm: ingest orchestrator-authored candidates (no VLM).

    Validates the provided JSON with the same schema + solution-space rules
    and enqueues through the same legality gates (floors / diversity / aging)
    as survey_round — the two arms differ ONLY in who performs the perception
    and candidate generation. The verdict is settled on the state with the
    code-computed mechanical checks merged in (idempotent if already settled
    via beam_record_fit's verdict_json).
    """
    try:
        from beam.survey import orchestrator_round

        return orchestrator_round(galaxy_dir, response_json, state_label=state_label)
    except Exception as e:
        return {"status": "failure", "error": str(e)}
