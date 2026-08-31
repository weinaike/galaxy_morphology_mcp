


# 星系成分分析的方法指南
@src/prompts/residual_analysis_message.md
---

## PA 约定覆盖条款（galfit 模式专用，优先级高于上文引用文件中的 PA 约定）
上文引用的 `residual_analysis_message.md` 面向多波段 GalfitS，其"PA 约定"要求 sky-PA 并对齐指南针。**galfit 单波段模式不适用该条款**：本模式采用 **N=+Y 契约**——假定图像正北方向与 Y 轴正上方一致，**PA 一律按"图像 Y 轴正上方为 0°，逆时针增大"读与写**（读 PA 对齐图像纵轴，不对齐指南针），与 feedme `10)` 参数行（GALFIT "+Y 轴为 0°"）数值等同，**主模型把 VLM 读出的 PA 原样写入 `10)` 行，不做任何换算**。`detect_bar_lopsidedness` 返回的 `bar.pa_deg` / `lopsidedness.phase_deg` 在本契约下可直接使用。

## 单位契约（galfit 模式专用）
本模式所有 Re / 位置 / 尺寸**一律为像素（px）**：VLM 从对比图面板读出的 px 值与 feedme 参数行同单位同参考系，**原样写入、禁止任何换算**（expdisk 的 Rs 换算 Re=1.68·Rs 是唯一例外：VLM 给出的 Re 一律指有效半径，主模型写入 `4)` 行时按 Rs=Re/1.68 换算）。全流程禁止出现 arcsec，禁止调用任何单位转换工具。

## 添加约束
GALFIT 的参数约束文件（通常以 `.cons` 为后缀）是解决成分分配失衡和参数越界最核心的工具。

### 一、如何在 `feedme` 文件中启用约束

在你的主输入文件（`feedme`）开头部分，有一项专门用于指定约束文件：

```text
G) galaxy.cons      # Parameter constraint file (empty string)
```

将你的约束文件名（例如 `galaxy.cons`）填入 `G)` 项即可。如果不需要约束，留空或写 `none`。

---
### 二、`.cons` 文件的基本语法

约束文件的每一行代表一条规则。它的标准语法格式如下：
`[成分编号]   [参数名称]   [约束类型]   [下限]   [上限]`

#### 1. 常见参数名称缩写

在 `.cons` 文件中，参数必须使用特定的英文缩写：

* 位置坐标：`x`, `y` (通常写在一起 `x,y`) 约束也要同时约束（不可单独约束x或者y）
* 总星等：`mag`
* 有效半径：`re` (Sérsic) / `rs` (Exponential disk) / `fwhm` (Gaussian/Moffat)
* Sérsic 指数：`n`
* 轴比：`q` (b/a)
* 位置角：`pa`

```text
# Component/    parameter   constraint    Comment
# operation (see below)   range

  3_2_1_9        x          offset      # Hard constraint: Constrains the
  3_2_1_9        y          offset      # x,y parameter for components 3, 2,
                                        # 1, and 9 to have RELATIVE positions
                                        # defined by the initial parameter file.
  
  1_5_3_2       re          ratio       # Hard constraint: similar to above
                                        # except constrain the Re parameters 
                                        # by their ratio, as defined by the
                                        # initial parameter file.

    3           n           0.7 to 5    # Soft constraint: Constrains the 
                                        # sersic index n to within values 
                                        # from 0.7 to 5.

    2           x           -1  0.5     # Soft constraint: Constrains 
                                        # x-position of component
                                        # 2 to within +0.5 and -1 of the
                                        # >>INPUT<< value.

    3-7         mag         -0.5 3      # Soft constraint:  The magnitude 
                                        # of component 7 is constrained to 
                                        # be WITHIN a range -0.5 mag brighter 
                                        # than component 3, 3 magnitudes 
                                        # fainter.

    3/5         re          1  3        # Soft constraint:  Couples components 
                                        # 3 and 5 Re or Rs ratio to be greater 
                                        # than 1, but less than 3. 

# Note on parameter column:
#   The parameter name options are x, y, mag, re (or rs -- it doesn't matter),
#   n, alpha, beta, gamma, pa, q, c, f1a (Fourier amplitude), f1p (Fourier
#   phase angle), f2a, f2p, r5 (coordinate rotation), etc., .  Or 
#   alternatively, one can specify the parameter number instead (for the
#   classical parameters only) corresponding to the same numbers in the
#   galfit input file.
```


