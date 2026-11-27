# workflow_galfits_s1 — 专家指导的多波段（GalfitS）星系 Beam Search 拟合工作流

要求严格遵循工作流开展星系拟合分析工作。
本 workflow 在**多波段 beam 引擎**上执行（`beam_init(mode="galfits")`，mb 后端 `src/beam_mb/`；束宽 W=5、预算 N_max=7+1 次保留 F1 仲裁、格子去重、class 配额——引擎机制由代码执行，agent 不得手工模拟）；专家提供的成分组合 T 作为**搜索空间约束**注入引擎，替代"每轮残差驱动自由探索"的推进方式。
图像分析与拟合执行只能使用 galmcp 中的工具。严禁使用 4_5v_mcp 的相关工具。
系统规定的规范（CLAUDE.md 的解空间定义、`.constrain` 同心规范、锁定准则）是你需要遵循的，严禁私自更改。

**多波段契约（与单波段 s1 的三点差异，全程生效）：**
1. **配置格式 `.lyric`**：拟合调用是 `run_galfits_image_fitting(config_file=<lyric>, extra_args=["--fit_method","ES"] (+ "--parconstrain","iter{n}.constrain"))`——不是 `run_galfit`；结构校验是 `check_lyric_file`（五元组语义，min==max 退化区间禁止）；每轮转录产物是 `_iter{n}.lyric`（+ K≥2 时 `iter{n}.constrain`）。
2. **单位契约**：VLM 读写**某一波段面板的 px 并标注 `panel_band`**（`re_px / q / pa_sky_deg / dx_px / dy_px`），px↔arcsec 转换由转录层完成——编排模型与 VLM 一律不手写 arcsec；引擎内部库存为 arcsec（digest 展示 arcsec + 参考波段 px + 每波段星等）。
3. **PA 约定**：**sky-PA**（北 = 0°，逆时针向东，按面板罗盘读数）——多波段流程**不适用**单波段的 N=+Y 约定，不得沿用 GALFIT 习惯。
另外：AGN 一律是 **N 块**（psf 在多波段的化身，`structure_name: "agn"`）；sky 是 I 块固定值，不参与拟合；`--readsummary` 禁用（热启动由转录层从 gssummary 全段回填）。

## 专家指定的成分组合（expert_components）

本 workflow 在调用时除传入根 `.lyric` 文件路径外，还会接收人类专家提供的目标星系形态结构组成（expert_components）。
专家成分组合的格式为：**`成分(候选模型,候选模型,...)`**，多个成分之间用英文逗号分隔。

**示例：** `bulge(sersic,psf), disk(sersic), bar(sersic)` 含义为：
- bulge 成分：可使用 sersic 或 psf 模型拟合，在候选模型之间择优选用
- disk 成分：使用 sersic 模型拟合
- bar 成分：使用 sersic 模型拟合

**专家提供的成分组合：**
{expert_components}

### T 到引擎槽位的翻译规则（mb 解空间：disk / bulge / bar / agn(N 块) / singlesersic，以及 sersic_f——m=1 傅里叶载体，仅限主 disk）

- `bulge(sersic,psf)` → bulge 槽位（sersic，n 自由）；候选模型中的 **psf 即塌缩替换通道**（Bulge Re 塌缩 <0.5 px（参考波段）时，`remove(bulge)+add(agn)`（N 块）是合法的候选模型切换）
- `disk(...)` → disk 槽位（sersic，n=1 引擎固定——多成分模型的盘一律如此）；若 T 的**唯一**成分是盘且专家候选模型写 sersic → singlesersic 槽位（单成分 regime，n 自由）
- `bar(sersic)` → bar 槽位（n=0.5 固定）
- `agn(psf)` / `nucleus` / `Nucleus` → AGN 点核（**N 块**，mb 中 psf 的唯一等价物；主星系中心点源角色专属）
- 偏心 / `Fourier` / `F1` → 主 disk 的 `sersic → sersic_f`（m=1）类型切换：surveyor 可在前 N_max 次内依偶极/单侧不对称残差证据直接提出（计入预算）；终止时 best 仍无 sersic_f disk 才走 7+1 保留仲裁（am > 0.02 且 BIC 不恶化才保留）
- T 中的 `lens` / `edgedisk` / `companion` / `outerdisk` **不在 mb 解空间**：在报告中明确记录该冲突，按 T 的合法子集继续（mb 校验器直接拒绝这些成分，强行添加只会浪费预算）；侧向星系用普通 disk（n=1 固定、q 自由）拟合

