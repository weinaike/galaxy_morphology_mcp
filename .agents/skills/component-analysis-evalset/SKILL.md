---
name: component-analysis-evalset
description: Extract and validate single-step component-analysis evaluation samples from the single-band and multi-band GALFIT historical archives. Use when building source inventories, recovering historical transitions, constructing leakage-free state bundles, adjudicating historical actions, running a pilot extraction, validating a pilot, or freezing the full component-analysis benchmark. Do not use for new GALFIT runs, SED or Image-SED evaluation, training-data construction, or prompt and rule tuning.
---

# Component Analysis Evalset

## Overview

Use this skill to build the approved component-analysis evaluation dataset from historical GALFIT and GalfitS archives. Treat `docs/component-analysis/evaluation-set-design.md` as the scientific authority and `docs/component-analysis/evaluation-set-skill-execution-plan.md` as the implementation authority.

## Workflow

1. Read `AGENTS.md`, `ROADMAP.md`, the execution plan, the design document, and the four files in `references/`.
2. Confirm the design SHA-256 and `component-evalset-v2` before reading either archive.
3. Select exactly one CLI mode and run `/home/www/ENTER/envs/galfit/bin/python -m src.tools.component_evalset <mode>`.
4. Keep both source roots read-only and write only to a new ignored run directory.
5. Treat source inventory and automatic prelabels as evidence, never as final scientific truth.
6. Stop at the stage gate required by the user. `pilot` stops at `PILOT_REVIEW_REQUIRED`; `full` requires a schema-valid approval artifact and a separate freeze step.

Do not run GALFIT or GalfitS, modify expert labels, delete historical files, or create an approval artifact from natural-language confirmation.

<!--

**1. Workflow-Based** (best for sequential processes)
- Works well when there are clear step-by-step procedures
- Example: DOCX skill with "Workflow Decision Tree" -> "Reading" -> "Creating" -> "Editing"
- Structure: ## Overview -> ## Workflow Decision Tree -> ## Step 1 -> ## Step 2...

**2. Task-Based** (best for tool collections)
- Works well when the skill offers different operations/capabilities
- Example: PDF skill with "Quick Start" -> "Merge PDFs" -> "Split PDFs" -> "Extract Text"
- Structure: ## Overview -> ## Quick Start -> ## Task Category 1 -> ## Task Category 2...

**3. Reference/Guidelines** (best for standards or specifications)
- Works well for brand guidelines, coding standards, or requirements
- Example: Brand styling with "Brand Guidelines" -> "Colors" -> "Typography" -> "Features"
- Structure: ## Overview -> ## Guidelines -> ## Specifications -> ## Usage...

**4. Capabilities-Based** (best for integrated systems)
- Works well when the skill provides multiple interrelated features
- Example: Product Management with "Core Capabilities" -> numbered capability list
- Structure: ## Overview -> ## Core Capabilities -> ### 1. Feature -> ### 2. Feature...

Patterns can be mixed and matched as needed. Most skills combine patterns (e.g., start with task-based, add workflow for complex operations).

Template guidance removed; the workflow above is authoritative.

<!-- formal workflow is defined below -->
-->

<!--
## Resources (optional)

Create only the resource directories this skill actually needs. Delete this section if no resources are required.

### scripts/
Executable code (Python/Bash/etc.) that can be run directly to perform specific operations.

**Examples from other skills:**
- PDF skill: `fill_fillable_fields.py`, `extract_form_field_info.py` - utilities for PDF manipulation
- DOCX skill: `document.py`, `utilities.py` - Python modules for document processing

**Appropriate for:** Python scripts, shell scripts, or any executable code that performs automation, data processing, or specific operations.

**Note:** Scripts may be executed without loading into context, but can still be read by Codex for patching or environment adjustments.

### references/
Documentation and reference material intended to be loaded into context to inform Codex's process and thinking.

**Examples from other skills:**
- Product management: `communication.md`, `context_building.md` - detailed workflow guides
- BigQuery: API reference documentation and query examples
- Finance: Schema documentation, company policies

**Appropriate for:** In-depth documentation, API references, database schemas, comprehensive guides, or any detailed information that Codex should reference while working.

### assets/
Files not intended to be loaded into context, but rather used within the output Codex produces.

**Examples from other skills:**
- Brand styling: PowerPoint template files (.pptx), logo files
- Frontend builder: HTML/React boilerplate project directories
- Typography: Font files (.ttf, .woff2)

**Appropriate for:** Templates, boilerplate code, document templates, images, icons, fonts, or any files meant to be copied or used in the final output.

---

**Not every skill requires all three types of resources.**
-->

## Required startup

1. Read `AGENTS.md`, `ROADMAP.md`, both component-analysis documents, and the references in this directory.
2. Confirm the design document SHA-256 and `adjudication_rule_version` before reading historical data.
3. Use only the project CLI:

   ```bash
   /home/www/ENTER/envs/galfit/bin/python -m src.tools.component_evalset <mode>
   ```

4. Keep source archives read-only and write only to a new run directory under `artifacts/component-analysis-evalset/`.
5. Never rewrite extraction logic in an ad hoc shell or Python command.

## Modes and stage gates

- `inventory`: scan both archives, verify labels, hash referenced files, classify exclusions, and produce a pilot-selection proposal. Do not recover or adjudicate actions.
- `pilot`: require an explicit, user-confirmed selection; build candidate/state artifacts only for those objects, then stop with `PILOT_REVIEW_REQUIRED`.
- `validate-pilot`: validate schemas, hashes, leakage checks, counts, and repeatability. Never create approval automatically.
- `full`: require a schema-valid approval artifact with matching design and inventory checksums; separate prepare from freeze and never infer approval from prose.

This first implementation must stop after `inventory` and the pilot-selection proposal unless the user explicitly authorizes the next phase. Do not run GALFIT or GalfitS, create approval, modify expert labels, or delete historical files.

## Frozen semantic rules

- Expert truth is `expert_final_components + semantic_mapping`; historical agent actions are not ground truth.
- F277W `obj1845`, `obj216`, `obj2185`, and `obj2758` map `elliptical` to `single_sersic`, never `disk`.
- Single-band multi-component `disk` uses `expdisk`; `single_sersic` is the single-component elliptical semantic.
- Multi-band mappings include `disk(lop) -> disk + fourier_m1`, `single sersic -> disk`, `nucleus -> agn`, and independent `edge_on_disk`.
- `companion?` is not a hard label. `KEEP_AND_CONTINUE` is not a canonical historical action.
- `CONVERGED` is a terminal output, not a separate terminal-selection task. The benchmark accepts only `CORRECT + high`.

## References

- `references/evaluation-rules.md`
- `references/source-layout.md`
- `references/adjudication-guide.md`
- `references/output-contract.md`
