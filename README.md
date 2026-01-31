# 星系形态学 MCP 工具

一个用于星系形态学分析的 MCP (Model Context Protocol) 服务器，封装了 GALFIT 和 GalfitS 工具，支持单波段和多波段星系图像拟合与分析。

## 功能特性

### 核心工具

| 工具名称 | 功能描述 |
|---------|---------|
| `run_galfit` | 执行 GALFIT 单波段拟合，返回优化的 FITS 文件、对比图像和拟合摘要 |
| `run_galfits` | 执行 GalfitS 多波段同时拟合，返回摘要文件、图像、SED 模型等结果 |
| `galfit_analyze_by_vllm` | 使用多模态大模型分析 GALFIT 拟合结果（图像 + 摘要） |
| `galfits_analyze_by_vllm` | 使用多模态大模型分析 GalfitS 多波段拟合结果（图像 + SED + 摘要） |

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

## 安装

### 环境要求

- Python >= 3.10
- GALFIT (用于单波段拟合)
- GalfitS (用于多波段拟合，可选)

### 通过 pip 安装

```bash
pip install -e .
```


## 配置

创建 `.env` 文件（参考 `.env.example`）：

```bash
# OpenAI API 配置（用于多模态分析）
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=           # 可选，默认使用官方端点
OPENAI_MODEL=gpt-4o        # 可选，默认 gpt-4o

# GALFIT 配置
GALFIT_BIN=/path/to/galfit  # GALFIT 可执行文件路径

# GalfitS 配置
GALFITS_BIN=/path/to/galfits       # GalfitS 命令或 Python 模块路径
GS_DATA_PATH=/path/to/gs_data      # GalfitS 数据目录
```

## 使用方法

### 启动 MCP 服务器

**STDIO 模式**（本地 MCP 客户端，如 Claude Desktop）：
```bash
python -m mcp_server --transport stdio
```

**HTTP 模式**（远程网络访问）：
```bash
python -m mcp_server --transport http --port 38507
```

### 配置 Claude Desktop

在 Claude Code 配置文件.mcp.json中添加：

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
        "GALFITS_BIN": "/home/wnk/miniconda3/envs/galfits/bin/python /home/wnk/code/GalfitS/src/galfits/galfitS.py",
        "GS_DATA_PATH": "/home/wnk/code/GalfitS",
        "OPENAI_API_KEY": "your-apikey",
        "OPENAI_BASE_URL": "https://open.bigmodel.cn/api/coding/paas/v4",
        "OPENAI_MODEL": "glm-4.6v"
      }
    }
  }
}

```

### 使用工具示例

**运行 GALFIT 拟合：**
```
调用 run_galfit，传入配置文件路径
```

**分析拟合结果：**
```
调用 galfit_analyze_by_vllm，传入：
- image_file: 对比图像路径 (PNG)
- summary_file: 拟合摘要文件路径 (.md)
```

## 已知限制与优化方向

当前版本实现了基本功能，以下方面仍需进一步优化：

### 图像渲染
- 当前的 `_normimg_galfit` 归一化算法可能需要针对不同数据特性进行调整
- galfit输出残差应该采用什么样的渲染方式， 需要天文老师进一步确认

### Summary 提取
- `extract_summary_galfit` 模块对 GALFIT 输出的解析依赖于固定格式，可能无法兼容所有版本
- summary应该包含哪些参数，对于拟合判断有益，需要天文老师进一步确认

### galfits summary
- galftits 已经形成summary 文件， 其中所有内容都需要关注吗？

### 其他
- 错误处理和日志记录可以更加完善
- 需要更全面的单元测试覆盖

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
