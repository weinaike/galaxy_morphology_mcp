# 多波段成分分析 Workflow：Luna 执行计划

状态：N0～N5 已实施并通过回归；104 v5 已完成一次工程 canary，但真实的“决策 → 新 lyric → 新 Image 拟合 → refit 评价 → 更新模型 → 收敛锁定”科学可用闭环尚未验收。下一阶段按本文件第 17 节的 N12～N16 执行。

更新日期：2026-09-08

## 1. 执行权威和历史文件

本文件是多波段 workflow 改造的唯一执行方案，保存 N0～N6 的基础契约和实施记录，并在第 13～16 节定义 N7～N11。Luna 同时遵守项目级 `AGENTS.md`、`CLAUDE.md` 和两个 workflow prompt，不读取 `.orig`、`.rej` 或历史 run 的执行顺序来决定动作。

`workflow-redesign-execution-plan.md.orig` 是 2026-09-03 保存的主文件旧版快照，包含此前的阶段记录和部分已经过期的验收表述。它不是当前规范、不是恢复状态，也不应被 Luna 读取为执行指令。保留它用于差异审计和回滚；未经单独确认不删除。

历史细节、旧 pilot 结果和旧决策只用于审计，不得作为新 baseline、`PolicyState`、最佳轮次或科学结论。当前方案中的“已完成”只表示工程接口或测试已完成，不表示科学上确认全局最优。

## 2. 目标和硬边界

目标是实现 JWST0716 风格的真实多轮多波段 Image workflow：

```text
baseline Image
  → Round 0 预测和证据
  → Rules 候选
  → Policy resolved_decision
  → 新 lyric
  → run_galfits_image_fitting
  → workflow_evaluate_refit
  → 接受候选或保留 baseline
  → 下一轮
```

必须遵守：

- JWST0831 只允许对象 `104`、`1071`、`1118`，严禁启动其余 31 个对象。
- JWST0831 不得使用 `run_galfits`。Image、SED、Image-SED 的入口分别是 `run_galfits_image_fitting`、`run_galfits_sed_fitting`、`run_galfits_image_sed_fitting`。
- 不得在 shell 或 Python subprocess 中直接运行 GALFIT／GalfitS。
- 每轮最多执行一个结构动作。
- 所有机器动作只能来自 schema 校验通过的 `decision_artifact.resolved_decision`。Markdown、Working Note、VLM 解释文字不能反向决定动作。
- 首轮 generic single Sersic 不得默认解释为 Disk。
- 只有 component analysis 明确确认 Disk 后，才允许产生 `DISK_N_NOT_FIXED`；确认后 Disk 的 `n` 必须固定为 1。
- 普通候选评价综合收敛、参数物理性、1D／2D 残差、reduced chi-square、BIC、参数边界和退化。BIC 不是唯一评价标准。
- Image 没有 `CONVERGED`、verifier `PASS`、`lockable=true` 和成功 `LOCKED` 时，SED 和 Image-SED 必须是 `NOT_RUN`。

## 3. 各模块职责

| 层 | 文件 | 唯一职责 |
|---|---|---|
| 输入适配 | `src/component_analysis/artifact_adapter.py` | 解析 lyric、验证 science／sigma／mask／PSF、建立明确 manifest |
| 数值证据 | `src/component_analysis/numeric.py`、`derived.py` | 只测量事实，不输出成分动作 |
| Round 0 | `src/tools/bar_lopsidedness_detection.py` 及 workflow adapter | 采集 `detect_bar_lopsidedness`，生成初始预测 artifact |
| Rules | `src/component_analysis/rules.py` | 根据当前模型和证据产生候选、终止检查和 raw decision |
| Policy | `src/component_analysis/policy.py` | 只解析 `INCONCLUSIVE`、管理 trial／重复 fingerprint／review 状态，不凭空创造科学候选 |
| 动作编译 | `src/component_analysis/workflow_bridge.py` | preflight、生成新 lyric、生成 MCP 调用契约，不直接拟合 |
| 调度 | `src/tools/workflow_batch_runner.py` | 按 resolved decision 调用 MCP、保存候选生命周期、更新当前模型 |
| 比较 | `src/component_analysis/refit_comparator.py` | 比较 baseline 和 candidate 的结构化拟合证据 |
| 状态 | `src/component_analysis/workflow_lifecycle.py`、`workflow_bridge.py` | 保存对象级状态、事件、当前配置和结果引用 |
| 报告 | `src/component_analysis/workflow_summary_renderer.py` | 只读取 schema 校验通过的 artifact，确定性生成 Markdown |

## 4. 当前模型状态必须这样表达

不能使用 `current_components=[]` 同时表示“没有模型”和“有一个未分类模型”。proposal artifact 必须区分：

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

