# main_generate.py
import asyncio
import os
import json
import time  # 🚀 引入 time 模块用于全局耗时统计
import datetime  # RUN_ID 时间戳
try:
    from dotenv import load_dotenv
except ImportError:  # dotenv 非必需：环境变量可能已由 shell 设置
    def load_dotenv(*a, **k):
        return False

from data_gen.dataset_utils import get_train_test_split
from data_gen.pipeline import DataGenPipeline
import glob
import hashlib
import shutil


def compute_config_signature(cfg: dict) -> str:
    """关键配置的哈希：配置变了→旧星系结果不可复用，续跑时该星系重跑。"""
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:12]


def is_galaxy_complete(gal_dir: str, gid: str, config_signature: str) -> bool:
    """星系「已完成」= trajectory.json 存在、合法、带 completed 标记、且配置签名一致。"""
    fp = os.path.join(gal_dir, f"{gid}_trajectory.json")
    if not os.path.exists(fp):
        return False
    try:
        with open(fp, "r", encoding="utf-8") as f:
            tree = json.load(f)
    except Exception:
        return False  # 损坏/截断 → 视为未完成
    meta = tree.get("metadata", {})
    if not meta.get("completed"):
        return False
    if config_signature and meta.get("config_signature") not in (None, config_signature):
        return False  # 配置变了，重跑
    return True


def reset_galaxy_dir(gal_dir: str):
    """清掉未完成星系的残留（半成品目录/archives），保证从头干净重跑。"""
    if os.path.isdir(gal_dir):
        shutil.rmtree(gal_dir, ignore_errors=True)


def _aggregate_into_global(global_total: dict, analytics: dict, llm_stats: dict, gal_id: str,
                           use_llm_reward: bool, is_vlm: bool):
    """把单个星系的 analytics/llm_stats 累加进全局大盘（从磁盘重聚合时复用）。"""
    global_total["processed_galaxies"] += 1
    global_total["timing_stats"]["per_galaxy_details"][gal_id] = analytics.get("elapsed_seconds", 0.0)
    global_total["total_actions_generated"] += analytics.get("total_actions_generated", 0)
    global_total["ssim_filtered_count"] += analytics.get("ssim_filtered_count", 0)
    global_total["physics_crashed_count"] += analytics.get("physics_crashed_count", 0)
    global_total["improved_count"] += analytics.get("improved_count", 0)
    global_total["depth_stats"]["max_depths"].append(analytics.get("max_depth", 0))
    global_total["depth_stats"]["mh_accepted_total"] += analytics.get("mh_accepted_count", 0)
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

    for step_k, label_map in analytics.get("per_step_action_stats", {}).items():
        gt_psa = global_total["per_step_action_stats"].setdefault(step_k, {})
        for label, d in label_map.items():
            acc = gt_psa.setdefault(label, {"generated": 0, "passed_ssim": 0, "improved": 0, "ssim_filtered": 0, "crashed": 0})
            for k in ("generated", "passed_ssim", "improved", "ssim_filtered", "crashed"):
                acc[k] += d.get(k, 0)

    if use_llm_reward:
        global_total["vlm_total_calls"] += llm_stats.get("vlm_total_calls", 0)
        global_total["vlm_better_count"] += llm_stats.get("vlm_better_count", 0)
        global_total["total_prompt_tokens"] += llm_stats.get("total_prompt_tokens", 0)
        global_total["total_completion_tokens"] += llm_stats.get("total_completion_tokens", 0)

    if is_vlm:
        global_total["vlm_proposal_total_calls"] += llm_stats.get("vlm_proposal_calls", 0)
        global_total["vlm_proposal_prompt_tokens"] += llm_stats.get("vlm_proposal_prompt_tokens", 0)
        global_total["vlm_proposal_completion_tokens"] += llm_stats.get("vlm_proposal_completion_tokens", 0)

    for step_key, step_data in analytics.get("per_step_stats", {}).items():
        if step_key not in global_total["per_step_stats"]:
            global_total["per_step_stats"][step_key] = {"generated": 0, "accepted": 0, "ssim_filtered": 0, "rejected": 0, "physics_crashed": 0}
        for k in ("generated", "accepted", "ssim_filtered", "rejected", "physics_crashed"):
            global_total["per_step_stats"][step_key][k] += step_data.get(k, 0)


