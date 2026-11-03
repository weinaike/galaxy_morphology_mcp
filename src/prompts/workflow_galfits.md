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
3. **首次拟合（确定性）**：把根 lyric 原样拷贝为星系主目录 `_iter1.lyric` → `run_galfits_image_fitting(config_file=<_iter1.lyric>, extra_args=["--fit_method","ES"])` → `beam_record_fit(galaxy_dir, action_id="", round_status_file=<workplace>/round_status.json)`。
   - 引擎按引用登记（`inputs.lyric` / `outputs.summary_files` 自动映射），解析 gssummary（free+fixed 两段）为 arcsec 库存，BIC 直接作为状态指标。

### 步骤 1. 主循环（在终止条件触发前持续）

```
while beam_status 显示队列非空且预算未耗尽:
```

a. **survey_round(galaxy_dir)**：引擎组装本轮请求（mb 提示词 + digest + 每波段拟合表）并调 VLM——
   - VLM 直接读对比图（各波段 Original/Model/Residual/1D 面板），给出 Phase 1.5 物理性判定（视觉半）与 1–4 个候选（JSON 契约：长度 px + `panel_band`，PA 为 sky-PA 按罗盘读）；
   - 引擎侧：mb schema 校验（解空间/多重性/两机制/arcsec Re 链 3% 预检/panel_band 必带/mandate 不可调）、mech 表（存在性下限、Re 链、μ0、bar_identity 等，arcsec 口径）合并为最终 verdict（mech hard 一票否决 PASS）、cells/class caps/账本去重后入队。编排模型不做任何打分或去重。

b. **apply_candidate(galaxy_dir, action_id=<队首>)**：引擎转录——
   - 未声明参数一律热启动回填父 gssummary 收敛值；结构行五元组一律默认边界集（候选只准声明初值）；
   - 类型强制：disk n=1 fixed、bar n=0.5 fixed、bulge n 创建即自由；`add(agn)` 产出 N 块反简并模板（Na10 固定、Na26 唯一流量自由度、Na11 固定 + Na18=4）；
   - K≥2 中心成分自动生成 `iter{n}.constrain`（成对绑定到 disk；AGN 用 `xcen_<Na1>` 名）；
   - 产出 `_iter{n}.lyric`（若 lyric 引用旧机器绝对路径，引擎按 basename 回落到星系目录解析图像/PSF）。

c. **run_galfits_image_fitting(config_file=<_iter{n}.lyric>, extra_args=["--fit_method","ES"] (+ "--parconstrain","iter{n}.constrain" 当返回携带))**。

d. **beam_record_fit(galaxy_dir, action_id, round_status_file=<workplace>/round_status.json)**：登记新状态、运行 mech 检查、按 BIC 更新队列/s*/停滞计数；返回的 `termination` 字段指示是否停。

e. 失败处置：run_galfits 异常 → `beam_mark_failure(galaxy_dir, action_id)`，回 a。

### 步骤 2. 终止（任一满足即停；`beam_status` 直接给出）

- 队列空；拟合数达 N_max（默认 7）；停滞达上限（默认 3）。

### 步骤 3. 收尾与锁定

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
