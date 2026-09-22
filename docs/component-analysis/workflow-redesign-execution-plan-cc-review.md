# workflow-redesign-execution-plan 只读架构与代码 Review

日期：2026-09-14
review 对象：`docs/component-analysis/workflow-redesign-execution-plan.md` 第 2、3、4、5、9、10、11 节，尤其是 N23～N27
review 范围：`src/component_analysis/`（rules、policy、decision_service、refit_comparator、workflow_bridge、workflow_summary_renderer）、`src/tools/workflow_batch_runner.py`、`src/tools/workflow_lifecycle.py`、`src/schemas/`、五份测试文件，以及最新 104 canary 真实 artifact（`jwst0831-20260910T095811Z`）。
性质：静态只读 review。本轮未修改任何代码，未运行测试，未执行任何 GALFIT／GalfitS 拟合。以下所有 file:line 均经实际读码或读取 artifact 核实。

## 0. 总结论

**方案不能原样进入 N23 实施。** N23～N27 的方向正确：静态核对确认方案第 2 节列出的每一条缺口都真实存在。但方案里有要求在当前 MCP artifact 下无法提供证据（`optimizer_converged` 无数据源），且该要求与终止门语义相互作用会导致新 workflow 死锁（任何候选不可接受、`CONVERGED` 不可达）。必须先修方案，再动代码。

---

## 1. Findings

### Critical

**C1. 双重仲裁真实存在，且两次都写状态**
- 位置：`src/tools/workflow_batch_runner.py:2095`（调 `workflow_complete_candidate`）、`src/tools/workflow_batch_runner.py:2117`（调 `workflow_evaluate_refit`），两者最终都进入 `evaluate_refit_with_policy`（`src/component_analysis/workflow_bridge.py:2113` 与 `:2045`）。
- 现象：同一候选串行执行两次完整仲裁（无 if/else 分支）。104 canary 实测：`artifacts/round_001/candidate_completion.json` 与 `artifacts/round_001/refit_decision.json` 各自持有一份完全相同的 `decision.action = {"action_type":"REJECT_REFIT","component":"companion"}` 和 `action_summary.refit_verdict = "REJECTED"`。
- 影响：一次候选产生两次 Policy transition、两次 PolicyState 落盘、两条 lifecycle 记录；`policy.py:478` 的 `trials_used` 在 trial-fit 路径下被重复消耗，试探预算实际是标称值的一半。runner 在 `workflow_batch_runner.py:2149` 用 `evaluated ... or complete ...` 兜底取 verdict，承认两个来源都可能是权威。
- 建议：按 N26 把 `complete_workflow_candidate` 内的 `evaluate_refit_with_policy`、`record_fit_lifecycle`、state 保存和 verdict 派生全部删除，只留 comparator evidence 返回。

**C2. `candidate_ref` 完全不存在，且身份参数存在真实的层级错配**
- 位置：全仓 `grep candidate_ref` 命中 0（src 与 tests 均无）。wrapper 签名 `src/tools/workflow_lifecycle.py:352-364` 把身份摊成平铺参数 `evidence_fingerprint=""`、`baseline_config_checksum=None`，函数体不从 `evidence_refs` 取值；runner（`workflow_batch_runner.py:2126-2130`）把这两个值嵌在 `evidence_refs` 里传。
- 现象：runner 计算的 fingerprint 从未到达 `_candidate_key`（`policy.py:285-301`）。canary 落盘证据：`round_001` PolicyState 的 `rejected_candidate_keys` 实际内容为 `{"action_type":"REJECT_REFIT",...,"baseline_config_checksum":null,"evidence_fingerprint":""}` —— 两个身份字段都是空值。
- 影响：Policy 的拒绝记忆退化为「动作类型＋成分名」，跨 baseline、跨证据全部混同。不是冗余，是数据丢失。
- 建议：N23 定义 schema 校验的 `candidate_ref`，服务端计算 key，删除 wrapper 平铺参数，避免两个入口并存。