`single_sersic` 是当前 lyric 的事实，不是 Disk 结论。只有显式 Disk 证据被 Rules／Policy 接受后，才把该 profile 的语义更新为 `disk`。

## 5. Rules、Policy 和决策状态

### 5.1 Rules 的候选优先级

Rules 先产生参数候选和结构候选，再选择一个候选。优先级数值越小越优先：

1. 必需参数／约束修复：`REFIT_PARAMETERS`。
2. 已有成分删除候选：`PROPOSE_REMOVE`，必须经过 remove 安全门。
3. 新增、替换和其他结构候选：`PROPOSE_ADD`、`PROPOSE_REPLACE`、`PROMOTE_SINGLE_SERSIC_TO_DISK`。

如果存在候选，即使当前拟合统计看起来不错，也不能直接 `CONVERGED`；必须先执行候选并比较。

### 5.2 决策状态矩阵

| `resolved_decision.action.action_type` | 触发条件 | 是否生成新 lyric | 是否执行 Image 拟合 |
|---|---|---:|---:|
| `REFIT_PARAMETERS` | 当前已确认成分存在参数边界、中心约束、必需固定项或允许的参数问题 | 是 | 是 |
| `PROPOSE_ADD` | 一个尚不存在的成分有完整结构证据 | 是 | 是 |
| `PROPOSE_REPLACE` | 已有成分需要替换，source／target 映射、约束和模型模板完整 | 是 | 是 |
| `PROPOSE_REMOVE` | 目标成分有真实局部残差支持，数据质量合格，remove 安全门通过 | 是 | 是 |
| `PROMOTE_SINGLE_SERSIC_TO_DISK` | 当前是未分类 single Sersic，且 component analysis 明确确认 Disk | 是 | 是 |
| `KEEP_AND_CONTINUE` | 没有候选，但存在可自动采集的新证据 | 否 | 否；只执行 `COLLECT_EVIDENCE` |
| `CONVERGED` | 没有候选，且所有 Image 终止门均为 `PASS` | 否 | 否；先 verifier 和 lock |
| `STOPPED_NEEDS_REVIEW` | 关键证据不可得／冲突、工具失败、schema 失败、重复不确定或轮次耗尽 | 否 | 否 |

`KEEP_AND_CONTINUE` 不是拟合动作。它必须包含枚举化 `next_transition=COLLECT_EVIDENCE` 和证据目标；没有证据采集路径时必须立即转 `STOPPED_NEEDS_REVIEW`。相同 lyric、result FITS 和 evidence fingerprint 不得创建新的拟合 round。

### 5.3 Policy 的职责边界

Policy 不把 `KEEP_AND_CONTINUE` 变成拟合，也不把 VLM 自由文本变成动作。它只处理原始 `INCONCLUSIVE`：

- VLM 不可用时，按规则允许的范围执行 numeric-only retry；
- 规则定义了可试探的候选且 trial budget 未耗尽时，降级为一个候选并交给真实 refit；
- 同一规则在相同 evidence fingerprint 下再次不确定，或 trial budget 耗尽时，转 `STOPPED_NEEDS_REVIEW`；
- 无安全的试探动作时，转 `STOPPED_NEEDS_REVIEW`。

## 6. 每种可执行动作的 lyric 编译契约

所有新 lyric 写入当前 run 的独立目录，例如：

```text
<object>/output/workflow/<run_id>/configs/obj_<id>_iterN.lyric
```

源 lyric 不修改，目标文件不得覆盖。每次编译都返回目标 lyric 的绝对路径、checksum、约束文件路径和 MCP 调用参数。

### 6.1 `REFIT_PARAMETERS`

- `target_model_label` 必须存在于 `current_profile`。
- 一轮只允许一个 `parameter_change`。
- 修改参数、边界、固定项或中心约束，不改变成分结构。
- 普通 refit 需要综合评价；`DISK_N_NOT_FIXED` 只在 Disk 已确认后执行，必须固定 `n=1`，不因 BIC、reduced chi-square 或残差变差单独回滚，但仍要求拟合收敛、参数物理有效且无严重边界／退化问题。

### 6.2 `PROPOSE_ADD`

支持的多波段模板包括 Disk、Bulge、Bar、AGN、Companion、Lens 和 Fourier m=1。每次只增加一个成分或一个 Fourier 项，并为所有波段建立一致的 profile、SED 关系、中心约束和必要固定项。

`PROPOSE_ADD disk` 不得用于把现有 single Sersic 重新解释为 Disk；这种情况必须使用 `PROMOTE_SINGLE_SERSIC_TO_DISK`。

### 6.3 `PROMOTE_SINGLE_SERSIC_TO_DISK`

