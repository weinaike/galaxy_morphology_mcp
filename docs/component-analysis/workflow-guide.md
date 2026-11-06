# 多波段星系成分分解 Workflow：组内维护指南

日期：2026-11-03
读者：共同维护本仓库、做星系成分分解的组内同事
范围：当前结构化 workflow（分支 `codex/component-analysis-redesign`）的启动方法、模块地图、规则位置与数据流。本文只描述现状；设计沿革与裁定历史见 `workflow-redesign-execution-plan.md`。

## 1. 与旧版 workflow 的区别（一句话版）

旧版：你（agent）逐轮读 prompt、看图、写下一轮 `.lyric`、调拟合工具——决策在你脑子里。
新版：**决策由仓库里的确定性规则层做出**，每一步落盘为可校验的 JSON artifact；你只负责启动和看报告。agent／VLM 只能提供受控证据，不能写配置、不能绕过判定。

## 2. 怎么启动（单对象拟合）

### 2.1 全自动方式（推荐，等价于旧版"给一个 .lyric 跑到停"）

```bash
cd /home/www/2026/galaxy_morphology_mcp
./batch_analyze_galfits_workflow_pilot.sh \
    /home/www/2026/GALFITS_examples/jwst0831 \   # 数据根目录（含各对象子目录）
    /tmp/my-run-$(date +%Y%m%d) \                # 运行状态目录（每次新跑换新的）
    --object-id 104                              # 只跑这一个对象
```

前提条件：

- 对象目录下恰好一个基础 `.lyric`（不含 `_iter`），science/sigma/mask/PSF 齐全且逐波段网格一致（preflight 会检查）；
- `.env` 已配 `OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL`（VLM 证据用；没有也能跑，规则自动降级纯数值）；
- `VLM_TIMING_LOG=1`（use-vlm 模式必需）。

启动器固定使用：多波段模式、`--use-vlm`、`--fit_method ES`、最多 10 轮、串行 GPU。先 `--dry-run` 可以只做输入检查不拟合。

常用变体（追加参数会透传给 runner）：

```bash
./batch_analyze_galfits_workflow_pilot.sh <数据根> <RUN_DIR> --object-id 104 --resume   # 断点续跑
./batch_analyze_galfits_workflow_pilot.sh <数据根> <RUN_DIR> --object-id 104 --max-rounds 20
```

底层等价命令（跳过包装脚本）：

```bash
COMPONENT_ANALYSIS_WORKFLOW_PILOT=1 VLM_TIMING_LOG=1 \
/home/www/ENTER/envs/galfit/bin/python src/tools/workflow_batch_runner.py \
  --root <数据根> --run-dir <RUN_DIR> --mode multiband \
  --pilot 1 --use-vlm --object-id 104 --resource-profile serial-gpu --max-rounds 10
```

### 2.2 RUN_DIR 与结果目录的分工

命令里的第二个参数（如 `/tmp/my-run`）是**运行状态目录**：断点续跑状态、运行期逐轮工作副本、批次清单——**不是科学结果**。科学产物全部落在对象目录 `output/` 下（见下表）。关键审计产物每轮结束即复制到持久目录，因此 RUN_DIR 在运行结束且不再 `--resume` 后可整体删除；中断续跑则保留它并用 `--resume --object-id <ID>` 指向同一目录。每次新跑务必用新的 RUN_DIR（设计要求，禁止复用旧 run 状态）。

### 2.3 结果在哪里

```text
<数据根>/<对象>/output/
  workflow/<run_id>/
    configs/                   # 配置与拟合输出（见下表）
    artifacts/                 # 结构化审计 JSON（见下表）
    summaries/                 # ★ 人类可读报告（见下表）——先看这里
    run_index.json             # 轮次索引（含 final_accepted_round_id / final_config_file）
    capacity_report.json
```

三个子目录的分工（以 205 为例）：

