请查看以下图像（包含原图、模型图、2D残差图及1D表面亮度轮廓图），进行客观的多模态视觉特征提取。

**阶段一：多模态视觉特征提取（仅客观描述）**
1. 高/低动态范围的原图的特征描述
    - 具体描述原图 X轴和 Y 轴的坐标范围，坐标轴单位，标题中描述的内容
    - 描述不同动态范围原图的中心星系的特征， 并推测高概率存在的星系成分（要提供强特征证据支持）
    - 描述未被mask掉的伴星系位置区域（明显独立的点源或者展源，即白色亮区，黑色为 mask 区）提供具体的坐标。注意伴星系不仅出现在外围，也可能紧贴主星系中心亮区（嵌入式伴星系，落在 bulge/bar 的等照度线 contour 上或以内），仔细观察高动态范围原图，有利于发现嵌入式伴星系。
    - **嵌入式伴星系位置可靠性警告**：在中心成分（Bulge/Bar）建立**之前**，残差被未拟合的中心通量污染，此时从残差图或原图高动态范围读出的嵌入式伴星系位置**不可靠**（易把 Bulge 残差伪特征误判为伴星系位置）。中心成分建立之后读出的位置才可作为强证据。
2. 2D 原图与模型的特征描述：评估两者的总体骨架轮廓是否一致，差异点在哪里？
    - **伴星系位置核验（仅当参数摘要中已存在 Companion 成分时执行）**：分别从原图与 Model 图上读出伴星系的像素中心 `(x_real, y_real)` 与 `(x_model, y_model)`，报告两者偏差 `Δx = x_model − x_real`、`Δy = y_model − y_real` 与 `Δr = √(Δx² + Δy²)`。若 `Δr > 2 px`，视为显著偏差。**但在生成位置修正候选前，先检查父状态是否已建立中心成分（Bulge 或 Bar）**：
        - 父状态**已含** Bulge/Bar → 生成 `tune(companion, x_real, y_real)` 位置修正候选（坐标仍写像素值，由主模型后续转 arcsec）。
        - 父状态**不含** Bulge/Bar 且伴星系出现参数触界（Re/xcen/ycen 撞边界）→ 这是"伴星系被借调去代偿中心通量"的退化信号，**位置修正是治标不治本**；此时应生成 `add(Bulge)` 或 `add(Bar)` 候选先把中心骨架建起来，伴星系位置待中心稳定后再修正。
        - 若 `Δr ≤ 2 px`，视为位置吻合，不需要生成位置修正候选（但仍可基于残差给出形态修正候选）。
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
    - **Disk 外围光度不足检查（优先于中心成分残差分析）**：先确认 disk 骨架是否正确——检查 1D 残差曲线（Δμ = Data − Model）在距中心 r > 2×Re_disk 的外围区域是否**系统性为正**（Data 亮于 Model，Δμ < 0）。若该外围区域存在宽阔的系统性正残差（非噪声波动，跨度 > 15 px，幅度 Δμ ≲ −0.05 mag），说明 disk 的 Re 偏小、外缘光度未覆盖，是 disk 骨架不准的信号。**此时中心成分的残差分析（Lens 隆起、Bulge 参数等）可能建立在错误的 disk 基线上**——若 disk Re 偏小，disk 会把本应属于自己的外缘通量"让"给中心成分（或迫使 lens/bar 膨胀去代偿），导致中心成分参数看似需要调整、实则只是 disk 骨架错的连带效应。因此应**优先修正 disk Re**，再看中心成分残差。发现此外围光度不足特征时，须在特征描述中明确标注正残差的起始半径、跨度与幅度，供阶段二生成 `tune(disk, Re 更大)` 候选（见 §Disk 外围光度不足触发规则）。注意：若外围区域数据点已变为红色三角形（达到背景噪声极限、低信噪比），判读需谨慎——只有当正残差在进入背景极限前就已系统性出现才视为命中。
    - **Lens 隆起的伴星系污染前置检查（先于 Lens 隆起诊断执行）**：1D 曲线是方位平均的产物——落在半径 r 处的致密源（伴星系、亮结）经方位平均后，同样会在该半径产生宽阔正向隆起，与 Lens 签名在 1D 上不可区分。发现中半径隆起时必须核对：隆起径向区间是否覆盖任一 companion 的中心半径（由参数摘要 xcen/ycen 换算）或原图可见伴星系候选的半径。若覆盖，须在特征描述中报告"隆起与伴星系半径共位"，并在 2D 残差图上区分两种形态：**局部紧凑亮斑**（方位覆盖 ≲90°，伴星系漏光签名）vs **方位连续环状正残差**（覆盖 ≳180°，Lens 签名）。同时报告 companion 的数值状态（Re 贴下界 / axrat 或 xcen 触界 / Mag 远暗于盘，均为未锚定真实源的信号）。
    - **Lens 隆起诊断（父状态已含 Bar 或 Bulge 时执行）**：检查 1D 残差曲线（Δμ = Data − Model）在距中心 ~1.5–2.5·Re_bar（无 Bar 时取 ~2–4·Re_bulge）处是否存在**宽阔的正向隆起**（Data 亮于 Model，Δμ < 0，跨度 ~10–30 px）。该隆起是 Lens（低 n 延展成分）的径向通量签名，与旋臂残差不同——旋臂在 2D 残差图上呈螺旋条带，经方位平均后在 1D 上幅度被压制；而 Lens 隆起在 2D 上呈近圆对称环状，1D 上幅度显著。发现此类宽阔隆起时，须在特征描述中明确标注位置、宽度与幅度，供阶段二生成 `add(Lens)` 候选（见 §Lens 1D 轮廓隆起触发规则）。**前置依赖**：若 Disk 外围光度不足检查已命中，Lens 隆起诊断的判读可能被 disk Re 偏小污染，应在 physical_motivation 中同时说明两者的相对贡献；若伴星系污染前置检查报告"隆起与伴星系半径共位"，Lens 隆起诊断的结论必须附带 2D 形态判别结果（局部亮斑 vs 环状）。

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
| Disk | n=1（**一律固定 vary=0，永不释放**） | 盘星系必备，延展轮廓 |
| Bulge | n≈4（先固定 n=4，后续可释放） | 中心致密圆成分 |
| Bar | n=0.5 **固定**，q<0.4 扁长 | "一字型"/"X 型"残差 |
| Lens | n<0.5 自由，q>0.5 | **低频但重要**——见下方【Lens 特别提醒】 |
| AGN / Nucleus（N 块） | Na1-Na27，无 Re 物理量 | 仅当 Bulge Re<0.2 px 全波段坍缩时启用 |

