# data_gen/pipeline.py
import asyncio
import os
import re
import json

from simulator_env.galfit_env import GalfitEnv
from simulator_env.galfit_actions import parse_components_from_feedme
from data_gen.proposal import generate_proposals
from data_gen.reward import calculate_reward
from data_gen.acceptance import judge_acceptance, save_trajectory
import time

class DataGenPipeline:
    def __init__(self, base_project_dir: str, output_root: str, max_iter: int = 100, proposal_strategy: str = "rule_based"):
        """
        初始化数据生成流 (无状态设计)。
        """
        self.env = GalfitEnv(base_project_dir=base_project_dir, max_iter=max_iter)
        self.output_root = output_root
        self.proposal_strategy = proposal_strategy # 记录策略: "rule_based", "expert_guided", "vlm_generated"
        
    def _count_sersic_components(self, feedme_path: str) -> int:
        count = 0
        if os.path.exists(feedme_path):
            with open(feedme_path, "r") as f:
                for line in f:
                    if re.match(r'^0\)\s+sersic', line.strip()):
                        count += 1
        return max(1, count)

    async def run_galaxy(self, galaxy_id: str, init_feedme: str, max_steps: int = 10,
                         num_variants: int = 4, use_llm_reward: bool = False, use_expert_prior: bool = False,
                         vlm_reward_model_name: str = "gemini-3.1-pro-preview",
                         vlm_proposal_model_name: str = None,
                         use_param_check: bool = False,
                         vlm_proposal_num_calls: int = 4,
                         use_expert_hint_for_vlm: bool = False,
                         use_history_for_vlm: bool = False,
                         history_max_steps: int = 0,
                         beam_top_k: int = 0,
                         vlm_reward_image_mode: str = "cutoff",
                         force_greedy: bool = True,
                         accept_all: bool = False,
                         max_patience: int = 2,
                         vlm_proposal_multiturn: bool = False):
        """
        处理单个星系，并返回该星系的局部统计报告。
        """
        # 新增：记录开始时间
        start_time = time.time()
        gal_out_dir = os.path.join(self.output_root, galaxy_id)
        
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
                "vlm_proposal_calls": 0,
                "vlm_proposal_prompt_tokens": 0,
                "vlm_proposal_completion_tokens": 0
            },
            "analytics": {
                "total_actions_generated": 0,
                "ssim_filtered_count": 0,
                "physics_crashed_count": 0,
                "improved_count": 0,
                "not_improved_count": 0,
                "action_type_distribution": {"A": 0, "B": 0, "C": 0},
                "improved_action_distribution": {"A": 0, "B": 0, "C": 0},
                "structural_distribution": {"add_disk": 0, "add_bar": 0, "add_bulge": 0, "add_psf": 0, "none": 0},
                "improved_structural_distribution": {"add_disk": 0, "add_bar": 0, "add_bulge": 0, "add_psf": 0, "none": 0},
                "passed_ssim_action_distribution": {"A": 0, "B": 0, "C": 0},
                "passed_ssim_structural_distribution": {"add_disk": 0, "add_bar": 0, "add_bulge": 0, "add_psf": 0, "none": 0},
                "add_with_realloc_count": 0,
                "per_step_stats": {},
                "per_step_action_stats": {}
            }
        }
        
        print(f"\n🚀 [Pipeline] 开始处理星系: {galaxy_id}")

        # 尝试加载专家先验 JSON（在初始化前打印，使成分数紧跟星系标头）
        expert_gt_data = None
        if use_expert_prior or use_expert_hint_for_vlm:
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

        init_state = await self.env.initialize(init_feedme, gal_out_dir, galaxy_id)
        if init_state["status"] != "success":
            print(f"❌ 初始化崩溃，跳过 {galaxy_id}。原因: {init_state.get('error')}")
            # 失败时依然返回空的统计报告，方便主程序汇总
            return {"analytics": tree["analytics"], "llm_stats": tree["llm_stats"], "success": False}

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

        # 动作标签：兼容 方案B完整规格(coarse_label) / 统一决策(structural) / 旧A/B/C/D(type)
        def _atype(act: dict) -> str:
            """粗分类 A(加)/B(删)/C(调参)。"""
            cl = act.get("coarse_label")
            if cl is not None:
                if cl.startswith("add_"):
                    return "A"
                if cl == "delete":
                    return "B"
                return "C"  # modify
            if "structural" in act:
                s = act.get("structural", "none")
                return "A" if s.startswith("add_") else ("B" if s.startswith("delete_") else "C")
            return act.get("type", "C")

        def _struct_key(act: dict):
            """structural_distribution 的键；modify→none，delete→None(不计入)。"""
            cl = act.get("coarse_label")
            if cl is not None:
                if cl.startswith("add_"):
                    return cl
                if cl == "modify":
                    return "none"
                return None  # delete
            if "structural" in act:
                s = act.get("structural", "none")
                return s if s in tree["analytics"]["structural_distribution"] else None
            return None

        def _fine_label(act: dict) -> str:
            """per_step_action_stats 的细粒度标签。"""
            cl = act.get("coarse_label")
            if cl is not None:
                return cl  # 方案B: add_disk/add_bar/add_bulge/add_psf/modify/delete
            if "structural" in act:
                s = act.get("structural", "none")
                if s.startswith("add_"):
                    return "A_realloc" if act.get("param_updates") else "A_pure"
                if s.startswith("delete_"):
                    return "B"
                return "C"
            return act.get("type", "?")

        def _ensure_psa(step_k: str, label: str):
            psa = tree["analytics"]["per_step_action_stats"]
            if step_k not in psa:
                psa[step_k] = {}
            if label not in psa[step_k]:
                psa[step_k][label] = {"generated": 0, "passed_ssim": 0, "improved": 0, "ssim_filtered": 0, "crashed": 0}
            return psa[step_k][label]

        def _fmt_metric(v):
            return f"{v:.4f}" if isinstance(v, (int, float)) else "NA"

        def _build_history_summary(parent_node: dict) -> str:
            """沿父链路生成历史轮次摘要：每步采纳动作/指标 + 同层被拒尝试（喂回 prompt，避免重复踩坑）。"""
            by_id = {n["node_id"]: n for n in tree["nodes"]}
            children_by_parent = {}
            for n in tree["nodes"]:
                children_by_parent.setdefault(n.get("parent_id"), []).append(n)
            # 构造 root→parent 链
            chain, cur = [], parent_node
            seen = set()
            while cur is not None and cur.get("node_id") not in seen:
                seen.add(cur.get("node_id"))
                chain.append(cur)
                cur = by_id.get(cur.get("parent_id"))
            chain.reverse()

            # 只取最近 history_max_steps 步（0=全部），控制 prompt 长度
            if history_max_steps and history_max_steps > 0:
                chain = chain[-history_max_steps:]

            lines = []
            for n in chain:
                depth = n.get("depth", 0)
                m = n.get("metrics", {})
                metric_str = f"chi2_nu={_fmt_metric(m.get('chi2_nu'))}, BIC={_fmt_metric(m.get('bic'))}"
                act = n.get("action_from_parent")
                if not act:
                    lines.append(f"- 第{depth}步(根节点): {metric_str}")
                else:
                    label = act.get("coarse_label", "?")
                    note = (act.get("target") or act.get("reasoning") or "").strip().replace("\n", " ")[:50]
                    mh_tag = "(退火接受,质量未改善)" if n.get("mh_accepted") else ""
                    lines.append(f"- 第{depth}步 采纳[{label}]{mh_tag} → {metric_str}{('；' + note) if note else ''}")
                # 同层被拒尝试（n 的兄弟里未被接受的）
                sibs = children_by_parent.get(n.get("parent_id"), [])
                rej = [s for s in sibs if s.get("node_id") != n.get("node_id") and not s.get("is_accepted")]
                if rej:
                    rlabels = [(s.get("action_from_parent") or {}).get("coarse_label", "?") for s in rej]
                    lines.append(f"    (同层被拒: {rlabels})")
            return "\n".join(lines) if lines else ""

        for step in range(1, max_steps + 1):
            best_in_layer = min(current_layer_nodes, key=lambda x: x["metrics"].get("chi2_nu", 999.0))
            print(f"  ▶️ Step {step}/{max_steps} | 当前活跃分支: {len(current_layer_nodes)} | 本层最优 Chi2_nu: {best_in_layer['metrics'].get('chi2_nu')}")

            step_key = str(step)
            tree["analytics"]["per_step_stats"][step_key] = {"generated": 0, "accepted": 0, "ssim_filtered": 0, "rejected": 0, "physics_crashed": 0}

            variants_per_parent = max(1, num_variants // len(current_layer_nodes))

            step_tasks = []
            task_info = []
            
            for p_idx, parent_node in enumerate(current_layer_nodes):
                num_sersic = self._count_sersic_components(parent_node["feedme_path"])
                state_context = {
                    "num_sersic": num_sersic,
                    "parent_components": parse_components_from_feedme(parent_node["feedme_path"]),
                    "history_summary": _build_history_summary(parent_node) if use_history_for_vlm else None,
                    "expert_gt": expert_gt_data if (use_expert_prior or use_expert_hint_for_vlm) else None,
                    "residual_path": parent_node.get("residual_path"),
                    "summary_path": parent_node.get("summary_path"),
                }
                actions, vlm_usage = await generate_proposals(
                    state_context, step, num_variants=variants_per_parent,
                    proposal_strategy=self.proposal_strategy,
                    vlm_proposal_model_name=vlm_proposal_model_name,
                    vlm_proposal_num_calls=vlm_proposal_num_calls,
                    vlm_proposal_multiturn=vlm_proposal_multiturn,
                )

                if vlm_usage:
                    tree["llm_stats"]["vlm_proposal_calls"] += vlm_usage.get("total_calls", 1)
                    tree["llm_stats"]["vlm_proposal_prompt_tokens"] += vlm_usage.get("prompt_tokens", 0)
                    tree["llm_stats"]["vlm_proposal_completion_tokens"] += vlm_usage.get("completion_tokens", 0)
                
                for v_idx, action in enumerate(actions):
                    if action.get("type") == "D":
                        continue

                    action_type = _atype(action)

                    tree["analytics"]["total_actions_generated"] += 1
                    tree["analytics"]["action_type_distribution"][action_type] += 1
                    tree["analytics"]["per_step_stats"][step_key]["generated"] += 1
                    _ensure_psa(step_key, _fine_label(action))["generated"] += 1

                    # 按 structural/coarse 细分统计 + 量化"加成分+重分配"
                    sk = _struct_key(action)
                    if sk is not None:
                        tree["analytics"]["structural_distribution"][sk] += 1
                    # 方案B：加成分几乎必带已有成分参数重分配(完整规格)，统一计入
                    if action_type == "A" and (action.get("spec") or action.get("param_updates")):
                        tree["analytics"]["add_with_realloc_count"] += 1
                    
                    node_id = f"node_{step}_p{p_idx}_v{v_idx}"
                    task = self.env.step(
                        action=action,
                        current_feedme_path=parent_node["feedme_path"],
                        current_png_path=parent_node["residual_path"],
                        output_dir=gal_out_dir,
                        node_id=node_id,
                        summary_path=parent_node["summary_path"],
                        step_idx=step,
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
                action_type = _atype(action)
                fine = _fine_label(action)
                struct_key = _struct_key(action)

                if res.get("status") != "success":
                    status_code = res.get("status", "")
                    error_msg = str(res.get("error", "")).lower()

                    # 修正状态误判：优先通过清晰的状态码（status）判定是否被 SSIM 拦截
                    if status_code == "rejected_by_ssim" or "ssim" in error_msg:
                        tree["analytics"]["ssim_filtered_count"] += 1
                        tree["analytics"]["per_step_stats"][step_key]["ssim_filtered"] += 1
                        _ensure_psa(step_key, fine)["ssim_filtered"] += 1
                        print(f"    🗑️ 拦截变体 {node_id} [动作: {action_type}] (SSIM 相似度过高)，清理文件...")
                    else:
                        tree["analytics"]["physics_crashed_count"] += 1
                        tree["analytics"]["per_step_stats"][step_key]["physics_crashed"] += 1
                        _ensure_psa(step_key, fine)["crashed"] += 1
                        real_error = res.get("error") or res.get("msg") or "未知执行异常"
                        print(f"    ❌ GALFIT运行失败变体 {node_id} [动作: {action_type}] ({real_error})，清理文件...")
                    
                    # 垃圾回收机制：清理硬盘残余变体文件
                    for filename in os.listdir(gal_out_dir):
                        # if node_id in filename:
                        if filename.startswith(f"{node_id}.") or filename.startswith(f"{node_id}_"):
                            file_path = os.path.join(gal_out_dir, filename)
                            try:
                                os.remove(file_path)
                            except Exception:
                                pass
                    continue


                # 修复点：尝试获取新图路径，兼容多个可能键名
                next_img = res.get("residual_path")
                prev_img = parent_node.get("residual_path")

                # VLM reward 看图模式: full=完整 comparison.png; cutoff=仅残差面板裁剪
                # galfit_env 默认存 cutoff,需要 full 时反推去掉 _cutoff 后缀
                if vlm_reward_image_mode == "full":
                    def _to_full(p):
                        if not p:
                            return p
                        stem, ext = os.path.splitext(p)
                        if stem.endswith("_cutoff"):
                            full_p = stem[:-len("_cutoff")] + ext
                            if os.path.exists(full_p):
                                return full_p
                        return p
                    prev_img_for_reward = _to_full(prev_img)
                    next_img_for_reward = _to_full(next_img)
                else:
                    prev_img_for_reward = prev_img
                    next_img_for_reward = next_img

                # 走到这里说明通过了 SSIM 滤渣，进入 reward 评估
                tree["analytics"]["passed_ssim_action_distribution"][action_type] += 1
                if struct_key and struct_key in tree["analytics"]["passed_ssim_structural_distribution"]:
                    tree["analytics"]["passed_ssim_structural_distribution"][struct_key] += 1
                _ensure_psa(step_key, fine)["passed_ssim"] += 1

                print(f"      - prev_img({vlm_reward_image_mode}): {prev_img_for_reward} (存在: {os.path.exists(str(prev_img_for_reward))})")
                print(f"      - next_img({vlm_reward_image_mode}): {next_img_for_reward} (存在: {os.path.exists(str(next_img_for_reward))})")

                step_delta_r, r_detail = calculate_reward(
                    old_metrics=parent_node["metrics"],
                    new_metrics=res.get("metrics", {}),
                    action=action,
                    step=step,
                    prev_image_path=prev_img_for_reward,
                    next_image_path=next_img_for_reward,
                    use_llm=use_llm_reward,
                    vlm_reward_model_name=vlm_reward_model_name,
                    use_param_check=use_param_check,
                    new_summary_path=res.get("summary_file") or res.get("summary_path"),
                    prev_summary_path=parent_node.get("summary_path"),
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
                    if struct_key and struct_key in tree["analytics"]["improved_structural_distribution"]:
                        tree["analytics"]["improved_structural_distribution"][struct_key] += 1
                    _ensure_psa(step_key, fine)["improved"] += 1
                else:
                    tree["analytics"]["not_improved_count"] += 1

                is_accepted, acceptance_reason = judge_acceptance(delta_r=step_delta_r, temperature=0.5, force_greedy=force_greedy, accept_all=accept_all)
                mh_accepted = is_accepted and step_delta_r < 0

                node_record = {
                    "node_id": node_id,
                    "parent_id": parent_node["node_id"],
                    "depth": parent_node.get("depth", 0) + 1,
                    "step": step,
                    "action_from_parent": action,
                    "feedme_path": res.get("feedme_path"),
                    "residual_path": next_img,
                    "metrics": res.get("metrics", {}),
                    "delta_R": step_delta_r,
                    "reward_detail": r_detail,
                    "is_accepted": is_accepted,
                    "mh_accepted": mh_accepted,
                    "status": res.get("status"),
                    "summary_path": res.get("summary_file") or res.get("summary_path")
                }
                tree["nodes"].append(node_record)
                
                if is_accepted:
                    next_layer_nodes.append(node_record)
                    tree["analytics"]["per_step_stats"][step_key]["accepted"] += 1
                else:
                    tree["analytics"]["per_step_stats"][step_key]["rejected"] += 1
                    
                flag = "✅ [Accepted]" if is_accepted else "🚫 [Rejected]"
                depth_info = f"depth={node_record['depth']}" if node_record['depth'] != step else ""
                mh_tag = " [MH]" if mh_accepted else ""
                print(f"    {flag}{mh_tag} {node_id} | 父: {parent_node['node_id']} | 动作: {action_type} | 增益: {step_delta_r:.3f} | {acceptance_reason}{(' | ' + depth_info) if depth_info else ''}")

            if next_layer_nodes:
                # Beam pruning: 每层保留 chi2_nu 最好的 K 个,防止深层分支爆炸 + 自动剪相似分支
                if beam_top_k and beam_top_k > 0 and len(next_layer_nodes) > beam_top_k:
                    pruned_count = len(next_layer_nodes) - beam_top_k
                    next_layer_nodes.sort(key=lambda n: n["metrics"].get("chi2_nu", 999.0))
                    next_layer_nodes = next_layer_nodes[:beam_top_k]
                    print(f"    ✂️  [Beam Pruning] 保留 chi2_nu 最好的 {beam_top_k} 个父节点,剪掉 {pruned_count} 个")
                current_layer_nodes = next_layer_nodes
                patience_counter = 0 # 只要有进步，耐心值清零
            else:
                if accept_all:
                    print(f"    ⚠️ 步进 {step} 所有变体被SSIM拦截，ACCEPT_ALL模式下跳过patience计数，继续下一步")
                else:
                    patience_counter += 1
                    print(f"    ⚠️ 步进 {step} 所有变体均未达标。当前耐心值: {patience_counter}/{max_patience}")

                    if patience_counter >= max_patience:
                        print(f"    🛑 连续 {max_patience} 步未达标，触发早停机制 (模拟 Action D 收敛)！提前结束本星系搜索。")
                        break

        # ==================== 落盘结束 ====================
        best_final_node = min(current_layer_nodes, key=lambda x: x["metrics"].get("chi2_nu", 999.0))
        tree["target_feedme"] = best_final_node["feedme_path"]
        tree["target_residual"] = best_final_node["residual_path"]

        # 新增：结算耗时
        end_time = time.time()
        elapsed_time = end_time - start_time
        tree["analytics"]["elapsed_seconds"] = round(elapsed_time, 2)

        # 新增：真实深度统计（基于 accepted 节点的 depth 字段）
        accepted_depths = [n["depth"] for n in tree["nodes"] if n.get("is_accepted") and n.get("depth", 0) > 0]
        mh_accepted_count = sum(1 for n in tree["nodes"] if n.get("mh_accepted"))
        tree["analytics"]["max_depth"] = max(accepted_depths) if accepted_depths else 0
        tree["analytics"]["avg_depth"] = round(sum(accepted_depths) / len(accepted_depths), 2) if accepted_depths else 0
        tree["analytics"]["mh_accepted_count"] = mh_accepted_count
        
        save_trajectory(tree, gal_out_dir)
        # 修改：把耗时打印在最终的战报里
        minutes, seconds = divmod(elapsed_time, 60)
        time_str = f"{int(minutes)}分{int(seconds)}秒" if minutes > 0 else f"{elapsed_time:.2f}秒"
        
        print(f"🎉 {galaxy_id} 处理完毕！最终深度: {best_final_node['depth']}，全场最优 Chi2_nu: {best_final_node['metrics'].get('chi2_nu')}")
        print(f"⏱️ 本星系总耗时: {time_str}")

        return {
            "analytics": tree["analytics"],
            "llm_stats": tree["llm_stats"],
            "success": True
        }