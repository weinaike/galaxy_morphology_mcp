
要求严格遵循工作流开展多波段(multi-band)星系拟合分析工作。

仅关注拟合盘、核球、侧视盘、棒、Lens、AGN核、偏心（Disk 上的 m=1 Fourier 模式）、伴星系这八种物理成分，仅可对这八种成分的残差添加模型成分拟合，其他残差特征可以选择保留不拟合。其中偏心（Fourier m=1）的添加由阶段一 detect_galfits_bar_lopsidedness 的检出结果驱动：lopsidedness 检出时，应作为最高优先级修正项参与阶段二的结构迭代（在增加其他成分之前先加 m=1）；未检出时进入阶段三再由 fourier_mode_analysis 做残差二次确认。
图像分析与拟合执行只能使用 galmcp 中的工具，不能直接 shell 执行 GalfitS 命令。所有 GalfitS 拟合必须使用 `--fit_method ES`。严禁使用4_5v_mcp的相关工具。
必须建立 todos 并独立完成所有阶段，直到 Image-SED 联合拟合成功。

## workflow

阶段一. 查看星系目录与原图分析
* **查看星系目录：** 确认所需的文件是存在的，包括 FITS 图像、掩膜文件、背景估计文件、lyric配置文件等。
* **查看原始数据与图像：** 使用 render_original, view_original_image, detect_galfits_bar_lopsidedness 工具分析原图，确认星系的基本形态特征（如是否存在明显的核球、盘、棒结构等）。这将为后续的拟合提供初始猜测值。
* **detect_galfits_bar_lopsidedness 结果解读与固化：**
    - 工具返回结构为 `{"results": [{band, bar:{detected, pa_deg, b_over_a}, lopsidedness:{detected, mag, phase_deg}}, ...]}`，按波段列表。
    - **检测性质（重要）**：此检测是**自上而下的形态学提示**（top-down hint），不是自下而上的成分级判定（bottom-up verdict）。它只看原图的等照度线/傅里叶特征，不经过"添加成分 → 看残差是否改善"的拟合验证。因此：
      - **检出（detected=True）= 弱正证据**：存在该成分的先验概率升高，阶段二应积极生成对应候选（但不保证拟合一定接受）。
      - **未检出（detected=False）= 零证据，不是负证据**：不构成"该成分不存在"的证明。bar/lop 可能在残差驱动的自下而上探索中被发现（典型情形：中心成分建立后，高动态范围图揭示出扁长内部结构；或 bulge 释放 n 后，残差浮现四极矩 bar 签名）。
      - **金标准**：判定成分存在性的最终依据是残差驱动的拟合验证（add → refit → 残差改善 + 参数物理），不是阶段一检测。
    - **跨波段 OR-logic**：任一波段 `bar.detected=True` → bar 先验概率升高，作为阶段二积极生成 Bar 候选的提示；任一波段 `lopsidedness.detected=True` → lop 先验概率升高，作为阶段二高优先级添加 Fourier m=1 的提示。
    - **PA 取值规则**：Bar 的 PA 优先取蓝端波段（如 F115W）的返回值；红端（F200W/F444W）作参考。`detect_galfits_bar_lopsidedness` 返回的 `pa_deg` 已经是 **sky-PA**（正北 0° 逆时针），可直接写入 `.lyric` 的 `Pa7`，无需任何换算。
    - **偏心添加决策**：任一波段 `lopsidedness.detected=True` → 在 `working_note.md` 头部标记 "m=1 Fourier 高优先级"，阶段二每次调用 `generate_beam_actions` 时需把该标签写入 `custom_instructions`，确保 VLM 在 Disk 已建立后第一时间给出"把 Disk 的 `Pa2) sersic` 改为 `sersic_f`"的候选动作。
    - **写入 working_note.md 头部**：将每波段 bar/lop 检测结论、PA、b/a、A1、phi1 固化到 `working_note.md`，供后续所有迭代轮次的 `generate_beam_actions` 读取（工具会自动把 `working_note.md` 内容注入 VLM 上下文）。**措辞告诫**：未检出的成分必须写成"未检出（零证据，非判定性）"或类似明确标注其非判定性质，不得写成裸的"NOT detected / 不存在"，避免 VLM 与主模型在打分阶段把提示性零证据误解为判定性负证据。

阶段二. 结构搜索与动态校验 (Beam Search 模式)
*目标：通过束宽 W=5 的 beam search 在结构空间中并行搜索最优的物理成分组合，避免贪心单路径在退化轮次（如约束失效、参数坍缩）处陷入局部最优。每个束内分支仍遵循"自下而上、完成一个成分拟合后再考虑新增"的渐进式理念；beam search 只是把"单一下一步"扩展为"多条并行候选路径"。*

### 常量定义（硬性，不随星系类型调整）
- 束宽 W = 5（优先队列最大长度）
- 全局拟合预算 N_max = 15（每次 `run_galfits_image_fitting` 调用，无论成功失败，都计数一次）
- 早停阈值 S_max = N_max = 15（连续无改进次数上限，当前设为与 N_max 相同，即早停实际上不生效，仅由拟合预算 N_max 控制终止）

### 形式化定义（精简版，便于智能体维护一致的状态语义）
- **状态** s = (C, P, R, reduced_χ², BIC, depth)，其中 C 为成分清单、P 为对应参数（`.lyric` 中的 `P*` 五元组）、R 为残差诊断（`all_bands_comparison.png` + 1D 残差特征）、reduced_χ² 与 BIC 取自 `.gssummary`、depth 为该状态在搜索树中的深度（s₁ 的 depth=1）。
- **动作** a = 复合动作，由 1–2 个语义内聚的原子操作组成。原子操作有三类：`add(type, params)` 新增成分、`remove(component)` 删除成分、`tune(component, param_delta)` 调参（含释放/固定 vary、收紧/放宽边界、修改 .constrain）。禁止捆绑无关联的原子操作。
- **转移** T(s, a) = s'：拷贝父状态的 `.lyric` → 按 a 修改 → 写 `_iter{n}.lyric` → `check_lyric_file` → `run_galfits_image_fitting --fit_method ES` → 读 `.gssummary` 抽取 reduced_χ²/BIC → 调用 `generate_beam_actions` 获取下一层候选。s'.depth = s.depth + 1。
- **初始状态** s₀：从输入 `.lyric` 解析得到（C={sersic}, P={输入参数}, R=原图诊断, reduced_χ²=⊥, BIC=⊥, depth=0）。s₀ 不是拟合产物，而是输入；首次拟合（步骤 0.4）对 `_iter1.lyric` 跑一次 `run_galfits_image_fitting` 直接得到 s₁，**不经过候选生成**。
- **当前最优** s\*：按主模型综合评分最高者（评分维度见 §去重与排序），**不是**单纯按 reduced_χ² 最低。