### B. 附加修饰维度（正交于成分类型，可与任一组合叠加）

- **Lopsidedness（m=1 Fourier 模式）**：把 Disk 的 `Pa2) sersic` 改为 `sersic_f`；当阶段一 `detect_galfits_bar_lopsidedness` 检出偏心残差时，Lopsidedness存在的可能性较高，是否启用需要进一步结合残差图分析；**只能作用于 Disk**，严禁加在 Bulge/Bar/Lens/AGN 上

### B'. 主星系同心约束（强制默认，非附加修饰——VLM 与主模型共享的硬约束）

**触发条件**：只要本轮父状态的 `expected_C'` 包含 **≥ 2 个主星系中心成分**（Disk/Bulge/Bar/Lens 四类中的任意两个或以上），同心约束就**必须生效**——这是默认硬约束，不是"需要时才做"的可选项，与候选的具体方向（加成分/调参/修约束）无关。

**VLM 职责**：在生成 `add(Bulge)` / `add(Bar)` / `add(Lens)` / `add(AGN)` 等新增主星系中心成分的候选时，`physical_motivation` **必须显式提及**"新增成分与现有 Disk/Bulge/Bar/Lens 通过 `.constrain` 绑定同一中心"；在 `expected_behavior_tag` 中可使用 `concentric_bound` 标识。生成 `tune(...)` 或 `remove(...)` 候选时，只要 `expected_C'` 仍含 ≥ 2 个主星系中心成分，同样默认继承同心约束（无需每轮重复声明，但不得遗忘）。

**伴星系豁免**：伴星系（P 块 label 含 `comp`/`companion`/`secondary`/`satellite`）的中心**严禁参与**主星系同心约束——伴星系中心必须保持 `vary=1` 自由拟合。VLM 不得在 `physical_motivation` 中建议把伴星系中心绑定到主星系。

**AGN 中心参数名**：N 块的中心参数是 `xcen_agn` / `ycen_agn`（不是 `agn_xcen` / `agn_ycen`）；绑定 AGN 时用这两个名字。

### C. 伴星系（独立 G 块，与主星系正交）

按距主星系中心的远近，伴星系分两类，**添加时机不同**：

- **独立 G 块 + P 块 Sersic/PSF**：拟合独立伴源；不参与主星系的 Re 全序校验。

**C1. 外围伴星系（Outer Companion）**：位于主星系延展盘之外或外缘附近（距中心 ≳ 2·Re_disk），与中心成分通量几乎不退化。可在任意阶段添加，不强制等待 Bulge/Bar。

**C2. 嵌入式伴星系（Embedded Companion）**：紧贴主星系中心亮区，落在 bulge/bar/disk 等照度线 contour 上或以内（距中心约 1–3·Re_bar）。与中心成分通量**强退化**——若在 Bulge/Bar 建立之前添加，伴星系会被拽向中心去代偿未拟合的中心通量，导致位置漂移、Re 膨胀、参数撞界发散。
- **硬性时机规则**：嵌入式伴星系必须在父状态**已建立 Bulge 或 Bar** 之后才添加。若父状态既无 Bulge 也无 Bar，**禁止**生成 `add(Companion)` 候选；应先生成 `add(Bulge)` 或 `add(Bar)` 候选。
- 位置必须 `vary=1`（±2" 量级窗口），严禁凭 VLM 像素估计直接 `vary=0`。
- 位置漂移 > 2 px 且父状态已含 Bulge/Bar 时，生成 `tune(companion, x_real, y_real)` 位置修正候选（详见 §伴星系位置核验）。

### 【Lens 特别提醒 — VLM 高频遗漏项】

Lens 在训练数据中频率低、且**无易辨识的 2D 视觉签名**（其残差应为**方位连续的环状**分布，但容易被忽略；**单侧紧凑亮斑不是 Lens 签名，是伴星系/亮结签名**）——它的存在通过**两条独立路径**揭示，任一满足即应生成 Lens 候选：

1. **路径 A（Bar 异常反推）**：父状态的 Bar 出现 `Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5`，意味着 Bar 被强行拉去拟合 Lens 结构，应拆分为 Bar + Lens。
2. **路径 B（1D 轮廓隆起）**：1D 表面亮度残差曲线在距中心 ~1.5–2.5·Re_bar 处出现宽阔正向隆起（详见 §Lens 1D 轮廓隆起触发规则），意味着过渡区有独立于 Bar 和 Disk 的延展通量成分。**此路径不需要 Bar 异常**——即使 Bar 参数完全正常，1D 隆起仍可独立触发。

**父状态含 Bar 或 Bulge 时，VLM 必须主动回忆两条触发条件**，不得仅凭"Bar 是常见成分"或"Bar 参数正常"就跳过 Lens 候选。详细触发条款与参数模板见下文 §Lens 1D 轮廓隆起触发规则 与 §通用规则 / Lens 候选生成时机。