**C3. 拒绝键用结果动作构造，原始 target 丢失**
- 位置：`evaluate_refit` 返回的 action 恒为 `{"action_type": "REJECT_REFIT"|"ACCEPT_REFIT", "component": ...}`（`rules.py:1152、1166、1179、1193、1205、1222、1244、1247、1255`），不含 `target_model_label` 与 `parameter_changes`；`policy.py:374-386` 用该 action 生成拒绝键。
- 现象：canary 里被拒动作实为 `PROPOSE_ADD companion target=candidate_12`，落盘键的 `target_model_label` 为 `null`。
- 影响：同一成分在不同 target 上的候选共享一个拒绝键；在 `candidate_6` 上试 companion 会被 `candidate_12` 的历史拒绝错误封禁。
- 建议：canonical action 必须来自原始候选，而非 refit 结果。

**C4. 根本没有候选队列；`REJECT_REFIT` 必然死路**
- 位置：全仓无 `pending_candidate_queue`，无 `PENDING/ACCEPTED/REJECTED/INVALIDATED` 字面量。`policy.py:374-386` 的 REJECT_REFIT 分支只记录拒绝即 `return decision`；runner 在 `workflow_batch_runner.py:2158-2168` 记 fingerprint、`round_index + 1`、`continue`，下一轮对同一 baseline 重跑 proposal，然后被 `:1962-1985` 的重复检测拦下直接 `_finish_with_review`。
- 现象：canary 完全复现——`run_index.json` 中 `rounds[1]` 与 `rounds[2]` 的 `baseline.config_file` 同为 `obj_104_baseline_iter1.lyric`，`rounds[2].action = None`，终态 `STOPPED_NEEDS_REVIEW`。
- 影响：当前闭环最硬的阻塞。第一次拒绝即等于整个对象迭代终止。N25 定位准确，但工作量被低估：Rules 侧 `_select_candidates`（`rules.py:910-933`）目前只返回一个 selected，队列需要 Rules 与 Policy 同时改。

### High

**H1. `optimizer_converged` 在当前产物中不可提取——方案要求无法满足，且会造成收敛死锁**
- 位置：真实 gssummary（`configs/output/20260910_180526_obj_104_baseline_iter2/obj104.gssummary`）全部非参数行只有 `chisq`、`reduced chisq`、`BIC`、`fitting time`、`num_generations: 5000  popsize: 10`，没有任何优化器收敛/退出状态字段。ES 是固定代数进化策略，不产生收敛标志。
- 现状实现确实是拿完整性冒充收敛：`refit_comparator.py:274-280` 的 `fit_converged` 直接由 `candidate_artifact["valid"]` 决定，而 `valid` 只是 status 成功＋文件齐全（`:150、:128-145`）；`artifact_adapter.py:913-924` 的多波段 `fit_succeeded` 只查 HDU 布局、shape 一致和 summary 文件存在（单波段路径 `:961-971` 反而查了 chisq 有限性）。
- 死锁机制：方案 N24「无法提取时写 `UNAVAILABLE`」＋ 5.6 节「`optimizer_converged` 未采集时为 `INCONCLUSIVE`」＋ 终止门「`UNAVAILABLE` 阻止收敛」三者叠加 ⇒ 每个候选恒为 `INCONCLUSIVE`、`CONVERGED` 永不可达。
- 建议：定义明确的 ES 收敛代理量并命名为它本来的样子（如 `fit_quality_ok`），或把 `optimizer_converged` 对本 GalfitS 版本标为 `NOT_APPLICABLE`，或要求 GalfitS 侧先补输出收敛信息。需小鱼儿裁定（Q1）。

**H2. comparator 的 band 顺序回退仍在，失败方向 fail-open**
- 位置：`refit_comparator.py:39-61`。先用正则 `f\d{3,4}[a-z]` 从文件名取 band token 匹配，失败才比 manifest `result_fits` 路径，两者都失败时 `if match_index is None and unused: match_index = 0`，取第一个未使用 result，不 raise、不标 unavailable。
- 影响：不是理论风险，是代码写明的默认行为。104 七波段文件名规范所以侥幸正确；任何重命名或 token 冲突会导致跨波段比较残差。方案要求删除回退正确，且应额外要求匹配失败必须抛错。

**H3. reduced chi-square 计算了但完全不参与判定**
- 位置：`refit_comparator.py:296-304` 完整输出 `baseline/candidate/delta/comparable`；`rules.py` 与 `policy.py` 中 `grep reduced_chisq` 命中 0，`rules.py:1174-1175` 只有一句「仅留作审计」的注释。
- canary 数值：baseline `0.5613413453`、candidate `0.5614057779`、delta `+6.44e-05`、`comparable: true`——变差了，但对 verdict 无任何作用。`artifact_valid` 同样全仓不存在。