### 步骤 0. 初始化（每个星系只执行一次）
1. 在星系主目录创建（或重置）`working_note.md`，按本节末尾的 §多分支 working_note 模板初始化空壳；把阶段一的 VLM 形态判断、bar/lop 跨波段 OR-logic 结论、PA 取值、b/a 全部写入头部。
2. 初始化：全局拟合计数 n = 0；全局 `.lyric` 文件计数 global_iter_id = 0；分支计数 branch_counter = 1（即 "A"）；当前最优 s\* = None；优先队列 Q = []；连续无改进计数 stagnation = 0。
3. 调用 `render_original` 渲染原图（如阶段一未做）；记录原图对比图路径。
4. **首次拟合（确定性，不调用 VLM）**——输入 `.lyric` 本身已经包含一个 sersic 起手成分，首次拟合直接对它跑，不需要候选生成：
    1) `global_iter_id += 1`（→ 1）。用 Read + Write 把输入 `.lyric` 原样拷贝为星系主目录下的 `_iter1.lyric`（命名统一，便于后续 `_iter{n}.lyric` 序列化管理；下次重跑前会清除历史记录，不会有冲突）。
    2) 调用 `check_lyric_file(_iter1.lyric)` 校验；失败按提示修复。
    3) 调用 `run_galfits_image_fitting(config_file=<_iter1.lyric 绝对路径>, extra_args=["--fit_method", "ES"])`。`n += 1`（→ 1）。
    4) 失败处置：若工具异常或未产出 summary/对比图，说明输入 `.lyric` 本身有问题——这是退化情形，不进入主循环，改为人工介入修复输入后重新执行步骤 0。
    5) 构造 s₁：`C₁`、`P₁` 取自输入 `.lyric`（首次拟合不修改成分与参数）；从产出的 `.gssummary` 读 reduced_χ² 与 BIC；`R₁` 取 `all_bands_comparison.png`。`s\* = s₁`（目前唯一状态）。在 `working_note.md` 分支 A 下追加 A.1 小节（fit #1，.lyric = `_iter1.lyric`）。
5. **首候选生成（depth=1）**：以 s₁ 的对比图为 `comparison_file`、`_iter1.lyric` 为 `lyric_file`、s₁ 的 `.gssummary` 为 `summary_file`，调用：
    ```
    generate_beam_actions(
        lyric_file        = <_iter1.lyric 绝对路径>,
        summary_file      = <s₁ 的 .gssummary 绝对路径>,
        comparison_file   = <s₁ 的 all_bands_comparison.png 绝对路径>,
        working_note_file = <working_note.md 绝对路径>,
        branch_id         = "A",
        parent_label      = "A.1",
        depth             = 1,
        custom_instructions = "<按 §custom_instructions 内容规范 填写：阶段一 bar/lop 结论 + s₁ 拟合结果的具体问题（触界参数/残差特征/成分身份异常等）；严禁给出具体候选方向建议>",
    )
    ```
    工具会按 depth=1 规则返回 1–2 个候选（lop 检出 → 1 个 sersic_f 切换候选；bar 检出 → 1–2 个 Bulge/Bar 候选；都未检出 → 1 个标准 add(Bulge) 候选）。
6. 对每个返回的候选，主模型按 §去重与排序 打分得到 g ∈ [0,1]；按 g 降序截断到 W=5 入队 Q。每个队列元素记录 `(s_parent=s₁, a, σ_from_vlm, g, branch_id, depth=2)`——注意：从这些候选执行转移得到的下一状态深度为 2。
7. 更新 `working_note.md` 的 Beam 状态快照。

### 步骤 1. 主循环（在终止条件未触发前持续执行）
```
while Q 非空 and n < 15 and stagnation < 15:
```
a. **出队**：从 Q 取出 g 最高的 (s, a, σ, g, branch, depth)。把它从 Q 中移除。
b. **执行转移 T(s, a)**：
    1) `global_iter_id += 1`；拷贝 s 对应的 `.lyric`，按 a 的 primitives 修改成分与参数，写入星系主目录的 `_iter{global_iter_id}.lyric`。**转写时必须严格遵守 §候选动作忠实执行原则**——候选声明中的语义核心字段（成分类型、n/vary 状态、量级约束、增删/中心约束策略、Fourier 阶数等）不得擅自修改；若主模型认为某候选有缺陷，应整条丢弃（记入"跨分支决策日志"），而不是修改后执行。
    2) **主星系同心约束检查（硬约束，无论 a 是否声明约束都必须执行）**：统计本轮 `_iter{global_iter_id}.lyric` 中主星系中心成分（Disk/Bulge/Bar/Lens，即 P 块且 label 不含 `comp`/`companion`/`secondary`/`satellite`）的数量 K：
       - **K ≥ 2**：**必须**写 `iter{global_iter_id}.constrain`（命名遵循 `Update_Constraints` 规范），把所有主星系中心成分的 xcen/ycen 绑定到 Disk（`pardictlc['bulge_xcen'] = 1 * pardictlc['disk_xcen']` 等成对出现，严禁仅绑定一个变量）。在 lyric 中把 Bulge/Bar/Lens 的 `P*3`/`P*4` 设为 `vary=0`（Disk 的 `Pa3`/`Pa4` 保持 `vary=1` 作为同心锚点）。AGN/N 块若共存则用 `xcen_agn`/`ycen_agn`（不是 `agn_xcen`）同样绑定到 disk。**伴星系（label 含 comp/companion/secondary/satellite）的中心严禁参与此约束**——伴星系中心必须保持 `vary=1` 自由拟合。调用 `run_galfits_image_fitting` 时必带 `--parconstrain iter{global_iter_id}.constrain`。
       - **K ≤ 1**（仅单 Disk 或起手单 sersic）：不写约束文件，正常拟合。
       - 该检查是主模型的强制职责，**不得依赖 VLM 候选声明**——即使 VLM 候选未提及同心约束，主模型也必须按上述规则补齐 `.constrain` 文件。
    3) **必须调用 `check_lyric_file`** 校验格式；失败按提示修复后再次校验，不得跳过。
    4) 调用 `run_galfits_image_fitting`，必带 `--fit_method ES`；若步骤 2) 产出了 `.constrain` 则必带 `--parconstrain iter{global_iter_id}.constrain`。`n += 1`。
    5) **失败处置**：若工具异常或未产出 summary/对比图，把该 (s, a) 记入 `working_note.md` 的"分支: 失败归档"小节，把 a 加入 s 的禁忌集，`stagnation += 1`，回到循环开头。
