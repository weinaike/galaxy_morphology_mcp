# data_gen/proposal.py
import random
import numpy as np

def get_action_probabilities(current_step: int) -> dict:
    """
    动态动作概率分配
    逻辑：前 5 步优先考虑增加成分 (A)，5 步之后优先考虑修改参数 (C)。
    """
    if current_step <= 5:
        # 前 5 步：70% 概率加组件，10% 概率删组件，20% 概率微调
        # return {"A": 0.70, "B": 0.10, "C": 0.20}
        return {"A": 1.0, "B": 0.0, "C": 0.0}
    else:
        # 5 步之后：90% 概率微调参数，保留 10% 概率做结构小调
        # return {"A": 0.05, "B": 0.05, "C": 0.90}
        return {"A": 1.0, "B": 0.0, "C": 0.0}

def sample_action(current_step: int, max_steps: int, available_components: list, expert_gt: dict = None) -> dict:
    """
    根据当前状态采样具体动作。
    兼容设计：可选择性接收 expert_gt 参数
    """
    probs = get_action_probabilities(current_step)
    action_type = random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]
    
    num_comps = len(available_components)

    if num_comps == 0:
        action_type = "A"

    if action_type == "A":
        comp = random.choice(["bar", "disk", "bulge", "psf"])
        # 如果有先验，在这里动态对 Action A 扩展补丁
        action_a = {"type": "A", "component_type": comp}
        if expert_gt:
            action_a = _inject_expert_patch_to_action(action_a, expert_gt)
        return action_a
    
    elif action_type == "B":
        if num_comps == 1:
            return generate_action_c_perturbation(num_comps)
        return {"type": "B", "target_index": random.randint(1, num_comps)}
    
    elif action_type == "C":
        return generate_action_c_perturbation(num_comps)

def generate_action_c_perturbation(num_sersic: int) -> dict:
    """
    生成对齐《扰动策略三（极简版）》的 Action C 参数。
    核心：仅对 Re 和 n 进行强迫解耦扰动。
    """
    if num_sersic == 0:
        return {"type": "D"}

    target_sersic = random.randint(0, num_sersic - 1)
    
    if random.random() < 0.20:
        return {"type": "C", "target_sersic_idx": target_sersic, "delta_re_factor": 1.0, "delta_n_val": 0.0}

    difficulty = random.choices(["easy", "medium", "hard"], weights=[0.26, 0.27, 0.27], k=1)[0]
    bias_mode = random.random() < 0.5
    
    if difficulty == "easy":
        sigma_re = 0.18
        sigma_n = 0.5
    elif difficulty == "medium":
        sigma_re = 0.40
        sigma_n = 1.0
    else:
        sigma_re = 0.47
        sigma_n = 2.0
        
    mu_re = -0.3 if bias_mode else 0.0
    re_factor = np.exp(np.random.normal(mu_re, sigma_re))
    re_factor = min(max(re_factor, 0.1), 2.5) 
    delta_n = np.random.normal(0, sigma_n)

    return {
        "type": "C",
        "target_sersic_idx": target_sersic,
        "delta_re_factor": float(re_factor),
        "delta_n_val": float(delta_n)
    }

