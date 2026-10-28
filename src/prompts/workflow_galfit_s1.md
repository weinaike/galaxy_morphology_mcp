# workflow_galfit_s1 — 专家指导的单波段星系 Beam Search 拟合工作流

要求严格遵循工作流开展星系拟合分析工作。
本 workflow 在 **beam search 引擎**上执行（束宽 W=5、预算 N_max=7+1 次保留 F1 仲裁、格子去重、class 配额——引擎机制由代码执行，agent 不得手工模拟）；专家提供的成分组合 T 作为**搜索空间约束**注入引擎，替代旧版"每轮贪心 +1 成分"的推进方式。
图像分析与拟合执行只能使用 galmcp 中的工具。严禁使用 4_5v_mcp 的相关工具。
系统规定的规范（CLAUDE.md 的解空间定义、`.cons` 规范、锁定准则）是你需要遵循的，严禁私自更改。

## 专家指定的成分组合（expert_components）

本 workflow 在调用时除传入 feedme 文件路径外，还会接收人类专家提供的目标星系形态结构组成（expert_components）。
专家成分组合的格式为：**`成分(候选模型,候选模型,...)`**，多个成分之间用英文逗号分隔。

**示例：** `bulge(sersic,psf), disk(sersic), bar(sersic)` 含义为：
- bulge 成分：可使用 sersic 或 psf 模型拟合，在候选模型之间择优选用
- disk 成分：使用 sersic 模型拟合
- bar 成分：使用 sersic 模型拟合

**专家提供的成分组合：**
{expert_components}

### T 到引擎槽位的翻译规则（本分支解空间：Disk(expdisk) / Bulge / Bar / AGN(psf) / F1 / SingleSersic）

- `bulge(sersic,psf)` → bulge 槽位；候选模型中的 **psf 即塌缩替换通道**（Bulge Re 塌缩 <0.5 px 时，`remove(bulge)+add(agn,psf)` 是合法的候选模型切换）
- `disk(...)` → disk 槽位（expdisk；本分支多成分模型的盘一律 expdisk）；若 T 的**唯一**成分是盘且专家候选模型写 sersic → singlesersic 槽位（单成分 regime，n 自由）
- `bar(sersic)` → bar 槽位（n=0.5 固定，q 硬上限 0.8）
- `agn(psf)` / `nucleus` / `Nucleus` → AGN 点核（psf）
- 偏心 / `Fourier` / `F1` → 阶段三的保留 F1 仲裁（只作用于 Disk / SingleSersic，amplitude>0.02 且 BIC_eff 不恶化才保留）
- T 中的 `lens` / `edgedisk` / `companion` / `outerdisk` **不在本分支解空间**：在报告中明确记录该冲突，按 T 的合法子集继续（`check_feedme_file` 会直接拒绝这些成分，强行添加只会浪费预算）

**约束语义（硬）：** 最终模型必须落在 T（或其塌缩/单成分等价形态）内；不允许添加 T 之外的成分；当图中出现 combo==T 的轮次后，不再增删成分、只调参数与约束。若拟合证据与 T 强烈矛盾（例如 T 无 bar 但棒特征压倒性），**不得擅自改 T** —— 照常按 T 完成拟合，并在最终报告中完整记录矛盾证据（专家保留裁决权）。

## workflow

### 阶段一. 初始化与专家约束注入

* **查看星系目录：** 确认所需的文件是存在的，包括 FITS 图像、掩膜文件、背景估计文件、feedme 文件等。
* **解析 T：** 按上面的翻译规则解析 `{expert_components}`，得到目标槽位集合 T 与候选模型映射；同时确定**禁用清单 F = 解空间槽位 − T**（例：T 不含 bar 且不含 psf 候选 → F 包含 "bar" 与 "agn"；T 的 bulge 带 psf 候选 → "agn" 不禁，塌缩替换通道保留）。
* **写 root.feedme：** 输入 feedme 的机械拷贝（含 `# STRUCTURE:` 语义命名，单成分输入命名为 singlesersic，sky 块逐字保留）——只作图锚点与转录模板，**绝不拟合它**。
* **初始化图：** `beam_init(galaxy_dir, root_feedme=root.feedme)`。
* **注册专家约束（关键一步）：** `beam_set_constraint(galaxy_dir, action="add", text="专家目标成分组合 T={...}，禁止 T 之外的结构", forbid_structures_json=F)` —— F 中结构的 add/tune 候选**和开局骨架**都会被代码在入队时直接丢弃（E_TEMP_CONSTRAINT），这是 T 成为硬约束的机制保证。

### 阶段二. Beam Search 主循环（T 注入每轮侦察）

1. **开局：** `render_original(galaxy_dir)` 生成原图 → `survey_round(galaxy_dir, state_label="A.0", opening_image=<原图路径>, directives="专家指定成分组合 T={<T 内容>}；开局骨架只能由 T 的子集构成，首选直接给出完整 T 的骨架（disk→bulge→bar 顺序），非 T 成分一律不出现")` → 对返回的 `next_candidate` 执行 `apply_candidate` → `run_galfit`。开局侦察同时完成初始形态判断（替代旧版的 bar_recognition / detect_bar_lopsidedness 单独调用——这两个调用在本流程中已退役）。
2. **逐轮循环（每轮机械执行，不得跳步、不得手工改写候选）：**
   - `apply_candidate(galaxy_dir, <next_candidate>)`
   - `run_galfit(config_file=<apply 返回的 feedme 绝对路径>)`
   - `beam_record_fit(galaxy_dir, action_id=<该候选>, round_status_file=<本轮归档的 round_status.json 路径>)` —— **按引用记录，严禁手抄 run_galfit 返回值**；记录被拒（E_RECORD_*）时按错误信息用引用形态重录
   - `survey_round(galaxy_dir, directives="目标成分组合 T={<T 内容>}；候选动作只允许 T 内成分的添加/调参，combo==T 已达成时只允许参数与约束调整")`
   - 读返回的 `termination` / `next_candidate`；`stop=true` 或预算耗尽则进入阶段三
   - 异常处置：survey 返回 **E_GRAPH_STATE** → 父态损坏，按错误信息 `beam_repair_artifacts` 修复后重试（勿重调 VLM）；run_galfit 崩溃 → `beam_mark_failure` 后继续下一候选
