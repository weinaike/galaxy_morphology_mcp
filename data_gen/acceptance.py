# data_gen/acceptance.py
import math
import random
import json
import os

def judge_acceptance(delta_r: float, temperature: float = 0.5) -> bool:
    """
    基于 Metropolis-Hastings 准则的退火接受逻辑。
    
    :param delta_r: 复合 Reward 的差值 (R_new - R_old)
    :param temperature: 温度 T。T 越高，越容易接受“变差”的动作(探索)；T 越低，越贪婪(利用)。
    :return: True (接受该动作), False (拒绝该动作)
    """
    # 1. 如果结果变好了 (增益 >= 0)，无条件贪婪接受
    if delta_r >= 0:
        return True,"Reward变好了"
        
    # 2. 如果结果变差了，计算接受概率 alpha
    # 防止 Python 的 math.exp 在极端情况下发生数值溢出
    try:
        alpha = math.exp(delta_r / temperature)
    except OverflowError:
        alpha = 0.0
        
    acceptance_prob = min(1.0, alpha)
    
    # 3. 掷骰子 (0~1 之间的随机数)。如果随机数 <= 概率，则接受！
    return random.random() <= acceptance_prob,f"Reward变差了，接受概率为 {acceptance_prob}"


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