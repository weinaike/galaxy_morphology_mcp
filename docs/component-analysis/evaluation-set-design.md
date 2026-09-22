# 成分分析评测集设计（v1）

> 更新日期：2026-09-14。
> 状态：数据源完成首轮只读盘点；micro-pilot 已抽取并人工审阅，正式评测集尚未抽取。
> `adjudication_rule_version`：`component-evalset-v2`（2026-09-14 升版：冻结 `PROMOTE`／`REPLACE` 分界、多波段未分类单 Sersic 记名 `unclassified_sersic`、复合转换按参数块血缘指派原子动作；2026-09-15 增补：`edge_on_disk` 终态对象与无中间状态复合动作均不进核心集、归 audit 池；单波段 `single_sersic -> disk(expdisk)` 原位转换为 `PROMOTE`；Gadotti 标签读法按 `MType` 规则执行；2026-09-16 增补：单波段 sersic 拟合 `n≈0.5`（固定或自由）识别为 `bar`，其余多成分 sersic 记 `bulge`；`PROMOTE` 的动作类型判定不依赖专家标签——elliptical 对象的 `single_sersic→expdisk` 同样记 `PROMOTE_SINGLE_SERSIC_TO_DISK`，其正确与否由动作裁决判定；复合动作中 `PROMOTE+ADD(一个成分)` 与 `REPLACE(single_sersic→某成分)+ADD(一个成分)` 两类两原子模式可进入核心集；2026-09-18 增补（小鱼儿 S8 QC 裁定）：①单波段成分语义标签来源扩展——`0) model # ... (Label)` 行内括号标签与 `# Object number: N -- Label` 段头、`# STRUCTURE` 注释同等优先；②单波段无标签多成分 Sersic，`n≈1.0`（容差 0.01）且 `vary=0` 记 `disk`（Sersic-disk），其余非 `n≈0.5` 记 `bulge`；③多波段 N 块（AGN）记 `agn`，非宿主 G 块属下的 P 块记 `companion`；④多波段泛化标签（`objN`）按「当轮决策文件 `objN→语义` 映射＋实例内 P 字母语义锚点＋`n≈0.5`／`n≈1.0` 固定启发式」恢复语义，无法恢复的块记状态标记 `unidentified_sersic`，涉及该标记结构变化的转换标 `INCONCLUSIVE` 归排除集；⑤多波段决策文件（`all_bands_comparison_component_analysis_*.md`）与单波段决策文件同为动作意图证据源，适用于动作核验与裁决；⑥CONVERGED 裁决不以 1D/2D 残差视觉核验为判据，终止证据＝历史最佳轮指定＋专家成分一致＋拟合健康（视觉核验升级通道保留，需要时可再加）；2026-09-20 增补（小鱼儿 label-rule-samples 审阅裁定）：⑦多波段 lyric 块前注释（如 `# Sersic function — Bulge (obj0)`、`# Component A: Bulge (Sersic, n free)`、`# Profile C - Nucleus (obj2)`）是泛化标签块的第一语义来源，优先于决策文件锚点／实例字母锚点／n 启发式；注释明写 AGN 才记 `agn`，注释 Nucleus/致密核的 P 块 Sersic 一律记 `bulge`（P 块 Sersic 拟合的核不是 AGN）；⑧轮次输入完整性门：多波段轮目录须含至少一个拟合产物（result FITS 或 gssummary）且 lyric 引用的全部科学图像存在；单波段配置 `A)` 行引用的输入图像须存在——不满足的轮次视为不可用拟合轮，不参与任何转换对与 CONVERGED 候选（reason code `NO_FIT_PRODUCT`／`SCIENCE_INPUT_MISSING`／`INPUT_IMAGE_MISSING`）；2026-09-20 第二批裁定：⑨benchmark 语义去重——同一 `(mode, object_id, source_components, canonical_action)` 决策点在冻结集内只保留证据最完整的一条（CONVERGED 以 `(mode, object_id, best-round components, CONVERGED)` 为键，同时覆盖镜像批次副本），其余记 `SEMANTIC_DUPLICATE` 归 audit——跨批次对同一星系的重复历史演示（拟合数值略异、内容指纹不合并）不得重复计入核心集；⑩非泛化、非语义标签的 sersic 块废除 bulge 残留回退，改记 `unidentified_sersic`（同泛化不可恢复处理）；2026-09-21 增补（小鱼儿方案 A 裁定）：⑪单波段无标签 Sersic 的 disk 判定增加尺寸判据——同轮存在 ≥2 个无标签 Sersic 时，其中距星系成分质心 ≤10 px 且 Re 最大者若 n≈1.0（±0.01，不论固定或自由）记 `disk`（其余块按 n 判：0.5→bar、其余→bulge，非最大块的固定 n=1.0 不再记 disk）；仅一个无标签块时维持「固定 n≈1.0→disk」；远心块不参与盘判定。背景量化：有标签块中固定 n≈1.0 的 bulge(294)/disk(84)、自由 n≈1.0 的 bulge(155)/disk(156)，n 值本身不能区分，须以尺寸＋位置辅助；此前为 `component-evalset-v1`）。

---

## 1. 目标与边界

目标是从单波段和多波段 MCP 历史拟合轨迹中抽取一组可复现、证据充分的单步决策样本，用于回答：

> 给定某一轮拟合结束时可见的输入状态，成分分析模块能否输出该状态下正确的下一步动作？

v1 统一评测“当前状态下的单步决策”，样本形式为：

```text
state_round_i -> historical_action -> state_round_j
```

