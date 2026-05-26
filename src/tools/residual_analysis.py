
import os
import uuid
from typing import Annotated, Any
import dotenv
from . import prompt
from .analyze_image import (
    create_vlm_client,
    encode_image_to_base64,
    read_summary_file,
    call_vlm_api,
)

dotenv.load_dotenv()


def component_analysis(
    image_file: Annotated[str, "Path to the combined residual image file [png file] containing three stamps: original, model, residual"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    mode: Annotated[str, "Fitting mode: 'single-band' for GALFIT or 'multi-band' for GalfitS"],
    custom_instructions: Annotated[str, "Context for this round of analysis: must include (1) scientific objective of this fitting task  (2) file path of `working_note.md`"] = "",
) -> dict[str, Any]:
    """
    Analyze galaxy fitting results to determine component composition and parameter adjustments.

    This function examines the fitting result stamps (Original | Model | Residual) alongside
    the fitting summary, identifies missing or misconfigured physical components (bulge, disk,
    bar, AGN, etc.), and provides actionable suggestions for component addition/removal and
    parameter refinement.
    Args:
        image_file (str): Path to the combined image file containing three stamps displayed horizontally:
                        - Original galaxy image
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
    if not os.path.exists(image_file):
        return {"status": "failure", "error": f"Image file not found: {image_file}"}
    if not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    # Read summary (needed for both modes)
    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {"status": "failure", "error": f"Failed to read summary file: {summary_file}"}

    # Build prompt and system message from the residual analysis templates

    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE

    # Append component specification based on mode
    if mode == "multi-band":
        component_spec = prompt.get_component_specification_galfits()
    else:
        component_spec = prompt.get_component_specification_galfit()

    if component_spec:
        system_message = system_message + "\n\n" + component_spec


    # ── Dispatch to the chosen analysis backend ──────────────────────
    analysis_mode = os.environ.get("ANALYSIS_MODE", "vlm").lower()
    session_id = ""

    if analysis_mode == "cc":
        if not os.environ.get("CLAUDECODE_API_KEY"):
            return {"status": "failure", "error": "ANALYSIS_MODE=cc requires CLAUDECODE_API_KEY to be set in environment"}
        from .cc_analysis import run_component_analysis_cc
        session_id = str(uuid.uuid4())

        prompts_list: list[str] = [
            f"{os.path.abspath(image_file)},查看原图图像和模型图像，分析中心星系的结构特征；重点描述二维残差图与一维轮廓图的差异特征。要求分析出真实存在的物理成分（盘、核球、侧视盘、棒、AGN核）。要求：所有现象描述必然基于图片内容， 不能主观臆测",
            f"集合拟合summmary文件：{os.path.abspath(summary_file)}，严格按照残差图分析与决策诊断树的逻辑，对成分进行分析，是否需要增加或删除成分？要求\n1。 仅关注中心区域星系图像与残差特征。2。仅关注拟合盘、核球、侧视盘、棒、AGN核这五种物理成分，仅可对这五种成分的残差添加模型成分拟合，其他残差特征可以选择保留不拟合\n 3。补充信息：{custom_instructions}",
            "只提供一个最重要的结论（不允许一次增加或删除多个成分）。调整成分的同时，如果需要同步修改其他成分的参数也需同步提供。输出格式要去：\n## 本次调整决策如下：\n1。本次调整物理目标:xxx \n2。具体内容:xxx",
        ]
        analysis, error = run_component_analysis_cc(
            system_prompt=system_message,
            analysis_prompts=prompts_list,
            session_id=session_id,
        )
        if error:
            return {"status": "failure", "error": error}

    elif analysis_mode == "acp":
        from .acp_analysis import run_component_analysis_acp

        step1 = f'''
你是一个集成了“计算机视觉特征提取”与“天体物理形态学专家推理”的自动化诊断 Agent。你的任务是基于 GALFIT 的拟合结果，通过严密的四步思维链（Chain-of-Thought），诊断当前模型的缺陷，并输出下一步的调整决策,

在这个过程中只能使用read_file 和 write_file 工具，不能使用其他工具。 write_file 可以用于编写 /tmp/todo_xxx.md来记录代办进展。

【输入信息】
1. 参数汇总：
{{os.path.abspath(summary_file)}}
2. 图像对比：
{os.path.abspath(image_file)} （包含原图、模型图、2D残差图及1D表面亮度轮廓图）
3. 补充信息：
{custom_instructions}

【执行步骤】
请你依次执行以下 4 个阶段的分析。在阶段 1 和阶段 2 中，你必须保持绝对的客观。

**阶段一：参数与运行状态审查**
1. 回顾历史轮次的目标与进展，描述当前拟合的状态（如：当前拟合的物理成分有哪些？参数的收敛情况如何？是否存在明显的异常参数值？等）。
2。遇到异常情况，需要先分析原因执行调参方案，不要急于使用奥卡姆剃刀。

**阶段二：多模态视觉特征提取（仅客观描述）**
1. 多对比原图特征描述
    - 描述原图的坐标、标注、标题等所包含的内容，不同对比度原图的差异
    - 描述中心星系包含的高概率的特征成分（强证据支持）
    - 描述未被mask掉的伴星系位置区域（明显独立的点源或者展源）
2. 2D 原图与模型的特征描述：评估两者的总体骨架轮廓是否一致，差异点再哪里？
3. 2D 残差图-核心区：描述中心区域残差的空间分布形态。
4. 2D 残差图-外围区：
    - 描述外围是否存在伴星系或者独立的点源，对于伴星系，描述其位置坐标，
    - 描述外围残差的空间分布特征（如同心环、同心弧、条带、随机分布等），以及残差的符号特征（正残差或负残差）。
5. 1D 亮度曲线与残差
    - 描述图表的坐标、标注、标题等所包含的内容，
    - 如果 sky 成分存在， 描述 sky 成分星等线与 sky background 虚线的关系（齐平、偏高或者偏低）
    - 描述 Data 与 Model 之间明显差异的区域（如中心过亮或过暗，某个半径范围内的系统偏亮或偏暗等）
    - 描述各成分的星等差异、以及残差曲线与各成分 Re 的对应关系（如残差的峰值位置是否与某个成分的 Re 对应等）


**阶段三：基于<星系成分分析的总体流程>开展推理（专家思维链CoT）**
- 结合 2D 和 1D 的分析，描述当前模型在拟合中心星系的结构特征方面的缺陷（如：是否存在明显的过拟合或者欠拟合？是否存在明显的成分缺失？是否存在明显的成分冗余？等）
- 依据<系成分分析的总体流程>逐条分析，发现当前拟合存在哪些问题有待改进

**阶段四：决策动作输出**
- 基于阶段三的分析，推理下一步动作（最紧迫）：
    - 必须提供中心星系成分调整建议：增加、删除或者维持.(增减数量必须小于或等于 1 个成分)
    - 必须附带具体的参数调整方案。
    - 必须确认以下内容：
        - 1. 如果存在 SKY成分， <sky> 是否已经 fix, 并且于 sky background 齐平。
        - 2. 伴星系是否均已添加到模型中。如果没有，需要给出具体的添加参数，这个不受中心星系增减成分（小于或等于 1 个成分）的约束
        - 3. 星系外围是否存在明显的对称残差遗留，外围对称残差优先级高于中心PSF。

- 遵循<调参策略>的一般规范，结合实际的残差特征，输出针对性的具体参数参数调整方案。

- 输出格式要求：
    ···
    ## 本次调整决策如下：
    # 1.本次调整物理目标:xxx 
    # 2.完备的初始参数:
    | 成分 | x | y | n | mag | Re | ba | pa |
    | --- | --- | --- | --- | --- | --- | --- | --- |
    | Disk | xxx | xxx | xxx | xxx | xxx | xxx | xxx |
    | Bulge | xxx | xxx | xxx | xxx | xxx | xxx | xxx |
    | Bar | xxx | xxx | xxx | xxx | xxx | xxx | xxx |
    | AGN | xxx | xxx | xxx | xxx | xxx | xxx | xxx |

    ···

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
        analysis_prompt = prompt.get_residual_analysis_prompt(summary_content)
        if custom_instructions:
            analysis_prompt += f"\n\n--- Additional requirements ---\n{custom_instructions}"        
        # --- VLM mode (original single-shot path) ---
        client, error = create_vlm_client()
        if error:
            return {"status": "failure", "error": error}

        base64_image = encode_image_to_base64(image_file)
        if not base64_image:
            return {"status": "failure", "error": f"Failed to encode image: {image_file}"}

        vlm_prompt = f'残差图文件路径：{image_file}' + analysis_prompt
        additional_content = [{"type": "text", "text": vlm_prompt}]

        analysis, error = call_vlm_api(
            client=client,  # type: ignore[arg-type]
            base64_image=base64_image,
            additional_content=additional_content,
            system_message=system_message,
        )
        if error:
            return {"status": "failure", "error": error}

    # analysis is guaranteed to be str when error is None
    assert analysis is not None, "Analysis should not be None when error is None"

    # Save analysis
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    if session_id:
        output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_component_analysis_{session_id}.md")
    else:
        output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_component_analysis.md")

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
