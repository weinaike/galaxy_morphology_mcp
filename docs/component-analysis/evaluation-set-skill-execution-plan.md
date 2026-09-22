# 成分分析评测集 Skill：Luna 执行计划

状态：方案已获领导认可；Skill、抽取程序和正式评测集均尚未创建。

更新日期：2026-09-10

## 1. 执行权威和本轮授权边界

本文件是将 `docs/component-analysis/evaluation-set-design.md` 落实为项目级 Skill 和确定性抽取程序的唯一执行入口。Luna 必须同时遵守项目根目录的 `AGENTS.md`、`CLAUDE.md` 和 `ROADMAP.md`。

Luna 是执行 agent 的代号，与执行客户端无关：Codex 与 Claude Code 均可担任，本文档规则对两者一致。Codex 从 `.agents/skills/component-analysis-evalset/` 发现 Skill；Claude Code 通过 `.claude/skills/component-analysis-evalset/SKILL.md` 薄入口引用同一份 Skill 内容和 references，不形成第二套规则。文中「Codex 裁决」一律读作「执行 agent 裁决」。

规则优先级如下：

1. 用户在当前对话中的明确要求。
2. 项目根目录 `AGENTS.md`（单波段入口）与 `CLAUDE.md`（多波段入口）。
3. `docs/component-analysis/evaluation-set-design.md` 中已确认的科学和评测规则。
4. 本执行计划中的工程拆分、运行顺序和验收门。
5. Skill 内的 `SKILL.md` 和 references。

如果第 3 项发生变化，必须先更新设计文档和 `adjudication_rule_version`，再同步 Skill、schema、测试和本执行计划；不得只改 Skill 形成第二套规则。

当前只授权 Luna 完成：

- 创建并验证项目级 Skill；
- 实现只读 inventory、pilot、validate-pilot 和 full 所需的确定性代码；
- 运行单元测试和合成／已有小型 fixture 集成测试；
- 读取历史数据并生成 inventory；
- 根据 inventory 生成 pilot 选择建议；
- 在小鱼儿确认 pilot 对象清单后运行 pilot；
- 生成 pilot 审阅材料并停止。

当前没有授权：

- 执行全量抽取；
- 将 pilot 自动视为批准；
- 修改或删除两个历史数据根目录中的任何文件；
- 重新执行 GALFIT／GalfitS；
- 修改专家终态标签；
- 使用 pilot 或正式 benchmark 调整成分分析规则、prompt 或阈值。

领导认可设计方案表示可以进入实施阶段，不等于允许跳过 pilot 审阅门直接执行全量抽取。全量抽取必须在 pilot 审阅通过后，由小鱼儿另行明确授权。

## 2. 目标和非目标

目标是提供一个可重复调用的项目级 Skill，使 Codex 能按照固定流程完成：

```text
historical archives
  -> source inventory
  -> transition recovery
  -> leakage-free state manifests
  -> deterministic evidence and prelabels
  -> evidence-based adjudications
  -> benchmark / audit / excluded artifacts
  -> frozen evaluation set
```

Skill 负责流程编排、阶段门和人与程序的职责分界。项目源码负责目录扫描、解析、哈希、去重、泄漏检查、schema 校验和冻结等确定性工作。Codex 只在设计文档允许的证据范围内完成无法纯程序化的动作裁决。

本次不是训练集构建、prompt／规则调参、新一轮星系拟合、完整 rollout 评测、SED／Image-SED 评测、refit 仲裁子评测、专家参数数值恢复评测或独立 `terminal_selection` 任务。

## 3. Skill 位置和目录结构

根据 OpenAI Codex 的项目级 Skill 发现规则，Skill 固定放在：

```text
.agents/skills/component-analysis-evalset/
```

官方说明：<https://developers.openai.com/codex/skills> 。Codex 会从当前工作目录向仓库根目录扫描 `.agents/skills`。

目录结构固定为：

```text
.agents/skills/component-analysis-evalset/
├── SKILL.md
├── agents/
│   └── openai.yaml
└── references/
    ├── evaluation-rules.md
    ├── source-layout.md
    ├── adjudication-guide.md
    └── output-contract.md
```

v1 不在 Skill 内建立第二套 Python 脚本。确定性逻辑统一位于项目源码，通过一个 CLI 入口调用。只有未来确认 Skill 需要脱离本仓库分发时，才考虑增加薄包装脚本或打包为 plugin。

不得在 Skill 中增加 `README.md`、`CHANGELOG.md`、`QUICK_REFERENCE.md` 或重复的实施记录。

