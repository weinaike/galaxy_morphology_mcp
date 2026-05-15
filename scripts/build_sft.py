import os
import json
import argparse
from tqdm import tqdm

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--traj_dir", type=str, required=True)
    parser.add_argument("--output_jsonl", type=str, required=True)
    args = parser.parse_args()

    all_sft_entries = []

    # 递归扫描所有的 *_sft.json 文件
    for root, _, files in os.walk(args.traj_dir):
        for f in files:
            if f.endswith("_sft.json"):
                with open(os.path.join(root, f), 'r') as jf:
                    data = json.load(jf)
                
                # 直接将事先准备好的 pair 转换为 ShareGPT 格式
                for pair in data.get("sft_pairs", []):
                    entry = {
                        "images": [pair["input_image"]], # 绝对路径，无拷贝
                        "conversations": [
                            {
                                "from": "human", 
                                "value": f"<image>\nTask: Analyze the residual image and output the next optimization action in JSON.\n\nContext:\n{pair['input_text']}"
                            },
                            {
                                "from": "gpt", 
                                "value": json.dumps(pair["output_action"], ensure_ascii=False)
                            }
                        ]
                    }
                    all_sft_entries.append(entry)

    # 导出
    os.makedirs(os.path.dirname(args.output_jsonl), exist_ok=True)
    with open(args.output_jsonl, 'w', encoding='utf-8') as f:
        for entry in tqdm(all_sft_entries, desc="Exporting JSONL"):
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"✅ 成功产出 {len(all_sft_entries)} 条多模态 SFT 训练数据！")
    print(f"📂 存储路径: {args.output_jsonl}")

if __name__ == "__main__":
    main()