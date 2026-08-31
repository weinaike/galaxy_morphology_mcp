
要求严格遵循工作流开展星系拟合分析工作。
关注拟合盘、核球、侧视盘、棒、AGN核、偏心、伴星系、Lens等星系成分，基于残差特征添加模型成分拟合，高级残差特征可以选择保留不拟合。
图像分析与拟合执行只能使用 galmcp 中的工具。严禁使用4_5v_mcp的相关工具
系统规定的规范、`generate_galfit_beam_actions` 的 Physicality Verdict、以及 `best-round-verifier` subagent 的审计结果是你需要遵循的，严禁私自更改。

## N=+Y 契约（本工作流全局 PA 约定，硬约束）
本工作流采用 **N=+Y 契约**：假定图像正北方向与 Y 轴正上方一致。因此 **PA 一律按"图像 Y 轴正上方为 0°，逆时针增大"读与写**——该约定与 feedme `10)` 参数行（GALFIT "+Y 轴为 0°"）数值等同，**主模型把 VLM 读出的 PA 原样填入 `10)` 行，不做任何换算**。`render_original` 原图上如有 lime 指南针（N/E 箭头）仅供参考形态判断，**读 PA 一律对齐图像纵轴，不对齐指南针**。`detect_bar_lopsidedness` 返回的 `bar.pa_deg` 与 `lopsidedness.phase_deg` 在本契约下可直接使用。

## 单位契约（本工作流全局单位约定，硬约束）
本工作流所有 Re / 位置 / 尺寸**一律为像素（px）**：VLM 从对比图面板读出的 px 值与 feedme 参数行同单位同参考系，主模型**原样写入、禁止任何换算**（expdisk 的 Rs 换算 Re=1.68·Rs 是唯一例外，由主模型在写入 `4)` 行时处理：VLM 候选给出的 Re 三元组一律指有效半径 Re）。**全流程禁止出现 arcsec**，也禁止调用任何单位转换工具。

## workflow

阶段一. 查看星系目录与原图分析
* **查看星系目录：** 确认所需的文件是存在的，包括 FITS 图像、掩膜文件、背景估计文件、feedme 文件等。
* **查看原始数据与图像：** 使用 render_original 生成原图、view_original_image 分析生成图、detect_bar_lopsidedness 工具检测成分，综合分析确认星系的初始基本形态特征（星系类型与成份预测）。这将为后续的拟合提供初始猜测值。
* **detect_bar_lopsidedness 结果解读与固化：**
    - 工具返回结构为 `{"bar": {detected, pa_deg, b_over_a}, "lopsidedness": {detected, mag, phase_deg}}`（单波段）。
    - **检测性质（重要）**：此检测是**自上而下的形态学提示**（top-down hint），不是自下而上的成分级判定（bottom-up verdict）。它只看原图的等照度线/傅里叶特征，不经过"添加成分 → 看残差是否改善"的拟合验证。因此：
      - **检出（detected=True）= 弱正证据**：存在该成分的先验概率升高，阶段二应积极生成对应候选（但不保证拟合一定接受）。
      - **未检出（detected=False）= 零证据，不是负证据**：不构成"该成分不存在"的证明。bar/lop 可能在残差驱动的自下而上探索中被发现（典型情形：中心成分建立后，高动态范围图揭示出扁长内部结构；或 bulge 释放 n 后，残差浮现四极矩 bar 签名）。
      - **金标准**：判定成分存在性的最终依据是残差驱动的拟合验证（add → refit → 残差改善 + 参数物理），不是阶段一检测。
    - **PA 取值规则**：`detect_bar_lopsidedness` 返回的 `pa_deg` / `phase_deg` 在 N=+Y 契约下可直接写入候选 PA 与 feedme `10)` 行，无需任何换算。
    - **偏心添加决策**：`lopsidedness.detected=True` → 在 `working_note.md` 头部标记 "m=1 Fourier 高优先级"，阶段二每次调用 `generate_galfit_beam_actions` 时需把该标签写入 `global_state_description` 的 [阶段一结论] 字段，确保 VLM 在 Disk 已建立后第一时间给出"在 Disk 成分块追加 F1 行"的候选动作。
    - **写入 working_note.md 头部**：将 bar/lop 检测结论、PA、b/a、A1、phi1 固化到 `working_note.md`，供后续所有迭代轮次蒸馏为 `global_state_description`（工具不再自动注入 working_note 全文，避免注意力稀释——主模型按 §global_state_description / local_state_description 生成规范 蒸馏）。**措辞告诫**：未检出的成分必须写成"未检出（零证据，非判定性）"或类似明确标注其非判定性质，不得写成裸的"NOT detected / 不存在"。

阶段二. 结构搜索与动态校验 (Beam Search 模式)
*目标：通过束宽 W=5 的 beam search 在结构空间中并行搜索最优的物理成分组合，避免贪心单路径在退化轮次（如约束失效、参数坍缩）处陷入局部最优。每个束内分支仍遵循"自下而上、完成一个成分拟合后再考虑新增"的渐进式理念；beam search 只是把"单一下一步"扩展为"多条并行候选路径"。*

### 常量定义（硬性，不随星系类型调整）
- 束宽 W = 5（优先队列最大长度）
- 全局拟合预算 N_max = 15（每次 `run_galfit` 调用，无论成功失败，都计数一次）
- 早停阈值 S_max = N_max = 15（连续无改进次数上限，当前设为与 N_max 相同，即早停实际上不生效，仅由拟合预算 N_max 控制终止）

### BIC 口径（全局约定，硬约束）
本工作流中所有模型优劣比较、状态账本记录与 ΔBIC 阈值判断，**一律使用 BIC_eff**（`fit_statistics.bic_eff`，= χ²/A_psf + k·ln(N/A_psf)：2D χ² 除以 PSF 面积 A_psf=π·(FWHM/2)²，k=N_free，N=N_dof+k 为拟合数据像素数）——它是按独立分辨元数归一化的有效 BIC，可比性强于逐像素 BIC。BIC_eff 缺失时（如 PSF 文件缺失）退回 1D BIC（`bic1d`）。**下文一切未加注明的 "BIC" / "ΔBIC" / "BIC 反升" 均指此口径**；参数摘要表中若同时出现 1D BIC 行，仅作参考。

### 形式化定义（精简版，便于智能体维护一致的状态语义）
- **状态** s = (C, P, R, reduced_χ², BIC, depth)，其中 C 为成分清单（以 feedme `# STRUCTURE:` 名标识）、P 为对应参数（feedme 参数行的值 + free/fixed toggle；Re/位置一律 px）、R 为残差诊断（对比 PNG + 1D 残差特征）、reduced_χ² 与 BIC 取自该轮 `run_galfit` 返回的 `fit_statistics`（reduced_χ² 优先 1D `chisq1d_nu`，缺失时退回 2D `chi2_nu`；BIC 一律取 **BIC_eff**（见 §BIC 口径），缺失时退回 `bic1d`）、depth 为该状态在搜索图中的深度（s₁ 的 depth=1）。
- **动作** a = 复合动作，由 1–2 个语义内聚的原子操作组成。原子操作有三类：`add(type, params)` 新增成分（须同时声明其 `# STRUCTURE:` 名）、`remove(component)` 删除成分、`tune(component, param_delta)` 调参（含释放/固定 toggle、收紧/放宽 `.cons` 边界）。禁止捆绑无关联的原子操作。
- **转移** T(s, a) = s'：以父状态的 `_iter{n}.feedme` 为**结构模板**（含 `# STRUCTURE:` 注释与 free/fixed toggle）、父状态 galfit.NN 的收敛值热启动回填（见步骤 1.b.1 热启动规则）→ 按 a 修改 → 写 `_iter{n}.feedme` → `check_feedme_file` → `run_galfit` → 读归档产物（`output_param_file` = galfit.NN 收敛值、`image_file` = 对比 PNG、`summary_file` = 统计）→ 调用 `generate_galfit_beam_actions` 获取下一层候选。s'.depth = s.depth + 1。
- **初始状态** s₀：从输入 feedme 解析得到（C、P 取输入结构，R=原图诊断, reduced_χ²=⊥, BIC=⊥, depth=0）。输入 feedme 通常是单 sersic 起手；若已含多个成分，直接以其为 s₀ 结构（不强拆）。s₀ 不是拟合产物，而是输入；首次拟合（步骤 0.4）对 `_iter1.feedme` 跑一次 `run_galfit` 直接得到 s₁，**不经过候选生成**。
- **当前最优** s\*：按主模型综合评分最高者（评分维度见 §去重与排序），**不是**单纯按 reduced_χ² 最低。
- **状态签名** sig(s)：状态的规范形式 = 排序后的成分清单 × 每成分 `(类型, n状态(fixed/free+值), Re(px), Mag, q, PA(N=+Y))` × free/fixed 配置 × `.cons` 边界配置。`check_feedme_file` 返回的规范化成分清单是签名的读取源。签名是图搜索去重的唯一载体（VLM 侧生成前比对、主模型侧执行前比对，共用同一把尺子）。
- **搜索图（图搜索，非树搜索）**：状态空间是**图**——不同动作序列可到达同一状态（如"加 X 后删 X"回到祖先结构）。为防重访，维护两本账作为 visited set：**输入账本**（历次已执行 `_iter{n}.feedme` 的规范形式：结构 × toggle 配置 × `.cons` 边界带 × 初始值带）与**结果账本**（历次已拟合状态的 sig + BIC + verdict + 僵尸标记）。转移分两类：**闭式转移**（产出状态可不经拟合精确投影：remove-only、参数 revert、边界还原）与**黑箱转移**（add、tune 等——结果不可预知，必须实跑拟合）。环检测规则见步骤 1.b.0。
- **僵尸成分 [zombie]**：拟合后通量占最亮成分之比 < 0.5% 的成分（**相对判据，禁止用绝对星等**——不同巡天深度差异数个量级；通量比由 Mag 差换算 `f_i/f_max = 10^(−0.4·(Mag_i − Mag_min))`）。仅相差僵尸成分的两个状态在结果账本中视为等价（零通量成分不改变模型可表达的像）。

