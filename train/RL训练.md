# RL训练方案

> 当前决策：使用v11 rule-based reward；先完成单步GRPO pilot和on-policy复测，再决定是否在full GRPO前加入Direct Preference Optimization。

## 1. 训练路线

| 阶段 | 输入 | 目标 | 通过条件 |
|---|---|---|---|
| Reward适配 | v11 raw及执行状态 | 生成粗等级、margin和mask | 顺序稳定、失败归因正确 |
| 离线replay | 历史parent-child与E1–E6多候选 | 检查分布、有效组率、pairwise | 无口径回归 |
| Rollout-only | SFT模型，每个parent采样多候选 | 测真实on-policy分布，不更新参数 | 有足够mixed groups |
| 单步GRPO pilot | 固定parent state | 验证reward可学且不被利用 | 独立VLM指标改善、执行率非劣 |
| 决策点 | pilot新数据 | 选择DPO或full/multi-turn GRPO | 见第4节 |

## 2. v11到GRPO Reward

v11定义为“v8-equivalent raw公式 + Precision-first F1（precision floor=0.85）阈值策略”，锁定阈值为`0.05139489475137804`。

| 状态 | coarse | 是否训练 | 处理 |
|---|---:|---:|---|
| 评测器/基础设施失败 | — | 否 | sample mask=0 |
| 策略输出非法或导致执行失败 | -1 | 是 | 不计算margin |
| 可执行但`raw≤threshold` | 0 | 是 | non-improvement |
| 可执行且`raw>threshold` | 1 | 是 | improvement |

```python
margin = clip((raw - threshold) / scale, -1, 1)
reward = coarse + lambda_margin * margin
```

`scale`只在固定val上用IQR/MAD校准；`lambda_margin`先试0.2，且保持`<0.5`。同一`(parent, step)`至少有两个有效候选且包含两个不同coarse等级才训练；全accepted或全rejected组只记录、不更新。

UI-S1首阶段使用`algorithm.uis1.mode=mean_norm`，避免`mean_std_norm`把小margin重新放大。UI-S1配置中的DAPO是动态组过滤，不是Direct Preference Optimization。

## 3. on-policy逐步复测

| 步骤 | 做法 | 必报指标 |
|---|---|---|
| G0：离线replay | 对历史记录重算raw/coarse/margin/group gate | 粗等级分布、有效组率、失败率、pairwise |
| G1：rollout-only | SFT checkpoint按parent分组采样，执行GALFIT但不更新 | mixed/all-positive/all-negative组比例、动作分布、耗时 |
| G2：VLM抽检 | 分层抽取阈值附近、高分rejected、delete和失败样本 | 二分类、same-parent pairwise、高分误判率 |
| G3：短程pilot | 单步、短训练、固定间隔保存checkpoint | VLM accepted rate、执行率、KL、entropy、组有效率 |
| G4：训练后复测 | 在固定physical-id test上重复G1–G2 | 相对SFT变化及cluster bootstrap CI |

测试集不调阈值；如发生分布漂移，只能在新的on-policy val上校准并锁定后评测test。SSR与训练reward同源，只作诊断；独立VLM accepted rate是第一阶段主指标。

## 4. DPO决策

| pilot现象 | 决策 |
|---|---|
| VLM accepted rate提升、执行率非劣、有效组充足 | 先扩大GRPO，DPO后置 |
| rejected多且已有可靠same-parent正负对 | full GRPO前加入DPO |
| delete错误集中 | 定向构造delete偏好对后DPO |
| KL/动作分布漂移过快 | 先DPO稳定策略或收紧GRPO |
| 同质组过多、有效组不足 | 增加采样/温度或用VLM补偏好；不取消GroupGate |

## 5. 当前工程

| 文件 | 变更 |
|---|---|
| `eval/reward_for_grpo.py` | v11失败门控、margin校准、同-parent组门控 |
| `eval/validate_grpo_reward.py` | 历史/on-policy JSONL replay，输出有效组率与可选VLM alignment |
| `eval/prepare_grpo_replay.py` | 将现有trajectory转换为统一replay JSONL |
| `tests/test_reward_for_grpo.py` | 阈值、失败归因、顺序保持、同质/混合组测试 |
| `eval/评测体系设计.md` | 保留完整评测依据、v1–v11历史和GRPO验收规则 |

下一步：把现有轨迹和SFT rollout转换为replay JSONL，然后接入UI-S1的rollout数据结构；完整多轮训练前另行验证discounted return和`gamma`，不直接沿用Android配置。