用于评测新增、删除、替换和参数／约束调整等实际执行动作；当当前轮是历史拟合选定的最佳轮次并满足终止条件时，`historical_action` 为 `CONVERGED`，此时 `state_round_j` 为空。

其中：

- 专家终态标签只提供星系应有的成分集合和成分语义映射。
- 历史拟合轨迹是 Agent／MCP 的拟合轨迹，不是科学家的逐轮专家演示。
- 历史动作不能直接作为正确答案；必须结合专家终态、动作前状态和动作后结果重新裁决。
- 历史最佳轮次由 `working_note.md` 或 `analysis_report*.md` 给出，只用于定位 `CONVERGED` 候选，不自动等于正确终态或专家终态标签。
- 即使历史最佳轮次与专家终态不一致，轨迹中仍可能包含局部正确的动作；反之，最终成分一致也不能证明中间每个动作都正确。

v1 范围限定为 Image 拟合阶段的单步决策评测，包含实际转换动作和满足条件时的 `CONVERGED` 终止决策。SED 和 Image-SED 目录不进入 v1；完整自主拟合的 rollout 评测留到单步评测稳定后实施。单波段和多波段使用统一动作语义，但分别建集、分别报告指标。

---

## 2. 数据源现状

### 2.1 单波段历史数据

数据根目录：`/media/data/galfit_run_history`。

权威说明文件：`/media/data/galfit_run_history/README.md`。该目录归档 2026-05 至 2026-07 的 GALFIT 历史数据；README 记录迁移时共有 99 个条目、约 25 GB。单波段数据实际包含两类观测：

#### Gadotti／SDSS r 波段

- `gadotti-json/`：20 个对象的专家参数 JSON。
- `gadotti-json-80/`：80 个扩展对象的专家参数 JSON。
- 当前共核对到 100 个 `<sample>_Gadotti_params.json`。
- JSON 中 `mag_disk`、`mag_bulge`、`mag_bar` 非零表示相应成分存在；v1 只据此派生专家成分集合。其余专家参数可以保留为辅助证据，但不作为参数数值真值参与主评测。
- `gadotti-gt/` 保存 20 个对象的人工参照拟合过程及 `best_turn/` 参照解。
- README 统计自动拟合约有 26 个批次、约 780 个对象目录，覆盖常规批次、80 星系扩展集、模型／skill 对照和 mismatch 策展集。
- 自动批次通常保存输入 FITS、`galfit.feedme`、逐轮 `galfit.NN`、`archives/<round_id>/`、`working_note.md` 和 `analysis_report.md`。

#### JWST NIRCam F277W 单波段

- `jwst_32_gt.json` 提供 25 个对象的专家成分集合。
- `jwst_human/` 保存 12 个对象的人工参照拟合目录。
- README 统计自动拟合约有 10 个批次、约 322 个对象目录。
- 当前标签中的硬成分包括 `disk`、`bulge`、`bar`、`nucleus`、`fourier` 和 `elliptical`；5 个 `companion?` 是不确定标签，不作为 v1 硬标签。
- 单波段 JWST F277W 中，`elliptical` 共 4 个：`obj1845`、`obj216`、`obj2185`、`obj2758`。它表示椭圆星系，canonical component 固定记为 `single_sersic`，对应使用 Sersic 模型的单成分拟合；它不能与使用 `expdisk` 模型的 `disk` 合并。`nucleus -> agn`、`fourier -> fourier_m1` 按当前项目语义规范化。
- Gadotti 专家标签读法（2026-09-15 裁定）：`gadotti-json*/` 的 `<sample>_Gadotti_params.json` 中，`MType` 为 `elliptical` 且 `mag_bulge` 有数值 → 标签为 `single_sersic`（`mag_bulge` 描述单成分而非分解中的 bulge）；`MType` 非 `elliptical` → 依有数值的 `mag_xxx` 字段确定成分（`mag_disk`→`disk`、`mag_bulge`→`bulge`、`mag_bar`→`bar`，mag=0 表示缺失）。

#### 单波段证据限制

- 大部分历史 Claude Code 会话 JSONL 已丢失；README 记录只有 `jwst_0705`、`gadotti-0707` 和 `gadotti-skill` 保留了部分或全部会话文件。
- 会话缺失不自动排除样本。只要前后轮配置、拟合结果、逐轮分析和最终报告足以恢复转换，仍可进入候选池。
- `analysis_report.md` 末尾 JSON 是历史拟合最终结论的权威记录；`.best_round.json` 只是工具缓存。
- 逐轮 `component_analysis` Markdown 可用于标签裁决和审计，但不能作为同一 state 的被测输入，否则会直接泄漏历史动作。

### 2.2 多波段历史数据

数据根目录：`/media/data/galfits_run_history`。

专家标签文件实际名称为 `expert-final-labels.json`。首轮核对结果：

- 32 个对象，`label_status` 全部为 `confirmed`。
- 规范化规则已经明确：`disk(lop) -> disk + fourier_m1`、`single sersic -> disk`、`nucleus -> agn`，`edge_on_disk` 保持独立。
- 标签覆盖 14 种成分组合。按单个成分计数：`disk` 26、`fourier_m1` 16、`bulge` 13、`bar` 7、`edge_on_disk` 6、`companion` 6、`agn` 4。

根目录下有 13 个历史批次目录。首轮只读盘点得到以下原始计数：