## 扁 Bulge → Bar 候选触发规则（联合诊断，VLM 必读）

**动机**：当面朝向（face-on）盘星系的 Bulge 出现显著扁平（b/a 偏小），最自然的物理解释不是"扁化的核球"，而是**被误标为 Bulge 的 Bar**——Bar 有独立的三轴结构，不随盘的倾角变圆。此判据用于在 beam search 中**强制探索 bar 假设**，避免因阶段一未检出 bar 就直接跳过 bar 方向。

### 触发条件（四条同时满足，缺一不可）

当父状态已含 Bulge（P 块 sersic）时，读取 `.gssummary` 中 bulge 的拟合参数，按下表联合判定：

| 指标 | 阈值 | 物理依据 |
|------|------|---------|
| `bulge_axrat` (b/a) | < 0.5 | Bar 经验上限约 0.4–0.5；圆核球通常 b/a > 0.6 |
| `bulge_ang` 与 `disk_ang` 的 PA 夹角 | > 20° | Bar 通常与盘主轴显著斜交；若 PA 一致，扁更可能来自投影而非 Bar |
| `bulge_n`（若 free） | 0.5 < n < 2.5 | Bar 的典型 Sérsic n 范围；n > 3.5 更像经典核球 |
| `disk_axrat`（倾角代理） | > 0.5 | 星系非 edge-on；edge-on 星系所有成分都扁，此规则禁用 |

**关键**：单看 b/a 不够。face-on 盘（disk b/a > 0.8）的 bulge b/a < 0.5 是强 Bar 信号；但 edge-on 盘的 bulge 扁是投影效应，不触发。

### 触发后的候选生成（两种动作必出其一）

触发条件成立时，**必须**在当轮候选里产出至少一个 Bar 方向候选，且不得因"阶段一未检出 bar"而自我审查跳过。两种候选测试不同物理假设，可择一或并存：

1. **`tune(Bulge→Bar, n=0.5 fixed)`**（转换） — 测试假设："这个扁成分本身就是 Bar，没有独立 Bulge"。成分数不变。判据：ΔBIC < 0 即支持。
2. **`add(Bar, n=0.5 fixed, PA≈bulge PA) + tune(Bulge, q_min=0.7)`**（新增 + 圆化） — 测试假设："扁 Bulge 之外还有一个独立 Bar"。成分数 +1。判据：需 ΔBIC < −10 才算显著（跨过新增参数惩罚）。圆化 Bulge 是为了打破 Bar/Bulge 简并，避免拟合器把其中一个推到极端参数。

**PA 取值**：候选的 Bar PA 优先取父状态 `bulge_ang`（已对齐到扁成分的长轴方向）；或取阶段一 `detect_galfits_bar_lopsidedness` 的 `bar.pa_deg`（若检出）。两者都是 sky-PA，可直接写入。

### 自检硬约束

- **触发复核**：若父状态含 Bulge 且上述四条联合条件成立，本次输出**必须**包含至少一个 Bar 候选（转换或新增）。若未产出，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"虽然 bulge q=0.27 触发，但 1D 残差曲线显示 X 方向更优"），不得静默跳过。
- **简并预警**：若选择"新增 Bar"方案，physical_motivation 必须提及"通过约束 Bulge q_min≥0.7 打破 Bar/Bulge 简并"——否则拟合器容易把两个都放在中心的扁成分推到极端参数（典型退化：bulge n 坍缩至下界、bulge/bar 亮度和 PA 几乎相同）。

## Disk 外围光度不足触发规则（VLM 必读，优先级高于中心成分规则）

**动机**：Disk 是星系的基础骨架成分。若 disk Re 偏小，disk 会把本应属于自己的外缘通量"让"出去——要么迫使 lens/bar 膨胀去代偿（导致 Re 反置、触界等退化），要么让中心成分（Bulge/Bar）在不正确的盘基线上建立、参数看似需要反复调整实则只是 disk 骨架错的连带效应。在 beam search 深入探索中心成分后，VLM 和主模型容易"忘记"回头调 disk Re 这个根本有效的方向。本规则强制 VLM 在每轮候选生成时**优先**检查 disk 外围光度，命中时以最高优先级生成 `tune(disk, Re 更大)` 候选。

### 触发条件（三条同时满足，缺一不可）

读取 1D 表面亮度残差曲线（Δμ = Data − Model，下方面板）与 disk 的拟合 Re：

| 指标 | 阈值 | 物理依据 |
|------|------|---------|
| **残差位置** | 系统性正残差（Data 亮于 Model，Δμ < 0）出现在 r > 2×Re_disk 的外围区域 | disk 的外缘指数衰减（n=1）轮廓若 Re 偏小，外缘光度会系统性低于真实数据 |
| **残差宽度与幅度** | 宽阔（跨越 > 15 px 量级，非窄尖峰），幅度 Δμ ≲ −0.05 mag | 显著的系统性通量缺失，超出噪声波动范围；窄尖峰更可能是背景梯度或掩膜边缘效应 |
| **disk Re 未触界** | disk Re < lyric 中 disk 的 re_max（即 disk 还有增大空间） | 若 disk Re 已触界，增大方向需先放宽 re_max，属另一类问题 |

### 判读注意事项