**H4. 残差只有一个聚合标量，没有 1D／2D 分离**
- 位置：`refit_comparator.py:185-206` 把每波段 2D 残差压成 `mean(|res|/sigma)` 再跨波段平均；`:264-273` 产出单一 `residual_outcome`；`rules.py:1097-1104` 归一化后所有门（`:1122、1180、1183、1207-1213`）只读这一个字符串。终止检查里的 `one_d_clean`（`rules.py:820`）是另一条独立路径，与 refit 判定无关且无 2D 对应项。
- 影响：方案 5.6 的 `one_d_residual_outcome`／`2d_residual_outcome` 无任何数据源，N24 需要新增 1D profile 残差测量实现，不只是改判定逻辑。

**H5. `boundary_hits` 只测候选侧，可能造成永久性拒绝**
- 位置：canary `refit_evidence.json` 的 `parameter_health` 实际只有 `{"candidate": "yes", "boundary_hits": ["obj0.re"], "degeneracy_warnings": []}`，没有 baseline 侧边界列表；`rules.py:1153、1194` 只要 `boundary_hits` 非空即 `REJECT_REFIT`。
- 影响：若某参数在 baseline 已命中边界，此后每个候选都会因这个与候选无关的既有边界被拒。建议 comparator 同时输出 baseline 边界集合，判定只对「候选新引入或未解决」的边界生效。

**H6. VLM 依赖候选越权执行，canary 有实证**
- 位置：`policy.py:32-45` 的 `_TRIAL_FIT_ACTIONS` 仍含 `COMPANION_NUMERIC_VLM_V1`；`:464-465` 证据采集分支被 `template is None` 挡住，rule ID 在表内即走 trial fit，唯一约束是预算和拒绝键。
- canary 实证：`round_001` decision 的 `raw_decision.action = INCONCLUSIVE`、`automation = {"resolution":"trial_fit","resolved_rule_id":"COMPANION_NUMERIC_VLM_V1"}`、`decision.action` 变成 `PROPOSE_ADD companion candidate_12`，而 provider `status: REFUSED`、23 次 attempt 全部 `REFUSED`、target 覆盖 0——在完全没有 VLM 确认的情况下真实生成了 lyric 并跑了一次多波段拟合。

**H7. Rules 不区分缺失证据与真实负证据**
- 位置：`rules.py:41-46` 的 `_features` 在取值前就用 `status == "AVAILABLE"` 过滤，`:49-51` 的 `_first_value` 无匹配返回 `None`；「字段缺失」「解析失败」「观测明确否定」三者在下游完全同形。`NOT_APPLICABLE` 仅 2 处（`rules.py:758、1066`）且不进终止检查；`non_blocking` 全仓不存在，只有默认 `blocking = (outcome == "INCONCLUSIVE")`（`rules.py:99`）。

**H8. 高优先级 blocking 候选没有屏障**
- 位置：`rules.py:915-921` 排序键为 `(priority, is_inconclusive, 原始下标)` 三元组，不含可执行性、与当前模型相关性、证据完整度、numeric 支持强度，也不查其它候选的 `blocking` 标志。`rules.py:1029-1033` 的 `inconclusive` 只在无任何可选结构候选时才起作用（`:1035`）。

**H9. `inconclusive_seen` 只按 rule ID 隔离**
- 位置：`policy.py:56` 类型 `dict[str, str]`（`rule_id -> evidence_fingerprint`），判定 `:448-450`，`terminated_rules` 为 `set[str]`（`:451-459`）。两个不同 companion target 共用一个终止槽，一个 target 终止连带终止同规则其它 target。

**H10. 多 target 规则首个 target 即返回**
- 位置：`rules.py:501-532` companion 规则双层循环内命中即 `return`（`:516` 或 `:526`），后续 region 与 feature 永不检查。canary numeric 层有 17 个 candidate region，实际只可能产出一个 companion 候选。

### Medium

**M1. `next_decision`／`ANALYZE_NEW_BASELINE` 全仓不存在**（grep 0）。当前 `refit_decision.json` 只是 runner 给返回值起的文件名，不是契约字段；返回结构为 `{decision, policy_state, action_summary}`，verdict 与下一步动作混在同一 `decision.action` 里。runner 在 `workflow_batch_runner.py:2149-2169` 直接读 `action_summary.refit_verdict` 字符串决定 baseline 切换与循环走向——这是绕过 decision artifact 的控制通道。机器动作目前**无法**保证只来自 `resolved_decision`。