## 4. Skill 内容契约

### 4.1 `SKILL.md`

使用 `skill-creator` 自带的 `init_skill.py` 初始化，Skill 名称必须为 `component-analysis-evalset`。YAML frontmatter 只保留 `name` 和 `description`：

```yaml
---
name: component-analysis-evalset
description: Extract and validate single-step component-analysis evaluation samples from the single-band and multi-band GALFIT historical archives. Use when building source inventories, recovering historical transitions, constructing leakage-free state bundles, adjudicating historical actions, running a pilot extraction, validating a pilot, or freezing the full component-analysis benchmark. Do not use for new GALFIT runs, SED or Image-SED evaluation, training-data construction, or prompt and rule tuning.
---
```

正文使用命令式表述，并控制在 500 行以内。至少包含：

1. 先读取 `AGENTS.md`、`ROADMAP.md`、本执行计划和设计文档。
2. 校验设计文档 SHA-256 和 `adjudication_rule_version`。
3. 判断用户请求对应 `inventory`、`pilot`、`validate-pilot` 或 `full`。
4. 调用唯一 CLI，不自行用临时 Python／shell 重写抽取逻辑。
5. 明确自动步骤和人工裁决步骤的分界。
6. 运行前检查原始数据只读边界和输出目录。
7. 每个阶段完成后执行 schema、checksum、泄漏和可重复性校验。
8. `pilot` 完成后强制停止，等待小鱼儿确认。
9. `full` 必须验证显式 approval artifact，不能依据自然语言历史记录猜测已批准。
10. 汇报样本数和对象数，并分别报告单波段和多波段。

### 4.2 `agents/openai.yaml`

使用 `init_skill.py` 或 `generate_openai_yaml.py` 生成，不手写未验证字段。固定 UI 内容：

```yaml
interface:
  display_name: "Component Analysis Evalset"
  short_description: "Build and validate the galaxy component-analysis benchmark"
  default_prompt: "Use $component-analysis-evalset to build a read-only pilot evaluation set from the approved historical archives."
```

不设置图标、品牌色或 MCP dependency。`allow_implicit_invocation` 保持默认值；触发边界由 description 控制。

### 4.3 references

- `evaluation-rules.md`：只做设计文档章节索引、版本和规则摘要，不复制整份设计文档。
- `source-layout.md`：记录两个数据根目录、标签入口、目录变体、解析优先级和已确认语义映射。
- `adjudication-guide.md`：记录逐转换核验清单、reason code 解释和冲突处理方式。
- `output-contract.md`：记录 CLI 模式、输出目录、schema、状态机和 approval artifact 格式。

每个 reference 顶部都记录 `design_document`、`design_sha256` 和 `adjudication_rule_version`。测试必须检查这些值与当前设计配置一致。

## 5. 项目源码结构

新增一个内部 package 和一个 CLI，避免多个脚本各自实现扫描和解析：

```text
src/component_analysis/evalset/
├── __init__.py
├── config.py
├── inventory.py
├── labels.py
├── transitions.py
├── state_manifest.py
├── evidence.py
├── adjudication.py
├── leakage.py
├── fingerprint.py
├── validation.py
└── freeze.py

src/tools/component_evalset.py
```

| 模块 | 唯一职责 |
|---|---|
| `config.py` | 解析运行配置、模式、源目录、输出目录和版本信息 |
| `inventory.py` | 只读扫描数据源并记录文件角色、大小、mtime 和 SHA-256 |
| `labels.py` | 读取专家标签并执行已冻结的 canonical component 映射 |
| `transitions.py` | 恢复父子轮次、配置差异、历史动作和复合动作状态 |
| `state_manifest.py` | 构建截止当前轮的显式输入清单和 `history_context` |
| `evidence.py` | 生成结构距离、拟合健康、残差、参数和约束事实 |
| `adjudication.py` | 校验裁决记录，不把自动 prelabel 直接签发为真值 |
| `leakage.py` | 检查未来轮次、当前动作说明、最终报告和 best-round 信息泄漏 |
| `fingerprint.py` | 生成稳定 transition fingerprint 并归并 aliases |
| `validation.py` | 执行 schema、引用、checksum、数量和可重复性检查 |
| `freeze.py` | 只从已验证裁决生成 benchmark、audit 和 excluded 产物 |
| `component_evalset.py` | 提供唯一命令行入口并按阶段调用上述模块 |

