# 星系形态学 MCP 工具

一个用于星系形态学分析的 MCP (Model Context Protocol) 服务器，封装了 GALFIT 和 GalfitS 工具，支持单波段和多波段星系图像拟合与分析。同时提供基于 FastAPI + Celery 的 HTTP 服务接口，支持异步任务提交。

## 功能特性

### 核心工具

| 工具名称 | 功能描述 | 可用条件 |
|---------|---------|---------|
| `run_galfit` | 执行 GALFIT 单波段拟合，返回优化的 FITS 文件、对比图像和拟合摘要 | 需设置 `GALFIT_BIN` |
| `run_galfits` | 执行 GalfitS 多波段同时拟合，返回摘要文件、图像、SED 模型等结果 | 需设置 `GALFITS_BIN` |
| `galfits_analyze_by_vlm` | 使用多模态大模型分析 GalfitS 多波段拟合结果（图像 + SED + 摘要） | 需设置 `GALFITS_BIN` |
| `view_original_image` | 分析原始星系图像，提取形态分类和结构组件信息 | 要求提供2 panel图 |
| `component_analysis` | 分析拟合残差图像，诊断缺失或配置不当的物理组件（bulge、disk、bar、AGN 等） | 始终可用 |

### 输出说明

**GALFIT 输出：**
- `optimized_fits_file`: 包含原始数据、模型和残差的 FITS 文件
- `image_file`: 三栏对比图（原始数据 | 模型 | 残差/σ）
- `summary_file`: Markdown 格式的拟合参数摘要

**GalfitS 输出：**
- `summary_files`: `.gssummary` 拟合摘要文件
- `imagefit_pngs`: 多波段图像拟合对比图
- `sedmodel_pngs`: SED (光谱能量分布) 模型图
- `result_fits`: 最佳拟合 FITS 结果文件

### HTTP 服务接口

除 MCP 协议外，还提供基于 FastAPI + Celery 的 HTTP 服务，支持异步拟合任务提交：

- `POST /api/fitting/` — 提交拟合任务（支持 image fitting / pure sed fitting / image sed fitting 三种模式）
- `GET /api/fitting-status/{task_id}` — 查询任务状态
- `GET /health` — MCP 服务健康检查
- `GET /api/tools` — 列出当前可用的 MCP 工具

## 安装

### 环境要求

- Python >= 3.10
- GALFIT（用于单波段拟合，可选）
- GalfitS（用于多波段拟合，可选）

### 通过 pip 安装

```bash
pip install -e .
```

### Docker 部署

```bash
docker-compose up -d
```

服务包含两个容器：
- **fastapi**: Web 服务 (端口 8000)，负责任务提交和状态查询
- **celery**: 异步任务 Worker，执行后台拟合计算

## 配置

根据使用场景选择配置方式：

- **本地开发/直接运行**：创建 `.env` 文件（参考 `.env.example`），服务启动时自动加载。
- **MCP 客户端接入（如 Claude Code）**：优先在 `.mcp.json` 的 `env` 字段中配置环境变量，无需 `.env` 文件。

```bash
# LLM API 配置（用于多模态分析）
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=           # 可选，默认使用官方端点
OPENAI_MODEL=gemini-3-flash-preview  # 推荐 gemini-3-flash-preview（性价比高）

# 分析后端选择：vlm（默认）、cc 或 acp
# vlm: 使用 OpenAI 兼容 API 进行分析（需配置 OPENAI_*）
# cc:  使用 Claude Code Agent SDK 进行分析（需配置 CLAUDECODE_*）
# acp: 使用 Gemini CLI ACP 模式进行分析（默认使用本地已登录会话）
ANALYSIS_MODE=vlm

# Claude Code Agent SDK 配置（ANALYSIS_MODE=cc 时需要）
# cc 模式通过 claude-agent-sdk 调用 Anthropic API
# 如需使用非 Anthropic 模型（如第三方 LLM），可安装 Claude Code Router 作为本地代理：
# https://github.com/musistudio/claude-code-router
CLAUDECODE_API_KEY=your_anthropic_api_key_here
CLAUDECODE_BASE_URL=xxx
CLAUDECODE_MODEL=gemini-3-flash-preview 

# Gemini CLI ACP 模式配置 (ANALYSIS_MODE=acp 时)
# 默认使用本地通过 `gemini login` 建立的会话。
# 如在 CI 等无登录环境，可配置 GEMINI_API_KEY。
# GEMINI_API_KEY=your_google_api_key_here
# GEMINI_MODEL=gemini-2.0-flash

# GALFIT 配置
GALFIT_BIN=/path/to/galfit  # GALFIT 可执行文件路径

# GalfitS 配置
GALFITS_BIN=/path/to/galfits       # GalfitS 命令或 Python 模块路径
GS_DATA_PATH=/path/to/gs_data      # GalfitS 数据目录

# HTTP 服务（可选）
MCP_ALLOWED_HOSTS=*                # 允许的主机，默认允许所有
```