**M2. 无幂等保护**。`workflow_bridge.py` 内 `grep event_id` 命中 0；幂等只在 `decision_service.py:599-605` proposal 侧有。重复提交同一候选会重复消耗预算、重复追加 lifecycle。

**M3. capability 契约滞后**。`src/tools/workflow_lifecycle.py:37-45` 的 `WORKFLOW_ACTIONS` 仍列 `KEEP_AND_CONTINUE` 且不含 `COLLECT_EVIDENCE`（后者只在 `:29` 的 `WORKFLOW_TRANSITIONS` 里）；`workflow_bridge.py:35-44` 的 `_ACTION_TYPES` 两者都有。对外宣告与新状态机不一致。

**M4. evidence view 命名与污染**。`numeric_plus_vlm` 全仓不存在，实际写键为 `vlm` 和 `numeric_only`（`policy.py:572、579、588、595`）。VLM 失败分支 `policy.py:559-577`：`decision` 在 `:560` 被重指向 retry 对象后，`:575-577` 构造 `evidence_views["vlm"]` 时 `action`／`rule_trace` 取自被换好的 `raw_decision`（正确），但 `candidate_actions` 取的是 retry 的顶层值（numeric-only 候选）。同一对象内字段来源不一致。

**M5. `run_index.json` 的 `round_dir` 指向 `/tmp/jwst0831-n17-canary-104-fixed-PDMqyY/...`**。持久化 run 的审计索引指向临时目录，`/tmp` 清理后 round 级审计链断裂。持久 `artifacts/round_00N/` 已存在，索引应指向它。

**M6. 报告把 refit 与下一步动作混为一谈**。`summaries/round_104_workflow_round1_component_analysis.md:160` 写 `next_transition：evaluate_refit_or_analyze_next_round`，`analysis_report_obj104.md:24-28` 每轮同一字符串。方案第 7 节要求「本轮执行的动作／refit 结果／队列中的下一动作」三者分离，第三项目前无内容可渲染（因为没有队列）。正面事实：`SED：NOT_RUN`、`Image-SED：NOT_RUN`、分级验收（`Image 迭代执行链：PASSED`、`安全停止门：PASSED`、`完整 workflow 闭环：NOT_PASSED`）都如实写了。

### Low

**L1. `rejected_components` 是只写不读的死状态**（写在 `policy.py:378、396`，无任何读取处）。删除风险低。

**L2. `vlm_evidence.schema.json` 无 target coverage 字段**（`grep required_targets|covered|missing_targets|attempts` 命中 0）。canary 的 `vlm_evidence.json` 只有 6 个键，attempt metadata 实际落在 proposal 的 `provider` 块。

**L3. 工作区有未清理的 `.orig`／`.rej` 残留**（8 个 `.orig`＋1 个 `.rej`）。`.rej` 是一次未应用成功的补丁；`tests/test_workflow_batch_runner.py.orig` 会被 grep 命中，容易在后续审计造成误读。属删除操作，需小鱼儿确认后再清理。

---

## 2. 十一项重点问题逐项回答