- 当前 profile 必须是唯一或目标明确的 `single_sersic`，且未被确认成其他成分。
- Rules 必须提供 Disk 证据和 reason code。
- 保留原 profile，不新增第二个 Disk。当前 GalfitS provider 不接受 `expdisk`
  作为 `P?2)` 类型；可执行 lyric 使用 `sersic` profile，并将 Disk 的 `n`
  固定为 1，同时在结构化 action 和 lyric 注释中记录 Disk 语义。
- 拟合后使用 Disk 专用评价门。

### 6.4 `PROPOSE_REPLACE`

- `replace_from` 必须是当前模型中的明确 profile label。
- `replace_to` 必须在允许的模型白名单中，并有 mode-specific 模板。
- 编译器删除旧 profile 在所有波段和 galaxy 成员列表中的引用，插入目标 profile，迁移可继承的参数、中心约束和 SED 关系。
- 生成新 lyric 后调用多波段 Image MCP，并通过 `workflow_evaluate_refit` 决定接受或拒绝。

如果某种替换尚无科学模板，Rules 不得生成该候选；不能让一个必然失败的 action 进入正式 workflow。

### 6.5 `PROPOSE_REMOVE`

- 目标 profile 必须存在，且不能删除唯一有效中心模型。
- Disk、AGN 等受保护角色仍需遵守 Rules 的保护条件。
- 必须有真实 `component_local_residual_facts`、数据质量通过和 remove pilot 通过。
- 编译器从所有波段 profile、galaxy 成员和相关约束中删除目标，生成新 lyric。
- 新 lyric 必须真实拟合并与 baseline 比较；拒绝时保留 baseline，不删除任何原始文件。

## 7. Runner 的强制执行循环

runner 必须按以下顺序执行，不能由 Markdown 或 VLM 文字跳步：

1. 验证对象白名单、基础 lyric 和每个波段的 science、sigma、mask、PSF 路径；sigma 必须来自 `/home/www/2026/GALFITS_examples/jwst0831/sigma/Sig<obj>_<band>.fits`，并检查 shape。
2. 从基础 lyric 建立全新 run ID 和独立 `PolicyState`，不使用旧 batch 的 resume、lifecycle 或 Image result。
3. 实际执行 Round 0 的 `detect_bar_lopsidedness` 和初始 component prediction。
4. 调用 `run_galfits_image_fitting` 建立 baseline，写入 baseline manifest、result、summary 和 comparison 路径。
5. 调用 component analysis，保存 numeric evidence、VLM evidence、rule trace 和 raw decision。
6. 调用 Policy，保存 schema 校验通过的 `resolved_decision`。
7. 对 `REFIT_PARAMETERS`、`PROPOSE_ADD`、`PROPOSE_REPLACE`、`PROPOSE_REMOVE`、`PROMOTE_SINGLE_SERSIC_TO_DISK`：
   - 调用 `workflow_action_preflight`；
   - 调用 `workflow_action_config` 生成新的 `_iterN.lyric`；
   - 调用 `check_lyric_file`；
   - 调用 `run_galfits_image_fitting`；
   - 建立 candidate manifest；
   - 调用 `workflow_complete_candidate` 和 `workflow_evaluate_refit`；
   - 接受时更新 `current_profile`、`current_config`、`current_fit`，拒绝时保留 baseline 并记录 rejected fingerprint。
8. 对 `KEEP_AND_CONTINUE`：只调用明确的证据采集器并重新分析；若没有新的证据路径，转 `STOPPED_NEEDS_REVIEW`。不得用同一 lyric 和 result FITS 空转。
9. 对 `CONVERGED`：调用 `workflow_verify_best_round`；只有返回 `verdict=PASS`、`lockable=true` 后，才调用 `workflow_lock_best_round`。锁定成功后才进入下游。
10. 每个 Image round 完成后立即运行本地确定性 renderer，生成逐轮 summary、Working Note 和对象最终报告。

## 8. 下游门和停止语义

只有同时满足以下条件，才允许调用 SED：

```text
Image workflow_status = CONVERGED
workflow_verify_best_round.verdict = PASS
verifier artifact.lockable = true
workflow_lock_best_round.status = LOCKED
```

以下任一情况都禁止 SED 和 Image-SED：

- `KEEP_AND_CONTINUE` 没有可执行证据采集路径；
- `STOPPED_NEEDS_REVIEW`；
- Image 达到真实拟合轮次上限；
- Image 工具失败；
- 任意 artifact schema 失败；
- verifier 非 `PASS`；
- best round 未 `LOCKED`。

有有效 Image result 时：`FIT_AVAILABLE`、`UNLOCKED`、`needs_review=true`、`sed_joint_eligible=false`。没有有效 Image result 时：`FAILED_NEEDS_REVIEW`。两种情况的 SED 和 Image-SED 都是 `NOT_RUN`。

## 9. Renderer 要求

