# 成分分析模块重构方案

> 面向：星系拟合 workflow 中 `analyze_multiband_components`（阶段二步骤 2）的成分增删决策模块
> 状态：v6（2026-08-13），三层架构与本轮规则边界已确认；第十一节开放问题已全部裁定为 v1 默认规则（待评测校准），Lens 保留入白名单、「Nucleus 代偿 Bulge」废弃；四类 artifact schema 已冻结 v1.0（见 `src/schemas/`）；新增第四节「INCONCLUSIVE 自动化消解策略」，流程全自动运行、人工复核改为事后批量审查
> 核对范围：`workflow_galfits.md`、`component_specification_galfits.md`、`residual_analysis.py`、`bar_lopsidedness_core.py`、`best-round-verifier.md`、jwst0709/0710/0716 拟合产物

---

## 一、已确认的总体方向

保留三层架构，但重新明确职责边界：

```text
第 1 层：数值证据层
  只回答“测到了什么”，不回答“是什么成分”，不直接产生动作。
  输出连续测量值、质量标志、数据来源和可用性。

第 2 层：VLM 形态证据层
  只回答“候选区域呈现什么形态”，不生成坐标，不直接产生动作。
  输出受控标签、置信度、证据区域和 uncertain。

第 3 层：确定性规则与迭代控制层
  合并数值证据、VLM 标签、历史轮次和项目规范，产生唯一动作。
  负责“提议 → 重拟合 → 事后仲裁 → 接受/回退”的状态转换。
```

三层架构的核心不是“尽量去掉 VLM”，而是把不同类型的判断交给适合的方法：

- 可重复测量、可定义误差的量交给数值方法。
- 形态语义和混淆类型交给 VLM，但限定标签空间和证据位置。
- 成分增删、优先级、阈值和回退由代码规则统一决定。
- 任一层证据不足时返回 `INCONCLUSIVE`，不得把“无法判断”静默解释为“不存在”；但可以由第四节的自动化消解策略把它显式地、有记录地映射为一个确定性默认动作，保证流程全自动完成。

### 已确认的成分白名单

正式成分白名单为八类（2026-08-13 裁定 Lens 保留并升入白名单）：

```text
Disk、Bulge、Edge-on Disk、Bar、AGN、Fourier m=1、Companion、Lens
```

`compact central source candidate` 不是白名单成分，而是“发现未分辨中心源、物理身份尚未确认”的分析状态。它不能出现在最终成分列表中，也不能仅凭影像证据被报告为 AGN。

---

## 二、当前问题及严重度

### S0：实施前必须解决

#### S0-1：缺少数值层的数据契约

当前 `analyze_multiband_components` 的显式输入是 lyric、gssummary 和 comparison PNG。拟议的残差矩、PSF FWHM、局部源检测和 WCS 对齐实际需要逐波段 result FITS、science FITS、sigma、mask、PSF、拟合区域及可选 catalog。

如果不先冻结数据契约，第 1 层 schema 无法稳定实现，也无法区分“未检出”与“数据不可用”。

#### S0-2：成分提议与 BIC 仲裁发生在不同时间

当前轮残差只能提出候选成分；新增成分后的 BIC、残差改善和物理参数必须在下一次拟合完成后才能得到。因此第 3 层必须是迭代状态机，不能把“有无判定”和“BIC 接受”写成一次同步决策。

#### S0-3：点源主判据在当前 GalfitS 参数化下不可识别

GalfitS 的 P 块 `Pa5` 是共享的内禀 `Re_arcsec`。当前代码只是按各波段 pixel scale 把同一个 `Re_arcsec` 转成不同的 `Re_pix`，因此不能使用“拟合 `Re_arcsec` 随 PSF FWHM 变化”作为点源判据。

点源应理解为经过 PSF 卷积后的 unresolved source，而不是令内禀 `Re = PSF FWHM`。替代方案见第六节。

#### S0-4：现有规范存在不一致，需要先冻结目标规则

现有文件之间确实存在不一致，但不应把所有不一致都视为新架构冲突。本方案默认继承未被明确修改的现行规则，只同步以下已经确认的目标规则：

1. **AGN、致密中心源与 N 块：** AGN 的物理身份与未分辨中心源候选分开；GalfitS 的 N 块仅用于 AGN 模型。中心源候选只有独立 AGN 证据时才能标记为 AGN。
2. **中心同心：** 采用现有表述 B。Disk、Bulge、Bar、AGN 或中心源模型等主星系中心成分的最终 `xcen`、`ycen` 必须完全一致；verifier 按最终结果核查。
3. **GalfitS Disk 的 n：** 采用现有 GalfitS 成分规范，Disk 使用 `sersic`，确认后将 `n` 固定为 1。单波段 GALFIT 的 `expdisk` 规则不因本方案改变。
4. **BIC／奥卡姆：** 见下文“BIC 与复杂度规则”。BIC 只在重拟合完成后比较，不能在 `PROPOSE` 阶段证明成分存在；主成分不得仅因 BIC 变差删除，可选成分默认要求 `BIC_gain >= 10`，并同时通过残差、拟合、参数和物理证据。
5. **成分白名单：** 采用八类白名单：Disk、Bulge、Edge-on Disk、Bar、AGN、Fourier m=1、Companion、Lens（2026-08-13 裁定 Lens 保留）。
6. **尺寸层级：** `Re_disk > Re_bar > Re_bulge`（含 Lens 时为 `Re_disk > Re_lens > Re_bar`，且 `n_lens < 0.5`、`q_lens > 0.5`）保留为盘星系多成分分解的通常物理期望和标签检查依据，不升级为无条件硬失败。反置时必须检查成分标签交换、模型简并和数据质量，并在工作记录中说明；不能仅凭尺寸反置自动删除成分。

