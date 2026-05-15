import asyncio
import os
import sys

# 添加项目路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from scripts.utils.actions import perform_action_add, perform_action_delete_random

async def test_actions():
    # 示例参数（请根据你的实际数据调整）
    galaxy_name = "test_galaxy_001"
    step_i = 1
    base_out_dir = "/mnt/data/wyh/galaxy_morphology_mcp/data/perturbed/test_action_a/tmp"  # 测试输出目录
    os.makedirs(base_out_dir, exist_ok=True)

    # 假设你有一个示例 feedme 文件路径（替换为真实路径）
    current_feedme = "/mnt/data/wyh/galaxy_morphology_mcp/data/perturbed/test_action_a/goodsn_4821_v5.feedme"  # 例如："/mnt/data/wyh/galaxy_morphology_mcp/data/example.feedme"

    if not os.path.exists(current_feedme):
        print(f"❌ 示例 feedme 文件不存在: {current_feedme}")
        print("请提供一个有效的 feedme 文件路径。")
        return

    print("🚀 测试增加成分 (perform_action_add)...")
    add_variants = await perform_action_add(current_feedme, galaxy_name, step_i, base_out_dir)
    print(f"✅ 增加成分结果: {add_variants}")

    print("🚀 测试删减成分 (perform_action_delete_random)...")
    delete_variants = await perform_action_delete_random(current_feedme, galaxy_name, step_i, base_out_dir)
    print(f"✅ 删减成分结果: {delete_variants}")

    print("🎉 测试完成！检查输出目录以验证文件生成。")

if __name__ == "__main__":
    asyncio.run(test_actions())