# 评测集 QC 样本查看器（evalset-qc-viewer）

> 更新日期：2026-09-17。方案已获小鱼儿确认；本文档是实施依据。
> 服务对象：`full` run 的 `FULL_REVIEW_REQUIRED` 审阅阶段，特别是 `a2-qc-samples/action-*.md` 质检样本的逐条查看与对同事的演示。

## 1. 目标与边界

把 `a2-qc-samples/action-*.md` 中一行一行的裁决摘要，变成一个可过滤、可下钻、可展示 comparison PNG 的本地网页。

- **只读工具**：不执行 GALFIT／GalfitS，不修改任何 run 产物，不写 `/media/data`；server 进程只响应 GET。
- **不属于抽取管线**：不进入 `component_evalset` 的 inventory/pilot/validate/full 四模式；路径展示中的目录列举仅用于显示动作后轮图片，不参与任何抽取或裁决逻辑。
- **零新依赖**：仅 Python 标准库（`http.server`＋`json`＋`html`）。

## 2. 数据链路（join 键＝sample_id）

`action-*.md` 表格只有 6 列摘要；完整信息按下表 join，**不需要按文件名猜测路径**：

| 信息 | 来源 |
|---|---|
| verdict／confidence／pool／reason codes／adjudicator_note | `<run>/evaluation-action-adjudications.jsonl` |
| canonical action（含原子动作）、state/post 轮次、expert 终态、当前成分、chi2/BIC、evidence_refs | `<run>/evaluation-decision-candidates.jsonl` |
| 输入文件路径（按 role：`comparison`／`configuration`／`fit_parameter_round`／`binary_fit`／`other`） | `<run>/evaluation-state-manifests/<sample_id>.json` 的 `source_refs` |
| 决策文件路径 | 单波段：adjudication `evidence_refs` 中 `*_comparison_component_analysis_*.md`；多波段无决策文件，以 `adjudicator_note` 代行 |
| 关键决策内容 | 决策 md 中「本次调整决策」段落切片（上限截断）＋`adjudicator_note` |
| state 轮 comparison PNG | manifest 中 `role=comparison` 的精确路径（单波段 `*_galfit_comparison.png`，多波段 `all_bands_comparison.png`） |
| post 轮 comparison PNG（辅助） | 取 `evidence_refs` 最后一条的所在目录，目录内匹配 `*comparison*.png`；`CONVERGED` 无 post 轮，不显示 |

## 3. 形态与部署位置

- 代码：`src/tools/evalset_qc_viewer.py`（当前仓库；`/media/data` 是严格只读数据根目录，不放代码）。
- 形态：本地 HTTP server＋单页 HTML，图片经 `/media/data/...` URL 只读映射内嵌展示（浏览器安全策略禁止 `file://` 页面 AJAX 与稳定内嵌本地 PNG；base64 单文件版预计百 MB 量级，不做）。
- 默认绑定 `127.0.0.1`；对同事演示时可 `--host 0.0.0.0`（仅内网演示用）。
- HTML 与数据在内存中按请求渲染，不在 run 目录写任何文件，避免污染 `FULL_REVIEW_REQUIRED` 状态的审计链。

## 4. 页面功能

1. 顶部：action 文件 tab（8 个）、verdict／pool／confidence／mode 过滤、sample_id/object 搜索、当前过滤计数。
2. 主表格一行一样本：sample_id、verdict、conf、pool、action、关键 reason codes（badge）。
3. 点击行展开详情卡片：
   - state 轮与 post 轮 comparison PNG 并排显示；
   - 输入文件路径按 role 分组，可点击打开原文；
   - 决策文件路径＋关键决策内容摘录；
   - `adjudicator_note`、原子动作、source vs expert 成分、chi2/BIC、state/post 轮次。

## 5. 安全与边界约束

- 静态映射仅限 `/media/data/galfit_run_history` 与 `/media/data/galfits_run_history` 两个根；路径规范化后必须落在根内，拒绝目录穿越。
- server 无任何写操作；HTTP 方法仅 GET/HEAD。
- run 目录与 `/media/data` 均不落盘新文件。

## 6. 验收

- 7 个 `action-*.md` 的全部样本 join 覆盖率 100%（启动时打印核对结果；实测 140/140，`a2-qc-samples/` 下为 7 个 action 文件而非 8 个）。
- `curl` 首页 200；抽若干 `/media/...` comparison PNG 返回 200。
- 人工抽 3 条：页面展示的 PNG 与该样本 `state_round_id` 所在轮目录一致。

## 7. 打开与关闭

### 启动（本机查看）

```bash
cd /home/www/2026/galaxy_morphology_mcp
/home/www/ENTER/envs/galfit/bin/python -m src.tools.evalset_qc_viewer
```

默认加载 `evalset-20260917T033940Z` run 的全部 `action-*.md`，绑定 `127.0.0.1:8765`，浏览器打开 `http://127.0.0.1:8765/`。

### 启动（同组同事共同查看）

```bash
cd /home/www/2026/galaxy_morphology_mcp
/home/www/ENTER/envs/galfit/bin/python -m src.tools.evalset_qc_viewer --host 0.0.0.0 --port 8792
```

同事在浏览器打开 `http://10.15.49.70:8792/`（服务器内网 IP，以 `hostname -I` 实际输出为准）。仅内网演示用，页面只读、无任何写操作，但内网任何人可见历史拟合数据，演示完即关闭。

需要长期驻留（不随当前终端会话退出）时：

```bash
cd /home/www/2026/galaxy_morphology_mcp
nohup /home/www/ENTER/envs/galfit/bin/python -m src.tools.evalset_qc_viewer --host 0.0.0.0 --port 8792 > /tmp/evalset-qc-viewer.log 2>&1 &
```

### 常用参数

- `--run-dir <path>`：指定其他 full run 目录。
- `--action-md <file> [<file> ...]`：显式载入抽查文件（如 `refit-sample-5pct.md`、`single-band-decision-verification-20pct.md`）。
- `--port`：换端口（被占用时）。

### 关闭

- 前台运行：终端里 `Ctrl+C`。
- 后台／nohup 运行：`pkill -f evalset_qc_viewer`，或先 `pgrep -af evalset_qc_viewer` 找到 PID 再 `kill <PID>`。

### 注意事项

- **数据更新后无需重启**：server 每次响应首页请求时检查 7 个 `action-*.md`、`evaluation-action-adjudications.jsonl`、`evaluation-decision-candidates.jsonl` 的 mtime，发现变化即自动重建页面（HTML 带 `Cache-Control: no-store`）。修改这些文件后，浏览器普通刷新（F5）即可看到新数据，清缓存或重启 server 都不需要。`evaluation-state-manifests/` 与决策 md 的内容变更不触发自动重建（历史数据只读，通常不变；确需刷新时重启 server）。
- 由 Claude Code 会话内启动的实例会随会话结束而停止；供同事访问前建议用 nohup 方式在独立终端启动。
- 服务器重启后需重新执行启动命令。