这些目标规则的文件级迁移记录见 `docs/component-analysis/spec-sync.md`。在同步执行前，不修改现有 component specification、workflow、verifier 或代码。

### S1：会显著影响科学可靠性

#### S1-1：原方案把 VLM 收窄到两个判别点，范围过窄

Disk 与椭圆星系、Bar 与旋臂/尘埃/衍射芒、Companion 与团块/前景星等混淆，仅靠单一数值特征无法可靠区分。VLM 应保留受控的“形态消歧”职责，但不得直接决定动作或生成坐标。

#### S1-2：残差二阶矩不能单独主判 Bar

全局二阶矩只能证明残差有拉长方向，不能证明它是 Bar。盘欠拟合、伴源、尘埃、PSF 失配和衍射结构都可能产生拉长残差。Bar 判据必须组合原图等照度线、径向 m=2、PA 稳定性、分辨率和 VLM 形态标签。

#### S1-3：catalog 不能作为 Companion 的权威否决

catalog 可能不完备、deblend 失败或深度不足。没有 catalog 匹配不等于没有 Companion。catalog 只能提供佐证，候选坐标必须来自数值源检测，VLM 只判断编号候选的形态类别。

#### S1-4：AGN 与未分辨中心源候选的物理身份必须分开

AGN 是 `galactic nucleus` 的一种物理身份，但 `galactic nucleus` 不等同于 AGN。仅凭影像中的未分辨中心光源，不能证明其为 AGN。方案统一如下：

- 有独立 AGN 证据（如光谱、SED、X-ray、变源或可靠外部 catalog）时，物理身份标记为 `AGN`，使用 N 块。
- 只有影像紧致中心证据、没有 AGN 物理证据时，标记为 `compact central source candidate`，不能在报告中宣称 AGN。
- 若有独立证据支持核星团等恒星来源，可标记为 `stellar nucleus`，使用 P 块紧致模型。
- `compact central source candidate` 是分析状态，不是第八类物理成分；其 P 块 profile 已裁定为小 Re Sérsic（GalfitS 不支持 psf／Gaussian profile，见既往实测记录），升级 N 块 AGN 的条件本轮不展开。

### S2：不阻塞原型，但影响评测与维护

- VLM 缺少严格 JSON schema、`uncertain` 和解析失败策略。
- 成分目前主要靠名称识别，缺少独立的 `physical_role` 字段。
- “约 100 倍加速”“准确率免费可得”尚无 benchmark，应降级为待验证假设。
- 验证集缺少 held-out 划分、困难负样本、人工一致性和错误成本指标。
- 参数撞界不能只用浮点值相等判断，需要读取上下界、vary 状态、约束来源和容差。

---

## 三、三层输入输出契约

### 第 1 层：数值证据层

#### 输入：`artifact_manifest`

```json
{
  "schema_version": "1.0",
  "round_id": "...",
  "lyric_file": "...",
  "summary_file": "...",
  "bands": [
    {
      "band": "nircam_f200w",
      "science_fits": "...",
      "science_hdu": 0,
      "result_fits": "...",
      "residual_hdu": 0,
      "mask_hdu": 1,
      "sigma_hdu": 2,
      "model_hdu": 3,
      "original_hdu": 4,
      "psf_fits": "...",
      "psf_hdu": 0,
      "pixscale_arcsec": 0.03,
      "fit_region": [0, 100, 0, 100]
    }
  ],
  "catalog": {
    "path": null,
    "format": null,
    "available": false
  }
}
```

manifest 由拟合产物解析器生成，分析工具不通过目录 glob 猜测文件。每个字段都需要验证路径、HDU、shape、WCS、单位和有限值比例。

#### 输出：`numeric_evidence`

第 1 层只输出事实型特征，所有特征都包含：

- `value`：测量值。
- `uncertainty`：可计算时给出误差或 bootstrap 区间。
- `status`：`AVAILABLE | UNAVAILABLE | INVALID`。
- `source`：波段、HDU、坐标系和算法版本。
- `quality_flags`：低 SNR、mask 占比高、PSF 欠采样、拟合失败等。

禁止输出 `bar_candidate`、`add_bar` 这类推断和动作字段。

### 第 2 层：VLM 形态证据层

VLM 输入 comparison PNG 及由数值层标出的候选区域 ID。候选坐标由数值算法产生，VLM 不允许自行创造坐标。

VLM 输出严格 JSON：

