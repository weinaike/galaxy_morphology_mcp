# 多波段成分分析 Workflow：当前执行计划

状态：N17～N22 已实施并完成一次对象 `104` 的真实 canary，但静态审计确认 refit 被重复评价、候选身份和队列状态未完整贯穿 Rules／Policy／runner。当前唯一待实施口径为本文件第 10 节 N23～N27 与 N28；完成修复和测试前不得重新执行真实拟合。

更新日期：2026-10-27

2026-10-27 裁定记录（小鱼儿已确认）：

- **Q1**：GalfitS ES 优化器为固定代数进化策略，`.gssummary` 不输出收敛状态。定义 ES 收敛代理量 `fit_quality_ok`（全波段 chisq/dof 有限、代数跑满、best value 不贴采样边界），refit 判定使用该名称；`optimizer_converged` 对当前 GalfitS 版本永久标 `NOT_APPLICABLE`。
- **Q2**：本阶段 refit 判定使用 2D 残差＋reduced chi-square；`one_d_residual_outcome` 为 `NOT_APPLICABLE`（可满足终止门），1D profile 残差测量留后续阶段实现后再转为硬门。
- **Q3**：`boundary_hits` 判定采用 baseline／candidate 对比语义：comparator 同时输出两侧边界集合，仅当候选**新引入**边界、或使 baseline 既有边界**恶化且未解决**时拒绝。
- **Q4**：无已确认成分时 `REQUIRED_FIXED_PARAMETERS` 为 `NOT_APPLICABLE`（可满足终止门），有效 generic single Sersic 可以合法终态。
- **N28**：新增 bounded agent 消解层（见第 10 节 N28），在 N23～N27 完成并经 104 canary 验证后实施。

2026-10-28 补充裁定（小鱼儿确认，基于 104 canary run2 的事实）：

- **事实**：104 的 27 条 companion 候选区域去重后 17 个 target，仅 3 个（candidate_12／13／15）在 ≥2 波段重复检出，其余 14 个为单波段 SNR 5～8 的检出；科学家终态（基础 lyric 单 Sersic）中 companion 至多一个或不存在。
- **裁定 1（多波段一致性门）**：companion 候选 target 必须「同一 WCS 候选在 ≥2 波段重复检出」才具备资格——才有资格进入 VLM required targets 与 `PROPOSE_ADD companion` 提案。单波段检出的峰保留为 non-blocking 审计 trace，不进入 VLM 覆盖义务、不产生候选、不阻塞 `CONVERGED`。阈值（≥2 波段）为 v1 默认值，待评测集 companion 样本校准。
  - 2026-10-28 二次裁定（小鱼儿，基于 run3 数据）：阈值提高到 **≥3 波段**（7 波段数据下 companion 属次要成分，宁缺勿滥）。run3 数据支撑：candidate_12（6 波段）／candidate_13（5 波段）为高质量候选；candidate_15 仅 2 波段（f115w／f150w 短波段），正是被 VLM 确认后拟合并以 `BIC_gain=-163` 拒绝的那个——≥3 门可在拟合前排除。
- **裁定 2（companion 非阻塞）**：companion 属于离轴可选成分，不改变中心结构解释。按第 5.3 节 blocking 定义，`COMPANION_NUMERIC_VLM_V1` 的 INCONCLUSIVE 一律 `blocking=false`：VLM 覆盖不足或确认缺失时不阻塞终止门、不驱动证据采集循环——没有正向证据就不提案（可选成分语义），其余终止门照常判定。
- **裁定 3（拒绝记忆稳定键，2026-10-28 小鱼儿确认）**：VLM 响应天然波动使 evidence fingerprint 逐轮必变，含 fingerprint 的拒绝键无法跨轮去重（run3 round_002 重提了已拒绝候选，幸被 runner baseline 防线拦截）。拒绝去重改用「canonical action＋rule／target＋baseline artifact fingerprint」的稳定键（不含 evidence fingerprint）；evidence fingerprint 仅保留审计。同一动作在同一 baseline 上被拒后不再自动重提——证据的实质改善在 VLM 波动下无法机器判定，交人工复核（`needs_review`），不以重试换空转。
- **裁定 4（主星系成分强制同心，2026-10-29 小鱼儿转达科学家统一口径）**：主星系（host）成分必须同心，**不因拟合效果（残差、BIC、reduced chi-square）变差而撤销或否决**；companion 作为离轴成分维持豁免。据此 `CENTER_CONSTRAINT_MISSING` 的 `REFIT_PARAMETERS`（LINK_CENTER）与 Disk `n=1` 同属**规范合规动作**：接受门＝拟合收敛（`fit_quality_ok`）＋参数物理有效＋无未解决的新增边界／退化；残差、BIC 与 reduced chi-square 仅审计记录。已存在的中心偏置若为物理真实，由科学家事后审阅判断，不在自动拟合循环内消解。
- **裁定 5（bar 轴比上限，2026-10-29 小鱼儿转达科学家统一口径）**：bar 的轴比 `q ≤ 0.7`。bar 模板的 q 边界上界由 1.0 收紧到 0.7（`[0.3, 0.05, 0.7, 0.01, 1]`）；拟合把「bar」推到 q→1（圆形化）即非物理棒解，收紧后此类候选在边界处直接暴露。
- **裁定 6（disk 确认后先拆 disk+bulge，2026-10-29 小鱼儿确认；旧版规范 component_specification_galfits.md 第 113 行已有、新版规则层此前未落实）**：单 disk 模型确认 Disk 后，**必须先尝试拆分为 disk+bulge 拟合**（bulge 的 n 不预设固定，按拟合仲裁决定去留），加不上 bulge 时才继续考虑其它结构成分。新规则 `DISK_BULGE_SPLIT_V1`：Disk 已确认且 bulge 不在模型时产出 `PROPOSE_ADD bulge` 候选，优先级高于 bar／companion／fourier 等结构提议；接受与否由既有 refit 矩阵仲裁（接受＝保留；拒绝＝稳定键记录、同 baseline 不再重提，流程继续考虑其它成分）。
- **裁定 7（m1 混淆给予试拟合仲裁资格，2026-10-30 小鱼儿确认）**：`FOURIER_M1_CONFOUNDING_V1`（m=1 振幅超阈值但存在混淆源——不对称信号可能来自伴星系／中心偏置等而非盘固有）加入试拟合仲裁表：混淆不再无限期 blocking，而是降级为弱候选真实拟合一次，由 refit 矩阵裁决（BIC／残差说话）；`fourier_m1` 同时按可选成分对待（`BIC_gain >= 10` 门生效，与「BIC 不降即删 m=1」的既有经验一致）。仲裁拒绝后稳定键记忆，同 baseline 不再重提。
- **裁定 8（N1 不可测时的确认替代组合，2026-10-30 小鱼儿确认）**：N1（`source_extent_psf_ratio`，源延展度）因等照度线拟合失败而 UNAVAILABLE 时，**不是否定证据**（H7 语义）；此时允许替代组合确认盘：**N2（几何稳定或单 Sersic n≤disk_n_max 未撞界）∧ VLM 明确盘标签**（`disk_like`／`spiral_arm`／`edge_on_disk`——N1 缺席时 VLM 必须明确，neutral 标签不确认）。trace 记录 `N1_UNAVAILABLE_ALTERNATIVE` 以便审计区分主组合与替代组合确认。
- **裁定 9（m1 改为收尾阶段动作，2026-11-02 小鱼儿确认）**：fourier_m1 **不参与流程中的结构提议竞争**——所有结构成分（bar／bulge／companion／agn 等）拟合完成后，在进入终止判定之前，才尝试给 disk 加 lop（收尾动作 `FOURIER_M1_CLOSING_V1`）。保留条件：**m=1 振幅 > `m1_keep_amplitude`（0.02）∧ `BIC_gain ≥ 10`**（阈值沿用可选成分统一标准，小鱼儿授权工程定值）**∧ 拟合合理不撞新边界**（沿用 Q3 边界语义与参数物理性门）。裁定 7 的流程中混淆试拟合路径（`_TRIAL_FIT_ACTIONS` 的 `FOURIER_M1_CONFOUNDING_V1`）随之退役——混淆与否不再影响收尾动作（数据说话）。收尾动作被拒后稳定键记忆，同 baseline 不再重提。
- **裁定 10（residual_outcome 改用 GalfitS 官方统计，2026-11-02 小鱼儿确认）**：comparator 不再自算 L1 残差分数（mean |r/σ|）——34 对象批跑 142／142 份 `.gssummary` 均含 per-band `chisq／dof／reduced chisq`，直接采用官方量。`residual_outcome` 判定改为：逐波段两侧 reduced chisq 齐全即可比，**跨波段平均 reduced chisq 下降 → improved、上升 → worse、持平 → equivalent**；任一波段缺失 → `inconclusive`（fail-closed）。自算残差分数与 evidence 中的 `residual_score` 块退役；`reduced_chisq` 门的既有语义（comparable 且不得实质变差，相对容差 1e-4）不变——residual 门与 reduced chisq 门同源后仍独立成门（前者看平均方向、后者看实质变差容差）。
- **裁定 11（成分接受标准统一，2026-11-02 小鱼儿确认）**：废除「可选成分／其余成分」两分 BIC 门——v1 设计将 agn／companion／lens 等列为「可选成分」要求 `BIC_gain>=10`，而 disk／bulge／bar 无 BIC 门；本批 34 对象实证该分类无统计学必然性且方向恰好相反（过加的是无门的 bulge，被严防的成分零误加）。统一后**所有 `PROPOSE_ADD` 一律要求：残差改善（裁定 10 口径）＋ `BIC_gain >= 10` ＋ 无未解决新边界／参数物理**。`optional_components` 集合与两分逻辑删除。
- **裁定 12（VLM 残差对比门，2026-11-02 小鱼儿确认，`dirtier` 具一票否决权）**：refit 判定不再纯数值——候选拟合后将 baseline 与 candidate 的 comparison PNG（含残差面板）交 VLM 做**残差对比**，受控三标签 `cleaner`／`unchanged`／`dirtier`。判定权重：`dirtier` → **拒绝候选（一票否决，即使数值门全过**——聚合标量对「中心好外围差／残差结构化变脏」无感，视觉是最后一道防线）；`cleaner`／`unchanged` → 放行交数值门；VLM 不可用／解析失败 → 纯数值门决定并如实记录降级（fail-open 不阻塞）。**规范合规动作豁免**（与裁定 4 一致）：`MANDATORY_DISK_N_SPEC` 与 `MANDATORY_CENTER_CONSTRAINT_SPEC` 分支不受此门影响——合规要求不因拟合效果（含视觉）否决。新 MCP 工具 `workflow_compare_residuals`（pilot gated）负责 VLM 调用，verdict 并入 `refit_evaluation.vlm_residual_verdict` 后进入矩阵；trace 规则 `VLM_RESIDUAL_GATE_V1`。
- **裁定 13（先拆分入口，2026-11-03 小鱼儿确认，基于 T3 诊断表全部 PNG 的人工审阅）**：当证据形态为「中心亮团＋延展外围」（VLM 中心标签 `central_compact_excess`）且盘未确认时，**第一动作是拆「中心成分＋外围」**——不等待盘确认。`DISK_BULGE_SPLIT_V1` 的适用条件由「disk 已确认∧bulge 不在」扩展为「bulge 不在∧（disk 已确认 ∨ （disk 未确认∧VLM 中心亮团标签））」；先拆分入口产出的仍是 `PROPOSE_ADD bulge`（n 自由），接受与否由既有统一矩阵仲裁（裁定 10／11／12 全部适用）。**盘身份确认后置**：拆分接受后模型为「bulge＋unclassified 外围 Sersic」，后续轮次的 `PROMOTE_SINGLE_SERSIC_TO_DISK` 照常评估外围成分的盘身份（现有 promotion_target 逻辑天然支持，无需额外改动）。`dust_lane` 冲突在先拆分入口同样仅审计。T3 的 9 个对象（1398／1429／1639／170／1803／287／331／365 等）即此路径的目标场景。

