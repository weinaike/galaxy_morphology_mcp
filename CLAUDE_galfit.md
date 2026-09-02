


# Methodology guide for galaxy component analysis
@src/prompts/residual_analysis_message.md
---

## PA-convention override clause (galfit mode only; takes precedence over the PA convention in the file referenced above)
The referenced `residual_analysis_message.md` targets the multi-band GalfitS flow: its "PA convention" requires sky-PA aligned with the compass. **The single-band galfit mode does not apply that clause**: this mode adopts the **N=+Y convention** — the image's North direction is assumed to coincide with the +Y axis, and **PA is always read and written as "0° = the image's +Y axis (up), increasing counterclockwise"** (read PA against the image's vertical axis, not the compass), numerically identical to the feedme `10)` parameter row (GALFIT's "+Y axis = 0°"); **the orchestrator writes the VLM's PA verbatim into the `10)` row with no conversion**. `detect_bar_lopsidedness`'s `bar.pa_deg` / `lopsidedness.phase_deg` can be used directly under this convention.

## Unit contract (galfit mode only)
All Re / positions / sizes in this mode are **pixels (px)**: a px value read by the VLM off a comparison panel shares the unit and reference frame of the feedme parameter rows and is **written verbatim — any conversion is forbidden** (the expdisk conversion Rs = Re/1.68 is the sole exception: Re values given by the VLM always mean the effective radius, converted by the orchestrator when writing the `4)` row). Arcsec must never appear anywhere in the flow, and calling any unit-conversion tool is forbidden.

## Solution space definition (single source of truth)

This section is the authoritative definition of the solution space for the single-band GALFIT flow: which components a fitted model may contain, the multiplicity rules, and the hard limits on component parameters. The workflow (`workflow_galfit.md`), the VLM candidate prompt (`beam_action_generation_prompt_galfit.md`) and the best-round verifier all reference this section; on any wording conflict, this section wins.

### 1. Allowed component inventory and multiplicity

| Slot | feedme type | Multiplicity | Admission condition |
|---|---|---|---|
| Disk | `expdisk` | ≤ 1 | any disk galaxy (the default assumption) |
| Edge-on Disk | `edgedisk` | ≤ 1; mutually exclusive with Disk (replaces it, and takes over its slot in the concentric anchor and the Re chain) | galaxy is edge-on (single-sersic b/a ≲ 0.17 with a dust lane / disk thickness) |
| Bulge | `sersic` | ≤ 1 | central compact round component |
| Bar | `sersic` (n = 0.5 fixed) | ≤ 1 | bar signatures (linear / X-shaped quadrupole residuals) |
| Lens | `sersic` (n < 0.5, q > 0.5) | ≤ 1 | 1D-profile bump path or bar-anomaly path |
| OuterDisk (envelope) | 2nd `expdisk` or `sersic` (n < 1) | ≤ 1 | outskirts still unfitted after the Disk is established (broad positive 1D residual at r > 2·Re_disk); must satisfy Re_outer > Re_disk; sits **above** the central Re chain, it does not displace it |
| AGN / Nucleus | `psf` | ≤ 1 | Bulge Re collapsed < 0.2 px (mandatory replacement); 0.2–0.5 px border zone as a competing variant |
| F1 (m=1 Fourier) | appended `F1)` row | ≤ 1 | lopsidedness; only on the Disk / edgedisk (or the single Sersic when no Disk exists); amplitude > 0.02 to retain |
| Companion | `sersic` or `psf` | 0..N | compact positive-residual blob with an original-image counterpart; \|ΔMag\| ≤ 5 vs the main galaxy; centre always free (never in the concentric chain, never in the Re chain). **Profile-type selection** (beam prompt C3, area rule): `psf` when the blob is unresolved (A_blob ≤ 1.5·A_psf, not elongated), `sersic` when resolved (A_blob ≥ 2.3·A_psf or elongated major/minor ≳ 1.3); border 1.5–2.3 → `psf` default. A `sersic` companion collapsing to Re < 0.2 px switches to `psf` |
| Sky | `sky` | exactly 1 | **never fitted, never a search dimension**: the sky block (value + toggle) is the manually provided setting of the input feedme and is carried **verbatim** into every `_iter{n}.feedme` — never backfilled from converged values, never freed or re-tuned (candidates touching the sky are invalid) |
| Single Sersic | `sersic` (n free), named `singlesersic` | exactly 1 and nothing else | elliptical terminal state; if it fits q < 0.5, the classification must be revisited (a disk is implied). **Transitions**: single→multi — the first `add(<central component>)` MUST bundle the conversion `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68)` (the `disk` name is reserved for the expdisk slot; a multi-component model with a sersic `disk` or a lingering `singlesersic` is invalid — `check_feedme_file` rejects it); multi→single — remove-only transitions leaving one central component may revert `disk → singlesersic` (n free) |

