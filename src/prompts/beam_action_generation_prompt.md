请查看以下图像（包含原图、模型图、2D残差图及1D表面亮度轮廓图），进行客观的多模态视觉特征提取。

**阶段一：多模态视觉特征提取（仅客观描述）**
1. 高/低动态范围的原图的特征描述
    - 具体描述原图 X轴和 Y 轴的坐标范围，坐标轴单位，标题中描述的内容
    - 描述不同动态范围原图的中心星系的特征， 并推测高概率存在的星系成分（要提供强特征证据支持）
    - 描述未被mask掉的伴星系位置区域（明显独立的点源或者展源，即白色亮区，黑色为 mask 区）提供具体的坐标。注意伴星系不仅出现在外围，也可能紧贴主星系中心亮区（嵌入式伴星系，落在 bulge/bar 的等照度线 contour 上或以内），仔细观察高动态范围原图，有利于发现嵌入式伴星系
2. 2D 原图与模型的特征描述：评估两者的总体骨架轮廓是否一致，差异点在哪里？
    - **伴星系位置核验（仅当参数摘要中已存在 Companion 成分时执行）**：分别从原图与 Model 图上读出伴星系的像素中心 `(x_real, y_real)` 与 `(x_model, y_model)`，报告两者偏差 `Δx = x_model − x_real`、`Δy = y_model − y_real` 与 `Δr = √(Δx² + Δy²)`。若 `Δr > 2 px`，视为显著偏差，**必须在阶段二生成一个 `tune(companion, x_real, y_real)` 候选动作**，把伴星系中心修正到原图真实位置（坐标仍写像素值，由主模型后续转 arcsec）；若 `Δr ≤ 2 px`，视为位置吻合，不需要生成位置修正候选（但仍可基于残差给出形态修正候选）。
3. 2D 残差图-核心区（中心星系延展区域范围内）：
    - 描述中心区域的正负残差分布对称性、残差强度、残差形态的空间分布（预测是否有尚未添加的成分特征）
    - **嵌入式伴星系检查（重要）**：核心区内是否存在位置固定、形态紧凑（接近 PSF 尺度到几像素）、单侧偏置的局部红色正残差热点？它与 bar/bulge PA 错位产生的"延展、中心对称四极矩"模式不同。若存在此类热点且原图对应位置可见次级亮峰（双峰结构），视为嵌入式伴星系残差特征，需准确报告其像素坐标。
    - 描述延展区域的残差的空间分布特征（如同心环、同心弧、条带、随机分布等）
    - 描述延展区域内是否存在独立伴星系的残差特征（独立的、非弥散的局部亮斑），对于该区域的伴星系，需要准确描述其中心位置坐标
    - 描述延展区域内是否存在偏心（lopsidedness）残差特征（通常表现为一边正残差一边负残差；伴星系也容易引起残差不对称的偏心特征，注意区分）
4. 2D 残差图-外围区（中心星系延展区域20px之外）：
    - 描述外围是否存在伴星系或者独立的点源（外围伴星系对中心拟合影响较小，可以选择不拟合；但若已识别出嵌入式伴星系，外围的可暂时忽略以集中处理中心结构）
5. 1D 亮度曲线与残差
    - 描述图表的坐标、标注、标题等所包含的内容，
    - 如果 sky 成分存在， 描述 sky 成分星等线与 sky background 虚线的关系（齐平、偏高或者偏低）
    - 描述 Data 与 Model 之间明显差异的区域（如中心过亮或过暗，某个半径范围内的系统偏亮或偏暗等）
    - 描述各成分的星等差异、以及残差曲线与各成分 Re 的对应关系（如残差的峰值位置是否与某个成分的 Re 对应等）

要求：所有描述必须基于图片内容，不能主观臆测。

<!-- phase:candidate_generation -->

基于你刚才的视觉特征分析，结合以下拟合参数摘要，作为 Beam Search 候选动作生成器，输出 **2–4 个** 互不同质化的候选复合动作。

参数摘要内容：
{summary_content}

补充信息（含 working_note 历史摘要、阶段一结论、本轮已尝试动作等）：{custom_instructions}

**阶段二：候选动作生成（Beam Search 模式）**

## 角色与目标
你现在是 Beam Search 中的**候选动作生成器**（Candidate Generator）。主模型（编排智能体）会在每次拟合完成后调用你，基于当前残差与历史，给出若干"下一步候选复合动作"，由主模型做去重、打分、入队。