**约束语义（硬）：** 最终模型必须落在 T（或其塌缩/单成分等价形态）内；不允许添加 T 之外的成分；当图中出现 combo==T 的轮次后，不再增删成分、只调参数与约束。若拟合证据与 T 强烈矛盾（例如 T 无 bar 但棒特征压倒性），**不得擅自改 T** —— 照常按 T 完成拟合，并在最终报告中完整记录矛盾证据（专家保留裁决权）。

## workflow

### 阶段一. 初始化与专家约束注入（每星系一次）

* **查看星系目录：** 确认所需的文件是存在的——各波段 FITS 图像、掩膜文件、PSF 文件、根 `.lyric` 等（图像/PSF/掩膜须在星系主目录，旧绝对路径按 basename 回落）。
* **解析 T：** 按上面的翻译规则解析 `{expert_components}`，得到目标槽位集合 T 与候选模型映射；同时确定**禁用清单 F = mb 解空间槽位 − T**（例：T 不含 bar 且不含 psf/agn 候选 → F 包含 "bar" 与 "agn"；T 的 bulge 带 psf 候选 → "agn" 不禁，塌缩替换通道保留；T 为单盘成分 → singlesersic 是其合法单成分形态，不禁）。
* **渲染原图：** `render_original(config_file=<根 .lyric>)` 渲染原图（含等照度线/掩膜/罗盘），得到开局图。
* **初始化图：** `beam_init(galaxy_dir, root_feedme=<根 .lyric>, mode="galfits", stage1_morphology="<你对原图的形态学自由文本判断>")`——引擎执行 `check_lyric_file` 静态门 + 严格不变量双校验，逐波段测量 PSF FWHM/A_psf 与 pixscale 写入图 meta，解析根 lyric 初值库存为 A.0（占位名单成分在图内归一化为 singlesersic，文件不改写）。形态学判断是自由文本，**不调用任何检测工具**。
* **注册专家约束（关键一步）：** `beam_set_constraint(galaxy_dir, action="add", text="专家目标成分组合 T={...}，禁止 T 之外的结构", forbid_structures_json=F)` —— F 中结构的 add/tune/skeleton/reclassify 候选**和开局骨架**都会被代码在入队与出队两层直接丢弃（E_TEMP_CONSTRAINT），且每轮 VLM digest 自动携带 [Temporary constraints] 块——这是 T 成为硬约束的机制保证。

### 阶段二. Beam Search 主循环（T 注入每轮侦察）

1. **开局：** `survey_round(galaxy_dir, state_label="A.0", opening_image=<开局图>, directives="专家指定成分组合 T={<T 内容>}；开局骨架只能由 T 的子集构成，首选直接给出完整 T 的骨架（disk→bulge→bar 顺序；单成分 T 给 singlesersic），非 T 成分一律不出现")` → 对返回的 `next_candidate` 执行 `apply_candidate` → `run_galfits_image_fitting`（extra_args 按 apply 返回的 message 逐字使用，含 `--parconstrain` 当有 constrain）→ `beam_record_fit`。开局骨架候选自带全部初值（px + panel_band，sky-PA），无需 gssummary 回填；骨架字母表之外（AGN/F1）不进开局，靠后续残差证据入列。
2. **逐轮循环（每轮机械执行，不得跳步、不得手工改写候选）：**
   - `apply_candidate(galaxy_dir, <next_candidate>)` → `_iter{n}.lyric`（+ `iter{n}.constrain`）
   - `run_galfits_image_fitting(config_file=<apply 返回的 lyric 绝对路径>, extra_args=["--fit_method","ES"] (+ "--parconstrain","iter{n}.constrain"))`
   - `beam_record_fit(galaxy_dir, action_id=<该候选>, round_status_file=<本轮 workplace>/round_status.json)` —— **按引用记录，严禁手抄 run_galfits 返回值**；记录被拒（E_RECORD_*）时按错误信息用引用形态重录
   - `survey_round(galaxy_dir, directives="目标成分组合 T={<T 内容>}；候选动作只允许 T 内成分的添加/调参，combo==T 已达成时只允许参数与约束调整")`
   - 读返回的 `termination` / `next_candidate`；`stop=true` 或预算耗尽则进入阶段三
   - 异常处置：survey 返回 **E_GRAPH_STATE** → 父态损坏，按错误信息 `beam_repair_artifacts` 修复后重试（勿重调 VLM）；run_galfits 异常 → `beam_mark_failure` 后继续下一候选；survey 校验重试耗尽/VLM 出错不致命——重试一次，再失败则照常登记该轮、用现有队列继续（`beam_status` 给出队首）；`termination.suspended_by_never_executed == true`（仅限预算内）时按其列出的未执行 inventory 花费队首，预算耗尽则停