| 项目 | 数量 | 说明 |
|---|---:|---|
| 历史批次目录 | 13 | 包含日期批次和 `jwst_multi_band_*` 批次 |
| 对象目录实例 | 207 | 同一对象可跨批次重复出现，尚未去重 |
| 可映射到 32 个专家标签的目录实例 | 196 | `_2` 后缀暂按同一对象归一化统计 |
| 无专家标签的目录实例 | 11 | 不进入评测集 |
| 非 `sed` 命名的 output 轮次目录 | 1416 | 按目录名排除含 `sed` 的原始计数，尚未完成 Image 语义确认、重复轨迹和完整性去重 |
| 名称含 `sed` 的轮次目录 | 239 | v1 排除 |
| 最终分析报告 | 193 | `analysis_report*.md` |
| Working Note | 205 | 不保证是逐轮即时快照 |

这些数字只描述原始存量，不能解释为可用评测样本数。当前还存在三类重复或歧义：

1. 同一对象在多个日期批次中重复拟合。
2. `jwst0709`／`jwst_multi_band_0709`、`jwst0716`／`jwst_multi_band_0716` 等目录包含相同轮次名和相同报告内容，但整个目录的文件集合不完全相同，不能只按批次名删除一份。
3. `1429_2`、`314_2`、`317_2` 等后缀对象需要先映射到基础 `object_id`，再判断它们是独立重跑还是文件副本。

因此，多波段只纳入 `expert-final-labels.json` 中出现的对象；其他对象从评测清单逻辑排除，不需要为构建评测集物理删除原始目录。重复项必须按对象、轮次输入和输出内容生成 transition fingerprint 后去重，不能按路径名猜测。

### 2.3 当前可用性结论

- 两个数据根目录均可读取，且同时保存了专家终态标签和多轮历史拟合产物。
- 单波段具有 100 个 Gadotti 标签和 25 个 JWST F277W 标签；多波段具有 32 个 confirmed 标签。
- 原始历史轮次数量充足，但证据完整性、重复批次、语义映射和未来信息泄漏尚未完成系统审计。
- 评测集规模必须在完成对象白名单过滤、Image-only 过滤、完整性检查、去重和动作裁决后再报告，当前不预设样本数。

---

## 3. Ground Truth 定义

### 3.1 对象级硬真值

对象级硬真值只有：

```text
expert_final_components + semantic_mapping
```

它不包含专家逐轮动作，也不要求历史拟合参数接近某组专家参数。即使 Gadotti JSON 中存在参数值，v1 主评测仍只使用由非零 `mag_*` 推导的成分集合，避免单波段和多波段使用不同的主标签定义。

### 3.2 历史最佳轮次

每个历史对象需要从 `analysis_report*.md` 或 `working_note.md` 提取：

- `historical_best_round_id`；
- 历史最佳轮的成分集合；
- `terminal_consistency = match | mismatch | unresolved`。

`terminal_consistency` 描述整条历史轨迹是否到达专家终态，不决定单个历史动作的裁决。历史最佳轮只有在专家成分集合一致、拟合有效且残差／参数健康满足终止门槛时，才可将当前决策标为正确的 `CONVERGED`；否则保留该轮次信息，但不能把 `CONVERGED` 作为正确动作。

### 3.3 转换级标签

每个历史转换都要独立判断：

```text
当前状态是否支持这个动作？
动作执行后的结果是否支持这个动作？
动作是否让结构更接近专家终态，或修复了阻止继续拟合的明确问题？
```

只有证据充分的转换才成为核心评测样本。历史上执行过但证据不足、明显有害或仅用于试探的动作保留在非核心审计池或排除清单中，不进入核心正确动作集。

---

## 4. 统一动作协议

### 4.1 单步决策动作

v1 的单步决策样本预测目标与当前结构化 workflow action contract 对齐：

| 动作 | 含义 |
|---|---|
| `PROPOSE_ADD(component)` | 新增当前模型中不存在的物理成分 |
| `PROPOSE_REMOVE(component)` | 删除已有的额外或有害成分 |
| `PROPOSE_REPLACE(from, to)` | 用正确物理模型替换现有错误模型 |
| `PROMOTE_SINGLE_SERSIC_TO_DISK` | 把现有未分类 single Sersic 原位确认为 Disk，不新建第二个 profile |
| `REFIT_PARAMETERS(parameter_changes)` | 保持成分结构，修改初值、边界、固定状态或必要约束 |
| `CONVERGED` | 当前轮是历史拟合选定的最佳轮次，且通过专家成分、拟合、残差和参数健康终止门槛；不生成下一轮 feedme |

`KEEP_AND_CONTINUE` 不映射为历史下一轮动作。它表示 workflow 的中间状态或证据收集状态；历史记录中即使出现类似表述，也只保留在解释性证据中。

`CONVERGED` 是单步决策中的终止动作，不是独立的最佳轮次选择任务。它必须记录 `historical_best_round_id` 和终止证据；历史最佳轮与专家终态不一致时，不能仅因为历史报告称其为 best round 就把它标为正确。

`ACCEPT_REFIT` 和 `REJECT_REFIT` 属于候选拟合完成后的 refit comparator 输出，不与 proposal 阶段的主评测混为一个任务。以后可以单独建立 refit 仲裁子评测集。

评测 canonical component 名称使用：`disk`、`single_sersic`、`bulge`、`edge_on_disk`、`bar`、`agn`、`fourier_m1`、`companion`、`compact_central_source_candidate`、`lens`，以及仅作状态标记的 `unclassified_sersic` 与 `unidentified_sersic`（2026-09-18 增补：多波段泛化标签经锚点与启发式仍无法恢复语义的块）。其中 `single_sersic` 仅用于单波段椭圆星系的 Sersic 单成分语义；多波段首轮未标注的单 Sersic 一律记为 `unclassified_sersic`，不得记为 `single_sersic`。`PROMOTE_SINGLE_SERSIC_TO_DISK` 只适用于未分类 single Sersic（`unclassified_sersic`）向 `disk` 的原位确认，不能用于把 `elliptical` 标签改写成 `disk`。某数据源没有对应专家语义时，不得自行猜测映射。

