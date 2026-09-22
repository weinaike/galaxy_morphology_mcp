<!-- design_document: docs/component-analysis/evaluation-set-design.md -->
<!-- adjudication_rule_version: component-evalset-v2 -->

# Adjudication Guide

Inventory does not adjudicate actions. Later stages inspect current state, configuration diff, post-action result, expert semantic set, and evidence references.

Use `INCONCLUSIVE` when a transition, action identity, or pre-action necessity cannot be recovered uniquely. Keep exploratory or harmful actions in `audit`, not benchmark. Split a composite action only when an intermediate configuration and fit result exist.

For `CONVERGED`, require the historical best-round reference, expert-component match, valid fit, healthy parameters, and acceptable 1D/2D residual evidence. Do not create a terminal-selection task.

## Single-band decision reading procedure (2026-09-16, user instruction)

- Each single-band round's **input** is `galfit.feedme` / `objxxx_s1_f277w.feedme`; `galfit.NN` files are **fitted outputs** (they carry the converged parameters and Chi^2/nu). The extractor prefers the round's feedme for component semantics (`# STRUCTURE:` comments) and falls back to `galfit.NN` with the n≈0.5→bar heuristic.
- The intended action of a round is read from the round directory's `*_comparison_component_analysis_*.md` (Action Plan / 调整决策 sections), cross-validated against the **next round's feedme** (the executed config). Some rounds contain multiple analysis files — the one whose plan matches the next feedme is the effective one; still ambiguous → route to `excluded`.
- Rounds without an analysis file: consult the object root's `analysis_report.md` iteration log.
- Concentric bulge/bar degeneracy: when numeric lineage (position/size/n) cannot uniquely attribute an atomic change, the analysis-file decision text prevails in adjudication; record the structural reading in the note.
