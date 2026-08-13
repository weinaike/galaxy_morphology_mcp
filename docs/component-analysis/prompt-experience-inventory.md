# 旧残差分析 prompt 经验盘点

> 目的：第 4 步（VLM 受控 JSON 改造）的第一个子任务。对旧 galmcp 残差分析 prompt 中沉淀的经验逐条标注去向，保证改造中经验不静默丢失，废弃条目有据可查。
> 范围：`src/prompts/residual_analysis_message.md`（下称 message）、`src/prompts/residual_analysis_prompt.md`（下称 prompt）。`component_specification_galfits.md` 的同步由 `docs/component-analysis/spec-sync.md` 单独覆盖，不在本文件范围内。
> 日期：2026-08-13

## 用户裁定（2026-08-13）

1. 非建模形态的识别描述（旋臂正负相间螺旋／串珠状结块、壳层同心弧、潮汐尾条带、混乱尘埃网络、水滴状偏心拉伸等）不迁移到新 VLM prompt。这些形态不进入拟合成分；受控标签枚举（`spiral_arm`、`dust_lane`、`tidal_feature`、`diffraction_psf` 等）保留，仅作混淆／否决标志，不附带识别指南。偏心（lopsidedness）的有无判定由数值层 m=1 检测主判（重构方案第六节第 6 小节），VLM 不参与。
2. 调参层与量化门槛类经验（sky fix、Bulge fix n=4→1→free、伴星系 20 px／5 星等影响范围门、初值分配等）本轮不处理，留在现有 prompt 中随现行 workflow 继续生效，不迁移进新三层架构。

## 去向标签

| 标签 | 含义 |
|---|---|
| RULES | 已落入 `src/component_analysis/rules.py` 或重构方案第六节 v1 判据 |
| NUMERIC | 已落入或规划落入 `src/component_analysis/numeric.py`（数值证据层） |
| VLM_LABEL | 仅以受控标签枚举形式保留（`vlm_evidence` schema），识别描述文本不迁移 |
| SPEC | 属规范文件内容，由 `docs/component-analysis/spec-sync.md` 同步矩阵覆盖 |
| STAY | 留在现有 prompt，不迁移（用户裁定：调参层本轮不管） |
| DROP | 有意废弃，附理由 |
| REPLACED | 被新判据替代，旧表述不再使用 |
| OPEN | 未裁定，规范同步前需确认 |

## 一、residual_analysis_message.md

| # | 条目 | 来源行 | 去向 | 说明 |
|---|---|---|---|---|
| M1 | 全局系统性异常（sky 未扣平、PSF 失配光晕、初值离谱） | 26–32 | STAY | 运行诊断与调参，不属成分增删 |
| M2 | 不主动修改 mask，影响大的源加成分吸收 | 32、39 | STAY | 现行 workflow 原则，保持 |
| M3 | 独立亮斑双重条件（残差亮斑＋原图对应亮源） | 66–69、107–108 | RULES | `_companion_rule` 的 `original_source_matches` 门＋数值层局部峰候选 |
| M4 | 旋臂识别描述（正负相间螺旋、串珠状结块、伴生尘埃带） | 55 | DROP | 用户裁定：旋臂不建模；`spiral_arm` 标签保留为 Bar 混淆否决项 |
| M5 | 尘埃带／坏像素→完善 mask | 57–59 | DROP | 描述文本废弃；`dust_lane` 标签保留（中心污染否决，见 `CENTRAL_MORPHOLOGY_CONFLICT_V1`） |
| M6 | 壳层同心弧、潮汐尾条带、混乱尘埃网络描述 | 60–63、126–128 | DROP | 用户裁定：非建模形态；`tidal_feature` 标签保留为混淆标志 |
| M7 | 偏心水滴状／鸡蛋形描述＋F1 优先添加 | 64–65、88–89、122–125、137 | REPLACED | 有无判定改由数值层 m=1 主判（原图 A1＋残差 m=1，NUMERIC）；「F1 只加在 Disk 上」已进 RULES（`_m1_rule` 适用条件）；形态描述废弃 |
| M8 | 伴星系与潮汐尾的区分要点 | 70 | VLM_LABEL | 归入 `independent_source` vs `tidal_feature` 标签区分，描述不迁移 |
| M9 | 成分添加次序 Disk→(F1/Companion)→Bulge→Bar | 72–75 | RULES | `decide_proposal` 的规则适用性排序体现同一优先级 |
| M10 | 先总体轮廓后中心细节 | 76–78 | RULES | 状态机「一次一动作＋逐轮迭代」承载同一意图 |
| M11 | 椭圆星系单 Sersic 三条件（残差消除＋中心<5px 正残差＋q>0.5） | 81–85 | REPLACED | 被 v1 Disk/Elliptical 判据替代（N1 必备＋N2/N3 组合，自由 n≥3 判光球） |
| M12 | Disk 认定（单 Sersic 残差畸变、分叉、拉长） | 86–87 | REPLACED | 被 N1/N2/N3 组合替代 |
| M13 | Bulge q<0.5 先换 Bar；Re 层级标签交换 | 94、159 | RULES/SPEC | 尺寸层级作标签／简并检查，非硬失败（已确认目标规则 6） |
| M14 | Bar 认定（一字型／X 型残差、原图长条、PA 预估） | 98–105 | RULES | `_bar_rule`：等照度强证据主判＋残差 m=2＋VLM `bar_like`/`peanut_x` 弱候选 |
| M15 | Lens 认定（Bar 的 Re/q 异常时拆 Bar+Lens） | 95–96、150–151 | RULES | 2026-08-13 裁定保留：Lens 升入白名单（第八类），判据迁移为 `_lens_rule`（Bar 参数异常＋延展区正残差触发，可选成分 BIC 门），接受条件 `Re_disk > Re_lens > Re_bar`、`n_lens < 0.5`、`q_lens > 0.5` |
| M16 | Nucleus 认定条件（1D 尖峰、Re<0.2px 候选） | 116–121 | REPLACED | 被可分辨尺度门替代（FWHM 平方差反卷积＋SNR 门）；Re<0.2px 降级为二级指纹 |
| M17 | Nucleus 代偿 Bulge、物理意义优先于奥卡姆 | 140–142 | DROP | 2026-08-13 裁定废弃：中心源统一走可分辨尺度门分流，无代偿逻辑；规范同步时从现有 prompt 移除 |
| M18 | 调参策略全节（初值继承、sky fix、n=4→1→free、Re 过小三尝试、mag 重分配） | 130–154 | STAY | 用户裁定本轮不管；属参数调整层，非成分动作 |
| M19 | 同心成分 x/y 同时绑定 | 152、162 | SPEC | 中心同心硬规则（已确认目标规则 2） |
| M20 | Disk n 可<1、Bulge n 范围 0.1–8 | 160–161 | SPEC | 物理参数范围，规范同步时并入 specification |
| M21 | m=1 amplitude>0.02 保留 | 163 | RULES | `RuleThresholds.m1_keep_amplitude = 0.02` |
| M22 | 成分简并须参数整体接近才可用（q 差异即物理不同） | 165–166 | NUMERIC/RULES | 重构方案第八节简并诊断：多维记录＋`degeneracy_warning`，删除仍须重拟合仲裁 |
| M23 | 奥卡姆作用域（只适用 Nucleus/AGN/Companion，禁删主成分） | 168–177 | RULES | 主成分／可选成分 BIC 作用域区分（`evaluate_refit`） |
| M24 | ΔBIC 三段式表（<0 拒绝／0–10 参考／>10 考虑） | 179–191 | RULES | `BIC_gain` 规则原样继承，限定 `EVALUATE_REFIT` 阶段 |