3. **预算与去重由引擎执行：** N_max=7 次探索拟合 + 1 次保留 F1（记在 n_f1_reserved，不计入 7）；同格子候选、class 配额、队列截断均由代码裁决——不要因为"专家目标还没到"而绕过引擎手工加轮。
4. **提前锁定（专家模式的效率条款）：** 当图中已存在 **combo==T、verdict=PASS**、且 CLAUDE.md 六条锁定准则全部满足的轮次时，可不等预算耗尽直接进入阶段三（省下的预算留给 F1 仲裁；用 `beam_status` 确认队列中已无 T 内的未执行方向）。
5. **锁定：** 优先在 **combo==T** 的轮次中择优（`beam_status` 的 best_state 与 State ledger）；按 CLAUDE.md 的六条锁定准则直接对照图与最佳轮归档核验（verdict-PASS、concentric `.cons` 链、成分完整性、参数状态）；未通过则在剩余预算内修复重拟合；`beam_grant_repair_budget` **仅限用户显式授权（2026-10-28），agent 不得自主调用**（历史 0/5 的自主授权曾改善 best）——预算耗尽且检查不过时，向用户报告失败项，由用户决定。

### 专家中断协议（拟合过程中的额外指令）

专家可能在任意轮次之间插入新指令。收到中断指令后**必须先落图、再继续拟合**，不得只记在对话上下文里（VLM 无状态，上下文信息不会自动传给后续请求）：

- **结构禁令型**（"不要再加某成分"）→ `beam_set_constraint(action="add", text=<原文>, forbid_structures_json=[...])`：此后每轮 VLM 请求自动携带该约束（digest 的 [Temporary constraints] 块），且入队/出队两层代码拦截——已在队列里的违规候选会在出队时被丢弃，指令立即生效。
- **自由文本指导型**（参数方向、形态判断等）→ `beam_set_constraint(action="add", text=<原文>, forbid_structures_json=[])`：文本持久化进图，从下一次 survey 起每轮随 prompt 注入 VLM。
- **解除指令** → `beam_set_constraint(action="remove", ...)`。
- 落图之后的每一轮 `survey_round`，把专家最新指令的要点并入 `directives` 一并传递（双通道强调）。
- 中断指令与 T 冲突时（例如要求加 T 外成分）：**不执行**，向专家说明冲突并等待确认——T 的变更只能由专家显式重申新的 expert_components。

### 阶段三. F1 仲裁与报告撰写

* **F1 仲裁（经验仲裁，7+1 的保留位）：** 最佳结果若无 F1 且预算未耗尽，先调用 `fourier_mode_analysis` 分析残差图（该工具只返回 m=1 初始参数 am/theta_m + 置信度，不输出 yes/no 结论），然后 `beam_enqueue_f1(galaxy_dir, amplitude=<am>, phase_deg=<theta_m>)` → `apply_candidate` → `run_galfit` → `beam_record_fit`（引用形态），由 **BIC_eff 数值裁决**去留：amplitude>0.02 且 BIC 不恶化才保留 F1 轮，否则保持原最佳。若最佳已含 F1 或预算耗尽，跳过。
* **报告撰写：** 使用 `write_file` 工具将分析结论写入当前星系目录：`analysis_report_xxx.md`。**报告内容包含：**
    * **预处理信息：** 掩膜说明、背景设定依据。
    * **专家指定成分组合：** expert_components 原文、翻译后的 T、每个成分最终采用的模型；T 与拟合证据的矛盾记录（如有，附具体残差/BIC 证据）。
    * **beam 搜索过程摘要：** 各轮成分组合与 BIC_eff 轨迹、被引擎过滤方向的要点（cell-dup / class-cap / 约束丢弃，可引 `beam_status` 决策日志）。
    * **最佳结果锁定分析：** 锁定理由（六条准则逐条）、最终参数表、形态学物理意义解读（如：成分 A 是经典盘结构、成分 B 是致密核心）。
    * **附件索引：** 最佳轮次归档目录（`best_state.archive_dir`）、最终 feedme、拟合对比图（原图/模型/残差）路径。
    * **json 格式化输出：** 在文档最后格式化输出轮次信息，便于规则提取与自动化处理：
    ```json
    {"best_turn":"<最佳轮次的目录名>","components":["<最优轮次包含的物理成分>"],"galaxy_type":"edge-on/face-on/elliptical"}
    ```
    其中 best_turn 的值为 archives/ 下最佳轮次子目录的名称（如 20260414T093323.c1993a48）。
    物理成分类型取值：[Disk,Bulge,Bar,Nucleus,Fourier,SingleSersic]（本分支解空间不含 Companion/Lens）。
    galaxy_type 可选值为 [edge-on,face-on,elliptical]；盘星系的 Disk 成分 q<0.3 则认定为侧向盘（edge-on）。

## 待分析星系

{argument}