`PROMOTE` 与 `PROPOSE_REPLACE` 的分界（`component-evalset-v2` 起冻结）：

- `PROMOTE_SINGLE_SERSIC_TO_DISK` 仅适用于**未分类** single Sersic 向 `disk` 的原位确认；确认 Disk 后固定 `n=1` 是该动作的规范组成部分，不另计为独立的参数动作。
- `PROPOSE_REPLACE(from, to)` 适用于**已有语义分类**的成分更换语义或 profile，`from` 与 `to` 必须一对一：既包括 profile 类型替换（如 `sersic -> ferrer`、`sersic -> edgeondisk`），也包括同 profile 下的语义改标（如单波段 `single_sersic -> bulge`：Sersic 模型不变，语义标签改变并伴随 `n` 固定）。
- 未分类成分（`unclassified_sersic`）没有「被裁定的物理语义」，因此不存在语义错误，不得作为 `PROPOSE_REPLACE` 的 `from`；它只能被 `PROMOTE` 确认为 `disk`。未分类成分在血缘上变为非 `disk` 成分（如直接成为 `bulge`）的原子变化无法映射为现有动作，所在转换保留复合候选并标记 `INCONCLUSIVE`。
- 单波段扩展（2026-09-15 裁定，2026-09-16 修订）：单波长轮次中未分解单 Sersic 向 `disk` 的原位转换（Sersic→expdisk，参数块血缘上原成分成为盘）一律计为 `PROMOTE_SINGLE_SERSIC_TO_DISK`——单波段确认 Disk 后即使用 expdisk 模型，模型切换是确认动作的组成部分。**动作类型判定不依赖专家标签**：elliptical 对象（专家终态 `single_sersic`）上的同类转换同样记 `PROMOTE`，其方向是否与专家语义冲突由动作裁决（verdict）表达，不改变动作类型。
- 单波段成分细分（2026-09-16 裁定）：GALFIT 单波段输出中 bar 与 bulge 同为 Sersic profile，以拟合 Sérsic 指数区分——`n≈0.5`（固定或自由，容差 0.01）记 `bar`，其余多成分 Sersic 记 `bulge`；单一无标注 Sersic 仍记 `single_sersic`。

---

## 5. State 输入与防泄漏

### 5.1 允许的 state bundle

根据单波段／多波段实际格式，当前轮输入可以包含：

- 当前轮实际使用的 `feedme`／`.lyric` 和约束文件；
- 当前轮输出参数文件与 summary；
- 当前轮 `result.fits` 或各波段 result FITS；
- 原图、模型、残差、sigma、mask、PSF 及 comparison PNG；
- 在当前轮之前已经生成的 detect、catalog、segmentation 证据；
- 当前轮之前已发生的结构化动作历史和 PolicyState。

评测 manifest 必须列出明确文件路径和 SHA-256，不使用 glob 在运行时猜测输入。

### 5.2 禁止进入同一 state 的内容

- 当前轮 `component_analysis` Markdown 及其中的“本次调整决策”；
- 包含当前轮之后信息的完整 `working_note.md`；
- 最终 `analysis_report*.md`；
- `.best_round.json` 或最终 best-round 结论；
- 下一轮配置、下一轮结果和任何后见标签。

`working_note.md` 通常是持续追加或事后整理的文件，不能直接作为历史时点输入。需要历史上下文时，应从当前轮之前的配置和已发生动作生成结构化 `history_context`。

### 5.3 输入截止点

每个样本必须记录 `input_cutoff`，含义是被测模块在该样本中最晚可以看到的轮次和时间。构建器必须执行泄漏检查，确认所有输入均不晚于该截止点。

---

## 6. 历史动作恢复

历史动作优先从相邻轮次的配置差异恢复，而不是直接相信自然语言总结：

1. 比较 `state_round_i` 的收敛输出与 `state_round_j` 的输入配置。
2. 恢复新增、删除、替换、profile 原位提升、参数 fixed/free、初值、边界和约束变化。
3. 用当前轮 `component_analysis` Markdown、`working_note.md` 和会话 JSONL 解释动作意图，但这些文件只进入标签侧 `evidence_refs`。
4. 如果一次历史转换同时包含多个结构动作，例如同时添加 Bulge 和 Bar，先检查下一轮及其后续轮次的实际输入配置、轮次差异和动作记录：
   - 如果存在中间拟合轮，或后续轮次明确只执行其中一个动作，则按可观测的中间状态拆成多个转换候选。
   - 如果只有 Working Note 中的动作顺序描述，没有对应的中间配置和拟合结果，则不能据此反推唯一动作，原记录保留为复合候选并排除出核心集。
   - 查看后续轮次只用于标注阶段恢复动作，不能把后续信息放回前一轮的 `state` 输入。
5. 纯粹把上一轮收敛值抄作新初值、但没有明确物理目标的机械变化，不单独标为 `REFIT_PARAMETERS` 正确动作。

---

## 7. 动作正确性裁决

### 7.1 结构距离

设当前成分集合为 `C_i`，专家终态集合为 `G`：

```text
d_i = |C_i symmetric_difference G|
delta_d = d_i - d_j
```