### 2. Per-component hard parameter limits

| Parameter | Rule |
|---|---|
| Re total order | `re_disk > re_lens > re_bar > re_bulge` (existing central components only, strict decrease; OuterDisk sits above re_disk; edgedisk plays the Disk slot) |
| n (Sérsic) | Disk: none (expdisk, n ≡ 1 by type). Bulge: fixed 4 at depth ≤ 2, free within 0.5–8 at depth ≥ 3. Bar: fixed 0.5, never released. Lens: free, prior < 0.5 (default bounds 0.1–0.6). OuterDisk (sersic variant): free, < 1. Single Sersic: free (overall-concentration observable) |
| q (b/a) | Disk/edgedisk: free — oblique disk configurations are legal search directions (the expdisk template's default q/PA toggles `0` are NOT used in this workflow; always set `1`). Bulge: prior > 0.5. Bar: prior < 0.4. Lens: prior > 0.5. Every shaped component: default bounds 0.05–1.0 |
| PA | free for every shaped component; N=+Y convention; bar/lens/disk initial values must come from measured feature directions |
| Mag | no default bounds (flux reallocation is part of the search); companions within 5 mag of the main galaxy; the zombie threshold (flux < 0.5% of the brightest component) is a dedup criterion only, never a removal ground by itself |
| Centre (x, y) | K ≥ 2 main-galaxy central components (Disk/edgedisk/Bulge/Bar/Lens/AGN): chained `x,y offset` constraint (anchor toggle `1 1`; GALFIT rewrites subordinates to `2 2`). K ≤ 1: free with a ±2 px default window. Companions: always free with a ±5 px soft window at insertion |
| Re floors | Bulge Re < 0.2 px → must become `psf`; 0.2–0.5 px → psf competing variant; every shaped component's Re lower bound defaults to `max(0.1, 0.5 × PSF FWHM)` px |
| F1 | only on the Disk / edgedisk / single Sersic; keep when amplitude > 0.02 and the fit does not degrade |

### 3. Mandatory default bound set (every round, merged into `iter{n}.cons`)

GALFIT feedme parameter rows carry no bounds — without a `.cons` the solution space is open and the bound-hit diagnostics are blind (unlike the GalfitS five-tuple, which bounds every parameter by construction). The orchestrator therefore writes the following default bounds for **every non-sky component, every round**, merged into the same `iter{n}.cons` as the concentric chain (GALFIT loads exactly one constraint file):

```text
# Default bounds for component <N> (every non-sky component, every round)
  <N>   re   max(0.1, 0.5*FWHM_PSF)   <half the fit-region side length>   # expdisk: the row bounds Rs
  <N>   n    0.1   8                  # sersic components only
  <N>   q    0.05  1.0                # shaped components only
# centres: K>=2 central components -> offset chain (no separate window);
#          K<=1 -> <N> x <init-2> <init+2> and <N> y <init-2> <init+2>
#          companions -> <N> x <init-5> <init+5> and <N> y <init-5> <init+5>
```

- FWHM_PSF comes from `fit_statistics.psf_fwhm` (fall back to 1 px when unavailable); expdisk rows are written in Rs (= declared Re / 1.68).
- **Provenance convention**: these default bounds count as **original** in the bound-hit provenance reporting; candidate-driven tightenings count as **self-imposed**.
- Candidate Re triplets may tighten `re` beyond the defaults; they may not widen past the default cap unless the candidate explicitly declares that intent.
- Standing exemptions (consistent with the bound-relaxation rule): the q upper bound (1.0 is the domain edge) and hard physical priors (Bar n = 0.5) are not relaxable bound-hits.

## Adding constraints
GALFIT's parameter-constraint file (usually suffixed `.cons`) is the central tool for curing unbalanced component flux allocation and runaway parameters.

### 1. Enabling constraints in the `feedme` file

Near the top of the main input file (`feedme`) there is an entry dedicated to the constraint file:

```text
G) galaxy.cons      # Parameter constraint file (empty string)
```

Simply put your constraint file's name (e.g. `galaxy.cons`) into the `G)` item. If no constraints are needed, leave it empty or write `none`.

---
### 2. Basic syntax of the `.cons` file

Each line of the constraint file is one rule. Its standard syntax:
`[component number]   [parameter name]   [constraint type]   [lower limit]   [upper limit]`

#### 1. Common parameter-name abbreviations

Inside a `.cons` file, parameters must use these English abbreviations:

* Position coordinates: `x`, `y` (usually written together `x,y`) — constrain them together (constraining x or y alone is not allowed)
* Integrated magnitude: `mag`
* Effective radius: `re` (Sérsic) / `rs` (exponential disk) / `fwhm` (Gaussian/Moffat)
* Sérsic index: `n`
* Axis ratio: `q` (b/a)
* Position angle: `pa`

```text
# Component/    parameter   constraint    Comment
# operation (see below)   range

  3_2_1_9        x          offset      # Hard constraint: Constrains the
  3_2_1_9        y          offset      # x,y parameter for components 3, 2,
                                        # 1, and 9 to have RELATIVE positions
                                        # defined by the initial parameter file.

  1_5_3_2       re          ratio       # Hard constraint: similar to above
                                        # except constrain the Re parameters
                                        # by their ratio, as defined by the
                                        # initial parameter file.

    3           n           0.7 to 5    # Soft constraint: Constrains the
                                        # sersic index n to within values
                                        # from 0.7 to 5.

    2           x           -1  0.5     # Soft constraint: Constrains
                                        # x-position of component
                                        # 2 to within +0.5 and -1 of the
                                        # >>INPUT<< value.

    3-7         mag         -0.5 3      # Soft constraint:  The magnitude
                                        # of component 7 is constrained to
                                        # be WITHIN a range -0.5 mag brighter
                                        # than component 3, 3 magnitudes
                                        # fainter.

    3/5         re          1  3        # Soft constraint:  Couples components
                                        # 3 and 5 Re or Rs ratio to be greater
                                        # than 1, but less than 3.

# Note on parameter column:
#   The parameter name options are x, y, mag, re (or rs -- it doesn't matter),
#   n, alpha, beta, gamma, pa, q, c, f1a (Fourier amplitude), f1p (Fourier
#   phase angle), f2a, f2p, r5 (coordinate rotation), etc., .  Or
#   alternatively, one can specify the parameter number instead (for the
#   classical parameters only) corresponding to the same numbers in the
#   galfit input file.
```


## Concentric constraint for main-galaxy central components (mandatory default, not optional) — `.cons` authoring specification

The four main-galaxy central component types Disk, Bar, Bulge, Lens **must be concentric**: as soon as the feedme contains **≥ 2** main-galaxy central components (`# STRUCTURE:` names disk/bulge/bar/lens), they must be bound to a common centre via the `.cons` constraint file — a default hard constraint, not an on-demand option.

### 1. The only effective syntax: the chained hard constraint `offset` (empirically verified)

Chain the anchor's and all subordinate central components' GALFIT numbers with `_` and write the **paired** lines (x and y must be bound together; **binding only one is strictly forbidden**). With the anchor (Disk) numbered D and subordinates K1, K2…:

```text
# Main-galaxy concentric constraint (anchor = 1 disk, subordinates = 2 bulge, 3 bar, 4 lens)
D_K1_K2_K3   x   offset
D_K1_K2_K3   y   offset
```

Concrete example (feedme: 1=disk, 2=bulge, 3=bar, 4=lens, 5=companion, 6=sky):

```text
# Concentric constraint: bulge/bar/lens anchored to disk (comp 1)
 1_2_3_4     x     offset
 1_2_3_4     y     offset
# Companion position pinned to initial estimate (soft ±5px window)
 5           x     122.5  123.5
 5           y     130.8  131.8
# (optional) other parameter bounds merged into the same file, e.g. the bulge n range
 2           n     0.5  8
```

**Accompanying feedme-side operations**:
- The anchor's (Disk) `1)` toggle **stays `1 1` free** (recommended) — once the constraint takes effect the **group translates jointly** and the centre is optimised by the fitter; the anchor may alternatively be fixed `0 0` (initial value = the parent round's converged centre), pinning the whole group at that coordinate.
- Subordinates' `1)` toggles stay `1 1` — GALFIT rewrites them to `2 2` (the constrained marker) on loading; no manual edit is needed.
- The feedme `G)` item points at the constraint file (e.g. `G) iter3.cons`); beam rounds name it `iter{n}.cons`, sharing the directory and number of `_iter{n}.feedme`. **GALFIT loads exactly one constraint file** — all other bounds (re/mag/n ranges, companion position windows) must be merged into that single `.cons`.

