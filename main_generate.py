# main_generate.py
import asyncio
import os
import json
import time  # 🚀 引入 time 模块用于全局耗时统计
from dotenv import load_dotenv

from data_gen.dataset_utils import get_train_test_split
from data_gen.pipeline import DataGenPipeline

# ==========================================
# 🛠️ 任务控制面板 (全局配置)
# ==========================================
TEST_MODE = True
USE_LLM_REWARD = True  # 大模型视觉打分全局开关
PROPOSAL_STRATEGY = "vlm_generated" # 提议策略: "rule_based", "expert_guided", "vlm_generated"
VLM_REWARD_MODEL_NAME = "gemini-3.1-pro-preview" # 大模型视觉打分模型名
VLM_PROPOSAL_MODEL_NAME = "gemini-3.1-pro-preview" # VLM 提议分布模型名（独立于 reward 模型）
USE_PARAM_CHECK = True  # 参数合理性审查（仅 USE_LLM_REWARD=True 时生效，让 VLM 同时审查拟合参数物理合理性）
VLM_PROPOSAL_NUM_CALLS = 4  # VLM 提议并发调用次数（仅 vlm_generated 策略生效）
USE_EXPERT_HINT_FOR_VLM = True  # 是否用 Gadotti_params.json 引导 VLM 提议（仅 vlm_generated 策略生效）
USE_HISTORY_FOR_VLM = True  # 是否把历史轮次摘要(父链路:采纳动作/指标/同层被拒)注入 VLM 提议 prompt（仅 vlm_generated）
VLM_HISTORY_MAX_STEPS = 0  # 历史轮次最多取最近多少步（0=全部，N=只取最近 N 步，控制 prompt 长度）

# 🚀 升级：多波段支持！你可以把想跑的波段全写进这个列表里
TARGET_BANDS = [
    "SDSS_gband", 
    # "SDSS_rband", 
    # "SDSS_iband"
]

# 自动获取当前 GalDecomp_Gen 的根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
GADOTTI_ROOT = os.path.join(PROJECT_ROOT, "gadotti_data")
OUTPUT_ROOT = os.path.join(PROJECT_ROOT, "output")

load_dotenv(override=True)  # .env 优先：覆盖 shell 里可能残留的旧 OPENAI_API_KEY