| 子目录 | 内容 | 典型文件 |
|---|---|---|
| `configs/` | **每轮生成的候选配置与真实拟合输出**。根层是各轮候选 `obj_<ID>_baseline_iterN.lyric` ＋同名 `.constrain`（约束）＋`timing_log.md`（VLM 计时）；`configs/output/` 下是每个 lyric 的 GalfitS 拟合输出（timestamped 目录：各波段 result FITS、`.gssummary`、`all_bands_comparison.png`、params）。**无 iter 后缀的目录＝round 0 的 baseline 拟合**（对基础 lyric 的初次拟合，run 的起点）；`iterN` 目录＝第 N 个候选的拟合 | `configs/output/20261030_224745_obj_205_baseline/`（baseline）、`..._iter3/`（第 3 个候选＝disk 接受轮） |
| `artifacts/` | **机器可读的结构化审计 JSON**，按 `round_NNN/` 组织：`workflow_manifest.json`（证据文件索引）、`proposal/`（数值＋VLM 证据）、`resolution/`（规则 trace＋策略决策）、`refit_evidence.json`／`refit_decision.json`（仲裁）、`candidate_lifecycle.json`。用于事后复算，人不直接读 | `round_002/resolution/resolution.json` |
| `summaries/` | **人类可读 Markdown**：每轮 `round_*_component_analysis.md`（当轮证据／规则／决策／仲裁全文）、`working_note.md`（迭代过程累计笔记）、`analysis_report_obj<ID>.md`（最终报告：状态／最终轮次／逐轮时间线／下游门） | `analysis_report_obj205.md` |

**最终选择的是哪一轮**：看 `run_index.json` 顶部的 `final_accepted_round_id` 与 `final_config_file`（显式写明），或最终报告头部的「最终接受轮次」行——即最后一次被 `ACCEPT_REFIT` 的候选（未收敛对象）；收敛对象则以 lock 结果为准。

### 2.4 单步手动方式（调试用）

**前置开关**：结构化工具只在 MCP server 进程带有 `COMPONENT_ANALYSIS_WORKFLOW_PILOT=1` 时注册（`mcp_server.py` 的 pilot 门控）。`.mcp.json` 的 galmcp env 块默认没有这个变量，所以 MCP 菜单里看不到它们——全自动 runner 不受影响（它启动 server 子进程时自动注入）。要手动单步调用，需在 `.mcp.json` 的 galmcp `env` 中加 `"COMPONENT_ANALYSIS_WORKFLOW_PILOT": "1"` 并重连 MCP。

MCP server（galmcp）注册的全套结构化工具，任何 MCP 客户端可逐步调用，与旧版 `workflow_galfits` 的手动节奏对应：

`workflow_capabilities` → `build_workflow_round_manifest` → `workflow_propose_round`（数值+VLM 证据）→ `workflow_resolve_round`（规则+策略出决策）→ `workflow_action_config`（生成新 lyric）→ `run_galfits_image_fitting` → `workflow_compare_residuals`（裁定12 残差对比）→ `workflow_complete_candidate`（数值比较）→ `workflow_evaluate_refit`（唯一仲裁）→ `record_workflow_fit_lifecycle` → …… 收敛后 `workflow_verify_best_round` → `workflow_lock_best_round` → `run_galfits_sed_fitting` → `run_galfits_image_sed_fitting`。

手动方式的关键纪律：**机器动作只能来自 `decision_artifact.resolved_decision`**；`workflow_evaluate_refit` 必须带 schema 合法的 `candidate_ref`；不要从 Markdown／summary 文字反推动作。

## 3. 一轮拟合的数据流

