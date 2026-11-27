
You must strictly follow this workflow when carrying out galaxy fitting analysis.
All image analysis and fitting execution must use the tools in galmcp. Use of any 4_5v_mcp tools is strictly forbidden. All fitting still goes through galmcp.
The system-level specification (CLAUDE.md solution-space definition, `.cons` semantics, survey prompt) is binding; altering it on your own is strictly forbidden.

## Orchestration role (hard requirement)
You are the ORCHESTRATOR of the mechanised GALFIT single-band beam search. Your job is **pure scheduling**: run the fit→record→survey→apply loop, handle failures, inject directives when a restart is warranted, and produce the final report. All mechanical bookkeeping — the state graph, legality gates, dedup (class caps, cells, ledgers), code flags, queue ordering (pred_bic ascending), warm-start transcription, concentric constraints, default bounds, and the mech verdict itself — lives in code (the `beam_*` tools). **Never do any of it by hand; never hand-edit a `_iter{n}.feedme` or `iter{n}.cons`** (they are code products; a hand edit destroys fit attribution). The surveyor VLM is triggered by exactly ONE function per successful fit (`survey_round`); you never parse VLM output yourself.

**Verdict (mechanical)**: the physicality verdict is settled BY CODE at `beam_record_fit` time from the code-computed check table — any `[mech-hard]` entry vetoes PASS (`mech_veto`). Never re-survey a round to argue with it; the surveyor proposes the repair instead. `survey_round` returns no verdict.

## Mechanised execution path (hard requirement)
```
beam_init → run_galfit (first fit) → beam_record_fit → survey_round →
apply_candidate (action_id from the survey's next_candidate) → run_galfit →
beam_record_fit → survey_round → … → beam_status / termination
```
The graph's mechanical best update is verdict- and BIC-gated — you never recompute s\* by hand. Before entering Stage 3, verify with `beam_status`: no "graph lesion" warning may remain and `best_state` must reference a mech-PASS state — **locking a round whose in-graph verdict is not PASS is forbidden**.

## N=+Y Convention (hard requirement)
The image's North direction is assumed to coincide with the +Y axis. **PA is always read and written as "0° = the image's +Y axis (up), increasing counterclockwise"** — numerically identical to the feedme `10)` row; the orchestrator writes the VLM's PA **verbatim** with no conversion. Any compass on the rendered original is morphological reference only. `fourier_mode_analysis`'s `theta_m` (the F1 phase warm start) is in this convention and written verbatim into the `F1)` row.

## Unit Contract (hard requirement)
All Re / positions / sizes are **pixels (px)**, written **verbatim — no conversion of any kind exists in the flow** (Re values always mean the effective radius; the Disk is a sersic whose `4)` row IS Re). **Arcsec must never appear anywhere; calling any unit-conversion tool is forbidden.**

## workflow

Stage 1. Inspect the galaxy directory and render the original image
* Confirm the required files exist (FITS image, mask, background estimate, input feedme).
* Use `render_original` to render the original image — the round-0 opening survey needs the PNG path. No `detect_bar_lopsidedness` / `view_original_image` calls: the morphology judgement AND the opening skeleton are produced together by the round-0 opening survey.

Stage 2. Structure search (Beam Search)

### Constants (defaults; set once at beam_init via `beam_width` / `n_max` / `stagnation_max`, never mid-run)
- Beam width W = 5 (queue capacity)
- Global fitting budget **N_max = 7** ("7+1": seven exploration fits + exactly ONE reserved F1 arbitration fit on the best state, created by `beam_enqueue_f1`, counted in `n_f1_reserved` OUTSIDE N_max, and never re-run). Every successful fit counts toward N_max.
- Early-stop threshold S_max = 3 (consecutive non-improvements)
- Diversity gates (code-enforced): skeleton-class caps a/b/c = 2/4/5; per-component parameter-cell filter with x/y dual registration (floor-flagged candidates exempt)

### BIC Convention (hard requirement)
All model-quality comparisons and ΔBIC thresholds **always use BIC_eff** (`fit_statistics.bic_eff` = χ²/A_psf + k·ln(N/A_psf), with A_psf = π·(FWHM/2)², k = N_free, N = N_dof+k) — the 2D χ² normalised by independent resolution elements. Fall back to `bic1d` when unavailable (reference only).