- `delta_d > 0`：下一轮结构更接近专家终态。
- `delta_d = 0`：结构距离不变，需要依赖参数／残差／拟合健康证据判断。
- `delta_d < 0`：结构远离专家终态，通常不能标为正确动作。

结构距离只是必要证据之一，不能单独决定裁决。新增正确成分但产生严重简并，或者删除额外成分后拟合明显失效，都不能仅凭 `delta_d > 0` 判为正确。

### 7.2 四类证据

每个转换动作统一检查：

1. **终态方向**：动作是否减少与专家成分集合的差异。
2. **动作前必要性**：在查看动作后结果之前，当前 state 是否已经存在可观察的动作依据。
3. **动作后有效性**：下一轮是否拟合成功，残差／统计量是否支持该动作，且没有新出现严重参数病态。
4. **后续稳定性**：动作结果是否在后续有效轮次中保留，或是否很快因失败被回退。

“动作前必要性”按以下步骤检查，不用主观印象替代证据：

1. 从当前轮的 result FITS、1D／2D 残差、summary、参数和约束中提取动作发生前事实。
2. 与专家终态的成分差异只作为方向性证据，不能单独证明当前轮已经有必要执行该动作。
3. 读取不晚于当前轮的 component-analysis 结果或 Working Note 中的既有观察，作为解释性证据；动作后新增的说明不能倒灌到当前 state。
4. 为每个动作记录 `pre_action_necessity=supported | unsupported | unknown` 和逐条 `evidence_refs`。核心样本至少需要一条直接可观察事实，且该事实与动作类型相符。

| 动作 | 可接受的动作前必要性事实 |
|---|---|
| `PROPOSE_ADD` | 当前残差存在未解释的结构，或当前轮之前已有可追溯的成分候选；专家终态缺少该成分只能作为方向证据 |
| `PROPOSE_REMOVE` | 当前成分是专家终态之外的额外成分，且存在边界命中、通量／尺度简并、残差无改善或其他明确病态事实 |
| `PROPOSE_REPLACE` | 当前 profile 与已裁定的物理语义不匹配，且配置或分析证据明确指向替换目标 |
| `PROMOTE_SINGLE_SERSIC_TO_DISK` | 当前确为未分类 single Sersic，且数据源语义映射和当前配置均支持原位确认为 Disk |
| `REFIT_PARAMETERS` | 存在非收敛、参数边界、约束冲突、NaN／Inf 或可复现的参数健康问题，且拟议修改直接针对该问题 |

如果只能在查看动作后结果或最终报告后才能解释动作，`pre_action_necessity=unknown`，不得标为 `CORRECT + high`。

`analysis_report*.md` 和 `working_note.md` 中的解释只能作为辅助证据；BIC、reduced chi-square、FITS 残差、参数和约束差异应优先使用机器可核验的原始产物。

### 7.3 参数健康只判断明显病态

没有专家参数真值时，参数只用于识别明确失败，不用于判断是否接近某个目标数值。需要记录的典型问题包括：

- 拟合未收敛或结果文件不完整；
- NaN／Inf；
- 参数撞到预设边界；
- 两个成分发生明显通量、尺度或中心简并；
- 成分尺寸层级、轴比或位置关系明显违反项目物理规范；
- 必需固定项或中心约束缺失；
- 结构性 1D／2D 残差仍然显著。

### 7.4 分动作判据

| 动作 | 标为 `CORRECT` 的最低要求 |
|---|---|
| `PROPOSE_ADD` | 新增成分属于 `G - C_i`；当前状态存在支持证据；下一轮拟合有效且没有新增严重病态；可选成分得到残差和统计证据支持 |
| `PROPOSE_REMOVE` | 被删成分属于 `C_i - G` 或有明确有害证据；删除后的拟合有效；残差和健康度没有实质恶化，最好有直接 A/B 支持 |
| `PROPOSE_REPLACE` | 被替换成分语义错误，目标成分属于专家终态；替换后结构距离下降且拟合健康 |
| `PROMOTE_SINGLE_SERSIC_TO_DISK` | 当前确为未分类 single Sersic；专家终态包含 Disk；原位提升后没有生成第二个 Disk，拟合有效且参数物理合理 |
| `REFIT_PARAMETERS` | 当前存在明确的参数／约束问题；动作直接针对该问题；下一轮相应问题消失或减轻，且没有引入更严重问题 |
| `CONVERGED` | 当前轮等于历史报告选定的 `historical_best_round_id`，且 `C_i == G`、拟合有效、无明确参数病态（2026-09-18 小鱼儿裁定：1D/2D 残差视觉核验不作为裁决判据，终止证据＝最佳轮指定＋专家成分一致＋拟合健康；视觉核验升级通道保留）；否则不能标为正确的 `CONVERGED` |

对于当前项目仍处于 review-only 的真实删除动作，只有具备实际前后轮 A/B 结果时才可进入核心评测集；仅有自然语言“建议删除”不足以形成金标准。

### 7.5 裁决枚举

| `action_verdict` | 含义 | 是否进入核心评测集 |
|---|---|---|
| `CORRECT` | 方向、必要性和动作后结果共同支持 | 仅 `confidence=high` 时进入 |
| `EXPLORATORY` | 当时有试探理由，但结果不支持保留该动作 | 否，进入非核心审计池 |
| `HARMFUL` | 远离终态，或造成明显拟合／参数退化 | 否，进入非核心审计池，可记为 forbidden action |
| `INCONCLUSIVE` | 证据缺失、冲突或无法恢复唯一动作 | 否，进入排除清单 |