- **背景极限判读**：r > 30–40 px 后数据点常变为红色三角形（达到背景噪声极限、低信噪比）。只有当正残差在**进入背景极限前**就已系统性出现（如 r ≈ 15–30 px 区间内持续 Δμ < 0），才视为可靠命中；仅靠背景极限后的红三角形态判读不可靠。
- **与 Lens 隆起的区分**：Lens 隆起的峰值位置在 ~1.5–2.5·Re_bar（介于中心与 disk 之间）；disk 外围光度不足的正残差从 r > 2×Re_disk 起向外延伸。两者可能同时存在——若 disk Re 偏小，lens 隆起的判读会被污染，应在同一候选集里同时包含 `tune(disk, Re 更大)` 与 `add(Lens)`（若 lens 触发也成立），让主模型打分竞争。
- **与 sky 背景的区分**：若 sky 背景估计偏高，也会让外围 Data 系统性亮于 Model。检查 sky 成分星等线与 sky background 虚线的关系——若两者齐平且外围正残差显著，更可能是 disk Re 偏小；若 sky 星等线偏高，应先考虑 sky 修正。

### 触发后的候选生成

触发条件全部成立时，**必须**以**最高优先级**产出 `tune(disk, Re 更大)` 候选（优先于中心成分的 add/tune 候选）：

- **action**: `tune(disk, Re_init = 1.3–1.5 × current_disk_Re)`
- **profile**: `sersic`（不变）
- **n**: 保持 fixed = 1（**禁止释放 disk n**，见 Disk 成分 Sérsic 指数 n 的操作规范）
- **Re**: 自由（vary=1），初始值取 1.3–1.5 × current_disk_Re；re_max 不收紧（让 disk 自由增大，不设人为上限）
- **其他参数**（xcen/ycen/axrat/ang）: 保持父状态拟合值热启动
- **physical_motivation** 须引用：1D 正残差的起始半径（"Δμ 在 r ≈ XX px（= Y·Re_disk）处开始系统性为负，持续至 r ≈ ZZ px，峰值 ≈ −W mag"）、2D 残差图对应区域的近圆对称弥散正残差（若有）、以及"disk Re 偏小导致外缘光度未覆盖"的物理理由

### 自检硬约束

- **触发复核**：若上述三条触发条件成立，本次输出**必须**包含至少一个 `tune(disk, Re 更大)` 候选，且其优先级应排在候选列表的前列。若未产出，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"外围正残差幅度仅 −0.03 mag，未达阈值"或"正残差仅在背景极限后的红三角区出现"），不得静默跳过。
- **与中心成分规则的优先级**：若本轮同时触发 disk 外围光度不足与扁 Bulge→Bar / Lens 隆起 / 嵌入式伴星系等规则，`tune(disk, Re 更大)` 候选**必须**占候选列表的一个名额（不得因其他规则候选多而挤掉 disk Re 方向）。

## Lens 1D 轮廓隆起触发规则（VLM 必读）

**动机**：Lens 是低浓度（n<0.5）、延展的轴对称成分，在 2D 残差图上**无独立视觉签名**（不像 Bar 有"一字型"残差、Bulge 有致密核心）。但它会在 1D 表面亮度轮廓的 **Bar-Disk 过渡区**留下可辨识的印记：Data 曲线相对于平滑模型出现一个**宽阔的正向隆起**。该隆起无法被 n=1 的 Disk（指数衰减）和中心 Bulge/Bar 的线性叠加自然产生——它的存在直接指示一个中等 Re、低 n 的额外成分（即 Lens）。**但该隆起可以由位于同半径的致密伴星系经方位平均自然产生**——1D 隆起证据不唯一，触发前必须先完成伴星系污染排除（见阶段一"Lens 隆起的伴星系污染前置检查"）。

### 触发条件（五条同时满足，缺一不可）

当父状态已含 Bar 或 Bulge（中心骨架已建立）时，读取 1D 表面亮度残差曲线（Δμ = Data − Model，下方面板）：

| 指标 | 阈值 | 物理依据 |
|------|------|---------|
| **隆起位置** | 峰值在距中心 ~1.5–2.5·Re_bar（无 Bar 时取 ~2–4·Re_bulge） | Lens 的 Re 介于 Bar 与 Disk 之间（全序链 `Re_disk > Re_lens > Re_bar > Re_bulge`），其通量贡献的径向峰值落在 Bar 之外、Disk 主体之内 |
| **隆起宽度** | 宽阔（跨越 ~10–30 px 量级，非窄尖峰） | 低 n Sersic（n<0.5）轮廓平缓，贡献跨越大半径范围；窄尖峰更像旋臂结块、PSF 问题或 binning 假象 |
| **隆起幅度** | Δμ 峰值 ≲ −0.1 mag（Data 明显亮于 Model） | 显著通量贡献，超出噪声波动范围；Δμ > −0.05 mag 的微小波动不构成触发 |
| **2D 对应** | 2D 残差图对应半径处呈**方位连续**（覆盖 ≳180°）的环状/壳层正残差，而非螺旋条带或单侧紧凑亮斑 | 排除旋臂——旋臂在 2D 上呈非轴对称螺旋图案，经方位平均后在 1D 上幅度被压制且位置不稳定。Lens 是轴对称的，1D 隆起幅度与 2D 环状残差一致 |
| **伴星系漏光排除** | 隆起径向区间内不存在"位置未校准或参数退化"的 companion（2D 同半径残差为局部紧凑亮斑时即视为未排除） | 致密源经方位平均后同样产生宽阔隆起；1D 证据无法区分两者，必须靠 2D 形态（局部亮斑 vs 方位连续环状）与 companion 数值状态（Re 贴下界 / axrat 或 xcen 触界 / Mag 远暗于盘）排除 |

### 与旋臂残差的关键鉴别

旋臂和 Lens 隆起都会在 1D 上表现为正残差，但物理本质不同：