```json
{
  "schema_version": "1.0",
  "observations": [
    {
      "target_id": "central_or_candidate_1",
      "label": "disk_like | spheroid_like | central_compact_excess | bar_like | peanut_x | edge_on_disk | spiral_arm | dust_lane | independent_source | clump | diffraction_psf | none | uncertain",
      "confidence": 0.0,
      "evidence_regions": ["band:panel:region_id"],
      "quality_flags": []
    }
  ]
}
```

规则：

- 必须允许 `uncertain`。
- 只返回受控标签，不输出自然语言动作。
- 不输出 `add_*`、`remove_*`、初始参数或坐标。
- 低质量图像、标签冲突或 JSON 解析失败时进入 `INCONCLUSIVE`。

### 第 3 层：规则与迭代控制层

输入：

- `numeric_evidence`
- `vlm_evidence`
- 当前模型的 `physical_role + profile_type`
- 历史轮次状态
- 项目规则版本和阈值版本

输出：

```text
PROPOSE_ADD_<component>
PROPOSE_REPLACE_<from>_WITH_<to>
PROPOSE_REMOVE_<component>
REFIT_PARAMETERS
KEEP_AND_CONTINUE
CONVERGED
INCONCLUSIVE
```

每次最多输出一个改变成分结构的动作，并附带机器可读的 rule ID、输入证据和未满足条件。

---

## 四、迭代状态机

```text
CURRENT_FIT
  │
  ├─ 第 1 层提取数值证据
  ├─ 第 2 层提取形态标签
  ▼
PROPOSE
  │  第 3 层选择一个候选动作
  ▼
REFIT_REQUIRED
  │  执行 run_galfits_image_fitting
  ▼
EVALUATE_REFIT
  │  比较新增前后残差、参数物理性、收敛和二级指标
  ├─ ACCEPTED ──> 以新轮为当前轮，继续搜索
  ├─ REJECTED ──> 回退候选前模型，记录否决证据
  └─ INCONCLUSIVE ──> 按下文自动化消解策略处理，不静默接受或删除
```

BIC 只能用于 `EVALUATE_REFIT`，不能在 `PROPOSE` 阶段证明一个尚未拟合的成分存在。

### INCONCLUSIVE 自动化消解策略（v1，2026-08-13 裁定）

拟合流程必须全自动完成，不设人工阻塞环节。`INCONCLUSIVE` 保留为证据层的诚实状态，但在第 3 层之上叠加一个确定性的 policy 层（`automation-policy@v1`），把每一种 `INCONCLUSIVE` 映射到唯一默认动作并留痕。policy 层不修改规则函数，只包装其输出；消解结果写入 decision artifact 的 `automation` 块（policy ID、原动作、消解理由、`needs_review` 标志）。

三类消解方式：

1. **试拟合仲裁（可实验裁决的证据冲突）：** 降级为弱候选照常提议，由 `EVALUATE_REFIT` 的残差／参数／BIC 门自动裁决接受或回退。适用：Disk 证据部分成立（`DISK_AMBIGUOUS_EVIDENCE_V1`）、Edge-on 低 q 但 VLM 未确认（`EDGE_ON_LOW_Q_V1`）、中心源跨波段分辨冲突（`CENTRAL_RESOLUTION_CONFLICT_V1`，试拟合取 Bulge 路径）、Companion 数值强但 VLM 不确定（`COMPANION_NUMERIC_VLM_V1`）。防护：每个星系设试探预算（默认最多 3 次弱候选试拟合）；被 `REJECT_REFIT` 否决过的成分在证据无实质变化时不得重复提议。
2. **保守默认兜底（数据不可用或已知高风险混淆）：** 不改变成分结构（等价 `KEEP_AND_CONTINUE`），当前模型即为该轮产出。适用：质量门全部未过（`CENTRAL_RESOLUTION_QUALITY_V1`）、尘埃／衍射污染（`CENTRAL_MORPHOLOGY_CONFLICT_V1`、`BAR_DIFFRACTION_CONFLICT_V1`，已知衍射芒误判 Bar 的回归案例要求保守）、m=1 混淆（`FOURIER_M1_CONFOUNDING_V1`）、数值与 VLM 强冲突（`DISK_VLM_CONFLICT_V1`）、Lens 仅参数异常无残差证据（`LENS_BAR_ANOMALY_V1`、`LENS_COMPANION_CONFLICT_V1`）。VLM 全断（`VLM_UNAVAILABLE_V1`）时降级为纯数值路径：以中性 VLM 证据重跑规则层，此时只有满足更严数值组合的证据才能触发动作。`EVALUATE_REFIT` 阶段的 `INCONCLUSIVE`（主门不确定或 BIC 不可比）兜底为 `REJECT_REFIT`，回退到候选前模型；其中「BIC 不可比」应通过数值层按统一口径自行重算两轮 BIC 在工程上消灭（待办）。
3. **留痕替代人工阻塞：** 每次自动兜底置 `needs_review = true` 并记录触发原因；最终分析报告汇总本星系所有被自动兜底的 `INCONCLUSIVE`，人工改为事后批量审查带标志的少数星系。评测集阶段统计「自动兜底决策与科学家判断的一致率」，用数据决定哪些兜底策略需要收紧。

