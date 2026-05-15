import asyncio
import os
import sys
import shutil
import json
import random
import argparse
import glob
import re
from tqdm.asyncio import tqdm

# ================= 路径与环境变量初始化 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

# 导入各类自研工具与模块
from scripts.utils.summary_parser import parse_galfit_summary
from scripts.utils.evaluator import evaluate_variants, calculate_bic
from scripts.utils.actions import perform_action_add, perform_action_delete, perform_action_perturb

# 并发控制：保护 CPU 和 I/O 不被撑爆
MAX_GALAXY_CONCURRENT = 4 
galaxy_semaphore = asyncio.Semaphore(MAX_GALAXY_CONCURRENT)

# ================= 辅助函数库 =================

def extract_chi2_from_summary(summary_path):
    """提取基础 chi2，用于 S0 初始化"""
    if not os.path.exists(summary_path): 
        return 9999.0
    with open(summary_path, 'r') as f:
        match = re.search(r'Chi\^2/nu\s*\|\s*([\d\.]+)', f.read())
        return float(match.group(1)) if match else 9999.0

def build_sft_actions(var_info):
    """基于统一接口规范，将下游返回的变体信息翻译为 Agent 的标准动作指令"""
    var_name = var_info["variant_name"]
    action_dict = {}
    reverse_action_dict = {}

    if "_action_a_" in var_name:
        action_dict = {"action": "add", "type": var_info["component_type"], "id": var_info["sersic_id"]}
        reverse_action_dict = {"action": "delete", "id": var_info["sersic_id"]}
    elif "_action_b_" in var_name:
        action_dict = {"action": "delete", "id": var_info["sersic_id"]}
        reverse_action_dict = {"action": "add", "id": var_info["sersic_id"], "note": "restore_deleted"}
    elif "_action_c_" in var_name:
        action_dict = {"action": "modify", "id": var_info["sersic_id"], "delta_params": var_info.get("delta_params", {})}
        reverse_action_dict = {"action": "reverse_modify", "id": var_info["sersic_id"]}
    
    return action_dict, reverse_action_dict

def verify_variant_files(var_dir, var_name):
    """防御性检查：确保下游变体真正生成了必需的文件"""
    feedme_p = os.path.join(var_dir, f"{var_name}.feedme")
    summary_p = os.path.join(var_dir, f"{var_name}_summary.md")
    png_p = os.path.join(var_dir, f"{var_name}_comparison.png")
    
    if os.path.exists(feedme_p) and os.path.exists(summary_p) and os.path.exists(png_p):
        return True, feedme_p, summary_p, png_p
    return False, None, None, None

# ================= 核心 MDP 调度器 =================