### 2. Verification markers that the constraint took effect (check every round)

- In the fitted `galfit.NN`, chained subordinates' `1)` toggles read **`2 2`** (GALFIT's constrained marker), and all chained components' x,y values are **exactly identical**.
- If a toggle is still `1 1` or the centres differ → the constraint **did not take effect** (usually a syntax error silently ignored); re-check the `.cons` syntax — never enter a broken round into the ledger.

### 3. Two empirically ineffective syntaxes are strictly forbidden (silent failure, no error)

The following plausible-looking pairwise forms were **tested on this machine's GALFIT and do not take effect** (GALFIT raises no parse error, yet the component centres each drift ~0.5 px — the constraint is ignored entirely; the historical cons.con in `gadotti-gt/Plate0270_MJD51909_Fiber095_r` uses the first form, and its galfit.05 centre drift is the evidence):

```text
# ✗ Invalid form 1: space-separated pairwise soft constraint — silently ignored, forbidden
 1  2  x  0.0 0.0
 1  2  y  0.0 0.0
# ✗ Invalid form 2: dash-pairwise soft constraint — equally ignored for x/y, forbidden
 1-2  x  0.0 0.0
 1-2  y  0.0 0.0
```

The only reliable form is the **chained hard constraint `offset`** above.

### 4. Companion exemption (mandatory)