收敛防护：同一 rule ID 在证据无实质变化时第二次返回 `INCONCLUSIVE`，即触发保守兜底并标记该问题已终结，本星系不再重试，避免迭代原地踏步。试探预算、重复阈値均为 v1 默认値，待评测校准。

### BIC 与复杂度规则

BIC 仅在以下条件同时满足时用于模型间比较：数据区域、mask、PSF、sigma、权重定义、波段集合和拟合模式一致。当前单波段与多波段实现的 BIC 计算细节可能不同，因此跨定义的数值不得直接比较；每次比较必须记录公式、数据点数和自由参数数目。

对同一数据契约下的简单模型与复杂模型，定义：

```text
BIC_gain = BIC_simple - BIC_complex
```

默认解释和动作规则：

| `BIC_gain` | 统计解释 | 规则动作 |
|---:|---|---|
| `<= 0` | 不支持增加复杂度 | 可选成分默认拒绝；主成分不能仅据此删除 |
| `0 < BIC_gain < 10` | 证据不足 | 返回 `INCONCLUSIVE`，由第四节自动化策略兜底（`EVALUATE_REFIT` 默认拒绝该可选成分并留痕） |
| `>= 10` | 复杂模型获得较强相对支持 | 仅在残差改善、拟合成功、参数未退化且物理证据通过时，才接受可选成分 |

`BIC_gain >= 10` 是可选成分的默认统计门槛，不是充分条件，也不是 AGN 物理身份的证明。该门槛主要适用于 AGN、`compact central source candidate`、Companion 和 Lens；Disk、Bulge、Edge-on Disk、Bar、Fourier m=1 等主结构不得仅因 BIC 变差而删除。独立证据确认的 AGN 即使影像 BIC 支持不足，也不能因此改写其物理身份；此时只能单独评估是否在当前影像模型中加入 N 块。

仲裁优先级暂定为：

1. 拟合是否成功，参数是否收敛且未出现阻断性异常。
2. 候选成分对应的局部和全局残差是否按预期改善。
3. 拟合后参数是否符合该成分的物理关系。
4. BIC、reduced χ² 等统计指标作为二级证据。

对 Disk、Bulge、Edge-on Disk、Bar、Fourier m=1，不允许只因 BIC 变差而删除；对 AGN、`compact central source candidate`、Companion、Lens 等可选成分，采用上述复杂度门槛，但 BIC 仍不是充分条件。

---

## 五、数值方法与 VLM 的分工原则

### 数值方法负责

- 精确坐标、半径、轴比、PA、FWHM、SNR、振幅和跨波段离散度。
- 逐像素 sigma 加权统计与 mask 处理。
- 参数是否 fixed/free、是否接近边界、是否 NaN、是否未收敛。
- 候选区域生成和跨波段 WCS 匹配。
- 当前轮与重拟合轮的同口径残差、BIC 和 χ² 对比。
- 可复现的质量门和阈值判断。

### VLM 负责

- Disk-like 延展、侧视结构、花生/X 型、棒状、旋臂、尘埃带等形态语义。
- 区分编号候选更像独立源、盘内团块、衍射/PSF 结构还是不确定。
- 在多个数值解释都成立时提供受控弱证据。

### VLM 不负责

- 生成候选坐标和参数数值。
- 单独判定 AGN 的物理身份。
- 单独决定成分增删。
- 覆盖数值层的无效数据标志。

### 第 3 层负责

- 将证据组合成候选动作。
- 决定证据优先级、阈值和冲突处理。
- 保证一次只改变一个成分。
- 执行新增后的事后仲裁和回退。

---

## 六、逐成分判据讨论稿

以下判据用于与科学家对齐。阈值先保留在规则配置中，完成评测前不硬编码为科学事实。

### 1. Disk

#### 数值证据

- 原图存在超过 PSF 尺度的连续延展光分布。
- 外层等照度线在一段径向范围内具有相对稳定的中心、q 和 PA。
- 单 Sérsic 或现有非 Disk 模型后，外层径向残差存在连续、同号、系统性偏离。
- 可选：一维剖面存在需要外层近指数成分解释的结构，但不把单一 Sérsic `n≈1` 当作充分证据。

#### VLM 证据

- 延展盘、旋臂或盘状包络。
- 区分盘结构与单纯椭圆光球、潮汐尾或不规则污染。

#### 提议规则（v1 最低证据组合，已裁定，阈值待 dev set 校准）

数值证据编号：

- N1 延展性：原图光分布延展到 `>= 3×PSF FWHM`，且外层区域通过质量门。
- N2 盘几何：外层等照度线在连续径向区间内 q、PA 稳定（默认 PA 抖动 `< 20°`、q 变化 `< 0.15`），或自由 n 单 Sérsic 拟合得到 `n <= 2.5` 且未触界。
- N3 外层残差：现有非 Disk 模型后，外层径向残差连续、同号、系统性偏离。

最低组合：