```text
baseline lyric ──run_galfits_image_fitting──▶ result FITS + .gssummary
        │
        ▼
  Round Manifest（显式索引各波段文件）
        ├─▶ 数值证据层：测量事实（尺度/轴比/残差/m1 振幅/边界/中心偏移…）
        ├─▶ VLM 证据层（可选）：受控标签（disk_like / bar_like / independent_source…）
        ▼
  Rules（src/component_analysis/rules.py）：规则 → 候选 + 终止检查
        ▼
  Policy（policy.py）：候选队列、排序、消解 → resolved_decision
        ▼
  动作编译（workflow_bridge.py）：新 _iterN.lyric + 约束文件
        ▼
  候选拟合 → comparator（refit_comparator.py）数值证据
        + VLM 残差对比（cleaner/unchanged/dirtier）
        ▼
  workflow_evaluate_refit：唯一仲裁（接受/拒绝/不确定）→ 队列推进或换 baseline
        ▼
  收敛判定 → verifier → lock → SED → Image-SED（下游门未过则 NOT_RUN）
```

### 3.1 真实例子：1071 的 round_001（拆 disk+bulge 那一轮）

以下全部摘自真实运行（run `jwst0831-20261029T071746Z`，1071 第二轮；round_000 已确认 Disk）。三级缩进引用内是 artifact 原文的精简摘录。

**第一步：Round Manifest ——「这一轮的证据从哪些文件来」的显式索引。** baseline 拟合完成后，runner 把本轮用到的每个文件登记成 manifest（禁止用文件名猜测配对）：

```json
{
  "round_id": "1071_workflow_round1",
  "config_file": ".../configs/obj_1071_baseline_iter1.lyric",
  "result_files": ["..._f444w_result.fits", "..._f410m_result.fits", "...（7 个波段，显式列出）"],
  "summary_file": ".../obj1071.gssummary",
  "comparison_png": ".../all_bands_comparison.png",
  "bands": [
    { "band": "nircam_f444w",
      "science_fits": ".../obj1071_F444Wsci.fits",
      "sigma_fits":   ".../sigma/Sig1071_F444W.fits",
      "mask_fits":    ".../obj1071_F444Wmask.fits",
      "psf_fits":     ".../F444W_hybrid.fits",
      "result_fits":  "..._f444w_result.fits",
      "result_hdus":  {"original_hdu": 4, "model_hdu": 3, "residual_hdu": 0} }
    //…每个波段一条，凭显式路径配对，不按下标
  ]
}
```

**第二步：证据层。** 数值层从 manifest 指向的 FITS／gssummary 测量事实（只测不说），VLM 层给受控标签。本轮关键值：

```text
source_extent_psf_ratio = 10.15   （N1：源比 PSF 延展 10 倍 ✓）
outer_isophote_geometry: pa_scatter=1.13°, q_range=0.056   （N2：几何稳定 ✓）
single_sersic_n = {n: 1.0, at_boundary: false}
original_m1_amplitude = 0.327     （m=1 振幅高——但留给收尾动作，不在本轮处理）
VLM: OK  central=edge_on_disk, candidate_9=clump, …
```

**第三步：Rules —— 逐条规则跑出 trace 和候选。** 每条规则给出 SATISFIED／NOT_SATISFIED／INCONCLUSIVE，满足的产出候选动作（本轮 7 条规则、5 个候选）：

```text
DISK_BULGE_SPLIT_V1:   SATISFIED      → 候选 PROPOSE_ADD bulge（优先级 3，盘已确认必须先拆）
EDGE_ON_LOW_Q_V1:      NOT_SATISFIED  （轴比 0.65 不满足侧视门）
CENTRAL_EXCESS_V1:     NOT_SATISFIED
BAR_STRONG_ISOPHOTE_V1: SATISFIED     → 候选 PROPOSE_ADD bar（优先级 4，排在拆分之后）
FOURIER_M1_CONFOUNDING_V1: INCONCLUSIVE, blocking=true （m1 有混淆，等收尾阶段处理）
COMPANION_NUMERIC_VLM_V1: SATISFIED   → 4 个候选（candidate_21/7/5/11，各 ≥3 波段+VLM 确认）
```

**第四步：Policy —— 排序选一个，其余排队。** 复合排序（可执行性→安全级→优先级）选出 `PROPOSE_ADD bulge`，其余候选留在队列里备后用：