`src/component_analysis/workflow_summary_renderer.py` 必须是本地确定性的 Python renderer：

- 只读取已经落盘并通过 schema 校验的 JSON artifact；
- 不调用 agent 调度模型生成权威动作总结；
- 不从 Markdown 反向解析动作；
- 字段缺失写 `unavailable` 或“未采集”，不补猜；
- 无 candidate refit 时也要显示 baseline 的收敛、残差、reduced chi-square、BIC、参数健康，并明确写“本轮没有生成候选模型”。

每轮 summary 必须包含：Round 0 预测、detect 结果、当前 profile、已确认成分、numeric／VLM evidence、raw／resolved action、reason code、新 lyric 和约束差异、每波段产物路径、综合拟合评价、refit verdict、PolicyState、needs_review、next_transition 和停止原因。

## 10. 实施阶段和涉及文件

### N0：冻结 schema 和 transition

修改 `decision_artifact.schema.json`、`workflow_proposal.schema.json`、`workflow_lifecycle.schema.json`，增加 `PROMOTE_SINGLE_SERSIC_TO_DISK`、`COLLECT_EVIDENCE`、replacement／remove 字段、candidate lyric 引用和真实执行状态。

### N1：输入和当前模型

修改 `artifact_adapter.py`、manifest builder、`decision_service.py`，接入 sigma 校验、Round 0 artifact、`current_profile`／`predicted_components`／`confirmed_components`。

动作编译器写入的行内 `semantic_label` 必须由 profile parser 保留并回流到下一轮 `current_profile`；否则 promotion 后会被错误地再次识别为 generic single Sersic。

### N2：Rules 和 Policy

修改 `rules.py`、`policy.py`：

- generic single Sersic 不自动变 Disk；
- Disk 确认后产生 promotion 动作；
- KEEP 只能指向证据采集；
- 不可恢复的缺证据、冲突、重复不确定转 STOPPED；
- 不允许 Rules 产生没有 adapter 的 replacement／remove action。

### N3：动作编译器

修改 `workflow_bridge.py`，实现 add、parameter refit、promotion、replace、remove 五类新 lyric 编译器和多波段 MCP 调用契约。

### N4：真实 runner

修改 `workflow_batch_runner.py`：候选动作统一走新 lyric、真实 Image MCP、candidate manifest、`workflow_evaluate_refit` 和接受／拒绝状态更新；KEEP 不得空转。

### N5：比较、renderer 和报告

修改 `refit_comparator.py`、`workflow_summary_renderer.py`，保证普通候选综合评价和 Disk 专用评价均有完整字段。

renderer 对终止决策的 `action=null` 必须输出 `unavailable`／停止原因，不能因空值访问失败而把已有有效 Image result 标成 `FAILED_NEEDS_REVIEW`。

### N6：测试和 canary

增加以下测试：

- 五类 lyric compiler 的 unit test；
- `PROPOSE_REPLACE` 和 `PROPOSE_REMOVE` 的多波段 profile／约束完整性测试；
- `PROMOTE_SINGLE_SERSIC_TO_DISK` 不产生第二个 profile 的测试；
- KEEP 不重复同一配置和 result 的 contract test；
- STOPPED 保留有效结果且不调用 SED 的 contract test；
- resume 和重复运行幂等测试；
- 三对象白名单测试；
- verifier PASS／LOCKED 后才调用下游的 mock integration test；
- JWST0716 历史 artifact 离线 replay，不改写历史结论。

## 11. 真实验收门

先只运行 JWST0831 对象 `104`。通过条件：

- 至少存在 baseline、`_iter1`、`_iter2` 三个不同 lyric；
- 每个真实候选 lyric 都对应不同的 result FITS；
- 每个候选都调用 `workflow_evaluate_refit`；
- 至少一次候选接受或拒绝，并正确更新或保留当前模型；
- 没有相同 fingerprint 空转；
- 每轮 summary 能直接说明动作、产物和下一步；
- Image 未锁定时 SED／Image-SED 保持 `NOT_RUN`。

104 canary 通过后，按同一 runbook 执行 `1071`、`1118`。三对象之外的 31 个对象在本文件更新并获得单独授权前不得启动。

## 12. 当前状态

- 三个基础 lyric 的 sigma 路径已补齐，并已验证 21 个 sigma FITS 存在且 shape 与 science 一致。
- 旧三对象 artifact 只作为 renderer fixture；不作为新 workflow baseline。
- 旧 timestamped output 结果已按授权删除；科学输入、基础 lyric、新 workflow 目录和其余对象均保留。
- 新 run `jwst0831-20260903T105600Z` 的 30 个逻辑 round 不能作为真实多轮拟合验收，因为其 round 重复使用同一 lyric 和 result FITS。
- N0～N5 的代码改造和相关回归测试已完成；N6 的 104 v5 真实 canary 已完成并通过“真实候选执行、refit 评价、重复 fingerprint 终止”的工程验收门，但该 run 的 `use_vlm=false`，Image 未 `CONVERGED`，没有 verifier PASS／LOCKED，因此不能作为 JWST0716 式科学可用闭环。`1071`、`1118` 以及其余 31 个对象仍未授权、未执行。