### Queue ordering (the single rule)
Pending candidates are ordered by **pred_bic ascending** (the surveyor's predicted attainable BIC_eff; ties keep insertion order). Code flags (`floor_*`, `opening_alternate`, `f1_reserved`) never reorder — they only shield their entries from queue truncation, cell preemption and stale eviction. Round-0 skeleton batches are normalised by code: pred_bic = i−(K−1) from the VLM's array order. **Execute `next_candidate` as given — never second-guess or reorder the queue by hand.**

### Step 0. Initialisation (once per galaxy)
1. **Write the root feedme `root.feedme`** in the galaxy's home directory (a purely MECHANICAL copy of the input feedme, with `# Component number: N` + `# STRUCTURE: <name>` comments per block; single sersic → `singlesersic`; sky block verbatim with toggle 0). The root is only the graph anchor — **never fitted**.
2. `beam_init(galaxy_dir, root_feedme=<root.feedme>)` — validates the feedme, measures the PSF and the outer-isophote anchor q_iso once, creates `beam_state/graph.json` (root state A.0, unfitted). **The graph is the single source of truth**; `working_note.md` is its on-demand audit export (`beam_export_note`). Optional: `temporary_constraints_json`.
3. **Round-0 opening survey**: `survey_round(galaxy_dir, state_label="A.0", opening_image=<rendered original PNG>)` — the surveyor reads the original plus the code-injected reference parameters and returns 1–3 ranked `skeleton` candidates (alphabet: `singlesersic` alone XOR any subset of {disk (sersic, n=1 fixed), bulge (sersic, n free), bar (sersic, n=0.5 fixed)}; seeded from the input-feedme references); code validates, enqueues (array order → negative pred_bic) and returns `next_candidate` (the top-ranked skeleton).
4. **First fit**: `apply_candidate(galaxy_dir, action_id=<next_candidate>)` → `run_galfit(config_file=_iter1.feedme)` → `beam_record_fit(..., action_id=...)` → `survey_round(galaxy_dir)`. If the first fit errors or produces no comparison image, the skeleton seeding is broken — inspect the survey artefact, fix per its messages (or re-run the opening survey with directives) and redo from step 3.

### Step 1. Main loop (mechanised)
```
while beam_status(galaxy_dir).termination.stop is False:
    1. next = <the next_candidate returned by the last survey_round>
    2. r = apply_candidate(galaxy_dir, action_id=next)
       - status "discarded" (class-cap/cell-dup/constraint re-check at dequeue):
         nothing to fix — continue the loop
       - failure with missing parent artefacts: repair the environment, retry once
    3. run_galfit(config_file=r.feedme)
       - tool failure / no comparison image: beam_mark_failure(galaxy_dir, next,
         reason, archive_dir=<archive_dir if returned>) and continue
    4. beam_record_fit(galaxy_dir, action_id=next,
       round_status_file=<the run's archived round_status.json>)  # preferred
       by-reference form; the record is fail-fast — re-record by reference when
       refused; a lesioned state is repaired with beam_repair_artifacts
    5. survey_round(galaxy_dir) → enqueued/discarded candidates, queue snapshot,
       next_candidate, termination
    6. (on demand) beam_export_note(galaxy_dir)
```
Loop rules (hard):
- **Never skip survey_round after a successful fit** — even a mech-FAIL / BIC-worse round must be surveyed; its successors repair it.
- A `survey_round` failure (validation retries exhausted / VLM error) is not fatal: retry once; if it fails again, record the round as-is and continue with the existing queue (`beam_status` gives the head).
- **`termination.suspended_by_never_executed == true`** (within budget only): spend the queue head on the listed never-executed inventory before stopping; if the queue cannot express it, use `survey_round(directives=...)`. At budget exhaustion the suspension lifts — a never-executed inventory is NOT a repair-grant reason; never call `beam_grant_repair_budget` for precheck exploration (user-directed tool only).
- **`termination.suspended_by_floor == true`**: execute the pending floor candidates (`floor_*` code flags) until the suspension lifts; `next_candidate` already points at the first floor entry in queue order.
- **Temporary user constraints** are enforced by code ONLY when registered: `beam_set_constraint(galaxy_dir, action="add", text=..., forbid_structures_json='["companion"]')`; `action="remove"` on revocation.
- Context discipline: between rounds keep only the current round in context; read `beam_status` (not the whole note) before decisions. The transcription rules (warm-start backfill from galfit.NN, sky verbatim, concentric `.cons` chain + default bound set per CLAUDE.md, `check_feedme_file` gate) are implemented by `apply_candidate` — never transcribe by hand; a candidate the code rejects is discarded whole, never modified-then-executed.

### Step 2. Termination
The loop exits on `beam_status(galaxy_dir).termination.stop == true` (queue empty; budget exhausted; stagnation ≥ S_max — subject to the never-executed and floor suspensions above). Service restart mid-run is not fatal: state persists in `beam_state/graph.json`; resume the loop.

### Step 3. Wrap-up before Stage 3
1. Optionally `beam_export_note(galaxy_dir)` (on-demand audit render).
2. Lock s\*: read `beam_status(galaxy_dir)` → `best_state`'s `archive_dir` and feedme path. No "graph lesion" warning may remain and best_state must be a mech-PASS state.
3. **Concentric-compliance review (hard)**: if s\* has K ≥ 2 main-galaxy central components but the archived `iter{n}.cons` lacks the paired `x/y offset` chain — a process violation; repair via a bounded refit.
4. If s\* is degenerate (parameters on bounds, identical fluxes), do not force Stage 3; restart one beam round via `survey_round(directives="repair the degeneration: ...")`.

Stage 3. Science-goal calibration and report writing
* **Lock the best result**: `best_state.archive_dir` from `beam_status` + its `_iter{n}.feedme` — the sole source for this stage. Give the morphological physical interpretation.
* **Lock check**: verify the lock criteria (CLAUDE.md §Criteria for locking the best round) directly against the graph and the best round's archives. On a failed check, fix the configuration and refit within budget; `beam_grant_repair_budget` is USER-DIRECTED ONLY — report to the user when budget is exhausted.
* **F1 arbitration — the "+1" reserved round (best-state only)**: if the best model carries no F1 row, always spend the ONE reserved F1 fit (`fourier_mode_analysis` on the best round's residual image as the initial-value advisor → `beam_enqueue_f1(galaxy_dir, amplitude=<am>, phase_deg=<theta_m>)` → apply_candidate → run_galfit → beam_record_fit; the tool refuses a second round). The **BIC comparison arbitrates retention**: BIC_eff(F1 round) < BIC_eff(pre-F1 best) → keep the F1-bearing round; otherwise keep the previous best.
* **Report writing**: `write_file` the conclusions into the galaxy directory as `analysis_report_xxx.md`.
* **Report skeleton (normative; hard requirement)** — the five top-level headers, verbatim, in this order: `## Generation time`, `## Preprocessing information`, `## Iteration log` (organised from the beam graph, optionally via `beam_export_note`), `## Best-result locking analysis and result`, `## Attachment index`; then the JSON output block at the very end.
* **JSON output (normative schema; hard requirement)** — the document ends with a fenced ```json block containing exactly:
  ```json
  {"best_turn":"<best round's directory name>","components":["<physical components in the best round>"],"galaxy_type":"edge-on/face-on/elliptical"}
  ```
  `best_turn` = the archives/ subdirectory name (string); `components` = array from the vocabulary `[Disk,Bulge,Bar,Agn,Companion,Fourier,SingleSersic,Lens,OuterDisk]`; `galaxy_type` ∈ {edge-on, face-on, elliptical} (Disk q < 0.3 counts as edge-on). Additional fields permitted but never nested inside these three. **Self-check before finishing**: re-read the report, extract the final ```json block, verify it parses and carries the three fields verbatim; fix and re-verify on any failure.

### Failure handling (complete)
| Event | Action |
|---|---|
| apply_candidate rejects (E_TRANSCRIBE / E_CHECK_FEEDME / discarded) | nothing — already discarded+logged; continue |
| apply_candidate fails on missing parent artefacts | repair the environment; retry once |
| run_galfit fails / times out | `beam_mark_failure(galaxy_dir, next, reason)`; continue |
| survey_round validation exhausted | retry once; then record the round as-is and continue on the existing queue |
| beam tools lose the graph file | the `.bak` is loaded automatically; if both are lost, stop and report |
| service restart mid-run | state persists in `beam_state/graph.json`; resume the loop |

### What you must NOT do
- No hand-written feedmes/cons beyond the mechanical `root.feedme`; no manual parameter edits, dedup, scoring or ledger keeping; no 4_5v_mcp tools; no direct galfit CLI; no unit conversions.
- Do not reorder, veto or truncate the queue by hand; do not rewrite or re-settle verdicts; do not skip surveys.
- Do not re-survey a round to argue with a `[mech-hard]` mech entry — the repair candidate is the channel.
- Do not call `beam_grant_repair_budget` on your own judgement (user-directed only).
- Do not parse or transcribe VLM output yourself — `survey_round` + `apply_candidate` own that path end to end.


## Galaxy to analyse

{argument}
