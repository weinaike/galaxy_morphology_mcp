# JWST0831 三对象结构化 workflow pilot 报告

日期：2026-09-01
执行范围：`104`、`1071`、`1118`
数据根目录：`/home/www/2026/GALFITS_examples/jwst0831`
workflow artifact 根目录：`/tmp/jwst0831-pilot-full-xo2S8L`
batch ID：`jwst0831-20260901T063513Z`

## 1. 结论和边界

本轮按最新授权只完成 manifest 顺序中的前三个对象，没有启动第 4 个对象 `1182`。三个对象均通过现有 MCP 工具完成 Image → SED → Image-SED 链路，结构化 proposal、raw／resolved decision、候选拟合比较、对象级 PolicyState、terminal lifecycle、action summary 和 provider timing 均已落盘。

本报告只确认工程链路和审计产物，不评价成分选择、参数或残差的科学质量。三个对象均为 `COMPLETED_WITH_REVIEW` 和 `UNLOCKED`，仍需小鱼儿检查拟合结果；它们没有被写成 `CONVERGED`，因此未调用 verifier 或 best-round lock。

其余 31 个对象未执行，状态为 `NOT_STARTED`，不是拟合失败。本轮结果不代表 34 对象全量验证通过，也不打开默认 workflow 接管。

## 2. 运行配置

- mode：`multi-band`。
- pilot：显式启用。
- VLM：显式启用，使用当前已授权的外部 provider。
- 资源：串行 GPU；本轮没有 GPU OOM，也没有切换到 CPU。
- provider retry：每轮最多一次；本轮未触发 retry 或 numeric fallback。
- 实际拟合入口：现有 `run_galfits_image_fitting`、`run_galfits_sed_fitting`、`run_galfits_image_sed_fitting` MCP 工具。
- 未修改 `.env`、provider 凭据、科学阈值、默认启用开关或 2026-08-20 历史结果。

## 3. 对象结果

| 对象 | terminal state | Image | SED | Image-SED | raw／resolved action | refit | PolicyState |
|---|---|---|---|---|---|---|---|
| `104` | `COMPLETED_WITH_REVIEW` | `COMPLETED` | `COMPLETED` | `COMPLETED` | `REFIT_PARAMETERS`／`REFIT_PARAMETERS` | `REJECTED` | `COMPLETED_WITH_REVIEW`／`UNLOCKED` |
| `1071` | `COMPLETED_WITH_REVIEW` | `COMPLETED` | `COMPLETED` | `COMPLETED` | `REFIT_PARAMETERS`／`REFIT_PARAMETERS` | `REJECTED` | `COMPLETED_WITH_REVIEW`／`UNLOCKED` |
| `1118` | `COMPLETED_WITH_REVIEW` | `COMPLETED` | `COMPLETED` | `COMPLETED` | `REFIT_PARAMETERS`／`REFIT_PARAMETERS` | `REJECTED` | `COMPLETED_WITH_REVIEW`／`UNLOCKED` |

三个对象的规则原因均为 `DISK_N_NOT_FIXED`。每个对象的首个候选 refit 被确定性 comparator 判为 `REJECTED`；下一轮在 baseline 未变化时再次产生完全相同的动作，runner 根据已拒绝动作指纹停止重复拟合，终止原因为 `duplicate rejected action on unchanged baseline`。该机制只防止重复执行相同候选，不裁定拟合的科学优劣。

主要结果图：

- `104`：Image comparison 位于 `/home/www/2026/GALFITS_examples/jwst0831/104/output/20260901_143516_obj_104/all_bands_comparison.png`；Image-SED comparison 位于 `/home/www/2026/GALFITS_examples/jwst0831/104/output/20260901_144034_obj_104_sed/all_bands_comparison.png`。
- `1071`：Image comparison 位于 `/home/www/2026/GALFITS_examples/jwst0831/1071/output/20260901_144711_obj_1071/all_bands_comparison.png`；Image-SED comparison 位于 `/home/www/2026/GALFITS_examples/jwst0831/1071/output/20260901_145107_obj_1071_sed/all_bands_comparison.png`。
- `1118`：Image comparison 位于 `/home/www/2026/GALFITS_examples/jwst0831/1118/output/20260901_145634_obj_1118/all_bands_comparison.png`；Image-SED comparison 位于 `/home/www/2026/GALFITS_examples/jwst0831/1118/output/20260901_151042_obj_1118_sed/all_bands_comparison.png`。

## 4. Provider timing

- proposal rounds：6。
- provider attempts：6。
- provider 总耗时：128.556471 秒。
- provider status：`USED=6`。
- parse status：`OK=6`，`PARSE_FAILED=0`。
- fallback status：`OK=6`，没有 numeric fallback。
- timing 文件：`104/timing_log.md`、`1071/timing_log.md`、`1118/timing_log.md` 均存在于各自星系目录。

## 5. 工程问题和修复