1. **`workflow_complete_candidate` 是否已严格限定为 comparator adapter、每个候选只一次 refit 评价？** 否。它仍调用 `evaluate_refit_with_policy`、写 lifecycle、存 state、出 verdict（C1）。canary 同一候选有两份完整仲裁产物。
2. **`candidate_ref` 是否足以唯一标识？** 该字段根本不存在（C2）。现有身份传递同时存在层级错配（runner 嵌套／wrapper 平铺）和语义命名错误（`baseline_config_checksum` 实际是 action＋baseline 路径的 sha256，见 `workflow_batch_runner.py:615-634`，既非 config 内容 checksum，也不只含 baseline）。canary 落盘键里两个身份值都是空。
3. **`REJECT_REFIT` 后能否进入下一安全候选？** 不能，不是被误阻断，而是根本没有队列可进（C4）。真实路径是「拒绝 → 重分析同 baseline → 重复检测 → STOPPED_NEEDS_REVIEW」。拒绝键丢 target（C3）会错误封禁不同 target 的同类候选；无 blocker 屏障（H8）是独立缺陷。
4. **`refit_decision` 与 `next_decision` 分离能否保证动作只来自 `resolved_decision`？** 分离尚未实现（M1）。runner 用 `action_summary.refit_verdict` 字符串驱动控制流，是绕过 decision artifact 的通道。方案设计能解决，但必须同时禁止 runner 读 `action_summary` 驱动控制流。
5. **Rules 是否区分四态（缺失／真实负／NOT_APPLICABLE／blocking 与 non-blocking INCONCLUSIVE）？** 不区分（H7）。缺失与真实负证据同形；`NOT_APPLICABLE` 仅 2 处且不进终止检查；`non_blocking` 不存在。
6. **收敛是否来自真实 optimizer 状态？** 不是，来自 artifact 完整性（H1）。更关键：真实 gssummary 无任何收敛状态字段可提取，方案口径本身不可实现，且会死锁。
7. **comparator 是否严格按 manifest 显式 band 映射？** 否。先按文件名 token，manifest 只是第二顺位，最后还有取 index 0 的 fail-open 回退（H2）。
8. **八项指标是否都进入 refit 判定？** 否。实际参与判定的只有 `fit_converged`、`residual_outcome`（单一聚合）、`parameters_physical`、`boundary_hits`、`degeneracy_warnings`，以及仅对可选成分（agn／compact_central_source_candidate／companion／lens 的 `PROPOSE_ADD`）生效的 BIC（`rules.py:1229-1247`）。`reduced_chisq` 与 `artifact_valid` 完全不参与（H3）；1D／2D 分离无数据源（H4）。
9. **generic single Sersic 能否作为合法 profile 与终态？** 作为 profile 可以且不会被误改写成 Disk：`rules.py:295-301` 的 `SPHEROID_SINGLE_SERSIC_V1` 返回 `action=None` 且 `SATISFIED`；promotion 需 `promotion_target` 与 Disk 证据组合同时成立（`rules.py:266-282、:963-967`）；`REQUIRED_FIXED_PARAMETER_V1`（`:608`）只对已确认角色触发。**但它无法成为终态**：`rules.py:878-886` 在 `required_items` 为空时给 `REQUIRED_FIXED_PARAMETERS` 写 `UNAVAILABLE`，而 `:1042` 要求全门 `PASS`，纯单 Sersic 永远拿不到 `CONVERGED`。「不为过门改标 Disk」已实现，「有效单 Sersic 可以是终态」未实现——N24 需明确改成 `NOT_APPLICABLE`。
10. **N23～N27 的测试／replay／smoke／canary 门是否可执行、是否漏关键 contract test？** 门本身可执行，但测试缺口大。全 tests 目录 grep：`candidate_ref` 0、`next_decision` 0、`queue` 0、`optimizer_converged` 0、`NOT_APPLICABLE` 0、`blocking` 0、`positional` 0。方案第 9 节矩阵中 (a) 单次仲裁、(b) 幂等、(c) 双 decision 分离、(d) 拒绝后取下一候选、(e) 接受后队列失效、(f) blocker 不被绕过、(g) VLM 依赖不转 trial、(h) band fail-closed、(i) 真实收敛与完整性分离、(j) reduced chi-square 入判、(k) 1D／2D 分离、(l) 三态终止语义、(m) 单 Sersic 终态、(n) 同证据异 baseline 不判重——全部 MISSING。现存最接近的 `test_rejected_component_not_retried`（`tests/test_policy.py:194`）测的是只写不读的 `rejected_components`；`test_rejected_action_is_not_repeated_on_unchanged_baseline`（`tests/test_workflow_batch_runner.py:521`）恰恰把「拒绝后停止」固化为期望行为，实施 N25 时必须改写。
11. **方案是否存在过度严格、矛盾或无法取证的要求？** 有三处：`optimizer_converged` 无数据源且死锁（Q1）、1D／2D 分离无实现（Q2）、`boundary_hits` 缺 baseline 对比语义（Q3）。

---

## 3. Open Questions（需小鱼儿裁定的科学口径）

**Q1（阻塞 N24，必须先定）** GalfitS ES 不输出收敛状态，gssummary 只有 chisq／reduced chisq／BIC／generations／popsize。方案要求的 `optimizer_converged` 无数据源，「未采集即 `INCONCLUSIVE`／`UNAVAILABLE` 阻止收敛」会使任何候选不可接受、`CONVERGED` 不可达。可选：
- (a) 定义并命名 ES 收敛代理量（全波段 chisq/dof 有限、迭代跑满、best_value 不贴采样边界），方案改用该名称，`optimizer_converged` 对本版本永久标 `NOT_APPLICABLE`（reviewer 推荐）；
- (b) 要求 GalfitS 侧先补输出收敛信息，本阶段 refit 判定不引入该门；
- (c) 其它口径。