async def process_galaxy_mdp(galaxy_path, field, traj_out_dir, disable_action_c):
    """
    单个星系的策略推演主循环
    """
    galaxy_name = os.path.splitext(os.path.basename(galaxy_path))[0]
    
    async with galaxy_semaphore:
        galaxy_workspace = os.path.join(BASE_DIR, "data", "galfit_results", field, galaxy_name)
        os.makedirs(galaxy_workspace, exist_ok=True)
        
        s0_summary = os.path.join(os.path.dirname(galaxy_path), f"{galaxy_name}_summary.md")
        curr_state = {
            "chi2": extract_chi2_from_summary(s0_summary),
            "bic": calculate_bic(s0_summary),
            "feedme": galaxy_path,
            "png_path": os.path.join(os.path.dirname(galaxy_path), f"{galaxy_name}_comparison.png"),
            "summary_path": s0_summary
        }
        
        history = []
        sft_pairs = []

        for step_i in range(5): # MAX_ROUNDS = 5
            all_variants = []

            def collect_variants(action_results_list):
                for action_list in action_results_list:
                    if not action_list: continue
                    for var in action_list:
                        var_name = var["variant_name"]
                        var_dir = os.path.join(galaxy_workspace, var_name)
                        
                        is_valid, f_path, s_path, p_path = verify_variant_files(var_dir, var_name)
                        if not is_valid: continue
                        
                        var["feedme_path"] = f_path
                        var["summary_path"] = s_path
                        var["png_path"] = p_path
                        var["chi2"] = extract_chi2_from_summary(s_path)
                        var["action_dict"], var["reverse_action_dict"] = build_sft_actions(var)
                        all_variants.append(var)

            # =========================================================
            # [新增] 调度大脑：解析当前拓扑，执行“硬约束”拦截
            # =========================================================
            with open(curr_state["feedme"], 'r') as f:
                feedme_content = f.read()
            sersic_count = len(re.findall(r'^\s*0\)\s*sersic', feedme_content, re.MULTILINE))
            psf_count = len(re.findall(r'^\s*0\)\s*psf', feedme_content, re.MULTILINE))

            tasks_ab = []

            # ---------------------------------------------------------
            # 阶段 1: 结构级探索 (Action A & B)
            # ---------------------------------------------------------
            
            # 策略 A：增量控制（Sersic 最大 4 个，PSF 最大 1 个）
            if sersic_count < 4 or psf_count < 1:
                tasks_ab.append(perform_action_add(curr_state["feedme"], galaxy_name, step_i, galaxy_workspace))
            else:
                # 触发拦截，不下发任务
                pass 

            # 策略 B：减量保护（至少保留 1 个主星系结构）
            if sersic_count > 1 or psf_count > 0:
                tasks_ab.append(perform_action_delete(curr_state["feedme"], galaxy_name, step_i, galaxy_workspace))
            else:
                # 仅剩唯一主成分，坚决不能触发删除
                pass 

            if tasks_ab:
                results_ab = await asyncio.gather(*tasks_ab)
                collect_variants(results_ab)

            # ---------------------------------------------------------
            # 阶段 2: 参数级探索 (Action C) [受控开关]
            # ---------------------------------------------------------
            if not disable_action_c:
                result_c = await perform_action_perturb(curr_state["feedme"], galaxy_name, step_i, galaxy_workspace)
                collect_variants([result_c])
                        
            if not all_variants: 
                break 
            
            # ---------------------------------------------------------
            # 3. 呼叫裁判：基于 SSIM / BIC / VLM 综合评价
            # ---------------------------------------------------------
            a_good_variant, random_bad_variant, is_converged = evaluate_variants(curr_state, all_variants)
            
            # ---------------------------------------------------------
            # 4. 数据打标与历史线推进
            # ---------------------------------------------------------
            if is_converged:
                sft_pairs.append({
                    "input_image": curr_state["png_path"],
                    "input_text": f"History: {json.dumps(history)}\n\n{parse_galfit_summary(curr_state['summary_path'])}",
                    "output_action": {"action": "stop", "reason": "converged"}
                })
                variants_to_keep = []
                break
            
            sft_pairs.append({
                "input_image": curr_state["png_path"],
                "input_text": f"History: {json.dumps(history)}\n\n{parse_galfit_summary(curr_state['summary_path'])}",
                "output_action": a_good_variant["action_dict"]
            })
            variants_to_keep = [a_good_variant]

            if random_bad_variant is not None:
                is_bad_enough = (random_bad_variant["final_score"] < -10.0)
                if is_bad_enough:
                    bad_history = history.copy() + [random_bad_variant["action_dict"]]
                    sft_pairs.append({
                        "input_image": random_bad_variant["png_path"],
                        "input_text": f"History: {json.dumps(bad_history)}\n\n{parse_galfit_summary(random_bad_variant['summary_path'])}",
                        "output_action": random_bad_variant["reverse_action_dict"]
                    })
                    variants_to_keep.append(random_bad_variant)

                    if random.random() < 0.10:
                        history.extend([
                            random_bad_variant["action_dict"], 
                            random_bad_variant["reverse_action_dict"], 
                            a_good_variant["action_dict"]
                        ])
                    else:
                        history.append(a_good_variant["action_dict"])
                else:
                    history.append(a_good_variant["action_dict"])
            else:
                history.append(a_good_variant["action_dict"])

            curr_state = {
                "chi2": a_good_variant["chi2"],
                "bic": a_good_variant.get("bic", 999.0),
                "feedme": a_good_variant["feedme_path"],
                "png_path": a_good_variant["png_path"],
                "summary_path": a_good_variant["summary_path"]
            }

            # ---------------------------------------------------------
            # 5. 极速垃圾回收
            # ---------------------------------------------------------
            keep_paths = [os.path.dirname(v["feedme_path"]) for v in variants_to_keep]
            for var in all_variants:
                var_dir = os.path.dirname(var["feedme_path"])
                if var_dir not in keep_paths and os.path.exists(var_dir):
                    shutil.rmtree(var_dir, ignore_errors=True)

        out_json = os.path.join(traj_out_dir, f"{galaxy_name}_sft.json")
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump({"galaxy_id": galaxy_name, "sft_pairs": sft_pairs}, f, indent=2, ensure_ascii=False)

# ================= 主程序入口 =================

async def main():
    parser = argparse.ArgumentParser(description="GalDecomp MDP Trajectory Generator")
    parser.add_argument("--input_dir", type=str, required=True, help="天区原始数据目录")
    parser.add_argument("--field", type=str, required=True, help="天区名称 (如 COS)")
    parser.add_argument("--traj_out_dir", type=str, required=True, help="轨迹 JSON 输出目录")
    parser.add_argument("--disable_action_c", action="store_true", help="是否关闭 Action C (参数微调) 探索分支")
    args = parser.parse_args()

    galaxy_dirs = [os.path.join(args.input_dir, d) for d in os.listdir(args.input_dir) 
                   if os.path.isdir(os.path.join(args.input_dir, d))]
    
    tasks = []
    status_msg = "[*] 扫描到 {} 个星系候选目录... (Action C 状态: {})"
    print(status_msg.format(len(galaxy_dirs), "已禁用 ❌" if args.disable_action_c else "已开启 ✅"))
    
    for g_dir in galaxy_dirs:
        feedme_files = glob.glob(os.path.join(g_dir, "*.feedme"))
        if feedme_files:
            tasks.append(process_galaxy_mdp(feedme_files[0], args.field, args.traj_out_dir, args.disable_action_c))
    
    if not tasks:
        print("❌ 未在天区内找到任何 .feedme 初始文件，请检查目录结构！")
        return

    print(f"🚀 启动星系策略并行探索 (最大并发星系: {MAX_GALAXY_CONCURRENT})...")
    
    with tqdm(total=len(tasks), desc=f"MDP 演化 [{args.field}]") as pbar:
        for f in asyncio.as_completed(tasks):
            await f
            pbar.update(1)

if __name__ == "__main__":
    asyncio.run(main())