# ==========================================
# 🛠️ 任务控制面板 (全局配置)
# ==========================================
TEST_MODE = True
EXPERIMENT_ID = "E7r"  # 实验编号，输出目录和日志自动带上（E1/E2/E3/E4/E5/E6/E6r/E7/E7r）
USE_LLM_REWARD = True  # 大模型视觉打分全局开关
PROPOSAL_STRATEGY = "vlm_generated" # 提议策略: "rule_based", "expert_guided", "vlm_generated"
VLM_REWARD_MODEL_NAME = "gemini-3.1-pro-preview" # 大模型视觉打分模型名
VLM_PROPOSAL_MODEL_NAME = "gemini-3.1-pro-preview" # VLM 提议分布模型名（独立于 reward 模型）
USE_PARAM_CHECK = True  # 参数合理性审查（仅 USE_LLM_REWARD=True 时生效，让 VLM 同时审查拟合参数物理合理性）
VLM_PROPOSAL_NUM_CALLS = 1  # VLM 提议并发调用次数（仅 vlm_generated 策略生效）。深层分支爆炸由 BEAM_TOP_K 兜底
USE_EXPERT_HINT_FOR_VLM = False  # 是否用 Gadotti_params.json 引导 VLM 提议（仅 vlm_generated 策略生效）
USE_HISTORY_FOR_VLM = True  # 是否把历史轮次摘要(父链路:采纳动作/指标/同层被拒)注入 VLM 提议 prompt（仅 vlm_generated）
VLM_HISTORY_MAX_STEPS = 0  # 历史轮次最多取最近多少步（0=全部，N=只取最近 N 步，控制 prompt 长度）
BEAM_TOP_K = 1  # 每个 step 后保留 chi2_nu 最好的 K 个 accepted 父节点（防止深层分支爆炸 + 自动剪相似分支）
VLM_REWARD_IMAGE_MODE = "full"  # VLM reward 看图模式: "full"=完整 comparison.png (1×4: orig|model|residual|1D SB); "cutoff"=仅残差面板裁剪图
FORCE_GREEDY = False  # 贪心接受开关: True=只接受改善(delta_r>=0)的变体; False=启用 Metropolis-Hastings 退火,允许小幅回退以探索更深轨迹
VLM_PROPOSAL_MULTITURN = False  # 多轮VLM提议开关: True=3轮对话(Turn1视觉→Turn2推理→Turn3决策,保留thinking_chain); False=兼容旧单轮
ACCEPT_ALL = True  # 全部接受模式: True=无论reward结果如何一律接受,模拟MCP交互式agent流程(FORCE_GREEDY将被忽略)
MAX_PATIENCE = 2  # 连续多少步全部变体未被接受时触发早停（默认2；ACCEPT_ALL时不生效,由MAX_STEPS兜底）
MAX_STEPS = 15  # 最大搜索深度（覆盖TEST_MODE默认的6步；ACCEPT_ALL模式下此为唯一硬上限）

# 断点续跑配置
FRESH_START = False  # True=清空实验目录从头跑; False=续跑(跳过已完成星系,重跑未完成的)
MAX_CONSECUTIVE_API_FAIL = 3  # 连续N个星系因API故障失败→判定key失效,保存进度干净退出

# 🚀 升级：多波段支持！你可以把想跑的波段全写进这个列表里
TARGET_BANDS = [
    # "SDSS_gband",
    "SDSS_rband",
    # "SDSS_iband"
]