`ACCEPTABLE_ALTERNATIVE` 不能仅由未执行的猜测产生。只有历史中存在独立执行且通过同等裁决的分支，或后续得到明确科学裁定时，才加入 `accepted_action_set`。

非核心审计池不是训练集，也不参与主准确率。它只用于保留被排除动作的可追溯记录，并在需要时统计错误添加、错误删除、试探动作和明显不安全动作；如果本周目标只是冻结一个评测集，可以只发布核心集和排除清单，审计池作为内部 artifact 保存。

### 7.6 裁决置信度

`confidence` 表示对标签裁决的证据强度，不是被测模型的置信度：

- `high`：前一轮状态、动作差异、后一轮结果、专家终态和关键原始证据均完整且一致。
- `medium`：动作方向明确，但缺少残差、参数健康或后续稳定性中的一类证据。
- `low`：主要依赖 Working Note／报告自然语言，或轮次关系仍有歧义。

v1 核心评测集只纳入 `CORRECT + high`。`medium` 和 `low` 保留供后续补证，不参与主指标。

---

## 8. 样本字段

### 8.1 必需字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 评测样本格式版本 |
| `sample_id` | 全局唯一的样本编号 |
| `dataset_id` | 数据源和历史批次标识 |
| `object_id` | 规范化后的星系编号 |
| `mode` | `single_band` 或 `multi_band` |
| `state_round_id` | 被测模块接收的当前轮次 |
| `post_action_round_id` | 历史动作执行后产生的轮次；`CONVERGED` 样本为空 |
| `input_cutoff` | 当前样本允许读取信息的截止轮次／时间 |
| `state_manifest` | 当前轮输入文件引用、角色、大小和 SHA-256 |
| `source_components` | 当前轮规范化成分集合 |
| `expert_final_components` | 对象级专家终态成分集合 |
| `current_fit_health` | 当前决策发生前的拟合健康事实 |
| `historical_action_raw` | 从历史资料恢复的原始动作或最佳轮次选择原文，不用于直接评分 |
| `canonical_action` | 映射到当前 action contract 的历史动作，包含 `CONVERGED` |
| `action_verdict` | `CORRECT`、`EXPLORATORY`、`HARMFUL` 或 `INCONCLUSIVE` |
| `confidence` | 裁决证据强度：`high`、`medium` 或 `low` |
| `verdict_reason_codes` | 可统计的裁决理由，例如 `REDUCES_COMPONENT_DISTANCE`、`NEW_BOUNDARY_HIT` |
| `evidence_refs` | 支撑裁决的前后轮 summary、FITS、配置、残差和报告引用 |
| `terminal_consistency` | 该历史对象最终结果与专家终态的关系 |
| `review_status` | `pending`、`adjudicated` 或 `excluded` |
| `evaluation_pool` | `benchmark`、`audit` 或 `excluded`；这是用途标识，不是 calibration／validation／test 划分 |

### 8.2 `current_fit_health`

该字段描述动作发生前可以观察到的拟合状态，不是单个主观分数。建议结构为：

```json
{
  "fit_converged": true,
  "bic": 275025.9,
  "reduced_chisq": 0.32,
  "residual_flags": ["CENTRAL_POSITIVE_RESIDUAL"],
  "parameter_flags": ["DISK_BULGE_MAG_DEGENERACY"],
  "constraint_flags": [],
  "data_quality_flags": []
}
```

### 8.3 `historical_action_raw` 与 `canonical_action`

- `historical_action_raw` 保留历史操作原貌，例如“一次同时添加 Bulge、固定 n、修改中心约束”。
- `canonical_action` 是机器评测使用的统一动作。无法无歧义映射为一个动作时，样本不能进入 v1 核心集。

### 8.4 `action_verdict` 与 `confidence`

- `action_verdict` 回答“这个历史决策是否正确”，包括 `CONVERGED` 是否在该轮正确终止。
- `confidence` 回答“现有证据对这个裁决支持得有多充分”。

例如，历史最佳轮与专家终态不一致时，某一步 `PROPOSE_ADD(bulge)` 仍可能是：

```json
{
  "terminal_consistency": "mismatch",
  "action_verdict": "CORRECT",
  "confidence": "high"
}
```

这表示整条历史轨迹没有完成目标，但该局部转换仍是可用于评测的正确动作。

`CONVERGED` 样本的最小形式为：

```json
{
  "state_round_id": "round_5",
  "post_action_round_id": null,
  "canonical_action": "CONVERGED",
  "historical_best_round_id": "round_5",
  "terminal_consistency": "match",
  "action_verdict": "CORRECT",
  "confidence": "high"
}
```

只有历史最佳轮与专家终态一致且满足终止条件时，`CONVERGED` 才能进入核心评测集；如果 `terminal_consistency=mismatch`，该轮不能标为正确的 `CONVERGED`，但其之前的局部转换仍可单独裁决。

### 8.5 可选字段

- `accepted_action_set`：同一 state 下已有证据确认的多个合理动作。
- `forbidden_actions`：有直接历史证据判定为有害的动作。
- `historical_best_round_id`：该次历史拟合报告选择的最佳轮次。
- `sample_fingerprint`：用于跨目录去重的稳定摘要，覆盖对象、当前轮、canonical action、动作后轮次（如有）和动作差异。
- `adjudication_rule_version`：本次动作裁决规则版本。

---

## 9. 数据分层与评测指标

### 9.1 数据分层

