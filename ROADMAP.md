# 项目路线图

## 当前阶段

成分分析重构：实施顺序第 5 步进行中。JWST0716 的 116 个历史轮次已完成 numeric-only、完整 VLM shadow 和第一阶段人工复核；候选位置映射已采用数值层 `candidate_N` overlay，中心语义已按方案 B 收敛为数值证据分流。当前只等待科学家裁定 `diffraction_psf` 的定位与证据优先级，再按对象划分 held-out 集进行泛化评测；Edge-on Disk 暂不参与优化。现有 workflow 未接管、未修改。

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
- 2026-08-19：将科学家提供的 JWST0716 多波段最终拟合标签结构化为 `docs/component-analysis/expert-final-labels.json`，作为 shadow run 的终态成分对照基准。评测范围共 32 个已确认对象；`obj_324` 因 F277W mask 覆盖过大、`obj_137` 因缺少专家标签，均移出基准。按专家定义，`nucleus` 规范化为 AGN，`single sersic` 规范化为 Disk，`edge-on disk` 保持独立；`disk(lop)` 规范化记录为 `disk`＋`fourier_m1`。
- 2026-08-20：建立 `docs/component-analysis/shadow-dev-set-jwst0716.json`，固定 20 个 JWST0716 shadow 输入对象及其历史轮次。清单覆盖全部 14 种语义终态成分组合，并为 Edge-on Disk、Disk＋Bulge、Disk＋Fourier m=1＋Bulge、Disk＋Fourier m=1＋Bar、Disk 与 Disk＋Fourier m=1＋AGN 增加稳定性重复样本。全部 20 轮均经 artifact adapter 实测通过；`obj_317` 为 6 波段、`obj_1831` 为 5 波段，其余为 7 波段。所选轮次仅是完整的历史输入，不视为专家终态真值。
- 2026-08-20：将初版 20 对象清单标记为 superseded，新增轮次级清单 `docs/component-analysis/shadow-dev-set-jwst0716-rounds.json`。该清单纳入 116 个完整非 SED 历史轮次：29 个与专家终态成分完全一致、26 个缺少一个终态成分、24 个缺少多个终态成分；另按对象保留 15 个纯额外成分和 22 个混合差异代表轮次。每轮均记录 lyric／gssummary／comparison PNG、语义成分差异、BIC 和 reduced chi-square，并通过 artifact adapter 输入校验。
- 2026-08-20：实现历史 lyric 成分语义规范化：`obj0`／`obj1` 优先按 profile 类型和相邻注释解析，`nucleus`→`agn`、`edgeondisk`→`edge_on_disk`，单一无注释 `obj0` Sérsic 才回退为 `disk`，`sersic_f` 的 `P?21) 1` 记录 `fourier_m1`；无法解析的泛化名称直接失败。批量入口强制校验规范化结果与清单 `source_components` 一致。新增 `tests/test_shadow.py`，与 adapter／规则层回归共 61 项通过。
- 2026-08-20：对轮次级清单完成 116/116 numeric-only shadow，未调用 VLM、未执行 GALFIT、失败 0。规则动作为 `PROPOSE_ADD` 67 轮、`KEEP_AND_CONTINUE` 49 轮；摘要位于 `docs/component-analysis/shadow-dev-results-jwst0716.json`，逐行对照表位于 `docs/component-analysis/shadow-dev-review-table-jwst0716.tsv`，完整临时 artifact 位于 `/tmp/jwst0716-shadow-rounds-20260820-parallel`。拟议成分中 22 轮属于专家终态、45 轮不属于专家终态，后者仅是人工复核优先级，不直接判定规则错误。
- 2026-08-21：完成 JWST0716 完整 VLM shadow 的第一阶段人工复核：逐轮审计 19 个 `PARSE_FAILED` raw response，归因为 7 个中心标签互斥冲突、7 个响应中途截断、4 个 `evidence_regions` 契约不匹配和 1 个低质量标签违规；完成 116 轮动作方向、逐成分覆盖、VLM／numeric-only 差异和对象级重复性统计，并对 16 个代表轮次完成 numeric evidence、VLM evidence、rule trace、decision artifact 与专家终态标签的结构化预审。总结报告位于 `docs/component-analysis/shadow-dev-evaluation-report-jwst0716-vlm.md`，结构化结果位于 `docs/component-analysis/shadow-dev-manual-review-jwst0716-vlm.json`。按用户指示，不再继续这 16 个轮次的 comparison PNG 视觉复核；该项不作为阻塞或待办。
- 2026-08-21：落实人工复核后不涉及科学裁决的首项修复：`docs/component-analysis/redesign.md` 升至 v8，明确 `evidence_regions` 契约和 Edge-on Disk 暂缓范围；VLM prompt 升至 `component-analysis-vlm@v1.1`，明确每个证据区域必须使用 `band:panel:region_id`、提供合法示例并允许无法定位时返回空数组。parser、schema、规则阈值和 workflow 均未改动。候选 ID 图像映射、`central` 多尺度语义和 `diffraction_psf` 的定位／证据优先级已记录为待用户或科学家裁定项。
- 2026-08-24：落实用户裁定的两项 VLM 契约修改。候选位置使用 `src/component_analysis/candidate_overlay.py` 生成 `candidate_overlay.png`，由数值层在各波段 original/residual 面板绘制 `candidate_N`；shadow runner 将 overlay 传给 VLM，原始 comparison PNG 保留不变，坐标不进入 prompt。中心语义采用方案 B：新 prompt 升至 `component-analysis-vlm@v1.2`，不暴露历史 `spheroid_like`；新 parser 拒绝该旧标签，冻结 schema 仍允许历史 JSON 校验，`_disk_rule` 与单 Sérsic 光球分流均不再读取该标签。新增科学家讨论入口 `docs/component-analysis/jwst0716-vlm-scientist-discussion-brief.md`，Edge-on Disk 继续排除在优化范围外。