### 步骤 0. 初始化（每个星系只执行一次）
1. 在星系主目录创建（或重置）`working_note.md`，按本节末尾的 §多分支 working_note 模板初始化空壳；把阶段一的 VLM 形态判断、bar/lop 检测结论、PA（N=+Y 契约）、b/a 全部写入头部。
2. 初始化：全局拟合计数 n = 0；全局 feedme 文件计数 global_iter_id = 0；分支计数 branch_counter = 1（即 "A"）；当前最优 s\* = None；优先队列 Q = []；连续无改进计数 stagnation = 0。
3. 调用 `render_original` 渲染原图（如阶段一未做）；记录原图对比图路径。
4. **首次拟合（确定性，不调用 VLM）**——输入 feedme 本身已是起手结构，首次拟合直接对它跑，不需要候选生成：
    1) `global_iter_id += 1`（→ 1）。用 Read + Write 把输入 feedme 原样拷贝为星系主目录下的 `_iter1.feedme`，**同时确保每个成分块有语义化的 `# STRUCTURE: <NAME>` 注释行**（`# Component number: N` 与 `0)` 行之间；起手单 sersic 通常命名为 `disk`，椭圆星系起步可命名 `sersic`）——GALFIT 输出文件会丢弃该注释，输入侧必须维护好命名，否则对比图图例、参数摘要与账本签名全部退化为类型名。B) 输出路径若与输入同名需改为独立输出名（避免覆盖输入 feedme）。相对路径按 feedme 所在目录解析。
    2) 调用 `check_feedme_file(_iter1.feedme)` 校验；失败按提示修复。
    3) 调用 `run_galfit(config_file=<_iter1.feedme 绝对路径>)`。`n += 1`（→ 1）。
    4) 失败处置：若工具异常或未产出对比图/summary，说明输入 feedme 本身有问题——这是退化情形，不进入主循环，改为人工介入修复输入后重新执行步骤 0。
    5) 构造 s₁：`C₁`、`P₁` 取自 `_iter1.feedme`（首次拟合不修改成分与参数）；reduced_χ² 与 BIC 取自返回的 `fit_statistics`；`R₁` 取归档的对比 PNG；拟合收敛值取归档的 `output_param_file`（galfit.NN）。`s\* = s₁`（首次拟合是唯一状态，无条件设为 s\*；若步骤 0.5 返回的 Physicality Verdict 为 FAIL，不回溯撤销 s₁——后续主循环中任何 PASS 状态可直接取代它）。在 `working_note.md` 分支 A 下追加 A.1 小节（fit #1，feedme = `_iter1.feedme`）。
5. **首候选生成（depth=1）**：以 s₁ 的对比图为 `comparison_file`、`_iter1.feedme` 为 `feedme_file`、归档的 `output_param_file`（galfit.NN）为 `fitted_param_file`、归档的 `summary_file` 为 `summary_file`，调用：
    ```
    generate_galfit_beam_actions(
        feedme_file        = <_iter1.feedme 绝对路径>,
        fitted_param_file  = <s₁ 的 galfit.NN 绝对路径（run_galfit 返回的 output_param_file）>,
        comparison_file    = <s₁ 的对比 PNG 绝对路径（run_galfit 返回的 image_file）>,
        summary_file       = <s₁ 的 summary 绝对路径>,
        global_state_description = "<按 §global_state_description / local_state_description 生成规范 蒸馏：此时 [状态账本]/[已验证盆]/[被否定假设] 均为空或仅有输入先验，主要写 [元信息（px 契约）]/[阶段一结论] 与 [预算]>",
        local_state_description  = "<按 §global_state_description / local_state_description 生成规范 填写：s₁ 拟合结果的具体问题（触界参数/残差特征/成分身份异常等）；严禁给出具体候选方向建议>",
        branch_id         = "A",
        parent_label      = "A.1",
        depth             = 1,
    )
    ```
    工具会按 depth=1 规则返回 1–2 个候选（lop 检出 → 1 个 +F1 候选；bar 检出 → 1–2 个 Bulge/Bar 候选；都未检出 → 1 个标准 add(Bulge) 候选），**同时返回 s₁ 的 `## Physicality Verdict` 块——解析并记入 A.1 小节（主循环中的 s\* 更新守门从 A.2 起严格执行）**。
6. 对每个返回的候选，主模型按 §去重与排序 打分得到 g ∈ [0,1]；按 g 降序截断到 W=5 入队 Q。每个队列元素记录 `(s_parent=s₁, a, σ_from_vlm, g, branch_id, depth=2)`——注意：从这些候选执行转移得到的下一状态深度为 2。
7. 更新 `working_note.md` 的 Beam 状态快照。

### 步骤 1. 主循环（在终止条件未触发前持续执行）
```
while Q 非空 and n < 15 and stagnation < 15:
```
a. **出队**：从 Q 取出 g 最高的 (s, a, σ, g, branch, depth)。把它从 Q 中移除。
b. **执行转移 T(s, a)**：
    0) **图搜索环检测（硬约束，先于写 feedme 与 global_iter_id 递增）**：
       - **R1 输入账本比对（所有动作）**：把 a 按 §候选动作忠实执行原则 转写为假想 feedme 的规范形式（结构 × toggle 配置 × `.cons` 边界带 × 初始值带；Re/位置一律 px），与**输入账本**逐条比对（容忍带同 §去重与排序）。带内等价 → 同一输入对确定性优化器无新信息，**整条丢弃**（记入"跨分支决策日志"，标注 action_id 与命中的账本行），`stagnation += 1`，回到循环开头。
       - **R2 闭式转移投影比对（仅 remove-only / 参数 revert / 边界还原类动作）**：这类动作的产出状态可**不经拟合精确投影**（父状态签名去掉被删成分 / 还原被 revert 的参数，幸存成分的 toggle/边界配置按热启动规则继承）。拿投影签名与**结果账本**逐行比对（僵尸感知：仅相差 [zombie] 成分的状态等价）：
         - **严格命中**（结构 × toggle/边界配置均一致，僵尸等价亦算命中）→ **零成本回滚**：不写 feedme、不调 run_galfit、**不计 n**；在 working_note 记一条回滚边（`<branch>.<round> --a--> ≡<命中轮次>`），`stagnation += 1`，回到循环开头。**不重跑步骤 d**——回滚目标状态的候选生成在其原始轮次已完成，其后继要么已入队要么已执行，重新生成只会得到重复候选。
         - **仅结构一致、toggle/边界配置有差** → 不回滚（如 bulge n free vs fixed 是不同的科学问题），但把该候选标记"[疑似近重复]"带入步骤 f：六维打分中"退化惩罚"维度记 0（满惩罚），除非候选的 novelty 声明能指出该配置差异承载独立假设。
         - 无命中 → 正常继续 1)。
       - add / tune 等黑箱转移只做 R1（结果不可预知，R2 不适用）。
    1) `global_iter_id += 1`；以 s 对应的 `_iter{n}.feedme` 为**结构模板**，按 a 的 primitives 修改成分与参数，写入星系主目录的 `_iter{global_iter_id}.feedme`。**热启动规则（硬约束）**：VLM 的诊断以父状态的**收敛解**为条件——模型图上的成分椭圆与图例的 Mag/Re/n/q/PA 画的就是父轮 galfit.NN 收敛值，VLM 未提议 tune 的参数隐含"该成分当前形态可接受"。因此子轮次必须让被声明的动作成为**父收敛解之上的干净增量**：所有**未被** primitives 声明的参数，feedme 参数行的初始值一律回填父状态 galfit.NN 的收敛值（free/fixed toggle 保持父轮设定），**禁止直接沿用父 feedme 的旧输入值**——那是上一轮的初始猜测，不是 VLM 评估过的解；从旧猜测重启会让未提及参数重新游走，既污染"候选 → 结果"的归因，又浪费收敛预算。注意 galfit.NN 会丢弃 `# STRUCTURE:` 注释——**结构与注释一律从父 `_iter{n}.feedme` 模板继承，仅数值从 galfit.NN 回填**。被 primitives 声明修改的参数按候选声明值写入；`add` 的新成分无父收敛值，按候选声明参数初始化（expdisk 写 Rs=Re/1.68）。回填时 sky 成分的 `1)` 值同样回填父轮收敛值（sky 背景通常应固定，见 CLAUDE.md）。**转写时必须严格遵守 §候选动作忠实执行原则**——候选声明中的语义核心字段（成分类型、`# STRUCTURE:` 名、n/toggle 状态、量级约束、增删/中心约束策略、F1 阶数等）不得擅自修改；若主模型认为某候选有缺陷，应整条丢弃（记入"跨分支决策日志"），而不是修改后执行。
    2) **主星系同心约束检查（硬约束，无论 a 是否声明约束都必须执行）**：统计本轮 `_iter{global_iter_id}.feedme` 中主星系中心成分（Disk/Bulge/Bar/Lens，即 `# STRUCTURE:` 名为 disk/bulge/bar/lens，不含 comp/companion/secondary/satellite）的数量 K：
       - **K ≥ 2**：**必须**写星系主目录下的 `iter{global_iter_id}.cons`，语法与完整示例严格遵循 CLAUDE.md《主星系同心约束——`.cons` 文件撰写规范》——把锚点（Disk，暂无 Disk 时取最亮中心成分）与所有从属中心成分的编号用 `_` 连成链，写**成对**的两行（缺一不可）：`<编号链>  x  offset` 与 `<编号链>  y  offset`。要点：锚点 `1)` 行 toggle 保持 `1 1`（约束生效后整组联动平移，中心由拟合器共同优化）；从属成分 `1)` 行保持 `1 1`（GALFIT 会自动改写为 `2 2` 受约束标记）；feedme `G)` 项指向该文件；本轮其他 `.cons` 边界（re/n/mag 范围、伴星系位置窗）**合并写入同一文件**（GALFIT 只加载一个约束文件）。**伴星系（`# STRUCTURE:` 名含 comp/companion/secondary/satellite）的编号严禁写入 offset 链**——改用软约束窗钉住位置：`<编号>  x  <init-5>  <init+5>` 与 `<编号>  y  <init-5>  <init+5>`（±5px，初值即 VLM 量测的像素坐标）。**语法警告**：空格成对（`1 2 x 0.0 0.0`）与横线成对（`1-2 x 0.0 0.0`）两种写法对本机 GALFIT 实测**静默无效**（不报错但中心漂移），严禁使用；唯一有效形式是链式 `offset` 硬约束。
       - **K ≤ 1**（仅单成分或起手单 sersic）：不写 offset 链；若有其他边界需求仍可写 `.cons`。
       - **生效验证（c 步骤中执行）**：拟合成功后检查归档 galfit.NN——链内从属成分 `1)` 行 toggle 应为 `2 2` 且链内各成分 x,y 数值完全一致；否则视为约束未生效（写法被静默忽略），该轮按 b.5 失败处置，修复 `.cons` 后重跑。
       - 该检查是主模型的强制职责，**不得依赖 VLM 候选声明**——即使 VLM 候选未提及同心约束，主模型也必须按上述规则补齐 `.cons`。
    3) **必须调用 `check_feedme_file`** 校验结构；失败（或出现"成分缺 `# STRUCTURE:` 名"警告）按提示修复后再次校验，不得跳过。
    4) 调用 `run_galfit(config_file=<_iter{global_iter_id}.feedme 绝对路径>)`。`n += 1`。
    5) **失败处置**：若工具异常或未产出对比图/summary，把该 (s, a) 记入 `working_note.md` 的"分支: 失败归档"小节，把 a 加入 s 的禁忌集，`stagnation += 1`，回到循环开头。
