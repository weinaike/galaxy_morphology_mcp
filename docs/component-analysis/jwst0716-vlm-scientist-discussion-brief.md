# JWST0716 VLM 成分分析：科学家讨论摘要

更新日期：2026-08-25

## 会议目的

这份摘要是与科学家讨论 JWST0716 成分分析重构的单一入口。建议先阅读下面的改造进展和当前证据边界，再讨论待裁定问题和评测方案。

本次会议不需要裁定“新方案已经优于旧方案”。建议形成三个可执行结论：

- 确认 diffraction_psf 的科学定位和证据优先级。
- 确认如何定义一轮拟合的合理下一步动作。
- 确认是否通过受控 A/B refit 比较新旧方案的实际效果。

Edge-on Disk 暂时继续排除在优化和评价范围之外。

这份摘要是和科学家讨论时的单一入口。历史 116 轮 shadow 结果仍保存在原始 artifact 中；本文件记录当前实现边界、尚待科学裁定的问题，以及需要时再打开的详细文件。

## 一、改造进展

### 1. 改造目标与总体架构

原始 residual_analysis.py 流程由自然语言分析直接给出下一轮建议。一个历史轮次的建议可能同时包含新增、删除、替换 profile、改变 Fourier 项，以及修改初值、参数边界、固定项或中心约束。这种流程能够支持专家工作，但难以稳定回答：观察到的证据是什么、哪条规则触发了动作、动作是否由数值证据或 VLM 观察直接支持。

新方案将职责拆成三层：

1. 数值证据层：从 FITS、残差、PSF、WCS 和拟合参数提取客观事实。
2. VLM 证据层：只输出受控的形态观察和混淆提示，不直接决定 GALFIT 动作。
3. 规则与 policy 层：根据数值证据和 VLM 观察，生成可追踪的结构动作。

每轮保存 numeric_evidence、vlm_evidence、rule_trace 和 decision_artifact，因此可以追溯“观察了什么、哪条规则满足、为什么最终新增或保持”。

### 2. 已完成的工程改造

数值证据层目前覆盖：

- 各波段数据质量、mask、SNR 和 PSF 可用性。
- Disk 的延展尺度、外层几何和系统性残差。
- 中心过量、中心源可分辨性和 PSF 分辨尺度。
- Bar 的等照度证据、残差 m=2、中心拉长和 PSF 衍射方向检查。
- Fourier m=1 非对称证据。
- 局部峰检测及跨波段 WCS 合并。
- Companion 候选的稳定 candidate_N 标识。

VLM 和契约层目前完成：

- 接入 OpenAI-compatible provider，历史 shadow 使用 gemini-3.1-pro-preview模型run决策结果。
- 使用严格 JSON schema 和受控形态标签。
- 明确 evidence_regions 的引用格式。
- VLM 解析失败时进入数值降级，不阻断整轮决策。
- 不再向 VLM 发送自由文本坐标。
- 由数值层生成 candidate_overlay.png，在图像上标记 candidate_N。
- 中心语义按方案 B 处理，Bulge、单 Sérsic 光球和 Disk 的分流主要依赖数值证据。

规则和运行基础设施目前完成：

- Disk、Bulge、Bar、Lens、Fourier m=1 和 Companion 规则已结构化。
- policy 能处理 PROPOSE、INCONCLUSIVE、数值降级、重复候选和保守 KEEP。
- 已建立 schema 校验、artifact adapter、批量 shadow runner 和回归测试。
- 正式 workflow 尚未接管，当前结果仍是 shadow 结果。

### 3. JWST0716 的 116 轮 shadow 结果

评测清单包含 116 个完整的非 SED 历史轮次，并关联对象级专家终态标签。历史轮次是 shadow 输入，不是专家逐轮动作真值。

| 指标 | 结果 |
|---|---:|
| 最终决策 | 116／116 |
| runner failures | 0 |
| VLM 严格解析成功 | 97 |
| PARSE_FAILED 后数值降级 | 19 |（已修复）
| PROPOSE_ADD | 48 |
| KEEP_AND_CONTINUE | 68 |

