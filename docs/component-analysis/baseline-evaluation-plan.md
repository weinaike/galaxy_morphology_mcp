# 成分分析基线评测模块方案（B0 讨论稿）

状态：**待小鱼儿确认，未执行**。
更新日期：2026-09-22（按小鱼儿裁定分两部分：B0 先行、全量测；B1/B2 延后）。
数据基础：冻结评测集 `artifacts/component-analysis-evalset/evalset-20260921T055138Z`（benchmark 388 条：单波段 345／多波段 43；audit 7,661 作否决集与错误分析）。

---

# Part 1（本轮实施）：B0 旧流程基线，全量 388

## B0.1 被测对象

历史拟合轨迹的逐轮决策者＝**`component_analysis` MCP 工具**（legacy prompt：`residual_analysis_message.md`＋`component_specification_galfit(_galfits).md`；输入＝当轮 comparison PNG＋summary＋mode；输出＝含「本次调整决策」的分析 Markdown）。B0＝把该工具在冻结样本的 state 轮上重放，与 canonical action 对比。这是与冻结集同源的生成方式，是最干净的基线。

明确不测（v1 边界）：agent 的多轮编排（working note 维护、奥卡姆过滤、落锁流程）——B0 只评「给定一轮状态，下一步动作决策」这一单步环节，与评测集的任务定义一致。

## B0.2 输入契约与防泄漏

每个样本的输入（全部来自 state manifest，≤ `input_cutoff`）：

| 输入 | 单波段 | 多波段 |
|---|---|---|
| image_file | 轮目录 `*_comparison.png` | 轮目录 `all_bands_comparison.png` |
| summary_file | 轮目录 `galfit_summary.md`（无则 `galfit.NN`，Chi²/nu 来源） | 轮目录 `*.gssummary` |
| mode | `single-band` | `multi-band` |
| custom_instructions | **v1 不传**（避免构造时的泄漏风险；working note 是追加文件不能整体入 state，按截止点重建属后续可选增强，须另行设计） | 同左 |

跑前逐样本校验 manifest 内文件 sha256 与冻结时一致（数据根被外部改动则拒跑）。标签侧字段（expert_final_components／canonical_action／verdict／决策 md 本身）全程不进输入。

## B0.3 调用方式

复用 `workflow_batch_runner` 的 MCP stdio client 基建连 galmcp server 调 `component_analysis`；每次调用记录 raw response、model_id、prompt 版本、attempt 数、耗时（provider metadata 契约已具备）。失败处理：`PARSE_FAILED`／`TIMEOUT`／`REFUSED` 重试 ≤2 次，仍失败记 `NO_RESPONSE`，单列 `no_response_rate`，该样本不计入其余指标分母（报告中标明）。

全量 388 条预计 VLM 调用 388＋重试次数（decode-bound，预计数小时）；runner 断点续作（逐样本落盘后 checkpoint），后台分批跑。

## B0.4 动作解析器（主要新工作）

「本次调整决策」Markdown → canonical action 契约的确定性解析：

1. **取第一步**：legacy 决策常写多步阶梯总目标（对应其「一次只增减一个成分」守则）——解析取**立即第一步**动作，与 canonical 的立即后轮口径一致；
2. 动作识别（复用裁决层已验证的模式）：成分参数表／`成分 N (Label)`／动作关键词（新增／增加→ADD；删除／移除→REMOVE；替换／改标／回退→REPLACE；sersic→expdisk 原位→PROMOTE；无需进一步调整／保持→CONVERGED；仅参数／初值／约束调整→REFIT）；
3. 成分映射：Disk/Bulge/Bar/Nucleus→agn/Fourier→fourier_m1/Companion/Lens/SingleSersic（中英同义词）；
4. 无法唯一解析→`PARSE_TO_ACTION_FAILED`，单列计数（这是旧方法「输出不可契约化」的实测率，本身是重要指标）；
5. 解析器为纯函数、带单元测试（从冻结集的历史决策 md 取 ≥20 条做回归 fixture）。

## B0.4b 双口径定义（2026-10-14 补记）

工具的决策 md 同时包含「预期最终成分组成」（整体成分预测）与「本次调整物理目标／具体调整内容」（立即动作描述），解析器从同一份 md 导出两个动作读数：

- **主口径 `action`**：来自目标成分行——目标集合 × 当前成分集合差集（1 加＝ADD／1 减＝REMOVE／一加一减＝REPLACE／single_sersic→含 disk＝PROMOTE／多原子＝COMPOUND／无差＝REFIT）；优先级覆盖：终结语句＞本轮明示范围（「本轮暂不…先…」）＞显式改标动词（「原 X 转换为 Y」）＞目标差集。
- **第一步口径 `action_first_step`**：来自「具体调整内容」的显式单一原子；无显式首步时按约定取序 **PROMOTE＞REMOVE/REPLACE＞ADD** 取第一原子（此约定是解析器约定、非 md 自述顺序，解读时须注意）；主口径为非 COMPOUND 时两口径恒相同。
- 报告双口径差＝「输出粒度（只给阶梯首步）与动作契约（立即完整执行）不匹配」的量化。

