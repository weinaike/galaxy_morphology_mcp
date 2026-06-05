#单个测试是不是比上一步更好 
# cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

# python test_reward.py \
#   --prev_image /media/zhongling/wyh/GalDecomp_Gen/test_image/limin_gadotti_done/Plate0300_MJD51943_Fiber581_r/archives/20260601T173849.c270e1b9/galfit_comparison.png \
#   --next_image /media/zhongling/wyh/GalDecomp_Gen/test_image/limin_gadotti_done/Plate0300_MJD51943_Fiber581_r/archives/20260601T192555.dde351b0/galfit_comparison.png \
#   --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/test.json


#批量测试（品松的给的是不是比上一步更好，默认最后一个时间戳是拟合成功的结果）
cd /media/zhongling/wyh/GalDecomp_Gen/data_gen

python test_reward_batch.py \
  --root_dir /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done \
  --test_reward_py /media/zhongling/wyh/GalDecomp_Gen/data_gen/test_reward.py \
  --output_json /media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/done_pinsong_reward_results.json