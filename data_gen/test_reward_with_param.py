import os
from reward import calculate_reward_model_with_param_new
import json


def main():
    prev_residual_image_path = "/media/zhongling/wyh/GalDecomp_Gen/output/vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0592_MJD52025_Fiber240_g/archives/20260609T194845.a27945e4/node_0_root_comparison.png"
    next_residual_image_path = "/media/zhongling/wyh/GalDecomp_Gen/output/vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0592_MJD52025_Fiber240_g/archives/20260609T194931.03df452d/node_1_p0_v3_comparison.png"
    prev_summary_path = "/media/zhongling/wyh/GalDecomp_Gen/output/vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0592_MJD52025_Fiber240_g/archives/20260609T194845.a27945e4/node_0_root_summary.md"
    new_summary_path = "/media/zhongling/wyh/GalDecomp_Gen/output/vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0592_MJD52025_Fiber240_g/archives/20260609T194931.03df452d/node_1_p0_v3_summary.md"

    result = calculate_reward_model_with_param_new(
        prev_residual_image_path=prev_residual_image_path,
        next_residual_image_path=next_residual_image_path,
        prev_summary_path=prev_summary_path,
        new_summary_path=new_summary_path,

        # 可选参数
        model_name="gemini-3.1-pro-preview",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.1,
        max_tokens=2560,
        timeout=300,
        confidence_threshold=0.6,
    )

    print("=" * 50)
    print("Result:")
    print(json.dumps(result, indent=4, ensure_ascii=False))
    print("=" * 50)


if __name__ == "__main__":
    main()
    
    
    
    
    