```text
候选队列（本轮结束后）:
  DISK_BULGE_SPLIT_V1  prio=3  SELECTED → 执行
  BAR_STRONG_ISOPHOTE  prio=4  DEFERRED → 排队
  COMPANION ×4         prio=4  DEFERRED → 排队
resolved_decision = PROPOSE_ADD bulge, workflow_status = CONTINUE
```

**第五步：编译＋候选拟合＋仲裁。** `workflow_action_config` 生成 `obj_1071_baseline_iter2.lyric`（新增 bulge 块，Re 按 0.4×disk_Re 初始化、上界=盘 Re），跑 `run_galfits_image_fitting`，然后 comparator＋VLM 残差对比出评价，`workflow_evaluate_refit` 按统一矩阵仲裁：

```text
residual_outcome = improved   （官方 per-band reduced chisq 平均下降）
BIC_gain         = +15694     （≥10 ✓）
新引入边界        = 无         ✓
VLM 残差对比      = 未启用（该 run 早于裁定 12）——现在会先过 dirtier 否决门
────────────────────────────────────────
verdict = ACCEPT_REFIT → next = ANALYZE_NEW_BASELINE
（bulge 进入模型；旧候选队列作废，下一轮从新 baseline 重新出证据）
```

这一轮结束后的模型：disk＋bulge。同一 run 的后续轮次：r002 把 bulge 锁到盘心（同心合规）→ r003 加 bar（BIC +13879）→ 最终 disk+bulge+bar。

**看懂这个例子就看懂了全部机制**：manifest 保证证据可溯源 → 规则只认证据出候选 → 策略排序选一个 → 编译拟合 → 统一矩阵仲裁 → 队列推进或换 baseline。每一步的 JSON 都在 `artifacts/` 里，事后可复算。

## 4. 模块地图（改代码前先看这张表）

| 层 | 文件 | 职责 |
|---|---|---|
| 规则 | `src/component_analysis/rules.py` | ★ 所有科学规则与终止门（详见第 5 节） |
| 策略 | `src/component_analysis/policy.py` | 候选队列/排序/拒绝记忆/幂等/消解；不发明候选 |
| 判定 | `rules.py::evaluate_refit` | refit 接受矩阵（详见第 6 节） |
| 比较 | `refit_comparator.py` | baseline vs candidate 的官方统计比较（BIC、reduced chisq、边界、参数健康） |
| VLM | `vlm.py` / `provider.py` / `decision_service.py` | 受控 prompt 与严格解析；失败自动降级，绝不驱动动作 |
| 动作编译 | `workflow_bridge.py` | 把已批准动作翻译成新 `.lyric` + `.constrain`（模板/边界/同心绑定都在这） |
| 调度 | `src/tools/workflow_batch_runner.py` | 全自动循环、白名单、下游门（SED 必须先 LOCKED） |
| 渲染 | `workflow_summary_renderer.py` | 从 artifact 生成人类可读报告（只读） |
| 契约 | `src/schemas/*.json` | 所有 artifact 的 JSON Schema（决策/清单/证据/生命周期） |

## 5. 规则都在哪里

### 5.1 结构提议规则（`rules.py`，按优先级）