- 提议 Disk：N1 必备；VLM 为 `disk_like`／`spiral_arm`／`edge_on_disk` 时，N2、N3 至少一项成立；VLM 为 `uncertain`／`none` 时，N2、N3 必须同时成立。
- 判为椭圆光球（不加 Disk）：N1 成立但 N2、N3 均不成立，自由 n 单 Sérsic `n >= 3` 且未触界，且 VLM 为 `spheroid_like` 或 `uncertain`。此时主体记为单 Sérsic 光球（`physical_role = bulge`）。
- 数值与 VLM 强冲突（数值支持盘但 VLM 高置信 `spheroid_like`，或反向），或 `2.5 < n < 3`、N2 与 N3 结果矛盾时，返回 `INCONCLUSIVE`，保持自由 n 单 Sérsic，按第四节自动化策略消解并留痕；不得默认 Disk 或 Elliptical。

#### 重拟合后接受条件

- Disk 参数稳定，n 按项目规范固定为 1。
- 外层残差按预期改善，且没有通过异常 Re、q 或 sky 偏移吸收背景。
- 对盘星系仍需按项目规范探索 Bulge；BIC 只作二级证据。

### 2. Bulge

#### 数值证据

- Disk 建立后，多个有效波段中心出现同心、近轴对称的连续正残差。
- 中心径向 profile 存在宽于 PSF 核的系统性过量，而不是单像素峰或 PSF 环。
- 候选尺度可分辨（按第 5 小节的可分辨尺度门判定），且与 Disk 的尺度、q 或 profile 形状存在可解释差异。

#### VLM 证据

- 中心平滑、同心、近轴对称的延展亮区。
- 用于排除明显的 Bar、尘埃遮挡、PSF 衍射结构或偏心团块。

#### 提议规则

- 数值中心过量跨波段成立，且不呈点源特征时，提议 Bulge。
- 只有 VLM 报告“中心较亮”不足以提议 Bulge。

#### 重拟合后接受条件

- `Re_bulge` 可分辨且小于 Disk，中心与 Disk 同心。
- n、q、Re 未触界，和 Disk/Bar 不构成明显参数等价。
- 中心残差改善；BIC 不作为单独删除 Bulge 的依据。

### 3. Edge-on Disk

#### 数值证据

- 外层 q 低于规则阈值。当前 `b/a < 0.17` 只作为待校准触发值，不直接定性。
- 延展尺度显著大于 PSF，主轴 PA 在外层稳定。
- 可选：垂直与径向剖面支持薄盘结构。

#### VLM 证据

- 明确的侧视盘、薄盘或垂直厚度结构。
- 尘埃带可作为侧视结构证据，但不能单独证明需要发光成分。

#### 提议规则

- 数值低 q 触发后，VLM 确认为 `edge_on_disk` 才提议将普通 Disk 替换为 `edgeondisk`。
- 低 q 但 VLM 不确定时保留普通 Disk 并标记复核。

#### 重拟合后接受条件

- edgeondisk 的径向和垂直尺度物理合理。
- 主轴残差和垂直残差改善，且没有异常挤压其他中心成分。

### 4. Bar

#### 数值证据

- 原图等照度线出现径向 ellipticity 上升、Bar 区间 PA 稳定、外侧 ellipticity 下降或 PA 转折。
- 中心残差在限定径向区间具有显著 m=2 或拉长信号，且方向不等同于单纯外盘欠拟合。
- Bar 候选半长轴相对 PSF 有足够分辨率，低 SNR 或欠采样波段不得投强证据。
- PSF 模板或衍射方向相关性用于否决仪器结构；衍射方向必须由每个观测的 PSF/V3 方向得到，不能硬编码为图像坐标 `0/45/90°`。

#### VLM 证据

- `bar_like`、`peanut_x` 或与盘相连的中心长条结构。
- 区分旋臂、尘埃带、独立伴源和 diffraction/PSF 结构。

#### 提议规则

- 已裁定：允许单个通过质量门的高质量波段的强等照度证据触发 Bar 候选，无需第二类独立证据；其余波段是支持、反对还是不可用必须逐波段记录。
- 高质量波段的等照度 Bar 检测成立，且无 PSF/衍射否决时，可提议 Bar；VLM 用于确认形态和发现冲突。
- 原图检测未成立时，必须同时具备局部数值拉长证据和 VLM `bar_like/peanut_x`，才进入弱候选。
- 不再采用“所有波段无质量门的 OR-logic”；只有通过质量门的波段可以贡献证据。

#### 重拟合后接受条件

- n 固定为 0.5，q、PA、Re 和方向符合候选证据。
- 中心条状或 X 型残差改善，且 `Re_disk > Re_bar`。
- 与 Bulge 的物理角色可区分；BIC 仅为二级证据。

### 5. AGN 与未分辨中心源候选

#### 数值证据

- 中心观测 FWHM、encircled-energy 或径向 profile 与同波段 PSF 比较。
- 中心残差在多个有效波段位置一致，且排除 PSF 错配、饱和和 cosmic ray。
- 共享 P 块 `Re_arcsec` 只能用于判断是否触界或不可分辨，不能用于计算“Re 与 PSF 的跨波段相关性”。
- AGN 身份需要额外的 SED、光谱、X-ray、catalog 或其他独立物理证据。