48 个新增动作的成分分布为：Companion 18、Bar 14、Bulge 11、Fourier m=1 4、Disk 1。

19 个解析失败的原因已经检查：中心标签互斥冲突 7 轮、响应中途截断 7 轮、evidence_regions 不符合契约 4 轮、低质量目标使用非法标签 1 轮。可由工程侧直接处理的契约问题已经处理，包括 evidence_regions 约束、candidate overlay、禁止自由文本坐标、中心语义方案 B 和旧 spheroid_like 标签拒绝。

历史 116 轮尚未用当前 v1.2 prompt、candidate overlay 和方案 B parser 重新运行，因此上述统计代表 2026-08-20 的历史完整 VLM shadow，不应冒充当前代码重跑结果。（未解决19个VLM降级的样本的问题修复的代码，还没有重新运行过。）

### 4. 与原始旧流程的资料边界

旧方案特指每个历史轮次的 all_bands_comparison_component_analysis_*.md 中“本次调整物理目标”部分。旧原始决策来源覆盖为：108 轮唯一且可解析、6 轮原始 Markdown 缺失、2 轮存在多份原始决策文件。

108 个唯一来源中的辅助方向标签可重叠（旧决策有多个动作），不能相加为总轮数：新增成分 65 轮、删除成分 17 轮、替换或模型改型 12 轮、Fourier／非对称调整 28 轮、参数或约束调整 40 轮、保持或继续 14 轮。

新旧方案不能用单一动作命中率比较。旧流程一轮可以同时提出多个原子动作；新方案每轮只有一个最终结构动作 PROPOSE_ADD 或 KEEP_AND_CONTINUE。完整逐轮对照见 shadow-dev-action-direction-comparison-jwst0716.md。

### 5. 当前能够确认与不能确认的事情

能够确认：

- 新方案工程链路已跑通，116 轮没有 runner failure。
- 每轮都有结构化证据和规则轨迹，审计性高于原始自然语言流程。
- VLM 解析失败不会阻断决策，数值降级路径有效。
- VLM 会实质改变规则行为，历史 shadow 相对 numeric-only baseline 有 21 轮动作变化。
- 候选位置映射和中心语义两个明确的证据契约问题已经修复。

不能确认：(主要是如何评价新方案，目前只能确认提升了可维护性和审计性)

- 新方案的科学准确率已经高于原始 residual_analysis.py。
- 48 个新增动作少于旧流程的新增建议，就意味着新方案更好。
- KEEP_AND_CONTINUE 增多一定是改善，也可能代表漏掉必要成分。
- 新增成分不在专家终态就一定错误；专家标签是对象终态，不是每轮唯一正确的下一步动作。
- 116 个轮次可以作为 116 个独立样本；相同 object_id 的多个轮次必须按对象聚合。

## 二、需科学家裁定的问题

### `diffraction_psf` 的含义与科学作用

`diffraction_psf` 表示点扩散函数（PSF）产生的衍射结构或衍射芒。PSF 是成像系统对点源的响应，`diffraction_psf` 是该响应中可能出现在图像或残差中的衍射图样；它不是 GALFIT 成分，也不应直接作为新增成分。它主要用于判断中心紧致过量、Bar 或候选源是否可能只是 PSF 伪影，避免把衍射结构当作真实成分。

“Bar 衍射冲突”是指：数值层的 Bar 证据通过，但 VLM 同时观察到 `diffraction_psf`，当前规则把后者作为冲突信号并阻止 `PROPOSE_ADD Bar`。衍射标签定位不清时，可能误杀真实 Bar；完全忽略它，则可能把衍射芒误建模为 Bar。

这里的 `band` 是具体滤镜波段，`panel` 是 comparison PNG 中的子图类别（如 original、model、residual），`region` 是子图中的空间或径向证据区域。要求统一 panel、region，意思是衍射证据和被否决结构应在同一波段、同一类子图、同一局部区域或相近尺度上对应，而不是凭“图中存在衍射”就否决整个结构。

