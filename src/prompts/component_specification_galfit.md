
# 成分添加规范

## Galfit 添加成分类型的规范 （必须严格遵守）

- 要增加成分BULGE： Component type选用 sersic.
- 要增加edge-on的星系盘：Component type选用 edgedisk。
- 当添加的 Bulge的 Re 小于 0.2 pixel(大于0.2px 是可以接受状态), 需要更换类型， 采用Component type为psf
- 要增加棒 Bar：Component type选用  n~0.5 的 Sersic 模型.
- 要增加指数衰减的星系盘disk: 选择 n=1 的 Sersic 模型。
- 如果星系已经有一个 Disk 成分了，针对星系外围（Outskirt）未拟合上的情况，可以添加第二个 Disk 成分或 Sérsic 成分，以捕捉更延展的结构，
- 星系是 Face-on、Edge-on需要需要先区分, 有助于提升 Disk的选择和初值设置的准确性；一个Disk的（b/a）小于 0.5，可以认定为Edge-on；

## 成分初始参数的设置参考

在深入各个模型之前，以下参数的获取方式通常是通用的，
- x 和 y（中心位置）：直接读取图像上该成分的亮度峰值像素坐标。如果多个成分同心（如核球+盘），它们的初始 x、y 应该设为相同。
- mag（积分星等）：如果是多成分拟合，初值设定方法。需要考虑 mag 的值，需要基于原来的 sersic 星等下调整；避免初始值差异过大导致拟合失败
  - a. 建议将成分间的通量差异分为“Comparable（相当）”
  - b. “Faint（较暗，约 1/3）”
  - c. “Much Fainter（暗很多，差 1-1.5 个星等）”三个等级
- b/a（轴比）：视觉估算。正圆为 1，越扁越接近 0。
- PA（位置角）：长轴相对于 y 轴（通常是正北）逆时针旋转的角度。初始值通过原图中预估, 特别是 Bar,该初值非常重要。
- 将单成分拆分为双成分拟合时： 例如准备用核球+盘 (Bulge+Disk) 的替代原来单 Sérsic 是：
    - 通量分配： 将测得的总通量按 $3:7$ 或 $4:6$ 的比例拆分，分别转换为星等赋值给核球和盘。
    - 尺寸分配： 核球的初始 $R_e$ 通常设为测光总半径的 $1/5$ 到 $1/3$；盘的初值 $R_e$ 要求大于单Sérsic的 Re （具体值需要考虑 1D Surface profile 残差曲线中部与后部的表现，要求Disk能够承接这个区域的光通量）。
    - 形态分配： 核球初始给定 n=4，盘初始n=1。
- 如果再残差图和原图上能够看到 Bar的特征，则可以考虑添加 Bar 成分，Bar 的初始参数设置为： n 固定为 0.5，轴比 b/a 初值设定在 0.2 - 0.4 之间，位置角 PA 根据图像中 Bar 的长轴方向测量后初始化，尺寸参数 R_e 则设定在核球和盘之间。Disk 与 Re的初值也进行对应调整，使得总体合理。
**要求基于上一轮的拟合结果副本的基础上预估与修改，起到逐渐改善效果的目的，不要每次都从头开始**

## 成分参数定义
1. sersic — 常用于 BULGE / Bar / Disk

0) sersic                 #  Component type
1) <x>  <y>  1 1          #  Position x, y
3) <mag>       1          #  Integrated magnitude
4) <R_e>       1          #  R_e (effective radius) [pix]
5) <n>         1          #  Sersic index n (de Vaucouleurs n=4)
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) 0.0000      0          #  -----
9) <b/a>       1          #  Axis ratio (b/a)
10) <PA>       1          #  Position angle (PA) [deg]
Z) 0                      #  Skip this model? (yes=1, no=0)

---
1. edgedisk — 常用于沿着视线方向几乎垂直观察的薄盘（$Z$ 轴方向的亮度分布）。

0) edgedisk               #  Component type
1) <x>  <y>  1 1          #  Position x, y
3) <mu0>       1          #  Mu(0) [mag/arcsec^2]
4) <h_s>       1          #  h_s (disk scale-height) [pix]
5) <R_s>       1          #  R_s (disk scale-length) [pix]
10) <PA>       1          #  Position angle (PA) [deg: Up=0, Left=90]
Z) 0                      #  Skip this model?

关键参数：h_s（标高）、R_s（标长）。
- mu0（中心表面亮度）：同上，盘中心的表面亮度。
- h_s（标高 Scale-height）：代表盘的厚度。初始化方法：在图中观察侧向盘在垂直方向的可见厚度，取其 1/3 或 1/4 作为初始值。
- R_s（标长 Scale-length）：代表盘的水平延伸。在长轴方向测量可见长度，除以 3 或 4 作为初始值。

---
1. psf — (常用于 活动星系核 AGN / 恒星 / 极其致密的核)

0) psf                    #  Component type
1) <x>  <y>  1 1          #  Position x, y
3) <mag>       1          #  Integrated magnitude
Z) 0                      #  Skip this model?

关键参数：仅 x, y 位置和积分星等，形状参数全部固定。
- x, y（中心位置）：必须极其精确。通常直接锁定图像中最亮的一个像素位置。
- mag（星等）：如果中心有明显的致密亮核（如 AGN），估算这个点源的星等。可以尝试用较小孔径测光的结果作为初始值。


## 成分的高阶参数，需要拟合高阶成分特征时使用。
The parameters C0, B1, B2, F1, F2, etc. listed below are hidden from the user unless he/she explicitly requests them.  These can be tagged on to the end of any previous components except, of course, the PSF and the sky -- If a Fourier or Bending amplitude is set to 0 initially GALFIT will reset it  to a value of 0.01. To prevent GALFIT from doing so, one can set it to any other value.

- Bending modes
B1)  0.07      1       # Bending mode 1 (shear)
B2)  0.01      1       # Bending mode 2 (banana shape)
B3)  0.03      1       # Bending mode 3 (S-shape)

- Azimuthal fourier modes
F1)  0.07  30.1  1  1  # Az. Fourier mode 1, amplitude and phase angle

- Traditional Diskyness/Boxyness parameter c
C0) 0.1         0      # traditional diskyness(-)/boxyness(+)


## 如何设置合理的约束条件（最佳实践）

约束条件（Constraints）是防止算法“暴走”的安全网，但网织得太紧会勒死正常的优化过程。建议流水线采取以下策略：

- 设定符合物理意义的软性边界（Hard Bounds）：
在 .cons 约束文件中，为关键参数划定既安全又不至于太局促的绝对区间：
    - 中心坐标 (x,y)： 约束在初始值的 $\pm 2$ 到 $5$ 个像素内（如果是高度扰动的并合星系可放宽）。绝对不能让星系中心飘到图像边缘。
    - 有效半径 $R_e$： 最小值约束为 0.1 像素（或 PSF 的一半），最大值约束为图像边长的 1/2 或 1/3，防止模型在尝试拟合平坦背景时无限膨胀。
    - Sérsic 指数 $n$： 这是最容易暴走的参数。对于纯星系结构，物理上合理的 $n$ 值通常在 $0.1 \sim 8.0$ 之间。建议将其强制约束在 0.1 8.0（除非星系包含非常尖锐的无法分辨的 AGN 核心，才允许放宽到 15 或 20）。
    - 轴比 $b/a$： 约束在 0.05 1.0 之间，防止弱信噪比的盘成分被压成一条无物理意义的无限细线。

