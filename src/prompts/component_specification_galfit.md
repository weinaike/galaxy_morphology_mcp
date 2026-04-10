
# 成分添加规范

## Galfit 添加成分类型的规范 （必须严格遵守）

- 要增加成分BULGE： Component type选用 sersic.
- 要增加沿着视线方向几乎垂直观察的薄盘：Component type选用 edgedisk。
- 要增加活动星系核 AGN / 恒星 / 极其致密的核：Component type选用psf 
- 要增加截断盘 / 棒 Bar / 透镜）：Component type选用ferrer (等效于 n~0.5 的 Sersic 模型，且有外截断特征）。)
- 要增加指数衰减的星系盘disk: Component type选用expdisk（等效于 n=1 的 Sersic 模型）。
- 对于Bar而言， PA 需要从图中观察的位置角，提供一个相对可靠的初值；否则galfit很难收敛；
- 新增成分时，要考虑数据分辨率不足或信噪比是否足够，低分辨率或低信噪比无法支撑复杂模型（3个成分及以上）。

## 各成分的具体参数设置

在深入各个模型之前，以下参数的获取方式通常是通用的，
- x 和 y（中心位置）：直接读取图像上该成分的亮度峰值像素坐标。如果多个成分同心（如核球+盘），它们的初始 x、y 应该设为相同。
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

