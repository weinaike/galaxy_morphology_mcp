
You must strictly follow this workflow when carrying out galaxy fitting analysis.
Focus on fitting the physical components of galaxies — disk, bulge, edge-on disk, bar, AGN, lopsidedness, companions, lens — adding model components driven by residual features; higher-order residual features may be left unfitted.
All image analysis and fitting execution must use the tools in galmcp. Use of any 4_5v_mcp tools is strictly forbidden.
The system-level specifications, the Physicality Verdict of `generate_galfit_beam_actions`, and the audit results of the `best-round-verifier` subagent are binding; altering them on your own is strictly forbidden.

## N=+Y Convention (global PA convention of this workflow; hard requirement)
This workflow adopts the **N=+Y convention**: the image's North direction is assumed to coincide with the +Y axis. **PA is therefore always read and written as "0° = the image's +Y axis (up), increasing counterclockwise"** — numerically identical to the feedme `10)` parameter row (GALFIT's "+Y axis = 0°"), and the orchestrator writes the VLM's PA **verbatim** into the `10)` row with no conversion. Any lime compass (N/E arrows) on the `render_original` image is for morphological reference only — **always read PA against the image's vertical axis, never against the compass**. `detect_bar_lopsidedness`'s `bar.pa_deg` and `lopsidedness.phase_deg` can be used directly under this convention.

## Unit Contract (global unit convention of this workflow; hard requirement)
All Re / positions / sizes in this workflow are **pixels (px)**: a px value read by the VLM off a comparison panel is in the same unit and reference frame as the feedme parameter rows, and the orchestrator writes it **verbatim — no conversion of any kind** (the expdisk Rs conversion Re=1.68·Rs is the sole exception, applied by the orchestrator when writing the `4)` row: Re triplets given by the VLM always refer to the effective radius Re). **Arcsec must never appear anywhere in the flow**, and calling any unit-conversion tool is forbidden.

## workflow

Stage 1. Inspect the galaxy directory and analyse the original image
* **Inspect the galaxy directory**: confirm the required files exist — the FITS image, the mask file, the background estimate file, the feedme file, etc.
* **Inspect the raw data and image**: use render_original to render the original image, view_original_image to analyse the rendering, and detect_bar_lopsidedness to detect components; form an overall judgement of the galaxy's basic morphology (galaxy type and component prediction). This provides the initial guesses for the subsequent fit.
* **Interpreting and recording the detect_bar_lopsidedness result**:
    - The tool returns `{"bar": {detected, pa_deg, b_over_a}, "lopsidedness": {detected, mag, phase_deg}}` (single band).
    - **Nature of the detection (important)**: it is a **top-down morphological hint**, not a bottom-up component verdict. It looks only at isophotes / Fourier features of the original image, without the "add a component → check whether the residual improves" fitting validation. Therefore:
      - **detected=True = weak positive evidence**: the prior probability of that component rises, and Stage 2 should actively generate the corresponding candidates (without guaranteeing the fit will accept them).
      - **detected=False = zero evidence, not negative evidence**: it does not prove the component absent. bar/lop can still be found in the residual-driven bottom-up exploration (typically: high-dynamic-range images reveal an elongated inner structure once the central components are built; or quadrupole bar signatures surface once the bulge n is released).
      - **Gold standard**: the final arbiter of a component's existence is residual-driven fitting validation (add → refit → residual improvement + physical parameters), not the Stage-1 detection.
    - **PA rule**: `detect_bar_lopsidedness`'s `pa_deg` / `phase_deg` can be written directly into candidate PAs and the feedme `10)` row under the N=+Y convention — no conversion.
    - **Lopsidedness decision**: `lopsidedness.detected=True` → tag "m=1 Fourier high priority" in the `working_note.md` header; in every Stage-2 call of `generate_galfit_beam_actions`, carry that tag into the `[Stage-1 conclusions]` field of `global_state_description`, ensuring the VLM proposes "append an F1 line to the Disk block" at the first opportunity once the Disk is established.
    - **Write the `working_note.md` header**: record the bar/lop conclusions, PA, b/a, A1, phi1 in `working_note.md` for later distillation into `global_state_description` (the tool no longer injects the full working_note — avoid attention dilution; the orchestrator distils per §Generation Spec for global_state_description / local_state_description). **Wording warning**: an undetected component must be written as "not detected (zero evidence, non-determinative)" or similar explicit wording — never a bare "NOT detected / absent", lest the VLM and the orchestrator mistake the hint-level zero evidence for a determinate negative in the scoring stage.

Stage 2. Structure search and dynamic validation (Beam Search mode)
*Goal: search the structure space in parallel for the optimal physical component combination with a beam of width W=5, avoiding a greedy single path getting stuck at degenerate rounds (failed constraints, parameter collapse) in local optima. Each in-beam branch still follows the bottom-up, one-component-at-a-time incremental philosophy; beam search merely widens "the single next step" into "several parallel candidate paths".*

### Constants (hard; not adjusted per galaxy)
- Beam width W = 5 (priority-queue capacity)
- Global fitting budget N_max = 15 (every `run_galfit` call counts, success or failure)
- Early-stop threshold S_max = N_max = 15 (cap on consecutive non-improvements; currently equal to N_max so the early stop is effectively inert — termination is controlled by the budget N_max alone)
- **Per-combination attempt cap = 4**: any single component combination (the same physical-identity inventory C) may be fitted at most 4 times — a diversity-promoting cap that forces exploration toward untried combinations instead of re-testing saturated ones (see the formal definitions and Step 1.b.0/R0)

### BIC Convention (global; hard requirement)
All model-quality comparisons, state-ledger records and ΔBIC threshold judgements in this workflow **always use BIC_eff** (`fit_statistics.bic_eff`, = χ²/A_psf + k·ln(N/A_psf): the 2D χ² divided by the PSF area A_psf=π·(FWHM/2)², with k=N_free and N=N_dof+k the number of fitted data pixels) — an effective BIC normalised by the number of independent resolution elements, more comparable than a per-pixel BIC. When BIC_eff is unavailable (e.g. a missing PSF file), fall back to the 1D BIC (`bic1d`). **Every unannotated "BIC" / "ΔBIC" / "BIC got worse" below refers to this convention**; if the parameter-summary table also shows a 1D BIC row, it is reference only.