A companion's number (`# STRUCTURE:` name containing comp/companion/secondary/satellite) is **strictly forbidden** in the concentric chain — companion centres must stay freely fitted. When adding a companion, pin its position with a **soft window** instead (±5px; the initial value is the VLM's measured pixel coordinate): `<number>  x  <init-5>  <init+5>` and `<number>  y  <init-5>  <init+5>`. Anchor choice: prefer the Disk; absent a Disk, the brightest central component.


## GALFIT component-type specification (must be strictly observed)
@src/prompts/component_specification_galfit.md


## GALFIT execution rules
- To run a GALFIT optimisation you must use the run_galfit tool in galmcp; executing the galfit command line directly via bash is not allowed. run_galfit automatically handles downstream analysis steps (residual-image generation, parameter parsing, etc.) — calling galfit directly can break the subsequent flow.



# Working-note authoring specification (Beam Search multi-branch edition)

- The working note is the core record of the whole beam search and its **single source of truth**: the priority queue Q's contents, the current best s\*, the counters n / stagnation / global_iter_id, and the state ledgers (input ledger + result ledger + rollback edges) are all governed by it — read it before every decision.
- **Its structure must strictly follow the §Multi-Branch working_note Template of `workflow_galfit.md`** (header with basic information + beam-state snapshot [overwritten] + state ledgers [appended] + branch sections [appended] + failure archive + cross-branch decision log [appended]).
- Header [mandatory]: the Stage-1 VLM morphology judgement (equivalent to the Round-0 original-image component prediction; the high-probability components must be stated explicitly) and the `detect_bar_lopsidedness` conclusions (bar/lop detected or not, PA (N=+Y convention), b/a; initial guesses only — the fit is the arbiter. An undetected component must be written as "not detected (zero evidence, non-determinative)").
- Each branch-round section [mandatory]:
  - the round's action (action_id and primitives summary) and the `_iter{n}.feedme` / `iter{n}.cons` used;
  - post-fit component types and key parameters (position px, magnitudes, sizes, shape parameters; expdisk annotated with Rs and effective radius Re), reduced_χ² (chisq1d_nu) / BIC (BIC_eff);
  - **the VLM Physicality Verdict (verdict / failed_checks summary; record verbatim, never rewrite)**;
  - the candidate action_ids returned by `generate_galfit_beam_actions` and their enqueue/truncation fate;
  - [mandatory] the deviation from the expected goal.