## 1. 执行权威、目标和边界

本文件是当前多波段成分分析 workflow 的唯一执行方案。`workflow-redesign-execution-plan.md.orig` 是旧版快照，只用于差异审计，不得读取为执行指令。历史 run、旧 lifecycle、旧 `PolicyState`、旧 Image result 和旧最佳轮次只能作为离线 fixture，不能成为新 run 的 baseline 或科学结论。

目标是实现类似 JWST0716 的真实多轮 Image workflow：每轮都基于上轮已接受模型的真实拟合产物产生结构化证据和决策；每个模型动作生成新的 lyric、执行新的多波段 Image fitting、调用 `workflow_evaluate_refit`；只有 Image 结构和拟合都通过审计并锁定后，才进入 SED 和 Image-SED。

```text
Round 0 原图预测
  → baseline Image fitting
  → Numeric Evidence + 可选 VLM Evidence
  → Rules（候选和终止检查）
  → Policy（resolved_decision）
  ├─ 模型动作 → 新 lyric → Image fitting → refit 评价 → 接受或拒绝 → 下一轮
  ├─ 证据采集 → 仅采集明确的新证据 → 重新进入 Rules
  ├─ CONVERGED → verifier PASS → lockable=true → LOCKED → SED → Image-SED
  └─ STOPPED_NEEDS_REVIEW → 保留 Image 审计结果，停止下游
```

必须遵守：

- JWST0831 真实拟合仅允许对象 `104`、`1071`、`1118`。当前 N23～N27 的真实 canary 只允许 `104`；在小鱼儿确认前，`1071`、`1118` 和其余 31 个对象都不得启动。
- JWST0831 不得调用 `run_galfit`。实际多波段拟合入口只能是 `run_galfits_image_fitting`、`run_galfits_sed_fitting`、`run_galfits_image_sed_fitting`。
- 不得在 shell、Python subprocess 或其他绕过 MCP 的路径直接执行 GALFIT／GalfitS。
- 每次模型动作最多改变一个结构语义或一个参数／约束计划；每个实际候选拟合必须调用 `workflow_evaluate_refit`。
- 所有机器动作只能来自通过 schema 校验的 `decision_artifact.resolved_decision`。VLM 原文、Markdown、Working Note 和模型生成的解释文字都不能反向决定动作。
- 首轮 generic single Sersic 只表示当前 profile，不得默认解释为 Disk。只有 component analysis 明确确认 Disk 后才能产生 `PROMOTE_SINGLE_SERSIC_TO_DISK` 或 `DISK_N_NOT_FIXED`；确认后 Disk 的 `n` 必须固定为 1。
- 普通候选的接受／拒绝必须综合收敛、参数物理性、1D／2D 残差、reduced chi-square、BIC、参数边界和退化。BIC 不是唯一标准。Disk 已确认后的 `n=1` 是语义约束，不因单一拟合统计量变差而撤销，但拟合仍必须收敛且参数物理有效。
- 不得把工程验收或 `LOCKED` 表述为科学上已经确认全局最优模型。

## 2. 当前事实和闭环缺口

2026-09-10 的最新 104 canary 位于：

```text
/home/www/2026/GALFITS_examples/jwst0831/104/output/workflow/jwst0831-20260910T095811Z/
```

它实际完成了：single Sersic 到 Disk 的 promotion、一次目标为 `candidate_12` 的 `PROPOSE_ADD companion`、一次真实 candidate Image fitting，以及最终 `STOPPED_NEEDS_REVIEW`。companion candidate 已收敛且残差分数略有改善，但 `obj0.re` 命中边界、`BIC_gain=-163.3515625`、reduced chi-square 从 `0.5613413453` 增至 `0.5614057779`，因此首次 `REJECT_REFIT` 有多项证据支持，不视为单独的 Rules 错误。最终的 `COMPLETED_WITH_REVIEW`、`FIT_AVAILABLE`、`UNLOCKED` 只表示有可审计的 Image 结果；它不是 Image 收敛、最佳轮次锁定，也不允许 SED 或 Image-SED。当前 `SED: NOT_RUN`、`Image-SED: NOT_RUN` 是正确状态。

