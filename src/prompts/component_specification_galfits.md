# 成分添加规范（GalfitS 多波段）

> 参数格式的完整定义（Pa1-Pa32 各字段含义、N 块结构、Galaxy/Atlas 组织、Phase 1/2/3 标志等）请通过 `/skill galfits-manual` 查询。本文件只规定**本项目特有的策略**：成分白名单、多波段判据、初值选取规则、迭代锁定规则、约束最佳实践。

## 成分类型的规范（必须严格遵守）

- **Disk（盘）**：Profile type 选用 `sersic`，Sérsic 指数 n ≈ 1（当确认了disk成分后，后续的拟合过程中n必须固定为1）。
- **Bulge（核球）**：Profile type 选用 `sersic`，Sérsic 指数 n ≈ 4（范围 0.1–8，不用固定）。
- **Edge-on Disk（侧视盘）**：Profile type 选用 `edgeondisk`。
- **Bar（棒）**：Profile type 选用 `sersic`，Sérsic 指数 n = 0.5。
- **AGN / 致密核**：Profile type 选用 N 块。**每个波段各自用 WCS 把 Re 转成 px 后必须全部 < 0.2 px** 才能替换为 PSF/AGN；任意一个波段 Re ≥ 0.2 px 则保持 Sersic（不要因 Re 触到 lyric 下界就切换，应放宽下界重新拟合）。
- **偏心 / Lopsidedness**：将 Disk 的 profile 从 `sersic` 改为 `sersic_f`，启用 m=1 模式（详见下文）。
- 如果星系已有一个 Disk 成分，而外围（outskirt）残差仍有系统性正残差，可添加第二个 Disk（sersic, n < 1, Re 较大），以捕捉延展结构。
- **仅关注盘、核球、侧视盘、棒、AGN 核、偏心（Disk 上的 m=1 Fourier 模式）这六种物理成分**，其他残差特征可以选择保留不拟合。

## 多波段融合判据（Bar）

GalfitS 多波段图像和残差图中各波段并排展示时，Bar 的判别遵循 **跨波段 OR-logic**：

- **逐波段独立判别**。只要**任意一个波段**的图像或残差上能识别出 Bar 特征（"一字型"/"花生型"亮区、内层等照度线明显比外层更扁等），即认定 Bar 存在，需添加 Bar 成分。
- 即使其他波段看不到，也不据此否定（不同波段 PSF FWHM、波长覆盖、SNR 不同）。
- 只有**所有波段都看不到** Bar 特征时，才能下"无 Bar"的结论。

注意：蓝端波段（如 F115W）PSF 更锐利，对中心结构更敏感，是 Bar 判别的主要依据。

## 成分初始参数策略

GalfitS 的参数格式为 `[initial_value, min, max, step, vary]`，其中 `vary=1` 为自由参数，`vary=0` 为固定参数。

### 通用参数获取方式

- **Pa3 / Pa4（中心位置 x, y）**：单位为 arcsec（相对于 region center）。先读取图像上该成分的亮度峰值像素坐标，再转换为相对于 region center 的 arcsec 偏移量。多成分同心时（如 Bulge+Disk），应将它们的 Pa3 和 Pa4 的初始值设为相同，保证同心。
- **Pa5（Re，单位 arcsec）**：初始化时先用像素估算，再乘以该波段的 pixel scale（arcsec/pixel）转换为 arcsec。
- **Pa8（轴比 b/a）**：视觉估算。正圆为 1，越扁越接近 0。
- **Pa7（位置角 PA）**：长轴相对于正北方向逆时针旋转的角度。Bar 的 PA 初值非常关键，务必从原图中仔细测量。

### 通量分配原则

添加多成分时，需要基于已有成分的星等进行合理拆分：
- **Comparable（相当）**：两个成分亮度接近
- **Faint（较暗，约 1/3）**
- **Much Fainter（暗很多，差 1–1.5 个星等）**

### 单成分拆分为双成分的参考

以 Bulge+Disk 替代单 Sérsic 为例：
- **通量分配**：将总通量按 3:7 或 4:6 比例拆分，分别转换为星等赋值给核球和盘。
- **尺寸分配**：
  - 核球的初始 Re 通常设为测光总半径的 1/5 到 1/3
  - 盘的初始 Re 要求大于单 Sersic 的 Re（需参考 1D Surface Brightness Profile 残差曲线中部与后部的表现，确保 Disk 能承接该区域的通量）

### Bar 的初始参数设置

如果在残差图和原图上能识别 Bar 特征：
- n 固定为 0.5
- 轴比 b/a 初值设定在 0.2–0.4 之间
- PA 根据图像中 Bar 的长轴方向测量后初始化
- Re 设定在核球和盘之间
- mag 参考通量分配原则
- 添加 Bar 的同时，Disk 的 Re 初值也应做相应调整，使总体合理

**优先使用 `detect_galfits_bar_lopsidedness` 的客观测量值作为初值**：
- 工具返回的 `bar.pa_deg` → Pa7 初值（跨波段 OR-logic，PA 取蓝端波段优先）
- 工具返回的 `bar.b_over_a` = 1 − e_max → Pa8 初值
- 仅当工具未检出但视觉明显时才手工估。

