
要求严格遵循工作流开展多波段(multi-band)星系拟合分析工作。

仅关注拟合盘、核球、侧视盘、棒、AGN核、偏心（Disk 上的 m=1 Fourier 模式）、伴星系这七种物理成分，仅可对这七种成分的残差添加模型成分拟合，其他残差特征可以选择保留不拟合。其中偏心（Fourier m=1）的添加由阶段一 detect_galfits_bar_lopsidedness 的检出结果驱动：lopsidedness 检出时，应作为最高优先级修正项参与阶段二的结构迭代（在增加其他成分之前先加 m=1）；未检出时进入阶段三再由 fourier_mode_analysis 做残差二次确认。
图像分析与拟合执行只能使用 galmcp 中的工具，不能直接 shell 执行 GalfitS 命令。所有 GalfitS 拟合必须使用 `--fit_method ES`。严禁使用4_5v_mcp的相关工具。
必须建立 todos 并独立完成所有阶段，直到 Image-SED 联合拟合成功。

## workflow

阶段一. 查看星系目录与原图分析
* **查看星系目录：** 确认所需的文件是存在的，包括 FITS 图像、掩膜文件、背景估计文件、lyric配置文件等。
* **查看原始数据与图像：** 使用 render_original, view_original_image, detect_galfits_bar_lopsidedness 工具分析原图，确认星系的基本形态特征（如是否存在明显的核球、盘、棒结构等）。这将为后续的拟合提供初始猜测值。
* **detect_galfits_bar_lopsidedness 结果解读与固化：**
    - 工具返回结构为 `{"results": [{band, bar:{detected, pa_deg, b_over_a}, lopsidedness:{detected, mag, phase_deg}}, ...]}`，按波段列表。
    - **跨波段 OR-logic**：任一波段 `bar.detected=True` → 认定 Bar 存在；任一波段 `lopsidedness.detected=True` → 认定偏心存在，作为阶段二高优先级添加 Fourier m=1 的依据。
    - **PA 取值规则**：Bar 的 PA 优先取蓝端波段（如 F115W）的返回值；红端（F200W/F444W）作参考。
    - **偏心添加决策**：任一波段 `lopsidedness.detected=True` → 在 `working_note.md` 头部标记 "m=1 Fourier 高优先级"，阶段二每次调用 `generate_beam_actions` 时需把该标签写入 `custom_instructions`，确保 VLM 在 Disk 已建立后第一时间给出"把 Disk 的 `Pa2) sersic` 改为 `sersic_f`"的候选动作。
    - **写入 working_note.md 头部**：将每波段 bar/lop 检测结论、PA、b/a、A1、phi1 固化到 `working_note.md`，供后续所有迭代轮次的 `generate_beam_actions` 读取（工具会自动把 `working_note.md` 内容注入 VLM 上下文）。

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
        custom_instructions = "<阶段一 bar/lop 结论 + 头部其他固化信息>",
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
    1) `global_iter_id += 1`；拷贝 s 对应的 `.lyric`，按 a 的 primitives 修改成分与参数，写入星系主目录的 `_iter{global_iter_id}.lyric`。
    2) 若 a 涉及约束，写 `iter{global_iter_id}.constrain`（命名遵循 `Update_Constraints` 规范；AGN 中心参数名用 `xcen_agn` / `ycen_agn`）。
    3) **必须调用 `check_lyric_file`** 校验格式；失败按提示修复后再次校验，不得跳过。
    4) 调用 `run_galfits_image_fitting`，必带 `--fit_method ES`；若 `.constrain` 存在则带 `--parconstrain iter{global_iter_id}.constrain`。`n += 1`。
    5) **失败处置**：若工具异常或未产出 summary/对比图，把该 (s, a) 记入 `working_note.md` 的"分支: 失败归档"小节，把 a 加入 s 的禁忌集，`stagnation += 1`，回到循环开头。