该 run 当时的 VLM 请求全部为 `REFUSED`。小鱼儿已于 2026-09-11 确认 `.mcp.json` 与 `.env` 的 `OPENAI_BASE_URL` 一致，但该修改不会改写历史 artifact；新配置仍需先通过不执行拟合的 provider smoke test。

N17～N22 的测试通过不能覆盖本次静态审计发现的以下通用缺口：

| 缺口 | 已观察现象 | 必须修复的结果 |
|---|---|---|
| refit 重复评价 | runner 先调用 `workflow_complete_candidate`；该函数内部已经执行 `evaluate_refit_with_policy`，随后 runner 又调用 `workflow_evaluate_refit` | comparator 与决策分层；每个真实 candidate 只能有一次 Rules／Policy refit 评价和一次状态写入 |
| candidate 身份和参数语义丢失 | Policy 在 `REJECT_REFIT` 分支以结果动作生成拒绝键；runner 又把 `evidence_fingerprint`、`baseline_config_checksum` 嵌套在 `evidence_refs`，而 MCP wrapper 从顶层读取；现有 action fingerprint 还被误称为 baseline config checksum | refit artifact 必须携带 schema 校验的原始 `candidate_ref`；删除松散的重复顶层参数，以 canonical action、真实 baseline artifact fingerprint 和 evidence fingerprint 计算服务端 candidate key |
| 候选队列未推进 | 拒绝后重新分析相同 baseline，重复候选由 runner 检出后直接 `STOPPED_NEEDS_REVIEW` | 拒绝后先消费同一 proposal 的下一安全候选；队列为空后才能采集新证据或停止 |
| VLM 依赖候选越权 | `COMPANION_NUMERIC_VLM_V1=INCONCLUSIVE` 且缺少 VLM confirmation 时，Policy 仍通过 `_TRIAL_FIT_ACTIONS` 生成 `PROPOSE_ADD companion` | VLM 依赖候选只能保持不确定或进入限定 collector；只有独立 numeric 候选可在 VLM 失败时执行 |
| evidence view 被 fallback 覆盖 | VLM 失败后 decision 被 numeric retry 替换，随后 `evidence_views["vlm"]` 可能复制的是 fallback candidates，而不是原始 VLM view；字段名也与方案的 `numeric_plus_vlm` 不一致 | 原始 `numeric_plus_vlm` 与 `numeric_only` 独立落盘，fallback 只能新增 resolved view，不得改写前两个证据视图 |
| 候选排序不完整 | `_select_candidates` 实际只按整数 priority、是否 `INCONCLUSIVE` 和插入顺序排序 | 实现可审计的复合排序键，并为每项候选保存排序依据 |
| 高优先级 blocker 可被绕过 | Policy 先筛出所有 executable candidate；即使更高优先级候选为 blocking `INCONCLUSIVE`，也可能继续执行后面的低优先级动作 | 排序后先处理最高优先级相关 blocker；未解决前不得绕过到可能改变同一结构解释的低优先级模型动作 |
| refit 统计门不完整 | reduced chi-square 已落盘但没有进入普通 candidate 的确定性接受／拒绝逻辑 | refit 评价同时使用收敛、物理性、1D／2D 残差、reduced chi-square、BIC、边界和退化 |
| “收敛”语义不真实 | multi-band adapter 的 `fit_succeeded` 主要检查 HDU／shape／summary 存在；comparator 的 `fit_converged=yes` 也来自 artifact 完整性，并非优化器收敛状态 | 分离 `artifact_valid`、MCP 执行状态和 `optimizer_converged`；Rules 的收敛硬门只能使用真实优化器状态，未采集时为 `UNAVAILABLE` |
| 缺失证据被当作否定 | 部分 Rules 用 `_first_value` 取值，字段缺失时返回 `NOT_SATISFIED`；termination 又要求所有状态严格为 `PASS`，没有区分 `NOT_APPLICABLE` 与 `UNAVAILABLE` | 缺失证据统一为 `INCONCLUSIVE／UNAVAILABLE`；真实负证据才是 `NOT_SATISFIED`；不适用门为 `NOT_APPLICABLE` 并可满足终止条件 |
| 多波段比较可能静默错配 | comparator 按 band token 匹配失败后退回“第一个尚未使用的 result”；当前残差又只有一个聚合标量，没有独立 1D／2D 结论 | 只能按 manifest 中显式 band 映射比较，无法匹配即 fail-closed；分别生成 1D 与 2D residual outcome 及可比性 |
| `INCONCLUSIVE` 粒度过粗 | `inconclusive_seen` 和 `terminated_rules` 主要按 rule ID 记录，一个 target 可能终止同规则其它 target | 状态键至少包含 rule ID、target 和 evidence fingerprint；不同 target 独立处理 |
| 幂等事件缺少 baseline | proposal event ID 没有包含当前 config／result 的 baseline 身份 | 幂等键包含 run、round、baseline artifact fingerprint、evidence fingerprint 和受控 recommendation |
| trace 与能力契约滞后 | refit health trace 只写 `inputs=["obj0.re"]`；MCP capability 仍列旧 `KEEP_AND_CONTINUE` 且未列 `COLLECT_EVIDENCE` | trace 直接记录具体未满足条件和综合指标；capability 与新 schema／状态机一致 |

## 3. 模块职责和不可跨越的边界

| 层 | 主要文件 | 唯一职责 |
|---|---|---|
| 输入和 manifest | `artifact_adapter.py`、`workflow_bridge.py` | 解析 lyric，验证 science／sigma／mask／PSF，建立显式路径和 checksum 的 manifest |
| 数值证据 | `numeric.py`、`derived.py` | 只测量拟合与图像事实，不输出成分动作 |
| Round 0 | detection adapter | 保存 `detect_bar_lopsidedness`、初图预测与质量状态；预测不等于确认成分 |
| VLM 证据 | `provider.py`、`vlm.py`、`decision_service.py` | 只为 numeric layer 已发行的 target 生成受控观察，不创建 target、成分或动作 |
| Rules | `rules.py` | 从当前模型和各证据视图生成规则 trace、结构候选、参数候选和终止检查 |
| Policy | `policy.py` | 在 Rules 已有候选内排序，管理 evidence collection、trial、重复和 review；不创造科学候选 |
| 动作编译 | `workflow_bridge.py` | preflight、生成新 lyric、检查约束和构建 MCP 调用参数；不直接拟合 |
| runner | `workflow_batch_runner.py` | 只按 `resolved_decision` 调用 MCP，维护 lifecycle、candidate manifest、refit 评价与状态 |
| 比较 | `refit_comparator.py`、`workflow_complete_candidate` | 只按 manifest 的显式 band 映射比较 baseline 和 candidate，输出结构化 1D／2D residual、统计量、参数健康和真实收敛 evidence；不得给出最终 verdict、调用 Policy 或改写 lifecycle |
| refit 仲裁 | `rules.py`、`policy.py`、`workflow_evaluate_refit` | Rules 依据结构化 evaluation 给出 refit action，Policy 更新候选状态；这是 refit verdict 和状态写入的唯一入口 |
| renderer | `workflow_summary_renderer.py` | 只从 schema 校验通过的 artifact 生成确定性人类报告；不生成或改变机器动作 |

## 4. 当前模型和证据视图

artifact 必须明确区分当前 profile、Round 0 预测和已确认成分。禁止再用 `current_components=[]` 同时表示“没有模型”和“模型未分类”。最小表达为：

```json
{
  "current_profile": [
    {
      "component_id": "obj0",
      "model_type": "sersic",
      "semantic_label": "single_sersic",
      "classification": "unclassified"
    }
  ],
  "predicted_components": [],
  "confirmed_components": []
}
```

`single_sersic` 是 lyric 事实，不能推导为 Disk。只有 promotion 被接受后，才把同一 profile 的 `semantic_label` 变为 `disk` 并固定 `n=1`。