### 偏心（Disk + Fourier m=1）的初始参数策略

启用条件（满足任一）：
- `detect_galfits_bar_lopsidedness` 跨波段 OR-logic 判定偏心存在；
- 阶段三 `fourier_mode_analysis`（输入 F200W 残差）推荐。

操作：将 Disk 的 `Pa2) sersic` 改为 `Pa2) sersic_f`，启用 m=1 模式。完整参数模板见 `/skill galfits-manual` → model-components/profile-fourier.md。本项目额外约束：

- **m=1（Pa21=1）只能作用于 Disk 成分**（或单 Sersic 模型，若无 Disk）
- 保留原 Disk 的 Pa3-Pa8 不变
- 振幅 am（Pa22）初值参考 detect 工具返回的 `lopsidedness.mag`（即 A1_mean）
- theta_m（Pa23）初值参考 detect 工具返回的 `lopsidedness.phase_deg`（即 phi1_mean）；与 Pa7 同帧（sky PA，正北 0°，逆时针）
- m=1 不需要旋臂卷绕，Pa20（theta_out）置 0
- Phase 1（仅 image）：Pa17-Pa24 vary=1，Pa9-Pa16 沿用 Phase 规则

### 伴星系的初始参数设置

当残差图中检测到明显的伴星系时，将其作为独立的 Galaxy（G 块）添加，并新建对应的 Profile（P 块）：

- **几何参数（Pa3–Pa8）**：x、y、Re、q（b/a）、PA 均依据 LLM 对原图/残差图的识别结果直接赋值，无需手动估算
- **Sérsic 指数 n**：伴星系形态较简单，n 可直接设为 2
- **物理参数（红移 z、消光 EB-V、SED 参数等）**：直接照抄主星系的默认值
- **质量（log_M 或相关参数）**：伴星系 mass 统一设为 9
- 伴星系需声明为独立的 G 块，不要合并到主星系的 Ga2 组件列表中

**要求基于上一轮的拟合结果副本的基础上预估与修改，起到逐渐改善效果的目的，不要每次都从头开始。**

## 迭代拟合中的参数锁定规则

在迭代拟合过程中（阶段二的步骤 1–2 循环），必须遵循以下参数锁定规则：

### 规则 1：成分确认后锁定特征参数

一旦通过成分分析确认了disk成分，**立即锁定disk的特征 Sérsic 指数 n（设 vary=0）**：

| 物理成分 | 锁定的 n 值 | 锁定时机 |
|---------|------------|---------|
| Disk | n = 1（设 vary=0） | 成分分析确认为 Disk 后立即锁定 |

**注意：如果确认了disk成分，必须要在后续步骤中拆分成disk+bulge拟合一下，一开始不用固定bulge的n值，再根据分析拟合结果决定是否有必要保留bulge。**

### 规则 2：异常参数的应急锁定

当拟合结果出现以下异常情况时，可以尝试将 n 固定到成分对应的经验值后重新拟合：

| 异常现象 | 诊断 | 处理方式 |
|---------|------|---------|
| n > 8 或 n < 0.2 | 参数发散，模型失去物理意义 | 按成分类型固定 n（Disk→1, Bulge→4, Bar→0.5），重新拟合 |
| Re 异常大（接近或超过拟合区域 1/2） | 参数逃逸，模型拟合背景而非星系 | 固定 n 到成分对应经验值（Disk→1, Bulge→4, Bar→0.5），同时约束 Re 上限 |
| Re 异常小（< 0.1 pixel） | 成分可能不可分辨 | 固定 n 到成分对应经验值；若仍不收敛，考虑替换为 AGN（N 块） |
| n 和 Re 同时异常 | 模型退化 | 回退到上一轮稳定结果，固定 n 到成分对应经验值后重新拟合 |

**注意：不同波段的pixscale不一致，所以不同波段Re对应的pixel值不一致，只要最大的Re值大于0.1就不算异常。**

### 规则 3：多成分拟合时的位置约束

当模型包含多个同心成分（如 Bulge+Disk）时：
- 所有成分的 Pa3（x）和 Pa4（y）初始值必须相同
- 如果拟合后中心位置偏移 > 2 pixel（需通过 WCS 换算为 arcsec 进行比较），应检查是否存在成分退化，必要时回退

## 约束条件最佳实践

约束条件是防止算法"暴走"的安全网，但网织得太紧会勒死正常的优化过程。

### 软性边界设置（通过参数的 min/max 控制）

在 `[value, min, max, step, vary]` 中，通过 min 和 max 划定安全区间：

- **中心坐标（Pa3, Pa4）**：约束在初始值附近约 ±0.1–0.3 arcsec 内。绝对不能让中心飘到图像边缘。
- **有效半径 Re（Pa5）**：最小值 0.01 arcsec（约 PSF 的一半），最大值根据拟合区域大小和 pixel scale 换算，防止无限膨胀。
- **轴比 b/a（Pa8）**：约束在 0.05–1.0，防止弱信噪比的盘被压成无物理意义的细线。

### 注意事项

- **优先调初始值**：当拟合异常时，调初始值的优先级高于增加约束
- 评估 Re 时务必通过 WCS 换算为 pixel 后再判断，不要直接用 arcsec 值与像素比较