# 🎯 指定星系过滤：非空时只跑列表里的星系（按 id 后缀匹配，兼容带/不带波段前缀）。
# 空列表 = 维持原行为（TEST_MODE 取前20）。用于在特定数据集上对标（如 MCP 70% 的20个r波段星系）。
# 注意：用此功能对标时，TARGET_BANDS 要包含对应波段（如 SDSS_rband）。
GALAXY_ID_FILTER = [
    "Plate0270_MJD51909_Fiber095_r", "Plate0271_MJD51883_Fiber005_r",
    "Plate0274_MJD51913_Fiber503_r", "Plate0276_MJD51909_Fiber629_r",
    "Plate0284_MJD51943_Fiber397_r", "Plate0295_MJD51985_Fiber525_r",
    "Plate0300_MJD51943_Fiber581_r", "Plate0330_MJD52370_Fiber568_r",
    "Plate0391_MJD51782_Fiber072_r", "Plate0391_MJD51782_Fiber501_r",
    "Plate0414_MJD51901_Fiber247_r", "Plate0436_MJD51883_Fiber493_r",
    "Plate0461_MJD51910_Fiber109_r", "Plate0501_MJD52235_Fiber338_r",
    "Plate0535_MJD51999_Fiber497_r", "Plate0536_MJD52024_Fiber323_r",
    "Plate0556_MJD51991_Fiber236_r", "Plate0721_MJD52228_Fiber011_r",
    "Plate0765_MJD52254_Fiber182_r", "Plate0887_MJD52376_Fiber382_r",
]

# 自动获取当前 GalDecomp_Gen 的根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
GADOTTI_ROOT = os.path.join(PROJECT_ROOT, "gadotti_data")
OUTPUT_ROOT = os.path.join(PROJECT_ROOT, "output")

load_dotenv(override=True)  # .env 优先：覆盖 shell 里可能残留的旧 OPENAI_API_KEY