每轮必须保存并区分三个证据视图：

1. `numeric_only`：仅数值证据运行 Rules 的结果。
2. `numeric_plus_vlm`：有效 VLM observation 加入后运行 Rules 的结果。
3. `resolved_decision`：Policy 根据已保存的 Rules 候选产生的唯一机器动作。

VLM 缺失只能使依赖 VLM 的规则为 `INCONCLUSIVE`，不能跳过不依赖 VLM 的收敛、残差、参数健康、边界、中心约束、固定参数和已确认成分规则。

`numeric_plus_vlm` 必须保存“本轮原始 VLM evidence 可用时”的 Rules 输出；`numeric_only` 必须保存独立重算结果。VLM 失败时不得用 numeric fallback 覆盖或伪装前者，`numeric_plus_vlm` 应保留失败状态、已覆盖 target 和未覆盖原因。实现中的旧键 `evidence_views.vlm` 只做读取兼容，新写入统一使用上述名称。

## 5. 决策状态机：模型动作与证据采集分离

### 5.1 顶层 transition

`KEEP_AND_CONTINUE` 不再是新 workflow 的顶层机器动作。它只作为旧 artifact 的兼容读取值：renderer 必须如实展示旧值，但新的 Rules、Policy、schema 和 runner 不得生成它。

新的顶层 transition 只有以下四类：

| transition | 条件 | 是否生成新 lyric | 是否执行新的 Image fitting |
|---|---|---:|---:|
| 模型动作 | 有证据充分、可编译、未被当前 fingerprint 拒绝的动作候选 | 是 | 是 |
| `COLLECT_EVIDENCE` | 缺少关键证据，但存在明确 collector 且能产生新输入或新计算 | 否 | 否 |
| `CONVERGED` | 没有待执行模型动作，所有必要终止门为 `PASS` | 否 | 否 |
| `STOPPED_NEEDS_REVIEW` | 没有安全模型动作，也没有能产生新证据的 collector；或工具／schema／预算失败 | 否 | 否 |

`COLLECT_EVIDENCE` 不属于 Image fitting round，不应被计为一次模型拟合轮次。其唯一作用是产出新证据，再重新运行 Rules。它必须包含：

```json
{
  "next_transition": "COLLECT_EVIDENCE",
  "collector_id": "VLM_TARGET_RETRY",
  "evidence_targets": ["candidate_12"],
  "expected_input_change": "target_subset=candidate_12;prompt_variant=sparse_single_target",
  "expected_new_fingerprint": "<deterministic fingerprint of collector inputs>",
  "max_attempts": 1
}
```

允许的 collector 必须是枚举值并有本地实现，例如：

- `VLM_TARGET_RETRY`：只请求缺失 target 的稀疏 VLM observation；
- `COMPUTE_MISSING_RESIDUAL_PROFILE`：补齐当前 result FITS 未采集的 1D／2D 残差测量；
- `COMPUTE_PSF_DIAGNOSTIC`：补齐当前 result FITS 可计算的 PSF／中心分辨诊断。

同一 collector、同一输入、同一预期 fingerprint 只能执行一次。collector 无法产生新数据、返回相同 fingerprint、或超过 `max_attempts` 时，必须转 `STOPPED_NEEDS_REVIEW`。

### 5.2 模型动作契约

以下五类是仅有的可执行模型动作。每一种都必须生成新 lyric、通过 `check_lyric_file`、调用 `run_galfits_image_fitting`、建立 candidate manifest、调用 `workflow_evaluate_refit`，然后才允许接受或拒绝。

| action | 条件 | lyric 改动 |
|---|---|---|
| `REFIT_PARAMETERS` | 已确认成分有参数边界、中心约束、必要固定项、退化或可诊断参数问题 | 改一个 parameter／constraint plan，不改结构 |
| `PROPOSE_ADD` | 不存在的成分有完整结构证据、模板和参数计划 | 增加一个成分或一个 Fourier 项 |
| `PROPOSE_REPLACE` | 现有 profile 有明确 `replace_from`、`replace_to`、模板和物理理由 | 全波段替换 profile 与相关约束 |
| `PROPOSE_REMOVE` | 有真实局部残差事实、数据质量通过、remove safety gate 与 pilot 均通过 | 从全部 profile、Galaxy 成员和约束中删除目标 |
| `PROMOTE_SINGLE_SERSIC_TO_DISK` | 当前为未分类 single Sersic，component analysis 明确确认 Disk | 保留同一 profile，写入 Disk 语义并固定 `n=1`，不增加第二个 Disk |

`PROPOSE_REPLACE` 和 `PROPOSE_REMOVE` 不能再停留在“可显示、不可执行”的候选状态：要么实现完整 lyric compiler 和测试，要么 Rules 不得提出它们。

### 5.3 Rules 候选与收敛逻辑

Rules 必须：

- 对全部 numeric candidate regions 完成遍历。一个 target 的证据不足只能使该 target 的候选为 `INCONCLUSIVE` 或 `DEFERRED`，不得提前结束同一规则的其它 target。
- 每个规则 trace 记录 `SATISFIED`、`NOT_SATISFIED` 或 `INCONCLUSIVE`、所用字段、缺失原因、target、候选动作、优先级和是否阻止收敛。
- 候选排序先比较：动作安全性与可编译性、与当前模型的相关性、证据完整度、规则科学优先级、数值支持强度；不得按规则插入顺序或“最后一个 `INCONCLUSIVE`”决定。
- 把 `INCONCLUSIVE` 分为 `blocking` 和 `non_blocking`。只有与当前模型或未决高优先级结构直接相关、且存在合理证据采集路径的 `blocking` 不确定才阻止 `CONVERGED`。
- 明确区分字段缺失和真实负证据：缺失／解析失败为 `INCONCLUSIVE` 或 `UNAVAILABLE`，观测明确不支持才是 `NOT_SATISFIED`，不适用于当前 profile 的门为 `NOT_APPLICABLE`。
- terminal gate 以 `PASS` 或 `NOT_APPLICABLE` 为满足，`UNAVAILABLE` 和 blocking `INCONCLUSIVE` 均阻止收敛。例如有效 generic single Sersic 可以是终态，但不能为了通过终止门把它补写成 Disk；没有已确认 Disk 时，`DISK_N_NOT_FIXED` 应为 `NOT_APPLICABLE`；没有已确认成分时，`REQUIRED_FIXED_PARAMETERS` 为 `NOT_APPLICABLE`（2026-10-27 裁定 Q4），不得因该项缺失阻止有效单 Sersic 终态。
- 没有可执行动作时，若所有必要门为 `PASS／NOT_APPLICABLE`，返回 `CONVERGED`；若有明确 collector，返回 `COLLECT_EVIDENCE`；两者都没有时返回 `STOPPED_NEEDS_REVIEW`。

### 5.4 Policy 选择、拒绝和停止逻辑

Policy 只消费 Rules 已生成的候选，且必须：