1. MCP server 使用包含 `astropy` 的 galfit conda 解释器，但 SED 内部曾硬编码调用 PATH 中另一个 `python`。已改为沿用 `sys.executable`，避免子流程进入错误环境。
2. 原 runner 在 `--resume` 读取既有 manifest 后忽略 `--object-id`。已修复为限定调度所选对象，并让 GPU OOM fallback 同样受该集合限制。
3. 相同动作在未变化 baseline 上被拒绝后仍会重复拟合。已增加“动作＋baseline artifact”指纹；完全相同且已拒绝的候选不再执行。
4. batch `state.json` 已进入 review 终态时，根级 `policy_state.json` 曾停留在 `FIT_AVAILABLE／PENDING_VERIFIER`。已通过现有 `record_workflow_fit_lifecycle` 补写审计用 `STOPPED_NEEDS_REVIEW` terminal decision；三个对象复验均为 `COMPLETED_WITH_REVIEW／UNLOCKED／needs_review=true`、`pending_action=null`，并各自生成 `terminal_lifecycle.json`。
5. 原 34 对象进程按最新范围要求主动中止时，`1118` 正处于 SED。限定续跑成功恢复其 SED 和 Image-SED；中止留下的过程产物保留用于审计，没有删除或覆盖。
6. MCP lifecycle 返回值包含 `lifecycle_file`、`fit_valid` 两个便捷字段，它们不属于 schema artifact。runner 曾把完整返回值覆盖写回文件，导致 `additionalProperties=false` 校验失败。现已在落盘前剥离这两个返回层字段并验证 schema；前三对象共 12 个 baseline／candidate／terminal lifecycle 文件全部复验通过。

## 6. 幂等和范围审计

- 对 `1118` 重复执行限定 `--resume` 前后，三个对象的 output 子目录计数保持 `27／6／4`，没有新增拟合。
- `1182` 始终为 `NOT_STARTED`，round 数和 output 子目录计数均为 0。
- batch summary 为 `COMPLETED_WITH_REVIEW=3`、`NOT_STARTED=31`。
- 未执行对象：`1182`、`133`、`137`、`1398`、`1429`、`1446`、`1502`、`1525`、`1574`、`163`、`1639`、`170`、`1803`、`1825`、`1831`、`1833`、`1845`、`186`、`205`、`216`、`2185`、`2758`、`28`、`287`、`314`、`317`、`324`、`331`、`345`、`350`、`365`。

## 7. 后续人工批处理脚本

新增脚本：`batch_analyze_galfits_workflow_pilot.sh`。它只包装现有客户端无关 runner，实际拟合仍由 MCP 工具完成；脚本不包含 provider 凭据，不修改 `.env` 或默认开关。

继续本次 manifest 中剩余 31 个对象：

```bash
./batch_analyze_galfits_workflow_pilot.sh \
  /home/www/2026/GALFITS_examples/jwst0831 \
  /tmp/jwst0831-pilot-full-xo2S8L \
  --resume
```

只续跑一个对象：

```bash
./batch_analyze_galfits_workflow_pilot.sh \
  /home/www/2026/GALFITS_examples/jwst0831 \
  /tmp/jwst0831-pilot-full-xo2S8L \
  --resume \
  --object-id 1182
```

`RUN_DIR` 保存 manifest、断点状态和审计产物。继续同一批次时必须复用同一个 `RUN_DIR`；若 `/tmp/jwst0831-pilot-full-xo2S8L` 被系统清理，就不能用该路径恢复已有状态，应创建新的独立运行目录并先执行 `--dry-run`。

## 8. 验证记录

- batch runner、SED 环境修复聚焦回归：`19 passed`。
- batch runner 与共享 workflow lifecycle 回归：最终 `56 passed`。
- 三对象真实 artifact、PolicyState、timing 和限定续跑审计：通过。
- lifecycle 落盘修复后的最终全量回归：`257 passed, 5 skipped, 3 warnings`，耗时 566.92 秒；没有失败。warning 为既有 `asyncio_mode` 配置提示和两个合成图像等照度拟合提示。
- 真实 artifact schema：前三对象共 12 个 lifecycle 文件全部通过 `workflow_lifecycle` schema。
- shell 与补丁检查：`bash -n batch_analyze_galfits_mcp.sh batch_analyze_galfits_workflow_pilot.sh`、`git diff --check` 均通过。
- 新脚本真实数据 dry-run：`/tmp/jwst0831-wrapper-dryrun-aMG5aX`，选择对象 `104`，`preflight_status=READY`、`NOT_STARTED=1`，没有执行拟合或调用 provider。
### 2026-09-02 语义更正说明

上面的 `DISK_N_NOT_FIXED` 是本次 pilot 运行时旧适配器产生的工程原因：它把首轮未标注的单个 `obj0 + Sersic` 默认解释为 Disk，再要求把 `n` 固定为 1。因此，这一行不能解读为三个对象已经由成分分析科学确认存在 Disk，也不能解读为 BIC 是唯一评价标准。新的口径是：首轮单个 Sersic 保持未分类；只有 component analysis 明确确认 Disk 后，才触发固定 `n=1`，且该规范动作不因拟合质量变差而单独回滚。

本文其余内容是 2026-09-01 的历史运行记录，不覆盖、不改写已有 artifact；按新口径重新运行前，相关旧 decision 应标记为“语义修正前 artifact”。