Luna 下一次执行必须按本文件第 13～16 节实施，先完成 renderer／provider 可观测性修复和测试，再建立全新的 104 run。不得复用 v5 的 lifecycle、PolicyState 或 Image result，也不得把当前状态描述为 workflow 已经达到 JWST0716 式真实多轮拟合。

## 13. 104 v5 审计结论

N0～N5 的代码改造已经完成，最近一次全量回归为 `271 passed、5 skipped、3 warnings`。v5 只运行对象 `104`，真实事件链为：

```text
baseline
  → PROMOTE_SINGLE_SERSIC_TO_DISK
  → iter1 Image fitting
  → workflow_evaluate_refit：ACCEPTED
  → PROPOSE_ADD companion
  → iter2 Image fitting
  → workflow_evaluate_refit：REJECTED
  → 重复候选被 fingerprint 门阻止
  → STOPPED_NEEDS_REVIEW
```

该 run 证明了新 lyric、真实 Image fitting、refit 评价和重复候选终止的工程链路能够运行，但仍有以下限制：

- batch manifest 为 `use_vlm=false`，三轮 provider 均为 `DISABLED`；summary 中的 VLM `unavailable` 表示未调用，不是 VLM 没有发现结构。
- Round 1 被拒候选的 lifecycle 含 schema 不允许的顶层字段 `lifecycle_file`、`fit_valid`，renderer 因而漏报已经落盘的候选评价。
- Image 终止检查仍有 `ABSOLUTE_RESIDUAL=FAIL`、`RULES_COMPLETE=FAIL`、`NO_HIGH_PRIORITY_INCONCLUSIVE=FAIL`，所以没有 `CONVERGED`、verifier `PASS` 或 `LOCKED`。
- 最终状态 `COMPLETED_WITH_REVIEW`、`FIT_AVAILABLE`、`UNLOCKED` 只表示保留有效 Image 结果并等待复核；SED 和 Image-SED 正确保持 `NOT_RUN`，不表示科学上确认最优。

## 14. N7 前必须修复的问题

### 14.1 Lifecycle schema 和失败语义

- `workflow_bridge.py` 写回任何 lifecycle 前必须剥离 MCP 返回元数据，再按 `workflow_lifecycle` schema 校验。
- `workflow_batch_runner.py` 对 baseline、candidate、terminal lifecycle 统一使用同一落盘入口，candidate completion 后必须再次校验。
- candidate lifecycle schema 失败时立即停止自动循环并设置 review 状态，不得继续生成看似完整且具备下游资格的报告。
- 增加 rejected candidate 的真实 fixture，覆盖 `lifecycle_file`、`fit_valid` 不得进入 artifact 的回归场景。

### 14.2 Renderer 和证据语义

- renderer 以通过 schema 校验的 candidate lifecycle 和 refit artifact 为权威来源，分别列出 baseline 与 candidate 的 lyric、约束、result FITS、summary、comparison 和综合评价。
- 动作时间线必须分开显示 raw decision、policy resolution、executed action、refit verdict 和对象终态。
- VLM 区域必须明确显示 `DISABLED`、`REFUSED`、`PARSE_FAILED`、`TIMEOUT` 或 `OK`，并在已采集时显示 model、prompt version、attempt 和 timing；不能用含义不明的 `unavailable` 代替 provider 状态。
- Round 0 必须分开保存和展示 `detected_features`、`predicted_components`、`current_profile`、`confirmed_components`。generic single Sersic 只写入 `current_profile`，不得自动解释为 Disk。
- Working Note 必须累积每轮模型、参数差异、评价、距离预期的偏差和下一步；final report 必须汇总完整动作链、当前候选、未解决证据、verifier／lock 和下游状态。

### 14.3 Run 内输出布局

新 run 先把基础 lyric 复制为 run-scoped baseline lyric，保持输入路径明确并通过 `check_lyric_file`。baseline 和候选输出统一归入：

```text
<object>/output/workflow/<run_id>/
  configs/
  configs/output/
  artifacts/
  summaries/
  run_index.json
  capacity_report.json
```

`run_index.json` 必须建立 round、action、baseline／candidate 路径和 checksum 的明确映射。v5 只作为工程审计样本，不搬迁、不重写。

## 15. N7～N11 执行顺序

### N7：修复 lifecycle、provider 状态、renderer 和输出索引

