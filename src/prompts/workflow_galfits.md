# 多波段（GalfitS）星系拟合工作流 — 引擎驱动版

本流程与单波段 GALFIT 引擎流程（`src/beam/`）**完全同构**：束搜索的全部机械化职责（候选校验、去重/cells/class caps、队列老化、物理性 mech 判定、预算与终止、状态图）由代码引擎承担，编排模型只按本流程调用工具。多波段相对单波段的差异只有三点：

1. 配置格式 `.lyric`（arcsec 五元组、N 块 AGN、`.constrain` 同心约束）——由引擎的 mb 后端（`src/beam_mb/`）机械处理；
2. 单位契约——VLM 读/写**某一波段面板的 px 并标注波段**，px↔arcsec 转换由转录层完成；
3. 拟合完成后的**阶段三~五**（SED 拟合、图像-SED 联合拟合）。

**bar/lop 检测已退役（2026-11-03，与单波段 2026-10-09 的退役对齐）**：`detect_galfits_bar_lopsidedness` 不再进入任何阶段；不存在"检出 → 触发候选"的规则。所有候选（包括 bar、Fourier）一律由 VLM 在 `survey_round` 中直接读残差图生成，并提供每个成分的参数。旧工具 `generate_beam_actions`（Markdown 契约 + depth-1 触发规则）同步退役，不得调用。

**解空间（与单波段现分支一致）**：disk / bulge / bar / agn(N 块) / singlesersic。lens、outerdisk、edgedisk、companion、sersic_f/Fourier 变体在解空间之外（VLM 可将其描述为未拟合残差背景，不得转为候选；F1 的 mb 形态 = disk 的 `Pa2` 切 `sersic_f`，其保留槽机制落地前禁用）。

**真源**：`beam_state/graph.json`（队列、s*、账本、verdict、决策日志、临时约束全部在图里）。`working_note.md` 不是每轮职责——需要人读的审计导出时调 `beam_export_note`。

## 阶段二：图像拟合束搜索（引擎循环）

### 步骤 0. 初始化（每星系一次）

1. `render_original(config_file=<根 .lyric>)` 渲染原图（含等照度线/掩膜/罗盘），得到开局图。
2. `beam_init(galaxy_dir, root_feedme=<根 .lyric>, mode="galfits", stage1_morphology="<你对原图的形态学自由文本判断>")`。
   - 引擎执行：`check_lyric_file` 静态门 + 严格不变量（I/P/N 块的装配完整性）双校验；逐波段测量 PSF FWHM/A_psf 与 pixscale 写入图 meta；解析根 lyric 的初值库存为 A.0。
   - 形态学判断就是自由文本（可含你看到的 bar/盘/核球描述），**不调用任何检测工具**，检测结论不构成候选触发条件。

### 步骤 1. 开局（与 workflow_galfit.md 同构：VLM 先看图 → 转录 → 才拟合）

初始状态 A.0 是**未拟合**的根（根 lyric 结构的机械拷贝，BIC=⊥）——**A.0 永不直接拟合**。

1. **Round-0 opening survey（开局由 VLM 出题）**：`survey_round(galaxy_dir, state_label="A.0", opening_image=<开局图>)` ——surveyor 读原图 + 引擎注入的根 lyric obj0 参考参数，返回 **1–3 个排名 skeleton 候选**（每个候选用单个 `skeleton` 原语声明整份光度模型及每成分参数 `re_px / q / pa_sky_deg / dx_px / dy_px / panel_band`；字母表 = singlesersic 单独 XOR 含 disk 的 {disk, bulge, bar} 子集；组件数由 VLM 依图像决定，无固定阶梯）。引擎校验（解空间/两机制/arcsec Re 链预检）后以 A.0 为父入队并返回 `next_candidate`（首位骨架）；无 verdict 结算（尚无拟合）。obj0 参考参数是 **seeding 参考**（总量级/Re 量级/q/PA/中心的锚点），不是照抄模板；其余骨架候选带 `opening_alternate` 标记留队——主线不佳时束搜索花费的未试方向，豁免截断与老化淘汰。
2. **首拟合（标准循环接管）**：`apply_candidate(galaxy_dir, action_id=<next_candidate>)`（转录出 `_iter1.lyric` + K≥2 时 `iter1.constrain`；骨架自带全部初值，无 gssummary 回填）→ `run_galfits_image_fitting(config_file=<_iter1.lyric>, extra_args=["--fit_method","ES"] (+ "--parconstrain","iter1.constrain"))` → `beam_record_fit(galaxy_dir, action_id=<该骨架 id>, round_status_file=<workplace>/round_status.json)`（按引用登记：`inputs.lyric` / outputs 自动嗅探；gssummary free+fixed 两段解析为 arcsec 库存，BIC 为状态指标）→ `survey_round(galaxy_dir)`（第一轮普通 survey：结算 A.1 的 verdict 并入队残差驱动候选）。
   - 首拟合失败（无 summary/对比图）：骨架 seeding 已坏——不继续循环；检查 opening survey 工件、按其信息修复（或带 directives 重跑 opening survey），从 1 重来。

### 步骤 2. 主循环（在终止条件触发前持续）