c. **构造新状态 s'**：从新生成的 `.gssummary` 读 reduced_χ² 与 BIC；`R'` 取新生成的 `all_bands_comparison.png`；`C'`、`P'` 取自新的 `.lyric` 与 `.gssummary`。轮次命名：在所属分支内取 `branch.local_round`（如 A.2、A.3、B.1…，A.1 已被首次拟合占用），与 global_iter_id 解耦。s' 的深度 = `depth + 1`。
c.1 **Re 全序程序化校验**（Re-ordering gate）：调用 `check_re_ordering(summary_file=<新.gssummary 绝对路径>, lyric_file=<新_iter{n}.lyric 绝对路径>)`。该工具在 arcsec 域按基准链 `Re_disk > Re_lens > Re_bar > Re_bulge` 的子序列规则做严格数值比对，把 AGN(N 块) 与伴星系自动排除。
   - **status="pass"**：正常进入 d。
   - **status="fail"**：
     1. **该轮次直接失去 s\* 候选资格**（Re 反置视为拟合失败，即使 χ²/BIC 更优也不参与 beam 评分）；在 `working_note.md` 该轮小节标注"Re 反置否决"，并粘贴工具返回的 `violations` 清单作为证据。
     2. 若 `swappable_overall=True`（反置仅涉及 {Disk, Bulge}）：主 agent **直接生成**交换 disk ↔ bulge 标签后的 `_iter{n+1}.lyric`（复用 s' 的其他参数；经 `check_lyric_file` 校验后进入下一轮拟合），**跳过** generate_beam_actions 调用。
     3. 若 `swappable_overall=False`：把返回的 `custom_instructions_hint` 字段**原样拼接**到下一步 `generate_beam_actions` 的 `custom_instructions` 末尾；并在步骤 e.ii 中按 **§非物理结果恢复协议** 生成受保护的恢复候选（与 VLM 候选一同入队竞争）。正常走候选生成流程。
   - **status="error"**：记录 `error_message` 到 `working_note.md`，**不阻断**（沿用现状，由落锁前 verifier 兜底）；进入 d。
d. **登记 s' 评分并更新 s\***：按 §去重与排序 中的 score 函数给 s' 打分；若 score(s') > score(s\*)，s\* ← s'，`stagnation = 0`；否则 `stagnation += 1`。覆写 `working_note.md` 的"Beam 状态快照 / 当前最优 s\*"小节。**注意：stagnation 仅用于终止判定，不构成跳过步骤 e 的理由——s' 的后继可能优于 s\*（详见 §候选生成的诊断式原则）**。
e. **候选生成（无条件硬约束——见 §候选生成的诊断式原则；两个正交来源合并后统一进入步骤 f 打分入队）**：本步骤只要步骤 b 拟合成功就**必须**执行（失败处置分支 b.5 除外），无论 s' 是否更新 s\*、BIC 是否反升、参数是否触界、队列是否仍有未消费候选、拟合预算是否紧张。每轮主循环的候选由两个触发条件正交的来源并行生成——e.i 由 VLM 基于残差图像的视觉分析驱动，e.ii 由主模型基于 `.gssummary` 的客观阈值驱动。两类候选合并后走完全相同的去重 / 打分 / 截断规则（步骤 f），彼此平等竞争入队。
    - **e.i VLM 视觉驱动候选**：以新对比图为 `comparison_file`、新 `.lyric` 为 `lyric_file`、新 `.gssummary` 为 `summary_file`，调用：
        ```
        generate_beam_actions(
            ...,
            branch_id         = branch,
            parent_label      = <branch>.<local_round>,
            depth             = depth + 1,   # 新候选应用到的父状态深度
            working_note_file = <abs path>,
            custom_instructions = "<按 §custom_instructions 内容规范 填写：阶段一结论 + 父轮次已尝试动作清单 + s' 拟合结果的具体问题（触界参数/Re 全序校验 violations/残差特征/成分身份异常等）；严禁给出具体候选方向建议>",
        )
        ```
        工具按 `depth+1` 的分段规则返回候选（depth+1=2 → 2–3 个；depth+1≥3 → 2–4 个）。
    - **e.ii 主模型数值规则驱动候选**：主模型基于 s' 的 `.gssummary` 客观数值检查，向 VLM 委托需视觉验证的候选。这类候选针对"视觉上可见但数值上可疑的成分"——VLM 从图像上看到该成分存在会倾向于保留，但数值上若可疑（如通量极低），主模型把客观观数据交给 VLM，由 VLM 结合原图视觉验证决定是否生成移除候选。当前定义的触发规则：
        - **伴星系必要性检查（数值 + 视觉双轴判据）**：若 s' 含 Companion，读取 `.gssummary` 中 companion 与 disk 的 `logNorm_<component>_<band>` / `Mag_<component>_<band>`，计算通量比 `f_companion/f_disk = 10^(−0.4·ΔMag)`（其中 `ΔMag = Mag_companion − Mag_disk`）。
          - 若通量比 > 1%：companion 通量显著，不触发移除检查。
          - 若通量比 ≤ 1%（**条件 A 命中**）：主模型**不直接生成 remove 候选**，而是把三项数值（通量比、ΔMag、`ΔlogNorm = logNorm_companion − logNorm_disk`）写入当轮 `generate_beam_actions` 的 `custom_instructions`，格式为："伴星系条件 A 命中：companion 通量比 = 0.4%, ΔMag = 5.91, ΔlogNorm = -2.37。请 VLM 做条件 B 视觉验证：查看原图面板 companion 位置是否有肉眼可见亮斑，无可见源才生成 remove(Companion)。" 由 VLM 在候选生成阶段执行条件 B 视觉验证（见 `beam_action_generation_prompt.md` §伴星系移除验证）：仅当 A（数值暗）AND B（原图无可见源）同时成立时，VLM 才生成 `remove(Companion)` 候选。若原图有可见亮斑（B 不命中），VLM 不生成 remove 候选，companion 保留。
          - 若 s' 不含 Companion，不触发。
        - 未来若需扩展其他客观数值触发（如 sky 背景异常、某成分 Mag 异常暗等），按同样模式在此子项追加规则。
    - **追溯标记**：e.ii 触发的数值检查（无论 VLM 最终是否生成 remove 候选）在 `working_note.md` 的相应分支小节标注"[主模型数值规则委托]"，记录三项实测数值与 VLM 的视觉验证结论（原图有无可见源）。便于后续审计伴星系保留/移除决策的依据。
f. **去重 + 打分 + 入队**：主模型对每个新候选（e.i VLM 候选与 e.ii 主模型数值规则候选合并后的完整集合）：
    - 与 Q 中已有 (s_j, a_j) 做 §去重与排序 的语义去重；若等价则保留 g 较高者。
    - 对保留者按六维打分得到 g。
    - **g_min 阈值**：若 g < 0.3，直接丢弃，不入队（记入 `working_note.md` 的"跨分支决策日志"，标注 action_id 与丢弃原因）。这避免低质量候选堆积导致队列永不空。
    - 把 (s', a_new, σ_new, g, branch, depth=depth+1) 加入 Q；按 g 降序重新排序；截断到 W=5。被截掉的元素同样记入"跨分支决策日志"。
g. **持久化**：在 `working_note.md` 的相应分支小节追加本轮记录（配置/工具调用/成分/C、P 摘要/reduced_χ²/BIC/VLM 残差特征/入队的 action_id 列表）；覆写 Beam 状态快照（含 Q 的当前 5 项与 n 计数）。
h. **派生新分支（可选）**：当主模型发现某候选与当前束内主流方向显著不同、且 g ≥ 0.5 时，可标记新分支字母（branch_counter += 1，如 "B"），并在 working_note.md 新建 "分支 B" 小节。新分支共享全局 n 与 global_iter_id，避免预算失控。

### 步骤 2. 终止条件（任一满足即停）
- Q 为空；
- n ≥ 15；
- stagnation ≥ 15（连续 15 次出队的最高优先级 (s, a) 都没产生比 s\* 更优的 s'，即束内收敛；当前阈值与 N_max 相同，实际由 n ≥ 15 先行触发）。

### 步骤 3. 进入阶段三前的收尾
1. 在 `working_note.md` 的"跨分支决策日志"写下：终止条件、累计拟合次数 n、被探索过的分支数、被截掉的候选 action_id 列表。
2. 锁定 s\*：在 `working_note.md` 头部的"Beam 状态快照 / 当前最优 s\*"小节确认其对应的 `output/<timestamp>_<lyric_stem>/` 目录与 `_iter{global_iter_id}.lyric` 文件路径——这两个路径将作为阶段三、四、五的输入。
3. **同心约束合规性回查（硬约束）**：若 s\* 的主星系成分数 K ≥ 2（Disk/Bulge/Bar/Lens），但其对应的 `_iter{n}.lyric` 与 `run_galfits_image_fitting` 调用未附带 `.constrain` 文件与 `--parconstrain`，视为流程违规——回退到步骤 1.b.2 补齐 `.constrain` 后重跑该轮拟合，再进入阶段三。
4. 若 s\* 是退化状态（如成分参数碰边界、bulge/disk 通量完全相同），不要强行进入阶段三；改为：把"修复退化"作为强约束写入 `generate_beam_actions` 的 `custom_instructions`，重启一轮 beam search（重置 Q 与 stagnation，但保留 n 与 global_iter_id 计数）。

### §候选生成的诊断式原则（主模型硬约束）

**核心命题**：步骤 e（候选生成）是 beam search 的诊断回路，不是"拟合改善时的奖励"。只要步骤 b 拟合成功产出 summary/对比图（即未进入 b.5 失败处置分支），**必须**无条件执行步骤 e——无论 s' 是否更新 s\*、BIC 是否反升、参数是否触界、队列是否仍有未消费候选、拟合预算是否紧张。此规则无例外——VLM 的视觉诊断是 beam search 的核心回路，跳过它会让主模型退化为"看数字猜方向"的贪心搜索，丧失多模态诊断能力。

**原理**：s' 的 BIC 反升不等于物理假设错误——常见情况是候选的物理方向正确，但某个次级参数（中心位置 / PA / Re 量级 / n / q）初始化不当，拟合器收敛到次优解。此时 s' 的残差携带"哪个参数需要修正"的诊断信息，只有调用 `generate_beam_actions` 才能把残差转译为修正候选。跳过步骤 e 会令 beam search 退化为贪心搜索，错过"同方向、修正参数"的后继——这正是 beam search 相对贪心搜索的核心价值所在。

**通用失败→修正模式**（由 VLM 在候选生成阶段自主识别；主模型不得在 `custom_instructions` 中预先指定这些方向，见 §custom_instructions 内容规范）：
- 成分中心位置初始估计有误 → s' 残差在"模型位置"与"真实位置"之间呈偶极 → `tune(component, x_real, y_real)`
- 成分 PA 与真实主轴斜交 → s' 残差呈四极矩 → `tune(component, pa)`
- 新增成分 Re 量级偏小 → s' 残差呈中心环状正残差 → `tune(component, Re_init≈...)`
- 新增成分与父状态成分简并 → s' 中某成分身份坍缩（n/Re 触界）→ 释放/固定 n，或加同心约束打破简并

**执行校验**：下一轮迭代出队前，确认 working_note 相应分支小节已有"本轮 generate_beam_actions 返回的候选 action_id 列表"记录；若缺失，视为漏执行——禁止出队，回到步骤 e 补做。

### §非物理结果恢复协议（Re-ordering FAIL 的受保护恢复候选）

**核心命题**：Re-ordering FAIL 时，VLM 基于残差图生成的修正候选往往关注视觉可见的问题（PA 偏移、中心偏移等），而不太关注"收紧 Re 边界"这类程序化诊断驱动的机械修正——因为 Re-ordering 违规是 `check_re_ordering` 工具用精确数值诊断的（如 `re_lens=13" > re_disk=7"`），不是视觉判读的。如果恢复候选和其他候选一样走 g_min=0.3 的截断，往往会因评分偏低（残差改善不直观）被丢弃，导致 Re-ordering FAIL 的路径被过早放弃。

本协议的解决方案是：**不绕过 VLM**（VLM 照常在步骤 e.i 调用），而是由主模型在步骤 e.ii 生成**受保护的恢复候选**，通过 §去重与排序 的"强制保留条款"机制保证其入队（g ≥ 0.5 保底），与 VLM 候选一同公平竞争。当恢复候选被出队并拟合后，照常走 b→c→d→e 全流程（包括 VLM 候选生成），恢复链通过 beam search 的自然迭代逐步推进。

**触发条件**：步骤 c.1 中 `swappable_overall=False` 的 Re-ordering FAIL。

**恢复候选的生成规则**（主模型在步骤 e.ii 执行，与伴星系检查并列）：

若 s' FAIL Re-ordering（swappable=False），从 `violations` 中识别**膨胀成分**（Re 超过链中上方成分的那一个），生成以下 1-2 个恢复候选：

**恢复候选 A（Re-bound 收紧 + 热启动）**：
- `action_id`: `<branch>-<parent>-recovery-rebound`
- `primitives`: `tune(inflated_component, re_max = 0.9 × Re_above)`，其中 `Re_above` = 链中上方相邻成分的当前拟合 Re。若膨胀成分是 disk，则设 `disk re_init = 1.5 × max(下属 Re)`（不设上限，而是推大初始值）。其余参数从 s' 的拟合值热启动。
- `expected_C'`: 同 s'（不增删成分，仅调边界）
- `expected_behavior_tag`: `re_bound_enforce`
- **保底分 g ≥ 0.5**（强制保留条款，豁免 g_min 截断）