### Formal definitions (compact, for consistent state semantics)
- **State** s = (C, P, R, reduced_χ², BIC, depth), with C the component inventory (identified by feedme `# STRUCTURE:` names), P the parameters (feedme parameter-row values + free/fixed toggles; Re/positions always px), R the residual diagnostics (comparison PNG + 1D residual features), reduced_χ² and BIC from that round's `run_galfit` `fit_statistics` (reduced_χ² prefers the 1D `chisq1d_nu`, falling back to the 2D `chi2_nu`; BIC always takes **BIC_eff** (see the BIC Convention), falling back to `bic1d`), and depth the state's depth in the search graph (s₁ has depth=1).
- **Action** a = a composite of 1–2 semantically cohesive atomic operations, of three kinds: `add(type, params)` (must also declare its `# STRUCTURE:` name), `remove(component)`, `tune(component, param_delta)` (including releasing/fixing toggles and tightening/relaxing `.cons` bounds). Bundling unrelated operations is forbidden.
- **Transition** T(s, a) = s': take the parent state's `_iter{n}.feedme` as the **structure template** (with `# STRUCTURE:` comments and free/fixed toggles), warm-start-backfill the parent galfit.NN converged values (see Step 1.b.1) → apply a → write `_iter{n}.feedme` → `check_feedme_file` → `run_galfit` → read the archived artefacts (`output_param_file` = galfit.NN converged values, `image_file` = comparison PNG, `summary_file` = statistics) → call `generate_galfit_beam_actions` for the next-level candidates. s'.depth = s.depth + 1.
- **Initial state** s₀: parsed from the input feedme (C and P from the input structure, R = original-image diagnostics, reduced_χ²=⊥, BIC=⊥, depth=0). The input feedme is usually a single-sersic start; if it already has several components, take it as s₀ unchanged (do not force-split). s₀ is an input, not a fit product; the first fit (Step 0.4) runs `run_galfit` on `_iter1.feedme` to obtain s₁ directly, **without candidate generation**.
- **Current best** s\*: the state with the highest orchestrator composite score (scoring dimensions in §Deduplication and Ranking), **not** simply the lowest reduced_χ².
- **State signature** sig(s): the canonical form = sorted component inventory × per-component `(type, n-state, Re(px), Mag, q, PA (N=+Y))` × free/fixed configuration × `.cons` bound configuration. The canonical component inventory returned by `check_feedme_file` is the signature's source of record. The signature is the sole vehicle of graph-search deduplication (compared by the VLM before generating and by the orchestrator before executing — one shared yardstick).
- **Search graph (graph, not tree)**: the state space is a **graph** — different action sequences can reach the same state (e.g. "add X then delete X" returns to an ancestral structure). Two ledgers serve as the visited set: the **input ledger** (canonical forms of all executed `_iter{n}.feedme`: structure × toggle configuration × `.cons` bound bands × initial-value bands) and the **result ledger** (all fitted states' sig + BIC + verdict + zombie flags). Transitions are of two kinds: **closed-form** (the product state can be projected exactly without fitting: remove-only, parameter revert, bound restoration) and **black-box** (add, tune, etc. — outcomes unpredictable, must be fitted). Cycle-detection rules are in Step 1.b.0.
- **Zombie component [zombie]**: a component whose post-fit flux fraction relative to the brightest component is < 0.5% (**a relative criterion — absolute magnitudes are forbidden**: survey depths differ by orders of magnitude; the fraction follows from the Mag difference, `f_i/f_max = 10^(−0.4·(Mag_i − Mag_min))`). Two states differing only by zombie components are equivalent in the result ledger (a zero-flux component does not change the model's expressible image).
- **Per-combination attempt cap [combo-exhausted] (hard, diversity-promoting)**: any single component combination — the same **physical-identity inventory C** (equivalence per §Deduplication and Ranking criterion 1: naming swaps such as bulge n=0.5 ↔ bar allowed; parameter values and tune axes do **not** distinguish attempts) — may be fitted at most **4** times. Attempt counts come from the input ledger's structure signatures. Once a combination's 4th attempt has run, it is **[combo-exhausted]**: every further candidate whose `expected_C'` is physically identical to it is **discarded outright** — both at dequeue (Step 1.b.0/R0) and at enqueue (Step f) — logged in the cross-branch decision log as a "combo-exhausted discard". The orchestrator marks the combination "[combo-exhausted]" in the [State ledger] notes so the VLM stops proposing it and diversifies toward untried combinations. Note: a `remove`/`add` candidate whose `expected_C'` is a **different** inventory (e.g. trimming one component off the exhausted combination) remains legal — only candidates landing back on the exhausted inventory are banned.

### Step 0. Initialisation (once per galaxy)
1. Create (or reset) `working_note.md` in the galaxy's home directory with the empty skeleton of §Multi-Branch working_note Template; write the Stage-1 VLM morphology judgement, the bar/lop conclusions, the PA (N=+Y convention) and b/a into the header.
2. Initialise: global fit counter n = 0; global feedme counter global_iter_id = 0; branch counter branch_counter = 1 (i.e. "A"); current best s\* = None; priority queue Q = []; stagnation counter stagnation = 0.
3. Call `render_original` on the original image (if not done in Stage 1); record the rendered image path.
4. **First fit (deterministic, no VLM)** — the input feedme is already the starting structure; fit it directly without candidates:
    1) `global_iter_id += 1` (→ 1). Read + Write the input feedme verbatim into the galaxy home directory as `_iter1.feedme`, **while ensuring every component block carries a semantic `# STRUCTURE: <NAME>` comment line** (between `# Component number: N` and the `0)` line; the starting single sersic is usually named `disk`, an elliptical start may be `sersic`) — GALFIT's output files drop that comment, so the input side must maintain the naming, otherwise the comparison legend, the parameter summary and the ledger signatures all degrade to type names. **The sky block (value + toggle) is the manually provided setting of the input feedme and is carried verbatim** — the sky is never fitted and never a search dimension in this workflow; later rounds inherit it unchanged (see the warm-start rules). If B) shares a name with the input, give it a distinct output name (avoid overwriting the input feedme). Relative paths resolve against the feedme's directory.
    2) Call `check_feedme_file(_iter1.feedme)`; fix per its messages on failure. **Record the returned `psf_fwhm_px` / `a_psf_px2` in the working_note header** (they characterise the PSF once, before any fit): they feed the default Re lower bound (`max(0.1, 0.5×FWHM_PSF)`) and enter the `[Meta]` line of every `global_state_description` — the VLM needs A_psf for the companion psf-vs-sersic selection rule (beam prompt C3).
    3) Call `run_galfit(config_file=<absolute path of _iter1.feedme>)`. `n += 1` (→ 1).
    4) Failure handling: if the tool errors or produces no comparison image / summary, the input feedme itself is broken — a degenerate case; do not enter the main loop, fix the input manually and redo Step 0.
    5) Build s₁: `C₁`, `P₁` from `_iter1.feedme` (the first fit modifies neither components nor parameters); reduced_χ² and BIC from the returned `fit_statistics`; `R₁` from the archived comparison PNG; the converged values from the archived `output_param_file` (galfit.NN). `s\* = s₁` (the only state so far, set unconditionally; if the Physicality Verdict of Step 0.5 is FAIL, do not retroactively undo s₁ — any later PASS state supersedes it directly in the main loop). Append an A.1 subsection under branch A of `working_note.md` (fit #1, feedme = `_iter1.feedme`).
5. **First candidate generation (depth=1)**: with s₁'s comparison image as `comparison_file`, `_iter1.feedme` as `feedme_file`, the archived `output_param_file` (galfit.NN) as `fitted_param_file`, and the archived `summary_file` as `summary_file`, call:
    ```
    generate_galfit_beam_actions(
        feedme_file        = <absolute path of _iter1.feedme>,
        fitted_param_file  = <absolute path of s₁'s galfit.NN (run_galfit's output_param_file)>,
        comparison_file    = <absolute path of s₁'s comparison PNG (run_galfit's image_file)>,
        summary_file       = <absolute path of s₁'s summary>,
        global_state_description = "<distilled per §Generation Spec: [State ledger]/[Verified basins]/[Refuted hypotheses] are empty or input priors only; mainly [Meta (pixel contract)]/[Stage-1 conclusions] and [Budget]>",
        local_state_description  = "<per §Generation Spec: the concrete problems of s₁'s fit (bound-hit parameters / residual features / identity anomalies); candidate-direction suggestions are strictly forbidden>",
        branch_id         = "A",
        parent_label      = "A.1",
        depth             = 1,
    )
    ```
    Per the depth=1 rules the tool returns 1–2 candidates (lop detected → one +F1 candidate; bar detected → 1–2 Bulge/Bar candidates; neither → one standard add(Bulge)), **along with s₁'s `## Physicality Verdict` block — parse it into the A.1 subsection (the s\* update gate applies strictly from A.2 on)**.
6. Score each returned candidate per §Deduplication and Ranking to get g ∈ [0,1]; truncate by g descending to W=5 and enqueue into Q. Each queue element records `(s_parent=s₁, a, σ_from_vlm, g, branch_id, depth=2)` — note: the next state reached by executing these candidates has depth 2.
7. Update the `working_note.md` beam-state snapshot.

### Step 1. Main loop (run while no termination condition holds)
```
while Q non-empty and n < 15 and stagnation < 15:
```
a. **Dequeue**: take the highest-g (s, a, σ, g, branch, depth) from Q and remove it.
b. **Execute the transition T(s, a)**:
    0) **Graph-search cycle detection (hard; before writing any feedme or incrementing global_iter_id)**:
       - **R0 per-combination attempt cap (all actions; checked first)**: count the executed attempts whose inventory is physically identical to the candidate's `expected_C'` from the input ledger's structure signatures (identity per §Deduplication and Ranking criterion 1 — naming swaps allowed; parameter values/tune axes do not distinguish). If the count has reached **4**, the combination is **[combo-exhausted]** — **discard the whole candidate** (log in the "cross-branch decision log" as a "combo-exhausted discard" with the action_id and the 4 hit rounds), `stagnation += 1`, continue the loop. Candidates whose `expected_C'` is a **different** inventory (e.g. removing a component from the exhausted combination) pass this check.
       - **R1 vs the input ledger (all actions)**: transcribe a per §Faithful-Execution Principle into the canonical form of a hypothetical feedme (structure × toggle configuration × `.cons` bound bands × initial-value bands; Re/positions in px) and compare line by line with the **input ledger** (tolerance bands as in §Deduplication and Ranking). Equivalent within the bands → the same input gives a deterministic optimiser no new information — **discard the whole candidate** (record in the "cross-branch decision log" with the action_id and the ledger line hit), `stagnation += 1`, continue the loop.
       - **R2 closed-form projection (remove-only / parameter-revert / bound-restoration actions only)**: their product states can be **projected exactly without fitting** (drop the removed component from the parent signature / restore the reverted parameter; survivors inherit the toggle/bound configuration per the warm-start rules). Compare the projected signature line by line with the **result ledger** (zombie-aware: states differing only by [zombie] components are equivalent):
         - **Exact hit** (structure × toggle/bound configuration both identical; a zombie-equivalent hit counts) → **zero-cost rollback**: write no feedme, call no run_galfit, **do not count n**; record a rollback edge in working_note (`<branch>.<round> --a--> ≡<hit round>`), `stagnation += 1`, continue the loop. **Do not re-run Step d** — the rollback target's candidate generation already happened in its original round; its successors are enqueued or executed, and regenerating would only duplicate candidates.
         - **Structure matches but toggles/bounds differ** → no rollback (e.g. bulge n free vs fixed are different science questions), but tag the candidate "[suspected near-duplicate]" into Step f: dimension 4 (degeneracy penalty) scores 0 (full penalty) unless the novelty claim shows the configuration difference carries an independent hypothesis.
         - No hit → proceed to 1).
       - Black-box transitions (add / tune etc.) do R1 only (R2 does not apply).
    1) `global_iter_id += 1`; take s's `_iter{n}.feedme` as the **structure template**, apply a's primitives, and write `_iter{global_iter_id}.feedme` in the galaxy home directory. **Warm-start rules (hard)**: the VLM's diagnosis is conditioned on the parent's **converged solution** — the model-panel ellipses and the legend's Mag/Re/n/q/PA draw the parent galfit.NN converged values, and parameters the VLM did not propose to tune are implicitly "acceptable as they are". The child round must therefore make the declared action a **clean increment on the parent converged solution**: for every parameter **not declared** by the primitives, backfill the parameter row's initial value with the parent galfit.NN converged value (free/fixed toggle kept from the parent round); **directly reusing the parent feedme's old input values is forbidden** — those were last round's initial guesses, not the solution the VLM assessed; restarting from old guesses lets unmentioned parameters wander again, polluting the candidate→result attribution and wasting convergence budget. Note that galfit.NN drops `# STRUCTURE:` comments — **inherit structure and comments from the parent `_iter{n}.feedme` template; backfill numbers only from galfit.NN**. Parameters declared by the primitives take the candidate's declared values; an `add`ed component has no parent value and is initialised per the candidate's declared parameters (expdisk writes Rs=Re/1.68). **Exception — the sky block (value + toggle) is the manually provided setting of the input feedme: carried verbatim into every `_iter{n}.feedme`, never backfilled from galfit.NN and never modified** (the sky is not a search dimension; see CLAUDE.md's solution-space definition). **Transcription must strictly follow §Faithful-Execution Principle** — semantic core fields declared by the candidate (component type, `# STRUCTURE:` name, n/toggle state, magnitude constraints, add/remove and centre-constraint strategy, F1 order, etc.) must not be altered; a candidate the orchestrator deems flawed is **discarded whole** (logged in the "cross-branch decision log"), never modified-then-executed.
    2) **Concentric-constraint check for main-galaxy components (hard; execute regardless of whether a mentions constraints)**: count the main-galaxy central components in this round's `_iter{global_iter_id}.feedme` (Disk/Bulge/Bar/Lens, i.e. `# STRUCTURE:` names disk/bulge/bar/lens, excluding comp/companion/secondary/satellite) as K:
       - **K ≥ 2**: **must** write `iter{global_iter_id}.cons` in the galaxy home directory, its syntax and full example strictly per the CLAUDE.md section "Concentric constraint for main-galaxy central components — `.cons` authoring specification" — chain the anchor (the Disk; absent one, the brightest central component) with all subordinate central components' numbers using `_`, and write the **paired** lines (both indispensable): `<chain>  x  offset` and `<chain>  y  offset`. Key points: the anchor's `1)` toggle stays `1 1` (once effective, the group translates jointly and the centre is optimised by the fitter); subordinates' `1)` rows stay `1 1` (GALFIT rewrites them to `2 2` as the constrained marker); the feedme `G)` item points at the file; any other `.cons` bounds this round (re/n/mag ranges, companion position windows) are **merged into the same file** (GALFIT loads exactly one constraint file). **Companion numbers (`# STRUCTURE:` names containing comp/companion/secondary/satellite) are strictly forbidden in the offset chain** — instead pin the position with a soft window: `<number>  x  <init-5>  <init+5>` and `<number>  y  <init-5>  <init+5>` (±5px; the initial value is the VLM's measured pixel coordinate). **Syntax warning**: the space-pair (`1 2 x 0.0 0.0`) and dash-pair (`1-2 x 0.0 0.0`) forms are **silently ineffective** on this machine's GALFIT (no error, but the centres drift) — strictly forbidden; the only effective form is the chain `offset` hard constraint.
       - **K ≤ 1** (a single component or the starting single sersic): no offset chain; a `.cons` may still be written for other bounds.
       - **Default bound set (hard; every round, independent of K)**: feedme parameter rows carry no bounds — without a `.cons` the solution space is open and the bound-hit diagnostics are blind. Per the CLAUDE.md "Solution space definition — mandatory default bound set", write the default `.cons` bounds for **every non-sky component**: `re` row (`max(0.1, 0.5×FWHM_PSF)` to half the fit-region side; FWHM_PSF from `fit_statistics.psf_fwhm`, fallback 1 px; expdisk rows in Rs = Re/1.68), `n` row (0.1–8, sersic components only), `q` row (0.05–1.0, shaped components only); centres per the offset-chain rule above, or a ±2 px window when K ≤ 1; companions keep their ±5 px windows. **Merged into the same `iter{global_iter_id}.cons`** (GALFIT loads exactly one constraint file). Provenance convention: these defaults count as "original" bounds in the d.ii bound-hit reporting; candidate-declared tightenings count as "self-imposed"; candidates may tighten but not widen past the default cap without an explicit declaration.
       - **Effectiveness check (in step c)**: after a successful fit, check the archived galfit.NN — chained subordinates' `1)` toggles should read `2 2` and the chained components' x,y values should be exactly identical; otherwise the constraint did not take effect (the syntax was silently ignored) and the round is handled as a b.5 failure: fix the `.cons` and rerun.
       - This check is the orchestrator's mandatory duty and **must not depend on the VLM's candidate declarations** — even if the candidate says nothing about concentricity, the orchestrator must complete the `.cons` per the rules above.
    3) **`check_feedme_file` must be called** to validate the structure; on failure (or a "component missing `# STRUCTURE:` name" warning) fix and re-validate — skipping is forbidden.
    4) Call `run_galfit(config_file=<absolute path of _iter{global_iter_id}.feedme>)`. `n += 1`.
    5) **Failure handling**: if the tool errors or produces no comparison image / summary, record the (s, a) in the "branch: failure archive" section of `working_note.md`, add a to s's taboo set, `stagnation += 1`, continue the loop.
