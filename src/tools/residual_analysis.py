
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
    """从某个输出文件路径向上定位星系主目录（首个含 output/ 子目录的祖先）。"""
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
    """把单次 VLM 分析的 timing 追加写到星系目录下的 timing_log.md，便于跨轮汇总。

    默认不写文件（避免污染星系目录）；在 .env 中设置 VLM_TIMING_LOG=1 开启。
    计时数据始终可通过 stdout 与 result["timing"] 获取，不受此开关影响。
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

        cc_summary = f"请使用 read_file 工具读取参数摘要文件：{os.path.abspath(summary_file)}"
        cc_instructions = custom_instructions
        if working_note_file:
            cc_instructions += f"\n\n历史轮次的分析结果和调整决策摘要记录在 {working_note_file}"

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
            custom_instructions += f"\n\n历史轮次的分析结果和调整决策摘要记录在 {working_note_file}"

        phase_param = prompt.get_phase_parameter_review(
            f"请使用 read_file 工具读取参数摘要文件：{os.path.abspath(summary_file)}",
            custom_instructions,
        )

        step1 = f'''
你是一个集成了"计算机视觉特征提取"与"天体物理形态学专家推理"的自动化诊断 Agent。你的任务是基于 GALFITS 的拟合结果，通过严密的四步思维链（Chain-of-Thought），诊断当前模型的缺陷，并输出下一步的调整决策。

在这个过程中只能使用read_file 和 write_file 工具，不能使用其他工具。 write_file 可以用于编写 /tmp/todo_xxx.md来记录代办进展。

【输入文件】
- 图像文件：{os.path.abspath(comparison_file)}（包含原图、模型图、2D残差图及1D表面亮度轮廓图）

请使用 read_file 工具读取上述文件后，依次执行以下 4 个阶段的分析。在阶段 1 和阶段 2 中，你必须保持绝对的客观。

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
                custom_instructions += f"\n\n历史轮次的分析结果和调整决策摘要：\n{working_note_content}"

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
- 必须严格落实【调整决策】中的要求，基于上一轮的拟合结果的基础上调整初始参数。
    - 落实过程中，不可以私自增减（即使因为拟合出现异常），增减成分的决策权归属 component_analysis 所有。
- 落实过程中，可以对参数细节做调整，比如参数初值、fix/free等，但必须保证调整的方向和目标与 component_analysis 输出的决策完全一致。
- 决策落实完成后，及时调用 component_analysis 进行下一轮的分析和调整，直到达到满意的拟合结果为止。
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

        cc_summary = f"请使用 read_file 工具读取参数摘要文件：{os.path.abspath(summary_file)}"
        cc_instructions = custom_instructions
        if working_note_file:
            cc_instructions += f"\n\n历史轮次的分析结果和调整决策摘要记录在 {working_note_file}"

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
            custom_instructions += f"\n\n历史轮次的分析结果和调整决策摘要记录在 {working_note_file}"

        phase_param = prompt.get_phase_parameter_review(
            f"请使用 read_file 工具读取参数摘要文件：{os.path.abspath(summary_file)}",
            custom_instructions,
        )

        step1 = f'''
你是一个集成了"计算机视觉特征提取"与"天体物理形态学专家推理"的自动化诊断 Agent。你的任务是基于 GALFIT 的拟合结果，通过严密的四步思维链（Chain-of-Thought），诊断当前模型的缺陷，并输出下一步的调整决策。

在这个过程中只能使用read_file 和 write_file 工具，不能使用其他工具。 write_file 可以用于编写 /tmp/todo_xxx.md来记录代办进展。

【输入文件】
- 图像文件：{os.path.abspath(image_file)}（包含原图、模型图、2D残差图及1D表面亮度轮廓图）

请使用 read_file 工具读取上述文件后，依次执行以下 4 个阶段的分析。在阶段 1 和阶段 2 中，你必须保持绝对的客观。

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
                custom_instructions += f"\n\n历史轮次的分析结果和调整决策摘要：\n{working_note_content}"

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

# 决策落实守则
- 必须严格落实【调整决策】中的要求，基于上一轮的拟合结果的基础上调整初始参数。
    - 落实过程中，不可以私自增减（即使因为拟合出现异常），增减成分的决策权归属 component_analysis 所有。
    - 若【调整决策】涉及到多个成分增减，需要遵循一次只增减一个成分的原则，避免陷入局部最优或者拟合崩溃。
        - example1: 目标：sersic->(expdisk + F1 + Bulge);实际要分三步：sersic->expdisk->(expdisk + F1)->(expdisk + F1 + Bulge)
        - example2: 目标：sersic->(expdisk + companion);实际要分两步：sersic->expdisk->(expdisk + companion)        
        - 这里的一次是执行一次 run_galfit，一个调整策略可以执行多次run_galfit，调整与执行的优先级持续参考 example1、example2 的顺序。(后步骤要基于前步骤的输出结果参数基础上继续调整)
    - 落实过程中，可以对参数细节做调整，比如参数初值、fix/free等，但必须保证调整的方向和目标与 component_analysis 输出的决策完全一致。
- 决策落实（多次调参拟合）完成后，无论结果如何，都必须必须向 component_analysis 汇报进展并获取下一轮的分析并获取调整意见，严禁私自决定。
- 你可以怀疑 component_analysis 的判断，但只能保留意见，必须严格执行 component_analysis 的决策，直到所有决策落地执行（可以反馈意见，严禁私自篡改决策）。
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
        _br_label = _best_info.get("best_round_label") or "未知轮次"
        _br_id = f"round {_br}（{_br_label}）" if _br is not None else _br_label
        result["best_round_judge"] = (
            f"对比图像，最优轮次判断（{_best_info.get('status')}）：当前最优轮次为 {_br_id}。"
        )
        if os.environ.get("BEST_ROUND_VERBOSE") == "1":
            if _best_info.get("verdict") is not None:
                result["best_round_verdict"] = _best_info["verdict"]
            if _best_info.get("comparison_text") is not None:
                result["best_round_comparison"] = _best_info["comparison_text"]
    return result