| 特征 | 旋臂 | Lens 隆起 |
|------|------|-----------|
| 2D 残差形态 | 螺旋条带（非轴对称） | 近圆对称环状/壳层 |
| 1D 隆起幅度 | 方位平均后被压制，幅度较小 | 幅度显著（Δμ ≲ −0.1 mag） |
| 1D 隆起宽度 | 较窄且位置受旋臂相位影响 | 宽阔且位置稳定（锁在 1.5–2.5·Re_bar） |
| 触发结论 | 不生成 Lens 候选 | 生成 `add(Lens)` 候选 |

### 触发后的候选生成

**污染分支（优先于 add(Lens)）**：若隆起径向区间与某致密源半径共位，按模型中是否已有共位的 companion 分两种情形：
- **已有 companion 且共位**：该 companion 存在位置偏差（Δr > 2 px，见 §伴星系位置核验）或参数退化（Re 贴下界 / axrat 或 xcen 触界 / Mag 异常暗于盘）时，本规则的优先产出是 `tune(companion, x_real, y_real)` 位置修正候选（或伴星系必要性核查）——先消除漏光，再看隆起是否仍在。
- **尚无 companion（或无一与隆起共位）但 2D 残差在同半径呈局部紧凑亮斑**（原图对应位置可见点源/展源则证据更强）：本规则的优先产出是 `add(Companion, x_blob, y_blob, 紧致先验 Re_init≈PSF 尺度至几 px, 中心 vary=1 ±2" 窗口)` 候选——用致密源解释该半径的隆起，而非 `add(Lens)`。坐标仍写像素值，由主模型后续转 arcsec。嵌入式伴星系的时机约束在此自动满足：路径 B 的前提是父状态已含 Bar 或 Bulge。
仅当 2D 残差在同半径呈方位连续环状（覆盖 ≳180°）、或 companion 新增/修正后隆起依旧时，才生成 `add(Lens)`。证据混合（同半径既有局部亮斑又有环状残差）时，两类候选可作为竞争候选并存，由主模型打分。

触发条件全部成立时，**必须**产出 `add(Lens)` 候选：

- **action**: `add(Lens, n<0.5 free, q>0.5, Re_init≈隆起峰值半径)`
- **profile**: `sersic`
- **n**: 自由（vary=1），初始 ~0.3，范围 [0.1, 0.5]（物理先验 n<0.5）
- **Re**: 自由，初始值取 1D 隆起峰值对应的半径；范围下界 > Re_bar（或 Re_bulge，取较大者），上界 < Re_disk，确保满足全序链
- **q (axrat)**: 自由，初始 ~0.8，范围 [0.5, 1.0]（Lens 近圆，q>0.5）
- **PA**: 自由，初始取 disk_ang（Lens 近圆，PA 不敏感）
- **中心**: 自由（vary=1），初始取星系中心附近
- **physical_motivation** 须引用：1D 隆起的精确位置（"Δμ 在 r≈XX px 处出现宽阔隆起，峰值 ≈−Y mag"）、2D 对应的环状残差特征、以及当前 Bar/Disk 均无法自然产生该过渡区通量的物理理由

### 自检硬约束

- **触发复核**：若父状态含 Bar 或 Bulge，且上述五条联合条件成立（含伴星系漏光排除），本次输出**必须**包含至少一个 `add(Lens)` 候选。若未产出，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"1D 隆起位置在 1.2·Re_bar，偏离典型 Lens 区间"或"隆起与未校准 companion 半径共位，优先修正伴星系"或"隆起对应 2D 局部紧凑亮斑，优先 add(Companion)"），不得静默跳过。若隆起与 companion 半径或 2D 局部亮斑共位但本次仍输出了 `add(Lens)`，physical_motivation 必须引用 2D 环状残差的方位连续性证据（覆盖 ≳180°）。
- **路径 A（Bar 异常）联动**：若父状态 Bar 同时满足路径 A 条件（`Re_bar ≳ Re_disk` 或 `q_bar ≳ 0.5`），可生成 `tune(Bar, split→Bar+Lens)`（拆分）或 `add(Lens)`（新增）候选之一，或两者并存测试不同假设。拆分候选的 physical_motivation 须引用 Bar 异常参数；新增候选的 physical_motivation 须引用 1D 隆起特征。

## Lens Re 膨胀触发规则（VLM 必读，生成竞争式路径）

**动机**：Lens 添加后常出现 Re 膨胀——lens Re 触上限、或 lens Re ≥ disk Re 导致 Re-ordering FAIL。此时 VLM 容易只给出"收紧 lens Re"单一方向，错过"增大 disk Re"和"移除 lens"这两个可能更优的修复路径。本规则强制 VLM 在 lens 膨胀信号出现时生成**三条方向不同的竞争式候选**，由主模型打分选择。

### 触发条件（任一满足即触发）

读取 `.gssummary` 中 lens 的 Re 拟合值，以及父轮次 lyric 中 lens 的 `P*5` 五元组 re_max：

| 指标 | 阈值 | 物理依据 |
|------|------|---------|
| **lens Re 触上限** | lens_Re ≥ 0.98 × re_max | 拟合器想让 lens 更大但被锁，是 lens 试图超越物理角色的信号 |
| **lens Re ≥ disk Re** | lens_Re ≥ disk_Re（Re-ordering FAIL 时 custom_instructions 会报告） | lens 膨胀超过 disk，违反 Re_disk > Re_lens 全序 |

### 触发后的候选生成（三条竞争式路径必出）

触发条件成立时，**必须**产出以下三条候选，覆盖三种不同的物理假设：

**候选 A — 收紧 lens Re 上限**
- action: `tune(lens, re_max = 0.9 × Re_above)`，其中 `Re_above` = 全序链中 lens 上方相邻成分（通常为 disk）的当前 Re
- 假设：lens 真实属于过渡区，膨胀是拟合器逃逸；收紧后 lens 回到正确角色
- 适用：disk Re 合理、lens 在过渡区确有独立通量贡献（1D 隆起仍存在）
- expected_behavior_tag 示例：`lens_re_bound_tighten`