c. **Build the new state s'**: read reduced_χ² and BIC from the `run_galfit` return (`fit_statistics`'s `chisq1d_nu` and **`bic_eff`**, falling back to `chi2_nu` / `bic1d` respectively when missing; see the BIC Convention); `R'` is the archived comparison PNG; `C'`, `P'` come from the new feedme and the archived `output_param_file` (galfit.NN converged values). Round naming: within the branch, `branch.local_round` (A.2, A.3, B.1…; A.1 is taken by the first fit), decoupled from global_iter_id. s' has depth `depth + 1`.
d. **Candidate generation + physicality verdict of the fit (unconditional hard requirement — see §Diagnostic-First Principle of Candidate Generation; the two orthogonal sources merge into Step f for scoring and enqueuing)**: this step **must** run whenever Step b's fit succeeded (the b.5 failure branch excepted), regardless of the verdict, whether the BIC got worse, whether parameters hit bounds, whether unspent candidates remain in the queue, or how tight the budget is. Each main-loop round's candidates come from two orthogonal sources — d.i is VLM-driven from residual-image visual analysis plus the **physicality verdict**; d.ii is orchestrator-driven from objective thresholds on the fit numbers. The two merge and pass through exactly the same dedup / scoring / truncation rules (Step f), competing equally for the queue.
    - **d.i VLM visual candidates + physicality verdict**: with the new comparison image as `comparison_file`, the new feedme as `feedme_file`, the archived galfit.NN as `fitted_param_file`, and the archived summary as `summary_file`, call:
        ```
        generate_galfit_beam_actions(
            ...,
            global_state_description = "<distilled from working_note per §Generation Spec and refreshed at Step g: [Meta (pixel contract)]/[Stage-1 conclusions]/[State ledger]/[Rollback edges]/[Verified basins]/[Refuted hypotheses (with ΔBIC values + failure reasons + reopening conditions)]/[Budget]>",
            local_state_description  = "<per §Generation Spec: the concrete problems of s''s fit (bound-hit parameters / residual features / identity anomalies) + the numeric-rule delegations; candidate-direction suggestions are strictly forbidden>",
            branch_id         = branch,
            parent_label      = <branch>.<local_round>,
            depth             = depth + 1,   # depth of the parent state the new candidates apply to
        )
        ```
        Per the `depth+1` staging the tool returns candidates (depth+1=2 → 2–3; depth+1≥3 → 2–4), **and the returned Markdown's top carries a `## Physicality Verdict` block (verdict / failed_checks / swap_hint) — the VLM's physicality verdict on s''s fit (centred on the concentric nesting of the components' 2·Re ellipses on the Model panel: disk ⊃ lens ⊃ bar ⊃ bulge, inner areas clearly smaller than the adjacent outer layer, an overall blemish-free "onion", and the outermost component's 2·Re not exceeding the fitting region)**. The orchestrator parses it and records it **verbatim** into working_note (no rewriting):
        - `verdict: PASS` → s' qualifies for the s\* comparison in Step e.
        - `verdict: FAIL` → s' **loses s\* eligibility** (even with better χ²/BIC); annotate the round's section with "physicality FAIL veto" and paste the failed_checks evidence; generate protected recovery candidates per **§Recovery Protocol for Non-Physical Results** (competing with the VLM candidates in Step f). Per the prompt's contract the VLM gives at least one failed_checks repair candidate this round.
        - **Verdict block missing or unparseable** (VLM ignored the format): the orchestrator applies a minimal fallback — call `check_feedme_file` for the canonical inventory and compare the main-galaxy total order `re_disk > re_lens > re_bar > re_bulge` numerically (expdisk via `re_effective_px`; no other tools; this is only the minimal numerical subset of the VLM's nesting check and cannot catch morphological violations like poking-out or crossing); record the result in working_note tagged "[verdict fallback]". Normally rely on the VLM; this fallback only prevents a format miss from disabling the gate.
        - `swap_hint: disk_bulge_swap`: confirm the VLM candidates include a disk ↔ bulge label-swap repair; if the VLM omitted it, the orchestrator generates it as a B-fill per §Faithful-Execution Principle (g ≥ 0.5, mandatory-retention), tagged "[orchestrator swap supplement]".
    - **d.ii Orchestrator numeric-rule candidates**: from s''s fit numbers (galfit.NN + feedme toggles + `.cons` bounds) the orchestrator runs objective numeric checks and reports the **phenomena and numerical facts** to the VLM; **what candidates get generated is decided entirely by the VLM per `beam_action_generation_prompt_galfit.md`** (the orchestrator neither generates nor hints directions). These checks target "visually present but numerically suspicious components" — the VLM, seeing a component in the image, tends to keep it, but if the numbers are suspicious (e.g. extremely low flux, or a base component's Re too small and compensated by an extended one), the orchestrator hands the objective numbers to the VLM, which combines them with the original image to decide whether to generate adjustment candidates (removal or parameter tuning). Currently defined triggers:
        - **Companion-necessity check (numeric + visual dual axis)**: if s' has a Companion, read the companion's and disk's Mag from the `check_feedme_file` inventory and compute the flux ratio `f_companion/f_disk = 10^(−0.4·ΔMag)` (`ΔMag = Mag_companion − Mag_disk`).
          - Ratio > 1%: the companion's flux is significant; the removal check does not trigger.
          - Ratio ≤ 1% (**condition A hit**): the orchestrator **generates no candidate**; it writes the three facts (flux ratio, ΔMag, ratio to the brightest component) into this round's `local_state_description` in the form: "Companion condition A hit (objective facts): companion flux ratio = 0.4%, ΔMag = 5.91." — **with no operational suggestion attached**. Condition-B visual verification and the `remove(Companion)` candidate are decided autonomously by the VLM per `beam_action_generation_prompt_galfit.md`'s Companion-Removal Verification: generate the remove candidate only when A (numerically faint) AND B (no visible source in the original) both hold; with a visible blob, do not.
          - If s' has no Companion, skip.
        - **Disk-Re bottleneck check (bound-hit extended component + Re/flux degeneracy)**: if s' has a lens or bar, read its fitted Re (effective radius for expdisk) and its re upper bound in `.cons` (without a bound, the "bound-hit" sub-criterion fails), plus the disk's Re and Mag. When the lens/bar Re hits the cap (fitted value ≥ 0.98 × cap) **and** either sub-criterion below holds, call it a "disk-Re bottleneck hit" — the extended component wants to be bigger but is held back by the disk Re, an objective signal that the disk Re is too small:
          - **Sub-criterion A (Re degeneracy)**: `Re_lens/bar / Re_disk ≥ 0.85` (the two are highly degenerate along Re; the lens/bar nearly catches the disk).
          - **Sub-criterion B (flux approaching or exceeding)**: `Mag_lens/bar ≤ Mag_disk + 0.2` (the lens/bar flux ≥ ~83% of the disk, or more). This catches the degeneration where the lens/bar is forced to take over the disk's outer flux.
          - **Fact report** (after either sub-criterion hits; if the disk Re is also bound-hit, do not trigger — the disk has no room): write the bottleneck **facts** into `local_state_description` in the form: "Disk-Re bottleneck signal (objective facts): lens(bar)_Re=5.0px at the cap (re_max=5.0px), Re_lens(bar)/Re_disk=0.93 [≥0.85 (Re degeneracy hit) / <0.85], lens(bar)_Mag=16.86 [≤ disk_Mag+0.2=17.28 (flux-approaching hit) / above]. disk_Re=6.1px (not bound-hit / unconstrained)." — **no operational suggestion**; generating `tune(disk, larger Re)`-type candidates (1D-curve visual confirmation included) is decided by the VLM per the Disk Outer-Flux-Deficit Trigger Rule. Such VLM candidates enter the queue with a floor of **g ≥ 0.5** (mandatory retention — the floor affects ranking only, not generation rights).
          - **Rationale**: the rule targets the "lens/bar inflating to compensate the disk's outer flux" degeneration — when the lens/bar Re hits the cap and degenerates with the disk in Re or flux, the root cause is usually a too-small disk Re, not a genuinely oversized lens/bar. The signal is fully objective (from the fit numbers) and does not rely on the VLM's visual reading of low-SNR outskirts (which has proven unstable — once the lens compensates the outer flux the 1D curve flattens and the visual check stops firing).
          - If s' has no lens/bar, skip.
        - **Lens Re inflation check (parameter-state trigger; three competing candidates)**: if s' has a lens, read the lens's fitted Re, its re cap in `.cons` (no bound → the "cap hit" sub-criterion fails), and the disk's Re and Mag.
          - **Trigger (either hits)**:
            - **Cap hit**: `lens_Re ≥ 0.98 × re_max`.
            - **Re inversion**: `lens_Re ≥ disk_Re` (the VLM's physicality verdict catches this, or the orchestrator compares directly).
          - **Fact report** (the orchestrator writes the signal's **facts** into `local_state_description`; **no candidate generated, no suggestion attached** — the competing paths A/B/C (+ conditional D) are generated by the VLM per the Lens Re Inflation Trigger Rule; the orchestrator only scores and enqueues): format: "Lens Re inflation signal (objective facts): lens_Re=Xpx at the cap (re_max=Ypx) [and/or] lens_Re=Xpx ≥ disk_Re=Zpx (Re total-order inversion). lens_Mag=W, disk_Mag=V. Bound provenance: current re_max=Y [self-imposed (original re_max=R) / original / unconstrained]. Degeneracy numbers: Re_lens/Re_disk=ratio; lens_Mag−disk_Mag=Δ." — provenance and degeneracy numbers follow the applicability wording of paths A/B/C/D so the VLM can judge which apply.
          - **Absence handling (objective feedback, no surrogate generation)**: if the VLM omits one of A/B/C (or D when its conditions hold) while the trigger holds and states no waiver in physical_motivation, the orchestrator **does not generate it** (generation rights belong entirely to the VLM) — instead: (1) log "[VLM candidate-absence violation]" in the working_note decision log (the missing item + trigger values); (2) note the fact objectively in the next round's `local_state_description` (e.g. "last round D's conditions held but the candidate was absent"), letting the VLM self-correct under new evidence. VLM-generated A/B/C/D candidates enter the queue with a floor of **g ≥ 0.5** (mandatory retention).
          - **Rationale**: lens inflation is the most common post-addition degeneration, with four mutually exclusive physical hypotheses (lens overrunning / a too-small disk skeleton / a parasitic lens / **an over-tight self-imposed cap**); single-direction exploration misses the best repair. Forced multi-path competition lets the beam search's parallel exploration work fully; hypothesis D exists specifically for "the hit cap was set by the last round's own repair action, not by physics" — there, further tightening or removal just spins in the wrong direction, and the only correct action is to relax.
          - If s' has no lens, skip.
        - **Generic bound-hit parameter scan (generalised rule; objective fact report, no candidates)**: each round the orchestrator compares s''s galfit.NN fitted values against the `.cons` bounds (including `iter{n}.cons` and the input constraints) **parameter by parameter** (all components, all bounded parameters), listing every bound-hit parameter (|fitted − bound| ≤ 2%×|bound| or equal), and for each reports **five facts** objectively: parameter name and fitted value (px, per the pixel contract), the hit bound, the direction (upper/lower), the **bound provenance** (self-imposed = the current bound differs from the input configuration's original, produced by an earlier tightening/repair; original = same as the input), and the number of consecutive bound-hit rounds (from the working_note state ledger). Write the list verbatim into `local_state_description` (format: "Bound-hit parameter list (objective facts): lens_Re=9.0px at the cap re_max=9.0px [self-imposed (original 12.6px)] 2 consecutive rounds; companion_Re=1.0px at the floor re_min=1.0px [original] 4 consecutive rounds; …"), **with no candidate-direction suggestion or imperative attached** ("please generate / suggest generating / confirm generating / should relax" are all violations) — the generation duty is decided by the VLM per the tiers of the 🔑 Bound-Relaxation Rule (Tier 1 self-imposed mandatory / Tier 2 original competing / exemptions). **Unbounded parameters cannot hit a bound and never enter the list.**
        - Further objective numeric triggers (anomalous sky, anomalously faint Mag, etc.) may be appended here in the same pattern — **describe the phenomenon and numbers only; generation decisions always belong to the VLM**.
    - **Traceability**: the d.ii numeric checks (whether or not the VLM generated the corresponding candidates) are annotated "[orchestrator numeric-rule delegation]" in the relevant branch section of `working_note.md`, recording the measured values (companion: flux ratio/ΔMag; disk-Re bottleneck: lens/bar Re and re_max, disk Re and re_max, Re ratio, flux ratio, sub-criterion A/B hit; lens Re inflation: lens Re and re_max, disk Re, cap-hit/inversion, lens_Mag, disk_Mag, the triggered competing path A/B/C/D — D additionally recording the **provenance judgement between the current re_max and the original input re_max** and both degeneracy sub-criteria) and the VLM's visual-verification conclusions. This audits later decisions to keep/remove/adjust components.
e. **Register s''s score and update s\* (physicality gate)**: score s' per the score function of §Deduplication and Ranking; **only when d.i's Physicality Verdict is PASS (or the fallback verdict passes)** may s' compare against the previous best — if score(s') > score(s\*), s\* ← s' and `stagnation = 0`; else `stagnation += 1`. An s' with verdict=FAIL **does not take part in the s\* comparison** (count `stagnation += 1`), but its repair candidates still enter the queue in Step f — the repair loop is the beam search's core path, and a FAIL state's successors may quickly recover to PASS. Overwrite the "beam-state snapshot / current best s\*" section of `working_note.md`. **Note: stagnation is for termination only and never justifies skipping Step d — s''s successors may beat s\* (see §Diagnostic-First Principle of Candidate Generation).**
f. **Dedup + score + enqueue**: for every new candidate (the merged set of d.i VLM and d.ii orchestrator candidates):
    - **Combination-cap gate (first)**: any candidate whose `expected_C'` is physically identical to a **[combo-exhausted]** combination (4 executed attempts) is **discarded outright** — do not score or enqueue it (log in the "cross-branch decision log" as a "combo-exhausted discard"). Additionally, do not enqueue same-combination candidates beyond what would take that combination's executed + enqueued count past 4 — a single combination never consumes more than 4 of the exploration slots.
    - Semantic dedup against the (s_j, a_j) already in Q per §Deduplication and Ranking; keep the higher g of equivalents.
    - **Compare against the execution history (graph-search visited set; the same signature criteria as the in-Q dedup)**: per Step 1.b.0's R1/R2, compare the candidate's hypothetical canonical input / closed-form projection signature against the **input ledger** and **result ledger** — R1 hit → discard; R2 exact hit → do not enqueue (executing it would be a zero-cost rollback; enqueuing wastes a Q slot — log it); structure-only match → tag "[suspected near-duplicate]" (dimension 4 scores 0).
    - Score the survivors on the six dimensions to get g (near-duplicates' degeneracy-penalty dimension per the b.0 rules).
    - **g_min threshold**: discard any candidate with **g < 0.3** outright (log in the "cross-branch decision log" with the action_id and the reason). This stops low-quality candidates from piling up and keeping the queue from ever emptying.
    - Enqueue (s', a_new, σ_new, g, branch, depth=depth+1) into Q; re-sort by g descending; truncate to W=5. Log the truncated elements in the "cross-branch decision log" too.
g. **Persist**: append this round's record to the relevant branch section of `working_note.md` (configuration / tool calls / components / C, P summary / reduced_χ² / BIC / **the VLM physicality verdict (verdict and failed_checks summary)** / VLM residual features / the enqueued action_ids); overwrite the beam snapshot (with Q's current 5 entries and the n counter); **maintain the two ledgers (graph-search visited set; hard)** — after a successful fit: append `_iter{n}.feedme`'s canonical form (the `check_feedme_file` output) to the input ledger; append the state signature + BIC + verdict to the result ledger (components with flux < 0.5% of the brightest tagged [zombie]; if the result is zombie-equivalent to a ledger line, append a rollback edge); **refresh the `global_state_description` distillation** (per §Generation Spec: append this round to [State ledger]; increment [Rollback edges]/[Verified basins]/[Refuted hypotheses (with failure reason and reopening condition)]; refresh [Budget] — the next d.i call uses the updated version).
h. **Derive a new branch (optional)**: when a candidate differs markedly from the current in-beam mainstream and g ≥ 0.5, the orchestrator may open a new branch letter (branch_counter += 1, e.g. "B") and add a "Branch B" section in working_note.md. New branches share the global n and global_iter_id to keep the budget in check.

### Step 2. Termination (stop when any holds)
- Q is empty;
- n ≥ 15;
- stagnation ≥ 15 (the 15 highest-priority (s, a) dequeued in a row produced no s' better than s\* — the beam has converged; the threshold equals N_max, so n ≥ 15 fires first in practice).

### Step 3. Wrap-up before Stage 3
1. Write into the "cross-branch decision log" of `working_note.md`: the termination condition, the cumulative fit count n, the number of branches explored, and the truncated candidate action_ids.
2. Lock s\*: in the "beam-state snapshot / current best s\*" section of `working_note.md`, confirm its `archives/<timestamp>.<hash>/` directory and `_iter{global_iter_id}.feedme` path — these two paths feed Stage 3.
3. **Concentric-constraint compliance review (hard)**: if s\* has K ≥ 2 main-galaxy components (Disk/Bulge/Bar/Lens) but the corresponding `_iter{n}.feedme`'s `G)` item does not point at a `.cons` containing the paired `x/y offset` chain, this is a process violation — return to Step 1.b.2, complete the `.cons`, rerun that round, then enter Stage 3.
4. If s\* is a degenerate state (parameters on bounds, bulge/disk fluxes identical), do not force Stage 3; instead inject "repair the degeneration" as a hard constraint into `generate_galfit_beam_actions`'s `local_state_description` and restart one beam search round (reset Q and stagnation; keep n and global_iter_id).

### §Diagnostic-First Principle of Candidate Generation (orchestrator hard requirement)

**Core proposition**: Step d (candidate generation + physicality verdict) is the beam search's diagnostic loop, not a "reward when the fit improves". Whenever Step b's fit succeeds and produces a comparison image / summary (i.e. the b.5 failure branch was not taken), Step d **must** run unconditionally — whatever the physicality verdict, whether the BIC got worse, whether parameters hit bounds, whether unconsumed candidates remain in the queue, or how tight the budget is. No exceptions — the VLM's visual diagnosis is the beam search's core loop; skipping it degrades the orchestrator into a greedy "read numbers and guess directions" search, losing multimodal diagnosis.

**Rationale**: a rising BIC for s' does not mean the physical hypothesis is wrong — commonly the candidate's direction is right but some secondary parameter (centre / PA / Re magnitude / n / q) was poorly initialised and the fitter converged to a suboptimum. s''s residuals then carry the diagnostic information of "which parameter needs correcting", and only `generate_galfit_beam_actions` can translate the residuals into corrective candidates. Skipping Step d degrades the beam search into greedy search and misses the "same direction, corrected parameters" successor — precisely the beam search's core value over greed.

**Generic failure→correction patterns** (recognised autonomously by the VLM at generation time; the orchestrator must not pre-specify these directions in `global_state_description` / `local_state_description`, see §Generation Spec):
- A component's centre was misestimated → s''s residuals form a dipole between "model position" and "true position" → `tune(component, x_real, y_real)` (px written directly)
- A component's PA is oblique to the true major axis → s''s residuals form a quadrupole → `tune(component, pa)` (N=+Y contract)
- An added component's Re is too small → s''s residuals form a central ring of positive residual → `tune(component, Re_init≈...)` (px written directly)
- An added component is degenerate with the parent's → some component's identity collapses in s' (n/Re bound-hit) → release/fix n, or break the degeneracy with a `.cons` bound

**Execution check**: before the next dequeue, confirm the working_note's branch section already lists "this round's generate_galfit_beam_actions candidate action_ids"; if missing, the step was skipped — dequeuing is forbidden; return to Step d and do it.

### §Recovery Protocol for Non-Physical Results (protected recovery candidates for physicality FAIL)

**Core proposition**: on a physicality FAIL (VLM Physicality Verdict = FAIL), the VLM's residual-driven repair candidates tend to address visually obvious problems (PA offsets, centre offsets) rather than mechanical fixes driven by numerical diagnosis like "tighten the Re bound" — because a Re total-order violation is diagnosed from the legend/summary's exact numbers (e.g. `re_lens=13px > re_disk=7px`) and may have no direct visual counterpart. If such recovery candidates went through the same g_min=0.3 truncation as the rest, they would often be dropped for low scores (no visible residual improvement), abandoning the FAIL path too early.

The protocol's solution: **do not bypass the VLM** (it is still called in Step d.i and, per the prompt's contract, itself proposes failed_checks repairs); the orchestrator generates **protected recovery candidates** in Step d.ii, guaranteed enqueued via §Deduplication and Ranking's mandatory-retention mechanism (floor g ≥ 0.5), competing fairly with the VLM's. When a recovery candidate is dequeued and fitted, it passes through the full b→c→d→e→f flow (including the next round's VLM verdict and candidate generation); the recovery chain advances through the beam search's natural iteration.

**Trigger**: Step d.i's VLM Physicality Verdict = FAIL.

**swap branch (disk ↔ bulge label swap)**: when `swap_hint=disk_bulge_swap` (the FAIL consists solely of the {disk, bulge} Re inversion), the fix is the label swap — the VLM should already have proposed it; if omitted, the orchestrator generates it as a B-fill (g ≥ 0.5). This branch generates **no** recovery candidates A/B below (the swap itself is the fix).

**Generation rules for recovery candidates** (the orchestrator executes them in Step d.ii alongside the companion check; for non-swap FAILs):

Identify the **inflated component** from failed_checks (the one whose Re exceeds the component above it in the chain) and generate 1–2 recovery candidates:

**Recovery candidate A (Re-bound tightening + warm start)**:
- `action_id`: `<branch>-<parent>-recovery-rebound`
- `primitives`: `tune(inflated_component, re_max = 0.9 × Re_above)` (written into `.cons`), where `Re_above` = the current fitted Re of the adjacent component above in the chain. If the inflated component is the disk, instead set `disk Re_init = 1.5 × max(subordinate Res)` (push the initial value up rather than cap it). All other parameters warm-start from s''s fitted values.
- `expected_C'`: same as s' (no component changes; bounds only)
- `expected_behavior_tag`: `re_bound_enforce`
- **Floor g ≥ 0.5** (mandatory retention, exempt from the g_min truncation)

**Recovery candidate B (warm start from the most recent PASS state + tightening)**:
- `action_id`: `<branch>-<parent>-recovery-warmstart`
- `primitives`: initialise all components from the **most recent physicality-PASS state** on the current beam-search path, plus candidate A's Re tightening.
- `expected_C'`: same as s'
- `expected_behavior_tag`: `warmstart_rebound`
- **Floor g ≥ 0.5** (mandatory retention)
- Generated only in the round after A (if A still FAILs) — A and B are sequential: A explores first; only after A FAILs does B have a "most recent PASS state" to warm-start from.

**Progressive relaxation (level 3, merged into candidate D of the Lens Re Inflation Trigger Rule; mandatory, not optional)**: if A or B passes but the lens still hits the **tightened** cap with no degeneracy signal (Re_lens/Re_disk < 0.85 and Mag_lens > Mag_disk + 0.2), that situation **is** the trigger scenario of candidate D in the d.ii lens-Re-inflation check (self-imposed provenance: current re_max < original input re_max) — the VLM must produce D (`tune(lens, re_max = hit value × 1.3)`); if the VLM omits it, the orchestrator generates it as "[orchestrator lens-inflation supplement-D]" (g ≥ 0.5). For non-lens components, progressive relaxation still follows the standard `tune(component, re_max += 2px)` action under the 🔑 Bound-Relaxation Rule.

**Traceability**: recovery candidates are annotated "[recovery candidate A/B]" in the relevant branch section, recording the failed_checks list, the re_max set, and the warm-start source. VLM and recovery candidates compete equally; even truncated ones are logged.

**Relation to the VLM**: the protocol does not replace the VLM — Step d.i still runs normally with visual candidates and FAIL repairs (possibly PA fixes, component changes). Recovery candidates are merely one more d.ii rule, parallel to the companion check. Both classes are scored and enqueued together in Step f; the beam search's parallel exploration is fully preserved.

### §Deduplication and Ranking (orchestrator duty; rule-based dedup disabled)

**Semantic dedup criteria** — two candidates (s_i, a_i) and (s_j, a_j) are equivalent when **all three** hold; keep the higher g:
1. The post-action expected inventories `expected_C'` are equivalent in **physical identity** (naming swaps allowed, e.g. "bulge n=0.5 q=0.4" ≡ "bar n=0.5 q=0.4").
2. The expected parameter values agree within tolerance bands: Re ±20% (expdisk compared in effective radius), Sérsic n ±0.5, q (b/a) ±0.1, PA ±10° (**N=+Y contract**, same frame as the feedme `10)` row), mag ±0.5.
3. `expected_behavior_tag` is identical.

**History dedup (graph-search cycle detection, before semantic dedup and six-dimension scoring)** — comparing only within Q is not enough: Q is "to explore", the execution history is "explored"; both must share one signature yardstick. Before scoring, every new candidate undergoes two comparisons (per Step 1.b.0):
1. **vs the input ledger (R1, all actions)**: transcribe the candidate into a hypothetical feedme's canonical form (structure × toggle configuration × `.cons` bound bands × initial-value bands, px) and compare with the executed inputs; equivalent within the bands → discard (rerunning the same input yields no new information; log it).
2. **vs the result ledger (R2, closed-form only)**: remove-only / parameter-revert / bound-restoration candidates project exactly; compare the projected signature with the fitted results' signatures (**zombie-aware**: states differing only by [zombie] components are equivalent); exact hit → the candidate is neither executed nor enqueued (b.0 handles it as a zero-cost rollback); structure-only match → tag "[suspected near-duplicate]" with dimension 4 scored 0.

**Priority score g ∈ [0,1]** — the orchestrator scores every candidate 0–1 on six dimensions and takes the **weighted average** as g. **Non-equal weights: dimension 3 (path diversity) ×2**, the rest ×1, i.e. `g = Σ(wᵢ·sᵢ) / Σwᵢ`; with dimension 6 active, w = [1,1,2,1,1,1], else [1,1,2,1,1]. Rationale: with equal weights, tune chains on one component structure (progressive bound relaxation, same-basin fine-tuning) and context reopenings occupy beam slots consecutively; doubling the diversity weight is a structural filter — repeated directions score low there and are doubly penalised in g. Note: a low diversity score only lowers ranking priority — **it never bans a direction**; the mandatory-retention and persistent-candidate floors are unaffected by the weighting.
1. **Residual-improvement potential**: combine the VLM's σ with the orchestrator's independent estimate of the explainable residual fraction.
2. **Physical-plausibility prior**: consistency with the "Disk → (F1/Companion if detected) → Bulge → Bar → Other" addition order; conformity of Bar/Bulge/Lens/Nucleus with the recognition conditions (see `<Overall workflow of galaxy component analysis>`). **Stage-1 detect_bar_lopsidedness results are only a weak prior here**: a detection may add a little (hint-level positive evidence), but **a non-detection must not deduct** — non-detection is zero evidence, not negative. A Bar/Lens/Fourier candidate grounded in residual evidence (central quadrupole, high-ellipticity inner structure, bar-like residuals, etc.) scores on the **strength of the residual evidence** even if Stage 1 detected nothing; the gold standard is residual-driven fitting validation, not Stage-1 detection.
   **The "hint-level evidence ≠ negative evidence" principle extends to historical path failures**: a candidate X failing in a parent context (rising BIC, bound hits) does **not** constitute determinate evidence that "the hypothesis X is wrong". When the beam search reaches a new component context, the orchestrator **must not** carry over "this direction failed historically" to suppress the physical-plausibility score of a same-direction candidate the VLM re-proposes on residual evidence.
3. **Path diversity (weight ×2)**: the more a candidate's direction differs from the **globally used candidates** (the executed history, i.e. the action genealogy in the input/result ledgers) and from the current Q's elements, the higher the score. Direction criteria: the expected_C' component-set difference primarily; expected_behavior_tag / main tune axis secondarily.
4. **Degeneracy penalty**: is the parent state already degenerate (e.g. a failing `.cons`, identical bulge/disk flux)? Might this action inherit the degeneration? **This dimension assesses whether the candidate itself inherits the parent's degeneration** — a successor-correction candidate (position / PA / Re fixes) of a worse-BIC s' (say, a poorly initialised parameter) is repairing the degeneration and should get a **low** penalty.
5. **Historical consistency**: coherence with the working_note's prior goals — avoid flip-flopping.
6. **BIC threshold**: active only when the action adds/removes a Nucleus/AGN; estimate whether ΔBIC clears the +10 threshold (BIC = `fit_statistics`'s `bic_eff` per the BIC Convention; fall back to `bic1d` if missing).

`score(s)` decides s\* and shares the same dimensions and weights (dimension 3 ×2); it scores a finished fit rather than an enqueued candidate.

**g_min enqueue threshold**: any candidate with **g < 0.3** is discarded outright. Log discards in the "cross-branch decision log" with action_id and "g < 0.3".

**Mandatory-retention clauses (exempt from the g truncation)**: the following candidates must be enqueued even with low g (at least one variant each), because they test physical hypotheses or programmatic-diagnosis repairs that residual intuition alone cannot judge — unexplored means no evidence:

- **Flat-Bulge → Bar candidate**: when the parent has a Bulge satisfying the joint trigger (bulge q < 0.5 AND |bulge_PA − disk_PA| > 20° (N=+Y) AND 0.5 < bulge_n < 2.5 (if free) AND disk q > 0.5), the orchestrator must enqueue the VLM's Bar-direction candidate (`tune(Bulge→Bar)` conversion or `add(Bar)+tune(Bulge, q_min=0.7)` addition, at least one) with a floor of g ≥ 0.5, and **must not depress dimension 2 for "Stage 1 detected no bar"**. The orchestrator writes the four trigger values objectively into local_state_description so the VLM knows the trigger holds. If the VLM returns no Bar candidate while triggered, the orchestrator **proactively generates** an `add(Bar, n=0.5 fixed, PA≈bulge_PA)` candidate (initialised per the "B-fill" rules of §Faithful-Execution Principle), tagged "[orchestrator flat-bulge trigger supplement]", through the normal scoring-and-enqueue flow.
- **Lens candidate**: when the parent has a Bar with `Re_bar ≳ Re_disk` or `q_bar ≳ 0.5`, Lens candidates are likewise floor-enqueued.
- **Physicality-FAIL recovery candidates** (see §Recovery Protocol): on a FAIL, recovery candidates A (Re-bound tightening) and B (warm start + tightening) are floor-enqueued at g ≥ 0.5; the disk_bulge_swap label-swap candidate likewise (VLM's or the orchestrator's fallback).
- **Disk-Re bottleneck candidate** (see d.ii): when the lens/bar Re hits the cap with Re degeneracy (≥0.85) or flux-approaching (≥83% disk), the `tune(disk, larger Re)` candidate is floor-enqueued at g ≥ 0.5.
- **Lens self-imposed-cap relaxation candidate D** (see d.ii): when the lens Re hits a cap produced by an earlier tightening (current re_max < original input re_max) with neither degeneracy sub-criterion met, candidate D (`tune(lens, re_max = hit value × 1.3)`) is floor-enqueued at g ≥ 0.5 (VLM's or the orchestrator's "[orchestrator lens-inflation supplement-D]").
- **Persistent-candidate protection (VLM repeat proposals across rounds)**: if the VLM returned a **same-direction** candidate in the last **≥2 `generate_galfit_beam_actions` calls** (adjacent or not, same branch or not) with σ ≥ 0.5 each time, that direction **must** be enqueued and executed at least once; the orchestrator must not push its g below g_min=0.3 on "historical failure", "consistency" or "diversity" grounds.
  - **"Same direction"**: identical `expected_behavior_tag` and physically equivalent `expected_C'` (parameter tweaks allowed — PA=90° vs 85°, Re_init=1.5px vs 2.0px are the same direction).
  - **On trigger**: force the candidate's g to **≥0.6** (floor); annotate "[persistent-candidate protection triggered]" in working_note with the triggering calls' session_ids and σ values.
  - **Post-execution expiry**: if the candidate then fails (BIC worse, parameters escaping the constraint region), the orchestrator may veto later same-direction candidates **within the same component context** with a written reason (in the "cross-branch decision log"); the veto holds **only for the same C'**. Any context change (a component added or removed) re-arms the protection for new same-direction candidates.
  - **Rationale**: repeated same-direction high-σ proposals across calls are stronger evidence than a single σ — one call may misjudge, repeated appearances across contexts mean the residual feature is stable. Suppressing them puts the orchestrator's prior above the VLM's visual evidence, against the beam search's design.

### §Generation Spec for global_state_description / local_state_description (orchestrator duty; hard requirement)

The `generate_galfit_beam_actions` VLM is **stateless**: each call sees only the current round's residual image. Cross-round memory rides on two parameters, both written by the orchestrator —

- **`global_state_description` (global state)**: a **distillation** of cross-round stable facts (not the full working_note!). Fixed schema, fixed field order, ≤ ~50 lines total:
  ```
  [Meta] Pixel contract: every Re/position in this file is px, in the same reference frame as the VLM's panels and the feedme parameter rows — directly diffable and directly writable (the orchestrator fills values verbatim, no unit conversion); arcsec is forbidden.
        PSF: FWHM=… px, A_psf=… px² (measured once from the PSF image before the first fit; the VLM uses A_psf for the companion psf-vs-sersic selection rule, beam prompt C3)
  [Stage-1 conclusions] bar/lop detection; PA (N=+Y convention: +Y up = 0° counterclockwise, directly usable in the feedme 10) row); b/a
  [State ledger] one line per fitted state (the VLM must compare expected_C' landed signatures line by line before generating each candidate):
      | round | state signature (px) | BIC_eff | verdict | notes |
      | A.4 | {disk:Rs6.5(Re10.9),M16.2; bulge:n4f,Re1.2px,M18.7; comp:px(95,128),Re0.5px} | 23499 | PASS | comp Re at lower bound |
      Signature format: component:type,n-state (f/free+value),Re(px),Mag,q,PA; collapsed components (flux fraction <0.5%) tagged [zombie]
  [Rollback edges] confirmed equivalences of closed-form transitions; a hit means zero information:
      e.g. A.5 --remove(bar)--> ≡A.4; A.11 ≡A.10+[zombie bulge] (the bulge collapsed; content identical to A.10)
  [Verified basins] px values + source rounds + evidence tier ([data-verified]/[unverified]).
      e.g. companion ≈ px(95,128), r≈33px, 1D spike r≈33px co-located; anchored in A.4/A.6/A.8 [data-verified]
  [Refuted hypotheses] all five fields mandatory: direction | context signature | quantitative evidence | failure reason | reopening condition.
      e.g. bar+bulge coexistence | {disk,bulge,bar,comp} | ΔBIC_eff +15.5/+67.5 (A.5/A.11) | the two components degenerate at Re≈1.7px, fighting for flux | the degeneracy disappears
  [Budget] n = X / N_max, Y left
  ```
  Maintenance rules — refresh at each round's Step g:
  - `[State ledger]`: append a line per successful fit; tag components with flux < 0.5% of the brightest `[zombie]` (**relative flux criterion; absolute magnitudes forbidden**); when a component combination's **4th** attempt completes, mark its lines `[combo-exhausted]` in the notes column (the per-combination attempt cap — the VLM must not generate further candidates on that inventory; see the formal definitions).
  - `[Rollback edges]`: append on an R2 exact hit, or when a result is zombie-equivalent to a ledger line.
  - `[Verified basins]`: new entry only when the fit produced physical values without bound hits (collapsed/bound-hit/drifted ones don't count).
  - `[Refuted hypotheses]`: new entry when the BIC worsened by ≥ 10 in the same context or the physicality FAILED — **with ΔBIC/bound-hit numbers + failure reason + reopening condition**.
- **`local_state_description` (current-round supplement)**: the round's objective description, containing:
  1. The parent inventory C and key parameter summary;
  2. **The concrete problems of the current fit** (the most important part; objective and thorough):
     - bound-hit parameters (⚠️ with values, **in px**, e.g. `bar_Re=30px ⚠️ at the cap`; bounds come from `.cons` — unbounded parameters cannot hit);
     - unfitted residual features (position / symmetry / strength, quoting the Stage-1 visual wording);
     - identity confusion (disk/bulge label swap, a bar gone round and fat, a bulge collapsed to a point source);
     - **flat-Bulge → Bar trigger values** (when the parent has a Bulge): list `bulge q`, `|bulge_PA − disk_PA|` (N=+Y), `bulge_n`, `disk q` objectively and state whether the joint condition holds — numbers only, no direction hints;
     - **disk-Re bottleneck signal** (when the parent has lens/bar): lens/bar `Re`/`re_max`/bound-hit status + disk `Re`/`re_max`/bound-hit status; on a hit, mark "disk-Re bottleneck hit" — numbers only;
     - **outer residual sign** (1D curve at r > 2×Re_disk): "Data brighter than Model" / "Model brighter than Data" / "flat", quoting the Stage-1 wording;
  3. The orchestrator's numeric-rule delegations (companion condition-A numbers / lens Re inflation signal / other d.ii quantitative signals).

**Shared prohibitions (candidate-direction suggestions)**:
- ❌ no "priority repair directions: (1)...(2)...(3)..." lists;
- ❌ no hinting at or recommending specific action types ("suggest releasing the disk n", "suggest adding a Lens", "suggest rolling back to A.2", "suggest tightening the bar Re cap");
- ❌ no premature direction convergence or filtering — that is the VLM's job (via prompt rules + scoring).
- ⚠️ `[Refuted hypotheses]` in `global_state_description` is a **fact record**, not a direction suggestion — recording "X was refuted (ΔBIC=+402)" is legitimate; smuggling in "therefore go direction Y" is not.

**Handling the Physicality Verdict**: the verdict authority belongs to the VLM — verdict / failed_checks / swap_hint are output by the VLM; the orchestrator only parses, records and gates, never rewrites; the orchestrator likewise **must not** preview its own expected verdict in local_state_description (e.g. "this round should FAIL") to avoid steering the VLM.

### §Faithful-Execution Principle for Candidate Actions (orchestrator duty; hard requirement)

Each candidate returned by `generate_galfit_beam_actions` declares primitives (e.g. `add(Bulge, n=4 fixed, [Re_min, Re_init, Re_max]=[4,6,10]px, q=0.9, PA=135°)`, `tune(bulge, n=0.5 fixed)`, `tune(disk, +F1)`). When transcribing candidates into `_iter{n}.feedme` parameter rows, the orchestrator must strictly separate two field classes:

**Class A: fields that must not be modified (the candidate's semantic core)**
- Component type (the `0)` line: `sersic` / `expdisk` / `edgedisk` / `psf` etc.)
- The `# STRUCTURE:` semantic name (the target's name; new components are named by physical type disk/bulge/bar/lens/companion/agn)
- Physical parameters' free/fixed state and target values (e.g. `n=4 fixed`, `n=0.5 fixed`, `n free`, `q fixed=0.33`)
- Magnitude constraints (e.g. `q>=0.5`, `Re<=30px`, `Re_max=20px` — written into `.cons`)
- Component additions/removals (`add` / `remove`) and the target name
- The centre-constraint strategy (`.cons` offset chain / free / fixed coordinates)
- The Fourier mode order (F1) and whether it is enabled
- The atomic operations' bundling (a candidate's 1–2 primitives execute as one whole; splitting is forbidden)

**Class B: fields the orchestrator may fill (when the VLM gave no precise value)**
- **Parameters not declared by the primitives (default warm start, per Step 1.b.1)**: the row's initial value takes the parent galfit.NN converged value (toggle from the parent round); reusing the parent feedme's old input values is forbidden — the VLM diagnosed the parent converged solution, and unmentioned means "acceptable"; the child must start from the converged solution to execute the VLM's judgement faithfully.
- Parameters declared but without precise values: pick numbers near the parent's **fitted** value (not the old input) — e.g. with a parent fitted Re=4.6px, the child's Re_init stays within 4–5px; order-of-magnitude deviations are forbidden.
- The `.cons` lines' concrete bound numbers (use the VLM's Re triplet directly; place by the declared magnitude only when a constraint is declared without precise values).
- **Re triplet written directly (galfit has no conversion)**: a Re in a VLM candidate is a **pixel value** (even a stray `"` mark means pixels — the VLM reads a pixel grid); the feedme `4)` row is pixels too — write the triplet's **Re_init verbatim into the `4)` row** and `Re_min`/`Re_max` into the `.cons` `re` line (`<number>  re  <Re_min>  <Re_max>`). **Sole exception**: for an expdisk (Disk) target, the `4)` row takes the scale length `Rs = Re / 1.68` (the triplet refers to the effective radius). **Any arcsec conversion is forbidden; calling unit-conversion tools is forbidden.**
- **Companion / component centre coordinates written directly**: coordinates in a VLM candidate (e.g. `tune(companion, x=115, y=130)`) are **pixel coordinates** on the comparison image, in the same frame as the feedme `1)` row — the orchestrator fills them **verbatim**. **The converted coordinate is only an initial estimate** — VLM pixel readings can be off by ±10–20 px (the dirtier the parent residuals, the larger the error); keep companion positions free (`1 1`) with a `.cons` ±5px soft window (see Step 1.b.2). Fixing them is considered only in later rounds after the position has been calibrated.
- **Adding an expdisk (add(Disk) or a sersic→expdisk switch)**: the candidate's Re triplet is the effective radius; the `4)` row takes `Rs = Re_init / 1.68`, and the `.cons` re bounds are likewise placed in Rs.
- Other non-companion centres (e.g. an added Bulge's x/y): usually the parent fitted value (concentric) or the main-galaxy centre pixel — not the VLM's rough value.

**Core constraint**: if the orchestrator deems a Class-A field flawed (physically unreasonable, conflicting with the parent, repeating a failed direction), it **must discard the whole candidate** (logged in the "cross-branch decision log" with action_id, reason, and the offending Class-A field); **executing after modifying Class-A fields is absolutely forbidden**. This keeps every fit attributable to a VLM-proposed direction — supporting or refuting it, never a "distorted execution" sample.

**Legitimate adjustment range of Class B**: even for Class-B fills, the orchestrator must not alter the candidate's physical intent. If the VLM proposes `add(Nucleus, Re_init=1px)`, the orchestrator may set Re_init to 0.8px or 1.2px (same order) but not 0.3px (three times off — turning an "extended core" into a "compact point source").

**Canonical counter-examples (strictly forbidden)**:
- The VLM proposes `add(Nucleus, n=4 fixed, Re_init=1px)`; the orchestrator executes `add(Nucleus, n=2 free, Re_init=0.3px)` — violating Class A (n value/state) and Class B's range at once
- The VLM proposes `tune(bulge, n=0.5 fixed)` (converting the bulge to a Bar); the orchestrator executes `tune(bulge, n free)` — looser-looking, but a different candidate was actually run and the original proposal untested
- The VLM proposes `add(Bulge, q>=0.5)`; the orchestrator strips the q floor and executes — turning "a round bulge" into "an arbitrary-ellipticity bulge"

**Correct practice**: if the orchestrator thinks the VLM's n=4 is unreasonable, discard the candidate and log "discarded: Nucleus n=4 conflicts with the pseudo-bulge prior"; to test n=2, submit it as a new orchestrator candidate through the full scoring flow — never piggyback on a VLM candidate.

### §Multi-Branch working_note.md Template (the agent must maintain this structure)

```markdown
# Galaxy {ID} Beam Search Working Note (GALFIT single-band)

## Basic information
- Galaxy ID / coordinates / fitting region (the feedme H) item) / band
- Beam width W = 5; global budget N_max = 15
- PA convention: N=+Y (+Y up = 0° counterclockwise, same frame as the feedme 10) row); unit contract: pixels only
- PSF: FWHM = … px, A_psf = … px² (from check_feedme_file before the first fit; feeds the default Re floor and the companion psf-vs-sersic rule)
- Stage-1 conclusions (VLM morphology classification, bar/lop detection, PA, b/a)

## Beam-state snapshot (overwrite this section after each main-loop iteration; do not append)
### Current best s*
- Branch / round: <branch>.<local_round>   e.g. A.3
- Component inventory C*: {...} (# STRUCTURE names)
- reduced_χ² / BIC: ... / ... (chisq1d_nu / BIC_eff; see the BIC Convention)
- VLM physicality verdict: PASS
- Corresponding archives directory: archives/<timestamp>.<hash>/
- Corresponding feedme: _iter{global_iter_id}.feedme

### Current priority queue Q (by g descending, at most 5 entries)
| rank | branch | parent round | action summary | σ | g |
|---|---|---|---|---|---|
| 1 | A | A.2 | +Nucleus (compact, psf) | 0.55 | 0.78 |
| 2 | B | B.1 | release bulge_n | 0.35 | 0.62 |
| ... | | | | | |

### Global fit counters
- n = X / 15
- stagnation = Y / 15
- global_iter_id = Z (the next feedme suffix)

## State ledgers (graph-search visited set; append one line per successful fit; never overwrite)
### Input ledger (canonical forms of the executed feedmes; check_feedme_file output)
| round | input signature (structure × toggles × .cons bound bands × initial-value bands, px) | _iter{n}.feedme |
|---|---|---|
| A.4 | {disk:Rs6.5(Re10.9); bulge:n4f,Re~1.2px; comp:px(95,128) window ±5px} | _iter4.feedme |

### Result ledger (fitted-state signatures + outcomes)
| round | state signature (px) | BIC_eff | verdict | zombie/bounds |
|---|---|---|---|---|
| A.4 | {disk:Rs6.5(Re10.9),M16.2; bulge:n4f,Re1.2px,M18.7; comp:px(95,128),Re0.5px} | 23499.2 | PASS | comp Re at lower bound |
| A.11 | {disk,bar,comp, bulge:[zombie]} | 23527.2 | FAIL | bulge M24 collapsed |

### Rollback edges (closed-form equivalences; a hit means zero information)
- A.5 --remove(bar)--> ≡A.4
- A.11 ≡A.10+[zombie bulge]

## Branch A: <theme, e.g. "Disk+Bulge mainline">
### A.1 (fit #1, feedme: _iter1.feedme)
- Configuration / tool calls (incl. the .cons summary) / components / C, P summary / reduced_χ² / BIC
- VLM physicality verdict: PASS/FAIL (failed_checks summary; paste evidence on FAIL)
- VLM residual-feature summary (Phase 1 of generate_galfit_beam_actions)
- The action_ids returned by generate_galfit_beam_actions and their enqueue fate (log the truncated too)
### A.2 ...

## Branch B: <theme>
### B.1 (fit #N, feedme: _iter{global_iter_id}.feedme) ...

## Branch: failure archive
### (fit #M, parent = <branch>.<round>, failed)
- Action: <action_id or description>
- Failure reason: <tool error / check_feedme_file rejection / no fit output etc.>
- Disposition: add to the parent state's taboo set

## Cross-branch decision log (append; never overwrite)
- fit #X: derived branch B (reason: A degenerated at R2; explore the early Bar direction)
- fit #Y: merged A.3 and B.2 (semantically equivalent, bulge n=0.5 → bar)
- Termination: <condition>, cumulative n=..., branches explored=...
```

**Naming rules**:
- Branch ids: capital letters in derivation order (A, B, C…); the initial branch is A.
- Rounds within a branch: `<branch>.<local_round>` (A.1, A.2, B.1…); local_round increments only within the branch.
- Feedme filenames: the global `_iter{n}.feedme` (n = global_iter_id) to avoid collisions; branch membership rides on the working_note index, not the filename. Constraint files `iter{n}.cons` share the feedme's directory and number.
- `archives/` subdirectories: keep the existing `<timestamp>.<hash>/` naming.

### Agent execution guidelines (against context overflow)
1. **Persist state in `working_note.md`; do not rely on context memory**: Q's contents, s\*, n and stagnation all take the working_note's "beam-state snapshot" as the single source of truth; read that section before every decision.
2. **Keep only the current round in context**: the dequeued (s, a) and the freshly generated candidates; flush them to disk and clear them once handled.
3. **Prefer overwriting to appending**: the beam snapshot is overwritten each iteration; only branch sections and the cross-branch log append.

### Step 4. Physical-meaning analysis and Occam's razor (after the beam search terminates)
- Physical-meaning analysis: following the `<Physical-meaning analysis of galaxy components and strategy>` section strictly, re-examine every component of s\* parameter by parameter. On any non-physical case (a Bulge with Re < 0.2 px forced as Sersic, a Bar whose PA (N=+Y) clearly conflicts with the image), **restart one beam-search round** with "repair the non-physical component" injected as a hard constraint into `generate_galfit_beam_actions`'s `local_state_description` (reset Q and stagnation; keep n and global_iter_id). For a Bulge Re in the 0.2–0.5 px border zone, explore the Sersic and psf paths in competition within the beam search — adopt the psf only if its 2D residuals are clearly better; otherwise keep the Sersic. A Bulge Re < 0.2 px (collapsed to a point source) must be replaced by the psf type (AGN-compensated Bulge physics takes priority).
- Occam's razor:
  - **Nucleus/AGN**: if s\* has a Nucleus with ΔBIC < 10 (BIC_eff; see the BIC Convention), restart the beam search with `remove(Nucleus)` as the highest-priority candidate for validation; keep the Nucleus if the BIC worsens on removal.
  - **Companion**: if s\* has a Companion with flux ratio ≤ 1% (condition A, computed as in d.ii), write the numbers as hard context into `generate_galfit_beam_actions`'s `local_state_description` (same format as d.ii) and let the VLM run condition-B visual verification (is there a visible blob at the companion position in the original panel?). Only when A∧B both hold, restart the beam search with `remove(Companion)` as the top-priority candidate for validation. With a visible blob (B fails), no removal.
  - **F1 component**: an F1 amplitude > 0.02 is physically meaningful; removal on BIC grounds is forbidden; only validate removal through a beam-search restart when the fit diverges or parameters are non-physical.
- The cumulative n of these restarts stays under the N_max = 15 budget; if exhausted, Stage 3 decides acceptability.

Stage 3. Science-goal calibration and report writing
* **Lock the best result**: read the optimal round's `archives/` subdirectory and `_iter{n}.feedme` from the "beam-state snapshot / current best s\*" section of `working_note.md` — the sole source for all analysis in this stage. Give its morphological physical interpretation (e.g. component A is a classic disk; component B a compact nuclear star cluster).
* **Before formally locking, the subagent `best-round-verifier` must be called** to audit the candidate round independently; on **FAIL locking is strictly forbidden** — fix per its "blocking issues" and re-audit to PASS; only PASS (WARN allowed) may lock.
* Science-goal calibration: the science goal cares about lopsidedness. If the best result has no Fourier (F1) component, call fourier_mode_analysis on the residual image to judge whether a Fourier mode is needed for the lopsided asymmetric residuals. Skip this step if a Fourier component is already present.
    - Only the first-order Fourier mode may be used, and only on the Disk component or the single-Sersic model (when there is no Disk) — implemented by appending an `F1) <amp> <phase> 1 1` parameter row before that component's `Z)` line.
    - An F1 amplitude > 0.02 with no degradation in fit quality may be kept; update the best round to the latest F1-bearing round. Otherwise abandon F1 and keep the previous best round. If fourier_mode_analysis recommends adding, return to Stage 2 and restart one beam-search round with "+F1" injected as a hard constraint into `generate_galfit_beam_actions`'s `local_state_description` (reset Q and stagnation; keep n and global_iter_id).
* Report writing: use the `write_file` tool to write the conclusions into the current galaxy directory as `analysis_report_xxx.md`.
* **The report contains:**
    * **Generation time**: date and time.
    * **Preprocessing information**: mask notes and the background-setting rationale.
    * **Iteration log**: organised from the multi-branch structure of `working_note.md` — per branch (A/B/C…), the evidence for each round's component additions/removals (multimodal visual judgement records), parameter divergences and rollbacks, the beam-truncated candidate action_ids, cross-branch decisions and semantic-dedup merges.
    * **Best-result locking analysis and result**: the locking rationale, the final adopted parameter table, and the physical interpretation.
    * **Attachment index**: the best round's directory path, the final `feedme`, and the final comparison image (original/model/residual) path.
    * **JSON output**: format the round information at the end of the document for rule extraction and automation:
      ```json
      {"best_turn":"<best round's directory name>","components":["<physical components in the best round>"],"galaxy_type":"edge-on/face-on/elliptical"}
      ```
      best_turn is the best round's subdirectory name under archives/ (e.g. 20260414T093323.c1993a48).
      Physical component types: [Disk,Bulge,Bar,Nucleus,Companion,Fourier,SingleSersic,Lens]
      galaxy_type: one of edge-on / face-on / elliptical; a disk galaxy with Disk q < 0.3 counts as edge-on.


## Galaxy to analyse

{argument}