- Prefer overwriting to appending: the beam-state snapshot is overwritten after every main-loop iteration; only branch sections and the cross-branch decision log append.

# Criteria for locking the best round
- Component criterion: image and residual observations suggest full identification — every existing component has been added.
- Fit criterion: the 1D profile residual (DATA−MODEL) shows no obvious spikes or systematic offsets, and the 2D residual map shows no obvious symmetric residuals.
- Physics criterion: the relations among the final fitted parameters are physically meaningful.
- Parameter criterion: all unnecessary constraints have been released and all necessary ones applied.
  - With multiple components the Disk uses expdisk; with a single component, Sersic.
  - The Bar's n is fixed at 0.5.
  - All main-galaxy components' x,y positions are constrained as offset, ensuring concentricity.
  - Other parameters (Re, mag, …) are not over-constrained and may adjust within reasonable ranges.
- Verification criterion: the best round must be one analysed by `generate_galfit_beam_actions` (its archives directory contains `*_beam_actions_*.md` candidate artefacts, and the working_note records that round's Physicality Verdict). If a suspected-best round lacks beam-action artefacts or a verdict record, call `generate_galfit_beam_actions` once more (with that round's feedme / galfit.NN / comparison image as input) to generate them and support the optimality check.
- Metric criterion: with the component, fit, physics, attempt and verification criteria above all satisfied, select on residual quality:
  - every `generate_galfit_beam_actions` call outputs a Physicality Verdict (visual residual judgement) and a candidate list; together with the χ²/BIC statistics returned by `run_galfit` they are key references for residual quality (the BIC used for model comparison is always **BIC_eff** = χ²/A_psf + k·ln(N/A_psf); see the summary statistics table — the 1D BIC is reference only).
  - When two rounds differ only in F1, an F1 amplitude above the 0.02 threshold suffices to keep it — choose the F1-bearing round.

### Lock-enforcing audit (enforcement)
These six criteria are easily overlooked in execution, so **before formally locking the best round, the subagent `best-round-verifier` must be called** (defined in `.claude/agents/best-round-verifier.md`) to audit the candidate round independently, mechanically and traceably:
- The subagent is a **read-only audit**: it checks every criterion with evidence and returns `verdict: PASS|FAIL`.
- `FAIL` → locking is strictly forbidden; fix per its "blocking issues" list, refit and re-audit to `PASS`; only `PASS` (WARN allowed) may lock.
- The Stage-3 locking steps of the workflows (`workflow_galfit` / `workflow_galfits`) embed this audit gate.
