# data_gen/pipeline.py
import asyncio
import os
import re
import json

from simulator_env.galfit_env import GalfitEnv
from data_gen.proposal import generate_proposals
from data_gen.reward import calculate_reward
from data_gen.acceptance import judge_acceptance, save_trajectory

class DataGenPipeline:
    def __init__(self, base_project_dir: str, output_root: str, max_iter: int = 100, proposal_strategy: str = "rule_based"):
        """
        初始化数据生成流 (无状态设计)。
        """
        self.env = GalfitEnv(base_project_dir=base_project_dir, max_iter=max_iter)
        self.output_root = output_root
        self.proposal_strategy = proposal_strategy # 记录策略
        
    def _count_sersic_components(self, feedme_path: str) -> int:
        count = 0
        if os.path.exists(feedme_path):
            with open(feedme_path, "r") as f:
                for line in f:
                    if re.match(r'^0\)\s+sersic', line.strip()):
                        count += 1
        return max(1, count)

    async def run_galaxy(self, galaxy_id: str, init_feedme: str, max_steps: int = 10, 
                         num_variants: int = 4, use_llm_reward: bool = False, use_expert_prior: bool = False):
        """
        处理单个星系，并返回该星系的局部统计报告。
        """
        strategy_folder = f"{self.proposal_strategy}_proposal"
        gal_out_dir = os.path.join(self.output_root, strategy_folder, galaxy_id)
        
        os.makedirs(gal_out_dir, exist_ok=True)
        
        # 局部数据结构初始化 (用于落盘 JSON 和生成本轮报告)
        tree = {
            "galaxy_id": galaxy_id,
            "metadata": { # 增加元数据标记
                "proposal_strategy": self.proposal_strategy,
                "expert_file_used": use_expert_prior
            },
            "init_feedme": init_feedme,
            "target_feedme": None,
            "target_residual": None,
            "is_good_fit": False, 
            "nodes": [],
            "llm_stats": {
                "vlm_total_calls": 0,
                "vlm_better_count": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "model_used": "None"
            },
            "analytics": {
                "total_actions_generated": 0,
                "ssim_filtered_count": 0,
                "physics_crashed_count": 0,
                "improved_count": 0,    
                "not_improved_count": 0, 
                "action_type_distribution": {"A": 0, "B": 0, "C": 0},
                "improved_action_distribution": {"A": 0, "B": 0, "C": 0}
            }
        }
        
        print(f"\n🚀 [Pipeline] 开始处理星系: {galaxy_id}")
        
        init_state = await self.env.initialize(init_feedme, gal_out_dir, galaxy_id)
        if init_state["status"] != "success":
            print(f"❌ 初始化崩溃，跳过 {galaxy_id}。原因: {init_state.get('error')}")
            # 失败时依然返回空的统计报告，方便主程序汇总
            return {"analytics": tree["analytics"], "llm_stats": tree["llm_stats"], "success": False}

        # 尝试加载专家先验 JSON
        expert_gt_data = None
        if use_expert_prior:
            # 有可能 init_feedme 是基于文件的绝对路径，我们要拿到它所在的目录
            source_dir = os.path.abspath(os.path.dirname(init_feedme))
            json_path = os.path.join(source_dir, "Gadotti_params.json")
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        expert_gt_data = json.load(jf)
                        
                    # ==========================================
                    # 诊断日志：验证 JSON 读取与战略解析
                    # ==========================================
                    mtype1 = expert_gt_data.get("MType", "unknown")
                    
                    mtype2 = expert_gt_data.get("MType2", "unknown")
                    # 判断需要几个 Sersic 成分：
                    target_sersic_count = 1
                    if expert_gt_data.get("re_disk", 0.0) > 0: target_sersic_count += 1
                    if expert_gt_data.get("re_bar", 0.0) > 0: target_sersic_count += 1
                    
                    print(f"    🧠 [专家先验已挂载] 检测到形态: {mtype1} ({mtype2})")
                    print(f"       - 目标成分数量: {target_sersic_count} (Disk: {expert_gt_data.get('re_disk',0)>0}, Bar: {expert_gt_data.get('re_bar',0)>0})")
                    print(f"       - 目标核球 n 值: {expert_gt_data.get('sersic_bulge', 'N/A')}")
                    
                except Exception as e:
                    print(f"    ⚠️ [先验加载警告] 无法解析 {json_path}: {e}")
            else:
                print(f"    ⚠️ [先验加载警告] 未找到专家文件: {json_path}")
            
        root_node = {
            "node_id": "node_0_root",
            "parent_id": None,
            "depth": 0,
            "action_from_parent": None,
            "feedme_path": init_state["feedme_path"],
            "residual_path": init_state["residual_path"],
            "summary_path": init_state["summary_path"],
            "metrics": init_state["metrics"],
            "is_accepted": True
        }
        tree["nodes"].append(root_node)
        
        current_layer_nodes = [root_node]

        patience_counter = 0
        max_patience = 2 # 允许原地踏步的最大次数

        for step in range(1, max_steps + 1):
            best_in_layer = min(current_layer_nodes, key=lambda x: x["metrics"].get("chi2_nu", 999.0))
            print(f"  ▶️ Step {step}/{max_steps} | 当前活跃分支: {len(current_layer_nodes)} | 本层最优 Chi2_nu: {best_in_layer['metrics'].get('chi2_nu')}")
            
            variants_per_parent = max(1, num_variants // len(current_layer_nodes))
            
            step_tasks = []
            task_info = [] 
            
            for p_idx, parent_node in enumerate(current_layer_nodes):
                num_sersic = self._count_sersic_components(parent_node["feedme_path"])
                state_context = {
                    "num_sersic": num_sersic,
                    "expert_gt": expert_gt_data  # 把读出来的 JSON 直接塞进上下文里 (如果没有就是 None)
                }
                actions = generate_proposals(state_context, step, num_variants=variants_per_parent)
                
                for v_idx, action in enumerate(actions):
                    if action["type"] == "D": continue
                    
                    action_type = action["type"]
                    tree["analytics"]["total_actions_generated"] += 1
                    tree["analytics"]["action_type_distribution"][action_type] += 1
                    
                    node_id = f"node_{step}_p{p_idx}_v{v_idx}"
                    task = self.env.step(
                        action=action, 
                        current_feedme_path=parent_node["feedme_path"], 
                        current_png_path=parent_node["residual_path"], 
                        output_dir=gal_out_dir, 
                        node_id=node_id,
                        summary_path=parent_node["summary_path"] 
                    )
                    step_tasks.append(task)
                    task_info.append((node_id, action, parent_node))
                    
            if not step_tasks:
                break

            print(f"    ⏳ 并发执行 {len(step_tasks)} 个变体...")
            results = await asyncio.gather(*step_tasks)
            
            next_layer_nodes = []

            for info, res in zip(task_info, results):
                node_id, action, parent_node = info
                action_type = action["type"]
                
                if res.get("status") != "success":
                    status_code = res.get("status", "")
                    error_msg = str(res.get("error", "")).lower()
                    
                    # 修正状态误判：优先通过清晰的状态码（status）判定是否被 SSIM 拦截
                    if status_code == "rejected_by_ssim" or "ssim" in error_msg:
                        tree["analytics"]["ssim_filtered_count"] += 1
                        print(f"    🗑️ 拦截变体 {node_id} [动作: {action_type}] (SSIM 相似度过高)，清理文件...")
                    else:
                        # 真正的物理引擎段错误或 FITS 损坏才会走到这里
                        tree["analytics"]["physics_crashed_count"] += 1
                        real_error = res.get("error") or res.get("msg") or "未知执行异常"
                        print(f"    ❌ GALFIT运行失败变体 {node_id} [动作: {action_type}] ({real_error})，清理文件...")
                    
                    # 垃圾回收机制：清理硬盘残余变体文件
                    for filename in os.listdir(gal_out_dir):
                        if node_id in filename:
                            file_path = os.path.join(gal_out_dir, filename)
                            try:
                                os.remove(file_path)
                            except Exception:
                                pass
                    continue


                # 修复点：尝试获取新图路径，兼容多个可能键名
                next_img = res.get("residual_path")
                prev_img = parent_node.get("residual_path")
                
                print(f"      - prev_img: {prev_img} (存在: {os.path.exists(str(prev_img))})")
                print(f"      - next_img: {next_img} (存在: {os.path.exists(str(next_img))})")

                step_delta_r, r_detail = calculate_reward(
                    old_metrics=parent_node["metrics"],
                    new_metrics=res.get("metrics", {}),
                    action=action, 
                    step=step,
                    prev_image_path=prev_img,
                    next_image_path=next_img,
                    use_llm=use_llm_reward
                )

                improved = False
                if use_llm_reward:
                    # 获取 VLM 返回的字典，即使物理引擎报错没调 LLM，这里也能安全拿到 {}
                    vlm_detail = r_detail.get("vlm_detail", {})
                    
                    # 🚀 只要有 vlm_detail 返回（说明大模型确实被触发了），就立刻记账！
                    if vlm_detail:
                        tree["llm_stats"]["vlm_total_calls"] += 1
                        
                        # 无论大模型回答是对是错，Token 账单必须记下
                        usage = vlm_detail.get("usage", {})
                        tree["llm_stats"]["total_prompt_tokens"] += usage.get("prompt_tokens", 0)
                        tree["llm_stats"]["total_completion_tokens"] += usage.get("completion_tokens", 0)
                        tree["llm_stats"]["model_used"] = vlm_detail.get("model_used", "gpt-4o")
                        
                        # 兼容多重键名
                        improvement_score = vlm_detail.get("final_improvement")
                        if improvement_score is None:
                            improvement_score = vlm_detail.get("improvement")
                            
                        # 如果确实解析到了分数，且判定为 1 (变好)
                        if improvement_score is not None and int(improvement_score) == 1:
                            tree["llm_stats"]["vlm_better_count"] += 1
                            improved = True
                else:
                    if step_delta_r > 0: improved = True
                
                if improved:
                    tree["analytics"]["improved_count"] += 1
                    tree["analytics"]["improved_action_distribution"][action_type] += 1
                else:
                    tree["analytics"]["not_improved_count"] += 1

                is_accepted, acceptance_reason = judge_acceptance(delta_r=step_delta_r, temperature=0.5)
                
                node_record = {
                    "node_id": node_id,
                    "parent_id": parent_node["node_id"],
                    "depth": step,
                    "action_from_parent": action,
                    "feedme_path": res.get("feedme_path"),
                    # "residual_path": res.get("image_file"),
                    "residual_path": next_img,
                    "metrics": res.get("metrics", {}),
                    "delta_R": step_delta_r,
                    "reward_detail": r_detail,
                    "is_accepted": is_accepted,
                    "status": res.get("status"),
                    "summary_path": res.get("summary_file") or res.get("summary_path") 
                }
                tree["nodes"].append(node_record)
                
                if is_accepted:
                    next_layer_nodes.append(node_record)
                    
                flag = "✅ [Accepted]" if is_accepted else "🚫 [Rejected]"
                print(f"    {flag} {node_id} | 父: {parent_node['node_id']} | 动作: {action['type']} | 增益: {step_delta_r:.3f} | {acceptance_reason}")

            if next_layer_nodes:
                current_layer_nodes = next_layer_nodes
                patience_counter = 0 # 只要有进步，耐心值清零
            else:
                patience_counter += 1
                print(f"    ⚠️ 步进 {step} 所有变体均未达标。当前耐心值: {patience_counter}/{max_patience}")
                
                if patience_counter >= max_patience:
                    print(f"    🛑 连续 {max_patience} 步未达标，触发早停机制 (模拟 Action D 收敛)！提前结束本星系搜索。")
                    break # 🚀 直接跳出 for 循环，进入落盘结算环节！

        # ==================== 落盘结束 ====================
        best_final_node = min(current_layer_nodes, key=lambda x: x["metrics"].get("chi2_nu", 999.0))
        tree["target_feedme"] = best_final_node["feedme_path"]
        tree["target_residual"] = best_final_node["residual_path"]
        
        save_trajectory(tree, gal_out_dir)
        print(f"🎉 {galaxy_id} 处理完毕！最终深度: {best_final_node['depth']}，全场最优 Chi2_nu: {best_final_node['metrics'].get('chi2_nu')}")
        
        # 🚀 极其关键：将本星系的战报返回给主程序
        return {
            "analytics": tree["analytics"],
            "llm_stats": tree["llm_stats"],
            "success": True
        }