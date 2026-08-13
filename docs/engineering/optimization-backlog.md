# galmcp 工程优化清单

> 来源：2026-07-28 工程审计（只读审计，10 维度，187 次工具调用）
> 性质：待办清单，非已完成。每条带 file:line 便于定位。
> 优先级图例：🔴 必须（可靠性/安全）｜ 🟡 应该（可维护性/调试）｜ 🟢 缓做（锦上添花）

---

## 总体判断

代码功能完整、能跑，但工程化程度与科研价值不匹配。主要问题：错误处理粗糙、超时/网络调用无防护、大量重复代码、测试基本是坏的。均为机械性修复，不需重构架构。

**与论文的关系**：第 6、7 条（prompt drift、静默错误）正是 memory 里 analyzer 误判（hallucinate 伴星系、矛盾归因）的工程根因。修这些 = 让论文实验对象（VLM 分析）稳定可控，工程修复与论文实验是同一件事的两面。

---

## 🔴 必须修（影响可靠性 / 安全风险）

| # | 位置 | 问题 | 修法 |
|---|------|------|------|
| 1 | `src/tools/cc_analysis.py:85` | `permission_mode="bypassPermissions"` 在宿主文件系统上放权跑子 agent，**安全风险最高** | 改受限权限 + 收紧 cwd |
| 2 | `src/tools/run_galfits.py:846` + `galfits_fitting.py:328` | `run_galfits_sed_fitting` 的 `timeout_sec` 是死参数（声明了没传），真正生效的是 `galfits_fitting.py:328` 硬编码 `1800`。**这是 memory `galmcp-sed-tool-silent-timeout` 的代码根因**——5 成分星系 = 5×30min = 2.5h 无进度 | timeout 一路传到 `PureSEDFitting`→`ImageFitting` |
| 3 | `src/service/file_manager.py:101,193,217` + `src/service/tasks.py:58,70,80` | download/upload/callback post 都无 `timeout=`，挂起则永久阻塞 | 加 `(connect, read)` 元组超时 + 有界重试 |
| 4a | `tests/test_claude_integration.py:40` | 调 `component_analysis(..., mode="single-band")`，但该函数无 mode 参数 → TypeError | 改测试或补参数 |
| 4b | `tests/test_run_galfits.py:30-38` | 断言 `fitting_log.md` 存在，但源码无人写此文件 → 功能已删测试未同步 | 删测试或恢复功能 |

---

## 🟡 应该修（可维护性 / 调试）

| # | 位置 | 问题 | 修法 |
|---|------|------|------|
| 5 | 21 个文件 117 处 `print()` | MCP 是 stdio 协议，print 到 stdout 会污染协议通道 | 统一 `logging`，handler 走 stderr |
| 6 | `residual_analysis.py:76` vs `:290`；`run_galfits.py:151-388` vs `:390-652` | `analyze_multiband_components` 与 `component_analysis` 90% 重复且 prompt 飘移（短版 vs 长版带例子）；perband/multiband comparison png 同样 90% 重复 | 合并为带 `mode` 参数的单函数；prompt 抽共享常量 |
| 7 | `run_galfit.py:138,450,439,196,210,222`；`extract_summary_galfit.py:39,611`；`parse_lyric.py:116,180` | 静默吞错（`except Exception: return None`、裸 `except:`），出错无任何痕迹 | `except Specific: logger.exception(...)` + 结构化错误返回 |
| 8 | `openai_analysis.py:128-134` | 重试对所有异常（含鉴权失败、模型不存在等非瞬时错误）都重试 3×10s | 仅对超时/限流/5xx 重试，其余 fail-fast |
| 9 | `modify_lyric.py:194`（标注 `-> str` 实返 dict）；`render_original.py:199`（`"image files"` 带空格 vs `"image_file"`）；`galfits_fitting.py`（`"error"` vs `"failed"`，tasks.py 强行改写掩盖） | 返回类型谎报 + status 词汇不一致 + key 命名漂移 | 统一 schema 与 status 词表 |

---

## 🟢 缓做（低优先 / 锦上添花）

- 像素尺度 / PSF FWHM / 模型名 / temperature 散落多处硬编码（温度居然有 0.2/0.3/0.7 三个值：`view_original_image.py:86`、`openai_analysis.py:123`、`base.py:98`）→ 抽配置。
- `run_galfit` 一个函数干 6 件事（解析→跑→画图→summary→归档→拼消息，180 行）→ 拆分。
- 大图 base64 内嵌（`openai_analysis.py:45`）可能超 OpenAI 25MB 请求上限，无尺寸防护 → 加 size guard / 走 Files API。
- 无 CI（`.github/` 无 workflow），测试仅本地跑 → 加 GitHub Actions 跑现有单测，至少暴露坏测试。
- 副作用：`best_round_registry.py:88` 写 `.best_round.json`、`residual_analysis.py:248` 写 `*_component_analysis_<session>.md` 到**用户星系目录**（与 memory 目录污染同类问题）→ 统一写到可配置 artifacts 目录。
- `prompts/__init__.py:23` 类级 Singleton 缓存 prompt 永久不失效 → 加 `clear_cache()` 或 stat mtime。
- `fourier_mode_analysis.py:10`、`best_round_registry.py:50` 50 行 prompt 字符串硬编码在源码 → 迁到 `prompts/*.md`。
- `galfits_fitting.py:18` `ALL_BANDS` 与 `MAG_ZERO_POINTS` 必须手动同步 → 改 `{band: zp}` 字面量。
- `run_galfits.py:900` 测试函数含 `/home/jiangbo/...` 绝对路径已提交 → 移出源码或 `if __name__` 守卫。

---

## 重复代码热点（去重重灾区）

| 重复对 | 重叠 | 行动 |
|--------|------|------|
| `analyze_multiband_components` ↔ `component_analysis`（residual_analysis.py） | ~90%，200 行 scaffold | 抽 `_run_component_analysis_pipeline(image, summary, lyric_file=None)` |
| `create_perband_comparison_png` ↔ `create_multiband_comparison_png`（run_galfits.py:151-652） | ~90%，仅 GridSpec 布局不同 | 抽 `_render_band_row(gs_row, bdata, ...)` |
| `_run_async`（openai_analysis.py:30 ↔ cc_analysis.py:41） | 逐字节相同 | 移到 `src/tools/_async_utils.py` |
| `_parse_gssummary`（run_galfits.py:103）↔ `parse_gssummary`（parse_lyric.py:216） | 两套独立 .gssummary 解析器，schema 略异 | 留 line-iterator 版，删另一版 |
| `_generate_subcomps`（run_galfit.py:87）+ parse_lyric.py:717 注释死代码 + :756 第三变体 | 三份 subcomps 实现 | 删注释块，文档化调用路径 |

---

## 流程层面建议

1. **加 CI**：跑现有单测即可立刻暴露第 4a/4b 两条坏测试，防继续腐烂。
2. **建 fixture 回归库**：几个已知好结果的拟合样本（盘 / 棒 / 侧视 / 致密各一），改代码后回归跑。同时是论文 benchmark 雏形。
3. **VLM 分析输入/输出结构化落盘**：现在散在星系目录无法批量统计。结构化后，"VLM 在 N 个星系上的准确率"等论文指标免费可得。

---

## 推荐动手顺序

1. 🔴2 SED timeout 死参数（有 memory 教训背书，范围小、收益立竿见影）
2. 🔴1 cc_analysis bypassPermissions（安全）
3. 🔴3 网络调用超时
4. 🔴4 修/删坏测试
5. 🟡6 合并重复 component_analysis（同时稳定论文实验对象）
6. 其余按优先级排
