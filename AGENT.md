
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

* 位置坐标：`x`, `y` (通常写在一起 `x,y`)
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

表现： GALFIT 正常运行完毕，但输出的 galfit.01 文件中，参数旁边带有星号 * 或方括号 []（如 [20.000]*）。

深层原因： 在 .cons 文件中设置了不合理的硬边界，阻断了正常的梯度下降。或者在多组件参数绑定（例如强制 Bulge 和 Disk 中心坐标严格一致）时，由于实际物理中心存在微小偏移，导致模型强行扭曲其他参数来弥补。

### 如何设置合理的约束条件（最佳实践）

约束条件（Constraints）是防止算法“暴走”的安全网，但网织得太紧会勒死正常的优化过程。建议流水线采取以下策略：

- 设定符合物理意义的软性边界（Hard Bounds）：
在 .cons 约束文件中，为关键参数划定既安全又不至于太局促的绝对区间：
    - 中心坐标 (x,y)： 约束在初始值的 $\pm 2$ 到 $5$ 个像素内（如果是高度扰动的并合星系可放宽）。绝对不能让星系中心飘到图像边缘。
    - 有效半径 $R_e$： 最小值约束为 0.1 像素（或 PSF 的一半），最大值约束为图像边长的 1/2 或 1/3，防止模型在尝试拟合平坦背景时无限膨胀。
    - Sérsic 指数 $n$： 这是最容易暴走的参数。对于纯星系结构，物理上合理的 $n$ 值通常在 $0.2 \sim 8.0$ 之间。建议将其强制约束在 0.2 8.0（除非星系包含非常尖锐的无法分辨的 AGN 核心，才允许放宽到 15 或 20）。
    - 轴比 $b/a$： 约束在 0.05 1.0 之间，防止弱信噪比的盘成分被压成一条无物理意义的无限细线。

- 利用相对约束（Parameter Tying）稳定复杂模型： 当尝试分解高难度的 Bulge+Disk 甚至添加 Bar 时，参数极易发生简并。此时需要绑定参数：
    - 强绑定：通过在配置文件中将 Bulge 的 x, y 坐标变量与 Disk 强行链接（偏移量设为固定或完全一致），减少两个自由度，能极大提升收敛稳定性。
    - 相对约束：可以约束核球的尺寸始终小于盘（例如在 .cons 中限定 Bulge 的 $R_e$ 不能超过 Disk $R_e$ 的 80%）。

- “撞墙”后的动态降级策略（Fallback Strategy）： 流水线在解析 galfit.01 时，一旦检测到某参数带 * 号撞墙：
    - 如果是位置 $x, y$ 撞墙，说明初始猜得太偏，应以当前结果为新起点，放宽空间约束再跑一次。
    - 如果是某个组件的 $n$ 值撞到了下限（如 $0.1$）或亮度极暗，大概率说明数据根本不支持复杂的双组件拟合（发生过拟合）。此时流水线应自动“降级”，剔除冗余组件，退回单 Sérsic 模型重新拟合。


## 物理意义分析与策略

* 在拟合得到的结果中，如果目标源的一个成分给出的参数满足n<1 （n~0.5）同时q< 0.5，这个成分可能是个bar或者edge-on disk。如果这个源在这个成分之外存在一个re大于此成分的disk成分， 则可以把这个成分修改成bar 进行拟合（n固定成0.5的sersic model）；如果此成分是该星系唯一成分，则可以不做修改。
* 如果一个sersic  model拟合出来的结果 , re很小，远远小于1个pixel，意味着这个成分拟合的是一个点源，可以换成PSF model去拟合。
* 同一个源的两个成分bulge+disk拟合完，如果bulge和disk中心之间的距离大于disk成分本身的re，可能两个成分拟合到两个不同的源上了。后续调整可以考虑增加一个成分拟合伴源，同时通过constrain文件限制同一个源不同成分之间中心的距离。
* 对于盘星系而言，同一个源的两个成分bulge+disk拟合完，如果bulge的re 大于disk的re，意味着在拟合的过程中，bulge和disk的标签反了，可以交换这两个成分的标签。如果是3个成分拟合同一个源，通常情况存re_disk>re_bar>re_bulge,可以以此逻辑更新成分的标签。

## 奥卡姆剃刀原则

如果出现以下直观现象或者 BIC 统计现象，需要使用奥卡姆剃刀原则，剔除不必要的成分

1. 第一优先级：剔除用于“打补丁”的成分（优先优化数据本身）
    1. 特征： 有些成分常常被用来填补外围大尺度上的系统性亮斑/暗斑，或者中心极小范围内的同心圆残差。
    2. 处理方法： 删掉它们。这些残差通常是因为天空背景（Sky background）没有扣平，或者点源扩散函数（PSF）不匹配造成的。正确的做法是去修正背景平面拟合或重新提取 PSF，而不是用一个毫无物理意义的极宽或极窄的 Sersic 成分去“背锅”。