**恢复候选 B（路径最近 PASS 态热启动 + 收紧）**：
- `action_id`: `<branch>-<parent>-recovery-warmstart`
- `primitives`: 以当前 beam search 路径上**最近的 Re-ordering PASS 状态**的全部成分拟合值为初始值，叠加候选 A 的 Re 边界收紧。
- `expected_C'`: 同 s'
- `expected_behavior_tag`: `warmstart_rebound`
- **保底分 g ≥ 0.5**（强制保留条款）
- 仅在候选 A 生成后的下一轮（如果 A 的拟合仍 FAIL）才生成——因为 A 和 B 是顺序链的两步，A 先探索，A FAIL 后 B 才有"路径最近 PASS 态"作为热启动源。

**渐进放宽（等级 3，自然融入 beam search）**：若候选 A 或 B 拟合后 PASS 但某些成分触收紧后的上限，后续轮次的 VLM 或主模型可在正常候选生成中提议 `tune(component, re_max += 2")`——这是标准的 `tune` 动作，不需要特殊机制。

**追溯标记**：恢复候选在 `working_note.md` 的相应分支小节标注"[恢复候选 A/B]"，记录 violations 清单、设置的 re_max 值、热启动来源。VLM 候选与恢复候选平等竞争，被截断的也照常记录。