### 定位与证据优先级

当前规则会把任意位置的高置信 `diffraction_psf` 当作全局冲突信号。需要确定：

1. 一个波段的衍射结构能否否决另一个波段的 Bar 或中心源证据？
2. 衍射证据是否必须和被否决结构位于同一波段、同一 panel、同一空间区域和相近径向尺度？
3. VLM 的衍射判断与实际 PSF 模板方向检查冲突时，哪一方优先？
4. 对已经通过数值分辨尺度门的中心过量，衍射标签应直接否决，还是只降级为 `INCONCLUSIVE`／待重拟合？

建议起点：只有在 band、panel、region 和结构尺度均匹配时，`diffraction_psf` 才能否决局部数值证据；跨波段或无法定位的全局标签只能作为冲突提示，不能直接否决。

## 三、建议对照的三个案例

| 案例 | 用途 | 关键事实 | 详细 artifact |
|---|---|---|---|
| `obj_331 iter2` | 可能误杀 Bar | F115W 数值 Bar 门通过，但全局 `diffraction_psf` 导致 KEEP | `/tmp/jwst0716-shadow-rounds-20260820-vlm/331_20260718_115818_obj_331_iter2/` |
| `obj_1502 iter5` | 可能正确否决 | 部分波段有 Bar 数值信号，但专家终态无 Bar，衍射否决方向可能正确 | `/tmp/jwst0716-shadow-rounds-20260820-vlm/1502_20260717_081358_obj_1502_iter5/` |
| `obj_1429 initial` | 可能误杀 Bulge | F410M 中心过量已被数值层判为可分辨，但全局 `diffraction_psf` 阻止 Bulge | `/tmp/jwst0716-shadow-rounds-20260820-vlm/1429_20260717_044425_obj_1429/` |

每个目录重点看 `numeric_evidence.json`、`vlm_evidence.json`、`decision_artifact.json`、`vlm_prompt.txt`；对应的原始比较图路径记录在该目录的 `manifest.json` 的 `comparison_png` 字段。`/tmp` 为临时 artifact 目录，若被清理，以 `docs/component-analysis/shadow-dev-manual-review-jwst0716-vlm.json` 中的案例说明和 `shadow-dev-evaluation-report-jwst0716-vlm.md` 中的统计为准。

## 四、建议的新旧方案评价框架

当前资料只能说明新旧方案分别做了什么，不能可靠证明哪个方案更好。原因有三点：

- 旧流程一轮可以同时提出新增、删除、替换 profile、Fourier 调整和约束修改；新方案每轮只有一个最终结构动作。
- 专家标签是对象终态，不是该历史轮次唯一正确的下一步动作。
- 历史轮次通常已经沿旧建议继续拟合，而新方案动作没有实际执行，直接比较后续拟合结果会有路径依赖。

建议分两个阶段评价。

### 阶段一：盲法动作审查

不让评审者知道动作来自新方案还是旧方案，根据 comparison PNG、numeric_evidence、vlm_evidence、当前成分和专家终态，填写每轮的动作集合：

- required_actions：证据明确要求处理的动作。
- acceptable_actions：有证据支持、但不是唯一合理路径的动作。
- rejected_actions：与图像、数值或物理条件冲突的动作。
- evidence_sufficiency：sufficient、limited 或 insufficient。

旧流程先拆成原子动作，例如 ADD bulge、REMOVE bar、REPLACE sersic → edgeondisk、ADD fourier_m1、ADJUST_CONSTRAINT center_xy、KEEP_STRUCTURE，再与新方案动作分别评价。

建议人工评审标签：

