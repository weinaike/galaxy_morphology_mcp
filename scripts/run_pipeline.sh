#!/bin/bash
set -e

# 设置输出目录
FEEDME_NAME="perturbed_action_c_ii_feedme_simplify"

# [关键新增] 无论你在哪执行这个脚本，强制切换到项目根目录
cd "$(dirname "$0")/.."

# 日志记录配置
mkdir -p logs
LOG_FILE="logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "================================================="
echo "🚀 开始执行星系形态拟合自动化全流程流水线"
echo "📄 本次运行日志将实时保存至: ${LOG_FILE}"
echo "================================================="

eval "$(conda shell.bash hook)"
conda activate mcp

# =======================================================
# 🛡️ [智能跳过 1] 自动检测并保护扰动文件 (取代手动注释)
# =======================================================
PERTURB_DIR="data/perturbed/${FEEDME_NAME}"

# 【修复点】用 find 替代 ls，解决海量文件导致的通配符溢出问题
if [ -d "$PERTURB_DIR" ] && [ -n "$(find "$PERTURB_DIR" -maxdepth 1 -name '*.feedme' | head -n 1)" ]; then
    echo "🛡️  [跳过阶段 1] 检测到 ${PERTURB_DIR} 已有现成的扰动文件！"
    echo "⏭️  自动跳过生成步骤，保护现有参数不被重置篡改..."
else
    echo ">>> [阶段 1/3] 开始生成全量扰动数据..."
    python scripts/perturb_2_simplify.py --num_variants 1 --feedme_name ${FEEDME_NAME}
    echo ">>> ✅ 扰动数据生成完毕！"
fi

# STEPS=(10 20 40 60 80 100)
STEPS=(0)
echo ">>> [阶段 2 & 3] 开始阶梯式迭代拟合与数据集清洗..."

for step in "${STEPS[@]}"; do
    echo "-------------------------------------------------"
    echo "🔥 当前正在处理迭代阶梯: Step = ${step}"
    
    # =======================================================
    # 🛡️ [智能跳过 2] 宏观断点续跑：如果这个 Step 彻底搞定了，直接跳过！
    # =======================================================
    # 请确认你的最终 CSV 是叫这个名字，如果不叫 dataset_all.csv，请微调下面这行
    FINAL_CSV="data/dataset/${FEEDME_NAME}/dataset_all_step_${step}.csv" 
    if [ -f "$FINAL_CSV" ]; then
        echo "⏭️  [宏观跳过] 检测到 Step ${step} 的最终数据集已生成，本阶梯已彻底完工！"
        continue
    fi
    
    # 1. 执行并发拟合 (内部带有微观断点续跑)
    echo "   -> 启动 GALFIT 跑批 (Step=${step})..."
    python scripts/run_perturb_galfit.py --step ${step} --feedme_name ${FEEDME_NAME}
    
    # 2. 图像质检与划分
    echo "   -> 启动 SSIM 图像质检与数据集生成 (Step=${step})..."
    python scripts/split_train_test.py --step ${step} --feedme_name ${FEEDME_NAME}

    # 3. 战后清场
    echo "   -> 🧹 正在清理底层日志垃圾，释放磁盘空间..."
    rm -rf galfit_tmp/archives/
    rm -f galfit_tmp/*.fits  # 顺手把临时产生的废弃 fits 也扬了
    
    echo "✅ Step ${step} 流水线执行完毕！结果已保存至 data/dataset/step_${step}/"
done

echo "-------------------------------------------------"
echo "🧬 正在将所有阶梯的数据进行大一统合并..."
python scripts/merge_datasets.py --feedme_name ${FEEDME_NAME}

echo "================================================="
echo "🎉 全流程自动化处理圆满完成！"
echo "================================================="