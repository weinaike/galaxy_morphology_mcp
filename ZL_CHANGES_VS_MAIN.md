# 本地改动与最新 main 对比报告

## 对比基线

- 工程：`galaxy_morphology_mcp`
- 当前分支：`platform-communication-module`
- 当前提交：`0e442c392a78ee64e72a2aa4e120f9d9d3a5c9b4`
- 对比基线：`origin/main` (`5e513a3aa1424a2d5be27f26682c3bb36d7ba9e1`)
- 对比原则：以 `git diff origin/main` 的实际文件内容为准，不以 `git status` 相对旧索引的状态为准。

## 结论

除本报告外，本地相对最新 main 有 **8 个已跟踪文件发生内容变化**，以及 **2 个新增未跟踪文件**。

这些差异分为三类：

1. 拟合完成事件与外部通信解耦：5 个文件（其中 2 个为新增文件）。
2. zl 的批量拟合脚本：4 个文件（3 个脚本及其 `tqdm` 依赖）。
3. 无业务意义的文件末尾换行：1 个文件。

## 一、通信功能相关改动

### 新增文件

#### `src/tools/fit_event_publisher.py`

拟合完成事件的独立发布适配器：

- `existing_artifacts()`：收集实际存在的拟合产物，转为绝对路径并去重。
- `publish_fit_round()`：读取 `FIT_ROUND_EVENT_URL`，将 `fit_round_finished` 事件 POST 到外部 `communication_service`。
- 未设置事件地址时直接 no-op，因此 MCP 独立运行或普通本地使用不会产生网络请求。
- MCP tool 的公开参数中不包含 `callback_url` 等通信字段。

当前还保留旧环境变量名 `FIT_ROUND_CALLBACK_URL` / `FIT_ROUND_CALLBACK_TOKEN` 作为兼容回退；它们不是 tool 参数。

#### `tests/test_fit_event_publisher.py`

事件发布器的回归测试：

- 验证未配置服务环境变量时不发送请求。
- 验证配置 `FIT_ROUND_EVENT_URL` 后按标准事件结构发送请求。

### 修改文件

#### `src/tools/run_galfit.py`

单波段 GALFIT 拟合完成后的新增行为：

- 新增 `_json_safe()`，将 NumPy、数组和 PathLike 等结果转换为可写入 JSON 的数据。
- 拟合归档完成后生成 `round_status.json`。
- 状态文件记录输入、输出产物以及拟合统计量。
- 调用 `publish_fit_round()` 发布 `fit_round_finished`，`fitter` 为 `galfit`。
- 返回结果新增 `round_status_file` 和 `fit_statistics`。
- 返回的输入参数文件、输出参数文件改为归档后的实际路径。
- 记录归档后的 constraint、fit.log、galfit 参数文件等路径。

公开方法仍为：

```python
async def run_galfit(config_file, options=[])
```

没有通信参数。

#### `src/tools/run_galfits.py`

多波段 GalfitS 公共执行层 `run_galfits()` 的新增行为：

- 拟合完成后收集 `.gssummary`、PNG、FITS、约束、参数和日志等产物。
- 在本轮 workplace 中生成 `round_status.json`。
- 调用 `publish_fit_round()` 发布 `fit_round_finished`，`fitter` 为 `galfits`。
- 返回结果新增 `round_status_file`。

三个分阶段公共 wrapper 没有修改：

- `run_galfits_image_fitting()` 仍直接调用 `run_galfits()`。
- `run_galfits_sed_fitting()` 与最新 main 一致，不发送通信事件。
- `run_galfits_image_sed_fitting()` 仍直接调用 `run_galfits()`。

因此 image fitting 和 image-SED fitting 统一由真正执行拟合的 `run_galfits()` 发布事件，中间 SED 配置生成阶段不重复拉起通信链路。

#### `README.md`

新增“外部编排与轮次事件”说明：

- MCP 工程不负责拉起 Agent。
- 外部 `communication_service` 负责拉起 Codex/Claude，并以 stdio 挂载 MCP。
- tool API 不包含通信字段。
- 通过环境变量 `FIT_ROUND_EVENT_URL` 进行任务级事件投递。

## 二、zl 新增的批量拟合功能

以下文件全部不存在于最新 main，是 zl 分支新增的独立批处理脚本，不属于通信模块本身。

### `scripts/run_gadotti_data.py`

- 批量发现和执行 Gadotti 数据集的 GALFIT feedme。
- 支持并发、覆盖控制、进度展示和拟合产物整理。
- 调用公共 `run_galfit()`，不直接执行 GALFIT shell 命令。

### `scripts/run_galfit_S4G.py`

- 批量处理 S4G feedme。
- 排除 reduction 目录。
- 支持输出目录映射、并发、覆盖控制、进度展示和产物整理。
- 调用公共 `run_galfit()`。

### `scripts/run_galfit_overfit_data_zl.py`

- 批量运行 overfit GALFIT feedme。
- 支持模板筛选、每个样本数量限制和 dry-run。
- 整理输出产物，并把二维/一维拟合指标追加到 CSV 日志。
- 调用公共 `run_galfit()`。

### `pyproject.toml`

- 新增依赖：`tqdm>=4.66.0`。
- 该依赖主要服务于上述批量脚本的异步进度条，不属于通信发布器依赖；`requests` 在最新 main 中已经存在。


## 三、最终通信边界

```text
平台请求
  -> 外部 communication_service
      -> 按 data_type 选择 single_band / multi_band prompt
      -> 拉起 Codex 或 Claude Agent
      -> 通过 stdio 启动 galaxy_morphology_mcp
      -> 注入任务级 FIT_ROUND_EVENT_URL
          -> single-band: Agent 调用 run_galfit
          -> multi-band: Agent 调用 run_galfits_* wrapper
              -> image / image-SED 最终进入 run_galfits
      <- 拟合完成后发布 fit_round_finished
```

外部 `communication_service` 是另一个工程，不包含在本报告的 `origin/main` 文件差异统计中。