| 标签 | 含义 |
|---|---|
| SUPPORTED | 动作有视觉和数值支持，属于 acceptable_actions。 |
| PLAUSIBLE | 有一定依据，但不是当前优先动作，或证据仍有限。 |
| UNSUPPORTED | 缺少必要证据。 |
| CONTRADICTED | 与视觉、数值或物理条件明显冲突。 |
| MISSED_REQUIRED | 方案选择 KEEP 或其他动作，但漏掉明确必要的动作。 |
| NOT_EVALUABLE | 证据质量不足，无法判断。 |

主要统计应包括 contradicted_action_rate、required_action_coverage、unnecessary_structure_change_rate、keep_with_unresolved_required_action_rate 和 action_executability，而不是只统计新增成分是否出现在专家终态。

### 阶段二：关键分歧 A/B refit（根据决策建议重新拟合后看有没有变好）

人工盲审只能判断动作是否有证据支持，不能单独证明哪个动作产生更好的拟合。建议从以下分歧中抽取 20～30 个代表案例：

- 旧方案建议结构变化、新方案 KEEP。
- 新方案建议结构变化、旧方案没有建议。
- 两者建议不同成分。
- 新方案发生 PARSE_FAILED 后数值降级。
- Bulge、Bar、Companion、AGN 等主要成分均有覆盖。

每个案例从同一当前轮起点，分别执行旧方案动作和新方案动作，保持相同数据、mask、PSF、自由度政策和停止条件。比较：

- 2D 系统性或对称残差是否减少。
- 1D profile 残差是否改善。
- BIC 和 reduced chi-square。
- 参数撞界、成分简并和非物理解。
- 新增成分在后续轮次是否稳定保留。
- 是否减少后续为修复错误动作所需的轮次数。

BIC 不能单独决定胜负。建议先判断参数是否崩溃、残差结构是否实质改善、成分解释是否合理，只有这些条件相近时再比较 BIC 和 reduced chi-square。

最终按对象汇总 WIN、TIE、LOSS、INCONCLUSIVE，不按 116 个轮次独立抽样。置信区间或 bootstrap 也应以 object_id 为抽样单位，避免同一对象的多个轮次被过度加权。

推荐主指标是明确错误或有害动作率，第二指标是必要动作覆盖率，第三指标才是受控 refit 胜率。对自动化拟合而言，漏掉一个仍可在下一轮补救的候选，通常比提出错误成分并造成参数简并、错误删除或错误模型替换的代价低。

## 五、建议会议形成的结论

建议会议至少形成以下三项可执行结论：

1. diffraction_psf 是否可以否决 Bar 或中心源证据；允许否决的最小 band、panel、region 和尺度匹配条件是什么。
2. 逐轮人工评审是否采用“必要／可接受／拒绝动作集合”，而不是要求唯一正确动作。
3. 选择哪些 20～30 个新旧分歧轮次进入盲审和 A/B refit，以及接受什么样的 WIN／TIE／LOSS 判定。

## 六、参考文件

- 方案规范与已裁定边界：[redesign.md](redesign.md)，重点是 VLM 契约状态和第六节逐成分规则。
- 本轮完整统计：[shadow-dev-evaluation-report-jwst0716-vlm.md](shadow-dev-evaluation-report-jwst0716-vlm.md)。
- 结构化逐轮复核：[shadow-dev-manual-review-jwst0716-vlm.json](shadow-dev-manual-review-jwst0716-vlm.json)。
- VLM prompt 和 parser：[src/component_analysis/vlm.py](../../src/component_analysis/vlm.py)。
- 数值与规则实现：[src/component_analysis/rules.py](../../src/component_analysis/rules.py)。
- overlay 实现：[src/component_analysis/candidate_overlay.py](../../src/component_analysis/candidate_overlay.py)。
- shadow runner：[src/component_analysis/shadow.py](../../src/component_analysis/shadow.py)。

## 七、讨论结论记录

科学家确认后，只需回填本节：

```text
diffraction_psf 的最小定位要求：
与逐波段 psf_veto 冲突时的优先级：
与已分辨中心过量冲突时的处理：
是否允许跨波段否决：
```