**与 VLM 的关系**：本协议不替代 VLM——VLM 仍在步骤 e.i 照常生成视觉驱动候选（可能包括 PA 修正、成分增删等）。恢复候选只是 e.ii 多了一条规则，与伴星系检查等现有 e.ii 规则并列。两类候选在步骤 f 统一打分入队，beam search 的并行探索能力完好保留。

### §去重与排序（主模型职责，禁用规则去重）

**语义去重判据**——两个 (s_i, a_i) 与 (s_j, a_j) **同时满足**以下三条即视为等价，保留 g 较高者：
1. 施加动作后的预期成分清单 `expected_C'` 在**物理身份**上等价（允许命名互换，如 "bulge n=0.5 q=0.4" 等价于 "bar n=0.5 q=0.4"）。
2. 预期参数取值在容忍带内一致：Re ±20%、Sersic n ±0.5、q (b/a) ±0.1、PA ±10°（**sky-PA**，正北 0° 逆时针；与 `.lyric Pa7` 同帧，禁止按 +Y 轴约定比较）、mag ±0.5。
3. `expected_behavior_tag` 一致。

**优先级分数 g ∈ [0,1]**——主模型对每个候选按以下六个维度各打 0–1 分，等权平均得到 g：
1. **残差改善潜力**：结合 VLM 给的 σ 与主模型独立判断的残差可解释比例。
2. **物理合理性先验**：是否符合"Disk → (F1/Companion 若检出) → Bulge → Bar → Other"的成分添加次序；是否符合 Bar/Bulge/Lens/Nucleus 的认定条件（见 `<星系成分分析的总体流程>`）。**阶段一 detect_galfits_bar_lopsidedness 的检测结果在此维度仅作为弱先验**：检出可适度加分（提示性正证据），但**未检出不得扣分**——未检出是零证据而非负证据（详见阶段一"检测性质"条款）。一个基于残差证据（如中心四极矩、高扁率内部结构、bar 状残差等）的 Bar/Lens/Fourier 候选，即使阶段一未检出，其物理合理性得分应基于**残差证据的强度**评判，不得因阶段一未检出而压低。判定成分存在性的金标准是残差驱动的拟合验证，不是阶段一检测。
3. **路径多样性 bonus**：与当前 Q 中已有元素的方向差异越大越加分（对抗贪心坍缩）。例如 Q 中已有 3 个"加 Bulge"方向候选时，一个"切 edgeondisk"方向候选应得该维高分。
4. **退化惩罚**：父状态是否已退化（如 `--parconstrain` 被覆盖、bulge/disk 通量相同）；本动作是否可能继承退化。**此维度评估的是"候选本身是否继承父状态退化"，不是"父状态 s' 是否优于 s\*"**——一个 BIC 反升的 s'（如某成分参数初始化不当）其后继修正候选（位置修正 / PA 修正 / Re 修正）正在修复退化，应得**低**退化惩罚，即使其父状态 s' 看似"更差"。
5. **历史一致性**：是否与 `working_note.md` 前序目标连贯，避免反复横跳。
6. **BIC 门槛**：仅当动作涉及 Nucleus/AGN 的增删时启用；预估 ΔBIC 能否跨过 +10 门槛。

`score(s)` 用于判定 s\*，与 g 共用同一套维度，区别只在于它评估的是"已完成的拟合状态"而非"待入队的候选"。

**g_min 入队阈值**：任何 `g < 0.3` 的候选直接丢弃，不入队（避免低质量候选堆积导致队列永不空、终止完全靠 n=15 硬截止）。被丢弃的候选记入 `working_note.md` 的"跨分支决策日志"，标注 action_id 与"g < 0.3"。

**强制保留条款（豁免 g 截断）**：以下候选即便 g 较低也必须入队（至少保留一个变体），因为它们测试的是无法靠残差直觉判断的物理假设或程序化诊断驱动的修复，不探索就永远拿不到证据：

- **扁 Bulge → Bar 候选**：当父状态含 Bulge 且满足联合触发条件（`bulge_axrat < 0.5` AND `|bulge_ang − disk_ang| > 20°` AND `0.5 < bulge_n < 2.5`（若 free）AND `disk_axrat > 0.5`）时，主模型必须把 VLM 返回的 Bar 方向候选（`tune(Bulge→Bar)` 转换 或 `add(Bar)+tune(Bulge, q_min=0.7)` 新增，至少一个）以 g 不低于 0.5 的保底分入队，**不得因"阶段一未检出 bar"在物理合理性维度（维度 2）压分**。主模型在 custom_instructions 中须客观写出四条触发数值（见 §custom_instructions），让 VLM 知道触发条件已成立。若 VLM 在已触发情况下未返回任何 Bar 候选，主模型应**主动生成**一个 `add(Bar, n=0.5 fixed, PA≈bulge_ang)` 候选（参照 §候选动作忠实执行原则 的"B 类填空"规则初始化参数），追溯标记"[主模型扁-bulge 触发补充]"，走同样的打分入队流程。
- **Lens 候选**：父状态含 Bar 且 `Re_bar ≳ Re_disk(=1.68·Rs_disk)` 或 `q_bar ≳ 0.5` 时，Lens 候选同上保底入队。
- **Re-ordering FAIL 恢复候选**（见 §非物理结果恢复协议）：当 s' FAIL Re-ordering（swappable=False）时，主模型在 e.ii 生成的恢复候选 A（Re-bound 收紧）和 B（热启动+收紧）以 g ≥ 0.5 保底入队。这类候选针对的是程序化诊断（`check_re_ordering` 精确数值违规）驱动的机械修复，VLM 从残差图不容易直觉判断其改善潜力，故需保底保护。