## 二、residual_analysis_prompt.md

| # | 条目 | 来源行 | 去向 | 说明 |
|---|---|---|---|---|
| P1 | 阶段一客观描述框架（禁止臆测、只描述所见） | 1–24 | REPLACED | 被 `vlm_evidence` 受控 JSON 替代；「禁止臆测」以受控标签＋`uncertain` 结构化落实 |
| P2 | VLM 提供伴星系具体坐标 | 7、12 | DROP | 坐标必须来自数值层（回归案例 `galmcp-analyzer-companion-hallucination`）；schema 已禁止 VLM 输出坐标 |
| P3 | 1D SB profile 与 sky 线检查 | 16–20 | STAY | 属现行 workflow 视觉检查；数值层的中心孔径 SNR／残差矩为其结构化对应物 |
| P4 | 阶段二参数审查（只报撞界、简并、未收敛） | 26–39 | NUMERIC | 重构方案第八节：撞界须读 min/max/vary/容差，不比较浮点相等 |
| P5 | 阶段三专家 CoT 推理 | 41–52 | REPLACED | 被第 3 层确定性规则替代（rule ID＋trace 可追溯） |
| P6 | 阶段四决策输出格式（成分表＋一次一动作） | 54–89 | REPLACED | 被 `decision_artifact` schema 替代；「增减≤1 成分」继承为每轮一个动作 |
| P7 | 确认清单：sky fix（条 1）、Bulge→PSF 前多轮调参（条 5） | 64、68 | STAY | 调参层，用户裁定不管；「放宽下界重拟而非直接判点源」已另行进入可分辨尺度门二级指纹规则 |
| P8 | 确认清单：伴星系认定条件（条 2） | 65 | 部分 RULES | SNR＋原图对应源已进 `_companion_rule`；20 px／5 星等影响范围门 STAY（用户裁定） |
| P9 | 确认清单：x/y 同时绑定（条 3）、expdisk/sersic n=1（条 4） | 66–67 | SPEC | 已确认目标规则 2、3 |
| P10 | 确认清单：奥卡姆范围（条 6）、m1>0.02（条 8） | 69、71 | RULES | 同 M21、M23 |
| P11 | 确认清单：Re_disk=1.678×Rs_disk 尺度换算（条 9） | 72 | NUMERIC | 尺寸比较前的确定性换算，属数值层职责 |

## 三、结论

- 成分增删相关的判据经验（BIC 三段式、奥卡姆作用域、伴星系双重条件、Bar 组合判据、m1 阈值、一次一动作、尺寸层级检查）已全部进入规则层或冻结 schema，无遗漏。
- 非建模形态识别描述按用户裁定废弃；VLM 标签枚举保留其混淆区分功能。
- 调参层经验（M18、P3、P7、P8 的量化门）留在现有 prompt，随现行 workflow 继续生效，不进入新架构。
- 原两项 OPEN 已于 2026-08-13 裁定：M15 Lens 保留，升入白名单并进规则层（RULES）；M17 Nucleus 代偿废弃（DROP），按新规范的可分辨尺度门执行。
