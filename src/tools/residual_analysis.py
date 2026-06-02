
import os
import uuid
from typing import Annotated, Any
import dotenv
from . import prompt
from .analyze_image import (
    read_summary_file,
)

dotenv.load_dotenv()


def component_analysis(
    #image_file: Annotated[str, "Path to the combined residual image file [png file] containing three stamps: original, model, residual"],
    comparison_file: Annotated[str, "Path to the comparison image file [png file] containing the original image, model image, 2D residual image, and 1D surface brightness profile residual plot"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    mode: Annotated[str, "Fitting mode: 'single-band' for GALFIT or 'multi-band' for GalfitS"],
    working_note_file: Annotated[str, "File path of the working_note.md to track iterative fitting progress"] = "",
    custom_instructions: Annotated[str, "Context for this round of analysis: must include (1) scientific objective of this fitting task  (2) file path of `working_note.md`"] = "",
) -> dict[str, Any]:
    """
    Analyze galaxy fitting results to determine component composition and parameter adjustments.

    This function examines the fitting result stamps (Original | Model | Residual) alongside
    the fitting summary, identifies missing or misconfigured physical components (bulge, disk,
    bar, AGN, etc.), and provides actionable suggestions for component addition/removal and
    parameter refinement.
    Args:
        comparison_file (str): Path to the comparison image file containing the original image, model image, 2D residual image, and 1D surface brightness profile residual plot
        summary_file (str): Path to the optimization summary file containing detailed fitting information
        mode (str): Fitting mode: 'single-band' for GALFIT or 'multi-band' for GalfitS
        custom_instructions (str): Context for this round of analysis: must include (1) scientific objective of this fitting task  (2) file path of `working_note.md`
                        - Model image (fitted model)
                        - Residual image (Original - Model)
                        For multi-band fitting, each band has its own set of stamps.
        summary_file (str): Path to the optimization summary file containing:
                          - Fitted parameter values and their uncertainties
                          - Chi-squared statistics and goodness-of-fit metrics
                          - Component descriptions
        mode (str): 'single-band' for GALFIT or 'multi-band' for GalfitS.
        custom_instructions (str): Required context for multi-round iterative fitting. Must contain:
            1. **Scientific objective** — the scientific goal of this fitting task (e.g., bulge-disk decomposition, bar identification, AGN detection, galaxy morphology classification).
            2. **Round history summary** — file path of `working_note.md`                 
            Additional specific requirements or constraints can also be appended.

    Returns:
        dict[str, Any]: A dictionary containing:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The diagnostic analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)
    """
    # Validate input files
    if not os.path.exists(comparison_file):
        return {"status": "failure", "error": f"Image file not found: {comparison_file}"}
    if not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {"status": "failure", "error": f"Failed to read summary file: {summary_file}"}

    # Build system message from templates
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE

    if mode == "multi-band":
        component_spec = prompt.get_component_specification_galfits()
    else:
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
            return result

    else:
        # vlm mode: multi-turn via OpenAI SDK
        from .openai_analysis import run_openai_analysis

        if working_note_file and os.path.exists(working_note_file):
            working_note_content = read_summary_file(working_note_file) or ""
            if working_note_content:
                custom_instructions += f"\n\n历史轮次的分析结果和调整决策摘要：\n{working_note_content}"

        phase_param = prompt.get_phase_parameter_review(summary_content, custom_instructions)
        turn2 = phase_param + "\n\n" + phase_reason

        prompts_list = [turn1, turn2, turn3]
        deferred_system = os.environ.get("VLM_DEFERRED_SYSTEM", "0") == "1"
        analysis, session_id, error = run_openai_analysis(
            system_prompt=system_message,
            analysis_prompts=prompts_list,
            image_path=os.path.abspath(image_file),
            deferred_system=deferred_system,
        )
        if error:
            result = {"status": "failure", "error": error}
            if session_id:
                result["session_id"] = session_id
            if analysis:
                result["partial_analysis"] = analysis
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

    require = '''
- 必须严格落实【调整决策】中的要求，基于上一轮的拟合结果的基础上调整初始参数。
    - 落实过程中，不可以私自增减（即使因为拟合出现异常），增减成分的决策权归属 component_analysis 所有。
- 落实过程中，可以对参数细节做调整，比如参数初值、fix/free等，但必须保证调整的方向和目标与 component_analysis 输出的决策完全一致。
- 决策落实完成后，及时调用 component_analysis 进行下一轮的分析和调整，直到达到满意的拟合结果为止。
'''
    return {
        "status": "success",
        "analysis": analysis + require,
        "analysis_file": output_file,
    }