### §custom_instructions 内容规范（主模型职责，硬约束）

主模型传给 `generate_beam_actions` 的 `custom_instructions` 是 VLM 生成候选时的关键上下文。主模型在其中扮演的是**客观信息提供者**，不是**候选方向建议者**。主模型对候选方向的筛选发生在入队打分阶段（§去重与排序），而不是在 custom_instructions 阶段。

**必须包含**（客观描述性信息）：
1. 阶段一 bar/lop 跨波段 OR-logic 结论、PA（**sky-PA**，正北 0° 逆时针；与 `Pa7` 同帧，VLM 与主模型禁止套用 GALFIT 的 +Y 轴约定）、b/a；
2. 父状态的成分清单 C、关键参数 P 摘要；
3. **当前拟合结果存在的具体问题**（如有；这是最重要的部分，必须客观详尽）：
   - 哪些参数触及上下界（标注 ⚠️ 与具体数值，如 `bar_Re=12" ⚠️触上限`、`bulge_axrat=0.1 ⚠️触下界`）；
   - Re 全序校验是否通过（若 FAIL，粘贴 `check_re_ordering` 返回的 `violations` 清单）；
   - 残差图上观察到的未拟合特征（位置 / 对称性 / 强度，引用阶段一视觉特征的客观描述）；
   - 成分身份是否混淆（如 disk 与 bulge 标签互换、bar 丧失棒形态变圆变胖、bulge 坍缩成致密点源）；
   - **扁 Bulge → Bar 触发数值（若父状态含 Bulge）**：客观列出 `bulge_axrat=...`、`|bulge_ang − disk_ang|=...°`、`bulge_n=...`、`disk_axrat=...` 四个值，并标注联合触发条件是否成立（成立 / 不成立 + 缺哪条）。这是让 VLM 判断是否该生成 Bar 候选的客观依据，主模型只报数值，**不**暗示方向（不写"建议加 bar"或"应该转换"）。
4. 父轮次已尝试动作清单（避免 VLM 重复提出）。

**严禁包含**（候选方向建议）：
- ❌ 不得列出"优先修复方向：(1)...(2)...(3)..."这类具体候选方向清单；
- ❌ 不得暗示或推荐特定的动作类型（如"建议释放 disk n""建议加 Lens""建议回退到 A.2""建议收紧 bar Re 上限"）；
- ❌ 不得预先做方向收敛或筛选——这是 VLM 的职责。

**为什么**：主模型一旦在 custom_instructions 中给出具体方向，VLM 会倾向于直接跟随这些现成方向，而不再自主回忆 prompt 中的规则（如 Lens 触发条件、方向多样性示例、禁用动作清单）来生成多样化候选。这等于主模型替 VLM 做了一轮方向筛选，压制了 beam search 的并行探索能力（典型反例：父状态 bar 膨胀时主模型给了"收紧约束/释放 disk/回退"三个方向，导致 VLM 未产出本应由 Lens 规则触发的"拆 Bar→Bar+Lens"候选）。

**正确做法**：把问题客观摆出来（如"bar_Re=12" 触上限，q_bar=0.6 触上限，Re_bar > Re_disk 全序反置，bar 丧失棒形态"），让 VLM 自己根据 prompt 规则生成候选。

**check_re_ordering hint 的处理**：当 Re 全序校验 FAIL 时，`check_re_ordering` 返回的 `custom_instructions_hint` 可原样拼入 custom_instructions（它是客观的违规清单），但主模型不得在此基础上追加自己的修复方向建议。

### §候选动作忠实执行原则（主模型职责，硬约束）

`generate_beam_actions` 返回的每个候选声明了一组 primitives（如 `add(Bulge, n=4 fixed, Re=0.5")`、`tune(obj1, n=0.5 fixed)`、`tune(obj0, type=sersic_f, m=1)`）。主模型在把候选转写为 `_iter{n}.lyric` 的五元组时，必须严格区分两类字段：

**A 类：不得修改的字段（候选的语义核心）**
- 成分类型（`Pa2) sersic` / `sersic_f` / `edgeondisk` / `Gaussian` 等）
- 物理参数的 vary/fixed 状态与目标值（如 `n=4 fixed`、`n=0.5 fixed`、`n free`、`q fixed=0.33`）
- 量级性约束（如 `q>=0.5`、`Re<=3"`、`Re_max=20"`、`ba_init=0.9`）
- 成分的增删（`add` / `remove`）与目标 label
- 中心约束策略（tied to obj0 via `.constrain` / free / fixed at 某坐标）
- Fourier 模式阶数（m=1、m=2 等）与是否启用
- 原子操作的捆绑关系（候选声明的 1–2 个 primitive 必须作为一个整体执行，不得拆分）

**B 类：允许主模型填空的字段（VLM 未给精确值时）**
- 五元组的具体初始值数字（从父状态对应参数的拟合值附近起步，如父状态 Re=4.6" 则子候选 Re_init 取 4–5" 区间，不可数量级偏离）
- 五元组的 min/max/step 数字（仅用于满足格式要求：`vary=0` 时也要 `min != max`、初始值落在 `[min, max]` 区间内）
- **成分命名（必须语义化）**：Pa1/Pb1/Pc1... 的值必须使用物理类型名（`disk`/`bulge`/`bar`/`companion`/`agn` 等），**不得使用 `obj0`/`obj1` 之类无意义命名**——lmfit 参数名由 Pa1 构建（如 `bulge_xcen`、`bar_Re`），语义命名让 `.gssummary` 输出与 `.constrain` 文件直接可读。首次拷贝输入 `.lyric` 为 `_iter1.lyric` 时，若起手成分为 `obj0` 之类，应同步重命名（sersic 起手成分通常 → `disk`）；后续新增成分按其物理类型命名。
- **伴星系 / 成分中心坐标的像素→arcsec 转换（重要）**：VLM 候选里给出的伴星系坐标（如 `tune(obj2, x=115, y=130)`）是 `comparison.png` 上的**像素坐标**；而 `.lyric` 的 `Pc3` / `Pc4` 五元组要求的是**相对 R2 中心的 arcsec offset**。主模型必须调用 `mcp__galmcp__pixel2arcsec_offset(pix_x, pix_y, lyric_file, band, origin=1)` 把 VLM 给的像素坐标转换成 arcsec offset 后，再填入五元组。**禁止**把像素数字直接塞进五元组（会引发数量级错误的拟合发散），**也禁止**手动按"0.396 px/" 之类的硬编码像素比例换算（不同波段的 drizzle 采样可能导致像素比例不同，必须走 WCS）。同一伴星系在不同波段如果像素位置不同，要分别按各自波段的 WCS 转换。**转换后的 arcsec offset 仅为初始估计值**——VLM 像素判读可能有 ±10-20 px 误差（父模型残差越脏，误差越大）。Pc3/Pc4 必须设为 `[init, init-2, init+2, 0.1, 1]`（`vary=1`），让拟合器在 ±2 arcsec（≈±5px）窗口内校准真实质心，**不得直接 `vary=0` 锁死**。仅在位置已校准后的后续轮次中才可考虑固定。
- 其他非伴星系成分的中心坐标（如新增 Bulge 的 xcne/ycen）：通常从父状态拟合值或 `pixel2arcsec_offset` 工具获取，不用 VLM 给的粗略像素值。

