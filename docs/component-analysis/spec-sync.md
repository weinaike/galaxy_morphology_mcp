# 成分分析规范同步方案

> 目的：记录 `docs/component-analysis/redesign.md` 已确认的目标规则，以及现有规范文件如何同步。
> 状态：接管前迁移方案已确认；仅在 `redesign.md` 实施顺序第 5 步 shadow mode 和第 6 步 held-out benchmark 完成后执行。
> 日期：2026-08-13

## 一、同步边界

本文件只描述实施顺序第 7 步的规范迁移，不修改现有 prompt、workflow、verifier 或代码，也不定义当前重构代码的实施顺序。目标架构、逐成分判据和总实施顺序以 `docs/component-analysis/redesign.md` 为准。

同步原则：

1. 未列入本文件的现有规则继续有效。
2. component specification 定义科学成分和模型映射。
3. workflow 定义执行顺序、状态转换和 `needs_review` 事后批量审查入口（流程内无人工阻塞环节），不重新定义科学阈值。
4. `best-round-verifier` 只机械审计已确认规则，不自行定义成分判据。
5. 代码最后修改，必须能输出证据、规则 ID 和可追溯的动作记录。

## 二、已确认的目标规则

### 1. 正式成分白名单

正式白名单固定为八类（2026-08-13 裁定 Lens 保留并升入白名单）：

```text
Disk、Bulge、Edge-on Disk、Bar、AGN、Fourier m=1、Companion、Lens
```

Lens 的认定与接受条件见 `docs/component-analysis/redesign.md` 第六节第 8 小节（Bar 参数异常＋延展区残差触发，`Re_disk > Re_lens > Re_bar`、`n_lens < 0.5`、`q_lens > 0.5`）。同时裁定：旧规范「Nucleus 代偿 Bulge、物理意义优先于奥卡姆」规则废弃，中心源统一按可分辨尺度门分流，同步时须从现有 prompt 中移除该代偿逻辑。

`compact central source candidate` 不是白名单成分，而是中心源物理身份未确认时的分析状态。

### 2. AGN 与中心源状态

- AGN 是 `galactic nucleus` 的一种物理身份，不等同于所有中心源。
- GalfitS N 块只用于 AGN 模型，因为 N 块包含黑洞、吸积、连续谱、发射线和 torus 等 AGN SED 参数。
- 只有独立 AGN 证据支持时，才能将中心源标记为 AGN 并使用 N 块。
- 只有影像紧致证据时，标记为 `compact central source candidate`，不能报告为 AGN。
- 有独立恒星来源证据时，可标记为 `stellar nucleus`，使用待确认的 P 块紧致模型。

### 3. 中心同心

Disk、Bulge、Bar、AGN 或中心源模型等主星系中心成分，最终 `xcen`、`ycen` 必须完全一致。最终审计以拟合结果为准。

### 4. Disk 的 Sérsic 指数

GalfitS 多波段流程中，Disk 使用 `sersic`；确认 Disk 后，后续拟合固定 `n=1`。单波段 GALFIT 继续使用既有 `expdisk` 规则。

### 5. 尺寸层级

盘星系多成分分解的通常物理期望为：

```text
Re_disk > Re_bar > Re_bulge
```

含 Lens 时为 `Re_disk > Re_lens > Re_bar`，且 `n_lens < 0.5`、`q_lens > 0.5`。

该关系用于成分标签、参数退化和物理合理性检查，不作为无条件硬失败。反置时应检查标签交换、模型简并和数据质量，并记录理由。

## 三、BIC 与奥卡姆原则

### 1. 比较前提

只有在两个模型使用相同的 data region、mask、PSF、sigma、权重定义、波段集合和拟合模式时，BIC 差值才可直接比较。当前单波段和多波段实现的计算细节可能不同，跨定义数值不得直接比较；比较记录必须包含公式、有效数据点数和自由参数数量。

### 2. 定义与默认门槛

对简单模型和复杂模型定义：

```text
BIC_gain = BIC_simple - BIC_complex
```

| `BIC_gain` | 解释 | 默认动作 |
|---:|---|---|
| `<= 0` | 不支持增加复杂度 | 可选成分默认拒绝；主成分不能仅据此删除 |
| `0 < BIC_gain < 10` | 证据不足 | `INCONCLUSIVE`，由自动化 policy 层兜底为拒绝并留痕（`needs_review`） |
| `>= 10` | 复杂模型有较强相对支持 | 仍需满足残差、拟合、参数和物理条件，才接受 |

