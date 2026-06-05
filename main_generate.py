# main_generate.py
import asyncio
import os
import json
from dotenv import load_dotenv

from data_gen.dataset_utils import get_train_test_split
from data_gen.pipeline import DataGenPipeline

# ==========================================
# 🛠️ 任务控制面板 (全局配置)
# ==========================================
TEST_MODE = True  
USE_LLM_REWARD = True  # 大模型视觉打分全局开关
USE_EXPERT_PRIOR = False # 是否使用 GT JSON 数据来指导提议分布

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

load_dotenv()

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

    pipeline = DataGenPipeline(
        base_project_dir=GADOTTI_ROOT, # ⚠️ 注意这里传的是 Gadotti 的根目录，方便应对跨目录寻址
        output_root=OUTPUT_ROOT, 
        max_iter=100 
    )

    print(f"🔍 运行模式: {'测试' if TEST_MODE else '正式'} | VLM打分: {'开启' if USE_LLM_REWARD else '关闭'}")

    # 控制跑测试集还是全量集
    if TEST_MODE:
        # 测试模式下，我们从收集到的全集中切片跑前几个
        target_galaxies = all_train_set[:20] 
        max_steps = 1 # 8
        num_variants = 5 # 16
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
        "vlm_total_calls": 0,
        "vlm_better_count": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "models_used": set()
    }

    # ==========================================
    # 启动树搜索任务，并收集报告
    # ==========================================
    for gal in target_galaxies:
        gal_report = await pipeline.run_galaxy(
            galaxy_id=gal["id"],
            init_feedme=gal["init_feedme"],
            max_steps=max_steps,
            num_variants=num_variants,
            use_llm_reward=USE_LLM_REWARD,
            use_expert_prior=USE_EXPERT_PRIOR
        )
        
        if not gal_report.get("success", False):
            continue
            
        global_total["processed_galaxies"] += 1
        
        analytics = gal_report.get("analytics", {})
        global_total["total_actions_generated"] += analytics.get("total_actions_generated", 0)
        global_total["ssim_filtered_count"] += analytics.get("ssim_filtered_count", 0)
        global_total["physics_crashed_count"] += analytics.get("physics_crashed_count", 0)
        global_total["improved_count"] += analytics.get("improved_count", 0)
        global_total["not_improved_count"] += analytics.get("not_improved_count", 0)
        
        for act in ["A", "B", "C"]:
            global_total["action_type_distribution"][act] += analytics.get("action_type_distribution", {}).get(act, 0)
            global_total["improved_action_distribution"][act] += analytics.get("improved_action_distribution", {}).get(act, 0)
            
        if USE_LLM_REWARD:
            llm_stats = gal_report.get("llm_stats", {})
            global_total["vlm_total_calls"] += llm_stats.get("vlm_total_calls", 0)
            global_total["vlm_better_count"] += llm_stats.get("vlm_better_count", 0)
            global_total["total_prompt_tokens"] += llm_stats.get("total_prompt_tokens", 0)
            global_total["total_completion_tokens"] += llm_stats.get("total_completion_tokens", 0)
            
            model = llm_stats.get("model_used")
            if model and model != "None":
                global_total["models_used"].add(model)

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
        
        print("\n🎯 [全局 Action 策略效能评估]")
        for act in ["A", "B", "C"]:
            total = global_total['action_type_distribution'][act]
            success = global_total['improved_action_distribution'][act]
            rate = (success/total*100) if total > 0 else 0
            print(f"  - 动作 {act}: 产出 {success}/{total} 次有效提升 (成功率 {rate:.1f}%)")
            
        if USE_LLM_REWARD:
            hit_rate = (global_total["vlm_better_count"] / global_total["vlm_total_calls"]) * 100 if global_total["vlm_total_calls"] > 0 else 0
            total_tokens = global_total['total_prompt_tokens'] + global_total['total_completion_tokens']
            models_str = ", ".join(global_total["models_used"]) if global_total["models_used"] else "未知"
            
            print("\n🧠 [LLM 消耗与成本结算大盘]")
            print(f"  🤖 使用模型: {models_str}")
            print(f"  📊 环节 1: [判定残差图优化状态]")
            print(f"     ├─ API 成功调用次数: {global_total['vlm_total_calls']} 次")
            print(f"     ├─ 残差变好判定率: {global_total['vlm_better_count']} / {global_total['vlm_total_calls']} ({hit_rate:.1f}%)")
            print(f"     ├─ 输入 Token (Prompt): {global_total['total_prompt_tokens']:,}")
            print(f"     ├─ 输出 Token (Completion): {global_total['total_completion_tokens']:,}")
            print(f"     └─ 环节总消耗 Token: {total_tokens:,}")
        
        global_total["models_used"] = list(global_total["models_used"])
        global_report_path = os.path.join(OUTPUT_ROOT, "global_analytics_report.json")
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