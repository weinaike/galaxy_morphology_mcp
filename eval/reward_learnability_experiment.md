# Qwen3-VL reward可学习性上界实验

## 目标

验证确定性的v11 raw reward对Qwen3-VL是否可学习：把同一状态下实际执行得到的最高reward动作监督给模型后，重新采样的期望reward能否提高。最高reward动作是reward蒸馏目标，不称为GT。

## 主线与诊断支线

```text
Qwen3-VL-8B-Instruct
        |
        +-- 相同clean数据Full-SFT --> Qwen3-Full-SFT
                                      |
                                      +-- 固定parents采样/执行（before）
                                      |       |
                                      |       +-- 每组选择最高raw-reward动作
                                      |               |
                                      |               +-- reward-best继续Full-SFT
                                      |                       |
                                      |                       +-- 同配置采样/执行（after）
                                      |
                                      +-- 单轮GRPO（独立分支，不使用reward-best权重）
```

单轮链路验证通过，并且STOP监督和terminal reward完成后，再进入多轮GRPO。Qwen2.5的模型和结果保留为历史基线，不再作为主线，也不删除。

## 固定项

- Full-SFT数据、physical-id划分、prompt和图像像素上限与旧实验一致。
- reward固定为v11 raw reward，正例阈值为`0.05139489475137804`。
- SFT前后使用同一parents、K、temperature、top-p、top-k和seed。
- evaluator failure排除；格式错误和模型导致的GALFIT失败按`-1`计入。

## Pilot规模和判据

先用128个parent、每个8个候选。按physical-id将选优样本划分为90% train和10% held-out val。SFT后在全部128个parent上重新采样，分别报告：

- `train`：seen-state可学习性上界；
- `val`：unseen-state泛化；
- `all`：总体结果。

主要判据是failure-adjusted candidate mean的配对变化及按parent bootstrap 95% CI；同时记录best-of-K、正例率、成功率和success-only raw mean。

## 从before rollouts构造SFT数据

```bash
cd /mnt/si00237244jv/default/GalDecomp_Gen

python3 -m eval.build_reward_best_sft \
  --parents eval/reward_learnability_qwen3vl_v1/parents.jsonl \
  --rollouts eval/reward_learnability_qwen3vl_v1/before_rollouts.jsonl \
  --output-dir train/llamafactory/data_reward_best_qwen3vl_v1 \
  --min-successful-candidates 2 \
  --val-ratio 0.1 \
  --seed 42 \
  --image-root-from /media/zhongling/wyh/GalDecomp_Gen \
  --image-root-to /mnt/si00237244jv/default/GalDecomp_Gen
```

该命令生成train/val JSONL、选优审计文件、报告和独立的`dataset_info.json`。

## SFT前后配对统计

```bash
python3 -m eval.compare_reward_learnability \
  --before eval/reward_learnability_qwen3vl_v1/before_rollouts.jsonl \
  --after eval/reward_learnability_qwen3vl_v1/after_rollouts.jsonl \
  --selections train/llamafactory/data_reward_best_qwen3vl_v1/reward_best_selections.jsonl \
  --output eval/reward_learnability_qwen3vl_v1/comparison.json
```

如果seen-state仍无提升，优先检查模型输入、视觉分辨率以及是否需要解冻视觉塔；如果seen提升但val不提升，说明能记忆reward目标但缺少泛化；如果两者均提升，reward对该模型可学习，可进入正式GRPO。
