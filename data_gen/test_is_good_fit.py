import os
import json

from reward import is_good_fit


def main():
    residual_image_path = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/Plate0556_MJD51991_Fiber236_r/archives/20260528T142638.25e7b2ea/galfit_comparison_cutoff.png"
    # summary_md_path = "/media/zhongling/wyh/GalDecomp_Gen/test_image/Archive/Plate0391_MJD51782_Fiber072_r/archives/20260519T105830.ecf010ea/galfit_summary.md"

    result = is_good_fit(
        residual_image_path=residual_image_path,
        param=True,
        summary_md_path=None,
        model_name="gemini-3.1-pro-preview",
        api_key="sk-orCNeVDDaLy7zNfXvDYx9FX7z5uTdUkbWBJjFWeFDarSysSq",
        temperature=0.7,
        max_tokens=2048,
        timeout=300,
        confidence_threshold=0.6,
        max_retries=3,
        retry_sleep=10,
    )

    print("\n========== is_good_fit result ==========")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()