按第 14 节修复 lifecycle 元数据污染、schema 失败状态、VLM provider 可观测性、四层成分语义、候选评价渲染、Working Note、final report、run-scoped baseline 和 `run_index.json`。本阶段不得执行真实拟合。

### N8：测试、v5 离线 replay 和全量回归

必须增加并通过：

- rejected candidate lifecycle 无额外字段的 regression test；
- candidate lifecycle schema 失败时停止且不调用下游的 contract test；
- VLM `DISABLED`／失败／`OK` 的 renderer test；
- rejected refit 指标以及 baseline／candidate 路径完整性测试；
- Round 0 四层语义、Working Note、final report 和 run-scoped baseline 测试；
- 五类模型动作的 lyric／Image MCP／`workflow_evaluate_refit` contract test；
- resume、重复 fingerprint、三对象白名单和 verifier／lock 下游门测试。

使用保留的 v5 artifact 做离线 replay，只验证 schema 错误可见、字段读取、路径索引和 Markdown 完整性，不得修改 v5 的 decision、PolicyState、lifecycle、最佳轮次或科学结论。聚焦测试通过后运行全量回归。

### N9：只执行对象 104 的新 VLM canary

N7～N8 全部通过后，为对象 `104` 创建全新独立 run ID，从基础 lyric 生成 run-scoped baseline。不得 resume v5，也不得复用 v5 的 lifecycle、PolicyState 或 result FITS。

必须通过以下 wrapper 启动，并在首次拟合前断言 manifest 中 `pilot_enabled=true`、`workflow_mode=multi-band`、`use_vlm=true`：

```text
./batch_analyze_galfits_workflow_pilot.sh \
  /home/www/2026/GALFITS_examples/jwst0831 \
  <new_run_dir> \
  --object-id 104
```

wrapper 固定使用 `--mode multiband --pilot 1 --use-vlm --resource-profile serial-gpu --max-rounds 10`。每个模型动作必须产生新 lyric、调用 `run_galfits_image_fitting` 并调用 `workflow_evaluate_refit`。`KEEP_AND_CONTINUE` 只能采集新的结构化证据，不得重复相同 lyric、result FITS 和 evidence fingerprint。

### N10：执行收敛、落锁和下游强制门

只有 Image `CONVERGED`、verifier `PASS`、artifact `lockable=true` 且 best round 成功 `LOCKED` 后，才能依次调用 `run_galfits_sed_fitting` 和 `run_galfits_image_sed_fitting`。任一条件不满足时，按第 8 节停止语义结束，SED 和 Image-SED 保持 `NOT_RUN`。

不得为了得到 `CONVERGED` 而忽略未解决候选、降低科学门或让 Policy 创造 Rules 未提供的动作。没有安全动作时，`STOPPED_NEEDS_REVIEW` 是正确终态。

### N11：停止并提交 104 报告

104 新 canary 完成后必须停止，提交逐轮 summary、Working Note、final report、run index、capacity report、provider timing 和 verifier／lock 结果，等待小鱼儿确认。未经新确认，不运行 `1071`、`1118`；其余 31 个对象仍严禁启动。

## 16. 新 104 canary 验收和保留记录

验收条件：

- manifest 明确为 `use_vlm=true`，provider 状态和 timing 可追溯；
- Round 0 同时展示 detect、prediction、current profile 和 confirmed components；
- 每个被选模型动作都有不同 lyric、不同 result FITS 和一次 `workflow_evaluate_refit`；
- 被拒候选的 summary 直接显示拒绝原因和全部综合评价字段；
- baseline 和 candidate 的每波段路径不混写，Working Note 无需打开 JSON 即可理解每轮动作、结果和下一步；
- 没有相同 lyric、result FITS 和 evidence fingerprint 的重复拟合；
- Image 未收敛时，final report 明确写 `SED: NOT_RUN`、`Image-SED: NOT_RUN` 和停止原因；
- Image 收敛时，存在 verifier `PASS`、`lockable=true`、`LOCKED` 的完整引用，之后才允许有 SED／Image-SED 结果；
- `1071`、`1118` 和其余 31 个对象的拟合调用数为 0；
- 工程验收不得表述为科学上确认全局最优。

当前保留的审计样本为：

- `/home/www/2026/GALFITS_examples/jwst0831/104/output/20260907_155810_obj_104/`；
- `/home/www/2026/GALFITS_examples/jwst0831/104/output/workflow/jwst0831-20260907T075722Z/`；
- `/tmp/jwst0831-canary-20260907-104-v5/`；
- `/tmp/jwst0831-invalid-canary-20260907-inventory.txt`。

此前已按授权删除 v1～v4 的 12 个精确目录，共 685 个文件；删除前清单 checksum 为 `e3e7c861cdcd42bfb73d9a84838a22e49e243f4cc305d7b6c84b4fa29adea546`。新 canary 必须使用全新 run ID。没有再次获得明确授权和精确 checksum 清单前，不删除上述 v5 样本、科学输入 FITS、sigma、mask、PSF 或基础 lyric。