优先复用现有 feedme／lyric、FITS、summary、artifact adapter 和 action contract，不复制 parser。复用前必须用测试确认它支持历史数据格式；不支持时只增加最小适配层。

## 6. Schema 和运行配置

至少新增：

```text
src/schemas/evaluation_run_config.schema.json
src/schemas/evaluation_source_inventory.schema.json
src/schemas/evaluation_decision_candidate.schema.json
src/schemas/evaluation_state_manifest.schema.json
src/schemas/evaluation_action_prelabel.schema.json
src/schemas/evaluation_action_adjudication.schema.json
src/schemas/evaluation_set_manifest.schema.json
src/schemas/evaluation_pilot_approval.schema.json
```

`canonical_action` 应引用或严格对齐现有 workflow action schema，不再维护另一套动作枚举。所有 schema 设置 `additionalProperties: false`，版本字段必填。

运行配置至少包含：

```json
{
  "schema_version": "evaluation-run-config@v1",
  "run_id": "evalset-YYYYMMDDTHHMMSSZ",
  "mode": "inventory",
  "design_document": "docs/component-analysis/evaluation-set-design.md",
  "design_sha256": "<sha256>",
  "adjudication_rule_version": "component-evalset-v2",
  "single_band_root": "/media/data/galfit_run_history",
  "multi_band_root": "/media/data/galfits_run_history",
  "output_root": "artifacts/component-analysis-evalset",
  "selection_manifest": null,
  "approval_artifact": null
}
```

`run_id` 和时间戳不参与逻辑结果比较。输出根目录加入 `.gitignore`，但 schema、Skill、源码、测试和明确选定的小型 fixture 进入版本控制。不得把原始 FITS、批量 comparison PNG 或完整历史输出复制进仓库。

## 7. CLI 模式和状态机

统一入口：

```bash
/home/www/ENTER/envs/galfit/bin/python -m src.tools.component_evalset <mode> [options]
```

### 7.1 `inventory`

- 校验两个数据根目录和标签入口。
- 生成对象、批次、Image 轮次和文件角色清单。
- 逻辑排除无标签对象、SED／Image-SED 和关键产物缺失记录。
- 计算源文件 SHA-256。
- 生成标签组合、目录完整性和候选 pilot 覆盖矩阵。
- 不恢复科学动作，不生成正确标签。

输出状态为 `INVENTORY_READY` 或 `INVENTORY_FAILED`。

### 7.2 `pilot`

必须显式传入小鱼儿确认的 `pilot-selection.json`。不得默认取前 N 个对象，不得随机抽样，不得在运行时静默换对象。

- 仅处理 selection 中列出的对象和历史批次。
- 恢复轮次转换并执行 fingerprint 去重。
- 构建无未来信息的 state manifests。
- 自动提取证据并生成 prelabels。
- 生成逐转换 adjudication packet。
- 由执行 agent 依据设计文档写入裁决建议。
- 生成 pilot 审阅报告，不冻结正式 benchmark。

输出状态固定为 `PILOT_REVIEW_REQUIRED`。自动检查全部通过也不能自动变为 `PILOT_APPROVED`。

### 7.3 `validate-pilot`

- 校验所有 pilot artifact schema、引用和 checksum。
- 重新执行泄漏检查。
- 检查 canonical action 唯一性、复合动作处理和 aliases。
- 检查 `CORRECT + high` 是否满足核心准入门。
- 输出按 mode、object、action 和 verdict 的计数。
- 检查同配置重复运行的规范化结果一致性。
- 生成 `pilot-review.md` 和 `pilot-validation-report.json`。

只有小鱼儿审阅后，才能创建 `evaluation-pilot-approval@v1` artifact。它必须引用 pilot run ID、inventory checksum、design checksum、rule version、批准时间和批准人，不得由程序自行填写批准结论。

### 7.4 `full`

运行前必须同时满足：

- 存在 schema-valid 的 approval artifact；
- approval 引用的 pilot 状态是 `PILOT_APPROVED`；
- 当前 design SHA-256、规则版本和 inventory checksum 与 approval 一致；
- 用户在当前任务中明确要求执行全量抽取；
- 使用新的 full run ID 和空输出目录。

`full` 分两个可恢复阶段：

```text
prepare -> FULL_REVIEW_REQUIRED -> freeze -> FROZEN
```

`prepare` 生成全量候选、state、prelabels 和 adjudication packets，然后暂停。只有裁决完整且 validation 全部通过，`freeze` 才生成正式 benchmark。不得把 prelabel 自动转为最终裁决。

## 8. 运行产物