#### VLM 证据

- VLM 只可报告 `central_compact_excess` 或 PSF-like 形态，不能把它命名为 AGN。
- 用于识别中心残差是否被尘埃、衍射或错中心显著污染。

#### 可分辨尺度门（v1 已裁定，待注入-恢复实验校准）

Bulge 与 `compact central source candidate` 的分界逐波段判定。对中心过量（Disk 等外层成分扣除后）测量观测 `FWHM_obs`，与同波段 PSF 的 `FWHM_psf` 做平方差反卷积：

```text
FWHM_int = sqrt(max(FWHM_obs^2 - FWHM_psf^2, 0))
```

- 强证据分辨门：`FWHM_int >= 0.5×FWHM_psf` 且中心孔径（1×PSF FWHM）内 `SNR >= 20` 时，该波段判 `resolved`。
- `10 <= SNR < 20`：该波段只能投弱证据（`resolved_weak`／`unresolved_weak`）。
- `SNR < 10` 或 PSF 欠采样（`FWHM_psf < 2 px`）：该波段 `UNAVAILABLE`，不投证据。

跨波段融合：

- 至少一个高质量波段 `resolved`（优先最高分辨率波段）：判可分辨，走 Bulge 或 stellar nucleus 路径。
- 所有通过质量门的波段均 unresolved：判 `compact central source candidate`。
- 两个分辨率相当的高质量波段强证据冲突：返回 `INCONCLUSIVE`，记录逐波段证据表。

拟合参数指纹降级为二级检查：拟合 `Re_arcsec` 换算到各波段像素后全部 `< 0.2 px` 支持 unresolved；`Re` 触下界时必须放宽下界重拟，不得直接判点源。`0.5×FWHM_psf` 与 SNR 门槛的依据是 FWHM 测量误差随 SNR 的量级估计，属 v1 默认值，进入规则配置由 dev set 注入-恢复实验校准。

#### 提议规则

- 中心源已解析：提议 P 块 Sérsic Bulge 或 stellar nucleus，具体物理角色由尺度与上下文决定。
- 中心源未解析且只有影像证据：提议 `compact central source candidate`，profile 已裁定为 P 块小 Re Sérsic（GalfitS 不支持 psf／Gaussian profile）。
- 中心源未解析且有独立 AGN 证据：提议 N 块 AGN。
- 不允许仅凭小 Re 或 VLM 标签直接提议 N 块 AGN。

#### 重拟合后接受条件

- P 块中心源候选：中心残差改善、位置同心、profile 参数未通过触界制造伪点源。
- N 块 AGN：中心残差与 SED/光谱证据共同改善，关键 AGN 参数未无意义触界。
- 可选中心成分默认要求 `BIC_gain >= 10`，并同时通过中心残差改善、拟合收敛、参数未触界和物理身份证据；该门槛不是 AGN 身份的充分条件。

### 6. Lopsidedness / Fourier m=1

#### 数值证据

- 原图等照度 Fourier A1 与中心漂移检测。
- 当前模型残差的噪声加权 m=1 振幅和相位一致性。
- 排除邻近 Companion、mask 不对称和背景梯度造成的伪 m=1。

#### VLM 证据

- 不参与有无主判。
- 只用于标记明显 Companion、潮汐结构或污染，作为数值证据的混淆标志。

#### 提议规则

- 原图 detect 通过质量门后，优先提议在 Disk 上添加 m=1。
- 原图未检出但残差 m=1 稳定时，作为后期弱候选。

#### 重拟合后接受条件

- F1 只能作用于 Disk。
- 参数收敛、残差改善且没有由 Companion 或背景错误解释。
- 当两个轮次仅差 F1 且残差质量相近时，沿用项目规则：amplitude `> 0.02` 保留含 F1 的轮次。

### 7. Companion

#### 数值证据

- 在原图和残差图中进行 mask-aware 局部源检测，坐标全部由数值层给出。
- 候选需满足局部 SNR、独立峰、分割连通区、中心距离和影响范围条件。
- 通过 WCS 进行跨波段位置匹配，记录检测波段数和位置散布。
- catalog 匹配是正向佐证；未匹配不能直接否决。

#### VLM 证据

- 对编号候选判断 `independent_source | clump | diffraction_psf | uncertain`。
- 区分独立椭圆/圆形源、盘内恒星形成团块、潮汐结构和 PSF 伪影。

#### 提议规则

- 数值候选显著，原图有对应源，且 VLM 为 `independent_source` 时提议 Companion。
- 数值强但 VLM 不确定时进入 `INCONCLUSIVE`；VLM 单独发现但数值层没有候选时不得添加。

#### 重拟合后接受条件

- 局部残差显著改善，位置没有异常漂移，Re、q、n 和通量合理。
- 不通过扩大 Re 或移动到主星系中心吸收主星系结构。
- Companion 属可选复杂成分，BIC 可作为较强的二级门，但不能替代局部残差和物理检查。

