
import os
import uuid
from typing import Annotated, Any
import dotenv
from . import prompt
from .analyze_image import (
    read_summary_file,
)

from .parse_lyric import parse_image_infos_from_lyric, extract_component_attributes
from . import best_round_registry as _brr

dotenv.load_dotenv()


def _maybe_fetch_reference_blocks(image_file: str):
    """Best-effort visualRAG Few-shot retrieval for VLM turn-1.

    Queries the visualRAG service for the galaxy behind `image_file` and returns
    ``(reference_blocks, reference_intro)`` for ``run_openai_analysis``. Any
    failure (service disabled/down, empty results) returns ``(None, None)`` so
    the caller degrades to the legacy single-image turn-1.
    """
    try:
        from . import visualrag_client as vrag
        resp = vrag.query_service(image_file)
        if resp and (resp.get("baseline") or resp.get("positive")
                     or resp.get("hard_negatives")):
            blocks = vrag.fetch_reference_images(resp)
            if blocks:
                return blocks, vrag.REFERENCE_INTRO
    except Exception as e:  # noqa: BLE001
        print(f"[visualRAG] reference fetch failed, degrading to no-reference: {e}")
    return None, None


def _galaxy_dir_of(path: str) -> str:
    """Locate the galaxy home directory (first ancestor containing an output/ subdirectory) from an output-file path."""
    p = os.path.dirname(os.path.abspath(path))
    for _ in range(6):
        if os.path.isdir(os.path.join(p, "output")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return os.path.dirname(os.path.abspath(path))


def _persist_timing(ref_path: str, timing: dict) -> None:
    """Append the per-call VLM timing to timing_log.md in the galaxy directory for cross-round aggregation.

    Off by default (keeps galaxy directories clean); enable with VLM_TIMING_LOG=1 in .env.
    Timing data is always available via stdout and result["timing"] regardless of this switch.
    """
    if os.environ.get("VLM_TIMING_LOG", "0") != "1":
        return
    try:
        round_label = os.path.basename(os.path.dirname(os.path.abspath(ref_path)))
        gdir = _galaxy_dir_of(ref_path)
        tlog = os.path.join(gdir, "timing_log.md")
        lines = [f"## {round_label} | wall={timing.get('wall_time_s')}s"]
        for t in timing.get("turns", []):
            lines.append(
                f"- turn{t['turn']}: {t['duration_s']}s "
                f"(prompt={t['prompt_tokens']}, completion={t['completion_tokens']}, "
                f"{t['tok_per_s']} tok/s)"
            )
        with open(tlog, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    except Exception as e:  # noqa: BLE001
        print(f"Warning: failed to write timing log: {e}")


def analyze_multiband_components(
    lyric_file: Annotated[str, "Path to the lyric file containing the input information for multi-band fitting"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    comparison_file: Annotated[str, "Path to the comparison image file [png file] containing the original image, model image, 2D residual image, and 1D surface brightness profile residual plot"],
    working_note_file: Annotated[str, "File path of the working_note.md to track iterative fitting progress"] = "",
    custom_instructions: Annotated[str, "Context for this round of analysis: must include (1) scientific objective of this fitting task  (2) file path of `working_note.md`"] = "",
):
    # Validate input files
    if not os.path.exists(lyric_file):
        return {"status": "failure", "error": f"Lyric file not found: {lyric_file}"}
    if not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}
    if not os.path.exists(comparison_file):
        return {"status": "failure", "error": f"Comparison file not found: {comparison_file}"}

    summary_content = ""
    image_infos = parse_image_infos_from_lyric(lyric_file)
    for image_info in image_infos:
        components = extract_component_attributes(summary_file=summary_file, config_file=lyric_file, fits_file=image_info.image[0], band=image_info.band)
        summary_content += f"=== Band: {image_info.band} ===\n"
        for component in components:
            summary_content += f"- Component: {component['name']}\n"
            summary_content += f"  - Type: {component['type']}\n"
            summary_content += f"  - Parameters:\n"
            for param_name, param_value in component.items():
                if param_name not in ['name', 'type']:
                    summary_content += f"    - {param_name}: {param_value}\n"
        summary_content += "\n"
        
    if not summary_content:
        return {"status": "failure", "error": f"Failed to read summary file: {summary_file}"}

    # Build system message from templates
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE

    # Maintain best-round memory for this galaxy (visual-primary VLM comparison).
    _best_info = _brr.update_best_round_for_call(
        image_path=comparison_file, summary_path=summary_file, lyric_file=lyric_file)

    component_spec = prompt.get_component_specification_galfits()

    if component_spec:
        system_message = system_message + "\n\n" + component_spec

    # ── Dispatch to the chosen analysis backend ──────────────────────
    analysis_mode = os.environ.get("ANALYSIS_MODE", "vlm").lower()
    session_id = ""

    # Load shared phase templates
    phase_visual = prompt.get_phase_visual_extraction()
    phase_reason = prompt.get_phase_expert_reasoning()
    phase_decision = prompt.get_phase_decision_output()

    # Build 3-turn prompts from 4 phases (shared by all modes)
    turn1 = phase_visual
    turn3 = phase_decision

    if analysis_mode == "cc":
        # CC mode: agent can read files itself, pass file paths as summary_content
        if not os.environ.get("CLAUDECODE_API_KEY"):
            return {"status": "failure", "error": "ANALYSIS_MODE=cc requires CLAUDECODE_API_KEY to be set in environment"}
        from .cc_analysis import run_component_analysis_cc
        session_id = str(uuid.uuid4())

        cc_summary = f"Use the read_file tool to read the parameter summary file: {os.path.abspath(summary_file)}"
        cc_instructions = custom_instructions
        if working_note_file:
            cc_instructions += f"\n\nSummaries of previous rounds' analyses and adjustment decisions are recorded in {working_note_file}"

        phase_param = prompt.get_phase_parameter_review(cc_summary, cc_instructions)
        turn2 = phase_param + "\n\n" + phase_reason

        prompts_list: list[str] = [turn1, turn2, turn3]
        analysis, error = run_component_analysis_cc(
            system_prompt=system_message,
            analysis_prompts=prompts_list,
            session_id=session_id,
        )
        if error:
            return {"status": "failure", "error": error}

    elif analysis_mode == "acp":
        # ACP mode: single mega-prompt, agent can read files itself
        timing = None
        from .acp_analysis import run_component_analysis_acp
        if working_note_file:
            custom_instructions += f"\n\nSummaries of previous rounds' analyses and adjustment decisions are recorded in {working_note_file}"

        phase_param = prompt.get_phase_parameter_review(
            f"Use the read_file tool to read the parameter summary file: {os.path.abspath(summary_file)}",
            custom_instructions,
        )

        step1 = f'''
You are an automated diagnostic agent that combines computer-vision feature extraction with expert astrophysical morphology reasoning. Given the GalfitS fitting results, diagnose the deficiencies of the current model through a rigorous four-step chain of thought (CoT) and output the next adjustment decision.

During this process you may only use the read_file and write_file tools and no others. write_file may be used to maintain a progress checklist at /tmp/todo_xxx.md.

[Input files]
- Image file: {os.path.abspath(comparison_file)} (contains the original image, model image, 2D residual map and 1D surface-brightness profile)

After reading the file above with read_file, carry out the following 4 analysis phases in order. In Phases 1 and 2 you must remain strictly objective.

{phase_visual}

{phase_param}

{phase_reason}

{phase_decision}
'''
        prompts_list: list[str] = [step1]
        analysis, session_id, error = run_component_analysis_acp(
            system_prompt=system_message,
            analysis_prompts=prompts_list,
        )
        if error:
            result = {"status": "failure", "error": error}
            if session_id:
                result["session_id"] = session_id
            if analysis:
                result["partial_analysis"] = analysis
            if timing:
                result["timing"] = timing
            return result

    else:
        # vlm mode: multi-turn via OpenAI SDK
        from .openai_analysis import run_openai_analysis

        if working_note_file and os.path.exists(working_note_file):
            working_note_content = read_summary_file(working_note_file) or ""
            if working_note_content:
                custom_instructions += f"\n\nSummaries of previous rounds' analyses and adjustment decisions:\n{working_note_content}"

        # Soft "best-round regression" reference (only present when the comparison
        # judged the current round worse than the historical best). Fed into turn-2
        # (parameter review / reasoning), never turn-1 visual extraction.
        if _best_info and _best_info.get("comparison_conclusion"):
            custom_instructions += "\n\n" + _best_info["comparison_conclusion"]
        phase_param = prompt.get_phase_parameter_review(summary_content, custom_instructions)
        turn2 = phase_param + "\n\n" + phase_reason

        prompts_list = [turn1, turn2, turn3]
        deferred_system = os.environ.get("VLM_DEFERRED_SYSTEM", "0") == "1"
        ref_blocks, ref_intro = _maybe_fetch_reference_blocks(comparison_file)
        try:
            analysis, session_id, error, timing = run_openai_analysis(
                system_prompt=system_message,
                analysis_prompts=prompts_list,
                image_path=os.path.abspath(comparison_file),
                deferred_system=deferred_system,
                reference_blocks=ref_blocks,
                reference_intro=ref_intro,
            )
        finally:
            from . import visualrag_client as _vrag
            _vrag.cleanup_reference_images(ref_blocks)
        if error:
            result = {"status": "failure", "error": error}
            if session_id:
                result["session_id"] = session_id
            if analysis:
                result["partial_analysis"] = analysis
            if timing:
                result["timing"] = timing
            return result

    # analysis is guaranteed to be str when error is None
    assert analysis is not None, "Analysis should not be None when error is None"

    # Save analysis
    base_name = os.path.splitext(os.path.basename(comparison_file))[0]
    if session_id:
        output_file = os.path.join(os.path.dirname(comparison_file), f"{base_name}_component_analysis_{session_id}.md")
    else:
        output_file = os.path.join(os.path.dirname(comparison_file), f"{base_name}_component_analysis.md")
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis)
        print(f"Component analysis saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    if _best_info is not None:
        _brr.attach_analysis_to_best(comparison_file, analysis)

    require = '''
- The requirements in [Adjustment Decision] must be implemented strictly, adjusting initial parameters on the basis of the previous round's fitting results.
    - During implementation you may not add or remove components on your own (even if the fit misbehaves); the authority to add/remove components belongs solely to component_analysis.
- Parameter details may be fine-tuned during implementation (initial values, fix/free status, etc.), but the direction and goal of every change must remain fully consistent with the decision output by component_analysis.
- Once a decision has been implemented, promptly call component_analysis for the next round of analysis and adjustment until a satisfactory fit is reached.
'''
    result = {
        "status": "success",
        "analysis": analysis + require,
        "analysis_file": output_file,
    }
    if timing:
        result["timing"] = timing
        _persist_timing(comparison_file, timing)
    if _best_info is not None:
        result["best_round_status"] = _best_info.get("status")
        result["best_round"] = _best_info.get("best_round")
        result["best_round_label"] = _best_info.get("best_round_label")
        if os.environ.get("BEST_ROUND_VERBOSE") == "1":
            if _best_info.get("verdict") is not None:
                result["best_round_verdict"] = _best_info["verdict"]
            if _best_info.get("comparison_text") is not None:
                result["best_round_comparison"] = _best_info["comparison_text"]
    return result


def component_analysis(
    image_file: Annotated[str, "Path to the combined residual image file [png file] containing three stamps: original, model, residual"],    
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    working_note_file: Annotated[str, "File path of the working_note.md to track iterative fitting progress"] = "",
    custom_instructions: Annotated[str, "Context for this round of analysis: must include scientific objective of this fitting task"] = "",
) -> dict[str, Any]:
    """
    Analyze galaxy fitting results to determine component composition and parameter adjustments.

    This function examines the fitting result stamps (Original | Model | Residual) alongside
    the fitting summary, identifies missing or misconfigured physical components (bulge, disk,
    bar, AGN, etc.), and provides actionable suggestions for component addition/removal and
    parameter refinement.
    Args:
        image_file (str): Path to the combined residual image file containing three stamps: original, model, residual
        summary_file (str): Path to the optimization summary file containing detailed fitting information
        working_note_file (str): File path of the working_note.md to track iterative fitting progress
        custom_instructions (str): Context for this round of analysis: must include (1) scientific objective of this fitting task

        summary_file (str): Path to the optimization summary file containing:
                          - Fitted parameter values and their uncertainties
                          - Chi-squared statistics and goodness-of-fit metrics
                          - Component descriptions
        custom_instructions (str): Required context for multi-round iterative fitting. Must contain:
            1. **Scientific objective** — the scientific goal of this fitting task (e.g., bulge-disk decomposition, 
                bar identification, AGN detection, galaxy morphology classification).

    Returns:
        dict[str, Any]: A dictionary containing:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The diagnostic analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)
    """
    # Validate input files
    if not os.path.exists(image_file):
        return {"status": "failure", "error": f"Image file not found: {image_file}"}
    if not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {"status": "failure", "error": f"Failed to read summary file: {summary_file}"}

    # Maintain best-round memory for this galaxy (visual-primary VLM comparison).
    _best_info = _brr.update_best_round_for_call(
        image_path=image_file, summary_path=summary_file, lyric_file=None)

    # Build system message from templates
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE

    component_spec = prompt.get_component_specification_galfit()

    if component_spec:
        system_message = system_message + "\n\n" + component_spec

    # ── Dispatch to the chosen analysis backend ──────────────────────
    analysis_mode = os.environ.get("ANALYSIS_MODE", "vlm").lower()
    session_id = ""

    # Load shared phase templates
    phase_visual = prompt.get_phase_visual_extraction()
    phase_reason = prompt.get_phase_expert_reasoning()
    phase_decision = prompt.get_phase_decision_output()

    # Build 3-turn prompts from 4 phases (shared by all modes)
    turn1 = phase_visual
    turn3 = phase_decision

    if analysis_mode == "cc":
        # CC mode: agent can read files itself, pass file paths as summary_content
        if not os.environ.get("CLAUDECODE_API_KEY"):
            return {"status": "failure", "error": "ANALYSIS_MODE=cc requires CLAUDECODE_API_KEY to be set in environment"}
        from .cc_analysis import run_component_analysis_cc
        session_id = str(uuid.uuid4())

        cc_summary = f"Use the read_file tool to read the parameter summary file: {os.path.abspath(summary_file)}"
        cc_instructions = custom_instructions
        if working_note_file:
            cc_instructions += f"\n\nSummaries of previous rounds' analyses and adjustment decisions are recorded in {working_note_file}"

        phase_param = prompt.get_phase_parameter_review(cc_summary, cc_instructions)
        turn2 = phase_param + "\n\n" + phase_reason

        prompts_list: list[str] = [turn1, turn2, turn3]
        analysis, error = run_component_analysis_cc(
            system_prompt=system_message,
            analysis_prompts=prompts_list,
            session_id=session_id,
        )
        if error:
            return {"status": "failure", "error": error}

    elif analysis_mode == "acp":
        # ACP mode: single mega-prompt, agent can read files itself
        timing = None
        from .acp_analysis import run_component_analysis_acp
        if working_note_file:
            custom_instructions += f"\n\nSummaries of previous rounds' analyses and adjustment decisions are recorded in {working_note_file}"

        phase_param = prompt.get_phase_parameter_review(
            f"Use the read_file tool to read the parameter summary file: {os.path.abspath(summary_file)}",
            custom_instructions,
        )

        step1 = f'''
You are an automated diagnostic agent that combines computer-vision feature extraction with expert astrophysical morphology reasoning. Given the GALFIT fitting results, diagnose the deficiencies of the current model through a rigorous four-step chain of thought (CoT) and output the next adjustment decision.

During this process you may only use the read_file and write_file tools and no others. write_file may be used to maintain a progress checklist at /tmp/todo_xxx.md.

[Input files]
- Image file: {os.path.abspath(image_file)} (contains the original image, model image, 2D residual map and 1D surface-brightness profile)

After reading the file above with read_file, carry out the following 4 analysis phases in order. In Phases 1 and 2 you must remain strictly objective.

{phase_visual}

{phase_param}

{phase_reason}

{phase_decision}
'''
        prompts_list: list[str] = [step1]
        analysis, session_id, error = run_component_analysis_acp(
            system_prompt=system_message,
            analysis_prompts=prompts_list,
        )
        if error:
            result = {"status": "failure", "error": error}
            if session_id:
                result["session_id"] = session_id
            if analysis:
                result["partial_analysis"] = analysis
            if timing:
                result["timing"] = timing
            return result

    else:
        # vlm mode: multi-turn via OpenAI SDK
        from .openai_analysis import run_openai_analysis

        if working_note_file and os.path.exists(working_note_file):
            working_note_content = read_summary_file(working_note_file) or ""
            if working_note_content:
                custom_instructions += f"\n\nSummaries of previous rounds' analyses and adjustment decisions:\n{working_note_content}"

        # Soft "best-round regression" reference (only present when the comparison
        # judged the current round worse than the historical best). Fed into turn-2
        # (parameter review / reasoning), never turn-1 visual extraction.
        if _best_info and _best_info.get("comparison_conclusion"):
            custom_instructions += "\n\n" + _best_info["comparison_conclusion"]
        phase_param = prompt.get_phase_parameter_review(summary_content, custom_instructions)
        turn2 = phase_param + "\n\n" + phase_reason

        prompts_list = [turn1, turn2, turn3]
        deferred_system = os.environ.get("VLM_DEFERRED_SYSTEM", "0") == "1"
        ref_blocks, ref_intro = _maybe_fetch_reference_blocks(image_file)
        try:
            analysis, session_id, error, timing = run_openai_analysis(
                system_prompt=system_message,
                analysis_prompts=prompts_list,
                image_path=os.path.abspath(image_file),
                deferred_system=deferred_system,
                reference_blocks=ref_blocks,
                reference_intro=ref_intro,
            )
        finally:
            from . import visualrag_client as _vrag
            _vrag.cleanup_reference_images(ref_blocks)
        if error:
            result = {"status": "failure", "error": error}
            if session_id:
                result["session_id"] = session_id
            if analysis:
                result["partial_analysis"] = analysis
            if timing:
                result["timing"] = timing
            return result

    # analysis is guaranteed to be str when error is None
    assert analysis is not None, "Analysis should not be None when error is None"
    
    require = '''

# Decision-implementation rules
- The requirements in [Adjustment Decision] must be implemented strictly, adjusting initial parameters on the basis of the previous round's fitting results.
    - During implementation you may not add or remove components on your own (even if the fit misbehaves); the authority to add/remove components belongs solely to component_analysis.
    - When [Adjustment Decision] involves adding or removing several components, follow the one-component-per-step principle to avoid local minima or fit breakdown.
        - example1: target sersic->(expdisk + F1 + Bulge) must be split into three steps: sersic->expdisk->(expdisk + F1)->(expdisk + F1 + Bulge)
        - example2: target sersic->(expdisk + companion) must be split into two steps: sersic->expdisk->(expdisk + companion)
        - One "step" here means one run_galfit execution; a single adjustment strategy may involve multiple run_galfit calls, and the ordering always follows example1/example2 (each later step builds on the output parameters of the previous step).
    - Parameter details may be fine-tuned during implementation (initial values, fix/free status, etc.), but the direction and goal of every change must remain fully consistent with the decision output by component_analysis.
- After implementing the decisions (possibly over several tuning fits), you must report progress to component_analysis and obtain the next round of analysis and adjustment suggestions regardless of the outcome; deciding on your own is strictly forbidden.
- You may doubt component_analysis's judgement, but you may only register your reservation: its decisions must be executed exactly until all decisions have been carried out (feedback is allowed; silently altering decisions is strictly forbidden).
'''
    # Save analysis
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    if session_id:
        output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_component_analysis_{session_id}.md")
    else:
        output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_component_analysis.md")
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis + require)
        print(f"Component analysis saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    if _best_info is not None:
        _brr.attach_analysis_to_best(image_file, analysis)


    result = {
        "status": "success",
        "analysis": analysis + require,
        "analysis_file": output_file,
    }
    if timing:
        result["timing"] = timing
        _persist_timing(image_file, timing)
    if _best_info is not None:
        _br = _best_info.get("best_round")
        _br_label = _best_info.get("best_round_label") or "unknown round"
        _br_id = f"round {_br} ({_br_label})" if _br is not None else _br_label
        result["best_round_judge"] = (
            f"Best-round judgement from the comparison images ({_best_info.get('status')}): "
            f"the current best round is {_br_id}."
        )
        if os.environ.get("BEST_ROUND_VERBOSE") == "1":
            if _best_info.get("verdict") is not None:
                result["best_round_verdict"] = _best_info["verdict"]
            if _best_info.get("comparison_text") is not None:
                result["best_round_comparison"] = _best_info["comparison_text"]
    return result