每次运行写入独立目录：

```text
artifacts/component-analysis-evalset/<run_id>/
├── run-config.json
├── run-status.json
├── evaluation-source-inventory.json
├── evaluation-source-exclusions.jsonl
├── pilot-selection-proposal.json
├── evaluation-decision-candidates.jsonl
├── evaluation-state-manifests/
├── evaluation-action-prelabels.jsonl
├── adjudication-packets/
├── evaluation-action-adjudications.jsonl
├── leakage-report.json
├── validation-report.json
├── pilot-review.md
├── evaluation-set-v1.jsonl
├── evaluation-audit-v1.jsonl
├── evaluation-excluded-v1.jsonl
└── evaluation-set-v1-manifest.json
```

不同模式只生成适用子集，缺失的下游产物不得用空文件伪装完成。原始 FITS、PNG、feedme、lyric、summary 和报告通过绝对路径、文件角色、大小和 SHA-256 引用，不复制到 run 目录。每条引用必须能追溯到 inventory。

`run-status.json` 使用以下单向状态：

```text
INITIALIZED
INVENTORY_READY
PILOT_SELECTION_REVIEW_REQUIRED
PILOT_RUNNING
PILOT_REVIEW_REQUIRED
PILOT_APPROVED
FULL_PREPARING
FULL_REVIEW_REQUIRED
FREEZING
FROZEN
FAILED
```

失败后可以显式 resume，但不得覆盖已通过 checksum 的 artifact。

## 9. Pilot 对象选择

inventory 完成后生成覆盖矩阵和 `pilot-selection-proposal.json`，Luna 随即停止并让小鱼儿确认精确清单。建议总数为 12～16 个对象，按覆盖目标选择，不按目录顺序选择。

| 数据类型 | 建议数量 | 必须覆盖 |
|---|---:|---|
| Gadotti 单波段 | 4～5 | 常见 Disk、Bulge、Bar 组合；终态匹配和不匹配轨迹 |
| JWST F277W 单波段 | 5～6 | 4 个 `elliptical -> single_sersic`；至少一个非 elliptical 组合 |
| JWST 多波段 | 4～6 | `single sersic -> disk`、`disk(lop) -> disk + fourier_m1`，并尽量覆盖 Bulge、Bar、AGN、Companion、`edge_on_disk` |

整个 pilot 还必须至少包含：

- 一个跨批次重复对象；
- 一个 `_2` 变体或对象别名；
- 一个可拆分的复合动作候选；
- 一个因缺少中间状态而应排除的复合动作候选；
- 一个关键产物缺失或轮次关系不明确的排除案例；
- 一个历史最佳轮与专家终态一致的对象；
- 一个历史最佳轮与专家终态不一致但可能包含局部正确动作的对象；
- 至少一个 `CONVERGED` 候选。

无法在 12～16 个对象内覆盖全部情况时，优先保证语义映射、防泄漏、去重和裁决边界，在报告中列出未覆盖项，不放宽准入门。

`pilot-selection.json` 必须显式列出 `object_id`、mode、历史批次范围、选择理由、覆盖标签、预期验证边界和小鱼儿确认状态。

## 10. Pilot 审阅材料

`pilot-review.md` 必须让小鱼儿不阅读程序日志也能完成核验，至少包含：

1. 数据源、design checksum、规则版本和代码 commit。
2. 选择对象及覆盖理由。
3. 原始轮次数、去重后转换数和 aliases 数。
4. 每个对象的专家终态、历史最佳轮和 `terminal_consistency`。
5. 每个转换的前轮、后轮、配置差异和 canonical action。
6. 动作前必要性、动作后有效性、后续稳定性和证据引用。
7. `action_verdict`、`confidence` 和 reason codes。
8. `benchmark`、`audit`、`excluded` 的建议分层及原因。
9. 泄漏、checksum、schema 和可重复性检查结果。
10. 所有待确认、证据冲突和未覆盖规则。

关键 FITS／残差证据提供现有 comparison PNG 或明确路径，不生成新的拟合结果。

小鱼儿重点检查轮次关系、配置差异、语义映射、state 泄漏、动作前后证据、复合动作、重复路径和 `CONVERGED` 终止证据。

## 11. 自动化与人工裁决边界

程序可以确定文件存在性和 checksum、标签白名单和 canonical mapping、明确父子轮次、配置差异、结构距离、可解析拟合健康事实、fingerprint、aliases、input cutoff 和 schema 有效性。