## 主星系同心约束（强制默认，非可选）——`.cons` 文件撰写规范

Disk、Bar、Bulge、Lens 四类主星系中心成分**必须同心**：只要 feedme 中存在 **≥ 2 个**主星系中心成分（`# STRUCTURE:` 名为 disk/bulge/bar/lens），就必须通过 `.cons` 约束文件把它们绑定到同一中心——这是默认硬约束，不是"需要时才做"的可选项。

### 一、唯一有效的语法：链式硬约束 `offset`（实测验证）

把锚点与所有从属中心成分的 GALFIT 编号用 `_` 连成链，写**成对**的两行（x 与 y 必须同时绑定，**严禁只绑一个**）。设锚点（Disk）编号为 D，从属成分编号为 K1、K2…：

```text
# 主星系同心约束（锚点=1 disk，从属=2 bulge, 3 bar, 4 lens）
D_K1_K2_K3   x   offset
D_K1_K2_K3   y   offset
```

具体示例（feedme 中 1=disk, 2=bulge, 3=bar, 4=lens, 5=companion, 6=sky）：

```text
# Concentric constraint: bulge/bar/lens anchored to disk (comp 1)
 1_2_3_4     x     offset
 1_2_3_4     y     offset
# Companion position pinned to initial estimate (soft ±5px window)
 5           x     122.5  123.5
 5           y     130.8  131.8
# (可选) 其他参数边界与同心约束合并写在同一文件，如 bulge n 范围
 2           n     0.5  8
```

**feedme 侧配套操作**：
- 锚点（Disk）的 `1)` 行 toggle **保持 `1 1` 自由**（推荐）——约束生效后**整组联动平移**，中心由拟合器共同优化；锚点也可固定 `0 0`（初值取父轮收敛中心），效果是全组钉死在该坐标。
- 从属成分的 `1)` 行 toggle 保持 `1 1`——GALFIT 加载约束后会自动将其改写为 `2 2`（受约束标记），无需手动改。
- feedme `G)` 项指向该约束文件（如 `G) iter3.cons`）；beam 轮次命名 `iter{n}.cons`，与 `_iter{n}.feedme` 同目录、同编号。**GALFIT 只加载一个约束文件**——其他边界（re/mag/n 范围、伴星系位置窗）必须合并写入同一个 `.cons`。

### 二、约束生效的验证标志（每轮必查）

- 拟合产出的 `galfit.NN` 中，受约束从属成分的 `1)` 行 toggle 显示 **`2 2`**（GALFIT 的受约束标记），且链内全部成分的 x,y 数值**完全一致**。
- 若 toggle 仍为 `1 1` 或中心不一致 → 约束**没有生效**（多为写法错误被静默忽略），必须回查 `.cons` 语法，不得带病入账。

### 三、严禁使用两种实测无效的写法（静默失效，不报错）

以下两种"看似合理"的成对写法在本机 GALFIT 上**实测不生效**（GALFIT 不报解析错误，但成分中心各自漂移 ~0.5 px，约束完全被忽略；源目录 `gadotti-gt/Plate0270_MJD51909_Fiber095_r` 的历史 cons.con 即第一种写法，其 galfit.05 输出中心漂移即为实证）：

```text
# ✗ 无效写法一：空格分隔的成对软约束 —— 静默忽略，禁止使用
 1  2  x  0.0 0.0
 1  2  y  0.0 0.0
# ✗ 无效写法二：横线成对软约束 —— 对 x/y 同样静默忽略，禁止使用
 1-2  x  0.0 0.0
 1-2  y  0.0 0.0
```

唯一可靠的形式是上节的**链式硬约束 `offset`**。

### 四、伴星系豁免（强制）

伴星系（`# STRUCTURE:` 名含 comp/companion/secondary/satellite）的编号**严禁写入**同心约束链——伴星系中心必须保持自由拟合。新增伴星系时改用**软约束窗**钉住位置（±5px，初值即 VLM 量测的像素坐标）：`<编号>  x  <init-5>  <init+5>` 与 `<编号>  y  <init-5>  <init+5>`。锚点选取：优先 Disk；暂无 Disk 时取最亮的中心成分。


## Galfit 添加成分类型的规范 （必须严格遵守）
@src/prompts/component_specification_galfit.md


## Galfit 执行规范
- 执行 Galfit 优化，必须使用 galmcp 中的run_galfit工具， 不能直接使用用bash工具执行 galfit 命令行。因为 run_galfit 工具会自动处理一些后续的分析步骤（如残差图生成、参数解析等），直接调用 galfit 可能会导致后续流程无法