1. **核心评测集**：历史决策中 `action_verdict=CORRECT` 且 `confidence=high`，具有唯一 canonical action 或已验证的 `accepted_action_set`；`CONVERGED` 还必须有专家终态一致和完整终止证据。专家终态为 `edge_on_disk` 的对象（2026-09-15 裁定）不进入核心评测集，其转换样本归入 audit 池。复合动作默认归 audit 池，但两类模式（2026-09-16 小鱼儿裁定）视为合理单轮组合、可进入核心集：`PROMOTE_SINGLE_SERSIC_TO_DISK + PROPOSE_ADD(一个成分)` 与 `PROPOSE_REPLACE(single_sersic -> 某成分) + PROPOSE_ADD(一个成分)`——修改一个成分的参数并同轮新增一个成分是合理的历史操作形态；其余复合（多于两个原子、含 REMOVE、或 REPLACE 的 from 不是 single_sersic）仍归 audit。
2. **非核心审计池**：`EXPLORATORY`、`HARMFUL` 以及未达到核心门槛但仍有完整产物的记录。它只用于错误类型、安全性和历史轨迹覆盖分析，不参与主指标，也不用于训练或调参。
3. **待补证／排除集**：`INCONCLUSIVE`、证据不完整、复合动作无法拆分或语义映射未裁定；这些记录不作为被测模块的输入／输出样本。

### 9.2 划分规则

当前数据集只用于评测，不用于训练、校准或 prompt／规则调参，因此 v1 不设置 calibration、validation 和 test 三个统计 split，而是冻结一个完整的 `benchmark`，另存一个不计入主指标的 `audit` 池。

- 同一 sample fingerprint 的副本只保留一条，其他路径写入 aliases；去重是数据清理，不是 split。
- 单波段和多波段分别建集、分别报告；只有动作语义和证据规则完全一致时才报告合并结果。
- 统计时同时报告决策样本数量和 object 数量；置信区间和 bootstrap 以 `object_id` 为抽样单位，避免把同一星系的许多轮次误当成独立样本。
- 如果未来要用这批数据调参，不能把当前冻结 benchmark 继续当作无偏评测集；届时应新增独立对象，或在构建前另行冻结开发集和最终测试集。

### 9.3 v1 主指标

- `action_type_accuracy`：动作类型是否正确。
- `component_accuracy`：ADD／REMOVE／REPLACE 的目标成分是否正确。
- `accepted_action_hit_rate`：预测是否落入已验证合理动作集合。
- `parameter_plan_exactness`：`REFIT_PARAMETERS` 的目标参数和操作是否正确。
- `unsafe_action_rate`：预测是否命中已有直接证据否决的动作。
- `converged_precision`：预测 `CONVERGED` 的样本中，多少同时满足历史最佳轮、专家成分集合、拟合健康和残差终止门槛。

统计和 bootstrap 必须以 `object_id` 为抽样单位，不能把同一对象的多个高度相关轮次当成独立星系。

端到端 rollout 后续再评测最终成分 F1、有效收敛率、错误锁定率和拟合轮次；v1 不用历史轨迹模拟被测方法偏离后产生的新 state。

---

## 10. 执行方案

### 阶段 A：冻结语义和规则版本

1. 固化单波段／多波段成分名称到 canonical component 的映射。
2. 固化 JWST F277W `elliptical -> single_sersic` 的语义，并保留其与 `disk -> expdisk` 的模型区别。
3. 固化 `companion?` 为不确定标签，不参与硬方向判断。
4. 冻结转换动作、`CONVERGED` 终止决策、`action_verdict`、reason code 和 `current_fit_health` schema。
5. 冻结 `adjudication_rule_version`，正式 benchmark 发布后不得修改；若规则改变，必须生成新的数据集版本。

### 阶段 B：建立只读数据源清单

1. 扫描两个数据根目录，只纳入能映射到专家终态标签的对象。
2. 记录批次、对象、Image 轮次、配置、结果、summary、comparison、分析和最终报告的显式路径。
3. 从报告提取历史最佳轮，并记录是否与专家成分集合一致。
4. 对每个文件记录大小和 SHA-256；原始数据保持只读。
5. SED／Image-SED、无标签对象和缺少关键产物的轮次进入排除清单。

产物：`evaluation-source-inventory.json` 和 `evaluation-source-exclusions.jsonl`。

### 阶段 C：恢复轨迹与去重

1. 对普通转换依据明确时间、轮次配置引用和报告记录建立 `parent_round_id -> post_action_round_id`；对 `CONVERGED` 记录历史报告选定的 `historical_best_round_id`。
2. 解析前后配置，恢复成分、参数和约束差异。
3. 对包含多个动作的转换检查下一轮及后续轮次：有中间配置／拟合结果时拆分为单动作候选；只有文字顺序、没有中间状态时保留复合候选并排除核心集。复合转换的原子动作按参数块血缘（成分块位置与初值连续性）指派：原成分原位变为 `disk` 且前一轮为 `unclassified_sersic` 记 `PROMOTE_SINGLE_SERSIC_TO_DISK`；原已分类成分语义改标（如 `single_sersic -> bulge`）记 `PROPOSE_REPLACE`；新增成分块记 `PROPOSE_ADD`。原未分类成分变为非 `disk` 成分、或血缘无法唯一追踪的原子变化标记 `INCONCLUSIVE`，该转换保留为复合候选。
4. 生成 `sample_fingerprint`，至少覆盖 observation mode、规范化对象、当前轮配置、当前结果、动作后配置（如有）和动作差异。
5. 跨同日期目录、镜像目录和 `_2` 变体去重；保留证据更完整的 canonical source，其他路径作为 aliases。
6. 无法唯一确定父子轮次或动作的记录标记 `INCONCLUSIVE`。

产物：`evaluation-decision-candidates.jsonl`。

