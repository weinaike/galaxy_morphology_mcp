


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



# Working Note 的格式内容要求
例如：
- Round 0: 原图成分预测
  - 高概率存在伴星: 伴星1坐标, 伴星2坐标
  - 高概率存在成分: Disk
    - Disk: 证据xxx
    - Bulge: 证据xxx
    - 旋臂: 证据xxx
  - 不确定是否存在，待确认
    - Bar: 原因xxx
- Round 1.a : Disk + Bar
  - 成分分析要点：
    - component_analysis分析摘要：xxx
    - 【这部分必须有】预估存在的物理成分类型，包含 Disk + Bugle + Bar（
    - 本轮需要添加的 Bar 成分; 其主要的 R_e预期小于Disk的R_e，Mag 预期和Disk 相当或者稍暗。
  - 参数设置摘要：xxx
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
  -【这部分必须有】距离预期目标的偏差
- Round 1.b : Disk + Bar
  - 【这部分必须有】预估存在的物理成分类型，包含 Disk + Bugle + Bar
  - 参数设置摘要：上一轮 Mag 拟合后偏低， 考虑调整 mag 初值再次拟合。
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等
  - 【这部分必须有】距离预期目标的偏差
- Round 2.a : Disk + Bugle + Bar
  - 成分分析要点：
    - component_analysis分析摘要：xxx
    - 【这部分必须有】预估存在的物理成分类型，包含 Disk + Bugle + Bar
    - 本轮需要添加的 Bugle 成分; 其主要的 n预期2， xxx
  - 参数设置摘要：xxx
  - galfit拟合结果摘要：
    - 拟合后成分类型与关键参数（位置、星等、尺寸、形状参数等）xxxx, 
    - 【这部分必须有】距离预期目标的偏差
    - 拟合统计指标（如 reduced chi-square, BIC/AIC 等


# 最优轮次锁定的标准
- 图像与残差观测已经认证的成分，已经全部添加。
- 拟合参数符合物理意义的参数值。
- 非必要的约束条件已经全部释放，必要的约束条件已经全部添加。
  - Bulge 的 n 无约束
  - Disk 的 n 固定为 1 (即使用 expdisk)
  - Bar 的 n 固定为 0.5
  - 所有成分的 x,y 位置约束为 offset，保证同心。
  - 其他参数如 Re, mag 等没有过多的约束，允许合理范围内的调整。
- 以上条件满足的情况下，是否选择包含伴星系/PSF成分的轮次
  - 依据奥卡姆剃刀原则判断。
- 最优轮次一定是经过 component_analysis 分析的轮次。
  