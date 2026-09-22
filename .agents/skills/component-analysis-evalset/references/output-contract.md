<!-- design_document: docs/component-analysis/evaluation-set-design.md -->
<!-- adjudication_rule_version: component-evalset-v2 -->

# Output Contract

Each run is written to `artifacts/component-analysis-evalset/<run_id>/` and contains config, status, inventory, exclusions, coverage matrix, and pilot-selection proposal. Later modes may add candidates, state manifests, prelabels, adjudication packets, validation reports, and benchmark/audit/excluded JSONL files.

`run-status.json` is monotonic. `inventory` ends at `INVENTORY_READY`; `pilot` ends at `PILOT_REVIEW_REQUIRED`; `validate-pilot` cannot approve; `full` requires a user-created approval artifact and separates `prepare` from `freeze`.

All source references include absolute path, role, size, and SHA-256. Source files are never copied into run output.
