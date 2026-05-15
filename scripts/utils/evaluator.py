import os
import re
import math
import random
import logging

# 配置日志，方便追踪裁判的打分过程
logging.basicConfig(level=logging.INFO, format='[Evaluator] %(message)s')

# ================= 核心算法参数配置 =================
# 当 Delta BIC 的绝对值大于此阈值时，认为统计学指标足够置信，跳过大模型
BIC_CONFIDENCE_THRESHOLD = 5.0 

# 定义什么样的分数才算是“显著变坏”，用于抽取 a_bad
BAD_SCORE_THRESHOLD = -3.0  

# 视觉模型得分转换到 BIC 尺度的权重乘子（需根据实际 VLM 输出范围调优）
VLM_REWARD_WEIGHT = 10.0  
# ===================================================

def calculate_bic(summary_path):
    """
    解析 summary.md，提取参数计算 BIC。
    这里提供一个正则解析模板，你需要根据你的真实 summary 格式微调。
    公式: BIC = Chi^2 + k * ln(N_pix)
    """
    if not os.path.exists(summary_path):
        return 9999.0
        
    try:
        with open(summary_path, 'r') as f:
            content = f.read()
            
        # 提取 Reduced Chi^2 (chi^2_nu)
        chi2_nu_match = re.search(r'Chi\^2/nu\s*\|\s*([\d\.]+)', content)
        chi2_nu = float(chi2_nu_match.group(1)) if chi2_nu_match else 999.0
        
        # 提取自由度 N_DOF (通常 summary 里会有 N_DOF 或相关像素数)
        # 假设提取到了 ndof 和 n_pix，这里用伪代码占位
        # n_pix = extract_npix(...)
        # ndof = extract_ndof(...)
        # k = n_pix - ndof
        
        # 临时占位：假设我们简化计算，仅用 chi2_nu 代表基线
        # 你后续需要补全真实的 BIC 计算逻辑
        pseudo_bic = chi2_nu 
        return pseudo_bic
        
    except Exception as e:
        logging.error(f"BIC 计算失败 {summary_path}: {e}")
        return 9999.0

def get_vlm_reward(base_png_path, variant_png_path):
    """
    Level 2: 视觉大模型复核。
    输入原图和变体图，返回一个软奖励得分。分数越高代表 variant 越比 base 好。
    """
    # TODO: 接入 Qwen-VL-Reward 或其他视觉大模型 API
    # 模拟返回一个 [-1.0, 1.0] 的随机分数用于测试
    # return random.uniform(-1.0, 1.0)
    return 0.0

def evaluate_variants(curr_state, variants):
    """
    双层漏斗裁判法庭：统计学过滤 -> 视觉大模型复核
    """
    if not variants:
        return None, None, True

    base_png = curr_state["png_path"]
    # 确保当前状态包含基线 BIC
    if "bic" not in curr_state:
        curr_state["bic"] = calculate_bic(curr_state["summary_path"])
    base_bic = curr_state["bic"]

    evaluated_vars = []

    for var in variants:
        # -----------------------------------------------------
        # Level 1: 统计学硬指标 (Chi2 & BIC)
        # -----------------------------------------------------
        var_bic = calculate_bic(var["summary_path"])
        var["bic"] = var_bic
        
        # 定义改善量：BIC 越小越好，所以 base - var 为正表示变好
        delta_bic = base_bic - var_bic
        var["delta_bic"] = delta_bic
        
        # -----------------------------------------------------
        # 分流判定：是否需要大模型介入？
        # -----------------------------------------------------
        if abs(delta_bic) > BIC_CONFIDENCE_THRESHOLD:
            # 数据极其显著，直接打标，无需调用大模型
            var["final_score"] = delta_bic
            var["vlm_called"] = False
            logging.info(f"[{var['variant_name']}] Level 1 判定: delta_bic={delta_bic:.3f} (High Confidence)")
        else:
            # 数据模棱两可，进入 Level 2 让大模型用“眼睛”看
            # -----------------------------------------------------
            # Level 2: 视觉大模型软指标 (VLM Reward)
            # -----------------------------------------------------
            vlm_score = get_vlm_reward(base_png, var["png_path"])
            var["vlm_score"] = vlm_score
            var["vlm_called"] = True
            
            # 权重融合：综合微小的数学改善与大模型的视觉偏好
            var["final_score"] = delta_bic + (vlm_score * VLM_REWARD_WEIGHT)
            logging.info(f"[{var['variant_name']}] Level 2 介入: delta_bic={delta_bic:.3f}, vlm_score={vlm_score:.3f} -> final={var['final_score']:.3f}")

        evaluated_vars.append(var)

    # -----------------------------------------------------
    # 结果裁决与抽取
    # -----------------------------------------------------
    # 按 final_score 降序排列，分数最高的是本轮 Best
    evaluated_vars.sort(key=lambda x: x["final_score"], reverse=True)
    
    best_variant = evaluated_vars[0]

    # 构造“差生池 (Bad Pool)”，找出所有显著恶化的变体
    bad_pool = [v for v in evaluated_vars if v["final_score"] < BAD_SCORE_THRESHOLD]
    
    # 随机抽取一个作为受控回溯的教材（Negative Sampling）
    random_bad_variant = random.choice(bad_pool) if bad_pool else None

    # 收敛判断：如果最好的变体得分为 0 或负数，说明即使大模型介入也没找到更好的出路
    is_converged = best_variant["final_score"] <= 0.0

    return best_variant, random_bad_variant, is_converged