程序不得自行确定残差的物理成分解释、证据冲突时的科学结论、只有文字描述的复合动作顺序、未执行分支的可接受性、prelabel 到 `CORRECT + high` 的升级或 pilot 批准。

Codex 裁决必须基于 adjudication packet 的显式证据，逐条写出 `evidence_refs` 和 reason codes。无法核验时使用 `INCONCLUSIVE`，不得为增加核心样本数量降低置信度门槛。

## 12. 必须冻结的科学和评测语义

- 对象级硬真值只有 `expert_final_components + semantic_mapping`。
- 历史轨迹不是科学家逐轮示范，历史动作不能直接作为正确答案。
- 单波段 F277W 的 `obj1845`、`obj216`、`obj2185`、`obj2758` 固定映射为 `single_sersic`，不能映射为 `disk`。
- 单波段多成分 Disk 使用 `expdisk`；`single_sersic` 表示椭圆星系的 Sersic 单成分语义。
- 多波段保留 `disk(lop) -> disk + fourier_m1`、`single sersic -> disk`、`nucleus -> agn` 和独立 `edge_on_disk`。
- `companion?` 不作为硬标签。
- `KEEP_AND_CONTINUE` 不是 canonical historical action。
- `CONVERGED` 是统一单步决策中的终止输出，不建立 `terminal_selection`。
- `ACCEPT_REFIT`／`REJECT_REFIT` 不进入 v1 主任务。
- 核心集只接受 `CORRECT + high`。
- Gadotti 参数只作辅助证据，不作为 v1 数值真值。
- 无中间配置／拟合结果的复合动作不拆分进入核心集。
- 单波段和多波段分别建集、分别报告，bootstrap 以 `object_id` 为单位。

## 13. 防泄漏实现要求

每个 state manifest 必须有 `input_cutoff` 和文件角色。当前动作决定文本、含未来信息的 Working Note、最终报告、best-round 结论、下一轮产物、专家标签和标签侧字段都不能进入被测输入。

泄漏检查至少包含：

1. 路径角色白名单检查。
2. round／timestamp 截止检查。
3. JSON 字段黑名单检查。
4. Markdown 关键词和来源文件检查。
5. manifest 引用是否全部来自当前轮或之前。
6. 人工抽样打开输入文件复核。

只靠文件名不足以判断泄漏；持续追加的 Working Note 即使 mtime 较早也不得整份进入 state。标签侧引用必须与 input bundle 物理分离。

## 14. Fingerprint 和去重要求

fingerprint 的规范化输入至少覆盖：

```text
mode
canonical object_id
current config checksum
current result checksum
post-action config checksum or null
canonical normalized diff
canonical action or terminal marker
```

时间戳、绝对批次路径和 run ID 不进入 fingerprint。相同 fingerprint 只保留证据最完整的一条 canonical source，其他路径写入 `aliases`，不得删除原始目录。

`_2` 后缀先映射到基础 `object_id`，再依据内容判断是独立重跑还是副本，不能只根据名称合并。

## 15. 实施阶段和验收门

### S0：创建 Skill 骨架

- 使用 `skill-creator/scripts/init_skill.py` 初始化 Skill。
- 写入 `SKILL.md`、`agents/openai.yaml` 和 4 个 references。
- 运行 `quick_validate.py`。
- 验证显式 `$component-analysis-evalset` 能触发。

验收：目录、frontmatter、UI metadata 和引用有效；未创建抽取结果。

### S1：冻结 schema 和运行配置

- 新增第 6 节 schema。
- 固化 mappings、reason codes 和规则版本。
- 增加 design checksum 检查和输出目录忽略规则。

验收：schema 正例通过，缺字段、额外字段、错误动作和版本不一致负例失败。

### S2：实现 inventory

- 实现两类历史目录 adapter。
- 输出 inventory、exclusions、coverage matrix 和 pilot selection proposal。
- 检查源目录运行前后关键元数据不变。

验收：计数可解释，每个排除项有 reason code，不修改历史数据。完成后让小鱼儿确认 pilot 清单。

### S3：实现转换恢复和去重

- 恢复父子轮次，解析结构、参数和约束差异。
- 识别复合动作，生成 fingerprint 和 aliases。

验收：fixture 覆盖 ADD、REMOVE、REPLACE、PROMOTE、REFIT、CONVERGED、可拆／不可拆复合动作和重复路径。

### S4：实现 state manifest 和泄漏检查

- 构建显式文件引用和 `history_context`。
- 执行 input cutoff 和字段／角色检查。
- 为标签侧建立独立 evidence refs。