| 规则 ID | 位置 | 触发 | 产出 |
|---|---|---|---|
| `DISK_BULGE_SPLIT_V1` | `_disk_bulge_split_rule` | 盘已确认；或盘未确认但 VLM 见中心致密核（先拆入口） | PROPOSE_ADD bulge |
| `DISK_N1_N2_N3_V1` | `_disk_rule` | N1 延展+N2/N3+VLM 盘标签（或 N1 缺席替代组合） | 确认 Disk（PROMOTE，n 固定 1） |
| `CENTRAL_*` | `_central_source_rule` | 中心超丰+分辨测量 | agn / compact 候选；bulge 添加归拆分规则 |
| `BAR_*` | `_bar_rule` | 等照度线棒证据（q≤0.7 上界在模板） | PROPOSE_ADD bar |
| `LENS_*` | `_lens_rule` | bar 参数异常+延展正残差 | PROPOSE_ADD lens（Re_disk>Re_lens>Re_bar） |
| `COMPANION_NUMERIC_VLM_V1` | `_companion_rule` | ≥3 波段一致检出+VLM 独立源 | PROPOSE_ADD companion |
| `FOURIER_M1_CLOSING_V1` | `_m1_closing_rule` | 所有结构候选走完后、m1 振幅>0.02 | 收尾试加 fourier_m1 |

参数修复（REFIT_PARAMETERS）：`REQUIRED_FIXED_PARAMETER_V1`（Disk n=1）、`CENTER_CONSTRAINT_V1`（主星系同心，companion 豁免）、`PARAMETER_HEALTH_V1`（边界放宽）。

### 5.2 阈值

全部集中在 `rules.py` 顶部的 `RuleThresholds`（版本化，artifact 会记录 thresholds_version）。调阈值改这里，不要散落在规则里硬编码。

### 5.3 口径裁定（改规则前必读）

科学口径的 13 条裁定（companion 多波段门、主星系强制同心、bar q≤0.7、先拆 disk+bulge、m1 收尾动作、官方 reduced chisq、统一 BIC 门、VLM 残差否决权、先拆分入口……）全部带日期与理由记录在 `docs/component-analysis/workflow-redesign-execution-plan.md` 开头的裁定段。**改相关规则前先读对应裁定，不要无意推翻已定口径。**

### 5.4 判定矩阵（`evaluate_refit`）

所有 PROPOSE_ADD 统一标准（裁定 11）：

```text
接受 = 官方 per-band reduced chisq 平均下降（裁定 10）
     ∧ BIC_gain ≥ 10（RuleThresholds.optional_bic_gain）
     ∧ 无候选新引入/未解决边界（Q3：baseline 已有的不算）
     ∧ 参数物理有效 + fit_quality_ok
     ∧ VLM 残差对比 ≠ dirtier（裁定 12 一票否决；VLM 不可用则降级纯数值）
例外：规范合规动作（Disk n=1、主星系同心）不看拟合统计，只看收敛+物理+边界
```

## 6. 如何做常见修改

| 想做的事 | 改哪里 |
|---|---|
| 调某规则阈值 | `RuleThresholds`（rules.py 顶部） |
| 新增结构提议规则 | `rules.py` 写 `_xxx_rule` 函数 + 在 `decide_proposal` 的 `ordered_rules` 注册优先级 |
| 改成分模板初值/边界 | `workflow_bridge.py::_multi_profile_template` 与 `_apply_multi_action` |
| 改接受标准 | `rules.py::evaluate_refit` 判定矩阵（先核对裁定 10-12） |
| 加/改 VLM 问题 | `vlm.py`（受控标签枚举+严格 parser 同步改） |
| 看某次运行为何这样决策 | 对象 `output/workflow/<run_id>/summaries/` 逐轮 md；或 `artifacts/` 里的 decision_artifact.json 的 rule_trace |

## 7. 纪律红线（继承自项目 CLAUDE.md）

- 永不修改对象的基础 `.lyric`；新配置一律 `_iterN` 后缀。
- 拟合只能经 MCP 工具（`run_galfits_image_fitting` 等），不要 shell 直调 GalfitS。
- `--fit_method ES` 固定；禁用 `--readsummary`（会丢 vary=0 参数）。
- 每轮最多一个结构变化；每个候选必须恰好一次 `workflow_evaluate_refit`。
- SED/Image-SED 只有 Image `CONVERGED` + verifier `PASS` + `LOCKED` 之后才允许，否则保持 `NOT_RUN`。
- 任何"工程验收"都不等于科学最优；分歧对象按 `needs_review` 交人工。
