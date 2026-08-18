# 项目路线图

## 当前阶段

成分分析重构：实施顺序第 5 步进行中。规则层所需的 v1 派生数值事实、WCS 跨波段候选合并、PSF 模板方向否决和 OpenAI-compatible VLM callback 已在 `obj_170` 最优轮以 shadow mode 完成验证；下一步扩大到 held-out 历史轮次并校准阈值。现有 workflow 未接管、未修改。

## 已完成

- 已确认保留数值证据层、VLM 形态证据层、确定性规则与迭代控制层三层架构。
- 已确认正式成分白名单为 Disk、Bulge、Edge-on Disk、Bar、AGN、Fourier m=1、Companion 七类（2026-08-13 更新：Lens 保留并升入白名单，现为八类）。
- 已确认 AGN 与 `compact central source candidate` 的物理身份分开。
- 已确认 GalfitS Disk 使用 `sersic` 且确认后固定 `n=1`。
- 已确认中心成分采用严格同心审计规则。
- 已确认尺寸层级作为通常物理期望和复核依据，不作为无条件硬失败。
- 已确认可选成分默认使用 `BIC_gain >= 10` 统计门槛，并且必须同时通过残差、拟合、参数和物理证据。
- 已形成 `docs/component-analysis/redesign.md` 与 `docs/component-analysis/spec-sync.md`。
- 2026-08-13：四项开放判据（Disk／Elliptical 最低证据组合、Bar 单高质量波段触发、中心源候选 P 块小 Re Sérsic、Bulge／中心源可分辨尺度门）已裁定为 v1 默认规则，写入 `docs/component-analysis/redesign.md`（升至 v4），阈值待评测校准。
- 2026-08-13：四类 artifact schema（artifact_manifest、numeric_evidence、vlm_evidence、decision_artifact）已冻结 v1.0，位于 `src/schemas/`，含加载与校验入口；`tests/test_schemas.py` 39 个用例全部通过。
- 2026-08-13：实施顺序第 3 步完成：`src/component_analysis/` 实现数值证据层（numeric.py：加权二阶矩、方位 Fourier 模、FWHM 反卷积、孔径 SNR、mask-aware 局部峰检测、numeric_evidence 组装）与纯规则层（rules.py：七类成分 v1 提议规则、重拟合仲裁、BIC 门、阈值版本化），输出均经冻结 schema 校验；未接管现有 workflow。实现由另一 agent 起草，本轮审查后修复 Disk 提议规则中 `and/or` 优先级错误（VLM 支持盘时 N2/N3 应二选一，原写法忽略 N3 路径）；`tests/test_component_analysis.py` 43 个用例全部通过。
- 2026-08-13：完成旧残差分析 prompt 经验条目化盘点（`docs/component-analysis/prompt-experience-inventory.md`，第 4 步子任务 1）：逐条标注去向（RULES／NUMERIC／VLM_LABEL／SPEC／STAY／DROP／REPLACED）。用户裁定：非建模形态（旋臂／壳层／团块等）的识别描述不迁移，仅保留受控标签枚举；调参层量化门（sky fix、伴星系 20px／5 星等门等）留在现有 prompt 不迁移。两项 OPEN（Lens 认定、Nucleus 代偿 Bulge）待规范同步前裁定。
- 2026-08-13：两项 OPEN 裁定落实：Lens 保留并升入正式白名单（八类，按可选成分 `BIC_gain >= 10` 门管理）；「Nucleus 代偿 Bulge、物理意义优先于奥卡姆」废弃，中心源统一走可分辨尺度门分流。`docs/component-analysis/redesign.md` 升至 v5（第六节新增第 8 小节 Lens 判据：Bar 参数异常＋延展区正残差触发，接受条件 `Re_disk > Re_lens > Re_bar`、`n_lens < 0.5`、`q_lens > 0.5`），`docs/component-analysis/spec-sync.md` 与 `docs/component-analysis/prompt-experience-inventory.md` 已同步；`decision_artifact` schema 的 component 枚举增补 `lens`（向后兼容的增量修订，仍为 1.0），`rules.py` 新增 `_lens_rule`（阈值 `re_bar/re_disk >= 0.9` 或 `q_bar > 0.5`，属 v1 默认值待校准）并把 lens 纳入可选成分 BIC 门。
- 2026-08-13：整理项目文档结构：成分分析方案归档至 `docs/component-analysis/`，工程优化清单归档至 `docs/engineering/`，专利材料归档至本地忽略目录 `docs/patent/`；`ROADMAP.md` 保留在仓库根目录作为进度入口。
- 2026-08-13：`INCONCLUSIVE` 自动化消解策略落实（用户确认拟合流程须全自动、无人工阻塞）：`docs/component-analysis/redesign.md` 升至 v6，第四节新增三类消解——试拟合仲裁（降级为弱候选交 `EVALUATE_REFIT` 自动裁决）、保守默认兜底（不改变成分结构）、VLM 断供纯数值降级；试探预算默认 3 次、同一 rule ID 证据未变二次 `INCONCLUSIVE` 即终结、被否候选不重提；人工复核改为事后批量审查 `needs_review` 标志。`decision_artifact` schema 增补可选 `automation` 块（policy 版本、原动作、消解方式、理由、`needs_review`，向后兼容的增量修订仍为 1.0）；新增 `src/component_analysis/policy.py`（automation-policy@v1，不改动规则函数，只包装其输出）；`spec-sync.md` 同步 workflow 行、BIC 表与验收标准。
- 2026-08-17：实施顺序第 4 步完成：新增 `src/component_analysis/vlm.py`，从冻结 schema 派生标签与质量标志枚举，只向 VLM 暴露数值层签发的 `target_id`，不暴露候选坐标；提供版本化受控 prompt、严格 JSON parser、语义冲突检查及 `PARSE_FAILED`／`TIMEOUT`／`REFUSED` 空证据降级。旧 prompt 经验按盘点裁定仅保留受控标签，不迁移识别指南、坐标或动作决策；现有 workflow、残差分析 prompt 和 verifier 均未修改。
- 2026-08-17：新增 `src/component_analysis/artifact_adapter.py`，从显式 round／lyric／gssummary 输入构建 manifest，逐波段校验 result HDU 布局、science／mask shape、WCS、单位和有限值比例，并加载 residual／mask／sigma／model／original 与 PSF；不通过 glob 猜测输入。
- 2026-08-17：新增 `src/component_analysis/shadow.py` 与 `docs/component-analysis/shadow-mode.md`。runner 独立写出 manifest、numeric evidence、受控 VLM prompt／raw response、VLM evidence 和 decision artifact；VLM 通过 callback 隔离，未配置时明确记为 `REFUSED` 并走纯数值降级，不执行拟合或写回 workflow。
- 2026-08-17：真实 JWST PSF 验证发现整幅二阶矩受衍射翼主导，新增中心核半高宽测量，将 `obj_170` 七波段 PSF FWHM 从错误的 `18–31 px` 修正为 `2.65–4.00 px`；局部峰 ID 改为波段限定，消除多波段 `candidate_1` 冲突。
- 2026-08-17：新增 `src/component_analysis/derived.py`，从真实 FITS 派生源尺度／PSF 比、外层等照度几何、外层轴比、连续同号径向残差、中心过量与逐波段分辨尺度、中心 m=2／拉长、原图等照度 m=1、Bar profile 和原图候选对应源；从 gssummary 派生单 Sérsic n 与 Bar／Disk 尺度关系。数值层只输出测量事实和质量状态，不输出成分或动作。
- 2026-08-17：局部残差峰通过 science WCS 合并为稳定的跨波段候选 ID，记录检测波段与位置散布；`obj_170` 的 34 个波段内检测合并为 13 个候选，其中 10 个具有多波段对应。
- 2026-08-17：修正 Bar PSF 否决语义：尺度门通过不能冒充完整衍射检查；`psf_veto` 改为 `true | false | null`，规则层仅接受显式 `false` 的强等照度证据。实际 PSF／V3 方向相关性尚未实现。
- 2026-08-18：完成 PSF 模板方向相关性检查：`measure_directional_harmonic_alignment` 在候选 Bar 尺度环带比较实际卷积 PSF 与原图／残差的 `m=6` 轴方向，允许残差正负号翻转；质量不足保持 `null`，并记录方向来源、相位差、谐波 SNR、覆盖率及可用的 `PA_V3` 元数据。`obj_170` 中 F410M、F277W 命中衍射方向否决，F115W、F150W 因 SNR 不足保持 `null`；没有波段提供 Bar 的显式通过证据。
- 2026-08-18：新增 `OpenAICompatibleVLM`，读取根目录 `.env` 的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`，发送 comparison PNG 与受控 prompt，要求 OpenAI-compatible `response_format=json_object`，并把实际 `model_id` 写入 VLM evidence；不修改共享旧 provider 或现有 workflow。
- 2026-08-18：`obj_170` 最优轮完整 shadow artifact 已落盘。数值层产出 77 项特征，34 个波段内峰合并为 13 个 WCS 候选；retry1 的 VLM 返回 `central: disk_like` 与 `central: spiral_arm`，严格 JSON 解析通过；规则层输出 `KEEP_AND_CONTINUE`，`needs_review=true`，不改变当前 Disk＋Bulge 结构。旧方案第一次调用因过期 key 记录在前一目录，更新 key 后的结果位于 `/home/www/2026/GALFITS_examples/jwst0716/170/shadow/20260818_obj170_iter5_component_analysis_v1_retry1`。

## 进行中

- 逐成分 v1 默认判据已裁定并先行实施；阈值（N1/N2/N3 组合、`FWHM_int >= 0.5×FWHM_psf`、SNR 门等）待 dev set 评测后校准优化。
- 第 5 步 shadow mode 的单轮真实验证已完成；当前正在准备更多历史轮次的新旧对照和阈值校准，仍不接管正式 workflow。

## 待办

- 扩大 PSF／VLM shadow 对照到 held-out 历史轮次，检查不同 PSF、mask 和残差质量下的三态稳定性。
- 将可选 catalog 接入候选正向佐证；未匹配仍不得直接否决候选。
- 将单轮 `obj_170` 的旧新对照扩展为批量报告，只记录差异，不改变实际成分。
- 建立 held-out 评测集，校准阈值并进行科学家评审。
- 按 `docs/component-analysis/spec-sync.md` 同步 component specification、workflow、residual analysis prompt 和 `best-round-verifier`。
- 通过回归测试与科学家评审后，让新规则层接管正式动作决策。

## 阻塞

- 暂无；provider 已用 `.env` 中的 OpenAI-compatible 配置完成 `obj_170` 单轮调用，后续批量运行仍依赖该 key 在网关侧持续有效，凭证不写入仓库。
- v1 判据阈值属默认值，待评测校准，不阻塞实施。

## 最近验证

- 2026-08-13：完成现有规范与重构方案的只读对照，并更新重构方案和规范同步方案；现有规范同步与代码实现尚未执行。
- 2026-08-13：`/home/www/ENTER/bin/python -m pytest tests/test_schemas.py` 39 passed（pytest 9.1.1 已经确认后安装到 ENTER 环境），四类 schema 的合法样例、非法样例和层边界禁令均验证通过。
- 2026-08-13：`pytest tests/test_component_analysis.py tests/test_schemas.py` 82 passed（新增 43 个）：数值函数用已知矩／m=1／m=2／FWHM 的合成图像验证，规则层用纯 JSON fixture 覆盖七类成分的 PROPOSE／ACCEPT／REJECT／INCONCLUSIVE 分支（含 Bar 单高质量波段触发、中心源 resolved／unresolved／AGN 分流、VLM 解析失败降级、可选成分 BIC 门）。
- 2026-08-13：Lens 落实后全量回归 `pytest tests/test_component_analysis.py tests/test_schemas.py` 88 passed（新增 6 个 Lens 用例：Bar 参数异常＋延展残差提议、缺延展残差 INCONCLUSIVE、高置信 independent_source 否决、无 Bar 不提议、正常 Bar 参数不触发、lens 低 `BIC_gain` 拒绝）。
- 2026-08-13：policy 层落实后全量回归 `pytest tests/test_component_analysis.py tests/test_schemas.py tests/test_policy.py` 103 passed（新增 15 个 policy 用例：四类试拟合降级（Disk 模糊证据、Edge-on 替换、中心源分辨冲突取 Bulge 路径、Companion 带 target label）、衍射冲突保守兜底、预算耗尽、被否候选不重提、重复 `INCONCLUSIVE` 终结、VLM 断供纯数值降级两路径、`EVALUATE_REFIT` 兜底拒绝与状态记录、非 `INCONCLUSIVE` 直通）。
- 2026-08-17：`/home/www/ENTER/bin/python -m pytest tests/test_vlm.py tests/test_component_analysis.py tests/test_schemas.py tests/test_policy.py` 124 passed（新增 21 个 VLM 用例，覆盖 prompt 边界、合法 JSON、未知标签、缺字段、坐标字段、越权目标、标签冲突、低质量约束、严格 JSON、超时与拒答降级）；存在 1 条既有 pytest 配置警告：当前环境未识别 `asyncio_mode`。
- 2026-08-17：`/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_component_analysis.py tests/test_artifact_adapter.py tests/test_vlm.py tests/test_schemas.py tests/test_policy.py` 129 passed；新增 adapter／shadow runner、PSF 核半高宽及跨波段候选 ID 测试。仍有同一条既有 `asyncio_mode` 配置警告。
- 2026-08-17：只读运行 `/home/www/2026/GALFITS_examples/jwst0716/170/output/20260717_161120_obj_170_iter5`：7 个波段的路径、5-HDU result 布局、shape、WCS、单位和有限值检查全部通过；数值层产出 42 项基础特征，VLM 未调用而记录 `REFUSED`，decision 为带 `needs_review` 的 `KEEP_AND_CONTINUE`。产物位于 `/tmp/obj170-shadow-8xAnFG`，该结论仅验证链路，不作为完整科学判断。
- 2026-08-17：`/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_derived_evidence.py tests/test_artifact_adapter.py tests/test_component_analysis.py tests/test_vlm.py tests/test_schemas.py tests/test_policy.py`：134 passed；存在同一条既有 `asyncio_mode` 配置警告。
- 2026-08-17：对 `obj_170` 最优轮运行完整派生层，产出 77 项特征；34 个波段内局部峰合并为 13 个 WCS 候选。初次运行暴露 Bar 的 `psf_veto=false` 语义错误并与旧报告的衍射芒否决冲突；修正三态门后，用同一真实数值证据重跑规则层得到带 `needs_review` 的 `KEEP_AND_CONTINUE`，trace 为 `FOURIER_M1_CONFOUNDING_V1`。完整原始产物位于 `/tmp/obj170-shadow-derived-zXerZm`，其中 decision 生成于三态修正前，仅保留作问题复现，不代表修正后的最终动作。
- 2026-08-18：`/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_component_analysis.py tests/test_derived_evidence.py tests/test_artifact_adapter.py tests/test_vlm.py tests/test_vlm_provider.py -q`：85 passed；存在同一条既有 `asyncio_mode` 配置警告。
- 2026-08-18：`obj_170` retry1 shadow 运行完成并通过四类 schema 校验；VLM `parse_status=OK`、model `gemini-3.1-pro-preview`、2 条受控观察；Bar trace 为 `BAR_EVIDENCE_V1: NOT_SATISFIED`，决策为 `KEEP_AND_CONTINUE`，`needs_review=true`。旧新手工对照摘要与 artifact 同目录保存。
- 2026-08-18：全量 `pytest -q`：160 passed、1 skipped、15 failed。失败集中在未修改的旧集成边界：缺少可选 `claude-agent-sdk`（4 项）、`create_comparison_png` 当前 tuple 返回值与旧测试字符串断言不一致（10 项）、`run_galfits` 旧日志断言（1 项）；本次成分分析聚焦套件仍为 85 passed。
- 2026-08-18：最终成分分析回归 `/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_component_analysis.py tests/test_derived_evidence.py tests/test_artifact_adapter.py tests/test_vlm.py tests/test_vlm_provider.py tests/test_schemas.py tests/test_policy.py -q`：139 passed；存在同一条既有 `asyncio_mode` 配置警告。