## 进行中

- 除 Edge-on Disk 外，逐成分 v1 默认判据已裁定并先行实施；阈值（N1/N2/N3 组合、`FWHM_int >= 0.5×FWHM_psf`、SNR 门等）待 dev set 和 held-out 评测后校准优化。Edge-on Disk 保留现有实现，但在科学定义和建模边界统一前不参与优化。
- 第 5 步 shadow mode 的单轮真实 VLM 验证、116 轮 numeric-only 基线、116 轮完整 VLM shadow 及其第一阶段人工复核均已完成；候选映射与中心语义契约已实施，当前仅等待 `diffraction_psf` 科学裁定，并准备按 `object_id` 分组的 held-out 评测，正式 workflow 仍不接管。

## 待办

- 按 `object_id` 划分 held-out 历史轮次，保证同一对象不跨校准集与测试集；按对象或完整拟合序列汇总泛化结果，并检查不同 PSF、mask 和残差质量下的三态稳定性。
- 待科学家裁定 `diffraction_psf` 的定位和证据优先级：建议只有同波段、同区域或同尺度匹配的 VLM 证据可以否决局部数值证据。
- Edge-on Disk 的规则、prompt 和阈值优化暂缓；待科学定义和建模边界统一后单独建立评测。
- 将可选 catalog 接入候选正向佐证；未匹配仍不得直接否决候选。
- 科学家讨论前先阅读 `docs/component-analysis/jwst0716-vlm-scientist-discussion-brief.md`；需要逐轮细节时再查阅报告、review JSON 和 `/tmp/jwst0716-shadow-rounds-20260820-vlm` 三个 diffraction 案例目录。
- 建立 held-out 评测集，校准阈值并进行科学家评审。
- 按 `docs/component-analysis/spec-sync.md` 同步 component specification、workflow、residual analysis prompt 和 `best-round-verifier`。
- 通过回归测试与科学家评审后，让新规则层接管正式动作决策。

## 阻塞