def _inject_expert_patch_to_action(action: dict, expert_gt: dict) -> dict:
    """
    [新增辅助函数] 不侵入主架构，仅为 Action A 字典动态追加专家数值补丁
    """
    comp = action.get("component_type")
    
    # 创建一个用来装具体参数补丁的“空包裹”
    patch = {} 
    
    if comp == "disk":
        if expert_gt.get("sersic_disk") is not None:
            patch["preset_n"] = float(np.clip(np.random.normal(expert_gt["sersic_disk"], 0.1), 0.5, 1.5))
        if expert_gt.get("re_disk", 0) > 0:
            patch["preset_re"] = float(expert_gt["re_disk"] * random.uniform(0.9, 1.1))
            
    elif comp == "bulge":
        if expert_gt.get("sersic_bulge") is not None:
            patch["preset_n"] = float(np.clip(np.random.normal(expert_gt["sersic_bulge"], 0.3), 1.5, 6.0))
        if expert_gt.get("re_bulge", 0) > 0:
            patch["preset_re"] = float(expert_gt["re_bulge"] * random.uniform(0.9, 1.1))
            
    elif comp == "bar":
        if expert_gt.get("sersic_bar") is not None:
            patch["preset_n"] = float(np.clip(np.random.normal(float(expert_gt["sersic_bar"]), 0.1), 0.1, 1.0))
        if expert_gt.get("re_bar", 0) > 0:
            patch["preset_re"] = float(expert_gt["re_bar"] * random.uniform(0.9, 1.1))
        # 星系棒最核心的物理特征是极其扁平！
        if expert_gt.get("axis_ratio_bar") is not None:
            patch["preset_q"] = float(expert_gt["axis_ratio_bar"])
            
    if comp in ["disk", "bulge", "bar"]:
        patch["mag_offset"] = float(random.uniform(0.5, 1.5)) # 亮度微弱调暗保护

    # 如果包裹里有东西，就把包裹贴到动作字典上，并标明这是专家给的
    if patch:
        action["patch_applied"] = patch
        action["source"] = "expert_json"
        
    return action

def generate_proposals(state_context: dict, current_step: int, num_variants: int = 16, max_steps: int = 10, use_llm_proposal: bool = False) -> list:
    """
    智能槽位分配机制 (Smart Slot Allocation) - 完美融合专家先验补丁版
    """
    num_sersic = state_context.get("num_sersic", 0)
    expert_gt = state_context.get("expert_gt") # 🚀 从 pipeline 的 state_context 获取 JSON
    actions = []

    # ==========================================
    # 阶段 1：前 5 步 (结构探索期：建骨架)
    # ==========================================
    if current_step <= 5:
        # 1. 确定性分配 A：保证穷尽 4 种类型
        component_pool = ["bar", "disk", "bulge", "psf"]
        for comp in component_pool:
            if len(actions) < num_variants:
                action_a = {"type": "A", "component_type": comp}
                if expert_gt: # 🚀 增量覆盖机制：如果有真值，打上补丁
                    action_a = _inject_expert_patch_to_action(action_a, expert_gt)
                actions.append(action_a)
        
        # 2. 确定性分配 B
        if num_sersic > 0 and len(actions) < num_variants:
            actions.append({"type": "B", "target_index": random.randint(1, num_sersic)})
            
        # 3. 随机分配 C
        while len(actions) < num_variants:
            if num_sersic > 0:
                actions.append(generate_action_c_perturbation(num_sersic))
            else:
                action_a = {"type": "A", "component_type": random.choice(component_pool)}
                if expert_gt:
                    action_a = _inject_expert_patch_to_action(action_a, expert_gt)
                actions.append(action_a)

    # ==========================================
    # 阶段 2：5 步之后 (参数收敛期：抠细节)
    # ==========================================
    else:
        # 1. 极小概率分配结构变动
        if len(actions) < num_variants and random.random() < 0.3:
            comp = random.choice(["bar", "disk", "bulge", "psf"])
            action_a = {"type": "A", "component_type": comp}
            if expert_gt:
                action_a = _inject_expert_patch_to_action(action_a, expert_gt)
            actions.append(action_a)
        if num_sersic > 0 and len(actions) < num_variants and random.random() < 0.2:
             actions.append({"type": "B", "target_index": random.randint(1, num_sersic)})
             
        # 2. 绝对主导分配 C
        while len(actions) < num_variants:
            if num_sersic > 0:
                actions.append(generate_action_c_perturbation(num_sersic))
            else:
                comp = random.choice(["bar", "disk", "bulge", "psf"])
                action_a = {"type": "A", "component_type": comp}
                if expert_gt:
                    action_a = _inject_expert_patch_to_action(action_a, expert_gt)
                actions.append(action_a)

    random.shuffle(actions)
    return actions