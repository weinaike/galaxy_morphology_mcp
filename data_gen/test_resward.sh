# 单个测试是不是比上一步更好 
cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260607T142144.3f8a0bd1/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview_0/SDSS_gband_Plate0281_MJD51614_Fiber173_g/archives/20260607T142629.25f396d2/node_1_p0_v11_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test1.json



# 批量测试（品松的给的是不是比上一步更好，默认最后一个时间戳是拟合成功的结果）
# cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

python test_reward_batch.py \
  --root_dir /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done \
  --test_reward_py /media/zhongling/wyh/GalDecomp_Gen/data_gen/test_reward.py \
  --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/test_reward_batch_results_cutoff.json