- 保存 `numeric_only`、`numeric_plus_vlm` 和最终 resolved view。独立 numeric 候选在 VLM 失败时仍可进入真实 refit；依赖 VLM confirmation 的候选必须保持 `INCONCLUSIVE` 或进入限定 collector，不得由 Policy 转换为 trial fit。
- 每个候选包含稳定 `candidate_id`、完整原始 action、rule ID、target、排序依据和证据引用。候选试验键按 `sha256(canonical_action + baseline_artifact_fingerprint + evidence_fingerprint)` 计算；`canonical_action` 必须包含 component、target、replace 关系和 parameter plan，不能使用结果动作 `REJECT_REFIT` 代替。
- proposal 解析后把可执行候选保存为有序 `pending_candidate_queue`。选择候选时将其标为 `SELECTED`；refit 后标为 `ACCEPTED`、`REJECTED` 或 `INCONCLUSIVE`，不得丢失候选历史。
- 排序后的高优先级 `blocking INCONCLUSIVE` 是队列屏障：在其 collector 成功、被确定性证据否定或明确转人工前，不得执行会改变同一中心结构解释的低优先级 candidate。互不相关且 Rules 明确标为 non-blocking 的 candidate 才可继续。
- candidate 被 `REJECT_REFIT` 后，baseline 不变，并从当前队列中选择下一个未尝试的安全候选。只有队列为空时才决定 `COLLECT_EVIDENCE` 或 `STOPPED_NEEDS_REVIEW`。
- candidate 被 `ACCEPT_REFIT` 后，新 candidate 成为 baseline，旧队列立即失效；必须从新 result FITS 重新采集 evidence 和生成候选。
- `rejected_components` 只允许兼容读取历史状态，不得继续作为新状态的写入或筛选依据。不同 target、不同 baseline 或有实质新 evidence 时允许重新评估同类成分。
- `inconclusive_seen`、collector 和 terminated 状态必须按 rule ID、target、baseline 和 evidence context 隔离，不得因一个 target 终止同规则的其它 target。
- 不得把无动作的 `INCONCLUSIVE` 伪装成 `PROPOSE_ADD` 或任何模型动作；不得以重复调用相同 VLM prompt 代替证据计划。

### 5.5 refit 评价的唯一入口

`rules.evaluate_refit()` 是纯判定函数，不负责 MCP、文件或状态；`workflow_evaluate_refit` 是唯一对外 refit 仲裁入口。标准链路必须固定为：

```text
baseline manifest + candidate manifest + baseline／candidate 的真实 MCP fit result
  → refit_comparator／workflow_complete_candidate
      只生成 schema-valid refit_evidence 和 evaluation
  → workflow_evaluate_refit（每个 candidate 恰好调用一次）
      → rules.evaluate_refit（纯 Rules 判定）
      → Policy 更新 candidate queue／PolicyState
      → refit_decision + next_decision + candidate lifecycle
  → runner 只执行 next_decision.resolved_decision
```

具体约束：

- 保留 `workflow_complete_candidate` 名称以兼容现有 MCP 客户端时，它只能充当 comparator adapter；不得加载或保存 `PolicyState`，不得调用 `evaluate_refit_with_policy`，不得生成最终 refit verdict，亦不得写 candidate lifecycle。
- `workflow_evaluate_refit` 必须接收 schema 校验的 `candidate_ref`，至少包含原始 resolved action、candidate ID、rule ID、decision ref、baseline artifact fingerprint 和 evidence fingerprint。上述身份只允许出现在 `candidate_ref`，不再通过顶层参数或 `evidence_refs` 重复传递；服务端从这些字段计算 candidate key，不能信任客户端直接提交的 key。
- `workflow_evaluate_refit` 只能执行一次 Rules 判定、一次 Policy transition 和一次原子状态写入；重复提交同一 candidate ref 必须幂等返回同一结果，不得再次消耗队列、trial budget 或追加重复 lifecycle event。
- `ACCEPT_REFIT`／`REJECT_REFIT` 是 `refit_decision` 的 verdict，不是下一轮模型动作。返回值必须另含 schema-valid `next_decision` 或明确的 `null`：拒绝后可从原队列解析下一 candidate；接受后只能返回 `next_transition=ANALYZE_NEW_BASELINE`，由新 result 重新采集 evidence 和运行 Rules；需要补证据时返回 `COLLECT_EVIDENCE`；没有安全路径时返回 `STOPPED_NEEDS_REVIEW`。
- `next_decision` 若非 `null`，必须是完整 decision artifact，并且只有它的 `resolved_decision` 可被 runner 执行。runner 不得从 refit verdict、队列内部对象、Markdown 或自由文本直接构造下一动作。
- `decision_artifact` schema 必须让 `EVALUATE_REFIT` 状态强制包含 `candidate_ref`；历史 v1.1 artifact 只读兼容，不反向补写。

### 5.6 refit 多指标判定矩阵

普通 candidate 的 evaluation 必须显式包含下列字段，缺失时写 `unavailable` 并进入 `INCONCLUSIVE`，不得补猜：

```text
artifact_valid
fit_quality_ok（ES 收敛代理量：全波段 chisq/dof 有限、ES 代数跑满、best value 不贴采样边界）
optimizer_converged（对当前 GalfitS 版本恒为 NOT_APPLICABLE，仅保留字段位置）
parameters_physical
two_d_residual_outcome（2D 残差，band 严格映射）
one_d_residual_outcome（本阶段恒为 NOT_APPLICABLE，1D profile 测量留后续阶段）
reduced_chisq.baseline／candidate／delta／comparable
bic.baseline／candidate／gain／comparable
boundary_hits.baseline／candidate／new_or_unresolved（Q3 对比语义）
degeneracy_warnings
```

确定性判定顺序如下：

1. artifact 无效属于工具／schema 失败；`fit_quality_ok=false`、参数不物理、存在候选新引入或未解决的参数边界（Q3：`boundary_hits.new_or_unresolved` 非空）或退化时，普通 candidate 为 `REJECT_REFIT`。`fit_quality_ok` 未采集时为 `INCONCLUSIVE`，不得用文件存在代替；baseline 既有且候选未恶化的边界不参与拒绝。
2. `PROPOSE_ADD` 至少要有 2D 残差明确改善（Q2：本阶段 1D 门为 `NOT_APPLICABLE`，1D profile 测量实现后再转为硬门）；可选成分继续要求 `BIC_gain >= 10`，comparable reduced chi-square 不得实质变差。必要指标不可比较时为 `INCONCLUSIVE`，不能只凭 BIC 接受。
3. `PROPOSE_REPLACE`、`PROPOSE_REMOVE` 和普通 `REFIT_PARAMETERS` 允许残差等价，但 BIC 与 reduced chi-square 不得同时变差；二者方向冲突且没有其它决定性证据时为 `INCONCLUSIVE`。
4. `PROMOTE_SINGLE_SERSIC_TO_DISK` 和确认 Disk 后的 `DISK_N_NOT_FIXED` 属于模型语义约束：确认后 `n=1` 必须执行。只要 refit 收敛、参数物理有效且没有未解决的边界／退化，即可接受；残差、BIC 和 reduced chi-square 保留审计但不用于撤销 Disk 身份。
5. 所有比较使用 comparator 签发的可比性和版本化数值容差。不得在 renderer、runner 或模型文字中建立第二套判定。

每条 refit trace 必须列出实际使用的值、门槛、比较方向、未满足条件和最终原因。例如边界命中必须写明 `boundary_hits=["obj0.re"]`，不能只写一个没有解释的 input 名称。

## 6. VLM provider 和 target 覆盖协议

VLM 只提供受控形态证据。104 的 18 个 target 是 `central + 17 个 numeric candidate_regions`，不是 18 个已确认成分，也不是固定配置。target 数量必须随当前 numeric evidence 动态变化。

每一次 VLM attempt 都必须记录：

```text
model、prompt_version、attempt_id、target_ids、开始／结束时间、响应字节数、finish_reason、token_usage、parse_status、retry_variant
```

provider 未返回的字段写 `unavailable`，不得猜测。状态必须区分 `DISABLED`、`REFUSED`、`TIMEOUT`、`PARSE_FAILED`、`PARTIAL`、`OK`。

覆盖规则：

- 每个 required target 必须最终被标记为 `OK`、明确失败或 `NOT_APPLICABLE`；整体 `OK` 仅在所有 required target 都有有效 observation 时可用。
- 初始批次可包含少量 target；批次发生截断、空响应或 parse failure 时，必须递归拆分未覆盖 target，直至单 target。只重试第一个 target 是禁止的。
- retry 必须改变 target 子集或 prompt 版本，不能重复相同请求。单 target retry 使用更短的 sparse prompt。
- prompt 只请求当前 Rules 需要的 target，并限制 observation 数量和 notes 长度。notes 不得包含动作、坐标或参数建议。
- parser 对截断 JSON 继续 fail-closed；不允许部分采信截断响应。
- `max_tokens` 不得低于 `4096`，并保留 provider capability／token budget 测试。提高上限只是降低截断概率，不能替代 target 覆盖、稀疏 prompt 和递归拆分。

