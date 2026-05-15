import asyncio
import os
import sys
import json

# 退回到 galaxy_morphology_mcp 根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# 导入我们刚刚写的 Action C
from scripts.utils.actions import perform_action_perturb

async def main():
    # ================= 1. 准备测试数据 =================
    # 【请修改这里】找一个你本地真实存在的，能正常运行的 .feedme 文件路径
    test_feedme_path = os.path.join(BASE_DIR, "data", "galfit_sample_0204", "COS", "cosmos_38", "cosmos_38.feedme")
    
    if not os.path.exists(test_feedme_path):
        print(f"❌ 找不到测试用的 feedme 文件: {test_feedme_path}")
        print("请把路径修改为你本地真实存在的 .feedme！")
        return

    # 模拟主调度器传进来的参数
    galaxy_name = "test_galaxy_001"
    step_i = 1
    
    # 建一个专门的测试沙盒目录
    test_sandbox_dir = os.path.join(BASE_DIR, "data", "test_action_sandbox")
    os.makedirs(test_sandbox_dir, exist_ok=True)

    print(f"🚀 开始测试 Action C (参数扰动)...")
    print(f"📂 原始文件: {test_feedme_path}")
    print(f"📂 测试沙盒: {test_sandbox_dir}")
    print("-" * 50)

    # ================= 2. 执行 Action C =================
    # 设定生成 4 个变体
    results = await perform_action_perturb(
        current_feedme=test_feedme_path,
        galaxy_name=galaxy_name,
        step_i=step_i,
        base_out_dir=test_sandbox_dir,
        num_variants=100,
        max_iter=100
    )

    # ================= 3. 打印结果 =================
    print("-" * 50)
    if not results:
        print("⚠️ 没有任何变体存活。可能是 GALFIT 报错了，或者全被 SSIM 过滤掉了。")
    else:
        print(f"✅ 成功产出 {len(results)} 个有效变体！返回给调度器的 JSON 格式如下：")
        print(json.dumps(results, indent=4, ensure_ascii=False))
        
    print("-" * 50)
    print("你可以去看看沙盒目录，应该只有上面这些存活的变体文件夹留下来了。")
    print(f"沙盒路径: {test_sandbox_dir}")

if __name__ == "__main__":
    # 如果你的系统需要环境变量，请在这里确保它被加载
    # from dotenv import load_dotenv
    # load_dotenv()
    
    asyncio.run(main())