c. **构造新状态 s'**：从 `run_galfit` 返回值读 reduced_χ² 与 BIC（`fit_statistics` 的 `chisq1d_nu` 与 **`bic_eff`**，缺失时分别退回 `chi2_nu` / `bic1d`，见 §BIC 口径）；`R'` 取归档对比 PNG；`C'`、`P'` 取自新 feedme 与归档 `output_param_file`（galfit.NN 收敛值）。轮次命名：在所属分支内取 `branch.local_round`（如 A.2、A.3、B.1…，A.1 已被首次拟合占用），与 global_iter_id 解耦。s' 的深度 = `depth + 1`。
d. **候选生成 + 拟合结果物理性判定（无条件硬约束——见 §候选生成的诊断式原则；两个正交来源合并后统一进入步骤 f 打分入队）**：本步骤只要步骤 b 拟合成功就**必须**执行（失败处置分支 b.5 除外），无论 s' 的物理性判定结果如何、BIC 是否反升、参数是否触界、队列是否仍有未消费候选、拟合预算是否紧张。每轮主循环的候选由两个触发条件正交的来源并行生成——d.i 由 VLM 基于残差图像的视觉分析与**物理性判定**驱动，d.ii 由主模型基于拟合数值的客观阈值驱动。两类候选合并后走完全相同的去重 / 打分 / 截断规则（步骤 f），彼此平等竞争入队。
    - **d.i VLM 视觉驱动候选 + 物理性判定**：以新对比图为 `comparison_file`、新 feedme 为 `feedme_file`、归档 galfit.NN 为 `fitted_param_file`、归档 summary 为 `summary_file`，调用：
        ```
        generate_galfit_beam_actions(
            ...,
            global_state_description = "<按 §global_state_description / local_state_description 生成规范 从 working_note 蒸馏并随步骤 g 同步更新：[元信息（px 契约）]/[阶段一结论]/[状态账本]/[回滚边]/[已验证盆]/[被否定假设（带 ΔBIC 数值 + 失败原因 + 重开条件）]/[预算]>",
            local_state_description  = "<按 §global_state_description / local_state_description 生成规范 填写：s' 拟合结果的具体问题（触界参数/残差特征/成分身份异常等）+ 数值规则委托内容；严禁给出具体候选方向建议>",
            branch_id         = branch,
            parent_label      = <branch>.<local_round>,
            depth             = depth + 1,   # 新候选应用到的父状态深度
        )
        ```
        工具按 `depth+1` 的分段规则返回候选（depth+1=2 → 2–3 个；depth+1≥3 → 2–4 个），**且返回 Markdown 的顶部包含 `## Physicality Verdict` 块（verdict / failed_checks / swap_hint）——这是 VLM 对 s' 拟合结果的物理性判定（核心为 Model 面板上各成分 2·Re 椭圆的同心嵌套包含性：disk ⊃ lens ⊃ bar ⊃ bulge、内层面积明显小于紧邻外层、整体"洋葱"结构无瑕疵、最外围成分 2·Re 不越出拟合区域）**。主模型解析该块并**原样**记录到 working_note（不得改写判定内容）：
        - `verdict: PASS` → s' 获得步骤 e 的 s\* 比较资格。
        - `verdict: FAIL` → s' **失去 s\* 候选资格**（即使 χ²/BIC 更优）；在 working_note 该轮小节标注"物理性 FAIL 否决"并粘贴 failed_checks 证据；按 **§非物理结果恢复协议** 生成受保护恢复候选（与 VLM 候选一同进入步骤 f 竞争）。VLM 按 prompt 约定本轮至少给出一个针对 failed_checks 的修复候选。
        - **Verdict 块缺失或不可解析**（VLM 未按格式输出）：主模型做最小兜底——调用 `check_feedme_file` 取得规范化成分清单，纯数值比对主星系全序链 `re_disk > re_lens > re_bar > re_bulge`（expdisk 用 `re_effective_px`；不调用任何其他工具；这只是 VLM 嵌套包含性判定的最小数值子集，无法覆盖椭圆穿出/交叉等形态违例），结果记入 working_note 并标注"[verdict 兜底]"。正常情况应依赖 VLM 判定，此兜底仅防格式缺失导致守门失效。
        - `swap_hint: disk_bulge_swap`：确认 VLM 候选中含交换 disk ↔ bulge 标签方向的修复候选；若 VLM 漏给，主模型按 §候选动作忠实执行原则 的 B 类填空保底生成该候选（g ≥ 0.5，强制保留条款），追溯标记"[主模型 swap 补充]"。
    - **d.ii 主模型数值规则驱动候选**：主模型基于 s' 的拟合数值（galfit.NN + feedme toggle + `.cons` 边界）做客观数值检查，把**现象与数值事实**客观报告给 VLM；**生成什么候选完全由 VLM 依据 `beam_action_generation_prompt_galfit.md` 的规则自主决定**（主模型不代生成、不在描述中给方向建议）。这类检查针对"视觉上可见但数值上可疑的成分"——VLM 从图像上看到该成分存在会倾向于保留，但数值上若可疑（如通量极低、或基础成分 Re 偏小被延展成分代偿），主模型把客观观数据交给 VLM，由 VLM 结合原图视觉验证决定是否生成调整候选（移除或参数调整）。当前定义的触发规则：
        - **伴星系必要性检查（数值 + 视觉双轴判据）**：若 s' 含 Companion，从 `check_feedme_file` 的清单读 companion 与 disk 的 Mag，计算通量比 `f_companion/f_disk = 10^(−0.4·ΔMag)`（其中 `ΔMag = Mag_companion − Mag_disk`）。
          - 若通量比 > 1%：companion 通量显著，不触发移除检查。
          - 若通量比 ≤ 1%（**条件 A 命中**）：主模型**不生成任何候选**，只把三项数值事实（通量比、ΔMag、与最亮成分的通量比）写入当轮 `generate_galfit_beam_actions` 的 `local_state_description`，格式为："伴星系条件 A 命中（客观事实）：companion 通量比 = 0.4%, ΔMag = 5.91。"——**不附加任何操作建议**。条件 B 视觉验证与 `remove(Companion)` 候选的生成由 VLM 按 `beam_action_generation_prompt_galfit.md` §伴星系移除验证复核 自主决定：仅当 A（数值暗）AND B（原图无可见源）同时成立时才生成 remove 候选；原图有可见亮斑则不生成。
          - 若 s' 不含 Companion，不触发。
        - **disk Re 瓶颈检查（延展成分触界 + Re/通量简并判据）**：若 s' 含 lens 或 bar，读其 Re 拟合值（expdisk 换算有效半径）与 `.cons` 中对应的 re 上限（无约束则本检查的"触界"子条件不成立），以及 disk 的 Re 与 Mag。当 lens/bar 的 Re 触上限（拟合值 ≥ 0.98 × 上限）且满足下列**任一**子条件时，判定为"disk Re 瓶颈命中"——延展成分想更大但被 disk Re 拖住，是 disk Re 偏小的客观信号：
          - **子条件 A（Re 简并）**：`Re_lens/bar / Re_disk ≥ 0.85`（两者在 Re 维度高度简并，lens/bar 几乎追上 disk）。
          - **子条件 B（通量接近或超过）**：`Mag_lens/bar ≤ Mag_disk + 0.2`（即 lens/bar 通量 ≥ disk 的 ~83%，甚至超过 disk）。此条件捕捉 lens/bar 被迫接管 disk 外缘通量的退化模式。
          - **事实报告**（任一子条件命中后，若 disk Re 也触上限则不触发——disk 已无空间）：主模型把瓶颈**数值事实**写入 `local_state_description`，格式为："disk Re 瓶颈信号（客观事实）：lens(bar)_Re=5.0px 触上限（re_max=5.0px），Re_lens(bar)/Re_disk=0.93 [≥0.85（Re 简并命中）/ <0.85]，lens(bar)_Mag=16.86 [≤ disk_Mag+0.2=17.28（通量接近命中）/ > 该值]。disk_Re=6.1px（未触界/无约束）。"——**不附加任何操作建议**；`tune(disk, Re 更大)` 等候选的生成（含 1D 曲线视觉确认）由 VLM 按 `beam_action_generation_prompt_galfit.md` §Disk 外围光度不足触发规则 自主决定。VLM 生成的该方向候选以 g ≥ 0.5 保底**入队**（强制保留条款，见 §去重与排序——保底只作用于排队打分，不改变生成权归属）。
          - **原理**：该规则针对的是"lens/bar 膨胀去代偿 disk 外缘通量"的退化模式——当 lens/bar Re 触上限且与 disk 在 Re 或通量维度简并时，根因往往是 disk Re 本身偏小，而非 lens/bar 真的需要那么大。此信号完全客观（来自拟合数值），不依赖 VLM 在低信噪比外围区域的视觉判读（后者已证实不稳定——当 lens 已代偿外缘通量时，1D 曲线变平，视觉检查难以触发）。
          - 若 s' 不含 lens/bar，不触发。
        - **lens Re 膨胀检查（参数状态触发，生成三条竞争式候选）**：若 s' 含 lens，读 lens 的 Re 拟合值、`.cons` 中 lens 的 re 上限（无约束则"触上限"子条件不成立）、以及 disk 的 Re 与 Mag。
          - **触发条件（任一命中）**：
            - **触上限**：`lens_Re ≥ 0.98 × re_max`。
            - **Re 反置**：`lens_Re ≥ disk_Re`（VLM 物理性判定会命中此项，或主模型直接比对）。
          - **事实报告**（主模型把信号**数值事实**写入 `local_state_description`，**不生成候选、不附加操作建议**——竞争路径 A/B/C（+条件触发的 D）的生成由 VLM 按 `beam_action_generation_prompt_galfit.md` §Lens Re 膨胀触发规则 自主决定，主模型仅打分入队）：格式为："Lens Re 膨胀信号（客观事实）：lens_Re=Xpx 触上限（re_max=Ypx）[和/或] lens_Re=Xpx ≥ disk_Re=Zpx（Re 全序反置）。lens_Mag=W，disk_Mag=V。边界来源：当前 re_max=Y [自设（输入原始 re_max=R）/ 原始/ 无约束]。简并数值：Re_lens/Re_disk=比值；lens_Mag−disk_Mag=Δ。"——边界来源与简并数值按 §Lens Re 膨胀触发规则 候选 A/B/C/D 适用条件的口径客观报告，供 VLM 判定各路径是否适用。
          - **缺席处置（客观反馈，不代生成）**：若 VLM 在触发条件成立时漏掉候选 A/B/C 中的某条（或 D 条件成立时漏掉 D）且未在 physical_motivation 中说明放弃理由，主模型**不代为生成**（生成决定权完全在 VLM）——处置为：(1) 在 working_note 决策日志记录"[VLM 候选缺席违规]"（缺席项 + 触发数值）；(2) 在下一轮 `local_state_description` 客观附注该事实（如"上轮 D 适用条件成立但候选缺席"），由 VLM 在新证据下自行补正。VLM 生成的 A/B/C/D 候选以 g ≥ 0.5 保底**入队**（强制保留条款）。
          - **原理**：lens 膨胀是 lens 被添加后最常见的退化模式，有四种互斥的物理假设（lens 越界 / disk 骨架偏小 / lens 寄生 / **自设上限过紧**），单一方向探索会错过最优修复。强制多路径竞争让 beam search 并行探索能力完整发挥；其中 D 假设专门针对"触界的上限是上一轮修复动作自己设的、而非物理边界"的情形——此时继续收紧/移除都在错误的方向上打转，唯一正确动作是放宽。
          - 若 s' 不含 lens，不触发。
        - **通用触界参数扫描（泛化规则，客观事实报告，不生成候选）**：每轮主模型从 s' 的 galfit.NN 拟合值与 `.cons` 边界（含 `iter{n}.cons` 与输入约束）**逐参数比对**（一切成分、一切有边界的参数），列出全部触界参数（|拟合值 − 边界| ≤ 2%×|边界| 或相等），并对每个触界参数**客观报告五项事实**：参数名与拟合值（px，遵像素契约）、被触边界值、边界方向（上/下）、**边界来源**（自设 = 当前边界 ≠ 输入配置原始边界，系前轮收紧/修复动作产生；原始 = 与输入一致）、连续触界轮数（从 working_note 状态账本统计）。该清单原文写入 `local_state_description`（格式如："触界参数清单（客观事实）：lens_Re=9.0px 触上限 re_max=9.0px [自设（原始 12.6px）] 连续 2 轮； companion_Re=1.0px 触下界 re_min=1.0px [原始] 连续 4 轮； …"），**禁止附加任何候选方向建议或祈使句**——生成义务由 VLM 按 `beam_action_generation_prompt_galfit.md` 🔑 触界参数边界放宽规则 的分级（Tier 1 自设必出 / Tier 2 原始竞争 / 例外豁免）自主决定。**无 `.cons` 边界的参数不触界、不上清单**。
        - 未来若需扩展其他客观数值触发（如 sky 背景异常、某成分 Mag 异常暗等），按同样模式在此子项追加规则——**只描述现象与数值事实，生成决策一律交 VLM**。
    - **追溯标记**：d.ii 触发的数值检查（无论 VLM 最终是否生成对应候选）在 `working_note.md` 的相应分支小节标注"[主模型数值规则委托]"，记录实测数值（伴星系：通量比/ΔMag；disk Re 瓶颈：lens/bar Re 与 re_max、disk Re 与 re_max、Re 比值、通量比、命中子条件 A/B；lens Re 膨胀：lens Re 与 re_max、disk Re、是否触上限/反置、lens_Mag、disk_Mag、命中的竞争路径 A/B/C/D，其中 D 需记录**当前 re_max 与输入原始 re_max 的来源判定**与两简并子条件的实测结果）与 VLM 的视觉验证结论。便于后续审计成分保留/移除/参数调整决策的依据。