**候选 B — 增大 disk Re**
- action: `tune(disk, Re_init = 1.3–1.5 × current_disk_Re)`，re_max 不收紧（让 disk 自由增大）；disk n 保持 fixed=1
- 假设：disk Re 本身偏小，lens 被迫膨胀去代偿 disk 外缘通量；增大 disk 后 lens 自然回缩
- 适用：disk Re 未触界（disk_Re < disk 的 re_max）、或 1D 曲线 r > 2×Re_disk 区域 Data 亮于 Model、或父轮次间 disk axrat 剧烈变化（身份不稳定）
- **禁止**：当 disk Re 已触自身 re_max 上限时，候选 B 不适用（应显式在 physical_motivation 中说明"disk 已触界，候选 B 不适用"并跳过）

**候选 C — 移除 lens**
- action: `remove(lens)`
- 假设：lens 是寄生/简并成分，其通量本应由 disk 或 bulge 承担；移除后重新分配通量更物理
- 适用：lens 通量占比可疑（Mag_lens ≤ Mag_disk + 0.2，即通量接近甚至超过 disk）、或 lens n 退化（趋近 1 变成 mini-disk）、或历史轮次证明移除 lens 后 BIC 改善
- expected_behavior_tag 示例：`lens_remove_parasitic`

### 自检硬约束

- **触发复核**：若上述触发条件成立，本次输出**必须**包含候选 A、B、C。若少出某条，必须在对应 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"disk Re 已触 re_max，候选 B 不适用"），不得静默跳过。
- **方向多样性**：A/B/C 三条的 expected_behavior_tag 必须两两不同。
- **与 §Disk 外围光度不足触发规则的关系**：候选 B 与该规则可能同时触发（一个基于参数状态、一个基于 1D 残差形状）。若两者同时命中，候选 B 满足双重义务，无需重复生成。

## Disk 成分 Sérsic 指数 n 的操作规范

Disk 成分的 n **一律固定为 1，vary=0，永不释放**。无论 beam search 处于哪个阶段（基础结构阶段或深化阶段），都**禁止**生成 `tune(Disk, n_free)` 候选。

- **角色判定（关键）**：GalfitS 里 "Disk 成分" 和 "单 Sersic" 都用 `Pa2) sersic`，但二者 n 策略相反，必须按**角色**而非 profile type 判断——
    - **Disk 成分**（多成分分解中的盘组件，与 Bulge/Bar/Lens 并列存在）→ **n 固定为 1，永不释放**。
    - **单 Sersic**（整星系仅一个 sersic 组件、无并列中心成分；椭圆星系终态或 Round 0 起步）→ n 自由，作为整体浓度观测量。
- **身份互换退化的联合诊断（保留）**：若拟合结果出现 `disk_n ≈ 下界`（仅当 Disk 成分被错误配置为自由时才会发生）**且同时** `bulge_Re ≈ 上界`，或反之 `bulge_n` 触界、`disk_Re` 异常，判定为 disk↔bulge 身份互换退化——判据必须是**联合诊断**，单看某一参数不构成退化。出现联合退化时，主模型在打分阶段对该候选施加退化惩罚（§去重与排序 维度 4）。正常遵守"Disk n 固定"规则的候选不会触发此项。

## 候选数量规则（按 depth 强制分段，严禁凑数）

搜索树浅层的下一步通常是确定性的（建立 Disk+Bulge 基础结构），没有必要并行探索；真正的分支发生在双成分结构稳定之后。按 depth 分段给出候选数：

### ⚠️ 阶段一检测性质（所有 depth 适用）

阶段一 `detect_galfits_bar_lopsidedness` 是**自上而下的形态学提示**，不是自下而上的成分判定。检出 = 弱正证据（积极生成对应候选）；**未检出 = 零证据，不是负证据**。bar/lop 可能在残差驱动的探索中被发现——典型情形：中心成分建立后高动态范围图揭示扁长内部结构，或 bulge 释放 n 后残差浮现四极矩 bar 签名。因此 depth ≥ 2 时，即使阶段一未检出 bar/lop，只要**残差证据或原图特征支持**，仍应正常生成 Bar / Fourier / Lens 候选，不得因"未检出"而自我审查跳过。

### depth = 1（父状态是输入 .lyric 的首次拟合结果）
依据 `working_note.md` 头部的阶段一 `detect_galfits_bar_lopsidedness` 结论决定候选数：
- **lopsidedness 检出**（任一波段）→ **1 个候选**：`tune(Disk, sersic→sersic_f)`（偏心优先级最高，先于加任何成分）。
- **lopsidedness 未检出 + bar 检出**（任一波段）→ **1–2 个候选**：`add(Bulge, n=4 fixed)`（标准 Disk+Bulge 拆分）与 `add(Bar, n=0.5 fixed, PA≈阶段一 PA)`（仅当原图 bar 特征强）。此处 `PA` 用 **sky-PA**（见 §🔑 PA 约定），阶段一 `detect_galfits_bar_lopsidedness` 返回的 `bar.pa_deg` 可直接拿来用。
- **两者都未检出** → **1 个候选**：`add(Bulge, n=4 fixed)`。（depth=1 先建 Bulge 骨架；bar 的探索留到 depth≥2 基于残差证据进行，不因阶段一未检出而关闭。）
- **例外**：若单 sersic 拟合残差清楚地显示侧视盘特征（b/a < 0.17 且残差有 dust lane / 盘厚度），可改为 **1 个候选**：`tune(Disk, sersic→edgeondisk)`。