`BIC_gain >= 10` 是可选成分的默认统计门槛，不是充分条件，也不是 AGN 身份证明。

### 3. 成分作用域

- Disk、Bulge、Edge-on Disk、Bar、Fourier m=1：不能仅因 BIC 变差删除。主要依据为原图／残差结构、物理角色和拟合后的参数合理性。
- AGN、`compact central source candidate`、Companion、Lens：适用更严格的复杂度门。默认要求 `BIC_gain >= 10`，并同时满足局部残差改善、拟合收敛、参数未退化和相应物理证据。
- 独立证据确认的 AGN 不能因为影像 BIC 不支持就被改写为“非 AGN”；只能单独评估当前影像模型是否加入 N 块。
- BIC 只能在 `EVALUATE_REFIT` 阶段使用，不能在 `PROPOSE` 阶段证明尚未拟合的成分存在。

## 四、文件同步矩阵

| 文件 | 需要同步的内容 | 状态 |
|---|---|---|
| `src/prompts/component_specification_galfits.md` | 八类白名单（含 Lens）、AGN／中心源状态、GalfitS Disk `n=1`、中心同心、BIC 作用域、Lens 认定规则 | 待执行 |
| `src/prompts/workflow_galfits.md` | 八类搜索目标、`PROPOSE → REFIT → EVALUATE_REFIT`、BIC 只在重拟合后使用、`INCONCLUSIVE` 自动化消解策略（试拟合仲裁／保守兜底，`needs_review` 留痕，无人工阻塞） | 待执行 |
| `.claude/agents/best-round-verifier.md` | 中心同心硬规则、尺寸层级非无条件 FAIL、AGN N 块及独立证据、可选成分 `BIC_gain >= 10` | 待执行 |
| `src/prompts/residual_analysis_message.md` | 将中心源候选与 AGN 物理身份分开，统一奥卡姆适用范围和 `BIC_gain` 定义，移除「Nucleus 代偿 Bulge、物理意义优先于奥卡姆」逻辑 | 待执行 |
| `src/prompts/residual_analysis_prompt.md` | 结构化决策输出中的 `physical_role`、`resolved_state`、`physical_identity` | 待执行 |
| `src/tools/*.py` | 生成 numeric evidence、VLM evidence、decision artifact 和 rule trace；统一记录 BIC 定义 | 待执行 |
| 测试与评测集 | 覆盖中心源身份、BIC 边界、主成分不误删、尺寸反置审计 | 待执行 |

## 五、第 7 步执行时的文件同步顺序

前置条件：`redesign.md` 实施顺序第 1～6 步已完成，held-out benchmark 已给出阈值校准和错误案例审查结果。

1. 同步 `component_specification_galfits.md` 和单／多波段成分术语。
2. 同步 `workflow_galfits.md` 的状态机和 BIC 使用阶段。
3. 同步 `residual_analysis_message.md`、`residual_analysis_prompt.md` 的输出契约。
4. 同步 `best-round-verifier.md`，确保审计规则与 specification、workflow 完全一致。
5. 补齐正式接入所需的工具层代码和回归测试，但仍不自动接管动作决策。
6. 通过回归测试与科学家评审后，才按 `redesign.md` 第 8 步让新规则层接管正式动作决策。

## 六、验收标准

- 八类白名单（含 Lens）在 specification、workflow、decision artifact 和 verifier 中完全一致。
- Lens 只在 Bar 参数异常＋延展区正残差同时成立时提议，按可选成分 `BIC_gain >= 10` 门审计。
- 现有 prompt 中「Nucleus 代偿 Bulge」逻辑已移除，中心源统一走可分辨尺度门分流。
- `compact central source candidate` 不会被自动写成 AGN，也不会被当成第八类最终成分。
- AGN 只有在独立证据存在时才映射到 N 块。
- 中心成分最终 `xcen`、`ycen` 必须完全一致。
- GalfitS Disk 的 `n=1` 和单波段 GALFIT 的 `expdisk` 没有互相覆盖。
- 可选成分未达到 `BIC_gain >= 10` 时不会自动接受；达到后仍必须通过残差、参数和物理条件。
- 尺寸层级反置会触发标签／简并复核，但不会仅因反置自动 `FAIL`。
- 每个自动动作都能追溯到 schema version、rule ID、输入文件和具体证据。
- `INCONCLUSIVE` 全部由自动化 policy 层消解：decision artifact 携带 `automation` 块（policy 版本、原动作、消解方式、理由、`needs_review`），流程内无人工阻塞；带 `needs_review` 标志的决策可批量导出供事后审查。
