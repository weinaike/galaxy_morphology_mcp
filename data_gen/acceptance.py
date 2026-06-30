# data_gen/acceptance.py
import math
import random
import json
import os

import math
import random
from typing import Tuple

def judge_acceptance(delta_r: float, temperature: float = 0.5, force_greedy: bool = True, accept_all: bool = False) -> Tuple[bool, str]:
    """
    基于 Metropolis-Hastings 准则的退火接受逻辑。

    :param delta_r: 复合 Reward 的差值 (R_new - R_old)
    :param temperature: 温度 T。T 越高越容易探索，T 越低越贪婪。
    :param force_greedy: 强制贪婪模式开关。如果为 True，则只要变差直接拒绝。
    :param accept_all: 全部接受模式。无论reward结果如何一律接受，模拟MCP交互式流程。
    :return: (是否接受, 理由说明)
    """
    # 0. 全部接受模式：模拟交互式agent流程，不做任何质量门控
    if accept_all:
        tag = "改善" if delta_r >= 0 else "变差"
        return True, f"全部接受模式 (delta_r={delta_r:+.2f}, {tag})"

    # 1. 永远贪婪接受变好的结果
    if delta_r >= 0:
        return True, "Reward 变好 (增益 >= 0)"
        
    # 2. 如果结果变差，但开启了强制贪婪模式，直接枪毙 (这就是你目前想要的效果)
    if force_greedy:
        return False, "Reward 变差 (贪婪模式直接拒绝)"
        
    # 3. 结果变差，走概率退火接受 (保留探索火种)
    try:
        alpha = math.exp(delta_r / temperature)
    except OverflowError:
        alpha = 0.0
        
    acceptance_prob = min(1.0, alpha)
    roll = random.random()
    
    if roll <= acceptance_prob:
        return True, f"Reward 变差，但触发退火接受 (概率: {acceptance_prob:.2%}, 掷骰: {roll:.2f})"
    else:
        return False, f"Reward 变差，退火拒绝 (概率: {acceptance_prob:.2%}, 掷骰: {roll:.2f})"


def save_trajectory(tree_data: dict, output_dir: str):
    """
    将星系的整棵 MCMC 探索树（包含所有 winner 和 loser 节点）落盘。
    输出的 JSON 结构严格对齐你之前规划的 DPO 数据集格式。
    
    :param tree_data: 包含 galaxy_id, nodes 等信息的完整树字典
    :param output_dir: 存放轨迹 JSON 的目录
    """
    os.makedirs(output_dir, exist_ok=True)
    gal_id = tree_data.get("galaxy_id", "unknown_galaxy")
    
    # 统一命名规范
    filepath = os.path.join(output_dir, f"{gal_id}_trajectory.json")
    
    # 落盘，确保中文/特殊字符不乱码，且有良好的缩进可读性
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(tree_data, f, indent=4, ensure_ascii=False)
        print(f"💾 [数据落盘] 轨迹已成功保存至: {filepath}")
    except Exception as e:
        print(f"❌ [数据落盘] 保存轨迹 JSON 时发生错误: {e}")