### 8. Lens

继承旧规范的 Lens 认定经验（2026-08-13 裁定保留），迁移为数值主判：

#### 数值证据

- Bar 已存在且拟合后参数出现物理异常：`Re_bar` 接近或超过 `Re_disk`，或 `q_bar > 0.5`。
- 中心星系延展区仍残留平滑、近轴对称的正残差，无法由现有 Disk/Bar 吸收。

#### VLM 证据

- 不参与有无主判；仅当高置信 `independent_source`（延展残差实为伴源）时作混淆否决。

#### 提议规则

- Bar 参数异常与延展区正残差同时成立时，提议将 Bar 拆分为 Bar + Lens（Lens 用低 n Sérsic 建模）。
- 仅有 Bar 参数异常、无延展区残差证据时返回 `INCONCLUSIVE`（可能是简并或标签交换，需复核）。
- 无 Bar 的模型不单独提议 Lens。

#### 重拟合后接受条件

- `Re_disk > Re_lens > Re_bar`、`n_lens < 0.5`、`q_lens > 0.5`，且 Bar 参数回归物理（`q_bar` 降回 0.5 以下）。
- Lens 属可选成分，默认要求 `BIC_gain >= 10` 并同时通过残差与参数条件。

---

## 七、跨波段证据融合

不再使用无条件 OR。每个波段先经过质量门：

- PSF 是否可用、是否欠采样。
- 中心或候选区域的有效像素比例。
- 局部 SNR。
- mask 覆盖比例。
- WCS 和单位是否有效。
- 拟合是否成功、残差 FITS 是否完整。

通过质量门的波段再参与证据融合：

- 一个高质量波段的强证据可以触发候选，但必须记录其他波段是支持、反对还是不可用。
- 低质量波段只允许提供弱证据，不允许单独触发动作。
- 跨波段冲突不能压缩成简单布尔值，规则层必须保留逐波段证据表。

---

## 八、参数异常与简并诊断

### 参数边界

撞界判断必须同时读取：

- 拟合值。
- lyric 中的 min/max/vary。
- constrain/prior 来源。
- 参数单位和 profile 类型。

使用相对和绝对容差判断“接近边界”，不能只比较 `value == bound`。fixed 参数不计为撞界，但必须单独报告固定原因。

### 简并

简并不使用单一“参数相近”布尔值。至少记录：

- 成分图像或径向 profile 的相似度。
- Re、q、PA、中心和通量占比的接近程度。
- 重拟合初值变化后物理角色是否交换。
- covariance 或多初值拟合稳定性，若 GalfitS 能提供。

规则层可以输出 `degeneracy_warning`，但删除成分仍需通过对应成分的重拟合后仲裁。

---

## 九、验证计划与通过标准

### 1. 单元测试

- artifact manifest：路径、HDU、shape、WCS、单位和缺失数据状态。
- 数值特征：合成已知矩、m=1/m=2、局部峰、FWHM 和 PSF 结构。
- 规则层：每条 rule 使用纯 JSON fixture 测试 `PROPOSE/ACCEPT/REJECT/INCONCLUSIVE`。
- VLM parser：合法 JSON、未知标签、缺字段、冲突和超时降级。

### 2. 科学正确的前向合成测试

- 点源使用 delta/unresolved profile 经每波段 PSF 卷积，不使用 `Re = PSF FWHM`。
- 延展源使用不同内禀 Re 的 Sérsic，经同一 PSF 管线卷积。
- Bar、Disk、Bulge、F1 和 Companion 使用组合前向模型，并改变 SNR、分辨率、mask 和背景。
- 衍射伪影直接使用实际或模拟 PSF，不只生成固定角度十字。

### 3. 已知失败案例回归

| 样本类型 | 来源 | 验证目标 |
|---|---|---|
| 衍射芒误判 Bar | `galfits-diffraction-spike-bar-falsepositive` | 新方案不再误加 Bar |
| 捏造 Companion | `galmcp-analyzer-companion-hallucination` | 无数值候选坐标时 VLM 无法触发添加 |
| 核心残差矛盾归因 | `galmcp-analyzer-core-residual-contradiction` | 尘埃/紧致核冲突返回 `INCONCLUSIVE` |
| PA=nan 伪影 | `galmcp-analyzer-pa-nan-artifact` | 无效 PA 不参与跨波段融合 |
| 紧致盘 Disk/Bulge 简并 | `galfits-degenerate-compact-diskbulge` | 正确标记不可分辨或简并，不强制分类 |
| N 块 AGN 触界 | obj_163 | 验证 AGN 证据和 N 块参数边界策略 |

### 4. Held-out 评测集

按星系划分 train/dev/test，不能把同一星系的不同波段或不同拟合轮次分到不同集合。评测集需覆盖：

- 每种成分的正样本、负样本和困难负样本。
- 低 SNR、PSF 欠采样、mask 较大和 catalog 缺失。
- 多成分共存和成分简并。

主要指标：

