


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

* 位置坐标：`x`, `y` (通常写在一起 `x,y`) 约束也要同时约束
* 总星等：`mag`
* 有效半径：`re` (Sérsic) / `rs` (Exponential disk) / `fwhm` (Gaussian/Moffat)
* Sérsic 指数：`n`
* 轴比：`q` (b/a)
* 位置角：`pa`

```text
# Component/    parameter   constraint    Comment
# operation (see below)   range

  3_2_1_9        x          offset      # Hard constraint: Constrains the
                                        # x parameter for components 3, 2,
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

## 约束规范

约束条件（Constraints）是防止算法“暴走”的安全网，但网织得太紧会勒死正常的优化过程。建议流水线采取以下策略：

- 设定符合物理意义的软性边界（Hard Bounds）：
在 .cons 约束文件中，为关键参数划定既安全又不至于太局促的绝对区间：
    - 中心坐标 (x,y)： 约束在初始值的 $\pm 2$ 到 $5$ 个像素内（如果是高度扰动的并合星系可放宽）。绝对不能让星系中心飘到图像边缘。
    - 有效半径 $R_e$： 最小值约束为 0.1 像素（或 PSF 的一半），最大值约束为图像边长的 1/2 或 1/3，防止模型在尝试拟合平坦背景时无限膨胀。
    - Sérsic 指数 $n$： 这是最容易暴走的参数。对于纯星系结构，物理上合理的 $n$ 值通常在 $0.1 \sim 8.0$ 之间。建议将其强制约束在 0.1 8.0（除非星系包含非常尖锐的无法分辨的 AGN 核心，才允许放宽到 15 或 20）。
    - 轴比 $b/a$： 约束在 0.05 1.0 之间，防止弱信噪比的盘成分被压成一条无物理意义的无限细线。

- 利用相对约束（Parameter Tying）稳定复杂模型： 当尝试分解高难度的 Bulge+Disk 甚至添加 Bar 时，参数极易发生简并。此时需要绑定参数：
    - 强绑定：通过在配置文件中将 Bulge 的 x, y 坐标变量与 Disk 强行链接（偏移量设为固定或完全一致），减少两个自由度，能极大提升收敛稳定性。
    - 相对约束：可以约束核球的尺寸始终小于盘（例如在 .cons 中限定 Bulge 的 $R_e$ 不能超过 Disk $R_e$ 的 80%）。



## Galfit 添加成分类型的规范 （必须严格遵守）
@src/prompts/component_specification_galfit.md


## Galfit 执行规范
- 执行 Galfit 优化，必须使用 galmcp 中的run_galfit工具， 不能直接使用用bash工具执行 galfit 命令行。因为 run_galfit 工具会自动处理一些后续的分析步骤（如残差图生成、参数解析等），直接调用 galfit 可能会导致后续流程无法



# Working Note 的格式内容要求
例如：
- Round 1.a : Disk + Bar
  - 成分分析要点：
    - component_analysis分析摘要：xxx
    - 预估存在的物理成分类型，包含 Disk + Bugle + Bar
    - 本轮需要添加的 Bar 成分; 其主要的 R_e预期小于Disk的R_e，Mag 预期和Disk 相当或者稍暗。
  - 参数设置摘要：xxx
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
- Round 1.b : Disk + Bar
  - 参数设置摘要：上一轮 Mag 拟合后偏低， 考虑调整 mag 初值再次拟合。
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
- Round 2.a : Disk + Bugle + Bar
  - 成分分析要点：
    - component_analysis分析摘要：xxx
    - 预估存在的物理成分类型，包含 Disk + Bugle + Bar
    - 本轮需要添加的 Bugle 成分; 其主要的 n预期2， xxx
  - 参数设置摘要：xxx
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
