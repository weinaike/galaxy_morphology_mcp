# 成分分析 shadow mode 运行说明

## 运行边界

shadow mode 读取已经完成的 GalfitS 轮次，生成新模块的结构化证据与决策产物。它不执行拟合，不修改现有 workflow，也不把新模块的动作写回原拟合轮次。

输出目录必须与原拟合轮次目录分开。一次运行会写入：

- `manifest.json`
- `numeric_evidence.json`
- `candidate_overlay.png`
- `vlm_prompt.txt`
- `vlm_response.raw.json`
- `vlm_evidence.json`
- `decision_artifact.json`

## 输入

每次运行需要显式提供一个拟合轮次的：

- round 目录。
- lyric 文件。
- gssummary 文件。
- 可选的 `all_bands_comparison.png`。

适配器从 lyric 读取逐波段 science、PSF、mask、pixel scale 和 fitting region，并按明确的 GalfitS result 文件名读取 residual、sigma、model 和 original HDU；不会使用目录 glob 猜测输入。

历史 lyric 中的 `obj0`、`obj1` 等 profile 名称会在 shadow runner 内规范化为语义成分：优先使用 profile 类型和相邻注释，`nucleus` 统一为 `agn`，`edgeondisk` 统一为 `edge_on_disk`；单一无注释的 `obj0` Sérsic profile 才回退为 `disk`。无法从证据解析的泛化名称会直接报错，不会作为未知成分传入规则层。`sersic_f` 且 `P?21) 1` 会额外记录 `fourier_m1`。

## 数值层 dry-run

当前项目基础环境缺少可导入的 `astropy`，应使用已有的 galfit conda 环境：

```bash
PYTHONPATH=src /home/www/ENTER/envs/galfit/bin/python - <<'PY'
from component_analysis import build_manifest, run_shadow_round

round_dir = (
    "/home/www/2026/GALFITS_examples/jwst0716/170/output/"
    "20260717_161120_obj_170_iter5"
)
manifest = build_manifest(
    round_dir=round_dir,
    lyric_file=f"{round_dir}/obj_170_iter5.lyric",
    summary_file=f"{round_dir}/obj170.gssummary",
    comparison_png=f"{round_dir}/all_bands_comparison.png",
)
result = run_shadow_round(
    manifest,
    output_dir="/tmp/obj170-shadow",
)
print(result["decision_artifact"]["action"])
PY
```

不传 `vlm_callback` 时，VLM evidence 明确记录为 `REFUSED`，规则层走已定义的纯数值降级路径。这个结果用于验证数据链路，不能当作完整的新旧方案科学对照。

## 轮次级开发集批量运行

`docs/component-analysis/shadow-dev-set-jwst0716-rounds.json` 是当前 116 个轮次级输入清单，唯一专家终态真值来源是 `expert-final-labels.json`。批量入口默认运行数值层、VLM 和规则层，不执行 GALFIT；只有显式传入 `--numeric-only` 才跳过 VLM：

```bash
PYTHONPATH=src /home/www/ENTER/envs/galfit/bin/python \
  src/tools/run_component_shadow_devset.py \
  --dataset docs/component-analysis/shadow-dev-set-jwst0716-rounds.json \
  --input-root /home/www/2026/GALFITS_examples/jwst0716 \
  --output-dir /tmp/jwst0716-shadow-rounds \
  --summary-file /tmp/jwst0716-shadow-results.json
```

大样本运行可按对象 shard 并行；同一 shard 内会缓存相同对象／波段的原图等照度拟合表，避免重复计算：

```bash
--shard-index 0 --shard-count 4
```

每轮完整 artifact 位于 `--output-dir` 下，人工复核摘要由 `--summary-file` 写出，包含规范化后的当前成分、专家终态成分、关系分桶、动作类型、规则轨迹、数值质量计数和 `needs_review`。numeric-only 运行的 `needs_review=true` 是 VLM 未调用的保守标记，不等于拟合失败。
人工逐行对照可直接使用 `docs/component-analysis/shadow-dev-review-table-jwst0716.tsv`；其中 `proposal_vs_expert` 区分拟议成分是否属于专家终态，`proposal_vs_source` 区分当前轮次是否已经包含该成分。完整 VLM 运行的每轮摘要还包含 `vlm_parse_status`、`vlm_model_id` 和 `vlm_error`；只有 `vlm_parse_status=OK` 才算 VLM 成功参与的决策。