- 每种成分的 precision、recall、F1。
- 误加成分率和漏加成分率，分别报告。
- `INCONCLUSIVE` 比例和其中人工确认后的分布。
- 最终轮的残差、物理参数通过率和 best-round-verifier 通过率。
- wall-clock、VLM 调用次数和实际 API 成本。

### 5. 通过标准

数值阈值不在实现前拍定，由 dev set 校准，在 held-out test set 上一次性报告。当前只预先规定原则：

- 已知高风险回归案例不得重犯。
- 新方案不能用提高漏检率来换取表面的零幻觉。
- 主成分物理有效率不得低于旧流程。
- 所有自动动作都必须可追溯到 schema version、rule ID 和具体证据。
- “100 倍加速”和“准确率提升”在 benchmark 完成前只作为待验证假设。

---

## 十、规范同步提案（本轮不执行）

### 1. `component_specification_galfits.md`

- 明确区分 `AGN` 与 `compact central source candidate`；正式成分白名单更新为八类（新增 Lens，2026-08-13 裁定）。
- AGN 统一使用 N 块，并要求独立 AGN 物理证据。
- `compact central source candidate` 的 P 块 profile 已裁定为小 Re Sérsic；专项测试用于校准其 Re 上限与初值，不再做 Gaussian 选型。
- 删除“拟合 Re 跨波段随 PSF 变化”的判据，改用观测 profile 与 PSF 的同波段比较。
- 将无条件跨波段 OR 改成“质量门后的证据融合”。

### 2. `workflow_galfits.md`

- 阶段二步骤 2 改为结构化分析结果驱动。
- 明确 `PROPOSE → REFIT → EVALUATE_REFIT` 的交替流程。
- 仍保持一次只改变一个成分。
- `INCONCLUSIVE` 按第四节自动化消解策略处理并留痕（试拟合仲裁或保守兜底＋`needs_review` 标志），流程不阻塞；不能静默当作无成分，人工复核改为事后批量审查。

### 3. `.claude/agents/best-round-verifier.md`

这里的 verifier 指当前仓库中的只读落锁审计 subagent `best-round-verifier`。它在正式锁定最优轮次前，按六个维度检查分析文件、成分探索、残差、物理参数、参数约束和指标，并返回 `PASS/FAIL`。

同步方向：

- 审计时读取结构化 decision artifact 和 rule trace，不再只依赖自由文本 component analysis。
- AGN 必须检查 N 块及其独立证据；`compact central source candidate` 按确认后的 P 块规范检查。
- BIC 按 `BIC_gain` 规则审计：可选成分默认检查 `>= 10`，但不能覆盖主成分的物理和残差证据。
- 中心成分最终 `xcen`、`ycen` 完全一致作为硬规则；尺寸层级作为物理期望和标签检查，不因单独反置自动 `FAIL`。

同步顺序：先确认本方案，再修改 component specification，再修改 workflow 和 verifier，最后实现代码与测试。

---

## 十一、实施顺序

1. 与科学家确认第六节逐成分判据，尤其是 Disk/Bulge、Bar、AGN/`compact central source candidate`。
2. 冻结 artifact manifest、numeric evidence、VLM evidence 和 decision artifact schema。
3. 先实现数值层与纯规则层单元测试，不接管现有 workflow。
4. 改造 VLM 为受控标签 JSON 输出。
5. 以 shadow mode 同时运行旧方案和新方案，只记录差异，不改变实际成分。
6. 建立 held-out benchmark，校准阈值并审查错误案例。
7. 按 `docs/component-analysis/spec-sync.md` 同步规范文档和 verifier。
8. 通过回归和科学家评审后，才让新规则层接管正式动作决策。

### 已裁定的 v1 默认规则（2026-08-13）

原四项开放问题已裁定为 v1 默认规则，先实施后按评测结果优化：

1. Disk 与 Elliptical 的最低证据组合：按第六节 Disk 提议规则的 N1/N2/N3 组合执行，冲突时 `INCONCLUSIVE` 并保持自由 n 单 Sérsic。
2. Bar 允许单个通过质量门的高质量波段强等照度证据触发，其余波段证据逐波段记录。
3. `compact central source candidate` 的 P 块采用小 Re Sérsic；升级 N 块 AGN 的条件暂不展开，维持“仅独立 AGN 证据时使用 N 块”。
4. Bulge 与 `compact central source candidate` 的分界按第六节可分辨尺度门执行（`FWHM_int >= 0.5×FWHM_psf` 且 `SNR >= 20`），阈值进入规则配置，由 dev set 注入-恢复实验校准。
5. 旧规范两项 OPEN 经验裁定（2026-08-13）：Lens 认定规则保留，Lens 升入正式白名单（第八类，见第六节第 8 小节，按可选成分 BIC 门管理）；「Nucleus 代偿 Bulge、物理意义优先于奥卡姆」废弃，中心源统一走可分辨尺度门分流。
6. 拟合流程全自动（2026-08-13 裁定）：`INCONCLUSIVE` 不设人工阻塞环节，按第四节自动化消解策略（试拟合仲裁／保守兜底／纯数值降级）消解并留痕，人工复核改为事后批量审查 `needs_review` 标志。
