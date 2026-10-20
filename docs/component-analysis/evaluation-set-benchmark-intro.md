# 成分分析评测数据集总池介绍

更新日期：2026-10-20。面向组内介绍；规则细节以 `evaluation-set-design.md`（科学权威，`adjudication_rule_version: component-evalset-v2`）为准。

---

## 1. 这个数据集是什么

从单波段（GALFIT）与多波段（GalfitS）共 **1,181 个历史拟合实例**（单波段 985／多波段 196；对应 157 个星系）中，抽取每条轨迹的**单步决策样本**，回答一个问题：

> 给定某一轮拟合结束时可见的全部输入状态，成分分析模块能否输出该状态下正确的下一步动作？

每条样本的形式是 `state_round_i → canonical_action → state_round_j`，其中动作对齐结构化 workflow 的动作契约（`PROPOSE_ADD/REMOVE/REPLACE`、`PROMOTE_SINGLE_SERSIC_TO_DISK`、`REFIT_PARAMETERS`、`CONVERGED`，以及两原子的 `COMPOUND`）。标签不是照抄历史操作：每条转换都经「专家终态方向＋动作前必要性＋动作后有效性＋后续稳定性」四类证据独立裁决，只有 `CORRECT + high`（证据完整一致）的样本才进 benchmark。

## 2. 存放路径

| 内容 | 路径 |
|---|---|
| **冻结 run（现行版本）** | `artifacts/component-analysis-evalset/evalset-20261009T100335Z/` |
| benchmark（核心评测集） | 上述目录下 `evaluation-set-v1.jsonl`（388 条） |
| audit 池 | `evaluation-audit-v1.jsonl`（7,661 条） |
| excluded 池 | `evaluation-excluded-v1.jsonl`（587 条） |
| 溯源 manifest | `evaluation-set-v1-manifest.json`（design sha `ea63d8c2…`、源 inventory sha、冻结时间 2026-10-09T10:12:35Z、code commit、三池/模式/动作统计） |
| 每条样本的允许输入清单 | `evaluation-state-manifests/<sample_id>.json`（含每个输入文件的路径、角色、大小、SHA-256，已过六项泄漏检查） |
| 源数据（严格只读） | `/media/data/galfit_run_history`（单波段）、`/media/data/galfits_run_history`（多波段） |

冻结纪律：manifest 记录 design checksum 与源 inventory checksum，评测跑前逐文件校验输入 sha；冻结后任何修改都需要新版本号＋新 run。

## 3. 三池构成

| 池 | 条数 | 用途 |
|---|---:|---|
| **benchmark** | 388 | 主评测集：唯一决策点、全部 `CORRECT+high` |
| audit | 7,661 | 不计主指标：含语义重复 810 条（同对象同状态同动作的跨批次重复演示）、内容指纹别名 288、REFIT 批量归档 2,575、`CORRECT+medium/low`、`EXPLORATORY`（可作 unsafe 否决集）等 |
| excluded | 587 | `INCONCLUSIVE`：证据缺失或语义不可恢复 |

## 4. benchmark 统计

**按动作 × 模式（388 条）**：

| 动作 | 单波段 | 多波段 | 合计 | 占比 |
|---|---:|---:|---:|---:|
| COMPOUND（两原子组合） | 108 | 15 | 123 | 31.7% |
| PROPOSE_ADD | 99 | 13 | 112 | 28.9% |
| CONVERGED（终止） | 69 | 11 | 80 | 20.6% |
| PROPOSE_REPLACE | 33 | 1 | 34 | 8.8% |
| PROPOSE_REMOVE | 27 | 3 | 30 | 7.7% |
| PROMOTE_SINGLE_SERSIC_TO_DISK | 9 | 0 | 9 | 2.3% |

- COMPOUND 全部为两原子模式（`PROMOTE+ADD` 或 `REPLACE(single_sersic→X)+ADD`，2026-09-16 裁定可入核心集）
- 星系对象：**116 个**（单波段 97／多波段 19）；每对象 1～9 条样本不等，统计检验须以对象为抽样单位

**对象来源**：单波段＝Gadotti/SDSS r 波段 100 个＋JWST F277W 25 个专家标签；多波段＝JWST 32 个 confirmed 终态标签（专家终态只提供成分集合，不提供逐轮动作）。

## 5. 每条 benchmark 样本包含什么

| 字段组 | 内容 | 用途 |
|---|---|---|
| `state_round_id`＋`input_cutoff`＋`state_manifest` | 被测输入：该轮 feedme/lyric、拟合输出、result FITS、comparison PNG、sigma/mask/PSF 及此前轮次文件（逐文件 sha256） | **喂给被测模块**；决策 md、working note、最终报告、专家标签等一律不在内（防泄漏） |
| `canonical_action` | 标准答案：动作类型＋目标成分（COMPOUND 含原子分解） | 评分 |
| `source_components`／`expert_final_components` | 当前轮成分集合／专家终态 | 标签侧审计 |
| `action_verdict`／`confidence`／`verdict_reason_codes` | 裁决证据（全部 CORRECT+high） | 审计 |
| `mode`／`object_id` | 分组与 bootstrap 抽样单位 | 统计 |

## 6. 怎么评

统一指标（各方法一把尺子，三列可比）：`action_type_accuracy`、`component_accuracy`（ADD/REMOVE/REPLACE 目标成分）、`converged_precision/recall`、`unsafe_action_rate`（否决集＝audit 池同对象同状态的 EXPLORATORY 动作）；单/多波段分别报告，95% CI 以对象级整群 bootstrap 计算。评测框架分「样本供给层—被测适配层—评分层」，接入新方法只需写适配层（详见 `baseline-evaluation-plan.md`）。

## 7. 旧方法（B0）基线结果（2026-10-13，供参照）

被测＝历史轨迹的逐轮决策者 `component_analysis` 工具（388 条全量重放，custom_instructions 按防泄漏不传）：

| 指标 | 结果 |
|---|---|
| action_type_accuracy | **42.9%**（CI95 [39.0%, 47.0%]） |
| component_accuracy | 37.3% |
| converged_recall | 10/79（12.7%） |
| unsafe_action_rate | 11.9% |

失败模式（221 条错配归因）：主体不是「判断错误」，而是旧方法输出与动作契约的系统性错位——**无终止信号**（67 条）与**用自然语言叙述做成分角色指派而非结构原子**（69 条）；另有删除保守（28 条）与成分级错误（110 条）。这两类正是结构化 workflow action contract 的改进靶点。报告：`artifacts/component-analysis-evalset/evalset-baseline-b0-20261010T014917Z/baseline-summary.md`。