2. 第二优先级：替换用错轮廓的“假 Sersic”成分特征： 
    1. 如果一个新增加的 Sersic 成分，其拟合出的 $ R_e $ 极小（小于 1 个像素），且 $ n $ 值极高，它其实是在试图拟合一个未解析的点源（如 AGN 或致密核星团）。
    2. 处理方法： 删掉这个高度非线性的 Sersic 成分，直接用一个纯粹的 PSF 成分（Point Source Function）来代替。这能大大减少参数空间的复杂度和自由度。
3. 第三优先级：合并高度简并的“冗余主成分”特征： 
    1. 如果你拟合了三个 Sersic 成分（例如想做一个 核球+内盘+外盘 模型），但发现其中两个的椭率、位置角非常接近，且有效半径有很大的重叠。
    2. 处理方法： 果断将这两个相似的成分合并为一个。如果合并后残差图出现了前面提到的“等相面扭曲（四极矩）”特征，更优雅的做法是保留单个成分，但开启傅里叶模式或径向参数梯度拟合，而不是硬塞两个独立的 Sersic 进去。
4. 第四优先级：剔除光度贡献微乎其微的“幽灵成分”
    1. 特征： 某个成分的整体光度仅占星系总光度的 1% 甚至更低，且它在残差图上并没有对应非常明确、尖锐的物理结构。

## 常用成分类型及其关键参数

在深入各个模型之前，以下参数的获取方式通常是通用的，

- x 和 y（中心位置）：直接读取图像上该成分的亮度峰值像素坐标。如果多个成分同心（如核球+盘），它们的初始 x、y 应该设为相近。
- mag（积分星等）：如果是多成分拟合，可以将总星等按经验比例分配（例如核球比盘暗 1-2 个星等）。
- b/a（轴比）：视觉估算。正圆为 1，越扁越接近 0。
- PA（位置角）：长轴相对于 y 轴（通常是正北）逆时针旋转的角度。初始值通过原图中预估
- 在多组件拟合（例如拟合双 Sérsic 或 Bulge+Disk）时，如果不小心给两个组件赋予了完全相同的初始 $(x,y)$ 坐标、$R_e$ 和亮度，雅可比矩阵中的两列会变得完全线性相关。这在数学上导致矩阵无法求逆，算法无法计算下一步的步长。如果多个组件共处同一位置，必须在生成 参数 时引入微小扰动
- 多组件拟合的光度与尺寸拆分策略： 如果你准备拟合核球+盘 (Bulge+Disk) 的双 Sérsic 模型，绝对不能把 SExtractor 测出的总星等和总半径原封不动地同时赋予两个组件。经验法则：
    - 通量分配： 将测得的总通量按 $3:7$ 或 $4:6$ 的比例拆分，分别转换为星等赋值给核球和盘。
    - 尺寸分配： 核球的初始 $R_e$ 通常设为测光总半径的 $1/5$ 到 $1/3$；盘的 $R_e$ 则设为测光总半径的 $1 \sim 1.5$ 倍。
    - 形态分配： 核球初始给定 $n=4.0$（接近德沃库勒尔定律），盘初始给定 $n=1.0$（指数盘）。
---

1. sersic — 常用于 BULGE

0) sersic                 #  Component type
1) <x>  <y>  0 0          #  Position x, y
2) <mag>       1          #  Integrated magnitude
3) <R_e>       1          #  R_e (effective radius) [pix]
4) <n>         1          #  Sersic index n (de Vaucouleurs n=4)
5) 0.0000      0          #  -----
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) <b/a>       1          #  Axis ratio (b/a)
9) <PA>        1          #  Position angle (PA) [deg]
Z) 0                      #  Skip this model? (yes=1, no=0)

关键参数：R_e（有效半径）、n（Sérsic 指数）
- R_e（有效半径/半光半径）：大概是核球主体光晕的半径大小。
- n（Sersic 指数）：控制光度分布的核心浓度和边缘衰减速度。如果是经典核球 (Classical Bulge) 或 椭圆星系，初始化为 4（即 de Vaucouleurs 轮廓）。如果是伪核球 (Pseudobulge) 或盘，初始化为 1（即指数轮廓）。如果不确定，可以折中初始化为 2 或 2.5 让其自由拟合。
---
1. expdisk  — 常用于 DISK 专为指数衰减的星系盘设计（等效于 n=1 的 Sersic 模型）。

0) expdisk                #  Component type
1) <x>  <y>  0 0          #  Position x, y
2) <mag>       1          #  Integrated magnitude
3) <R_s>       1          #  R_s (disk scale-length) [pix]
4) 0.0000      0          #  -----
5) 0.0000      0          #  -----
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) <b/a>       0          #  Axis ratio (b/a)
9) <PA>        0          #  Position angle (PA) [deg: Up=0, Left=90]
Z) 0                      #  Skip this model?