### depth = 2（双成分基础结构已建立）
输出 **2–3 个**候选。典型方向：修约束、释放/收紧某参数、新增致密成分（Nucleus）、切换某成分模型类型。

### depth ≥ 3（深化探索）
输出 **2–4 个**候选。此时 beam search 的并行探索价值最大，应充分利用方向多样性。

### 通用规则（所有 depth 适用）
- **严禁凑数**：若无法生成足够多 `expected_behavior_tag` **两两不同**的候选，允许实际数量少于上述下限。宁可只给 1 个高质量候选，也不要给 2 个实质相同的凑数候选。
- **物理动机必须基于阶段一**：每个候选的 physical_motivation 必须引用阶段一描述过的具体残差特征（位置、强度、对称性等），严禁凭空推测。
- **遵循成分添加次序**（按伴星系位置分类）：
    - **外围伴星系**（距中心 ≳ 2·Re_disk）：`Disk → (F1/Outer Companion 若检出) → Bulge → Bar → Lens → Other`
    - **嵌入式伴星系**（距中心 ≲ 2·Re_disk，落在主星系 contour 内）：`Disk → Bulge → Bar → (Embedded Companion 若检出) → Lens → Other`
    - 即**嵌入式伴星系必须在 Bulge/Bar 之后**，外围伴星系无此约束。Bar/Lens/Nucleus 的认定条件须符合 `<星系成分分析的总体流程>` 章节。
    - **Lens 候选生成时机（两条独立路径，满足任一即触发）**：
        - **路径 A（Bar 异常反推）**：当父状态的 Bar 出现物理异常（`Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5`，即 Bar 被强行拉去拟合 Lens 结构）时，应生成拆分候选：`tune(Bar, split→Bar+Lens)` 或 `add(Lens, n<0.5 free, q>0.5, Re between bulge/bar and disk)`。
        - **路径 B（1D 轮廓隆起）**：当 1D 表面亮度残差曲线在距中心 ~1.5–2.5·Re_bar 处出现宽阔正向隆起（详见 §Lens 1D 轮廓隆起触发规则），即使 Bar 参数完全正常，也应生成 `add(Lens, n<0.5 free, q>0.5, Re_init≈隆起峰值半径)` 候选。
        - Lens 用 `sersic`，n 自由（vary=1）但物理先验 n<0.5，Re 满足全序基准 `Re_disk > Re_lens > Re_bar > Re_bulge`（仅比较实际存在的中心成分，把缺失者从链中剔除后按相对顺序严格递减），q>0.5，与 bulge/bar/disk 同心。