# Working Note 的撰写规范（Beam Search 多分支版）

- Working Note 是记录 beam search 全过程的核心文档，也是**唯一真源**：优先队列 Q 的内容、当前最优 s\*、拟合计数 n / stagnation / global_iter_id、状态账本（输入账本 + 结果账本 + 回滚边）全部以它为准，每次决策前先 Read。
- **结构必须严格按照 `workflow_galfit.md` §多分支 working_note 模板**（头部基本信息 + Beam 状态快照[覆写] + 状态账本[追加] + 分支小节[追加] + 失败归档 + 跨分支决策日志[追加]）。
- 头部【必填】：阶段一 VLM 形态判断（相当于 Round 0 原图成分预测，必须明确指出高概率存在的成分）、`detect_bar_lopsidedness` 的结论（bar/lop 检出与否、PA（N=+Y 契约）、b/a；仅作为初始猜测，实际以拟合效果为准。未检出的成分必须写成"未检出（零证据，非判定性）"）。
- 每个分支轮次小节【必填】：
  - 本轮动作（action_id 与 primitives 摘要）与所用的 `_iter{n}.feedme` / `iter{n}.cons`；
  - 拟合后成分类型与关键参数（位置 px、星等、尺寸、形状参数；expdisk 标 Rs 与有效半径 Re）、reduced_χ²（chisq1d_nu）/ BIC（bic1d）；
  - **VLM 物理性判定（Physicality Verdict：verdict / failed_checks 摘要，原样记录不得改写）**；
  - `generate_galfit_beam_actions` 返回的候选 action_id 列表与入队/截断情况；
  - 【必填】距离预期目标的偏差。
- 覆写优于追加：Beam 状态快照每次主循环迭代后覆写；分支小节与跨分支决策日志才追加。

# 最优轮次锁定的标准
- 成分条件：图像与残差观测得到疑似已经充分认证，存在的成分已经全部添加。
- 拟合条件：1D profile残差图（DATA-MODEL）已经没有明显的尖峰或者系统性的偏离。2D残差图已经没有明显的对称残差。
- 物理条件：最终拟合参数之间的关系符合物理意义。
- 参数条件：非必要的约束条件已经全部释放，必要的约束条件已经全部添加。
  - 多成分时:Disk 使用 expdisk, 单成分时：使用 Sersic 成分。
  - Bar 的 n 固定为 0.5
  - 所有中心星系成分的 x,y 位置约束为 offset，保证同心。
  - 其他参数如 Re, mag 等没有过多的约束，允许合理范围内的调整。
- 校验条件：最优轮次一定是经过 `generate_galfit_beam_actions` 分析的轮次（该轮 archives 目录下存在 `*_beam_actions_*.md` 候选产物，且 working_note 中记录了该轮的 Physicality Verdict）。疑似最优的轮次如果缺少 beam_actions 产物或 verdict 记录，需要补调一次 `generate_galfit_beam_actions`（以其 feedme / galfit.NN / 对比图为输入）生成，以辅助验证是否符合最优条件。
- 指标条件：以上成分、拟合、物理、尝试、校验五个维度的条件都满足的情况下，基于残差质量选择
  - `generate_galfit_beam_actions` 每次调用都会输出 Physicality Verdict（残差视觉判断）与候选清单，配合 `run_galfit` 返回的 χ²/BIC 统计，是残差质量的重要参考（模型比较所用 BIC 一律为 **BIC_eff** = χ²/A_psf + k·ln(N/A_psf)，见 summary 统计表；1D BIC 仅作参考）
  - 两个轮次的差异仅在 F1时，F1 成分的 amplitude 大于 阈值 0.02 就可以保留,选择包含 F1 成分的轮次。

### 落锁强制审计（enforcement）
上述六维标准在执行中容易被遗漏，因此**正式锁定最优轮次之前，必须调用 subagent `best-round-verifier`**（定义见 `.claude/agents/best-round-verifier.md`）对候选轮做独立、机械、可追溯的校验：
- 该 subagent 为**只读审计**，按上述六个维度逐条核查并给出证据，返回 `verdict: PASS|FAIL`。
- `FAIL` → 严禁落锁，按其"阻断性问题"清单修复后重拟、复审至 `PASS`；`PASS`（含 WARN）方可落锁。
- 工作流（`workflow_galfit` / `workflow_galfits`）的阶段三锁定步骤已内嵌此审计门。