---
name: component-analysis-evalset
description: Extract and validate single-step component-analysis evaluation samples from the single-band and multi-band GALFIT historical archives. Use when building source inventories, recovering historical transitions, constructing leakage-free state bundles, adjudicating historical actions, running a pilot extraction, validating a pilot, or freezing the full component-analysis benchmark. Do not use for new GALFIT runs, SED or Image-SED evaluation, training-data construction, or prompt and rule tuning.
---

# Component Analysis Evalset（Claude Code 薄入口）

本文件只是 Claude Code 的发现入口，不承载独立流程。权威 Skill 内容位于 `.agents/skills/component-analysis-evalset/SKILL.md`，其规则细节位于同目录 `references/`（`evaluation-rules.md`、`source-layout.md`、`adjudication-guide.md`、`output-contract.md`）。开始任何评测集任务前，先完整阅读那份 SKILL.md 和四个 references，不得在本文件之外形成第二套规则。

## 硬门摘要

- 科学权威：`docs/component-analysis/evaluation-set-design.md`；实施权威：`docs/component-analysis/evaluation-set-skill-execution-plan.md`。
- 先读 `CLAUDE.md`／`AGENTS.md`、`ROADMAP.md` 和上述两份文档，再触碰数据。
- 唯一 CLI 入口：`/home/www/ENTER/envs/galfit/bin/python -m src.tools.component_evalset <inventory|pilot|validate-pilot|full>`，不得用临时脚本重写抽取逻辑。
- 两个数据根目录（`/media/data/galfit_run_history`、`/media/data/galfits_run_history`）严格只读；不执行 GALFIT／GalfitS，不修改专家标签，不删除历史文件。
- `pilot` 完成后固定停在 `PILOT_REVIEW_REQUIRED`，等待小鱼儿确认。
- `full` 需要显式 schema-valid approval artifact，不得从自然语言历史记录推断已批准。
- prelabel 只是建议；`CORRECT + high` 准入和 pilot 批准不得由程序或 agent 自动签发。