async def main():
    print(f"🔍 正在启动多波段扫描任务，目标波段: {TARGET_BANDS}")
    
    # 🚀 自动遍历收集所有波段的数据
    all_train_set = []
    all_test_set = []
    
    for band in TARGET_BANDS:
        band_dir = os.path.join(GADOTTI_ROOT, band)
        if not os.path.exists(band_dir):
            print(f"⚠️ 警告: 找不到波段目录 {band_dir}，已跳过。")
            continue
            
        print(f"\n📂 正在扫描波段: {band}")
        # 这里依然调用我们的 get_train_test_split
        train_set, test_set = get_train_test_split(band_dir, num_test=10) # 假设每个波段抽 10 个做测试
        
        all_train_set.extend(train_set)
        all_test_set.extend(test_set)
        
    print(f"\n📦 多波段数据合并完毕: 总测试集 {len(all_test_set)} 个，总训练集 {len(all_train_set)} 个。")

    if not all_test_set and not all_train_set:
        print("❌ 致命错误: 在所有指定的波段中均没有找到任何数据文件！")
        return

    if USE_LLM_REWARD and VLM_REWARD_MODEL_NAME != "none":
        strategy_folder = f"{PROPOSAL_STRATEGY}_proposal_vlm_reward_{VLM_REWARD_MODEL_NAME.lower()}"
    else:
        strategy_folder = f"{PROPOSAL_STRATEGY}_proposal_rule_based_reward"

    if PROPOSAL_STRATEGY == "vlm_generated":
        strategy_folder = f"vlm_proposal_{VLM_PROPOSAL_MODEL_NAME.lower()}"
        if USE_LLM_REWARD and VLM_REWARD_MODEL_NAME != "none":
            strategy_folder += f"_vlm_reward_{VLM_REWARD_MODEL_NAME.lower()}"
        else:
            strategy_folder += "_rule_based_reward"
        if USE_EXPERT_HINT_FOR_VLM:
            strategy_folder += "_experthint"
        if USE_HISTORY_FOR_VLM:
            strategy_folder += "_hist"

    pipeline = DataGenPipeline(
        base_project_dir=GADOTTI_ROOT, # ⚠️ 注意这里传的是 Gadotti 的根目录，方便应对跨目录寻址
        output_root=os.path.join(OUTPUT_ROOT, strategy_folder), 
        max_iter=100,
        proposal_strategy=PROPOSAL_STRATEGY
    )

    print(f"🔍 运行模式: {'测试' if TEST_MODE else '正式'} | VLM打分: {'开启' if USE_LLM_REWARD else '关闭'}")

    # 控制跑测试集还是全量集
    if TEST_MODE:
        # 测试模式下，我们从收集到的全集中切片跑前几个
        target_galaxies = all_train_set[:20] 
        max_steps = 6 # 8
        num_variants = 16 # 16
    else:
        target_galaxies = all_train_set  
        max_steps = 10               
        num_variants = 16            

    # ==========================================
    # 🚀 全局总账本 (Map-Reduce 核心 + 留存高级 Token 统计)
    # ==========================================
    global_total = {
        "processed_galaxies": 0,
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
        "per_step_action_stats": {},
        "add_with_realloc_count": 0,
        "vlm_total_calls": 0,
        "vlm_better_count": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "vlm_proposal_total_calls": 0,
        "vlm_proposal_prompt_tokens": 0,
        "vlm_proposal_completion_tokens": 0,
        "vlm_reward_model_name": VLM_REWARD_MODEL_NAME,
        "per_step_stats": {},
        # 🚀 新增：耗时统计专用字典
        "timing_stats": {
            "total_elapsed_seconds": 0.0,
            "total_elapsed_str": "",
            "average_seconds_per_galaxy": 0.0,
            "per_galaxy_details": {}  # 记录每个星系的独立耗时
        }
    }

    # ==========================================
    # 启动树搜索任务，并收集报告
    # ==========================================
    global_start_time = time.time()  # 🚀 启动大盘全局计时器

    for gal in target_galaxies:
        gal_report = await pipeline.run_galaxy(
            galaxy_id=gal["id"],
            init_feedme=gal["init_feedme"],
            max_steps=max_steps,
            num_variants=num_variants,
            use_llm_reward=USE_LLM_REWARD,
            use_expert_prior= PROPOSAL_STRATEGY == "expert_guided",
            vlm_proposal_model_name=VLM_PROPOSAL_MODEL_NAME if PROPOSAL_STRATEGY == "vlm_generated" else None,
            use_param_check=USE_PARAM_CHECK,
            vlm_proposal_num_calls=VLM_PROPOSAL_NUM_CALLS if PROPOSAL_STRATEGY == "vlm_generated" else 4,
            use_expert_hint_for_vlm=USE_EXPERT_HINT_FOR_VLM if PROPOSAL_STRATEGY == "vlm_generated" else False,
            use_history_for_vlm=USE_HISTORY_FOR_VLM if PROPOSAL_STRATEGY == "vlm_generated" else False,
            history_max_steps=VLM_HISTORY_MAX_STEPS,
        )
        
        if not gal_report.get("success", False):
            continue
            
        global_total["processed_galaxies"] += 1
        analytics = gal_report.get("analytics", {})
        
        # 🚀 记录该星系的独立耗时
        gal_time = analytics.get("elapsed_seconds", 0.0)
        global_total["timing_stats"]["per_galaxy_details"][gal["id"]] = gal_time
        
        global_total["total_actions_generated"] += analytics.get("total_actions_generated", 0)
        global_total["ssim_filtered_count"] += analytics.get("ssim_filtered_count", 0)
        global_total["physics_crashed_count"] += analytics.get("physics_crashed_count", 0)
        global_total["improved_count"] += analytics.get("improved_count", 0)
        global_total["not_improved_count"] += analytics.get("not_improved_count", 0)
        
        for act in ["A", "B", "C"]:
            global_total["action_type_distribution"][act] += analytics.get("action_type_distribution", {}).get(act, 0)
            global_total["improved_action_distribution"][act] += analytics.get("improved_action_distribution", {}).get(act, 0)
            global_total["passed_ssim_action_distribution"][act] += analytics.get("passed_ssim_action_distribution", {}).get(act, 0)

        for struct_key in ["add_disk", "add_bar", "add_bulge", "add_psf", "none"]:
            global_total["structural_distribution"][struct_key] += analytics.get("structural_distribution", {}).get(struct_key, 0)
            global_total["improved_structural_distribution"][struct_key] += analytics.get("improved_structural_distribution", {}).get(struct_key, 0)
            global_total["passed_ssim_structural_distribution"][struct_key] += analytics.get("passed_ssim_structural_distribution", {}).get(struct_key, 0)
        global_total["add_with_realloc_count"] += analytics.get("add_with_realloc_count", 0)

        # 聚合分步×动作漏斗
        for step_k, label_map in analytics.get("per_step_action_stats", {}).items():
            gt_psa = global_total["per_step_action_stats"].setdefault(step_k, {})
            for label, d in label_map.items():
                acc = gt_psa.setdefault(label, {"generated": 0, "passed_ssim": 0, "improved": 0, "ssim_filtered": 0, "crashed": 0})
                for k in ("generated", "passed_ssim", "improved", "ssim_filtered", "crashed"):
                    acc[k] += d.get(k, 0)

        if USE_LLM_REWARD:
            llm_stats = gal_report.get("llm_stats", {})
            global_total["vlm_total_calls"] += llm_stats.get("vlm_total_calls", 0)
            global_total["vlm_better_count"] += llm_stats.get("vlm_better_count", 0)
            global_total["total_prompt_tokens"] += llm_stats.get("total_prompt_tokens", 0)
            global_total["total_completion_tokens"] += llm_stats.get("total_completion_tokens", 0)

        if PROPOSAL_STRATEGY == "vlm_generated":
            llm_stats = gal_report.get("llm_stats", {})
            global_total["vlm_proposal_total_calls"] += llm_stats.get("vlm_proposal_calls", 0)
            global_total["vlm_proposal_prompt_tokens"] += llm_stats.get("vlm_proposal_prompt_tokens", 0)
            global_total["vlm_proposal_completion_tokens"] += llm_stats.get("vlm_proposal_completion_tokens", 0)

        for step_key, step_data in analytics.get("per_step_stats", {}).items():
            if step_key not in global_total["per_step_stats"]:
                global_total["per_step_stats"][step_key] = {"generated": 0, "accepted": 0, "ssim_filtered": 0, "rejected": 0, "physics_crashed": 0}
            for k in ("generated", "accepted", "ssim_filtered", "rejected", "physics_crashed"):
                global_total["per_step_stats"][step_key][k] += step_data.get(k, 0)

    # ==========================================
    # 🚀 大盘跑完，结算全局耗时统计
    # ==========================================
    global_elapsed = time.time() - global_start_time
    global_total["timing_stats"]["total_elapsed_seconds"] = round(global_elapsed, 2)
    
    hours, rem = divmod(global_elapsed, 3600)
    minutes, seconds = divmod(rem, 60)
    global_total["timing_stats"]["total_elapsed_str"] = f"{int(hours)}小时 {int(minutes)}分 {int(seconds)}秒"
    
    if global_total["processed_galaxies"] > 0:
        avg_time = global_elapsed / global_total["processed_galaxies"]
        global_total["timing_stats"]["average_seconds_per_galaxy"] = round(avg_time, 2)

    # ==========================================
    # 打印与保存全局大盘
    # ==========================================
    print("\n" + "🌟"*30)
    print("📈 [全数据集多波段生成任务总结大盘]")
    print("🌟"*30)
    
    if global_total['processed_galaxies'] > 0:
        print(f"✅ 处理星系总数: {global_total['processed_galaxies']}")
        print(f"🚀 生成变体总数: {global_total['total_actions_generated']}")
        print(f"  ├─ 被 SSIM 过滤/物理抛弃: {global_total['ssim_filtered_count']}")
        print(f"  ├─ 物理引擎崩溃: {global_total['physics_crashed_count']}")
        
        valid_count = global_total['improved_count'] + global_total['not_improved_count']
        print(f"  └─ 有效评估总数: {valid_count}")
        print(f"     ├─ 成功优化残差: {global_total['improved_count']}")
        print(f"     └─ 优化失败/平庸: {global_total['not_improved_count']}")
        
        # 🚀 打印耗时统计模块
        print("\n⏱️ [算力消耗统计]")
        print(f"  ├─ 总计耗时: {global_total['timing_stats']['total_elapsed_str']}")
        print(f"  └─ 平均耗时: {global_total['timing_stats']['average_seconds_per_galaxy']} 秒/星系")
        
        print("\n🎯 [全局 Action 策略效能评估]")
        for act in ["A", "B", "C"]:
            total = global_total['action_type_distribution'][act]
            success = global_total['improved_action_distribution'][act]
            rate = (success/total*100) if total > 0 else 0
            print(f"  - 动作 {act}: 产出 {success}/{total} 次有效提升 (成功率 {rate:.1f}%)")

        if PROPOSAL_STRATEGY == "vlm_generated":
            def _funnel_line(label, gen, ssim, imp, width=12):
                ssim_rate = (ssim / gen * 100) if gen > 0 else 0
                rwd_rate = (imp / ssim * 100) if ssim > 0 else 0
                total_rate = (imp / gen * 100) if gen > 0 else 0
                return (f"  {label:<{width}} 生成 {gen:>3} | 过SSIM {ssim:>3} ({ssim_rate:>5.1f}%) | "
                        f"过reward {imp:>3} (占过SSIM {rwd_rate:>5.1f}%) | 总成功率 {total_rate:>5.1f}%")

            # 表1: A/B/C 动作漏斗
            print("\n🧩 [表1: A/B/C 动作漏斗] (生成 → 过SSIM → 过reward)")
            for act in ["A", "B", "C"]:
                _g = global_total['action_type_distribution'][act]
                _s = global_total['passed_ssim_action_distribution'][act]
                _i = global_total['improved_action_distribution'][act]
                print(_funnel_line(f"动作{act}", _g, _s, _i))

            # 表2: structural 细分漏斗
            print("\n🧩 [表2: structural 细分漏斗] (A=add_*; none=纯调参C)")
            for struct_key in ["add_disk", "add_bar", "add_bulge", "add_psf", "none"]:
                _g = global_total['structural_distribution'][struct_key]
                _s = global_total['passed_ssim_structural_distribution'][struct_key]
                _i = global_total['improved_structural_distribution'][struct_key]
                print(_funnel_line(struct_key, _g, _s, _i))
            add_total = sum(global_total['structural_distribution'][k] for k in ["add_disk", "add_bar", "add_bulge", "add_psf"])
            realloc = global_total['add_with_realloc_count']
            realloc_rate = (realloc/add_total*100) if add_total > 0 else 0
            print(f"  加成分决策中携带参数重分配: {realloc}/{add_total} ({realloc_rate:.1f}%)")

            # 表3: 分步 × 动作 漏斗 (方案B: add_disk/add_bar/add_bulge/add_psf/modify/delete; 旧: A_pure/A_realloc/B/C)
            if global_total["per_step_action_stats"]:
                print("\n🧩 [表3: 分步 × 动作 漏斗] (add_*=加对应成分; modify=纯改参/改fix; delete=删成分)")
                preferred = ["add_disk", "add_bar", "add_bulge", "add_psf", "modify", "delete",
                             "A_pure", "A_realloc", "B", "C"]
                for step_k in sorted(global_total["per_step_action_stats"].keys(), key=lambda x: int(x)):
                    lm = global_total["per_step_action_stats"][step_k]
                    ordered = [l for l in preferred if l in lm] + [l for l in lm if l not in preferred]
                    print(f"  ── Step {step_k} ──")
                    for label in ordered:
                        d = lm[label]
                        print("  " + _funnel_line(label, d["generated"], d["passed_ssim"], d["improved"]))

        if global_total["per_step_stats"]:
            print("\n📊 [分步成功率统计]")
            for step_key in sorted(global_total["per_step_stats"].keys(), key=lambda x: int(x)):
                s = global_total["per_step_stats"][step_key]
                gen = s["generated"]
                acc = s["accepted"]
                rate = (acc / gen * 100) if gen > 0 else 0
                print(f"  Step {step_key}: 生成 {gen}, 接受 {acc} ({rate:.1f}%), SSIM过滤 {s['ssim_filtered']}, 拒绝 {s['rejected']}, 崩溃 {s['physics_crashed']}")
            
        if USE_LLM_REWARD:
            hit_rate = (global_total["vlm_better_count"] / global_total["vlm_total_calls"]) * 100 if global_total["vlm_total_calls"] > 0 else 0
            total_tokens = global_total['total_prompt_tokens'] + global_total['total_completion_tokens']
            
            print("\n🧠 [LLM 消耗与成本结算大盘]")
            print(f"  🤖 使用模型: {VLM_REWARD_MODEL_NAME}")
            print(f"  📊 环节 1: [判定残差图优化状态]")
            print(f"     ├─ API 成功调用次数: {global_total['vlm_total_calls']} 次")
            print(f"     ├─ 残差变好判定率: {global_total['vlm_better_count']} / {global_total['vlm_total_calls']} ({hit_rate:.1f}%)")
            print(f"     ├─ 输入 Token (Prompt): {global_total['total_prompt_tokens']:,}")
            print(f"     ├─ 输出 Token (Completion): {global_total['total_completion_tokens']:,}")
            print(f"     └─ 环节总消耗 Token: {total_tokens:,}")

        if PROPOSAL_STRATEGY == "vlm_generated":
            proposal_tokens = global_total['vlm_proposal_prompt_tokens'] + global_total['vlm_proposal_completion_tokens']
            print(f"\n🔮 [VLM 提议分布消耗统计]")
            print(f"  🤖 提议模型: {VLM_PROPOSAL_MODEL_NAME}")
            print(f"  ├─ API 调用次数: {global_total['vlm_proposal_total_calls']} 次")
            print(f"  ├─ 输入 Token (Prompt): {global_total['vlm_proposal_prompt_tokens']:,}")
            print(f"  ├─ 输出 Token (Completion): {global_total['vlm_proposal_completion_tokens']:,}")
            print(f"  └─ 提议环节总消耗 Token: {proposal_tokens:,}")
        
        global_report_path = os.path.join(OUTPUT_ROOT, strategy_folder, "global_analytics_report.json")
        try:
            with open(global_report_path, "w", encoding="utf-8") as f:
                json.dump(global_total, f, indent=4, ensure_ascii=False)
            print(f"\n💾 全局多波段统计大盘已安全保存至:\n   {global_report_path}")
        except Exception as e:
            print(f"⚠️ 保存全局统计报告失败: {e}")
            
    else:
        print("⚠️ 未处理任何星系，无统计数据生成。")
        
    print("🌟"*30 + "\n")

if __name__ == "__main__":
    if not os.getenv("GALFIT_BIN"):
        print("⚠️ 警告: 未检测到 GALFIT_BIN 环境变量。")
    asyncio.run(main())