## 17. 当前最高优先级：修复 VLM 截断和决策链可持续性

第 17 节覆盖并细化第 15 节的 N9～N11。当前目标不是放宽 verifier 或下游门，而是先让
`Numeric Evidence → Rules → Policy → Resolved Decision` 在 VLM 暂时不可用时仍能正确评估
所有不依赖 VLM 的证据，并在有安全候选时继续执行真实多轮 Image fitting。

### 17.1 18 个 VLM target 的来源

VLM target 数量不是固定配置，也不是 workflow 预设的 18 个天体。`src/component_analysis/vlm.py`
中的 `allowed_target_ids()` 始终加入一个保留目标 `central`，然后按当前
`numeric_evidence.features[*].candidate_regions[*].region_id` 的首次出现顺序加入唯一候选区。
因此最近一次 104 canary 的 18 个 target 是：

```text
1 个 central + 17 个 numeric candidate_regions = 18 个 target
```

这些 target 是数值层在 residual／profile／局部峰等事实中生成的候选区域，不等于 18 个已确认的
物理成分。VLM 只能为这些已发行的 target 提供受控观察，不能创建新 target、成分或动作。

必须新增测试，验证：

- target 数量随 `candidate_regions` 动态变化；
- 相同 `region_id` 只出现一次；
- `central` 不能由 numeric layer 重复发行；
- 0 个候选时只请求 `central`，不能填充虚假的候选目标。

### 17.2 VLM provider 协议修复

当前证据已经确认：Round 0 的响应可解析，后续响应在 JSON 中途截断，retry 返回相同字节。
因此不得把该问题继续归类为普通 schema 失败，也不能继续对同一 prompt 做无变化重试。

修改 `src/component_analysis/provider.py`、`src/component_analysis/decision_service.py` 和
`src/component_analysis/vlm.py`：

1. provider 必须记录每次请求的 `model`、`prompt_version`、开始／结束时间、响应字节数、
   `finish_reason`、token usage（provider 返回时）和 attempt ID。provider 不返回这些字段时写
   `unavailable`，不得猜测截断原因。
2. VLM prompt 改为稀疏输出契约：`observations` 可以为空，只要求标注当前 Rules 需要的
   target；不得要求为全部候选生成长篇 notes。每个 observation 的 notes 必须短且不能包含动作、
   坐标或参数。
3. 为单次响应增加明确的 observation 数量和 notes 长度上限。超限时应在本地 schema 层失败，
   并记录可诊断的 reason code。
4. retry 必须改变请求条件：优先使用更小的 target 子集和更短的 prompt；不得重复提交相同的
   prompt、图片和 response budget。retry 仍失败时生成 schema 合法的 `PARSE_FAILED` artifact，
   同时保留原始响应和 provider metadata。
5. parser 继续 fail-closed：截断 JSON 中已经出现的 observation 不得被部分采信，也不得从
   raw response 反向生成动作。

VLM 证据状态必须区分：`DISABLED`、`REFUSED`、`TIMEOUT`、`PARSE_FAILED` 和 `OK`。这些状态
只描述证据可用性，不直接决定 workflow action。

### 17.3 Rules 不得因 VLM 失败整体短路

修改 `src/component_analysis/rules.py` 和相关 schema，使 Rules 在 VLM 不可用时仍执行完整的
numeric evidence 评估：

- 依赖 VLM label 的规则单独产生 `INCONCLUSIVE` trace，例如 companion 的
  `independent_source` 确认；
- 参数边界、中心约束、固定参数、收敛、1D／2D 残差、reduced chi-square、BIC、物理性和
  已有 profile 的安全结构规则必须继续评估；
- 一个 VLM 失败不能提前返回并跳过其它 numeric 规则；
- `raw_decision` 必须记录每条规则的 `SATISFIED`、`NOT_SATISFIED` 或 `INCONCLUSIVE`，
  并明确哪些候选因 VLM 缺失而不可用；
- `resolved_decision` 仍是唯一机器动作来源，VLM raw text、Markdown 和 Working Note
  不能生成动作。

这不意味着把 VLM 结论当作 numeric 结论。缺少 VLM 只会使对应规则保持不确定；它不能否定已经
由 numeric evidence 独立满足的参数或模型约束。

### 17.4 Policy 和 `KEEP_AND_CONTINUE` 的可执行语义

修改 `src/component_analysis/policy.py`：

1. numeric-only retry 必须是 Rules 的显式第二证据视图，而不是绕过 Rules 的隐藏分支；artifact
   同时保存原始 VLM 视图、numeric-only 视图和最终 resolved decision。
