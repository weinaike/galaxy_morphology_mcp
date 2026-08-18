# 成分分析 shadow mode 运行说明

## 运行边界

shadow mode 读取已经完成的 GalfitS 轮次，生成新模块的结构化证据与决策产物。它不执行拟合，不修改现有 workflow，也不把新模块的动作写回原拟合轮次。

输出目录必须与原拟合轮次目录分开。一次运行会写入：

- `manifest.json`
- `numeric_evidence.json`
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

## 使用现有 OpenAI-compatible 配置

项目根目录 `.env` 中配置以下变量即可，凭证不会写入 artifact：

```text
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api2.road2all.com/v1
OPENAI_MODEL=gemini-3.1-pro-preview
```

这里的 provider 名称是“OpenAI-compatible gateway”，不是模型厂商名称；`OPENAI_MODEL` 是网关实际接受的模型 ID。受控 callback 已封装为 `OpenAICompatibleVLM`：它读取同一份配置，发送 comparison PNG 和版本化 prompt，要求 JSON object 响应，并把 model ID 写入 `vlm_evidence.json`。

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
def vlm_callback(comparison_png: str, prompt: str) -> str:
    # 调用获准使用的 VLM，并返回模型的原始文本。
    ...

result = run_shadow_round(
    manifest,
    output_dir="/tmp/obj170-shadow-vlm",
    vlm_callback=vlm_callback,
)
```

回调接收 comparison PNG 路径和受控 prompt，必须返回原始字符串。parser 会校验严格 JSON、受控标签、目标 ID 和层边界；超时、拒绝或解析失败会写入对应状态，不会触发拟合动作。

## 当前限制

- 规则层使用的 v1 派生事实已经从真实 FITS／gssummary 生成，包括源尺度、外层等照度几何、径向残差、中心过量与可分辨尺度、m=1／m=2、Bar profile、单 Sérsic n 和 Bar／Disk 参数关系。
- 局部峰已经通过 WCS 合并为稳定候选 ID，并记录检测波段和位置散布；`original_source_matches` 当前采用原图局部对比 SNR，catalog 佐证仍待接入。
- Bar 的三态 PSF 否决已实现。候选 Bar 尺度环带中的实际卷积 PSF 与原图／残差 `m=6` 轴方向相符时记录 `psf_veto=true`；显著测量且错向时记录 `false`；PSF 谐波、图像谐波或覆盖率未过质量门时记录 `null`。`PA_V3` 作为 science header 元数据保留，但不使用未经验证的固定角度旋转 PSF。
- 真实 VLM provider 已接入 callback。VLM 断供、认证失败或严格 JSON 解析失败仍会记录对应状态，并按规范走纯数值降级；只有 `parse_status=OK` 才能作为完整的新旧方案科学对照的一部分。
- 七波段逐波段等照度拟合当前运行时间较长，后续 benchmark 需要记录性能并决定是否缓存中间表。
