import os
import json
import argparse
from datetime import datetime

from reward import calculate_reward_model


def run_test(
    prev_image_path: str,
    next_image_path: str,
    output_json_path: str,
    model_name: str = "gpt-5.5",
    api_key: str = None,
):
    """
    Test calculate_reward_model with two residual images and save result to JSON.
    """

    if not os.path.exists(prev_image_path):
        raise FileNotFoundError(f"Previous image not found: {prev_image_path}")

    if not os.path.exists(next_image_path):
        raise FileNotFoundError(f"Next image not found: {next_image_path}")

    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)

    # 1. 这里不要再叫 improvement，因为现在 calculate_reward_model 返回的是 dict
    if api_key is None:
        reward_result = calculate_reward_model(
            prev_residual_image_path=prev_image_path,
            next_residual_image_path=next_image_path,
            model_name=model_name,
        )
    else:
        reward_result = calculate_reward_model(
            prev_residual_image_path=prev_image_path,
            next_residual_image_path=next_image_path,
            model_name=model_name,
            api_key=api_key,
        )

    # 2. 兼容两种情况：
    # 情况 A：calculate_reward_model 返回 dict，里面有 final_improvement / improvement / usage
    # 情况 B：calculate_reward_model 仍然返回 int，例如 0 或 1
    if isinstance(reward_result, dict):
        improvement = reward_result.get(
            "final_improvement",
            reward_result.get("improvement", 0)
        )
        confidence = reward_result.get("confidence", 0.0)
        reason = reward_result.get("reason", "")
        usage = reward_result.get("usage", {})
    else:
        improvement = reward_result
        confidence = None
        reason = ""
        usage = {}

    improvement = int(improvement)

    result = {
    "prev_image_path": prev_image_path,
    "next_image_path": next_image_path,
    "model_name": model_name,

    "improvement": improvement,
    "reward_judgement": "improved" if improvement == 1 else "not_improved",

    "confidence": confidence,
    "reason": reason,

    "prompt_tokens": usage.get("prompt_tokens", 0),
    "completion_tokens": usage.get("completion_tokens", 0),
    "total_tokens": usage.get("total_tokens", 0),

    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[Done] Result saved to: {output_json_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test VLM-based GALFIT residual improvement reward."
    )

    parser.add_argument(
        "--prev_image",
        type=str,
        required=True,
        help="Path to previous-step residual image.",
    )

    parser.add_argument(
        "--next_image",
        type=str,
        required=True,
        help="Path to next-step residual image.",
    )

    parser.add_argument(
        "--output_json",
        type=str,
        default="/media/zhongling/wyh/GalDecomp_Gen/data_gen/test_reward_result.json",
        help="Path to save output JSON.",
    )

    parser.add_argument(
        "--model_name",
        type=str,
        default="gpt-5.5",
        help="VLM model name.",
    )

    parser.add_argument(
        "--api_key",
        type=str,
        default="sk-orCNeVDDaLy7zNfXvDYx9FX7z5uTdUkbWBJjFWeFDarSysSq",
        help="API key. If not provided, use default api_key in reward.py.",
    )

    args = parser.parse_args()

    run_test(
        prev_image_path=args.prev_image,
        next_image_path=args.next_image,
        output_json_path=args.output_json,
        model_name=args.model_name,
        api_key=args.api_key,
    )