## 使用方法

### 启动 MCP 服务器

**STDIO 模式**（本地 MCP 客户端，如 Claude Code）：
```bash
python -m mcp_server --transport stdio
```

**HTTP 模式**（远程网络访问）：
```bash
python -m mcp_server --transport http --port 38507
```

支持的启动参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--transport, -t` | 传输模式：stdio 或 http | stdio |
| `--host, -H` | HTTP 监听地址 | 0.0.0.0 |
| `--port, -p` | HTTP 监听端口 | 38507 |
| `--path, -P` | MCP 协议路径 | /mcp |

### 配置 Claude Code

在项目的 `.mcp.json` 中添加：

```json
{
  "mcpServers": {
    "galmcp": {
      "command": "python",
      "args": [
        "src/mcp_server.py",
        "--transport",
        "stdio"
      ],
      "env": {
        "GALFIT_BIN": "/usr/bin/galfit",
        "GALFITS_BIN": "python /path/to/GalfitS/src/galfits/galfitS.py",
        "GS_DATA_PATH": "/path/to/GalfitS",
        "OPENAI_API_KEY": "your-apikey",
        "OPENAI_BASE_URL": "https://open.bigmodel.cn/api/coding/paas/v4",
        "OPENAI_MODEL": "glm-4.6v",
        "ANALYSIS_MODE": "vlm",
        "CLAUDECODE_API_KEY": "your-anthropic-apikey",
        "CLAUDECODE_BASE_URL": "",
        "CLAUDECODE_MODEL": "gemini-3-flash-preview"
      }
    }
  }
}
```

> **分析模式说明：** `ANALYSIS_MODE` 控制残差分析（`component_analysis`）的后端：
> - `vlm`（默认）：通过 OpenAI 兼容 API 调用多模态模型，需配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。
> - `cc`：通过 Claude Code Agent SDK 调用 Anthropic API，需配置 `CLAUDECODE_API_KEY`。支持通过 [Claude Code Router](https://github.com/musistudio/claude-code-router) 代理到其他 LLM 提供商。
> - `acp`：通过 Gemini CLI ACP 模式调用分析，默认使用 `gemini login` 后的本地会话。

## 项目结构

```
src/
├── mcp_server.py          # MCP 服务主入口，工具注册与传输配置
├── tools/
│   ├── run_galfit.py      # GALFIT 单波段拟合执行
│   ├── run_galfits.py     # GalfitS 多波段拟合执行
│   ├── analyze_image.py   # VLM 多模态分析（GALFIT/GalfitS 结果）
│   ├── view_original_image.py  # 原始星系图像形态分类
│   ├── component_analysis.py   # 残差分析与组件诊断
│   ├── modify_feedme.py   # GALFIT feedme 配置文件修改
│   ├── extract_summary_galfit.py  # GALFIT 参数摘要提取
│   ├── pix2radec.py       # 像素坐标转赤经赤纬
│   ├── read_fits.py       # FITS 文件读取工具
│   ├── multi_thresh_plot.py  # 多阈值可视化
│   └── prompt.py          # 工作流 Prompt 定义
├── service/
│   ├── main.py            # FastAPI 应用，HTTP 任务提交接口
│   ├── tasks.py           # Celery 异步任务定义
│   └── file_manager.py    # 文件与工作空间管理
├── llms/
│   ├── base.py            # LLM 客户端基类
│   ├── openai_llm.py      # OpenAI API 客户端
│   └── glm_llm.py         # 智谱 GLM API 客户端
└── prompts/               # Prompt 模板（分类、分析、工作流）
```


## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