原始 VLM prompt／response 的持久化是次要审计增强项，不阻塞本阶段闭环；但持久 artifact 至少必须保存 attempt metadata、parse error、target coverage 和可定位的临时 raw response 引用。不得把临时 raw response 当成机器动作来源。

## 7. 人类可读 renderer 和报告契约

renderer 必须是本地确定性的 Python 实现，只读取已落盘并通过 schema 校验的 JSON artifact。它可以对已有字段进行排序、翻译、汇总和归类，但不能补猜科学结论、数值、动作或 `next_transition`，也不能调用 agent 调度模型。

### 7.1 每轮 summary 的固定正文顺序

每个 Image fitting round 结束后立即写入：

```text
summaries/round_<round_id>_component_analysis.md
```

正文必须按以下顺序，不得用整段 nested JSON 代替：

1. **本轮结论**：当前 workflow status；是否执行新 Image fitting；是否接受 candidate；SED／Image-SED 状态；下一步或停止原因。
2. **当前模型**：当前 profile、已确认成分、当前 lyric；明确 `single_sersic`、`disk` 等语义。
3. **Round 0 原图预测**：`detect_bar_lopsidedness` 逐波段结论、检测到的 Bar／lopsidedness、预测成分和其“预测而非确认”的状态。字段缺失写 `unavailable`。
4. **本轮拟合评价**：baseline 和 candidate 分开列出是否收敛、1D／2D 残差、reduced chi-square、BIC、参数物理性、边界、退化、refit verdict。没有 candidate fitting 时明确写“本轮没有生成 candidate lyric，未执行新的 Image fitting”，而不是只写 `unavailable`。
5. **关键证据**：按波段和用途归组 Numeric Evidence；每项展示测量名、波段／target、值、状态和它支持或限制的规则。不得重复输出无波段归属的同名字典。
6. **VLM 证据覆盖**：provider 总状态、model、prompt version、请求次数、成功／失败次数、`finish_reason=length` 次数、required／covered／missing target、每个有效 observation 的受控 label 和 confidence。`PARTIAL` 或 `PARSE_FAILED` 必须说明哪些规则受影响。
7. **规则判断**：以表格列出 rule ID、结果、关键条件／缺失原因、目标、候选动作、是否阻止收敛。必须解释 raw decision 为何产生。
8. **动作决策**：分别展示 Raw Action、Policy resolution、Resolved Action、reason code、是否实际执行。对 `COLLECT_EVIDENCE` 展示 collector、target、预期新 fingerprint 和最大次数。
9. **lyric 与产物**：baseline／candidate 的 lyric、约束差异摘要、每个波段的 result FITS、gssummary、comparison 路径；完整路径可置于折叠式审计索引，但必须可定位。
10. **状态和下一步**：精简 `PolicyState` 摘要，例如 trial budget、已拒绝的具体 candidate key、best round status、needs_review、verifier、next transition。不得把完整嵌套 `PolicyState` JSON 放在正文。

完整 JSON、原始字段和值必须通过 `artifact_index.json`、`run_index.json` 或文末“审计索引”链接，不得占据正文。

### 7.2 Working Note 和最终报告

对象级 `summaries/working_note.md` 必须累计每轮的以下内容：模型变化、动作理由、参数／约束差异、candidate 评价、距离预期目标的偏差、接受／拒绝和下一步。它必须能在不打开 JSON 的情况下读懂整个 Image 迭代链。

最终 `summaries/analysis_report_obj<ID>.md` 必须包含：

- Image 最终状态、是否锁定、停止原因；
- 从 baseline 到终态的动作时间线；
- 最终接受模型和仍未解决的问题；
- verifier／lock 结果；
- `SED` 与 `Image-SED` 状态；Image 未通过下游门时必须明确写 `SED: NOT_RUN`、`Image-SED: NOT_RUN`；
- run index、artifact index 和容量报告引用；
- 明确说明这只是工程状态和可追溯证据，不代表科学上确认全局最优。

## 8. Runner、下游门和输出布局

每个新 run 必须从基础 lyric 创建全新 run ID、run-scoped baseline lyric 和独立 `PolicyState`。不得 resume 或复用旧 run 的 lifecycle、PolicyState、Image result、baseline 或最佳轮次。

JWST0831 的 sigma 路径必须来自：

```text
/home/www/2026/GALFITS_examples/jwst0831/sigma/Sig<obj>_<band>.fits
```

runner 在真实 Image 阶段必须严格执行：

1. 验证对象白名单、基础 lyric，以及所有 science／sigma／mask／PSF 文件和 shape。
2. 执行 Round 0 `detect_bar_lopsidedness`，保存独立 artifact。
3. 调用 `run_galfits_image_fitting` 产生 baseline。
4. 建立 manifest，采集 numeric evidence 和可选 VLM evidence，运行 Rules 和 Policy。
5. 对模型动作：preflight → 新 lyric → `check_lyric_file` → `run_galfits_image_fitting` → candidate manifest → comparator 只生成 refit evidence → `workflow_evaluate_refit` 恰好一次 → runner 只执行返回的 `next_decision.resolved_decision`。
6. 对 `COLLECT_EVIDENCE`：仅调用定义的 collector；其后以新 evidence fingerprint 重跑 Rules。不得新建 lyric 或重复 Image fitting。
7. 每个 Image fitting round 结束后生成 summary、更新 Working Note；终态时生成 final report、`run_index.json`、`artifact_index.json` 和 `capacity_report.json`。`run_index.json` 的 `round_dir` 必须指向持久 `artifacts/` 下的 round 目录，不得指向 `/tmp` 临时 run 根，避免临时目录清理后审计链断裂。

只有同时满足以下条件才允许调用 SED：

```text
Image workflow_status = CONVERGED
workflow_verify_best_round.verdict = PASS
verifier artifact.lockable = true
workflow_lock_best_round.status = LOCKED
```

任何 `STOPPED_NEEDS_REVIEW`、真实拟合轮次上限、Image 工具失败、schema 失败、verifier 非 `PASS` 或 best round 未 `LOCKED` 都禁止 SED 和 Image-SED。有有效 Image result 时记录 `FIT_AVAILABLE`、`UNLOCKED`、`needs_review=true`、`sed_joint_eligible=false`；没有有效 Image result 时记录 `FAILED_NEEDS_REVIEW`。两种情况都必须保持 SED／Image-SED 为 `NOT_RUN`。

## 9. 测试和验收矩阵

真实拟合之前，必须通过下列测试和 offline replay：