**核心约束**：主模型若认为某候选的 A 类字段有缺陷（物理不合理、与父状态冲突、重复已失败方向等），**必须整条丢弃该候选**（记入 `working_note.md` 的"跨分支决策日志"，标注 action_id、丢弃原因、以及是哪条 A 类字段触发了否决），**绝不得在修改 A 类字段后执行**。这保证每次拟合结果可以干净归因到 VLM 提议的某个方向——要么支持它，要么否定它，不会成为"执行走样"的污染样本。

**B 类字段的合理调整范围**：即使 B 类允许填空，主模型也不能借机改变候选的物理意图。例如 VLM 提议 `add(Nucleus, Re_init=1")`，主模型可以把 Re_init 填成 0.8" 或 1.2"（数量级一致），但不能缩到 0.3"（数量级偏离 3 倍，实质上把"延展核"改成了"致密点源"）。

**典型反例（严格禁止）**：
- VLM 提议 `add(Nucleus, n=4 fixed, Re_init=1")`，主模型改成 `add(Nucleus, n=2 fixed, Re_init=0.3")` 后执行——同时违反 A 类（n 值）与 B 类的合理范围（Re 数量级），拟合结果既不能支持也不能否定原提议
- VLM 提议 `tune(obj1, n=0.5 fixed)`（把 obj1 转 Bar），主模型改成 `tune(obj1, n free)`（释放 n）后执行——虽然看起来"更宽松"，但实际跑的是另一个候选，原提议未被测试
- VLM 提议 `add(Bulge, q>=0.5)`，主模型去掉 q 下界后执行——实质上把"加一个圆 bulge"改成了"加一个任意椭率 bulge"

**正确做法**：若主模型认为 VLM 提议的 n=4 不合理，应丢弃该候选并在决策日志写下"丢弃：Nucleus n=4 与 pseudo-bulge 先验冲突"；若主模型想测试 n=2 的变体，应作为主模型自己生成的新候选走完整打分流程，而不是寄生在 VLM 候选上偷改。

### §多分支 working_note.md 模板（智能体必须按此结构维护）

```markdown
# Galaxy {ID} Beam Search Working Note

## 基本信息
- 星系ID / 坐标 / 拟合区域 / 波段
- 束宽 W = 5；全局预算 N_max = 15
- 阶段一结论（VLM 形态分类、bar/lop 跨波段 OR-logic、PA 取值（**sky-PA**，正北 0° 逆时针；写入时直接进 `Pa7` 无需换算）、b/a）

## Beam 状态快照（每次主循环迭代后覆写本节，不要追加）
### 当前最优 s*
- 分支 / 轮次: <branch>.<local_round>   例: A.3
- 成分清单 C*: {...}
- reduced_χ² / BIC: ... / ...
- 对应 output 目录: output/<timestamp>_<lyric_stem>/
- 对应 .lyric: _iter{global_iter_id}.lyric

### 当前优先队列 Q（按 g 降序，至多 5 项）
| 排序 | 分支 | 父轮次 | 动作摘要 | σ | g |
|---|---|---|---|---|---|
| 1 | A | A.2 | +Nucleus(致密) | 0.55 | 0.78 |
| 2 | B | B.1 | release bulge_n | 0.35 | 0.62 |
| ... | | | | | |

### 全局拟合计数器
- n = X / 15
- stagnation = Y / 15
- global_iter_id = Z (下一个 .lyric 后缀)

## 分支 A: <分支主题，如 "Disk+Bulge 主线">
### A.1 (对应 fit #1, .lyric: _iter1.lyric)
- 配置 / 工具调用（含 --parconstrain 等）/ 成分 C / 参数 P 摘要 / reduced_χ² / BIC
- VLM 残差特征摘要（来自 generate_beam_actions 的阶段一）
- generate_beam_actions 返回的候选 action_id 列表与入队情况（被截掉的也记）
### A.2 ...

## 分支 B: <分支主题>
### B.1 (对应 fit #N, .lyric: _iter{global_iter_id}.lyric) ...

## 分支: 失败归档
### (fit #M, 父=<branch>.<round>, 失败)
- 动作: <action_id 或描述>
- 失败原因: <工具异常 / check_lyric_file 拒绝 / 拟合未产出等>
- 处置: 加入父状态禁忌集

## 跨分支决策日志（追加，不覆写）
- fit #X: 派生分支 B（原因：A 在 R2 退化，探索早期 Bar 方向）
- fit #Y: 合并 A.3 与 B.2（语义等价，bulge n=0.5 → bar）
- 终止: <终止条件>，累计 n=..., 被探索分支数=...
```

**命名规则**：
- 分支标识：大写字母，按派生顺序递增（A、B、C…），初始分支为 A。
- 分支内轮次：`<branch>.<local_round>`（A.1, A.2, B.1…），local_round 仅在该分支内递增。
- `.lyric` 文件名：仍用全局 `_iter{n}.lyric`（n 为 global_iter_id），避免文件名冲突；分支归属通过 working_note 索引而非文件名承载。
- `output/` 子目录：沿用现有 `<timestamp>_<lyric_stem>/` 命名，不改。

### 智能体执行守则（防止上下文超限）
1. **持久状态写 `working_note.md`，不要靠上下文记忆**：Q 的当前内容、s\*、n、stagnation 全部以 working_note 的"Beam 状态快照"为唯一真源；每次决策前先 Read 该节。
2. **上下文只保留当前一轮**：当前出队的 (s, a) 与刚生成的候选列表；处理完立即落盘并清出上下文。
3. **覆写优于追加**：Beam 状态快照每次覆写；分支小节与跨分支日志才追加。