2. Policy 可以在以下条件下继续：存在至少一个安全的 numeric 候选，或存在明确的
   `COLLECT_EVIDENCE` 采集器，并且该动作不会重复已执行的 lyric、result FITS 或 evidence
   fingerprint。
3. `KEEP_AND_CONTINUE` 必须包含：
   - `next_transition=COLLECT_EVIDENCE`；
   - 明确的 evidence target 和 collector ID；
   - 新输入或新计算的预期 fingerprint；
   - 下一轮完成后重新进入 Rules 的条件。
4. 若没有新的证据采集路径，或当前 fingerprint 与上一轮相同，必须返回
   `STOPPED_NEEDS_REVIEW`，不能用 `KEEP_AND_CONTINUE` 空转。
5. VLM 连续失败本身不能凭空产生结构动作；但也不能阻止独立 numeric 候选进入真实 refit。
   已拒绝的候选在相同证据 fingerprint 下不得重复提出。
6. `PROPOSE_ADD`、`PROPOSE_REPLACE`、`PROPOSE_REMOVE`、`REFIT_PARAMETERS` 和
   `PROMOTE_SINGLE_SERSIC_TO_DISK` 仍必须生成新 lyric、执行新 Image fitting，并调用
   `workflow_evaluate_refit`。BIC 不能单独决定接受或拒绝；Disk promotion 确认后 n=1 仍是
   语义约束，不因单一统计量变差撤销。

### 17.5 最短可验证执行顺序

Luna 必须按以下顺序实施，不得先启动真实拟合：

**N12：provider 和 VLM fixture 修复**

- 增加截断响应、空响应、超限 notes、动态 target 数量和非重复 retry fixture；
- 修复 provider metadata、稀疏 prompt 和 retry；
- 保证所有失败响应仍产生 schema 合法 artifact。

**N13：Rules／Policy 解耦和新证据状态**

- 移除 VLM 失败导致的 Rules 整体提前返回；
- 实现逐规则 `INCONCLUSIVE`、numeric-only 视图和真正的 `COLLECT_EVIDENCE`；
- 增加相同 fingerprint、已拒候选和新 evidence collector 的 contract tests。

**N14：renderer 和报告补齐**

- 每轮显示 provider status、attempt、finish reason、raw／numeric-only／resolved 三层决策；
- 明确列出因 VLM 缺失而不可用的规则和仍可执行的 numeric 候选；
- `KEEP_AND_CONTINUE` 必须显示 collector、预期新 fingerprint 和下一步；
- 缺少字段写 `unavailable` 或“未采集”，不能补猜。

**N15：离线回放和全量回归**

- 用保留的 v5 artifact 和截断 VLM fixture 做 offline replay；
- 验证不改写历史 lifecycle、PolicyState、最佳轮次或 scientific decision；
- 先运行成分分析、workflow、schema、renderer 和 policy 测试，再运行全量回归；
- 测试不通过时不得执行真实拟合。

**N16：只对 104 执行全新 VLM-enabled canary**

- 创建新的 run ID，从基础 lyric 建立 run-scoped baseline；
- manifest 必须满足 `pilot_enabled=true`、`workflow_mode=multi-band`、`use_vlm=true`；
- 只调用 `run_galfits_image_fitting`、`run_galfits_sed_fitting`、
  `run_galfits_image_sed_fitting`，不得调用 `run_galfit`；
- 每个模型动作必须有新 lyric、新 Image result 和 `workflow_evaluate_refit`；
- 只要 Image 未满足 `CONVERGED → verifier PASS → lockable=true → LOCKED`，SED 和
  Image-SED 必须是 `NOT_RUN`；
- 104 完成后立即停止，不运行 1071、1118 或其余 31 个对象。

### 17.6 104 闭环验收门

只有满足以下全部条件，才可把 104 标记为“工程闭环通过”：

- 至少完成 baseline、一个真实候选动作和一次下一轮决策；
- 每个候选 action 都有不同 lyric、不同 result FITS、完整 refit artifact 和 provider timing；
- `KEEP_AND_CONTINUE` 没有重复 lyric、result FITS 或 evidence fingerprint；
- Image 真正达到 `CONVERGED`，并完成 verifier `PASS`、`lockable=true`、best round `LOCKED`；
- 之后才存在 SED 和 Image-SED 结果；
- 若 Image 未收敛，最终报告必须明确 `SED: NOT_RUN`、`Image-SED: NOT_RUN` 和停止原因；
- 报告不得把工程闭环通过表述为科学上确认全局最优。

第 17 节完成前，当前 104 v5 run 只作为失败原因和离线 fixture 使用，不得 resume，也不得把
其 `COMPLETED_WITH_REVIEW`、`FIT_AVAILABLE` 或 `UNLOCKED` 当作新的 scientific baseline。