| 类别 | 必测内容 |
|---|---|
| schema／capability | `EVALUATE_REFIT` 强制 `candidate_ref`；candidate queue／状态枚举完整；新 capability 包含 `COLLECT_EVIDENCE` 且不再宣告生成 `KEEP_AND_CONTINUE`；历史 v1.1 artifact 只读兼容 |
| comparator | baseline／candidate 只能按 manifest 的显式 band 配对，不能位置回退；分别输出 1D／2D residual；区分 artifact validity、MCP 状态和真实 optimizer convergence；`workflow_complete_candidate` 不调用 Rules／Policy、不修改 state、不写 lifecycle |
| Rules | VLM failure 不短路 numeric rules；缺失与真实负证据分离；`PASS／NOT_APPLICABLE／UNAVAILABLE` 终止语义；全部 target 遍历；blocking／non-blocking `INCONCLUSIVE`；复合候选排序；refit 综合真实收敛、物理性、1D／2D 残差、reduced chi-square、BIC、边界和退化；trace 原因完整 |
| Policy | VLM 依赖 `INCONCLUSIVE` 不转 trial fit；高优先级 blocker 不被低优先级动作绕过；精确 candidate key；target 级 inconclusive；拒绝后选择同队列下一候选；接受后使旧队列失效；无候选无 collector 时停止；`numeric_plus_vlm` 不被 fallback 覆盖 |
| refit 单入口 | 一个真实 candidate 只触发一次 `rules.evaluate_refit`、一次 Policy transition、一次状态写入和一个 candidate lifecycle；返回分离的 `refit_decision`／`next_decision`；重复 MCP 请求幂等返回，不重复消费状态 |
| lyric compiler | `REFIT_PARAMETERS`、`PROPOSE_ADD`、`PROPOSE_REPLACE`、`PROPOSE_REMOVE`、promotion 均生成新的有效 lyric；replace／remove 跨波段 profile 与约束完整；promotion 不产生第二个 Disk |
| runner | candidate A 拒绝后执行队列中的 candidate B；没有 B 时才 collect／stop；相同 action、target、baseline 和 evidence 不重复拟合；三对象白名单；resume 幂等；禁止 `run_galfit` 和 shell GALFIT／GalfitS |
| event identity | 相同 evidence 但不同 baseline 不视为重复；相同 baseline／evidence／candidate 的重复提交不产生新事件；不同 target 互不封禁 |
| VLM provider | 动态 target、0 candidate、重复 region ID、截断 JSON、空响应、超长 notes、单 target retry、递归拆分、非重复 prompt、完整 target coverage、`max_tokens` 传递 |
| downstream | Image 未锁定时不调用 SED／Image-SED；只有 `CONVERGED + PASS + lockable + LOCKED` 才调用下游 |
| renderer | 首次拒绝与队列下一步分开显示；具体边界、残差、reduced chi-square、BIC 和拒绝原因；不得把 `REJECT_REFIT` 写成下一轮模型动作 |
| replay | 用 v5、N16 和最新 104 artifact 只读复现双评价、错误拒绝键和重复状态；修复后 replay 生成独立派生报告，不改写历史 lifecycle、PolicyState、最佳轮次或 scientific decision |

相关测试、offline replay 和全量回归必须全部通过，才能执行 provider smoke test；smoke test 通过后才能启动真实 canary。

补充要求（2026-10-27，来自只读 review）：

- 实施 N25 时必须**改写**两个把当前缺陷固化为期望行为的既有测试：`tests/test_workflow_batch_runner.py` 的 `test_rejected_action_is_not_repeated_on_unchanged_baseline`（拒绝后停止 → 拒绝后消费同队列下一候选）和 `tests/test_policy.py` 的 `test_rejected_component_not_retried`（消费只写不读的 `rejected_components` → 改为验证 candidate queue 状态迁移）。
- N26 必须新增 contract test：禁止 runner 从 `action_summary.refit_verdict` 字符串驱动控制流；runner 的循环走向只能来自 `next_decision.resolved_decision`。

## 10. 当前最高优先级：N23～N27 实施顺序

N17～N22 是已执行历史，不再作为待执行指令。下一位实施 agent 必须先完成本方案 review；小鱼儿确认 review 结论后，才按 N23～N27 修改代码。

### N23：冻结 refit 单入口和 candidate identity 契约

修改 schema、MCP capability 和接口：

- 为新 artifact 定义 `candidate_ref`，强制保存完整原始 action、candidate ID、rule ID、decision ref、baseline artifact fingerprint 和 evidence fingerprint；由服务端计算 candidate key。删除 `evidence_fingerprint`、伪 `baseline_config_checksum` 等重复输入位置，避免 wrapper 与 runner 读取层级不一致。
- 将候选队列和 target 级 `INCONCLUSIVE` 状态纳入新的 state 契约；如需 schema／state 版本升级，必须保留旧 artifact 的只读 renderer／replay 兼容，不得原地迁移历史状态。
- 明确 `workflow_complete_candidate` 是 comparator adapter，`workflow_evaluate_refit` 是唯一 Rules／Policy refit 仲裁入口；其响应契约分离不可执行的 `refit_decision` 和唯一可执行来源 `next_decision`，并枚举 `ANALYZE_NEW_BASELINE`、`COLLECT_EVIDENCE`、`STOPPED_NEEDS_REVIEW` 等 next transition。
- 修正 `workflow_capabilities`：新动作集合包含 `COLLECT_EVIDENCE`，不包含会由新 workflow 生成的 `KEEP_AND_CONTINUE`；旧值只保留读取兼容。
- 完成 schema、capability 和历史兼容测试。

### N24：修复 Rules 和 refit 多指标判定

修改 `rules.py`、`refit_comparator.py` 及相关 schema：

- companion 等多 target 规则必须遍历全部候选区域，不得在第一个 `INCONCLUSIVE` target 提前返回。
- 全面审计 `_first_value` 和所有终止检查：缺失证据不得写成 `NOT_SATISFIED`；`NOT_APPLICABLE` 可满足终止门，`UNAVAILABLE`／blocking `INCONCLUSIVE` 必须阻止收敛；generic single Sersic 不得为通过门而改标 Disk。
- 候选使用可审计复合排序键：动作可执行性、与当前模型相关性、证据完整度、科学优先级、numeric 支持强度和稳定 tie-breaker；artifact 保存每个排序字段。
- 删除 Rules／Policy 中“VLM 依赖不确定即可 trial fit”的路径；VLM 不可用时只允许独立 numeric 候选继续。
- comparator 删除 band 匹配失败后的顺序回退，严格使用 manifest，匹配失败必须 fail-closed 报错；输出 2D residual outcome（Q2：1D 门本阶段为 `NOT_APPLICABLE`）；从 gssummary 提取 ES 收敛代理量 `fit_quality_ok`（Q1：全波段 chisq/dof 有限、代数跑满、best value 不贴采样边界），并与 artifact 完整性、MCP 成功分开；`optimizer_converged` 对当前 GalfitS 版本恒写 `NOT_APPLICABLE`。`fit_quality_ok` 无法提取时写 `UNAVAILABLE`。
- comparator 按 Q3 输出 baseline 与 candidate 两侧 `boundary_hits`，并计算 `new_or_unresolved` 集合；Rules 拒绝逻辑只消费该集合。
- 按第 5.6 节实现 refit 多指标矩阵，reduced chi-square 必须进入普通 candidate 判定，BIC 不得成为唯一标准。
- 完整填写 rule trace 的实际值、门槛、target、blocking、缺失原因和拒绝原因。
- 完成逐规则、多 target、排序和 refit matrix unit tests。

### N25：实现真实 Policy candidate queue

修改 `policy.py`、`decision_service.py`：

- proposal 解析后持久化有序 candidate queue，状态至少包含 `PENDING`、`SELECTED`、`ACCEPTED`、`REJECTED`、`INCONCLUSIVE`、`INVALIDATED`；高优先级 blocking 候选必须形成可审计屏障，不能被相关的低优先级动作绕过。
- `decision_service` 分别保存原始 `numeric_plus_vlm` 和独立 `numeric_only`，fallback 只影响 resolved view；不得用 numeric retry candidate 覆盖原始 VLM view。
- `REJECT_REFIT` 必须通过 `candidate_ref` 更新原始候选；保留 baseline，并把同队列下一候选写入 `next_decision`，不能把 `REJECT_REFIT` 当下一轮动作。
- `ACCEPT_REFIT` 后原子切换 baseline、清空 pending action 并使旧队列失效；下一轮必须重新采集 evidence。
- 停止写入和消费全局 `rejected_components`；历史字段只读展示。
- `inconclusive_seen`、collector key、terminated key 和 event ID 都加入 target／baseline 维度；不同 target 和不同 baseline 互不影响。
- 完成拒绝后继续、接受后重建、队列耗尽、collector、幂等和 resume tests。

### N26：消除双评价并统一 lifecycle／renderer

修改 `workflow_bridge.py`、`workflow_lifecycle.py`、`workflow_batch_runner.py` 和 renderer：