**Q2** 方案 5.6 要求 `one_d_residual_outcome` 与 `2d_residual_outcome` 分离，但当前 comparator 只有 2D 像素残差，没有 1D profile 提取实现。是把 1D profile 测量纳入 N24 范围（工作量明显增加），还是本阶段先只做 2D＋reduced chi-square，1D 留作后续？

**Q3** `boundary_hits` 目前只有候选侧。判定口径应确认为「只对候选新引入或未解决的边界拒绝」，还是保留「任何边界即拒绝」？后者在 baseline 已有边界命中时会让对象永久无法推进（H5）。

**Q4** 单 Sersic 终态：确认 `REQUIRED_FIXED_PARAMETERS` 在无已确认成分时应为 `NOT_APPLICABLE`（可满足终止），而非现在的 `UNAVAILABLE`（阻止收敛）？

**Q5** 工作区 8 个 `.orig` 与 1 个 `.rej` 残留是否可清理？属删除操作，确认后再做。

---

## 4. 验收结论

**方案是否可进入 N23 实施：不可以原样进入。** 先修方案三项（Q1 硬阻塞；Q2、Q3 影响 N24 范围与判定正确性），修完即可开工。N23 本身（schema／capability／接口冻结）不依赖这三项，可并行起草，但 N24 必须等 Q1 定案。

### 需要先修改方案的条目

1. 第 5.6 节与 N24 的 `optimizer_converged` 要求——按 Q1 改为可取证的口径，否则实施后 workflow 死锁（H1）。
2. 第 5.6 节的 1D／2D 残差分离——补充「需新增 1D profile 测量实现」或缩小本阶段范围（H4、Q2）。
3. 第 5.6 节 `boundary_hits` 判定——明确 baseline／candidate 对比语义（H5、Q3）。
4. 第 5.3 节终止门——明确无已确认成分时 `REQUIRED_FIXED_PARAMETERS` 为 `NOT_APPLICABLE`，使有效单 Sersic 真正可终态（Q4）。
5. 第 9 节测试矩阵——补一条：实施 N25 时必须改写 `tests/test_workflow_batch_runner.py:521` 与 `tests/test_policy.py:194`，它们把当前缺陷固化为期望行为。
6. 第 10 节 N26——补一条：禁止 runner 从 `action_summary.refit_verdict` 驱动控制流（M1），否则 `next_decision` 分离形同虚设。
7. 第 8 节——补一条：`run_index.json` 的 `round_dir` 必须指向持久 `artifacts/`，不得指向 `/tmp`（M5）。

### 不需要修改、可直接按现方案实施的条目

- N23 的 `candidate_ref` 契约、服务端计算 candidate key、删除重复输入位置（C2、C3 已核实为真）。
- N23 的 `workflow_complete_candidate` 降级为 comparator adapter、`workflow_evaluate_refit` 单一仲裁入口（C1 已核实）。
- N23 的 capability 修正（M3 已核实：仍列 `KEEP_AND_CONTINUE`、动作集缺 `COLLECT_EVIDENCE`）。
- N24 的多 target 遍历（H10）、复合排序键（H8）、删除 VLM 依赖 trial fit 路径（H6，canary 有实证）、comparator 删除 band 顺序回退（H2）、reduced chi-square 入判（H3）。
- N25 的候选队列、拒绝后取下一候选、接受后队列失效、target／baseline 维度隔离（C4、H9）、停止写 `rejected_components`（L1）。
- N26 的双评价消除、幂等（M2）、renderer 三段分离（M6）。
- N27 的顺序（focused → replay → 全量 → 无拟合 smoke → 仅 104 canary）与第 11 节分级验收门。第 11 节的分级设计是这份方案里最稳的部分：104 现有报告确实如实标了 `完整 workflow 闭环：NOT_PASSED`，没有把安全停止冒充闭环。

---

## 5. 状态声明

以上全部为静态 review 结论。代码尚未修改；104 的 `STOPPED_NEEDS_REVIEW` 仍是当前真实状态；本 review 不代表任何缺口已修复。