- `diffraction_psf` 的定位和证据优先级尚待科学家裁定，阻塞该局部冲突规则的最终收敛，但不阻塞历史结果审计及其他成分的对象级 held-out 设计。
- Edge-on Disk 科学定义和建模边界尚未统一；该成分已移出当前优化范围，不阻塞其他成分评测。
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
- 2026-08-20：`/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_shadow.py tests/test_artifact_adapter.py tests/test_component_analysis.py tests/test_derived_evidence.py tests/test_schemas.py -q`：104 passed；存在同一条既有 `asyncio_mode` 配置警告。116 轮 numeric-only shadow 全部完成，FITS WCS 的 `FITSFixedWarning` 为 astropy 对历史时间／观测站字段的自动修正提示。
- 2026-08-20：单轮真实 VLM smoke 使用 gemini-3.1-pro-preview 返回 parse_status=OK，并由 independent_source 观察参与规则决策；随后 116 轮完整 VLM shadow 全部完成，97 轮 parse_status=OK、19 轮 PARSE_FAILED、runner failures=0。最终动作 48 轮 PROPOSE_ADD、68 轮 KEEP_AND_CONTINUE；四类 artifact 全量通过 schema。结果位于 docs/component-analysis/shadow-dev-results-jwst0716-vlm.json，与 numeric-only 基线相比 21 轮动作发生变化。
- 2026-08-21：人工复核 JSON 通过标准库解析；19 个失败条目、16 个代表轮条目及唯一 ID 数量断言通过；失败轮次 ID 与 116 轮源汇总逐项一致，动作计数和 proposal 方向计数重新计算一致。总结 Markdown、结构化 JSON 和 `ROADMAP.md` 通过局部空白与差异检查；本阶段按用户指示不包含 comparison PNG 视觉验证。
- 2026-08-21：VLM prompt v1.1 修复后运行 `/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_component_analysis.py tests/test_derived_evidence.py tests/test_artifact_adapter.py tests/test_vlm.py tests/test_vlm_provider.py tests/test_schemas.py tests/test_policy.py tests/test_shadow.py -q`：143 passed；存在同一条既有 `asyncio_mode` 配置警告。结构化 review JSON 已通过标准库解析，并确认包含 3 项待裁决记录和 16 个代表轮条目。
- 2026-08-24：运行 `/home/www/ENTER/envs/galfit/bin/python -m pytest tests/test_vlm.py tests/test_component_analysis.py tests/test_artifact_adapter.py tests/test_schemas.py tests/test_shadow.py -q`：122 passed，存在同一条既有 `asyncio_mode` 配置警告。新增测试覆盖 candidate overlay artifact、VLM 输入路径、方案 B 数值-only 中心分流和旧 `spheroid_like` 新解析拒绝；JSON 文件通过标准库解析，`git diff --check` 通过。

- 2026-08-24：修正 `docs/component-analysis/shadow-dev-action-direction-comparison-jwst0716.md` 的旧方案口径：旧方案改为 116 个历史轮次中 residual_analysis.py 原始流程写入的 `all_bands_comparison_component_analysis_*.md` 最终调整决策，而不是 numeric-only shadow baseline。报告逐轮并列旧决策原文摘要、辅助动作标签、新 VLM shadow 最终动作、当前成分和专家终态；原始来源覆盖为 108 轮可解析、6 轮缺失、2 轮多文件冲突、0 轮决策段无法解析，均明确标注而不以其他 artifact 替代。
- 2026-08-25：扩充 `docs/component-analysis/jwst0716-vlm-scientist-discussion-brief.md`，将成分分析从开始改造到 JWST0716 116 轮 shadow 的进展、当前可确认与不可确认的结论、旧新方案比较边界、盲法动作审查和 A/B refit 评价框架统一整理到科学家讨论入口；保留已裁定的中心语义方案 B、candidate overlay、diffraction_psf 三个案例和 Edge-on Disk 暂缓范围。未修改正式 workflow、规则阈值或科学裁决。
- 2026-08-25：按讨论入口精简 `jwst0716-vlm-scientist-discussion-brief.md`，删除独立的“已裁定并已实现”章节，将 `diffraction_psf` 的定义、Bar 衍射冲突以及 band、panel、region 的含义并入科学家裁定问题；后续章节编号顺延。未修改代码、正式 workflow、规则阈值或科学裁决。