- 从 `workflow_complete_candidate` 删除 `evaluate_refit_with_policy`、PolicyState 写入、最终 verdict 和 lifecycle 写入，只保留 comparator evidence。
- runner 对每个真实 candidate 恰好调用一次 `workflow_evaluate_refit`；该调用负责唯一 `refit_decision`、Policy transition、`next_decision` 和 candidate lifecycle。
- 对同一 `candidate_ref` 的重复调用返回已落盘结果，不重复追加 `candidate_trials`、event history 或 action summary。
- runner 只消费 `next_decision.resolved_decision`：拒绝后直接进入同队列下一 candidate；接受后执行 `ANALYZE_NEW_BASELINE`；不得重新分析旧 baseline 后依靠本地 fingerprint 才发现重复。runner 侧 fingerprint 保留为最后一道 fail-closed 防线。
- 明确禁止 runner 从 `action_summary.refit_verdict` 或任何 summary 字符串驱动控制流（baseline 切换、循环走向、停止判断）；这些字符串只能进入人类可读渲染。删除现有从 `action_summary` 读 verdict 的控制通道。
- renderer 必须明确区分“本轮执行的模型动作”“refit 结果”“队列中的下一动作”；展示综合拒绝证据，不再生成相互矛盾的 refit verdict。
- 完成 MCP contract、mock integration、lifecycle、run index、renderer 和故障恢复测试。

### N27：只读 replay、全量验证、provider smoke 和 104 canary

1. 先运行 N23～N26 的相关 unit／contract／integration tests。
2. 使用 v5、N16 和最新 104 artifact 做只读 offline replay，确认能够复现旧缺口，并验证新逻辑不会双评价、不会错误重提候选。不得改写历史 artifact。
3. 运行全量回归；任何失败必须修复并重新执行相关测试和全量回归。
4. 测试全部通过后，执行一个不调用 GALFIT／GalfitS 的 VLM provider smoke test，确认新进程实际读取一致的 base URL，模型可用，响应通过 parser，metadata 和 target coverage 正确。不得输出或落盘 API key。
5. smoke test 失败时停止，不得启动真实拟合。smoke test 通过后，只对 `104` 创建全新 VLM-enabled run；不得 resume 或复用任何历史 lifecycle、PolicyState、Image result、baseline 或最佳轮次。
6. 新 manifest 首次写入后必须断言：

```text
pilot_enabled=true
workflow_mode=multi-band
use_vlm=true
```

7. 104 只可按本文件第 8 节的 MCP 链路执行。完成后立即停止，检查每个 candidate 只有一次 refit evaluation、候选队列历史、逐轮 summary、Working Note、final report、provider timing、run index、artifact index 和 capacity report。
8. 在小鱼儿明确确认前，不运行 `1071`、`1118` 或其余 31 个对象。
9. 完成后更新 `ROADMAP.md`，只能陈述已验证的工程事实。

### N28：bounded agent 消解层（N23～N27 完成并经 104 canary 验证后实施）

2026-10-27 小鱼儿裁定加入。目标：在确定性主干健康之后，把「队列耗尽＋证据不足」类合理停止进一步压缩，使流程在受约束前提下自动完成；agent 是消解层，不是第二决策系统。

前提与顺序：必须先完成 N23～N27 并通过 104 canary，确认确定性层不再因工程缺陷停止。禁止先上 agent 再修缺陷——那会让 agent 兜底工程 bug，掩盖问题并产生低质量决策。

agent 可获得的四类权限（均只允许在确定性层已产出的有限选项集合内选择，输出经 schema 校验的 `agent_recommendation`，由 Policy 合法性校验后才成为 `resolved_decision`）：

1. **候选排序与选择**：队列存在多个安全候选时排序并选下一个试谁；不得发明候选集合外的动作或成分。
2. **`INCONCLUSIVE` 消解**：在限定选项（试拟合仲裁／保守兜底／放弃该 target）中裁定；不得把「不确定」改写为「满足」。
3. **collector 选择**：决定 `COLLECT_EVIDENCE` 补哪个证据（VLM retry／补残差测量／PSF 诊断）及顺序；不得重复同 fingerprint 采集。
4. **parameter_plan 细化**：`REFIT_PARAMETERS` 时在数值层测得的合法范围内给初值／边界调整计划；不得写 lyric 文本，配置仍只能经 `workflow_action_config` 编译。

不可开放的红线：

- 统计硬门（BIC 门、refit 多指标矩阵、verifier、lock 标准）agent 不得调整；agent 只能决定「试什么」，不能决定「什么算好」。
- `PROPOSE_REMOVE` 维持 review-only，直至真实局部残差事实与 A/B remove pilot 通过。
- 工具／schema 失败类停止不交给 agent，按工程错误处理。
- agent 不得做自由证据解释；理由只能引用 numeric／VLM artifact 的既有字段。

安全机制：

- 事后审计而非事前阻塞：每个 agent 消解决策照常 `needs_review=true` 落盘，人工为批量事后审查。
- 预算与幂等：每对象 agent 消解次数设上限（默认值实施时定）；同一停止 fingerprint 只询问一次。
- 决策可追溯：每次消解独立 artifact（输入选项集合、选择、理由、引用的证据字段），可被评测集复算。

验收要求：104 新 canary 分层报告必须区分「确定性层完成」与「agent 消解完成」的轮次；agent 消解轮次的动作、选项集合和理由完整落盘可审计。

## 11. 104 canary 分级验收门

104 canary 必须分级汇报，不能把安全停止等同于完整闭环。

### 11.1 refit 与候选队列合同通过

必须同时满足：

- 每个真实 candidate 只有一次 comparator evidence、一次 `workflow_evaluate_refit`、一次 Policy transition 和一个 candidate lifecycle；
- refit response 明确分离 `refit_decision` 和 `next_decision`，runner 只消费后者；
- refit decision 的 `candidate_ref` 能定位原始 action、target、baseline、evidence 和 decision artifact；
- candidate 被拒绝后，同队列存在下一安全候选时必须继续执行；队列为空时才允许 collect 或 stop；
- VLM 依赖候选在缺少有效 VLM confirmation 时没有生成 lyric 或执行 Image fitting；
- 相同 candidate key 没有重复 refit，不同 target／baseline 没有被错误封禁；
- report 中 Rules、Policy、执行动作、refit 结果和下一 transition 相互一致。

### 11.2 Image 迭代执行链通过

必须同时满足：

- baseline 后至少执行一个真实模型动作，并在其 refit verdict 后产生明确的下一 transition；
- 每个执行动作有不同 lyric、不同 result FITS、candidate manifest、完整 refit artifact 和 provider timing；artifact validity、MCP 成功和真实 optimizer convergence 分别记录；
- VLM target coverage 状态与缺失 target 一致，`OK` 不掩盖部分覆盖、拒绝或截断；
- 任何 `COLLECT_EVIDENCE` 都有新输入并且只执行一次；没有新证据时正确停止；
- 每轮 summary、Working Note 和 final report 不打开 JSON 也能说明模型、证据、规则、动作、评价和下一步；
- `1071`、`1118` 和其余 31 个对象的拟合调用数为 0。

### 11.3 安全停止门通过

如果 104 的真实科学证据不足以收敛，允许正确结束为 `STOPPED_NEEDS_REVIEW`。此时必须保留有效 Image 审计结果，最终报告明确写 `SED: NOT_RUN`、`Image-SED: NOT_RUN` 和停止原因。这只表示 refit／队列合同、Image 迭代链或安全停止逻辑中实际达到的层级通过，不能称为完整 workflow 闭环。

### 11.4 完整 workflow 闭环通过

只有 Image 真实达到 `CONVERGED`，存在 verifier `PASS`、`lockable=true`、best round `LOCKED` 的 artifact 引用，并在此后成功执行 SED 和 Image-SED，才能称为 104 的完整 workflow 工程闭环通过。不得降低 Rules、refit comparator、verifier 或 lock 标准来强迫 104 满足本项；若 104 合理地停在 review，必须准确报告已通过的验收层级。

以上任何工程验收都不等同于科学上确认全局最优。

## 12. 保留记录

最新 104 canary `jwst0831-20260910T095811Z`、N16 104 run、v5 artifact 和旧三对象 artifact 继续作为只读 renderer／replay fixture。它们不得 resume，不得修改其 lifecycle、PolicyState、最佳轮次或科学结论。科学输入 FITS、sigma、mask、PSF 和基础 lyric 均不在本阶段删除范围内。