- **尊重历史**：补充信息中"已尝试动作"列表里的动作不得重复提出（除非换个明显不同的参数化方向）。
- **方向多样性**（多候选时）：候选之间必须覆盖**显著不同**的探索方向。典型对比组合：
    - "加成分" vs "调参"（如 +Nucleus(致密) vs release bulge_n）
    - "修约束" vs "换模型类型"（如 修复 bulge↔disk 同心 vs 切换 Disk→edgeondisk）
    - "奥卡姆剃刀" vs "深化"（如 remove(nucleus) vs tighten bulge_Re 上限）
    - "拆 Bar→Bar+Lens" vs "收紧 Bar Re 上限"（当父状态 Bar 的 Re/q 物理异常时，拆出 Lens 吸收延展成分 vs 用约束把 Bar 压回合理区间）
    - "add(Lens) 吸收 1D 隆起" vs "调 Disk/Bar 边界"（当 1D 轮廓在 1.5–2.5·Re_bar 出现宽阔隆起但 Bar 参数正常时，加 Lens 成分吸收过渡区通量 vs 尝试用调参让现有成分覆盖该区域）
    - "伴星系位置修正" vs "伴星系形态修正"（如 tune(companion, x_real, y_real) vs tune(companion, q_init=0.9, Re<=2")）；仅当阶段一已报告位置偏差 > 2 px 时，位置修正候选才是必须的，否则优先形态修正
    - "lens 膨胀时三路径竞争"（当 lens Re 触上限或 lens_Re ≥ disk_Re 时：收紧 lens Re vs 增大 disk Re vs 移除 lens，三者测试不同物理假设；详见 §Lens Re 膨胀触发规则）

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

## 伴星系移除验证（当 custom_instructions 含"伴星系条件 A 命中"时执行）

当补充信息中出现伴星系通量比 ≤ 1% 的数值报告（标注为"伴星系条件 A 命中"），VLM **必须**执行以下条件 B 视觉验证，决定是否生成 `remove(Companion)` 候选：

1. **定位**：从参数摘要中读取 companion 的 xcen/ycen（arcsec offset），结合 R2 中心，推算 companion 在图上的像素位置；或从残差图上识别伴星系残差位置反推原图位置。
2. **查看原图面板**（**不是残差面板！不是模型面板！**），判断该位置是否有肉眼可见的小亮斑点：
   - **无可见亮斑**（该位置干干净净，或仅被 mask 覆盖）→ 条件 B 命中 → A∧B 成立 → **生成** `remove(Companion)` 候选。physical_motivation 须同时引用数值证据（通量比、ΔMag 实测值）与视觉证据（"原图 companion 位置无可见源，仅为模型伪迹"）。
   - **有可见亮斑**（原图该位置有明确的独立亮斑 / 点源 / 展源）→ 条件 B 不命中 → A∧¬B → **禁止**生成 `remove(Companion)` 候选。该 companion 是真实致密源（即使通量比很低），必须保留。
3. **只看原图面板**：残差面板上的正残差**不能**作为"可见源"的证据——即使 companion 是假的，残差面板也可能因模型未拟合该位置而出现正残差。原图面板是物理真实性的唯一仲裁。

## 自检（生成后必须逐项确认）
- 候选数量符合当前 depth 的分段规则（depth=1 多数情况为 1 个；depth=2 为 2–3 个；depth≥3 为 2–4 个）
- 每个候选 primitives 数量 ∈ [1, 2]，且语义内聚
- 多候选时，所有候选的 `expected_behavior_tag` **两两不同**（硬约束；无法满足则减少候选数）
- **禁止生成释放 Disk n 的候选**（Disk 的 n 一律固定为 1，见"Disk 成分 Sérsic 指数 n 的操作规范"）；单 Sersic 模型（无 Bulge/Bar/Lens 并列）除外
- physical_motivation 引用的特征均在阶段一出现过
- 已尝试动作未被重复提出
- 所有候选的 expected_C' 与当前父状态的 C' 差异均可解释
- **Disk 外围光度触发复核（硬约束，优先级高于中心成分规则）**：必须确认已在 1D 残差曲线上检查 r > 2×Re_disk 外围区域的系统性正残差（见 §Disk 外围光度不足触发规则的三条触发条件：位置 + 宽度幅度 + disk Re 未触界）。条件全部成立时，本次输出**必须**包含至少一个 `tune(disk, Re 更大)` 候选，且优先级排在候选列表前列；若未产出，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"外围正残差幅度仅 −0.03 mag，未达 −0.05 mag 阈值"或"正残差仅在背景极限后的红三角区出现"），不得静默跳过。
- **Lens 触发复核（硬约束）**：父状态含 Bar 或 Bulge 时，必须确认已主动回忆**两条** Lens 触发路径：
    - **路径 A（Bar 异常反推）**：`Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5`。
    - **路径 B（1D 轮廓隆起）**：1D 残差曲线在 ~1.5–2.5·Re_bar 处出现宽阔正向隆起（位置 + 宽度 + 幅度 + 2D 方位连续环状 + 伴星系漏光排除五条联合条件，见 §Lens 1D 轮廓隆起触发规则）。**路径 B 触发前必须确认已执行伴星系污染前置检查**（见阶段一）：若隆起区间覆盖某 companion 半径且 2D 残差为局部紧凑亮斑，本次输出的优先候选应是 `tune(companion, ...)`（已有共位 companion）或 `add(Companion, ...)`（尚无共位 companion，2D 残差为局部亮斑）而非 `add(Lens)`；若仍生成 `add(Lens)`，physical_motivation 必须引用 2D 环状残差的方位连续性证据（覆盖 ≳180°）。
    - **任一路径条件成立但本次未产出任何 Lens 相关候选**（`add(Lens)` 或 `tune(Bar, split→Bar+Lens)`）时，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"Bar 异常但残差更支持 X 方向"或"1D 隆起位置在 1.2·Re_bar，偏离典型 Lens 区间"），不得静默跳过。
- **Lens Re 膨胀触发复核（硬约束）**：若补充信息报告 lens Re 触上限（lens_Re ≥ 0.98 × re_max）或 lens_Re ≥ disk_Re（Re-ordering FAIL），必须确认本次输出包含候选 A（收紧 lens Re）/ B（增大 disk Re）/ C（移除 lens）三条竞争路径（详见 §Lens Re 膨胀触发规则）。若少出某条，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**（如"disk Re 已触 re_max，候选 B 不适用"），不得静默跳过。
- **嵌入式伴星系时机复核（硬约束）**：若生成了 `add(Companion)` 候选且阶段一报告该伴星系为嵌入式（落在主星系等照度线 contour 上或以内，距中心 ≲ 2·Re_disk），**必须确认父状态已含 Bulge 或 Bar**。若父状态既无 Bulge 也无 Bar 却出现了嵌入式伴星系残差，**禁止**生成 `add(Companion)` 候选——应改为 `add(Bulge)` 候选，待中心骨架建立后再处理伴星系。违反此约束的典型失败模式：伴星系被拽向中心、三参数（Re/xcen/ycen）全部撞界发散。
- **伴星系移除验证复核（硬约束）**：若补充信息中含"伴星系条件 A 命中"（通量比 ≤ 1%），必须确认已在**原图面板**上做过条件 B 视觉验证。若原图 companion 位置有可见亮斑却生成了 `remove(Companion)` 候选，视为违反约束。
- **扁 Bulge → Bar 触发复核（硬约束）**：父状态含 Bulge 时，必须按"扁 Bulge → Bar 候选触发规则"的四条联合条件（bulge b/a < 0.5 AND PA 夹角 > 20° AND bulge n ∈ (0.5, 2.5) AND disk b/a > 0.5）做核对。条件全部成立时，本次输出**必须**包含至少一个 Bar 方向候选（`tune(Bulge→Bar)` 转换 或 `add(Bar)+tune(Bulge, q_min=0.7)` 新增）；若未产出，必须在 Candidate 的 physical_motivation 中**显式说明放弃理由**，不得静默跳过。
- **主星系同心约束复核（硬约束）**：本轮输出的每个候选，只要其 `expected_C'` 包含 **≥ 2 个主星系中心成分**（Disk/Bulge/Bar/Lens，不含伴星系），就**必须**默认继承同心约束——`add(...)` 类候选的 `physical_motivation` 必须显式提及"通过 `.constrain` 绑定同一中心"；`tune(...)` / `remove(...)` 类候选默认继承无需重复声明。**伴星系（label 含 comp/companion/secondary/satellite）严禁参与**主星系同心约束。若本次输出中出现 `add(Bulge/Bar/Lens/AGN)` 候选却未在任何候选的 physical_motivation 中提及同心绑定，视为违反约束。