e. **登记 s' 评分并更新 s\*（物理性守门）**：按 §去重与排序 中的 score 函数给 s' 打分；**仅当 d.i 的 Physicality Verdict 为 PASS（或 verdict 兜底判定通过）时**，s' 才与之前的最佳模型比较——若 score(s') > score(s\*)，s\* ← s'，`stagnation = 0`；否则 `stagnation += 1`。verdict=FAIL 的 s' **不参与 s\* 比较**（记 `stagnation += 1`），但其修复候选照常在步骤 f 入队——修复回路是 beam search 的核心路径，FAIL 状态的后继可能快速恢复到 PASS。覆写 `working_note.md` 的"Beam 状态快照 / 当前最优 s\*"小节。**注意：stagnation 仅用于终止判定，不构成跳过步骤 d 的理由——s' 的后继可能优于 s\*（详见 §候选生成的诊断式原则）**。
f. **去重 + 打分 + 入队**：主模型对每个新候选（d.i VLM 候选与 d.ii 主模型数值规则候选合并后的完整集合）：
    - 与 Q 中已有 (s_j, a_j) 做 §去重与排序 的语义去重；若等价则保留 g 较高者。
    - **与执行历史比对（图搜索 visited set，与 Q 内去重共用同一套签名判据）**：按步骤 1.b.0 的 R1/R2 规范，把候选的假想输入规范形式 / 闭式转移投影签名与**输入账本、结果账本**比对——R1 命中 → 丢弃；R2 严格命中 → 不入队（它被执行时会被零成本回滚，入队只是浪费 Q 名额，记入决策日志）；仅结构一致 → 标"[疑似近重复]"（维度 4 记 0）。
    - 对保留者按六维打分得到 g（带"[疑似近重复]"标记者的退化惩罚维度按 b.0 规则处理）。
    - **g_min 阈值**：若 g < 0.3，直接丢弃，不入队（记入 `working_note.md` 的"跨分支决策日志"，标注 action_id 与丢弃原因）。这避免低质量候选堆积导致队列永不空。
    - 把 (s', a_new, σ_new, g, branch, depth=depth+1) 加入 Q；按 g 降序重新排序；截断到 W=5。被截掉的元素同样记入"跨分支决策日志"。
g. **持久化**：在 `working_note.md` 的相应分支小节追加本轮记录（配置/工具调用/成分/C、P 摘要/reduced_χ²/BIC/**VLM 物理性判定（verdict 与 failed_checks 摘要）**/VLM 残差特征/入队的 action_id 列表）；覆写 Beam 状态快照（含 Q 的当前 5 项与 n 计数）；**维护两本账（图搜索 visited set，硬约束）**——本轮成功拟合后：输入账本追加 `_iter{n}.feedme` 的规范形式（`check_feedme_file` 输出）；结果账本追加状态签名 + BIC + verdict（通量占最亮成分 < 0.5% 的成分标 [zombie]；若结果与账本某行僵尸等价，追加回滚边）；**同步更新 `global_state_description` 蒸馏**（按 §global_state_description / local_state_description 生成规范：[状态账本] 追加本轮行、[回滚边]/[已验证盆]/[被否定假设（含失败原因与重开条件）] 增量、刷新预算——下一次 d.i 调用即使用更新后的版本）。
h. **派生新分支（可选）**：当主模型发现某候选与当前束内主流方向显著不同、且 g ≥ 0.5 时，可标记新分支字母（branch_counter += 1，如 "B"），并在 working_note.md 新建 "分支 B" 小节。新分支共享全局 n 与 global_iter_id，避免预算失控。

### 步骤 2. 终止条件（任一满足即停）
- Q 为空；
- n ≥ 15；
- stagnation ≥ 15（连续 15 次出队的最高优先级 (s, a) 都没产生比 s\* 更优的 s'，即束内收敛；当前阈值与 N_max 相同，实际由 n ≥ 15 先行触发）。

### 步骤 3. 进入阶段三前的收尾
1. 在 `working_note.md` 的"跨分支决策日志"写下：终止条件、累计拟合次数 n、被探索过的分支数、被截掉的候选 action_id 列表。
2. 锁定 s\*：在 `working_note.md` 头部的"Beam 状态快照 / 当前最优 s\*"小节确认其对应的 `archives/<timestamp>.<hash>/` 目录与 `_iter{global_iter_id}.feedme` 文件路径——这两个路径将作为阶段三的输入。
3. **同心约束合规性回查（硬约束）**：若 s\* 的主星系成分数 K ≥ 2（Disk/Bulge/Bar/Lens），但其对应的 `_iter{n}.feedme` 的 `G)` 项未指向含成对 `x/y offset` 链的 `.cons` 文件，视为流程违规——回退到步骤 1.b.2 补齐 `.cons` 后重跑该轮拟合，再进入阶段三。
4. 若 s\* 是退化状态（如成分参数碰边界、bulge/disk 通量完全相同），不要强行进入阶段三；改为：把"修复退化"作为强约束写入 `generate_galfit_beam_actions` 的 `local_state_description`，重启一轮 beam search（重置 Q 与 stagnation，但保留 n 与 global_iter_id 计数）。

### §候选生成的诊断式原则（主模型硬约束）

**核心命题**：步骤 d（候选生成 + 物理性判定）是 beam search 的诊断回路，不是"拟合改善时的奖励"。只要步骤 b 拟合成功产出对比图/summary（即未进入 b.5 失败处置分支），**必须**无条件执行步骤 d——无论 s' 的物理性判定结果、BIC 是否反升、参数是否触界、队列是否仍有未消费候选、拟合预算是否紧张。此规则无例外——VLM 的视觉诊断是 beam search 的核心回路，跳过它会让主模型退化为"看数字猜方向"的贪心搜索，丧失多模态诊断能力。

**原理**：s' 的 BIC 反升不等于物理假设错误——常见情况是候选的物理方向正确，但某个次级参数（中心位置 / PA / Re 量级 / n / q）初始化不当，拟合器收敛到次优解。此时 s' 的残差携带"哪个参数需要修正"的诊断信息，只有调用 `generate_galfit_beam_actions` 才能把残差转译为修正候选。跳过步骤 d 会令 beam search 退化为贪心搜索，错过"同方向、修正参数"的后继——这正是 beam search 相对贪心搜索的核心价值所在。

**通用失败→修正模式**（由 VLM 在候选生成阶段自主识别；主模型不得在 `global_state_description` / `local_state_description` 中预先指定这些方向，见 §global_state_description / local_state_description 生成规范）：
- 成分中心位置初始估计有误 → s' 残差在"模型位置"与"真实位置"之间呈偶极 → `tune(component, x_real, y_real)`（px 直写）
- 成分 PA 与真实主轴斜交 → s' 残差呈四极矩 → `tune(component, pa)`（N=+Y 契约）
- 新增成分 Re 量级偏小 → s' 残差呈中心环状正残差 → `tune(component, Re_init≈...)`（px 直写）
- 新增成分与父状态成分简并 → s' 中某成分身份坍缩（n/Re 触界）→ 释放/固定 n，或加 `.cons` 边界打破简并

**执行校验**：下一轮迭代出队前，确认 working_note 相应分支小节已有"本轮 generate_galfit_beam_actions 返回的候选 action_id 列表"记录；若缺失，视为漏执行——禁止出队，回到步骤 d 补做。

### §非物理结果恢复协议（物理性 FAIL 的受保护恢复候选）

**核心命题**：物理性 FAIL（VLM Physicality Verdict = FAIL）时，VLM 基于残差图生成的修正候选往往关注视觉可见的问题（PA 偏移、中心偏移等），而不太关注"收紧 Re 边界"这类数值诊断驱动的机械修正——因为 Re 全序违规是图例/参数摘要精确数值诊断的（如 `re_lens=13px > re_disk=7px`），残差图上不一定有直观对应。如果恢复候选和其他候选一样走 g_min=0.3 的截断，往往会因评分偏低（残差改善不直观）被丢弃，导致物理性 FAIL 的路径被过早放弃。

本协议的解决方案是：**不绕过 VLM**（VLM 照常在步骤 d.i 调用，且其自身已按 prompt 约定给出针对 failed_checks 的修复候选），由主模型在步骤 d.ii 生成**受保护的恢复候选**，通过 §去重与排序 的"强制保留条款"机制保证其入队（g ≥ 0.5 保底），与 VLM 候选一同公平竞争。当恢复候选被出队并拟合后，照常走 b→c→d→e→f 全流程（包括下一轮 VLM 判定与候选生成），恢复链通过 beam search 的自然迭代逐步推进。

**触发条件**：步骤 d.i 中 VLM Physicality Verdict = FAIL。

**swap 分支（disk ↔ bulge 标签互换）**：若 `swap_hint=disk_bulge_swap`（FAIL 仅由 {disk, bulge} Re 反置构成），修复路径就是交换标签——VLM 应已给出该候选；若漏给，主模型按 B 类填空保底生成（g ≥ 0.5）。此分支**不生成**下述恢复候选 A/B（交换标签候选本身即修复路径）。

**恢复候选的生成规则**（主模型在步骤 d.ii 执行，与伴星系检查并列；适用于 swap 之外的 FAIL）：

从 failed_checks 中识别**膨胀成分**（Re 超过链中上方成分的那一个），生成以下 1-2 个恢复候选：

**恢复候选 A（Re-bound 收紧 + 热启动）**：
- `action_id`: `<branch>-<parent>-recovery-rebound`
- `primitives`: `tune(inflated_component, re_max = 0.9 × Re_above)`（写入 `.cons`），其中 `Re_above` = 链中上方相邻成分的当前拟合 Re。若膨胀成分是 disk，则设 `disk Re_init = 1.5 × max(下属 Re)`（不设上限，而是推大初始值）。其余参数从 s' 的拟合值热启动。
- `expected_C'`: 同 s'（不增删成分，仅调边界）
- `expected_behavior_tag`: `re_bound_enforce`
- **保底分 g ≥ 0.5**（强制保留条款，豁免 g_min 截断）

**恢复候选 B（路径最近 PASS 态热启动 + 收紧）**：
- `action_id`: `<branch>-<parent>-recovery-warmstart`
- `primitives`: 以当前 beam search 路径上**最近的物理性 PASS 状态**的全部成分拟合值为初始值，叠加候选 A 的 Re 边界收紧。
- `expected_C'`: 同 s'
- `expected_behavior_tag`: `warmstart_rebound`
- **保底分 g ≥ 0.5**（强制保留条款）
- 仅在候选 A 生成后的下一轮（如果 A 拟合后仍 FAIL）才生成——因为 A 和 B 是顺序链的两步，A 先探索，A FAIL 后 B 才有"路径最近 PASS 态"作为热启动源。

**渐进放宽（等级 3，已并入 §Lens Re 膨胀触发规则的候选 D，必出而非可选）**：若候选 A 或 B 拟合后 PASS 但 lens 仍触**收紧后的上限**，且无简并信号（Re_lens/Re_disk < 0.85 且 Mag_lens > Mag_disk + 0.2），则该情形**就是** d.ii lens Re 膨胀检查中候选 D 的触发场景（自设边界判定：当前 re_max < 输入原始 re_max）——VLM 必产出 D（`tune(lens, re_max = 触界值 × 1.3)`），VLM 漏给时主模型按"[主模型 lens 膨胀补充-D]"保底生成（g ≥ 0.5）。非 lens 成分的渐进放宽仍按标准 `tune(component, re_max += 2px)` 动作处理，适用 🔑 触界参数边界放宽规则。

**追溯标记**：恢复候选在 `working_note.md` 的相应分支小节标注"[恢复候选 A/B]"，记录 failed_checks 清单、设置的 re_max 值、热启动来源。VLM 候选与恢复候选平等竞争，被截断的也照常记录。

**与 VLM 的关系**：本协议不替代 VLM——VLM 仍在步骤 d.i 照常生成视觉驱动候选与物理性 FAIL 修复候选（可能包括 PA 修正、成分增删等）。恢复候选只是 d.ii 多了一条规则，与伴星系检查等现有 d.ii 规则并列。两类候选在步骤 f 统一打分入队，beam search 的并行探索能力完好保留。

### §去重与排序（主模型职责，禁用规则去重）

**语义去重判据**——两个 (s_i, a_i) 与 (s_j, a_j) **同时满足**以下三条即视为等价，保留 g 较高者：
1. 施加动作后的预期成分清单 `expected_C'` 在**物理身份**上等价（允许命名互换，如 "bulge n=0.5 q=0.4" 等价于 "bar n=0.5 q=0.4"）。
2. 预期参数取值在容忍带内一致：Re ±20%（expdisk 比较有效半径）、Sersic n ±0.5、q (b/a) ±0.1、PA ±10°（**N=+Y 契约**，与 feedme `10)` 同帧）、mag ±0.5。
3. `expected_behavior_tag` 一致。

**历史状态去重（图搜索环检测，先于语义去重与六维打分执行）**——语义去重只比对 Q 内候选是不够的：Q 是"待探索"，执行历史是"已探索"，两者必须共用同一套签名判据。每个新候选在打分前先做两级比对（规范见步骤 1.b.0）：
1. **vs 输入账本（R1，所有动作）**：候选转写为假想 feedme 的规范形式（结构 × toggle 配置 × `.cons` 边界带 × 初始值带，px），与历次已执行输入比对；带内等价 → 丢弃（同一输入重跑无新信息，记入决策日志）。
2. **vs 结果账本（R2，闭式转移专用）**：remove-only / 参数 revert / 边界还原类候选的产出状态可精确投影，投影签名与已拟合结果签名比对（**僵尸感知**：仅相差 [zombie] 成分的状态等价）；严格命中 → 该候选不执行、不入队（零成本回滚由 1.b.0 处理）；仅结构一致 → 标"[疑似近重复]"，维度 4 记 0 分。

**优先级分数 g ∈ [0,1]**——主模型对每个候选按以下六个维度各打 0–1 分，**加权平均**得到 g。**权重非等权：维度 3（路径多样性）权重 ×2**，其余维度权重 ×1，即 `g = Σ(wᵢ·sᵢ) / Σwᵢ`，激活维度 6 时 w = [1,1,2,1,1,1]，维度 6 未激活时 w = [1,1,2,1,1]。加倍理由：等权方案下，同一成分结构的 tune 链（渐进边界放宽、同盆微调）与 context 重开会连续占据束内名额；多样性权重加倍是结构层面的滤重手段——同向重复的候选该维低分，在 g 中被双倍惩罚。注意：多样性低分只降低排序优先级，**不构成方向禁入**；强制保留条款与持续候选保护条款的保底分不受本加权影响。
1. **残差改善潜力**：结合 VLM 给的 σ 与主模型独立判断的残差可解释比例。
2. **物理合理性先验**：是否符合"Disk → (F1/Companion 若检出) → Bulge → Bar → Other"的成分添加次序；是否符合 Bar/Bulge/Lens/Nucleus 的认定条件（见 `<星系成分分析的总体流程>`）。**阶段一 detect_bar_lopsidedness 的检测结果在此维度仅作为弱先验**：检出可适度加分（提示性正证据），但**未检出不得扣分**——未检出是零证据而非负证据。一个基于残差证据（如中心四极矩、高扁率内部结构、bar 状残差等）的 Bar/Lens/Fourier 候选，即使阶段一未检出，其物理合理性得分应基于**残差证据的强度**评判，不得因阶段一未检出而压低。判定成分存在性的金标准是残差驱动的拟合验证，不是阶段一检测。
   **"提示性证据 ≠ 负证据"原则同样适用于历史路径失败**：某候选 X 在父状态 s_i 的成分 context 下拟合失败（BIC 反升、参数触界等），**不构成"X 这个物理假设错误"的判定性证据**。当 beam search 探索到新的成分 context 后，VLM 基于残差证据再次提出同方向候选时，主模型**不得跨 context 套用"该方向历史失败"的结论**压低物理合理性得分。
3. **路径多样性（权重 ×2）**：候选方向与**全局已使用过的候选**（已执行历史，即输入账本/结果账本中的动作族谱）及当前 Q 内已有元素的方向差异越大越加分。方向判据：expected_C' 的成分集合差异为主，expected_behavior_tag / 主要 tune 轴为辅。
4. **退化惩罚**：父状态是否已退化（如 `.cons` 失效、bulge/disk 通量相同）；本动作是否可能继承退化。**此维度评估的是"候选本身是否继承父状态退化"**——一个 BIC 反升的 s'（如某成分参数初始化不当）其后继修正候选（位置修正 / PA 修正 / Re 修正）正在修复退化，应得**低**退化惩罚。
5. **历史一致性**：是否与 `working_note.md` 前序目标连贯，避免反复横跳。
6. **BIC 门槛**：仅当动作涉及 Nucleus/AGN 的增删时启用；预估 ΔBIC 能否跨过 +10 门槛（BIC 取 `fit_statistics` 的 `bic_eff`，见 §BIC 口径；缺失时退回 `bic1d`）。

`score(s)` 用于判定 s\*，与 g 共用同一套维度与同一套权重（维度 3 权重 ×2），区别只在于它评估的是"已完成的拟合状态"而非"待入队的候选"。

**g_min 入队阈值**：任何 `g < 0.3` 的候选直接丢弃，不入队。被丢弃的候选记入 `working_note.md` 的"跨分支决策日志"，标注 action_id 与"g < 0.3"。

**强制保留条款（豁免 g 截断）**：以下候选即便 g 较低也必须入队（至少保留一个变体），因为它们测试的是无法靠残差直觉判断的物理假设或程序化诊断驱动的修复，不探索就永远拿不到证据：

- **扁 Bulge → Bar 候选**：当父状态含 Bulge 且满足联合触发条件（`bulge q < 0.5` AND `|bulge_PA − disk_PA| > 20°`（N=+Y 契约）AND `0.5 < bulge_n < 2.5`（若 free）AND `disk q > 0.5`）时，主模型必须把 VLM 返回的 Bar 方向候选（`tune(Bulge→Bar)` 转换 或 `add(Bar)+tune(Bulge, q_min=0.7)` 新增，至少一个）以 g 不低于 0.5 的保底分入队，**不得因"阶段一未检出 bar"在物理合理性维度（维度 2）压分**。主模型在 local_state_description 中须客观写出四条触发数值，让 VLM 知道触发条件已成立。若 VLM 在已触发情况下未返回任何 Bar 候选，主模型应**主动生成**一个 `add(Bar, n=0.5 fixed, PA≈bulge_PA)` 候选（参照 §候选动作忠实执行原则 的"B 类填空"规则初始化参数），追溯标记"[主模型扁-bulge 触发补充]"，走同样的打分入队流程。
- **Lens 候选**：父状态含 Bar 且 `Re_bar ≳ Re_disk` 或 `q_bar ≳ 0.5` 时，Lens 候选同上保底入队。
- **物理性 FAIL 恢复候选**（见 §非物理结果恢复协议）：当 s' 被 VLM Physicality Verdict 判为 FAIL 时，主模型在 d.ii 生成的恢复候选 A（Re-bound 收紧）和 B（热启动+收紧）以 g ≥ 0.5 保底入队；`swap_hint=disk_bulge_swap` 时的交换标签候选（VLM 给出或主模型保底）同样 g ≥ 0.5 保底。
- **disk Re 瓶颈候选**（见 d.ii 主模型数值规则）：当 lens/bar Re 触上限且满足 Re 简并（≥0.85）或通量接近（≥83% disk）任一子条件时，`tune(disk, Re 更大)` 候选以 g ≥ 0.5 保底入队。
- **lens 自设上限放宽候选 D**（见 d.ii lens Re 膨胀检查）：当 lens Re 触上限、该上限系前轮收紧产生（当前 re_max < 输入原始 re_max）、且两简并子条件均未命中时，候选 D（`tune(lens, re_max = 触界值 × 1.3)`）以 g ≥ 0.5 保底入队（VLM 给出或主模型按"[主模型 lens 膨胀补充-D]"保底）。
- **持续候选保护条款（VLM 跨轮次重复提议）**：若 VLM 在最近 **≥2 次 `generate_galfit_beam_actions` 调用**中（无论父轮次是否相邻、是否同分支）都返回了**同方向**候选，且每次 σ ≥ 0.7，则该方向候选**必须**入队并至少被执行一次，主模型不得以"历史路径失败"、"认知一致性"、"路径多样性"等理由将其 g 压到 g_min=0.3 以下。
  - **"同方向"判据**：`expected_behavior_tag` 相同，且 `expected_C'` 在物理身份上等价（允许参数微调，如 PA=90° vs PA=85°、Re_init=1.5px vs 2.0px 均视为同方向）。
  - **触发后处理**：把该候选的 g 强制设为 **≥0.6**（保底分），在 working_note 标注"[持续候选保护触发]"，记录触发的 2 次（或更多）调用的 session_id 与 σ 值。
  - **执行后的失效边界**：若该候选被执行后拟合失败（BIC 反升、参数触界逃离约束区等），主模型可在**同一成分 context** 内对后续同方向候选给出书面否决理由（写入"跨分支决策日志"），该否决**仅在相同 C' 下有效**。一旦成分 context 变化（新增或删除任一成分），本保护条款对新出现的同方向候选重新生效。
  - **原理**：VLM 跨多次调用持续给出同方向高 σ 候选，是比单次 σ 更强的证据——单次可能是 VLM 误判，多次跨 context 重复出现则说明残差特征稳定存在。主模型连续压分相当于把主模型的先验凌驾于 VLM 的视觉证据之上，违反 beam search 并行探索的设计初衷。

### §global_state_description / local_state_description 生成规范（主模型职责，硬约束）

`generate_galfit_beam_actions` 的 VLM 是**无状态**的：每次调用只看到当轮残差图。跨轮次记忆由两个参数承载，均由主模型生成——

- **`global_state_description`（全局状态）**：跨轮次稳定事实的**蒸馏**（不是 working_note 全文！）。主模型从 working_note 蒸馏，固定 schema、固定字段顺序，总量 ≤ ~50 行：
  ```
  [元信息] 像素契约：本文件所有 Re/位置一律 px，与 VLM 读图面板、feedme 参数行同一参考系，可直接 diff、可直接写入（主模型原样填入，无任何单位换算）；禁止出现 arcsec
  [阶段一结论] bar/lop 检测结论；PA（N=+Y 契约：Y 轴正上 0° 逆时针，可直接进 feedme 10) 行）；b/a
  [状态账本] 每个已拟合状态一行（VLM 生成每个候选前必须逐行比对 expected_C' 落地签名）：
      | 轮次 | 状态签名(px) | BIC_eff | verdict | 备注 |
      | A.4 | {disk:Rs6.5(Re10.9),M16.2; bulge:n4f,Re1.2px,M18.7; comp:px(95,128),Re0.5px} | 23499 | PASS | comp Re触下界 |
      签名规范：成分:类型,n状态(f/free+值),Re(px),Mag,q,PA；坍缩成分（通量占比<0.5%）标 [zombie]
  [回滚边] 闭式转移的已确认等价关系，命中即零信息：
      例：A.5 --remove(bar)--> ≡A.4；A.11 ≡A.10+[zombie bulge]（bulge 坍缩，真实内容与 A.10 相同）
  [已验证盆] px 值 + 来源轮次 + 证据级（[数据验证]/[待核验]）。
      例：companion ≈ px(95,128)，r≈33px，1D尖峰r≈33px 共位，A.4/A.6/A.8 三轮锚定 [数据验证]
  [被否定假设] 五字段缺一不可：方向 | context签名 | 定量证据 | 失败原因 | 重开条件。
      例：bar+bulge 共存 | {disk,bulge,bar,comp} | ΔBIC_eff +15.5/+67.5（A.5/A.11）| 两成分 Re≈1.7px 简并互抢通量 | 简并对消失
  [预算] n = X / N_max，剩 Y
  ```
  维护规则：每轮主循环结束（步骤 g 持久化时）同步更新——
  - `[状态账本]`：每次成功拟合追加一行；通量占最亮成分 < 0.5% 的成分标 `[zombie]`（**相对通量判据，禁止绝对星等**）。
  - `[回滚边]`：R2 严格命中、或拟合结果与账本某行僵尸等价时追加。
  - `[已验证盆]`：新增条件 = 拟合产出物理值且无触界（坍缩/触界/漂移的不算）。
  - `[被否定假设]`：新增条件 = 同 context 下 BIC 反升 ≥ 10 或物理性 FAIL，**必须带 ΔBIC/触界数值 + 失败原因 + 重开条件**。
- **`local_state_description`（本轮状态补充）**：当轮客观描述，包含：
  1. 父状态成分清单 C、关键参数 P 摘要；
  2. **当前拟合结果的具体问题**（最重要的部分，客观详尽）：
     - 触界参数（标注 ⚠️ 与具体数值，**px 单位**，如 `bar_Re=30px ⚠️触上限`；边界来自 `.cons`，无约束不触界）；
     - 残差图未拟合特征（位置 / 对称性 / 强度，引用阶段一视觉特征原文）；
     - 成分身份混淆（disk/bulge 标签互换、bar 变圆变胖、bulge 坍缩成点源）；
     - **扁 Bulge → Bar 触发数值**（若父状态含 Bulge）：客观列出 `bulge q`、`|bulge_PA − disk_PA|`（N=+Y 契约）、`bulge_n`、`disk q` 四值并标注联合条件是否成立——只报数值，不暗示方向；
     - **disk Re 瓶颈信号**（若父状态含 lens/bar）：lens/bar 的 `Re`/`re_max`/是否触界 + disk 的 `Re`/`re_max`/是否触界，命中时标注"disk Re 瓶颈命中"——只报数值；
     - **外围残差符号**（1D 曲线 r > 2×Re_disk 区域）："Data 亮于 Model" / "Model 亮于 Data" / "平坦"，引用阶段一原文；
  3. 主模型数值规则委托内容（伴星系条件 A 数值 / lens Re 膨胀信号 / 其他 d.ii 触发的定量信号）。

**两者共同的严禁条款（候选方向建议）**：
- ❌ 不得列出"优先修复方向：(1)...(2)...(3)..."这类方向清单；
- ❌ 不得暗示或推荐特定动作类型（"建议释放 disk n""建议加 Lens""建议回退到 A.2""建议收紧 bar Re 上限"）；
- ❌ 不得预先做方向收敛或筛选——这是 VLM 的职责（发生在 prompt 规则 + 打分阶段）。
- ⚠️ `global_state_description` 的 `[被否定假设]` 是**事实记录**不是方向建议——记录"X 方向已被否定（ΔBIC=+402）"是合法的，但不得借记录之夹带"因此应该走 Y 方向"。

**Physicality Verdict 的处理**：物理性判定权在 VLM——verdict / failed_checks / swap_hint 由 VLM 在返回中输出，主模型只解析、记录与执行守门，不得改写；主模型亦**不得**在 local_state_description 中预告自己预判的物理性结论（如"本轮应判 FAIL"），避免引导 VLM 的判定。

### §候选动作忠实执行原则（主模型职责，硬约束）

`generate_galfit_beam_actions` 返回的每个候选声明了一组 primitives（如 `add(Bulge, n=4 fixed, [Re_min, Re_init, Re_max]=[4,6,10]px, q=0.9, PA=135°)`、`tune(bulge, n=0.5 fixed)`、`tune(disk, +F1)`）。主模型在把候选转写为 `_iter{n}.feedme` 的参数行时，必须严格区分两类字段：

**A 类：不得修改的字段（候选的语义核心）**
- 成分类型（`0) sersic` / `expdisk` / `edgedisk` / `psf` 等）
- `# STRUCTURE:` 语义名（候选 target 的名字；新增成分按其物理类型命名 disk/bulge/bar/lens/companion/agn）
- 物理参数的 free/fixed 状态与目标值（如 `n=4 fixed`、`n=0.5 fixed`、`n free`、`q fixed=0.33`）
- 量级性约束（如 `q>=0.5`、`Re<=30px`、`Re_max=20px`——写入 `.cons`）
- 成分的增删（`add` / `remove`）与目标名
- 中心约束策略（`.cons` offset 链 / 自由 / 固定坐标）
- Fourier 模式阶数（F1）与是否启用
- 原子操作的捆绑关系（候选声明的 1–2 个 primitive 必须作为一个整体执行，不得拆分）

**B 类：允许主模型填空的字段（VLM 未给精确值时）**
- **未被 primitives 声明的参数（默认热启动，见步骤 1.b.1 热启动规则）**：参数行初始值一律取父轮 galfit.NN 的收敛值（toggle 沿父轮），不得沿用父 feedme 的旧输入值——VLM 的诊断以父收敛解为条件，未提及即"可接受"，子轮次从收敛解起步才是完整执行 VLM 的判断。
- 被 primitives 声明但未给精确值的参数：具体初始值数字从父状态对应参数的**拟合值**（而非旧输入值）附近起步，如父状态拟合 Re=4.6px 则子候选 Re_init 取 4–5px 区间，不可数量级偏离
- `.cons` 行的具体边界数字（VLM 给了 Re 三元组时直接采用；仅当候选声明量级约束而无精确值时按声明量级落位）
- **Re 三元组的直写（galfit 无换算）**：VLM 候选里的 Re 是**像素值**（即使偶带 `"` 号也是像素——VLM 看的是像素网格）；feedme `4)` 行同样是像素——主模型把 `[Re_min, Re_init, Re_max]` 中的 **Re_init 原样写入 `4)` 行**，`Re_min/Re_max` 写入 `.cons` 的 `re` 行（`<编号>  re  <Re_min>  <Re_max>`）。**唯一例外**：目标成分是 expdisk（Disk）时，`4)` 行写标长 `Rs = Re / 1.68`（Re 三元组指有效半径）。**禁止任何 arcsec 换算、禁止调用单位转换工具**。
- **伴星系 / 成分中心坐标的直写**：VLM 候选里给出的坐标（如 `tune(companion, x=115, y=130)`）是对比图上的**像素坐标**，与 feedme `1)` 行同一参考系——主模型**原样填入**（expdisk/sersic 的 `1)` 行即图像像素坐标）。**转换后的坐标仅为初始估计值**——VLM 像素判读可能有 ±10-20 px 误差（父模型残差越脏，误差越大），伴星系位置保持 `1 1` 自由并由 `.cons` ±5px 软窗约束（见步骤 1.b.2）。仅在位置已校准后的后续轮次中才可考虑固定。
- **expdisk 新增（add(Disk) 或 sersic→expdisk 切换）**：候选给出的 Re 三元组是有效半径，`4)` 行写 `Rs = Re_init / 1.68`，`.cons` 的 re 边界同样按 Rs 值落位。
- 其他非伴星系成分的中心坐标（如新增 Bulge 的 x/y）：通常取父状态拟合值（同心）或主星系中心像素，不用 VLM 给的粗略值。

**核心约束**：主模型若认为某候选的 A 类字段有缺陷（物理不合理、与父状态冲突、重复已失败方向等），**必须整条丢弃该候选**（记入 `working_note.md` 的"跨分支决策日志"，标注 action_id、丢弃原因、以及是哪条 A 类字段触发了否决），**绝不得在修改 A 类字段后执行**。这保证每次拟合结果可以干净归因到 VLM 提议的某个方向——要么支持它，要么否定它，不会成为"执行走样"的污染样本。

**B 类字段的合理调整范围**：即使 B 类允许填空，主模型也不能借机改变候选的物理意图。例如 VLM 提议 `add(Nucleus, Re_init=1px)`，主模型可以把 Re_init 填成 0.8px 或 1.2px（数量级一致），但不能缩到 0.3px（数量级偏离 3 倍，实质上把"延展核"改成了"致密点源"）。

**典型反例（严格禁止）**：
- VLM 提议 `add(Nucleus, n=4 fixed, Re_init=1px)`，主模型改成 `add(Nucleus, n=2 free, Re_init=0.3px)` 后执行——同时违反 A 类（n 值/状态）与 B 类的合理范围
- VLM 提议 `tune(bulge, n=0.5 fixed)`（把 bulge 转 Bar），主模型改成 `tune(bulge, n free)` 后执行——虽然看起来"更宽松"，但实际跑的是另一个候选，原提议未被测试
- VLM 提议 `add(Bulge, q>=0.5)`，主模型去掉 q 下界后执行——实质上把"加一个圆 bulge"改成了"加一个任意椭率 bulge"

**正确做法**：若主模型认为 VLM 提议的 n=4 不合理，应丢弃该候选并在决策日志写下"丢弃：Nucleus n=4 与 pseudo-bulge 先验冲突"；若主模型想测试 n=2 的变体，应作为主模型自己生成的新候选走完整打分流程，而不是寄生在 VLM 候选上偷改。

### §多分支 working_note.md 模板（智能体必须按此结构维护）

```markdown
# Galaxy {ID} Beam Search Working Note (GALFIT single-band)

## 基本信息
- 星系ID / 坐标 / 拟合区域（feedme H) 项）/ 波段
- 束宽 W = 5；全局预算 N_max = 15
- PA 契约：N=+Y（Y 轴正上 0° 逆时针，与 feedme 10) 同帧）；单位契约：全 px
- 阶段一结论（VLM 形态分类、bar/lop 检测结论、PA 取值、b/a）

## Beam 状态快照（每次主循环迭代后覆写本节，不要追加）
### 当前最优 s*
- 分支 / 轮次: <branch>.<local_round>   例: A.3
- 成分清单 C*: {...}（# STRUCTURE 名清单）
- reduced_χ² / BIC: ... / ...（chisq1d_nu / BIC_eff，见 §BIC 口径）
- VLM 物理性判定: PASS
- 对应 archives 目录: archives/<timestamp>.<hash>/
- 对应 feedme: _iter{global_iter_id}.feedme

### 当前优先队列 Q（按 g 降序，至多 5 项）
| 排序 | 分支 | 父轮次 | 动作摘要 | σ | g |
|---|---|---|---|---|---|
| 1 | A | A.2 | +Nucleus(致密,psf) | 0.55 | 0.78 |
| 2 | B | B.1 | release bulge_n | 0.35 | 0.62 |
| ... | | | | | |

### 全局拟合计数器
- n = X / 15
- stagnation = Y / 15
- global_iter_id = Z (下一个 feedme 后缀)

## 状态账本（图搜索 visited set；每次成功拟合追加行，不覆写）
### 输入账本（已执行的 feedme 规范形式，check_feedme_file 输出）
| 轮次 | 输入签名（结构 × toggle × .cons 边界带 × 初始值带，px） | _iter{n}.feedme |
|---|---|---|
| A.4 | {disk:Rs6.5(Re10.9); bulge:n4f,Re~1.2px; comp:px(95,128) 窗±5px} | _iter4.feedme |

### 结果账本（已拟合状态签名 + 结局）
| 轮次 | 状态签名(px) | BIC_eff | verdict | 僵尸/触界 |
|---|---|---|---|---|
| A.4 | {disk:Rs6.5(Re10.9),M16.2; bulge:n4f,Re1.2px,M18.7; comp:px(95,128),Re0.5px} | 23499.2 | PASS | comp Re触下界 |
| A.11 | {disk,bar,comp, bulge:[zombie]} | 23527.2 | FAIL | bulge M24 坍缩 |

### 回滚边（闭式转移等价关系，命中即零信息）
- A.5 --remove(bar)--> ≡A.4
- A.11 ≡A.10+[zombie bulge]

## 分支 A: <分支主题，如 "Disk+Bulge 主线">
### A.1 (对应 fit #1, feedme: _iter1.feedme)
- 配置 / 工具调用（含 .cons 内容摘要）/ 成分 C / 参数 P 摘要 / reduced_χ² / BIC
- VLM 物理性判定: PASS/FAIL（failed_checks 摘要；FAIL 时粘贴证据）
- VLM 残差特征摘要（来自 generate_galfit_beam_actions 的阶段一）
- generate_galfit_beam_actions 返回的候选 action_id 列表与入队情况（被截掉的也记）
### A.2 ...

## 分支 B: <分支主题>
### B.1 (对应 fit #N, feedme: _iter{global_iter_id}.feedme) ...

## 分支: 失败归档
### (fit #M, 父=<branch>.<round>, 失败)
- 动作: <action_id 或描述>
- 失败原因: <工具异常 / check_feedme_file 拒绝 / 拟合未产出等>
- 处置: 加入父状态禁忌集

## 跨分支决策日志（追加，不覆写）
- fit #X: 派生分支 B（原因：A 在 R2 退化，探索早期 Bar 方向）
- fit #Y: 合并 A.3 与 B.2（语义等价，bulge n=0.5 → bar）
- 终止: <终止条件>，累计 n=..., 被探索分支数=...
```

**命名规则**：
- 分支标识：大写字母，按派生顺序递增（A、B、C…），初始分支为 A。
- 分支内轮次：`<branch>.<local_round>`（A.1, A.2, B.1…），local_round 仅在该分支内递增。
- feedme 文件名：仍用全局 `_iter{n}.feedme`（n 为 global_iter_id），避免文件名冲突；分支归属通过 working_note 索引而非文件名承载。约束文件 `iter{n}.cons` 与 feedme 同目录、同编号。
- `archives/` 子目录：沿用现有 `<timestamp>.<hash>/` 命名，不改。

### 智能体执行守则（防止上下文超限）
1. **持久状态写 `working_note.md`，不要靠上下文记忆**：Q 的当前内容、s\*、n、stagnation 全部以 working_note 的"Beam 状态快照"为唯一真源；每次决策前先 Read 该节。
2. **上下文只保留当前一轮**：当前出队的 (s, a) 与刚生成的候选列表；处理完立即落盘并清出上下文。
3. **覆写优于追加**：Beam 状态快照每次覆写；分支小节与跨分支日志才追加。

### 步骤 4. 物理意义分析 与 奥卡姆剃刀原则（beam search 终止后执行）
- 物理意义分析：严格遵循 `<星系成分物理意义分析与策略>` 章节，对 s\* 的每个成分逐条复核参数物理意义。如出现不物理情况（如 Bulge Re < 0.2 px 但被强加为 Sersic、Bar 的 PA（N=+Y 契约）与图像明显冲突），**重启一轮 beam search**：把"修复该不物理成分"作为强约束注入 `generate_galfit_beam_actions` 的 `local_state_description`（reset Q 与 stagnation，保留 n 与 global_iter_id）。对于 Bulge Re 处于 0.2–0.5 px 边界区域的情况，应在 beam search 中同时探索 Sersic 和 psf 两条路径进行竞争对比——只有 psf 路径的 2D 残差明显更优时才采纳，否则保留 Sersic。Bulge Re < 0.2 px（坍缩为点源）时必须替换为 psf 类型（AGN 代偿 Bulge 的物理意义优先）。
- 奥卡姆剃刀原则：
  - **Nucleus/AGN 成分**：若 s\* 含 Nucleus 且 ΔBIC < 10（BIC_eff 口径，见 §BIC 口径），把 `remove(Nucleus)` 作为最高优先级候选重启 beam search 验证；删除后 BIC 反升则保留 Nucleus。
  - **伴星系（Companion）**：若 s\* 含 Companion 且通量比 ≤ 1%（条件 A，计算方式同 d.ii），把该数值结论作为强上下文写入 `generate_galfit_beam_actions` 的 `local_state_description`（格式同 d.ii），由 VLM 执行条件 B 视觉验证（原图面板 companion 位置是否有肉眼可见亮斑）。仅当 A∧B 同时成立时，把 `remove(Companion)` 作为最高优先级候选重启 beam search 验证。若原图有可见亮斑（条件 B 不命中），不触发移除。
  - **F1 成分**：F1 的 amplitude > 0.02 即物理意义成立，禁止因 BIC 变化移除；仅当拟合发散/参数不物理时才通过重启 beam search 验证删除。
- 上述重启 beam search 的累计 n 仍受 N_max = 15 总预算约束；若预算已耗尽，进入阶段三由阶段三判定是否可接受。

阶段三. 科学目标校准与报告撰写
* **锁定最佳结果**：从 `working_note.md` 的"Beam 状态快照 / 当前最优 s\*"小节读取最优轮次对应的 `archives/` 子目录与 `_iter{n}.feedme`，作为本阶段所有分析对象的唯一来源。给出其对应的形态学物理意义（如：成分 A 代表经典的盘结构，成分 B 代表致密的核心星团）。
* **正式落锁前，必须调用 subagent `best-round-verifier`** 对候选轮做独立审计，**`FAIL` 严禁落锁**，按"阻断性问题"修复后复审至 `PASS`；`PASS`（含 WARN）方可落锁。
* 科学目标校准：科学目标关心偏心的影响，如果最佳结果不包含 Fourier(F1) 成分，调用 fourier_mode_analysis 对残差图进行分析判断是否需要采用 fourier mode 对偏心非对称残差拟合。如果已经包含 Fourier 成分，则跳过该步骤。
    - 只能使用 1 阶 Fourier 模式进行拟合补偿。且只能作用于 Disk 成分 或者 是单 Sersic 模型（如果没有 Disk 成分）——实现方式为在该成分块的 `Z)` 行之前追加 `F1) <amp> <phase> 1 1` 参数行。
    - F1 成分的 amplitude 大于 0.02，且拟合质量没有恶化 就可以保留；更新最佳轮次为带 F1 的最新轮次。否则放弃 F1 成分，保持原来的最佳轮次。若 fourier_mode_analysis 建议 adding，回到阶段二重启一轮 beam search：把"+F1"作为强约束注入 `generate_galfit_beam_actions` 的 `local_state_description`（reset Q 与 stagnation，保留 n 与 global_iter_id 计数）。
* 报告撰写：使用 `write_file` 工具将分析结论写入当前星系目录：`analysis_report_xxx.md`。
* **报告内容包含：**
    * **生成时间：** 日期和时分秒。
    * **预处理信息：** 掩膜说明、背景设定依据。
    * **迭代过程流水账：** 基于 `working_note.md` 的多分支结构整理——按分支（A/B/C…）列出每轮新增/删除成分的依据（多模态视觉判断记录）、参数发散与回退记录、束内被截掉的候选 action_id、跨分支决策与语义去重合并事件。
    * **最佳结果锁定分析与结果：** 最佳结果的锁定理由、最终采用的参数表、物理意义解读。
    * **附件索引：** 最佳轮次的目录路径、最终的 `feedme` 文件、最终的拟合对比图（原图/模型/残差）路径。
    * **json格式化输出：** 在文档最后格式化输出轮次信息，便于规则提取，自动化处理。格式如下：
      ```json
      {"best_turn":"<最佳轮次的目录名>","components":["<最优轮次包含的哪些物理成分>"],"galaxy_type":"edge-on/face-on/elliptical"}
      ```
      其中 best_turn 的值为 archives/ 下最佳轮次子目录的名称（如 20260414T093323.c1993a48）。
      物理成分类型：[Disk,Bulge,Bar,Nucleus,Companion,Fourier,SingleSersic,Lens]
      galaxy_type:可选的是正向盘、侧向盘、椭圆星系三种。即：[edge-on,face-on,elliptical],如果盘星系的 Disk成分的 q<0.3，则认定为侧向盘


## 待分析星系

{argument}