## B0.5 评分器

| 指标 | 定义 |
|---|---|
| `action_type_accuracy` | 预测动作类型＝canonical（COMPOUND 需原子集合一致） |
| `component_accuracy` | ADD/REMOVE/REPLACE/PROMOTE 的目标成分一致 |
| `converged_precision`／`recall` | 80 条 CONVERGED 样本上的查准／查全 |
| `unsafe_action_rate` | 预测命中否决集＝audit 池同 `(mode, object_id, source_components)` 的 EXPLORATORY 动作 |
| `parse_to_action_rate` | 决策 md 成功映射为动作契约的比例 |
| `no_response_rate` | VLM 重试后仍无有效输出 |

统计：单／多波段分别报告；动作分层；bootstrap 以 `object_id` 为单位（1,000 次）。B0 的对照真值全部来自冻结集标签侧，评分器只读标签。

## B0.6 产物

`artifacts/component-analysis-evalset/evalset-baseline-b0-<ts>/`：

- `baseline-report.json`：逐样本（sample_id、输入文件引用、raw response 路径、解析动作、canonical、各命中布尔、attempt/耗时）；
- `baseline-summary.md`：指标表＋分层统计＋失败/解析失败样本清单＋caveat（custom_instructions 未传、VLM 非确定性）；
- `baseline-run-config.json`：model_id、prompt 版本、冻结集 checksum、解析器版本——VLM 非确定，重复性门只作用于解析器与评分器（确定性双跑一致），VLM 原文全程留档。

## B0.7 验收门

1. manifest sha 全量校验通过；
2. 解析器对 ≥20 条历史决策 md 的 fixture 回归通过；3 个样本人工核验「md→动作」解析正确；
3. 解析器＋评分器双跑逐条一致；
4. 指标表完整（单／多波段 × 动作 × 主指标），`NO_RESPONSE`／`PARSE_TO_ACTION_FAILED` 明细可查。

## B0.8 实施顺序

1. 解析器＋fixture 测试（半天，可先行）；
2. runner（MCP client＋manifest 校验＋checkpoint）（半天）；
3. 全量后台跑＋断点续作；
4. 评分与报告（解析器同日完成）。
评测中发现的旧方法缺陷只记录不修（保持基线纯净）。

---

# Part 2（延后）：B1/B2 新 workflow 对照与评测框架分层

## 框架分层（2026-10-10 小鱼儿确认）

| 层 | 内容 | 不同方法间 |
|---|---|---|
| 样本供给层 | 冻结集＋state manifest＋sha 校验＋防泄漏（input_cutoff 截断） | 完全复用 |
| 被测 adapter 层 | 把某方法输出变成统一动作契约 | 每方法一个：B0＝工具调用＋md 解析；B1＝证据适配器＋规则层；B2＝三层链 |
| 评分层 | 统一评分器＋对象级 bootstrap＋单/多波段分层 | 完全复用 |

- B1/B2 启动时把 `b0_runner` 两端（`_manifest`/`_inputs_for`/`_verify_sha`/`score_b0`）抽成共享 harness，B0 adapter 原样保留。
- 任何方法封装成「(state 输入文件) → 动作契约」即可接入；输出自由文本的方法需配解析器，解析失败率计入该方法辅助指标。
- 方法需要 state manifest 之外的输入（如 SED）＝评测集 v1 边界问题（Image-only），先扩评测集再接入。

## 指标口径（主指标对所有方法一致，三列可比）

主指标一把尺子：`action_type_accuracy`（双口径）、`component_accuracy`、`converged_precision/recall`、`unsafe_action_rate`＋对象级 bootstrap＋单/多波段分层。各方法追加自己的辅助指标、**单列不进主表**：B0＝`parse_to_action_rate`/`no_response_rate`；B1＝`no_decision_rate`（规则层 INCONCLUSIVE 比例）；B2＝VLM 降级率。

## B1/B2 前置与内容

前置条件：当前分支 workflow 的遗留问题（N23～N27 口径）修复完成、真实拟合 canary 通过后再启动。B1 numeric-only（全量 388，需新建单波段证据适配器——`artifact_adapter` 目前只支持多波段 lyric）；B2 VLM-enabled 三层链对照。单波段统计口径（小鱼儿 2026-09-22 修正）：GALFIT 的 `*_summary.md` **含 BIC**——2026-05-26 之前为 2D BIC（数值大）、之后改为 1D BIC（数值小），两者不可跨切换期比较，且切换过程中部分星系拟合结果可能无 BIC；B1 适配器解析 summary md 时按「有则记、无则 None、跨期比较须分版本」处理。B0 不受影响：summary md 原样作为工具输入，由工具自行消费。