关键参数：R_s（盘标长）
- R_s（盘标长 Scale-length）：表面亮度下降 $e$ 倍（约 2.718 倍）的距离。它与有效半径 $R_e$ 的数学关系为：$R_e \approx 1.678 R_s$。初始化方法：如果你知道盘的半光半径（通过测光或肉眼估计盘的范围），除以 1.678 即可作为 R_s 的初始值。肉眼看的话，大概是盘的整体可见半径的 1/3 到 1/4 左右。

---
1. ferrer  — 常用于 DISK（截断盘 / 棒 Bar / 透镜）Ferrer 轮廓的特点是中心比较平缓，而在外部有一个清晰的截断边界（Sharp drop-off），非常适合拟合星系棒 (Bar)。

0) ferrer                 #  Component type
1) <x>  <y>  0 0          #  Position x, y
2) <mu>        1          #  Surface brightness at FWHM [mag/arcsec^2]
3) <R_out>     1          #  Outer truncation radius [pix]
4) <alpha>     0          #  Alpha (outer truncation sharpness)
5) <beta>      0          #  Beta (central slope)
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) <b/a>       1          #  Axis ratio (b/a)
9) <PA>        1          #  Position angle (PA) [deg: Up=0, Left=90]
Z) 0                      #  Skip this model?

关键参数：R_out（外截断半径）、alpha（截断锐度）、beta（中心斜率）
- mu（中心/半高宽处表面亮度）：通常用中心最亮处的表面亮度估计值（mag/arcsec^2）。
- R_out（外截断半径）：肉眼观察棒（Bar）或截断盘的边缘，测量从中心到明显截断处的像素距离。
- alpha（截断锐度）：控制边缘截断有多“陡峭”。通常初始化为 2 或 3。
- beta（中心斜率）：控制中心的平坦程度。如果是典型的星系棒，中心较平，通常初始化为 0（完全平坦）或 0.5。

---
1. edgedisk — 常用于沿着视线方向几乎垂直观察的薄盘（$Z$ 轴方向的亮度分布）。

0) edgedisk               #  Component type
1) <x>  <y>  0 0          #  Position x, y
2) <mu0>       1          #  Mu(0) [mag/arcsec^2]
3) <h_s>       1          #  h_s (disk scale-height) [pix]
4) <R_s>       1          #  R_s (disk scale-length) [pix]
5) 0.0000      0          #  -----
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) 1.0000      -1         #  (固定 b/a=1)
9) <PA>        0          #  Position angle (PA) [deg: Up=0, Left=90]
Z) 0                      #  Skip this model?

关键参数：h_s（标高）、R_s（标长）。注意 b/a 固定为 1。
- mu0（中心表面亮度）：同上，盘中心的表面亮度。
- h_s（标高 Scale-height）：代表盘的厚度。初始化方法：在 DS9 中测量侧向盘在垂直方向的可见厚度，取其 1/3 或 1/4 作为初始值。通常是一个很小的值（例如 2~10 像素，取决于图像分辨率）。
- R_s（标长 Scale-length）：代表盘的水平延伸。在长轴方向测量可见长度，除以 3 或 4 作为初始值。
注意：此时 b/a 被强制固定为 1，因为软件已经通过 h_s 和 R_s 的比例接管了形状的计算，不需要再额外定义轴比。
---
1. psf — (常用于 活动星系核 AGN / 恒星 / 极其致密的核)

0) psf                    #  Component type
1) <x>  <y>  0 0          #  Position x, y
2) <mag>       1          #  Integrated magnitude
3) 0.0000      0          #  -----
4) 0.0000      0          #  -----
5) 0.0000      0          #  -----
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) 1.0000      -1         #  (固定 b/a=1)
9) 0.0000      -1         #  (固定 PA=0)
Z) 0                      #  Skip this model?

关键参数：仅 x, y 位置和积分星等，形状参数全部固定。
- x, y（中心位置）：必须极其精确。通常直接锁定图像中最亮的一个像素位置。
- mag（星等）：如果中心有明显的致密亮核（如 AGN），估算这个点源的星等。可以尝试用较小孔径测光的结果作为初始值。
注意：PSF 无法拟合形状，因此 R_e、n、b/a、PA 等参数对其全部失效（全为固定状态）。

## Galfit 添加成分类型的规范 （必须严格遵守）

- 要增加成分BULGE： Component type选用 sersic.
- 要增加沿着视线方向几乎垂直观察的薄盘：Component type选用 edgedisk。
- 要增加活动星系核 AGN / 恒星 / 极其致密的核：Component type选用psf 
- 要增加截断盘 / 棒 Bar / 透镜）：Component type选用ferrer (等效于 n~0.5 的 Sersic 模型，且有外截断特征）。)
- 要增加指数衰减的星系盘disk: Component type选用expdisk（等效于 n=1 的 Sersic 模型）。
- 对于Bar而言， PA 需要从图中观察的位置角，提供一个相对可靠的初值；否则galfit很难收敛；