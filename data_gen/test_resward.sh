# 单个测试是不是比上一步更好 
cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152140.4fc093e9/node_0_root_comparison_cutoff.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/output/expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview/SDSS_gband_Plate0398_MJD51789_Fiber337_g/archives/20260607T152159.eae44b77/node_1_p0_v15_comparison_cutoff.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test.json


#批量测试（品松的给的是不是比上一步更好，默认最后一个时间戳是拟合成功的结果）
# cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

python test_reward_batch.py \
  --root_dir /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/three-pannels-figure \
  --test_reward_py /media/zhongling/wyh/GalDecomp_Gen/data_gen/test_reward_batch.py \
  --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/test_reward_batch_results_threefigure.json