c. **构造新状态 s'**：从新生成的 `.gssummary` 读 reduced_χ² 与 BIC；`R'` 取新生成的 `all_bands_comparison.png`；`C'`、`P'` 取自新的 `.lyric` 与 `.gssummary`。轮次命名：在所属分支内取 `branch.local_round`（如 A.2、A.3、B.1…，A.1 已被首次拟合占用），与 global_iter_id 解耦。s' 的深度 = `depth + 1`。
d. **更新 s\***：按 §去重与排序 中的 score 函数评分；若 score(s') > score(s\*)，则 s\* ← s'，`stagnation = 0`；否则 `stagnation += 1`。覆写 `working_note.md` 的"Beam 状态快照 / 当前最优 s\*"小节。
e. **下一层候选生成**：以新对比图为 `comparison_file`、新 `.lyric` 为 `lyric_file`、新 `.gssummary` 为 `summary_file`，调用：
    ```
    generate_beam_actions(
        ...,
        branch_id         = branch,
        parent_label      = <branch>.<local_round>,
        depth             = depth + 1,   # 新候选应用到的父状态深度
        working_note_file = <abs path>,
        custom_instructions = "<父轮次已尝试动作清单 + 阶段一结论>",
    )
    ```
    工具按 `depth+1` 的分段规则返回候选（depth+1=2 → 2–3 个；depth+1≥3 → 2–4 个）。
f. **去重 + 打分 + 入队**：主模型对每个新候选：
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
3. 若 s\* 是退化状态（如成分参数碰边界、bulge/disk 通量完全相同），不要强行进入阶段三；改为：把"修复退化"作为强约束写入 `generate_beam_actions` 的 `custom_instructions`，重启一轮 beam search（重置 Q 与 stagnation，但保留 n 与 global_iter_id 计数）。

### §去重与排序（主模型职责，禁用规则去重）

**语义去重判据**——两个 (s_i, a_i) 与 (s_j, a_j) **同时满足**以下三条即视为等价，保留 g 较高者：
1. 施加动作后的预期成分清单 `expected_C'` 在**物理身份**上等价（允许命名互换，如 "bulge n=0.5 q=0.4" 等价于 "bar n=0.5 q=0.4"）。
2. 预期参数取值在容忍带内一致：Re ±20%、Sersic n ±0.5、q (b/a) ±0.1、PA ±10°、mag ±0.5。
3. `expected_behavior_tag` 一致。

**优先级分数 g ∈ [0,1]**——主模型对每个候选按以下六个维度各打 0–1 分，等权平均得到 g：
1. **残差改善潜力**：结合 VLM 给的 σ 与主模型独立判断的残差可解释比例。
2. **物理合理性先验**：是否符合"Disk → (F1/Companion 若检出) → Bulge → Bar → Other"的成分添加次序；是否符合 Bar/Bulge/Lens/Nucleus 的认定条件（见 `<星系成分分析的总体流程>`）。
3. **路径多样性 bonus**：与当前 Q 中已有元素的方向差异越大越加分（对抗贪心坍缩）。例如 Q 中已有 3 个"加 Bulge"方向候选时，一个"切 edgeondisk"方向候选应得该维高分。
4. **退化惩罚**：父状态是否已退化（如 `--parconstrain` 被覆盖、bulge/disk 通量相同）；本动作是否可能继承退化。
5. **历史一致性**：是否与 `working_note.md` 前序目标连贯，避免反复横跳。
6. **BIC 门槛**：仅当动作涉及 Nucleus/AGN 的增删时启用；预估 ΔBIC 能否跨过 +10 门槛。

`score(s)` 用于判定 s\*，与 g 共用同一套维度，区别只在于它评估的是"已完成的拟合状态"而非"待入队的候选"。

**g_min 入队阈值**：任何 `g < 0.3` 的候选直接丢弃，不入队（避免低质量候选堆积导致队列永不空、终止完全靠 n=15 硬截止）。被丢弃的候选记入 `working_note.md` 的"跨分支决策日志"，标注 action_id 与"g < 0.3"。

### §多分支 working_note.md 模板（智能体必须按此结构维护）

```markdown
# Galaxy {ID} Beam Search Working Note

## 基本信息
- 星系ID / 坐标 / 拟合区域 / 波段
- 束宽 W = 5；全局预算 N_max = 15
- 阶段一结论（VLM 形态分类、bar/lop 跨波段 OR-logic、PA 取值、b/a）

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
- 物理意义分析：严格遵循 `<星系成分分析与策略>` 章节，对 s\* 的每个成分逐条复核参数物理意义。如出现不物理情况（如 Bulge Re < 0.2 px 但被强加为 Sersic、Bar 的 PA 与图像明显冲突），**重启一轮 beam search**：把"修复该不物理成分"作为强约束注入 `generate_beam_actions` 的 `custom_instructions`（reset Q 与 stagnation，保留 n 与 global_iter_id）。对于 Bulge Re 处于 0.2–0.5 px 边界区域的情况，应在 beam search 中同时探索 Sersic 和 N 块 AGN 两条路径进行竞争对比——只有 AGN 路径的 2D 残差明显更优时才采纳，否则保留 Sersic。
- 奥卡姆剃刀原则：**仅适用于 Nucleus/AGN 成分**。若 s\* 含 Nucleus 且 ΔBIC < 10，把 `remove(Nucleus)` 作为最高优先级候选重启 beam search 验证；删除后 BIC 反升则保留 Nucleus。
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
    物理成分类型：[Disk,Bulge,Bar,AGN,Fourier,Companion]

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
