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

## 配置文件与诊断指南

### GalfitS Manual SKILL

项目包含完整的 GalfitS 配置文档（`/skill galfits-manual`），用于指导 `.lyric` 配置文件的编写和调试。

**文档位置：** `.claude/skills/galfits-manual/`

**主要章节：**

| 文件 | 描述 |
|------|------|
| `SKILL.md` | 导航索引和快速参考 |
| `data-config.md` | 数据配置（Region R, Image I, Spectrum S, Atlas A） |
| `model-components/` | 模型组件（Galaxy G, Profile P, Nuclei/AGN N, Foreground Star F） |
| `constraints/` | 参数约束（MSR、MMR、SFH、AGN 关系等） |
| `examples/` | 配置示例（多波段、纯 SED、联合拟合等） |
| `running-galfits.md` | 命令行参数和运行选项 |

**使用方法：**
在 Claude Code 中输入 `/skill galfits-manual` 可查阅完整文档。Claude code在执行过程中，会自动调用该skill以辅助理解和生成 GalfitS 配置文件。

### CLAUDE.md 诊断指南

`CLAUDE.md` 文件提供了 GalfitS 拟合结果的系统性诊断方法，用于评估拟合质量并指导参数调整。

**典型问题诊断（Case A-E）：**

| 案例 | 残差特征 | 处理方法 |
|------|----------|----------|
| Case A | 蓝/红颜色分裂（波段未对齐） | 允许波段间位置偏移，调整 Ia13/Ia14 |
| Case B | 偏心亮源残差 | 在该位置添加新的 Sersic 组件 |
| Case C | 穿过中心的长条状正残差 | 添加 bar（棒）组件 |
| Case D | 中心圆形正残差 | 添加 bulge 或 AGN 点源组件 |
| Case E | 模型结构与输入图像显著偏离 | 重新调整初始参数（轴比、位置角、中心坐标） |

## 已知限制与优化方向

当前版本已实现基本功能，以下方面需进一步优化：

### GalfitS Manual 准确性
- 文档中的参数格式和范围可能需要根据实际数据特性进行验证和调整， 文档中可能存在不准确之处，需要天文老师协助确认

### 残差图像描述方法
残差图像（原始 - 模型）的视觉描述具有挑战性：

**当前困难：**
- 残差图是二维连续信号，难以用文字准确传达空间结构
- 不同信噪比波段的残差视觉特征差异显著
- 系统性残差（如 Case A-E）的识别依赖于主观经验
- 文字描述可能无法充分传达残差的强度和分布特征

**优化方向：**
- 需要开发更结构化的残差描述框架
- 考虑引入定量指标（如残差的径向分布、傅里叶分析等）辅助定性描述
- 建立标准化的残差图像模板库，用于对比和参考
- 与天文老师确认不同数据类型（低/高信噪比、不同波段）的残差描述标准

### Summary 提取与分析
- `.gssummary` 文件中的参数众多，需要确认哪些参数对拟合判断最有价值
- Summary 应包含哪些参数以便于 AI 模型快速识别问题


## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
