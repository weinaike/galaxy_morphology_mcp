# 单个测试是不是比上一步更好 
cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260607T142144.3f8a0bd1/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260607T142629.25f396d2/node_1_p0_v11_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test1.json

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260607T142144.3f8a0bd1/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260607T142642.fa4028ca/node_1_p0_v12_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test2.json

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0395_MJD51783_Fiber499_g/archives/20260607T151555.0b955855/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0395_MJD51783_Fiber499_g/archives/20260607T151611.e3da520b/node_1_p0_v11_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test3.json

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152140.4fc093e9/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152141.b350a0db/node_1_p0_v0_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test4.json

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152140.4fc093e9/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152152.ad636ece/node_1_p0_v11_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test5.json

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152140.4fc093e9/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152154.0ea3fd07/node_1_p0_v12_comparison_cutoff.png  \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test6.json

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152140.4fc093e9/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152159.eae44b77/node_1_p0_v15_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test7.json
python test_reward.py \
  --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260608T165556.3f8a0bd1/node_0_root_comparison_cutoff.png \
  --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260608T165620.4018747f/node_1_p0_v5_comparison_cutoff.png \
  --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test.json

# 批量测试（品松的给的是不是比上一步更好，默认最后一个时间戳是拟合成功的结果）
# cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

# python test_reward_batch.py \
#   --root_dir /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done \
#   --test_reward_py /media/zhongling/wyh/GalDecomp_Gen/data_gen/test_reward.py \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/test_reward_batch_results_threefigure.json