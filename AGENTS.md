


# 星系成分分析的方法指南
@src/prompts/residual_analysis_message.md
---

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


## Galfit 添加成分类型的规范 （必须严格遵守）
@src/prompts/component_specification_galfit.md


## Galfit 执行规范
- 执行 Galfit 优化，必须使用 galmcp 中的run_galfit工具， 不能直接使用用bash工具执行 galfit 命令行。因为 run_galfit 工具会自动处理一些后续的分析步骤（如残差图生成、参数解析等），直接调用 galfit 可能会导致后续流程无法



# Working Note 的撰写规范

- Working Note 是记录每轮拟合分析过程的核心文档，必须详细记录每轮的分析要点、参数设置、拟合结果以及距离预期目标的偏差等关键信息。 example示例中提到的 【必填】 的内容是必须包含的关键信息。
- Working Note 中必须包含<Round 0: 原图成分预测>，必须明确指出<高概率存在的成分>。数据来源于 detect_bar_lopsidedness 的结论优先级更高。（仅作为初始猜测，实际以拟合效果为准）
- 严格按照以下示例的格式撰写 Working Note，确保信息的完整性和清晰度。

<example>
## Working Note
- Round 0 (20260624T011517.77c05864): 原图成分预测
  - 总体判断：这是 xxx星系，特征是xxx
  - 【必填】detect_bar_lopsidedness 工具的结论：xxx
  - 【必填】综合分析高概率存在成分:
    - Disk: 证据xxx, (需要描述是否包含 lopsidedness 及其关键参数)
    - Bulge: 证据xxx
    - 旋臂: 证据xxx
    - 伴星1：坐标
    - 伴星2：坐标
  - 不确定是否存在，
    - Bar
- Round 1.a (20260624T012258.77c05864) : Disk + Bar
  - 成分分析要点：
    - 【必填】预估存在的物理成分类型，包含 Disk + Bugle + Bar  
    - component_analysis分析摘要：xxx
    - component_analysis 最优轮结论：20260624T012258.77c05864
    - 本轮需要添加的 Bar 成分; 其主要的 R_e预期小于Disk的R_e，Mag 预期和Disk 相当或者稍暗。
  - 参数设置摘要：xxx
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
  -【必填】距离预期目标的偏差
- Round 1.b (20260624T013233.77c05864): Disk + Bar
  - 【必填】预估存在的物理成分类型，包含 Disk + Bugle + Bar
  - 参数设置摘要：上一轮 Mag 拟合后偏低， 考虑调整 mag 初值再次拟合。
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
  - 【必填】距离预期目标的偏差
- Round 2.a (20260624T013908.77c05864): Disk + Bugle + Bar
  - 成分分析要点：
    - 【必填】预估存在的物理成分类型，包含 Disk + Bugle + Bar
    - component_analysis分析摘要：xxx
    - component_analysis 最优轮结论：20260624T012258.77c05864
    - 本轮需要添加的 Bugle 成分; 其主要的 n预期2， xxx
  - 参数设置摘要：xxx
  - galfit拟合结果摘要：
    - 【必填】距离预期目标的偏差
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
</example>

# 最优轮次锁定的标准
- 成分条件：图像与残差观测得到疑似已经充分认证，存在的成分已经全部添加。
- 拟合条件：1D profile残差图（DATA-MODEL）已经没有明显的尖峰或者系统性的偏离。2D残差图已经没有明显的对称残差。
- 物理条件：最终拟合参数之间的关系符合物理意义。
- 参数条件：非必要的约束条件已经全部释放，必要的约束条件已经全部添加。
  - 多成分时:Disk 使用 expdisk, 单成分时：使用 Sersic 成分。
  - Bar 的 n 固定为 0.5
  - 所有中心星系成分的 x,y 位置约束为 offset，保证同心。
  - 其他参数如 Re, mag 等没有过多的约束，允许合理范围内的调整。
- 校验条件：最优轮次一定是经过 component_analysis 分析的轮次。疑似最优的轮次如果不包含component_analysis的输出文件， 需要重新调用 component_analysis 生成。以辅助验证是否符合最优条件。
- 指标条件：以上成分、拟合、物理、尝试、校验五个维度的条件都满足的情况下，基于残差质量选择
  - component_analysis 分析工具每次调用都会输出最优轮次（残差视觉判断）以及卡方值信息，他们是残差质量的重要参考
  - 两个轮次的差异仅在 F1时，F1 成分的 amplitude 大于 阈值 0.02 就可以保留,选择包含 F1 成分的轮次。

### 落锁强制审计（enforcement）
上述六维标准在执行中容易被遗漏，因此**正式锁定最优轮次之前，必须调用 subagent `best-round-verifier`**（定义见 `.claude/agents/best-round-verifier.md`）对候选轮做独立、机械、可追溯的校验：
- 该 subagent 为**只读审计**，按上述六个维度逐条核查并给出证据，返回 `verdict: PASS|FAIL`。
- `FAIL` → 严禁落锁，按其"阻断性问题"清单修复后重拟、复审至 `PASS`；`PASS`（含 WARN）方可落锁。
- 工作流（`workflow_galfit` / `workflow_galfits`）的阶段三锁定步骤已内嵌此审计门。