### 步骤 4. 物理意义分析 与 奥卡姆剃刀原则（beam search 终止后执行）
- 物理意义分析：严格遵循 `<星系成分分析与策略>` 章节，对 s\* 的每个成分逐条复核参数物理意义。如出现不物理情况（如 Bulge Re < 0.2 px 但被强加为 Sersic、Bar 的 PA（**sky-PA**，对齐原图 N 箭头）与图像明显冲突），**重启一轮 beam search**：把"修复该不物理成分"作为强约束注入 `generate_beam_actions` 的 `custom_instructions`（reset Q 与 stagnation，保留 n 与 global_iter_id）。对于 Bulge Re 处于 0.2–0.5 px 边界区域的情况，应在 beam search 中同时探索 Sersic 和 N 块 AGN 两条路径进行竞争对比——只有 AGN 路径的 2D 残差明显更优时才采纳，否则保留 Sersic。
- 奥卡姆剃刀原则：
  - **Nucleus/AGN 成分**：若 s\* 含 Nucleus 且 ΔBIC < 10，把 `remove(Nucleus)` 作为最高优先级候选重启 beam search 验证；删除后 BIC 反升则保留 Nucleus。
  - **伴星系（Companion）**：若 s\* 含 Companion 且通量比 ≤ 1%（条件 A，计算方式同 e.ii），把该数值结论作为强上下文写入 `generate_beam_actions` 的 `custom_instructions`（格式同 e.ii），由 VLM 执行条件 B 视觉验证（原图面板 companion 位置是否有肉眼可见亮斑）。仅当 A∧B 同时成立（数值暗 AND 原图无可见源）时，把 `remove(Companion)` 作为最高优先级候选重启 beam search 验证（删除后 BIC 反升则保留 Companion）。若原图有可见亮斑（条件 B 不命中），不触发移除——该 companion 是真实致密源，通量低是因宿主太大而非源不存在。
- 上述两类重启 beam search 的累计 n 仍受 N_max = 15 总预算约束；若预算已耗尽，进入阶段三由阶段三判定是否可接受。

阶段三. 结果分析与报告撰写
* **锁定最佳结果**：从 `working_note.md` 的"Beam 状态快照 / 当前最优 s\*"小节读取最优轮次对应的 `output/` 子目录与 `_iter{n}.lyric`，作为本阶段所有分析对象的唯一来源。给出其对应的形态学物理意义（如：成分 A 代表经典的盘结构，成分 B 代表致密的核心星团）。
* **偏心成分（Fourier m=1）评估**：科学目标关心偏心的影响。
    - 如果最佳结果的 Disk 成分已经是 `sersic_f`（阶段一 lop 检出后于阶段二已添加），跳过本步。
    - 如果阶段一 lop 未检出但仍有疑虑：调用 `fourier_mode_analysis`，输入图为最佳轮次 **F200W 波段**的对比 PNG（原图/模型/残差），分析是否存在 m=1 傅里叶模式可修正的偏心非对称残差。工具返回 recommend_fourier=yes → 回到阶段二重启一轮 beam search：把"把 Disk 的 `Pa2) sersic` 改为 `sersic_f` 并设置 `Pa21) 1` 等参数（详见 component_specification_galfits.md）"作为强约束注入 `generate_beam_actions` 的 `custom_instructions`（reset Q 与 stagnation，保留 n 与 global_iter_id 计数）。
    - m=1 Fourier 成分的保留/移除判据：只有证明 Fourier 成分导致拟合不合理（参数发散、物理不成立）时才删除；删除也通过重启 beam search 验证。
* 使用 `write_file` 工具将分析结论写入当前星系目录：`analysis_report_xxx.md`。
* **报告内容包含：**
    * **生成时间：** 日期和时分秒。
    * **预处理信息：** 掩膜说明、背景设定依据。
    * **迭代过程流水账：** 基于 `working_note.md` 的多分支结构整理——按分支（A/B/C…）列出每轮新增/删除成分的依据（多模态视觉判断记录）、参数发散与回退记录、束内被截掉的候选 action_id、跨分支决策与语义去重合并事件。
    * **最佳结果详情：** 最终采用的参数表、物理意义解读。
    * **附件索引：** 最佳轮次的目录路径、最终的 `lyric` 文件、最终的拟合对比图（原图/模型/残差）路径。
    * **json格式化输出：** 在文档最后格式化输出轮次信息，便于规则提取，自动化处理。格式如下：
    ```json
    {"best_turn":"<最佳轮次的目录名>","components":["<最优轮次包含的哪些物理成分>"]    }
    ```
    其中 best_turn 的值为 output/ 下最佳轮次子目录的名称（如 20260414T093323.c1993a48）。
    物理成分类型：[Disk,Bulge,Bar,Lens,AGN,Fourier,Companion]

阶段四. SED拟合
SED拟合通常需要基于最优的Image拟合（在阶段三中已经确认）：
* 调用`run_galfits_sed_fitting`进行SED fitting。注意：`run_galfits_sed_fitting`使用的配置文件是最优的Image fitting使用的配置文件，同时，由于SED拟合需要基于image fitting得到的星系成分参数进行质量估计，需要指定最优image fitting对应的输出目录（通常是output目录下的某个子目录，例如20260414T093323.c1993a48）
    - SED拟合时不能传入约束文件，因为它基于单个成分进行拟合，且空间参数被固定
    - SED拟合只需要成功拟合一次即可
    - 若SED拟合失败，需要分析原因并重新SED拟合
    - SED拟合成功后，会生成一个新的配置文件，该文件是Image-SED联合拟合的输入文件

当SED拟合成功后，针对新生成的配置文件，需要检查其中每个成分（比如Px，x=a,b,c, ...）的Px9, Px11, Px12, Px14中五元组中的初值是否落在该五元组中指定的范围内，若不在该范围内，输出警告信息并结束，否则进入阶段五    

阶段五. Image-SED联合拟合
* 调用`run_galfits_image_sed_fitting`对image和SED进行一次联合拟合，输入配置文件是SED拟合成功后生成的配置文件            
    - 如果最佳的image-fitting使用了--parconstrain，那么run_galfits_image_sed_fitting也需要加载同一个约束文件
    - Image-SED拟合只需要成功一次即可
    - 若Image-SED拟合失败，需要分析原因并重新Image-SED拟合
    - Image-SED联合拟合成功后，标志着当前星系的拟合任务完成

当Image-SED联合拟合成功后，需要对比生成的png图像与Image拟合中最佳一轮中生成的png图像，如果两张图像的星系成分存在较大差异，输出对应的警告信息。

    
## 待分析星系

{argument}