```
while beam_status 显示队列非空且预算未耗尽:
    1. next = <上一次 survey_round 返回的 next_candidate>        # 引擎排序
    2. apply_candidate(galaxy_dir, action_id=next)
       → _iter{n}.lyric (+ iter{n}.constrain)；R0 复检（class caps / cells /
         临时约束）、热启动回填、默认边界集、类型强制全部由引擎完成
    3. run_galfits_image_fitting(config_file=<_iter{n}.lyric>,
          extra_args=["--fit_method","ES"] (+ "--parconstrain","iter{n}.constrain"))
    4. beam_record_fit(galaxy_dir, action_id=next,
          round_status_file=<workplace>/round_status.json)
       → 登记状态 + mech 检查（arcsec 口径）+ verdict 门控 + BIC 更新 s*/队列/停滞
    5. survey_round(galaxy_dir)
       → VLM 读对比图给 Phase 1.5 判定（视觉半）+ 1–4 候选（px + panel_band，
         sky-PA 罗盘）；引擎合并 mech 表（hard 一票否决 PASS）、schema 校验、
         cells/class caps/账本去重后入队，返回 next_candidate
    失败处置：run_galfits 异常 → beam_mark_failure(galaxy_dir, action_id)，回 1。
```

循环纪律（与单波段一致）：**拟合成功后永不跳过 survey_round**（诊断回路原则——FAIL/BIC 反升轮同样要 survey，其后续候选就是修复路径）；`survey_round` 校验重试耗尽/VLM 出错不致命——重试一次，再失败则照常登记该轮、用现有队列继续（`beam_status` 给出队首）；`termination.suspended_by_never_executed == true`（仅限预算内）时按其列出的未执行 inventory 花费队首，预算耗尽则停（未执行 inventory 不构成修复预算理由）。

### 步骤 3. 终止（任一满足即停；`beam_status` 直接给出）

- 队列空；拟合数达 N_max（默认 7）；停滞达上限（默认 3）。

### 步骤 4. F1 仲裁（7+1 保留槽；锁定前必做，与单波段同构）

mb 的 F1 形态是**主 disk 的 `Pa2` 类型切换 `sersic → sersic_f`（m=1）**——只有 sersic_f 能承载 F1，且只挂在 main disk 上（VLM 不可直接提议；此槽保留给代码）。当最优状态不含 sersic_f disk 时：

1. `fourier_mode_analysis(image_file=<最优轮对比图>, source_id=<星系名>)` ——从最优残差读 m=1 初始参数（am / theta_m 热启动）。
2. `beam_enqueue_f1(galaxy_dir, amplitude=<am>, phase_deg=<theta_m>)` ——引擎在最优状态上构造重建对 `remove(disk)+add(disk, sersic_f, ...)`：拟合 disk 参数 arcsec→参考波段 px 回填、`r_in≈0.2·Re / r_out≈0.6·Re` 播种、am/theta_m 热启动；每 run 恰好一次，计数在 `n_f1_reserved`（N_max 之外）。
3. `apply_candidate → run_galfits_image_fitting → beam_record_fit(action_id=<f1 id>)` 照常执行。
4. **BIC 仲裁**：`beam_status` 比较 F1 轮与 pre-F1 最优的 BIC——改善则保留 F1 轮为新最优，否则保留原最优（拟合的 am 是报告事实，不是门限）；保留判据另有 am > 0.02 的物理性参考。保留槽永不重跑。

### 步骤 5. 收尾与锁定

1. `beam_status(galaxy_dir)` 复核：s* 的 verdict 为 PASS、mech 表无未处置 hard 项、同心 constrain 合规（决策日志中该轮带 `--parconstrain`）。
2. 六准则（成分识别完整、1D/2D 残差干净、参数关系物理、无过度约束、候选已生成过、指标最优）由编排模型对照图与归档直接核查；不满足 → `beam_grant_repair_budget` + `survey_round(directives=...)` 定向修复轮。
3. 需要人读的审计记录：`beam_export_note(galaxy_dir)`。

### 诊断回路原则（引擎保证，编排模型知悉）

每一轮成功拟合都会无条件触发一次 survey_round（VLM 视觉诊断是核心回路，与 BIC 升降无关——BIC 反升常是初始化次优，残差携带修正方向）。VLM 侧的修复菜单：物理性 FAIL → 至少一个修复候选（Re 链反置 → RECLASSIFY；通量存在性下限 → remove/mag 再分配；bar_identity → reclassify(bar→bulge)，仅当残差证有时追加更大 Re 的 add(bar)；μ0 反置 → mag 再分配或身份重标识）。机制见 `beam_survey_prompt_galfits.md`。

## 阶段三~五：SED 拟合与联合拟合（多波段独有；引擎束搜索结束后执行）

1. **阶段三（SED 拟合）**：`run_galfits_sed_fitting(config_file=<锁定轮的 .lyric>, image_fitting_workplace=<锁定轮 output 目录>, extra_args=["--fit_method","ES"])`——基于最优图像拟合结果做 SED 拟合，产出用于联合拟合的新 lyric。
2. **阶段四（联合拟合）**：`run_galfits_image_sed_fitting(config_file=<阶段三返回的新 lyric>, extra_args=["--fit_method","ES"])`。
3. 两阶段不进入束搜索空间（结构已锁定，只做 SED 自由度）；若联合结果暴露结构问题，回到阶段二以 directives 方式开修复轮。

## 常量与注意（引擎行为，编排模型不必干预）

- 预算：N_max=7、stagnation=3、W=5、class caps a/b/c=2/4/5、修复预算上限 4（可在 beam_init 覆盖）。
- 单位：引擎库存 arcsec；digest 展示 arcsec + 参考波段 px + 每波段星等；VLM 任何 px 值必须带 panel_band。
- `--readsummary` 禁用（只解析自由参数段）；热启动由转录层从 gssummary 全段回填。
- lyric 五元组永不允许 min==max（非退化区间写法，`check_lyric_file` 强制）。
- 图像/PSF/掩膜文件须在星系主目录（旧绝对路径按 basename 回落）。
