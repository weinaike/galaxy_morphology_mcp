
import os
from typing import Annotated, Any
from .analyze_image import (
    create_vlm_client,
    encode_image_to_base64,
    call_vlm_api,
)

SYSTEM_MESSAGE = """你是一位专业的星系残差不对称性分析专家。你的任务是判断残差图中是否存在需要用 1 阶傅里叶模式（Fourier mode, m=1）修正的偏心非对称结构。

必须遵循的规范：
1. 所有分析必须严格基于图片内容和拟合结果
2. 现象描述不能主观臆测，只能客观描述图像特征

工作原则：
- 先观察，后判断：先客观描述残差图特征，再给出诊断结论
- 只考虑 1 阶傅里叶模式（m=1），且只能作用于 Disk 成分 或者是单 Sersic 模型（如果没有 Disk 成分）。
- 只分析残差特征是否支持傅里叶模式，最终是否采用由调用方决定

傅里叶模式的正向指标（残差支持傅里叶模式）：
1. 偶极（Dipole）模式：Disk 区域沿某一轴线一侧正残差、对侧负残差
2. 偏心亮度分布：Disk 区域存在系统性不对称，无法由掩膜边缘、前景恒星或伴星系解释
3. 壳层（Shells）：边缘区域出现微弱的同心弧状正残差
4. 潮汐尾（Tidal tails）：边缘区域出现向外延伸的细长亮色条带

不推荐傅里叶模式的负向指标：
1. 残差呈随机噪声分布，无明显系统性结构
3. 不对称由尘埃带或前景源引起（应通过完善 Mask 解决）
4. 残差尺度较小，不显著影响整体拟合质量


傅里叶模式的初值建议：
1. 振幅 amplitude
    - 是一个无量纲的比例值 $a_m$，代表非对称结构相对于底层完美对称模型（如 Sersic 轮廓）的偏离程度。
    - 目视残差（Visual Residuals）： 观察残差图（Residual image），发现残差图中星系一侧有明显的亮斑（大约是整体亮度的 10%），你就可以把初始值设为 0.1。
    - 具体数值基于残差图中非对称结构的相对亮度来定，
2. $m=1$  相位角 phase angle（单位为度）
    - 什么是 $m=1$ 相位角？
        - 注意，不是星系整体的椭圆长轴（那是 $m=2$）
        - $m=1$ 代表星系的不对称性（Lopsidedness）或重心偏斜。
    - 想象星系是一个带有最亮核心的煎蛋，$m=1$ 就是“蛋白”向哪一侧流得最多、延伸得最远、或者哪一侧有明显的潮汐尾。你需要找到从“最亮核心”指向“额外物质最多的一侧”的方向向量。
    - 目视图像特征： * 对于 $m=1$（不对称/重心偏移）：观察星系整体朝哪个方向“偏沉”或者哪边有一条明显的潮汐尾/物质延伸。测算一下这个方向的方位角（正北为 0 度，逆时针方向）
    
输出格式：
## 1. 残差特征客观描述
（正负残差的位置、形态和尺度）

## 2. 不对称模式识别
（是否存在偏心/非对称特征，及其空间分布；是否可由掩膜/前景源/旋臂/尘埃解释）

## 3. 傅里叶模式决策
- recommend_fourier: yes/no
- 理由：（基于残差特征分析）
- 若 yes，提供傅里叶模式参数建议（am、theta_m 初始值及依据）

## 4. 科学目标差异
- 对于 JWST的数据，由于科学目标非常关心偏心结果。 所以对于 JWST 数据无论图像特征如何， recommend_fourier 都要输出 yes
- 对于 SDSS的数据，则正常判断。
"""




def fourier_mode_analysis(
    image_file: Annotated[str, "Path to the combined residual image file [png] from the BEST fitting round, containing three stamps: original, model, residual"],
    source_id: Annotated[str, "Identifier for the source/galaxy"] = "",
    custom_instructions: Annotated[str, "Optional context for this analysis, e.g. scientific objective or specific requirements"] = "",
) -> dict[str, Any]:
    """
    Analyze the residual image from the BEST fitting round to determine whether
    1st-order Fourier mode (m=1) should be applied to the Disk component.

    This tool is called at Step 4 of Phase 2 in the galaxy fitting workflow,
    AFTER the best fitting result has been selected and locked. It evaluates
    whether asymmetric patterns (dipole, shells, tidal tails) remain in the
    residuals that could be improved by Fourier mode correction on the Disk.

    Constraints:
    - Only 1st-order Fourier mode (m=1) is allowed
    - It can only be applied to the Disk component
    - This tool only analyzes residual features; the final decision on whether
      to actually apply Fourier mode is made by the caller using Occam's razor

    Args:
        image_file (str): Path to the combined image file from the best round,
                         containing three stamps: Original | Model | Residual.
        source_id (str): Identifier for the source/galaxy. e.g. sdss-Plate0271_MJD51883_Fiber005_r / jwst-obj28_s1_f277w 
                            Characterization data sources: SDSS, JWST, HST, S4G......
        custom_instructions (str): Optional context, e.g. scientific objective
                                  or specific analysis requirements.

    Returns:
        dict[str, Any]: A dictionary containing:
            - status (str): "success" if analysis completed, "failure" otherwise
            - analysis (str, optional): The Fourier mode analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis file (only on success)
    """
    # Validate input file
    if not os.path.exists(image_file):
        return {"status": "failure", "error": f"Image file not found: {image_file}"}

    # Build prompt
    prompt_text = (
        f"source_id: {source_id}\n\n"
        "请基于提供的图像，分析其中的残差图中是否存在 m=1 傅里叶模式可以修正的偏心非对称结构。按以下步骤逐步分析：\n\n"
        "步骤1：残差特征客观描述\n"
        "- 描述残差图中正残差和负残差的空间分布（位置、形态、尺度）\n"
        "- 特别关注 Disk 区域，识别是否存在偏心残差的特征：\n"
        "   - 一边蓝色，一边红色，非对称形态是偏心的典型特征\n"
        "步骤2：排除假阳性\n"
        "- Disk区域内要如果存在伴星系未被拟合，也会出现你一边蓝、一边红的情况。这种需要排除\n"
        "- Disk内的伴星系的正残差和偏心的正残差，他们存在明显的特征差异，前者表现为局部的独立源（局部中心比周围亮）后者表现为弥散的正残差，没有明显的局部中心亮点\n"
        "步骤3：傅里叶模式必要性判断\n"
        "- 基于步骤1和2，判断残差是否支持添加 m=1 傅里叶模式\n"
        "- 若支持，基于残差中非对称结构的相对亮度估算 amplitude 初值；基于非对称结构延伸方向（正北为0度，逆时针）估算 theta_m 初值\n"
        "步骤4: 格式化输出\n"
    )
    if custom_instructions:
        prompt_text += f"\n\n--- Additional requirements ---\n{custom_instructions}"

    additional_content = [{"type": "text", "text": prompt_text}]

    # Create VLM client
    client, error = create_vlm_client()
    if error:
        return {"status": "failure", "error": error}

    # Encode image
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {"status": "failure", "error": f"Failed to encode image: {image_file}"}

    # Call VLM
    analysis, error = call_vlm_api(
        client=client,  # type: ignore[arg-type]
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=SYSTEM_MESSAGE,
    )
    if error:
        return {"status": "failure", "error": error}

    # Save analysis
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_fourier_analysis.md")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis)
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file,
    }