## 116 轮完整 VLM 结果

完整 VLM 汇总位于 `docs/component-analysis/shadow-dev-results-jwst0716-vlm.json`，逐轮对照表位于 `docs/component-analysis/shadow-dev-review-table-jwst0716-vlm.tsv`，与 numeric-only 的动作差异位于 `docs/component-analysis/shadow-dev-vlm-vs-numeric-jwst0716.json`。

本次 116 轮均生成最终 decision artifact，runner failures 为 0；其中 97 轮 VLM 严格解析成功，19 轮因模型输出不符合受控 JSON／语义约束而进入已标记的数值降级路径。完整 VLM 最终动作是 48 轮 PROPOSE_ADD、68 轮 KEEP_AND_CONTINUE；与 numeric-only 基线相比，21 轮动作发生变化。

四类 artifact（manifest、numeric evidence、VLM evidence、decision artifact）逐轮通过 schema 校验。19 轮 PARSE_FAILED 仍保留原始模型返回和解析错误，不能与 97 轮 VLM 成功结果混合计算 VLM 决策准确率。

## 使用现有 OpenAI-compatible 配置

项目根目录 `.env` 中配置以下变量即可，凭证不会写入 artifact：

```text
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api2.road2all.com/v1
OPENAI_MODEL=gemini-3.1-pro-preview
```

这里的 provider 名称是“OpenAI-compatible gateway”，不是模型厂商名称；`OPENAI_MODEL` 是网关实际接受的模型 ID。受控 callback 已封装为 `OpenAICompatibleVLM`：它读取同一份配置，发送 shadow runner 生成的 `candidate_overlay.png` 和版本化 prompt，要求 JSON object 响应，并把 model ID 写入 `vlm_evidence.json`。原始 `comparison_png` 保留为未修改的参考图。

```python
from component_analysis import OpenAICompatibleVLM, build_manifest, run_shadow_round

vlm = OpenAICompatibleVLM()
result = run_shadow_round(
    manifest,
    output_dir="/tmp/obj170-shadow-vlm",
    vlm_callback=vlm,
)
```

## 接入 VLM

`run_shadow_round` 只依赖一个受控回调边界：

```python
def vlm_callback(candidate_overlay_png: str, prompt: str) -> str:
    # 调用获准使用的 VLM，并返回模型的原始文本。
    ...

result = run_shadow_round(
    manifest,
    output_dir="/tmp/obj170-shadow-vlm",
    vlm_callback=vlm_callback,
)
```

回调接收 candidate overlay 路径和受控 prompt，必须返回原始字符串。overlay 中的 `candidate_N` 位置由数值层绘制；VLM 只输出这些 ID，不接收自由文本坐标。parser 会校验严格 JSON、受控标签、目标 ID 和层边界；超时、拒绝或解析失败会写入对应状态，不会触发拟合动作。

## 当前限制

- 规则层使用的 v1 派生事实已经从真实 FITS／gssummary 生成，包括源尺度、外层等照度几何、径向残差、中心过量与可分辨尺度、m=1／m=2、Bar profile、单 Sérsic n 和 Bar／Disk 参数关系。
- 局部峰已经通过 WCS 合并为稳定候选 ID，并记录检测波段和位置散布；`original_source_matches` 当前采用原图局部对比 SNR，catalog 佐证仍待接入。
- Bar 的三态 PSF 否决已实现。候选 Bar 尺度环带中的实际卷积 PSF 与原图／残差 `m=6` 轴方向相符时记录 `psf_veto=true`；显著测量且错向时记录 `false`；PSF 谐波、图像谐波或覆盖率未过质量门时记录 `null`。`PA_V3` 作为 science header 元数据保留，但不使用未经验证的固定角度旋转 PSF。
- 真实 VLM provider 已接入 callback。VLM 断供、认证失败或严格 JSON 解析失败仍会记录对应状态，并按规范走纯数值降级；只有 `parse_status=OK` 才能作为完整的新旧方案科学对照的一部分。
- 七波段逐波段等照度拟合当前运行时间较长，后续 benchmark 需要记录性能并决定是否缓存中间表。