async def main():
    # 每次运行生成唯一时间戳，贯穿 strategy_folder 与日志展示，确保实验互不覆盖
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ==========================================
    # 📋 配置参数打印(日志开头一次性记录,便于复盘)
    # ==========================================
    print("=" * 70)
    print("📋 [本次运行配置]")
    print("=" * 70)
    print(f"  RUN_ID                    = {run_id}")
    print(f"  EXPERIMENT_ID             = {EXPERIMENT_ID}")
    print(f"  TEST_MODE                 = {TEST_MODE}")
    print(f"  PROPOSAL_STRATEGY         = {PROPOSAL_STRATEGY}")
    print(f"  TARGET_BANDS              = {TARGET_BANDS}")
    print(f"  USE_LLM_REWARD            = {USE_LLM_REWARD}")
    print(f"  USE_PARAM_CHECK           = {USE_PARAM_CHECK}")
    print(f"  VLM_REWARD_MODEL_NAME     = {VLM_REWARD_MODEL_NAME}")
    print(f"  VLM_PROPOSAL_MODEL_NAME   = {VLM_PROPOSAL_MODEL_NAME}")
    print(f"  VLM_PROPOSAL_NUM_CALLS    = {VLM_PROPOSAL_NUM_CALLS}")
    print(f"  USE_EXPERT_HINT_FOR_VLM   = {USE_EXPERT_HINT_FOR_VLM}")
    print(f"  USE_HISTORY_FOR_VLM       = {USE_HISTORY_FOR_VLM}")
    print(f"  VLM_HISTORY_MAX_STEPS     = {VLM_HISTORY_MAX_STEPS} (0=全部)")
    print(f"  BEAM_TOP_K                = {BEAM_TOP_K} (每层保留 chi2_nu 最好的 K 个父节点)")
    print(f"  VLM_REWARD_IMAGE_MODE     = {VLM_REWARD_IMAGE_MODE} (full=完整comparison.png, cutoff=仅残差面板)")
    print(f"  FORCE_GREEDY              = {FORCE_GREEDY} (True=纯贪心, False=MH退火探索)")
    print(f"  VLM_PROPOSAL_MULTITURN    = {VLM_PROPOSAL_MULTITURN} (True=3轮对话提议+thinking_chain)")
    print(f"  ACCEPT_ALL                = {ACCEPT_ALL} (True=全部接受,模拟MCP交互式流程)")
    print(f"  MAX_PATIENCE              = {MAX_PATIENCE} (连续N步零接受触发早停)")
    print(f"  MAX_STEPS                 = {MAX_STEPS} (最大搜索深度)")
    print(f"  PROJECT_ROOT              = {PROJECT_ROOT}")
    print(f"  GADOTTI_ROOT              = {GADOTTI_ROOT}")
    print(f"  OUTPUT_ROOT               = {OUTPUT_ROOT}")
    print("=" * 70)

    print(f"\n🔍 正在启动多波段扫描任务，目标波段: {TARGET_BANDS}")
    
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

    # 稳定目录：按 EXPERIMENT_ID 定位（不带 run_id 时间戳），已存在即续跑进同一目录。
    # run_id 改存进每个星系的 metadata 供溯源。
    strategy_folder = f"{EXPERIMENT_ID}__{strategy_folder}" if EXPERIMENT_ID else strategy_folder

    # 配置签名：关键参数变了→旧星系结果不复用（续跑时重跑）
    config_signature = compute_config_signature({
        "strategy": PROPOSAL_STRATEGY, "proposal_model": VLM_PROPOSAL_MODEL_NAME,
        "reward_model": VLM_REWARD_MODEL_NAME, "multiturn": VLM_PROPOSAL_MULTITURN,
        "num_calls": VLM_PROPOSAL_NUM_CALLS, "accept_all": ACCEPT_ALL,
        "force_greedy": FORCE_GREEDY, "max_steps": MAX_STEPS, "beam_top_k": BEAM_TOP_K,
        "bands": TARGET_BANDS, "use_param_check": USE_PARAM_CHECK,
        "expert_hint": USE_EXPERT_HINT_FOR_VLM, "use_history": USE_HISTORY_FOR_VLM,
    })

    _folder_abs = os.path.join(OUTPUT_ROOT, strategy_folder)
    if FRESH_START and os.path.isdir(_folder_abs):
        print(f"🧹 FRESH_START=True，清空实验目录: {_folder_abs}")
        shutil.rmtree(_folder_abs, ignore_errors=True)


    pipeline = DataGenPipeline(
        base_project_dir=GADOTTI_ROOT, # ⚠️ 注意这里传的是 Gadotti 的根目录，方便应对跨目录寻址
        output_root=os.path.join(OUTPUT_ROOT, strategy_folder), 
        max_iter=100,
        proposal_strategy=PROPOSAL_STRATEGY
    )

    print(f"🔍 运行模式: {'测试' if TEST_MODE else '正式'} | VLM打分: {'开启' if USE_LLM_REWARD else '关闭'}")

    # 控制跑测试集还是全量集
    if GALAXY_ID_FILTER:
        # 🎯 指定星系模式：从全集(train+test)中按 id 后缀匹配挑出指定星系
        all_galaxies = all_train_set + all_test_set
        def _match(g):
            gid = g["id"]
            return any(gid == f or gid.endswith(f) or gid.endswith("_" + f) for f in GALAXY_ID_FILTER)
        target_galaxies = [g for g in all_galaxies if _match(g)]
        matched_ids = {g["id"] for g in target_galaxies}
        # 报告哪些指定星系没找到，避免静默漏跑
        missing = [f for f in GALAXY_ID_FILTER
                   if not any(g["id"] == f or g["id"].endswith(f) or g["id"].endswith("_" + f)
                              for g in all_galaxies)]
        print(f"  🎯 指定星系模式: 请求 {len(GALAXY_ID_FILTER)} 个, 匹配到 {len(target_galaxies)} 个")
        if missing:
            print(f"  ⚠️ 未在数据集中找到的星系({len(missing)}个): {missing}")
        max_steps = MAX_STEPS
        num_variants = 16
    elif TEST_MODE:
        # 测试模式下，我们从收集到的全集中切片跑前几个
        target_galaxies = all_train_set[:20]
        max_steps = MAX_STEPS
        num_variants = 16
    else:
        target_galaxies = all_train_set
        max_steps = MAX_STEPS
        num_variants = 16

    print(f"  → 本次跑 {len(target_galaxies)} 个星系 | max_steps={max_steps} | num_variants={num_variants} | strategy_folder={strategy_folder}\n")

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
        # 深度统计
        "depth_stats": {
            "max_depths": [],
            "mh_accepted_total": 0,
        },
        # 耗时统计专用字典
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

    is_vlm = PROPOSAL_STRATEGY == "vlm_generated"
    strategy_out_root = pipeline.output_root

    # ---- 续跑扫描：区分已完成 vs 待跑 ----
    to_run, already_done = [], []
    for gal in target_galaxies:
        gdir = os.path.join(strategy_out_root, gal["id"])
        if is_galaxy_complete(gdir, gal["id"], config_signature):
            already_done.append(gal)
        else:
            to_run.append(gal)
    print(f"\n🔁 [续跑] 目标 {len(target_galaxies)} 个 | 已完成 {len(already_done)} 个 | 本次待跑 {len(to_run)} 个")

    # ---- 运行待跑星系（未完成的先清残留再从头跑）+ API 熔断 ----
    consecutive_api_fail = 0
    aborted = False
    for gal in to_run:
        gdir = os.path.join(strategy_out_root, gal["id"])
        reset_galaxy_dir(gdir)  # 清掉半成品，保证从头干净重跑
        try:
            gal_report = await pipeline.run_galaxy(
                galaxy_id=gal["id"],
                init_feedme=gal["init_feedme"],
                max_steps=max_steps,
                num_variants=num_variants,
                use_llm_reward=USE_LLM_REWARD,
                use_expert_prior= PROPOSAL_STRATEGY == "expert_guided",
                vlm_proposal_model_name=VLM_PROPOSAL_MODEL_NAME if is_vlm else None,
                use_param_check=USE_PARAM_CHECK,
                vlm_proposal_num_calls=VLM_PROPOSAL_NUM_CALLS if is_vlm else 4,
                use_expert_hint_for_vlm=USE_EXPERT_HINT_FOR_VLM if is_vlm else False,
                use_history_for_vlm=USE_HISTORY_FOR_VLM if is_vlm else False,
                history_max_steps=VLM_HISTORY_MAX_STEPS,
                beam_top_k=BEAM_TOP_K,
                vlm_reward_image_mode=VLM_REWARD_IMAGE_MODE,
                force_greedy=FORCE_GREEDY,
                accept_all=ACCEPT_ALL,
                max_patience=MAX_PATIENCE,
                vlm_proposal_multiturn=VLM_PROPOSAL_MULTITURN if is_vlm else False,
                run_id=run_id,
                config_signature=config_signature,
            )
        except Exception as e:
            # 星系跑崩(多为 API 抛错) → 未落完成标记，下次续跑会重跑；连续多次触发熔断
            consecutive_api_fail += 1
            print(f"❌ [星系失败/疑似API中断] {gal['id']}: {e}")
            print(f"   连续 API 失败 {consecutive_api_fail}/{MAX_CONSECUTIVE_API_FAIL}（该星系未落完成标记，下次续跑会重试）")
            if consecutive_api_fail >= MAX_CONSECUTIVE_API_FAIL:
                print(f"\n🛑 连续 {MAX_CONSECUTIVE_API_FAIL} 个星系 API 故障，判定 key 失效/系统性故障。")
                print("   已完成星系的进度均已落盘；修复 API key 后重跑本脚本即可从断点续跑。")
                aborted = True
                break
            continue

        if gal_report.get("success"):
            consecutive_api_fail = 0
        elif gal_report.get("fail_reason") == "init_crash":
            consecutive_api_fail = 0  # 星系自身问题(非API)，已写 failed 终态，不重试
        else:
            consecutive_api_fail += 1
            if consecutive_api_fail >= MAX_CONSECUTIVE_API_FAIL:
                print(f"\n🛑 连续 {MAX_CONSECUTIVE_API_FAIL} 次失败，退出；修复后重跑即可续跑。")
                aborted = True
                break

    # ---- 全局报告从磁盘重聚合（续跑正确性：不依赖内存累加）----
    for gal in target_galaxies:
        fp = os.path.join(strategy_out_root, gal["id"], f"{gal['id']}_trajectory.json")
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                tree = json.load(f)
        except Exception:
            continue
        if tree.get("metadata", {}).get("status") != "success":
            continue  # 只聚合成功完成的星系
        _aggregate_into_global(global_total, tree.get("analytics", {}), tree.get("llm_stats", {}),
                               gal["id"], USE_LLM_REWARD, is_vlm)

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

        # 深度统计
        max_depths = global_total['depth_stats']['max_depths']
        mh_total = global_total['depth_stats']['mh_accepted_total']
        avg_max_depth = round(sum(max_depths) / len(max_depths), 2) if max_depths else 0
        overall_max = max(max_depths) if max_depths else 0
        print(f"\n📏 [深度统计]")
        print(f"  ├─ 平均最大深度: {avg_max_depth}")
        print(f"  ├─ 全局最大深度: {overall_max}")
        print(f"  └─ 退火接受(MH)总数: {mh_total}")

        # 打印耗时统计模块
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

    # ==========================================
    # 🧾 完整性交代：确保不留没跑完的星系
    # ==========================================
    done_success, done_failed, incomplete = [], [], []
    for gal in target_galaxies:
        gid = gal["id"]
        fp = os.path.join(strategy_out_root, gid, f"{gid}_trajectory.json")
        status = None
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    meta = json.load(f).get("metadata", {})
                if meta.get("completed"):
                    status = meta.get("status", "success")
            except Exception:
                status = None
        if status == "success":
            done_success.append(gid)
        elif status == "failed_init":
            done_failed.append(gid)
        else:
            incomplete.append(gid)

    progress = {
        "experiment": EXPERIMENT_ID,
        "config_signature": config_signature,
        "target": len(target_galaxies),
        "completed_success": len(done_success),
        "failed_init": len(done_failed),
        "incomplete": len(incomplete),
        "incomplete_ids": incomplete,
        "aborted_by_circuit_breaker": aborted,
    }
    try:
        from data_gen.acceptance import atomic_write_json
        atomic_write_json(progress, os.path.join(strategy_out_root, "progress_report.json"))
    except Exception as e:
        print(f"⚠️ progress_report 保存失败: {e}")

    print("\n🧾 [完整性交代]")
    print(f"  目标 {len(target_galaxies)} | 成功 {len(done_success)} | init失败(终态) {len(done_failed)} | 未完成 {len(incomplete)}")
    if incomplete:
        if aborted:
            print(f"  ⚠️ 因 API 熔断提前退出，{len(incomplete)} 个星系未跑完 → 修复 key 后重跑本脚本即自动续跑：")
        else:
            print(f"  ❗ 仍有 {len(incomplete)} 个星系未完成（异常残留），重跑本脚本会自动重跑它们：")
        print(f"     {incomplete[:10]}{' ...' if len(incomplete) > 10 else ''}")
    else:
        print("  ✅ 所有目标星系均已到达终态（成功或明确失败），无半成品残留。")

    print("🌟"*30 + "\n")

if __name__ == "__main__":
    if not os.getenv("GALFIT_BIN"):
        print("⚠️ 警告: 未检测到 GALFIT_BIN 环境变量。")
    asyncio.run(main())