验收：合法当前轮输入通过；下一轮、最终报告、动作文本、专家标签和 best-round 注入测试失败。

### S5：实现证据和预标注

- 计算结构距离，提取 fit health、残差、参数、约束和后续稳定性。
- 输出 `pre_action_necessity`、prelabels 和 adjudication packets。

验收：prelabel 明确为建议，任何代码路径都不能直接签发 benchmark 标签。

### S6：实现裁决校验和冻结

- 校验 verdict、confidence、reason codes 和 evidence refs。
- 实现分层、approval gate、full resume 和规范化可重复性比较。

验收：没有 approval 时 full 失败；`CORRECT + medium`、证据缺失和错误 `CONVERGED` 不能进入 benchmark。

### S7：运行 pilot

前置：小鱼儿已确认 `pilot-selection.json`。

- 使用新 run ID 运行 pilot。
- 由执行 agent 根据 packet 完成裁决建议。
- 执行 `validate-pilot` 并生成 `pilot-review.md`。

验收：机器检查通过，报告完整，待确认项显式列出。到此停止，不创建 approval artifact，不执行 full。

### S8：全量抽取

本阶段不属于当前授权。只有小鱼儿确认 pilot 并明确要求继续后才执行 approval、full prepare、全量裁决、freeze 和基线评测。

验收：正式 manifest 包含源数据 checksum、design checksum、rule version、代码 commit、样本数、对象数和分层统计；结果按单波段、多波段和动作类型分别报告。

## 16. 测试要求

新增测试建议：

```text
tests/test_evalset_labels.py
tests/test_evalset_inventory.py
tests/test_evalset_transitions.py
tests/test_evalset_state_manifest.py
tests/test_evalset_leakage.py
tests/test_evalset_fingerprint.py
tests/test_evalset_adjudication.py
tests/test_evalset_freeze.py
tests/test_component_evalset_cli.py
```

测试必须覆盖 4 个 F277W elliptical、多波段映射、无标签和 SED 排除、`_2` 处理、六类 canonical action、`KEEP_AND_CONTINUE` 拒绝、复合动作、防泄漏、fingerprint 稳定性、`CONVERGED` 门、核心准入、approval gate、可重复性和源目录只读性。

验证顺序：

1. 新增模块聚焦测试。
2. 现有 component analysis 相关测试。
3. Skill `quick_validate.py`。
4. `git diff --check`。
5. 项目完整 pytest。
6. 真实 inventory 只读运行。
7. pilot 和 `validate-pilot`。

真实 inventory 和 pilot 不得以运行 `tests/test_run_galfit.py` 代替；前者验证历史抽取，后者验证 GALFIT 执行工具，目标不同。

## 17. 失败和停止条件

以下情况必须停止当前阶段并写入 `FAILED` 或 review-required 状态：

- design checksum 或规则版本不匹配；
- 标签缺失、解析失败或出现未裁定语义；
- 源文件在运行中变化；
- 父子轮次无法唯一恢复；
- state 泄漏未来信息；
- canonical action 无法唯一映射；
- schema 或 checksum 失败；
- 输出目录非空且未明确 resume；
- pilot 清单未确认；
- full 缺少当前任务授权或有效 approval artifact；
- 自动步骤需要修改历史目录、执行 GALFIT 或删除文件。

单条候选证据不足不导致整个 run 失败；该候选进入 `INCONCLUSIVE`／`excluded` 并保留原因。只有基础契约、数据一致性或安全边界失败时才停止整个 run。

## 18. Luna 汇报格式

每个阶段只汇报：

```text
阶段：Sx
状态：completed | review_required | failed
新增／修改文件：<paths>
验证：<commands and results>
数据影响：read-only | generated artifacts only
关键计数：objects / rounds / transitions / benchmark / audit / excluded
阻塞或待确认：<items>
下一步：<single next stage>
```

不能把“代码完成”“测试通过”“pilot 通过”和“科学标签正确”合并成一个结论。每项只声明实际验证过的范围。

## 19. 当前交付终点

Luna 本轮的终点是：

```text
Skill validated
  + implementation tests passed
  + source inventory reviewed
  + pilot selection approved by 小鱼儿
  + pilot generated
  + pilot validation passed
  + pilot-review.md ready
  -> STOP
```

不得越过该终点执行 S8。小鱼儿审阅 pilot 后，如果发现规则、字段或证据不足，先更新设计文档和版本，再重跑新 pilot；不能直接修改冻结结果掩盖问题。