3. **预算与去重由引擎执行：** N_max=7 次探索拟合 + 1 次保留 F1（记在 n_f1_reserved，不计入 7）；同格子候选、class 配额（a/b/c=2/4/5）、队列截断、热启动回填、默认边界集、类型强制（disk n=1 / bar n=0.5 / bulge→disk 转换）均由代码裁决——不要因为"专家目标还没到"而绕过引擎手工加轮。
4. **提前锁定（专家模式的效率条款）：** 当图中已存在 **combo==T、verdict=PASS**、且锁定准则全部满足的轮次时，可不等预算耗尽直接进入阶段三（省下的预算留给 F1 仲裁；用 `beam_status` 确认队列中已无 T 内的未执行方向）。
5. **锁定：** 优先在 **combo==T** 的轮次中择优（`beam_status` 的 best_state 与 State ledger）：s* 的 verdict 为 PASS、mech 表无未处置 hard 项、同心 constrain 合规（决策日志中该轮带 `--parconstrain`）、成分完整性与参数状态对照归档核验；未通过则在剩余预算内以 `survey_round(directives="<修复约束文本>")` 开定向修复轮；`beam_grant_repair_budget` **仅限用户显式授权，agent 不得自主调用**——预算耗尽且检查不过时，向用户报告失败项，由用户决定。

### 专家中断协议（拟合过程中的额外指令）

专家可能在任意轮次之间插入新指令。收到中断指令后**必须先落图、再继续拟合**，不得只记在对话上下文里（VLM 无状态，上下文信息不会自动传给后续请求）：

- **结构禁令型**（"不要再加某成分"）→ `beam_set_constraint(action="add", text=<原文>, forbid_structures_json=[...])`：此后每轮 VLM digest 自动携带 [Temporary constraints] 块，且入队/出队两层代码拦截——已在队列里的违规候选会在出队时被丢弃，指令立即生效。
- **自由文本指导型**（参数方向、形态判断等）→ `beam_set_constraint(action="add", text=<原文>, forbid_structures_json=[])`：文本持久化进图，从下一次 survey 起每轮随 prompt 注入 VLM。
- **解除指令** → `beam_set_constraint(action="remove", ...)`。
- 落图之后的每一轮 `survey_round`，把专家最新指令的要点并入 `directives` 一并传递（双通道强调）。
- 中断指令与 T 冲突时（例如要求加 T 外成分）：**不执行**，向专家说明冲突并等待确认——T 的变更只能由专家显式重申新的 expert_components。

### 阶段三. F1 仲裁、物理意义复核与报告撰写