### 阶段 D：构建无泄漏 state bundle

1. 为每个历史决策候选生成显式 state manifest；`CONVERGED` 候选不生成动作后 state。
2. 排除当前轮动作分析、完整 Working Note、最终报告和所有未来轮次文件。
3. 从既有历史配置生成截止当前轮的结构化 `history_context`。
4. 执行路径、时间和内容泄漏检查。
5. 对 `CONVERGED` 候选，输入只截止到当前轮；`historical_best_round_id`、最终报告和终止裁决只在标签侧保存。
6. 用现有单波段／多波段 parser 和 artifact adapter 验证输入可读性；不执行新 GALFIT／GalfitS 拟合。

产物：`evaluation-state-manifests/` 和泄漏审计报告。

### 阶段 E：自动生成裁决证据

1. 计算动作前后成分距离和 `delta_d`。
2. 提取拟合收敛、BIC、reduced chi-square、参数边界、NaN、简并、约束和残差事实。
3. 依据相邻配置差异生成 canonical action 候选。
4. 对实际转换生成 `pre_action_necessity` 及其证据引用；对 `CONVERGED` 候选生成历史最佳轮次、专家成分一致性和终止门槛证据。
5. 自动规则只生成预裁决和 reason codes，不直接把历史记录签发为正确标签。

产物：`evaluation-action-prelabels.jsonl`。

### 阶段 F：逐转换证据裁决

1. 每个决策样本按第 7 节规则查看动作前 state、动作差异、动作后结果（如有）和专家终态。
2. 由证据作出 `action_verdict`、`confidence` 和 `pre_action_necessity` 判断，不把历史 Agent 的自然语言结论当作权威。
3. 对 `CONVERGED` 单独核对历史报告选定的 `historical_best_round_id`、专家成分一致性以及拟合、残差和参数健康终止门槛；不把它改写成独立终态任务。
4. 历史最佳轮与专家标签不一致的对象仍逐转换审查，保留其中 `CORRECT + high` 的实际动作；该对象的 `CONVERGED` 保留为 `mismatch`，不能标为正确。
5. 证据冲突、仅有文字建议、复合动作无法拆分或参数健康不明确时，标记 `INCONCLUSIVE`，不强行补标签。
6. 抽样复核自动解析的成分语义、轮次关系、动作差异和证据引用。

产物：`evaluation-action-adjudications.jsonl`。

### 阶段 G：冻结评测集

1. 所有决策样本只从 `action_verdict=CORRECT + confidence=high` 中生成核心条目；`CONVERGED` 还必须满足历史最佳轮、专家成分一致和完整终止证据。
2. 不设置 calibration／validation／test split；将核心条目标记为 `evaluation_pool=benchmark`，将非核心记录标记为 `audit` 或 `excluded`。
3. 固化 schema、标签版本、源文件 checksum、构建脚本版本和纳入／排除统计，并报告决策样本数量和 object 数量。
4. 运行一次基线成分分析模块，确认评测执行器能读取输入并输出当前 action contract；`CONVERGED` 作为同一决策接口的终止输出验证。
5. 主结果按单波段、多波段、动作类型、`CONVERGED` 和对象级分别报告；bootstrap 以 `object_id` 为单位。

产物：`evaluation-set-v1.jsonl`、`evaluation-set-v1-manifest.json` 和基线报告。

---

## 11. 核心样本准入门

一个候选样本只有同时满足以下条件，才能进入 v1 核心评测集：

- 对象有 confirmed 专家终态成分集合和已裁定语义映射。
- `state_round_id` 和相关文件引用明确；对于非 `CONVERGED` 样本，动作后轮次的身份和顺序也必须明确；对于 `CONVERGED` 样本，`historical_best_round_id` 必须可从历史报告核验。
- 当前轮输入 bundle 可读取，关键拟合产物通过格式检查。
- state bundle 不包含当前动作、未来轮次或最终报告信息。
- 历史决策可以无歧义映射到一个 canonical action；`KEEP_AND_CONTINUE` 不作为 canonical action。
- 对于非 `CONVERGED` 样本，动作有可核验的前置必要性和后验结果证据；对于 `CONVERGED` 样本，终止证据完整。
- `action_verdict=CORRECT` 且 `confidence=high`；`CONVERGED` 还必须与专家终态一致。
- `sample_fingerprint` 已完成跨批次去重。
- `evaluation_pool` 已冻结，且同一 sample fingerprint 没有重复计数。

任何一项不满足，都进入非核心审计池或排除清单，而不是降低准入门以增加样本数。

---

## 12. 已确认规则与后续约束

1. JWST F277W 的 4 个 `elliptical` 标签（`obj1845`、`obj216`、`obj2185`、`obj2758`）固定映射为 `single_sersic`，表示椭圆星系的 Sersic 单成分拟合；它与使用 `expdisk` 模型的 `disk` 不同，不能合并。
2. v1 不建立独立的 `terminal_selection` 任务；`CONVERGED` 是统一单步决策接口中的终止输出，只有历史最佳轮与专家终态一致且满足终止门槛时才是正确动作。
3. 保留 `audit` 池作为 artifact，用于不安全动作和排除原因追溯；不用于训练、调参或主指标。
4. Gadotti 参数 JSON 在 v1 中仅用于派生成分集合和辅助健康审查；参数恢复评测另建子评测及容差标准。
5. 只有文字顺序、没有中间配置／拟合结果的复合动作排除核心集；有中间状态时可按实际配置差异拆分。
6. 不预设核心样本数量，以完成审计后的 `CORRECT + high` 决策样本数量为准。