与"单一决策"模式（`analyze_multiband_components`）不同，**你不输出唯一动作**，而是输出多个可行方向，让主模型在束内并行探索。

## 当前调用上下文
- **branch_id**: `{branch_id}`
- **parent_label**: `{parent_label}`（父轮次标识，如 `A.1`）
- **depth**: `{depth}`（父状态在搜索树中的深度；1 = 输入 .lyric 首次拟合后的状态；2 = 第二次拟合后；以此类推）

## 候选动作的原子操作
每个候选复合动作由至多 **2 个**原子操作组成，且必须语义内聚（服务于同一个物理目标）：
- `add(type, initial_params)` — 新增成分（如 add(Bulge, n=4 fixed, Re=0.05")）
- `remove(component_id)` — 删除已有成分
- `tune(component_id, param_delta)` — 调整成分参数（含释放/固定 vary、收紧/放宽边界、修改约束）

**禁止**捆绑无关联的原子操作（如同时增 Bulge 又改 Disk PA 又删伴星系）。

## 🔑 PA 约定（生成含 PA 的候选前必读）

凡候选动作涉及 PA（位置角）—— 如 `add(Bar, ..., PA=...)`、`tune(component, pa=...)`、Fourier 模式的 `theta_m` —— 一律使用 **sky-PA**：

- **0° = 正北**（不是图的纵轴！），**逆时针增加到东**
- 这与 GALFIT 单波段 "+Y 轴为 0°" 的约定不同；不要套用 GALFIT 习惯
- 视觉参照：`render_original` / `all_bands_comparison.png` 每张原图右上角的 lime 指南针（N/E 箭头）—— **对齐 N 箭头读角度**
- `detect_galfits_bar_lopsidedness` 返回的 `bar.pa_deg` 已经是 sky-PA，可直接作为候选 `PA=` 的取值

## 候选空间字母表（生成前必读）

在生成候选前，显式列出主星系与伴星系的合法成分类型空间。这是"菜单"不是"答案"——实际候选仍须基于阶段一的残差证据与下文各节的认定规则。**目的**：避免低频但合法的候选（特别是 Lens）因 VLM 训练分布稀疏而被系统性遗漏。

### A. 主星系成分类型

主星系（不含伴星系）的最终成分集合属于以下之一：

- **单 Sersic**：椭圆星系的合法终态，或 Round 0 首次拟合的起步形态
- **多成分组合**：从下表选取**实际存在的子集**（是子集，不是要全部添加）

| 成分 | 关键先验 | 一句话识别线索 |
|------|---------|---------------|
| Disk | n=1（确认后锁固定 vary=0） | 盘星系必备，延展轮廓 |
| Bulge | n≈4 自由 | 中心致密圆成分 |
| Bar | n=0.5 **固定**，q<0.4 扁长 | "一字型"/"X 型"残差 |
| Lens | n<0.5 自由，q>0.5 | **低频但重要**——见下方【Lens 特别提醒】 |
| AGN / Nucleus（N 块） | Na1-Na27，无 Re 物理量 | 仅当 Bulge Re<0.2 px 全波段坍缩时启用 |

### B. 附加修饰维度（正交于成分类型，可与任一组合叠加）

- **Lopsidedness（m=1 Fourier 模式）**：把 Disk 的 `Pa2) sersic` 改为 `sersic_f`；当阶段一 `detect_galfits_bar_lopsidedness` 检出偏心残差时，Lopsidedness存在的可能性较高，是否启用需要进一步结合残差图分析；**只能作用于 Disk**，严禁加在 Bulge/Bar/Lens/AGN 上
- **同心约束**：主星系多中心成分（Disk/Bulge/Bar/Lens）通过 `.constrain` 文件绑定 xcen/ycen；AGN 中心参数名是 `xcen_agn` / `ycen_agn`（不是 `agn_xcen`）

### C. 伴星系（独立 G 块，与主星系正交）

- **独立 G 块 + P 块 Sersic/PSF**：拟合主星系外围的独立伴源；不参与主星系的 Re 全序校验
- 伴星系位置必须 `vary=1`（±2" 量级窗口），严禁凭 VLM 像素估计直接 `vary=0`；位置漂移 > 2 px 时阶段一会强制生成 `tune(companion, x_real, y_real)` 候选

### 【Lens 特别提醒 — VLM 高频遗漏项】

Lens 在训练数据中频率低、且**无独立视觉签名**——它的存在通常通过 Bar 异常反推：父状态的 Bar 出现 `Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5` 时，意味着 Bar 被强行拉去拟合 Lens 结构，应拆分为 Bar + Lens。**父状态含 Bar 时，VLM 必须主动回忆此触发条件**，不得仅凭"Bar 是常见成分"就跳过 Lens 候选。详细触发条款与参数模板见下文 §通用规则 / Lens 候选生成时机。

## Disk 成分 Sérsic 指数 n 的操作规范

Disk 的 Sérsic 指数 n 在物理上**可以小于 1**（对应低表面亮度盘 / 平滑盘 / 截断盘，面亮度中心平坦、外围下降偏陡），n<1 是合法的物理解。但在多成分分解中，释放 disk n 会增加与中心成分（Bulge/Bar/Lens）的简并风险。按 beam search 阶段分级处理：

- **建立基础结构阶段（depth ≤ 2，或双成分 Disk+Bulge 尚未稳定）**：`tune(Disk, n=1 fixed, vary=0)`。固定 n=1 作为先验，先把 Disk 与中心成分的通量分配稳定下来。此时**不应**生成释放 disk n 的候选。
- **深化探索阶段（depth ≥ 3，且双成分结构已稳定）**：**允许**生成 `tune(Disk, n_free)` 候选，让拟合器探索真实盘轮廓（n 可能 ~0.5–1.3）。下界可放宽到 0.3 以容纳平滑盘。
- **身份互换退化的联合诊断（硬约束）**：释放 disk n 后，若拟合结果**同时**出现 `disk_n ≈ 下界` **且** `bulge_Re ≈ 上界`（或反之 bulge_n 触界、disk_Re 异常），这才是 disk↔bulge 身份互换的退化信号——判据必须是**联合诊断**，而不是单看"n 被释放"或"n<1"。出现联合退化时，主模型在打分阶段对该候选施加退化惩罚（§去重与排序 维度 4），而非一刀切禁止释放 n。

## 候选数量规则（按 depth 强制分段，严禁凑数）

搜索树浅层的下一步通常是确定性的（建立 Disk+Bulge 基础结构），没有必要并行探索；真正的分支发生在双成分结构稳定之后。按 depth 分段给出候选数：

### depth = 1（父状态是输入 .lyric 的首次拟合结果）
依据 `working_note.md` 头部的阶段一 `detect_galfits_bar_lopsidedness` 结论决定候选数：
- **lopsidedness 检出**（任一波段）→ **1 个候选**：`tune(Disk, sersic→sersic_f)`（偏心优先级最高，先于加任何成分）。
- **lopsidedness 未检出 + bar 检出**（任一波段）→ **1–2 个候选**：`add(Bulge, n=4 fixed)`（标准 Disk+Bulge 拆分）与 `add(Bar, n=0.5 fixed, PA≈阶段一 PA)`（仅当原图 bar 特征强）。此处 `PA` 用 **sky-PA**（见 §🔑 PA 约定），阶段一 `detect_galfits_bar_lopsidedness` 返回的 `bar.pa_deg` 可直接拿来用。
- **两者都未检出** → **1 个候选**：`add(Bulge, n=4 fixed)`。
- **例外**：若单 sersic 拟合残差清楚地显示侧视盘特征（b/a < 0.17 且残差有 dust lane / 盘厚度），可改为 **1 个候选**：`tune(Disk, sersic→edgeondisk)`。

### depth = 2（双成分基础结构已建立）
输出 **2–3 个**候选。典型方向：修约束、释放/收紧某参数、新增致密成分（Nucleus）、切换某成分模型类型。

### depth ≥ 3（深化探索）
输出 **2–4 个**候选。此时 beam search 的并行探索价值最大，应充分利用方向多样性。

### 通用规则（所有 depth 适用）
- **严禁凑数**：若无法生成足够多 `expected_behavior_tag` **两两不同**的候选，允许实际数量少于上述下限。宁可只给 1 个高质量候选，也不要给 2 个实质相同的凑数候选。
- **物理动机必须基于阶段一**：每个候选的 physical_motivation 必须引用阶段一描述过的具体残差特征（位置、强度、对称性等），严禁凭空推测。
- **遵循成分添加次序**：优先 Disk → (F1/Companion 若检出) → Bulge → Bar → Lens → Other。Bar/Lens/Nucleus 的认定条件须符合 `<星系成分分析的总体流程>` 章节。
    - **Lens 候选生成时机**：当父状态的 Bar 出现物理异常（`Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5`，即 Bar 被强行拉去拟合 Lens 结构）时，应生成拆分候选：`tune(Bar, split→Bar+Lens)` 或 `add(Lens, n<0.5 free, q>0.5, Re between bulge/bar and disk)`。Lens 用 `sersic`，n 自由（vary=1）但物理先验 n<0.5，Re 满足全序基准 `Re_disk > Re_lens > Re_bar > Re_bulge`（仅比较实际存在的中心成分，把缺失者从链中剔除后按相对顺序严格递减），q>0.5，与 bulge/bar/disk 同心。
- **尊重历史**：补充信息中"已尝试动作"列表里的动作不得重复提出（除非换个明显不同的参数化方向）。
- **方向多样性**（多候选时）：候选之间必须覆盖**显著不同**的探索方向。典型对比组合：
    - "加成分" vs "调参"（如 +Nucleus(致密) vs release bulge_n）
    - "修约束" vs "换模型类型"（如 修复 bulge↔disk 同心 vs 切换 Disk→edgeondisk）
    - "奥卡姆剃刀" vs "深化"（如 remove(nucleus) vs tighten bulge_Re 上限）
    - "拆 Bar→Bar+Lens" vs "收紧 Bar Re 上限"（当父状态 Bar 的 Re/q 物理异常时，拆出 Lens 吸收延展成分 vs 用约束把 Bar 压回合理区间）
    - "伴星系位置修正" vs "伴星系形态修正"（如 tune(companion, x_real, y_real) vs tune(companion, q_init=0.9, Re<=2")）；仅当阶段一已报告位置偏差 > 2 px 时，位置修正候选才是必须的，否则优先形态修正

## 候选预期信息
每个候选必须给出：
- **expected_C'**：施加动作后的预期成分清单（如 `{Disk, Bulge, Nucleus}`）
- **expected_behavior_tag**：预期拟合行为标签（短蛇形命名，如 `bulge_n_free_release`、`nucleus_add_compact`、`bar_pa_correct`、`edgeondisk_switch`、`constrain_fix_concentric`、`occam_remove_nucleus`、`disk_switch_sersic_f`）
- **local_benefit_σ** ∈ [0, 1]：你预估的"本动作能让 reduced_χ² 改善的比例"。0 表示无改善，1 表示残差几乎全被吸收。**此值仅供主模型参考**，主模型会独立打分。

## 输出 schema（严格遵守 Markdown 格式）

````markdown
# Beam Action Candidates (branch={branch_id}, parent={parent_label}, depth={depth})

## Candidate 1
- **action_id**: {branch_id}-{parent}-cand-1
- **primitives**:
  1. <add|remove|tune>(<target>, <key params>)
  2. <add|remove|tune>(<target>, <key params>)   ← 可选，至多 2 条
- **physical_motivation**: <引用阶段一描述的具体残差特征>
- **expected_C'**: {<component1>, <component2>, ...}
- **expected_behavior_tag**: <snake_case tag>
- **local_benefit_σ**: <0.0–1.0>

## Candidate 2   (按 depth 规则决定是否需要)
- **action_id**: {branch_id}-{parent}-cand-2
- **primitives**:
  1. ...
- **physical_motivation**: ...
- **expected_C'**: {...}
- **expected_behavior_tag**: ...
- **local_benefit_σ**: ...

## Candidate 3   (可选，按 depth 规则)
...

## Candidate 4   (可选，仅 depth≥3 时可能)
...
````

## 自检（生成后必须逐项确认）
- 候选数量符合当前 depth 的分段规则（depth=1 多数情况为 1 个；depth=2 为 2–3 个；depth≥3 为 2–4 个）
- 每个候选 primitives 数量 ∈ [1, 2]，且语义内聚
- 多候选时，所有候选的 `expected_behavior_tag` **两两不同**（硬约束；无法满足则减少候选数）
- **释放 Disk n 的候选仅在深化阶段（depth≥3 且双成分结构稳定）生成**；基础结构阶段（depth≤2）不生成释放 disk n 的候选（见"Disk 成分 Sérsic 指数 n 的操作规范"）
- physical_motivation 引用的特征均在阶段一出现过
- 已尝试动作未被重复提出
- 所有候选的 expected_C' 与当前父状态的 C' 差异均可解释
- **Lens 触发复核（硬约束）**：父状态含 Bar 时，必须确认已主动回忆 Lens 触发条件（`Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5`）。若条件成立但本次未产出任何 Lens 相关候选（`add(Lens)` 或 `tune(Bar, split→Bar+Lens)`），必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"Bar 异常但残差更支持 X 方向"），不得静默跳过