* **F1 仲裁（经验仲裁，7+1 的保留位；锁定前必做）：** mb 的 F1 形态是**主 disk 的 `sersic → sersic_f`（m=1）类型切换**——只有 sersic_f 能承载 F1，且只挂在主 disk 上。终止时最优状态仍不含 sersic_f disk 且预算未耗尽时：先调用 `fourier_mode_analysis(image_file=<最优轮对比图>, source_id=<星系名>)`（该工具只返回 m=1 初始参数 am/theta_m + 置信度，不输出 yes/no 结论），然后 `beam_enqueue_f1(galaxy_dir, amplitude=<am>, phase_deg=<theta_m>)` —— 引擎在最优状态上构造重建对 `remove(disk)+add(disk, sersic_f, ...)`（拟合 disk 参数 arcsec→参考波段 px 回填、`r_in≈0.2·Re / r_out≈0.6·Re` 播种、am/theta_m 热启动；每 run 恰好一次，计数在 n_f1_reserved，N_max 之外）→ `apply_candidate` → `run_galfits_image_fitting` → `beam_record_fit`（引用形态），由 **BIC 数值裁决**去留：BIC 改善则保留 F1 轮为新最优，否则保持原最优（拟合的 am 是报告事实，不是门限；另有 am > 0.02 的物理性参考）。若最佳已含 sersic_f disk 或预算耗尽，跳过。保留槽永不重跑。
* **物理意义分析：** 按系统消息的"Physical-meaning analysis"逐参数复核 s* 每个成分（mb 单位为 arcsec/sky-PA；N 块 AGN 的坍缩核球替换规则同单波段 psf）。任何非物理情形 → 以 `survey_round(directives=...)` 开定向修复轮（预算内；计数持续）。
* **奥卡姆剃刀（仅 AGN/F1 适用）：** s* 含 AGN 且 ΔBIC（gssummary BIC）< 10 → 以 `remove(AGN)` 为最高优先候选开验证轮（BIC 变差则保留）；F1（sersic_f disk）拟合 am > 0.02 即物理有效，**禁止以 BIC 为由移除**，仅在发散/非物理时验证移除。
* **报告撰写：** 使用 `write_file` 工具将分析结论写入当前星系目录：`analysis_report_<星系名>.md`（**本报告是阶段四~五 SED 拟合的硬门**——无报告时 `run_galfits_sed_fitting` 直接拒绝）。**报告骨架（规范性硬要求——五个一级标题按此措辞与顺序逐字出现，下游按标题定位）：**
    1. `## Generation time`——日期时间。
    2. `## Preprocessing information`——掩膜说明与背景设定（mb：sky 为 I 块固定值，不参与拟合）；本节内附 **`### Expert-specified components` 小节**：expert_components 原文、翻译后的 T、每个成分最终采用的模型、T 与拟合证据的矛盾记录（如有，附具体残差/BIC 证据）。
    3. `## Iteration log`——按束图多分支结构组织（可用 `beam_export_note` 渲染）：每分支每轮的成分增删证据（多模态视觉判断记录）、参数分歧与回滚、被引擎过滤方向的要点（cell-dup / class-cap / 约束丢弃，可引 `beam_status` 决策日志）。
    4. `## Best-result locking analysis and result`——锁定理由（准则逐条）、最终采用参数表（arcsec 结构参数 + 每波段星等 + am/theta_m 当有 F1）、形态学物理意义解读（如：成分 A 是经典盘结构、成分 B 是致密核心）。
    5. `## Attachment index`——最优轮的 `output/` 子目录路径（`best_state.archive_dir`）、最终 `_iter{n}.lyric`、最终 `all_bands_comparison.png` 路径。
    文档最后必须以一个围栏 ```json 块结尾（规范性 schema，下游按字段解析）：
    ```json
    {"best_turn":"<最优轮 output/ 子目录名，如 20261103_170350__iter1>","components":["<物理成分数组>"],"galaxy_type":"edge-on/face-on/elliptical"}
    ```
    - `best_turn`：**字符串**，`output/` 下的子目录名（非完整路径、非 lyric 名）；
    - `components`：**字符串数组**，每成分一项，取自固定词汇 `[Disk,Bulge,Bar,Agn,Companion,Fourier,SingleSersic,Lens,OuterDisk]`（F1 = sersic_f disk 记作 `Fourier`；本分支解空间实际只产出 Disk/Bulge/Bar/Agn/Fourier/SingleSersic）；
    - `galaxy_type`：`edge-on` / `face-on` / `elliptical` 之一；盘星系的 Disk 成分 q<0.3 则认定为侧向盘（edge-on）。

### 阶段四~五. SED 拟合与联合拟合（多波段独有；**前置条件：阶段三的 analysis_report 已写入**）

**硬门（引擎强制）**：`run_galfits_sed_fitting` 在星系目录无 `analysis_report_<星系名>.md` 时直接拒绝——图像拟合阶段未闭环（报告是规范性交付物，下游按其骨架/JSON 解析）。先完成阶段三的锁定核查与报告写作，再进入本阶段。

1. **阶段四（SED 拟合）**：`run_galfits_sed_fitting(config_file=<锁定轮的 .lyric>, image_fitting_workplace=<锁定轮 output 目录>, extra_args=["--fit_method","ES"])`——基于最优图像拟合结果做 SED 拟合，产出用于联合拟合的新 lyric；引擎同时把 best state 的同心 `iter{n}.constrain` 原样拷入新工作区（结构未变、组件未改名，直接复用；返回 `constrain_carried` 字段）。
2. **阶段五（联合拟合）**：`run_galfits_image_sed_fitting(config_file=<阶段四返回的新 lyric>, extra_args=["--fit_method","ES"])`——同心约束由引擎自动附加（工作区内同轮 constrain 优先；显式传 `--parconstrain` 时以显式为准），无需编排模型手动传递。
3. 两阶段不进入束搜索空间（结构已锁定，只做 SED 自由度）；若联合结果暴露结构问题，回到阶段二以 directives 方式开修复轮。

## 待分析星系

{argument}
