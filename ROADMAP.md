# 项目路线图

## 当前阶段

成分分析重构：schema 已冻结，数值层与纯规则层已实现并通过单元测试（独立 shadow-mode 包，未接管现有 workflow）；下一步为规范同步与 VLM 受控 JSON 改造。

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

## 进行中

- 逐成分 v1 默认判据已裁定并先行实施；阈值（N1/N2/N3 组合、`FWHM_int >= 0.5×FWHM_psf`、SNR 门等）待 dev set 评测后校准优化。

## 待办

- 按 `docs/component-analysis/spec-sync.md` 同步 component specification、workflow、residual analysis prompt 和 `best-round-verifier`。
- 编写 manifest/FITS 适配器，把拟合产物接入 `component_analysis`（shadow mode 前置步骤）。
- 将 VLM 改造成受控 JSON 输出并运行 shadow mode。
- 建立 held-out 评测集，校准阈值并进行科学家评审。

## 阻塞

- 暂无；v1 判据阈值属默认值，待评测校准，不阻塞实施。

## 最近验证

- 2026-08-13：完成现有规范与重构方案的只读对照，并更新重构方案和规范同步方案；现有规范同步与代码实现尚未执行。
- 2026-08-13：`/home/www/ENTER/bin/python -m pytest tests/test_schemas.py` 39 passed（pytest 9.1.1 已经确认后安装到 ENTER 环境），四类 schema 的合法样例、非法样例和层边界禁令均验证通过。
- 2026-08-13：`pytest tests/test_component_analysis.py tests/test_schemas.py` 82 passed（新增 43 个）：数值函数用已知矩／m=1／m=2／FWHM 的合成图像验证，规则层用纯 JSON fixture 覆盖七类成分的 PROPOSE／ACCEPT／REJECT／INCONCLUSIVE 分支（含 Bar 单高质量波段触发、中心源 resolved／unresolved／AGN 分流、VLM 解析失败降级、可选成分 BIC 门）。
- 2026-08-13：Lens 落实后全量回归 `pytest tests/test_component_analysis.py tests/test_schemas.py` 88 passed（新增 6 个 Lens 用例：Bar 参数异常＋延展残差提议、缺延展残差 INCONCLUSIVE、高置信 independent_source 否决、无 Bar 不提议、正常 Bar 参数不触发、lens 低 `BIC_gain` 拒绝）。
- 2026-08-13：policy 层落实后全量回归 `pytest tests/test_component_analysis.py tests/test_schemas.py tests/test_policy.py` 103 passed（新增 15 个 policy 用例：四类试拟合降级（Disk 模糊证据、Edge-on 替换、中心源分辨冲突取 Bulge 路径、Companion 带 target label）、衍射冲突保守兜底、预算耗尽、被否候选不重提、重复 `INCONCLUSIVE` 终结、VLM 断供纯数值降级两路径、`EVALUATE_REFIT` 兜底拒绝与状态记录、非 `INCONCLUSIVE` 直通）。
