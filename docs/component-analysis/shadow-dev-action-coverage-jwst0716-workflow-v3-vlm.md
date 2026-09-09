# JWST0716 v3 Action Coverage

This report is generated from the proposal-only shadow summary. It does not execute or lock a formal workflow.
- Completion status: complete
- Validation note:

- Successful rounds: 116/116
- Failed rounds: 0
- Numeric-only: False

## Resolved Actions

- `CONVERGED`: 2
- `NONE`: 1
- `PROPOSE_ADD`: 22
- `REFIT_PARAMETERS`: 91

## Raw Actions

- `CONVERGED`: 2
- `INCONCLUSIVE`: 22
- `PROPOSE_ADD`: 12
- `REFIT_PARAMETERS`: 80

## Candidate Action Reachability

- `INCONCLUSIVE`: 90
- `PROPOSE_ADD`: 84
- `REFIT_PARAMETERS`: 146

## Action Component / Target Breakdown

- `CONVERGED | none`: 2
- `NONE | none`: 1
- `PROPOSE_ADD | bar`: 6
- `PROPOSE_ADD | bulge`: 1
- `PROPOSE_ADD | companion; candidate_1`: 1
- `PROPOSE_ADD | companion; candidate_11`: 1
- `PROPOSE_ADD | companion; candidate_14`: 1
- `PROPOSE_ADD | companion; candidate_16`: 1
- `PROPOSE_ADD | companion; candidate_17`: 1
- `PROPOSE_ADD | companion; candidate_18`: 1
- `PROPOSE_ADD | companion; candidate_2`: 1
- `PROPOSE_ADD | companion; candidate_25`: 2
- `PROPOSE_ADD | companion; candidate_3`: 1
- `PROPOSE_ADD | companion; candidate_4`: 2
- `PROPOSE_ADD | companion; candidate_9`: 1
- `PROPOSE_ADD | disk`: 1
- `PROPOSE_ADD | fourier_m1`: 1
- `REFIT_PARAMETERS | bar:n`: 8
- `REFIT_PARAMETERS | bar:x,y`: 1
- `REFIT_PARAMETERS | bulge:n`: 3
- `REFIT_PARAMETERS | bulge:q`: 1
- `REFIT_PARAMETERS | bulge:re`: 1
- `REFIT_PARAMETERS | bulge:x,y`: 14
- `REFIT_PARAMETERS | disk:n`: 5
- `REFIT_PARAMETERS | disk:x,y`: 1
- `REFIT_PARAMETERS | nucleus:q`: 1
- `REFIT_PARAMETERS | nucleus:re`: 2
- `REFIT_PARAMETERS | obj0:n`: 47
- `REFIT_PARAMETERS | obj0:pa`: 1
- `REFIT_PARAMETERS | obj0:q`: 1
- `REFIT_PARAMETERS | obj1:x,y`: 4
- `REFIT_PARAMETERS | obj2:x,y`: 1

## INCONCLUSIVE Reasons and Resolution

### Rule Reasons

- `CENTRAL_RESOLUTION_QUALITY_V1`: 1
- `COMPANION_NUMERIC_VLM_V1`: 21
- `FOURIER_M1_CONFOUNDING_V1`: 13

### Policy Resolution

- `numeric_only_retry`: 12
- `rule_terminated`: 1
- `trial_fit`: 9

### Resolved Action

- `numeric_only_retry -> PROPOSE_ADD`: 1
- `numeric_only_retry -> REFIT_PARAMETERS`: 11
- `rule_terminated -> NONE`: 1
- `trial_fit -> PROPOSE_ADD`: 9

## KEEP_AND_CONTINUE Follow-up


## CONVERGED Termination Gates

- `ABSOLUTE_RESIDUAL=PASS`: 2
- `CENTER_CONSTRAINT=PASS`: 2
- `FIT_CONVERGENCE=PASS`: 2
- `NO_HIGH_PRIORITY_INCONCLUSIVE=PASS`: 2
- `PARAMETER_HEALTH=PASS`: 2
- `REQUIRED_FIXED_PARAMETERS=PASS`: 2
- `RULES_COMPLETE=PASS`: 2

## Workflow Status

- `CONTINUE`: 113
- `CONVERGED`: 2
- `STOPPED_NEEDS_REVIEW`: 1

## Object PolicyState Continuity

- Objects: 32
- Objects with multiple rounds: 28
- Rows with PolicyState: 116/116
- Rows whose state last_round_id matches the row: 116/116
- Maximum rounds per object: 9

## Legacy residual_analysis Direction Comparison

- V2 rows with a machine-readable legacy atomic-action direction: 0/116; the v2 manifest does not carry this field.
- Existing source comparison remains in `docs/component-analysis/shadow-dev-action-direction-comparison-jwst0716.md`; it is kept separate and is not treated as v2 action truth.
