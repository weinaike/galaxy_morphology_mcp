# 成分分析评测集 S8 全量抽取执行方案

状态：方案已获小鱼儿认可（含 §3 三项裁定），2026-09-17 定稿；执行在新会话进行，以本文件为唯一执行入口。

更新日期：2026-09-17

## 0. 前置状态（已完成，勿重做）

- `adjudication_rule_version` = `component-evalset-v2`（含全部 2026-09-15／16 增补：edge_on_disk 不入核心集、两原子复合模式准入、单波段 bar 识别 n≈0.5＋feedme `# STRUCTURE` 优先、PROMOTE 动作类型与专家标签解耦、Gadotti MType 标签读法）。
- 正式 pilot `artifacts/component-analysis-evalset/evalset-20260916T064856Z`：16 实例、133 候选、validate-pilot `PASS`、泄漏 0 违规。
- 16 条代表性裁决经小鱼儿确认（CORRECT 11／EXPLORATORY 4／INCONCLUSIVE 1；benchmark 3／audit 13）。
- 代码基线：`src/component_analysis/evalset/`（config／inventory／labels／pilot／evidence／fingerprint／leakage／validation），CLI `src/tools/component_evalset.py`，聚焦测试 76+ passed。

## 1. 执行边界

- 两个数据根目录严格只读；不执行 GALFIT／GalfitS；不修改专家标签。
- `full` 分两段：`prepare`（全量候选＋state＋prelabel＋packet）→ `FULL_REVIEW_REQUIRED` 停止；`freeze`（只消费已验证裁决生成三池）→ `FROZEN`。
- prelabel 永远是建议；`CORRECT+high` 与池分配由裁决层决定，validate 的准入门机器兜底。
- 失败按执行方案 §17 停止条件处理；单条候选证据不足只影响该条（INCONCLUSIVE／audit），不终止 run。

## 2. 阶段与验收门

### A0：approval artifact ＋ pilot 状态提升（代码＋一次写入）

实现 `approve` 步骤（CLI 子命令或最小脚本）：

1. 校验当前任务有小鱼儿的显式授权（新会话开场提示词即授权记录）。
2. 写 `evaluation-pilot-approval@v1` artifact：引用 pilot run `evalset-20260916T064856Z`、其 inventory checksum、**当前** design_sha256（注意：设计文档在 pilot run 之后有修订，approval 必须锚定最终 v2 文档的 sha，全量链用新 inventory 匹配）、`component-evalset-v2`、approved_by／approved_at。
3. 将 pilot run 的 run-status 置 `PILOT_APPROVED`。

验收：approval artifact 过 schema；其中 checksum 与新 inventory 一致。

### A1：全量 inventory ＋ selection ＋ prepare（只读）

1. 重跑 `inventory`（携带最终 v2 design sha；约 30～40 分钟，后台）。
2. 全量 selection：**所有** eligible 对象×批次实例（单波段 985＋多波段 196），轮次按时间戳目录分组、每目录取拟合输出（feedme 只作语义源），沿用现有生成器逻辑；selection 状态 `USER_AUTHORIZED_FULL`（schema 需增该枚举）。
3. `full prepare`（新 CLI 模式，复用 `build_pilot` 全链：候选／state manifests／prelabels／packets／fingerprint 别名归并／泄漏检查），输出到新 run 目录，状态 `FULL_REVIEW_REQUIRED`。

规模预估（按 pilot 比例外推，prepare 后出实测数）：约 9,000～10,000 轮 → 约 8,500 转换 ＋ 约 1,100 CONVERGED；别名归并预计压缩 10%～20%；动作分布参照 pilot（结构类约 65%、REFIT 约 33%）。

验收：全量候选 schema 通过；泄漏 0 违规；同配置重放一致；报告实测规模。

### A2：分层裁决（agent 执行，断点续作）

- **L1 确定性事实**：prelabel 已含 chi2／BIC 变化、专家终态方向、复合模式；不签发 verdict。
- **L2 结构转换逐条裁决**（ADD／REMOVE／REPLACE／PROMOTE／COMPOUND／CONVERGED，预估约 5,500 条）：每条基于 packet 证据写 verdict＋confidence＋reason codes；单波段动作按 adjudication-guide 的决策文件流程（`*_component_analysis_*.md`＋下一轮 feedme）抽查核验；歧义条目直接 audit 并标注。按批次写 `evaluation-action-adjudications.jsonl`（增量追加＋幂等），每批 500 条校验一次 schema。
- **L3 REFIT 转换（约 3,000 条）**：v1 不进入 benchmark、全量归 audit（裁决仅批量记录统计事实＋reason code `REFIT_POOL_AUDIT_V1`），抽 5% 逐条复核（§3-1 裁定）。
- 抽样质检：每动作类型随机抽 ≥20 条由小鱼儿复核；不合格率 >10% 时该类型全量返工。

验收：结构转换裁决覆盖率 100%；REFIT 批量记录＋抽检完成；质检通过。

### A3：validate-full ＋ freeze

1. `validate-pilot` 逻辑复用为 `validate-full`：schema／泄漏复检／重放一致／准入门（benchmark 仅 CORRECT+high 且单动作或合格复合模式／CONVERGED 须 match＋终止证据）／按 mode／object／action／verdict 计数。
2. `freeze`：从已验证裁决生成 `evaluation-set-v1.jsonl`（benchmark）、`evaluation-audit-v1.jsonl`、`evaluation-excluded-v1.jsonl`＋`evaluation-set-v1-manifest.json`（含源 inventory checksum、design checksum、rule version、代码 commit、样本数、对象数、分层统计；单／多波段分别建集分别报告）。
3. 状态 `FROZEN`；随后才可做基线评测（不在本方案内）。

验收：manifest 完整；三池计数与裁决一致；冻结后任何修改都需要新版本号＋新 run。

## 2.5 归档策略与 approval 门（2026-09-21 小鱼儿裁定）

小鱼儿的归档策略只保留最终正确 run，历史 run（含正式 pilot run 目录）可在获批后清理。因此 approve 与 full prepare 的 pilot 核验门在 pilot 目录不存在时回退到 approval 链：存在引用同一 `pilot_run_id` 且状态 `PILOT_APPROVED` 的历史 approval artifact 即视为已获批（approval 文件本身保留，构成可审计链条）。

## 3. 已裁定项（2026-09-17 小鱼儿确认，执行时不再询问）

1. **REFIT 转换 v1 不入 benchmark、批量归 audit**：评测主目标是成分结构决策，REFIT 逐条裁决成本高、收益低；全部保留在 audit 池，供 v2 扩展。
2. **抽查比例**：L2 单波段决策文件抽查 20%；L3 REFIT 抽检 5%。
3. **benchmark 不设数量上限**：按准入门（CORRECT+high 且单动作或合格复合模式）自然收敛，预期全量后 benchmark 规模为数百量级。

## 4. 汇报格式

沿用执行计划 §18：每阶段汇报 阶段／状态／文件／验证／数据影响／关键计数／阻塞／下一步；不合并「代码完成」「校验通过」「科学标签正确」三种结论。
