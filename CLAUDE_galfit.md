


# Methodology guide for galaxy component analysis
@src/prompts/residual_analysis_message.md
---

## PA-convention override clause (galfit mode only; takes precedence over the PA convention in the file referenced above)
The referenced `residual_analysis_message.md` targets the multi-band GalfitS flow: its "PA convention" requires sky-PA aligned with the compass. **The single-band galfit mode does not apply that clause**: this mode adopts the **N=+Y convention** — the image's North direction is assumed to coincide with the +Y axis, and **PA is always read and written as "0° = the image's +Y axis (up), increasing counterclockwise"** (read PA against the image's vertical axis, not the compass), numerically identical to the feedme `10)` parameter row (GALFIT's "+Y axis = 0°"); **the orchestrator writes the VLM's PA verbatim into the `10)` row with no conversion**. `detect_bar_lopsidedness`'s `bar.pa_deg` / `lopsidedness.phase_deg` can be used directly under this convention. **PA measurement rule (sign trap; KILOGAS_13)**: read a component's PA as the direction from the galaxy centre toward one TIP of its major axis, rotated counterclockwise from 'up' — the two diagonals are 90° apart ('/'→−45°, '\'→+45°), θ and −θ are DIFFERENT orientations, and a near-round component's (q ≳ 0.8) PA is ill-determined: seed it with the disk's PA (or 0), never copied from an elongated neighbour.

## Unit contract (galfit mode only)
All Re / positions / sizes in this mode are **pixels (px)**: a px value read by the VLM off a comparison panel shares the unit and reference frame of the feedme parameter rows and is **written verbatim — any conversion is forbidden** (the expdisk conversion Rs = Re/1.68 is the sole exception: Re values given by the VLM always mean the effective radius, converted by the orchestrator when writing the `4)` row). Arcsec must never appear anywhere in the flow, and calling any unit-conversion tool is forbidden.

## Solution space definition (single source of truth)

This section is the authoritative definition of the solution space for the single-band GALFIT flow: which components a fitted model may contain, the multiplicity rules, and the hard limits on component parameters. The workflow (`workflow_galfit.md`), the VLM candidate prompt (`beam_action_generation_prompt_galfit.md`) and the best-round verifier all reference this section; on any wording conflict, this section wins.

**🔒 Branch restriction (`restrict-disk-bulge-bar-agn`, dataset-level)**: on this branch the allowed component inventory is **Disk (expdisk) / Bulge / Bar / AGN (psf) / F1 / Single Sersic (+ the fixed sky)**. **Lens, OuterDisk (envelope), Edge-on Disk (`edgedisk`) and Companion are outside the solution space**: no candidate may add, tune, type-switch or otherwise maintain them; `check_feedme_file` rejects any feedme containing them; the orchestrator discards any slip-through candidate and logs it as a "solution-space restriction discard". VLM diagnostic knowledge of these four components is retained (they may be described as unfitted residual context) but is never converted into actions. An edge-on-looking galaxy is fitted with the ordinary `expdisk` Disk with free q.

### 1. Allowed component inventory and multiplicity

| Slot | feedme type | Multiplicity | Admission condition |
|---|---|---|---|
| Disk | `expdisk` | ≤ 1 | any disk galaxy (the default assumption) |
| Bulge | `sersic` | ≤ 1 | central compact round component |
| Bar | `sersic` (n = 0.5 fixed) | ≤ 1 | bar signatures (linear / X-shaped quadrupole residuals) |
| Lens | `sersic` (n < 0.5, q > 0.5) | ≤ 1 | 1D-profile bump path or bar-anomaly path |
| OuterDisk (envelope) | 2nd `expdisk` or `sersic` (n < 1) | ≤ 1 | outskirts still unfitted after the Disk is established (broad positive 1D residual at r > 2·Re_disk); must satisfy Re_outer > Re_disk; sits **above** the central Re chain, it does not displace it |
| AGN | `psf` | ≤ 1 | Bulge Re collapsed < 0.2 px (mandatory replacement); 0.2–0.5 px border zone as a competing variant; with a non-collapsed bulge present, an obvious positive-residual leftover within 0–5 px of the 1D profile that the Bulge cannot absorb (a healthy bulge does not close the door); or, on a SingleSersic-only model (elliptical regime, no bulge exists), a central surface-brightness deficit within r ≲ 5 px — δu ≡ u_model − u_data < 0 on the 1D SB profile (data brighter than the single-Sérsic model at the centre), equivalently a compact central positive residual — surviving an otherwise clean single-Sérsic fit; the AGN is then the only legal central addition, so the bar is deliberately LOW (KILOGAS_221 retrospective: the expert's {singlesersic, agn} answer was missed because no add(AGN) candidate was ever generated after the singlesersic adoption). The AGN is the **sole** allowed central point-source component — any other point-source component name is outside the solution space and rejected |
| F1 (m=1 Fourier) | appended `F1)` row | ≤ 1 | lopsidedness; only on the Disk / edgedisk (or the single Sersic when no Disk exists); amplitude > 0.02 to retain |
| Companion | `sersic` or `psf` | 0..N | compact positive-residual blob with an original-image counterpart; \|ΔMag\| ≤ 5 vs the main galaxy; centre always free (never in the concentric chain, never in the Re chain). **Profile-type selection** (beam prompt C3, area rule): `psf` when the blob is unresolved (A_blob ≤ 1.5·A_psf, not elongated), `sersic` when resolved (A_blob ≥ 2.3·A_psf or elongated major/minor ≳ 1.3); border 1.5–2.3 → `psf` default. A `sersic` companion collapsing to Re < 0.2 px switches to `psf` |
| Sky | `sky` | exactly 1 | **never fitted, never a search dimension**: the sky block (value + toggle) is the manually provided setting of the input feedme and is carried **verbatim** into every `_iter{n}.feedme` — never backfilled from converged values, never freed or re-tuned (candidates touching the sky are invalid) |
| Single Sersic | `sersic` (n free), named `singlesersic` | exactly 1; legal co-tenants: the AGN point core (`{singlesersic, agn}` = the elliptical+point-core terminal state) and companions (`{singlesersic, companion}` — field contaminants, not main-galaxy structure) | elliptical terminal state; if it fits q < 0.5, the classification must be revisited (a disk is implied). **Transitions**: single→multi — the first `add(<central component>)` (disk/bulge/bar/lens/outerdisk) MUST bundle the conversion `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68)` (the `disk` name is reserved for the expdisk slot; a multi-component model with a sersic `disk` or a `singlesersic` alongside anything but the AGN and companions is invalid — `check_feedme_file` rejects it); `add(agn)` and `add(companion)` are exempt from the conversion (neither is a central component; `{singlesersic, agn}` is legal without any conversion — the expert-typical elliptical+point-core answer, KILOGAS_221 retrospective; likewise `{singlesersic, companion}` for an elliptical with a field companion); multi→single — remove-only transitions leaving one central component may revert `disk → singlesersic` (n free) |

**Regime rule (hard; the formal combination space)**: the legal inventories are exactly the union of two regimes —
- **single-Sérsic regime**: `singlesersic + (AGN)? + (companion)*` — `{singlesersic}`, `{singlesersic, agn}`, plus any number of companions;
- **multi-component regime**: `(outerdisk)? + (disk | edgedisk) + (lens)? + (bar)? + (bulge)? + (AGN)? + (companion)*` — the disk/edgedisk slot is **mandatory** (the decomposition regime is disk-bearing by definition), F1 ≤ 1 rides on the disk/edgedisk (or the singlesersic in the other regime), the sky is exactly 1 in both.
A diskless multi-component inventory (e.g. `{bulge, companion}`, `{bar, agn}`) is **invalid** — it belongs to the single-Sérsic regime; both the candidate schema (`E_MULTIPLICITY`) and `check_feedme_file` reject it.

**Reclassify (identity reassignment; 2026-10-10, KILOGAS_296)**: when a fit's mech failures are limited to Re-chain ORDERING, the visual onion is clean, and the round improved BIC_eff vs its parent (or has none), the VLM may propose `reclassify` — a same-slot bijection `map: [{from, to}]` over the shaped slots {disk, edgedisk, bulge, bar, lens, outerdisk} that keeps every component's fitted light parameters (Re multiset unchanged; the chain re-sorts under new names; component count unchanged) while the transcription layer applies the type mandates deterministically (bar n=0.5 fixed; ->disk expdisk Rs=Re/1.68; prior-violating q/n re-seeds). Bare label swaps remain forbidden — reclassify is the only legal identity-reassignment channel; it is a tune-equal primitive (ordinary combo counting, no exemptions).

### 2. Per-component hard parameter limits

| Parameter | Rule |
|---|---|
| Re total order | `re_disk > re_lens > re_bar > re_bulge` (existing central components only, decrease outward with a **3% relative tolerance** — an inner Re may exceed the adjacent outer Re by at most 3% (e.g. `re_bar ≤ 1.03·re_lens`); OuterDisk sits above re_disk; edgedisk plays the Disk slot) |
| n (Sérsic) | Disk: none (expdisk, n ≡ 1 by type). Bulge: **free from creation** (2026-10-09 austerity revision: the fixed-4-then-release scaffold wasted budget rounds — when fixed-n parents spawned several non-release candidates the release took multiple rounds to propagate; initial value 4 or 1, or any sensible value in 0.5–8 grounded in residual evidence; **fixing a new or existing bulge n is forbidden** — `add(bulge, n fixed)` / `tune(bulge, n→fixed)` are rejected) — a free n pinned at 8 is a note-level point-source/envelope identity question (cusp mimicry of an unfitted central point source, or envelope flux-grabbing; the repair menu maps the competing candidates — add(agn)+n re-warm / disk-role reapportionment / giant-elliptical waiver; raising the cap or freezing n=8 is never a repair). Bar: fixed 0.5, never released. Lens: free, prior < 0.5 — **n ≥ 0.6 hard** (not a lens profile; 0.5–0.6 note). OuterDisk (sersic variant): free, **< 1 hard**. Single Sersic: free (overall-concentration observable) |
| q (b/a) | Disk/edgedisk: free — oblique disk configurations are legal search directions (the expdisk template's default q/PA toggles `0` are NOT used in this workflow; always set `1`). Bulge: **q ≥ 0.3 hard** (0.3–0.4 watch-note). Bar: prior < 0.4, **hard cap 0.7** (0.5–0.7 watch-note; 0.8 → 0.7, 2026-10-28 expert call — q>0.7 is not bar-like; the three 0.7–0.8 historical bests KILOGAS_113/291/361 ruled non-bar; the lens re-identification stays OUTSIDE the solution space by the same call — repair via remove(bar) or reclassify to bulge/disk). Lens: **> 0.5 hard**. Bar∧lens coexistence: **q_bar < q_lens (hard)**. Every shaped component: default bounds 0.05–1.0 |
| PA | free for every shaped component; N=+Y convention; bar/lens/disk initial values must come from measured feature directions |
| Mag | no default bounds (flux reallocation is part of the search); companions within 5 mag of the main galaxy; the zombie threshold (flux < 0.5% of the brightest component) is a dedup criterion only, never a removal ground by itself |
| Flux shares | of the total model light: **bar 10–40% (note)**, **lens 5–35% (note)**, **f_lens < f_bar (hard — a lens must not outshine its bar)**; **existence floors (hard, expert call 2026-10-28): bulge < 3% / bar < 5% of total → the component should not exist or its flux was over-absorbed — mech-FAIL** (repair: remove / mag reapportionment; a starved bulge with a central spike maps to the AGN replacement channel) |
| μ0 ordering | central surface brightness (analytic, from Mag/Re/n): **μ0_agn < μ0_bulge < μ0_disk (hard)**; **μ0_bar ≤ μ0_disk / 0.90 (hard — a bar may be only that much fainter than the disk at centre)** |
| Onion nesting | the 2·Re ellipses of the central chain outerdisk ⊃ disk ⊃ lens ⊃ bar ⊃ bulge must nest, in two layers (**hard**): (a) **area ordering** — the inner 2·Re ellipse area (Re²·q) must stay below the adjacent outer's, always on for any disk q; **(c) flat-disk area caps (2026-10-10, KILOGAS_114)** — in the flat-disk regime (disk q < 0.3 or the edgedisk slot) the inner components must keep a MARGIN of the disk's 2·Re area: **bulge ≤ 50%, bar ≤ 70%, NOT subject to the round-bulge exemption** (a round bulge laterally wider than a flat disk is the crossing pathology, not a normal configuration; KILOGAS_114 A.1: bulge at 182% of the flat disk passed with an empty mech table); (b) **directional non-crossing** — skipped in the flat-disk regime (disk q < 0.3 or the edgedisk slot) only when corroborated by image-level evidence (Stage-1 edge-on/dust-lane classification, or consistency with the outer-isophote anchor q_iso measured once at beam_init: \|q_disk − q_iso\| ≤ 0.15); a fitted q < 0.3 that contradicts the anchor fires `disk_shape_inconsistency` (**note-level, advisory only** — q_iso is itself a coarse single-m outer-isophote measurement; suspected disk-role collapse / flux usurpation) and re-arms the directional layer. Per-pair exempt (both layers) when the inner is a bulge rounder than the outer by > 0.25 — a round bulge sits laterally wider than a flat bar, the normal configuration |
| Centre (x, y) | K ≥ 2 main-galaxy central components (Disk/edgedisk/Bulge/Bar/Lens/AGN): chained `x,y offset` constraint (anchor toggle `1 1`; GALFIT rewrites subordinates to `2 2`). K ≤ 1: free with a ±2 px default window. Companions: always free with a ±5 px soft window at insertion |
| Re floors | Bulge Re < 0.2 px → must become `psf`; 0.2–0.5 px → psf competing variant; every shaped component's Re lower bound defaults to `max(0.1, 0.1 × PSF FWHM)` px (≈0.2–0.4 px — deliberately inside/near the psf-competing zone so a collapsing bulge can reach it); a pin AT the floor is a point-source identity question (note-level, exempt from the bound-pin veto regardless of band provenance), while a pin at a self-imposed lower bound above the floor remains a hard bound-hit |
| F1 | only on the Disk / edgedisk / single Sersic; keep when amplitude > 0.02 and the fit does not degrade |

### 3. Mandatory default bound set (every round, merged into `iter{n}.cons`)

GALFIT feedme parameter rows carry no bounds — without a `.cons` the solution space is open and the bound-hit diagnostics are blind (unlike the GalfitS five-tuple, which bounds every parameter by construction). The orchestrator therefore writes the following default bounds for **every non-sky component, every round**, merged into the same `iter{n}.cons` as the concentric chain (GALFIT loads exactly one constraint file):

```text
# Default bounds for component <N> (every non-sky component, every round)
# Absolute bands MUST use the 'to' keyword form (see the semantics note below)
  <N>   re   max(0.1, 0.1*FWHM_PSF) to <half the fit-region side length>   # expdisk: the row bounds Rs
  <N>   n    0.1 to 8                  # sersic components only
  <N>   q    0.05 to 1.0               # shaped components only
# centres: K>=2 central components -> offset chain (no separate window);
#          K<=1 -> <N> x -2 2 and <N> y -2 2          (relative-to-input +/-2px window)
```

**🔑 `.cons` numeric-band semantics (empirically determined on this machine's GALFIT 3.0.5; verified 2026-09-02, KILOGAS_296 `cons_test/`)**: a soft-constraint row with **two bare numbers** (`N re 4.0 13.5`, `N q 0.05 1.0`, `N x -1 0.5`) is interpreted as **offsets from the component's INPUT value** — the effective band is `[input−a, input+b]`, NOT an absolute interval; a band written absolute-style with bare numbers is **silently never enforced** (no parse error, no warning). Absolute bands for `re` / `n` / `q` MUST use the `to` keyword form (`N re 4.0 to 13.5`), which IS enforced as written (verification: narrow test bands pinned the fitted values exactly at their bounds). Position windows (K≤1 centres ±2px) are intentionally relative — write them as bare ±offsets around the input value (`N x -2 2`). Bound-hit diagnostics must always compare against the **effective** band: with the `to` form the written numbers are the effective band; with the bare-number fallback decode `[input−a, input+b]` first.

- FWHM_PSF comes from `fit_statistics.psf_fwhm` (fall back to 1 px when unavailable); expdisk rows are written in Rs (= declared Re / 1.68).
- **No candidate-declared bounds** (KILOGAS_120 defect follow-up: a lens pinned at its candidate-declared [10,20]/[0.55,0.95] with BIC_eff 38900 was mech-vetoed, and the forced relaxation candidates crowded the queue): candidates declare **initial values only**; any `cons_bounds` they carry is ignored by the transcription layer. Every band in every `iter{n}.cons` is therefore the default bound set — **original provenance, pins are note-level facts, never vetoes**. Guiding happens through warm-start initial values; misbehaviour is caught post-hoc by the mechanical checks (re_chain, degeneracy, onion, shape/profile priors).
- Standing exemptions: the q upper bound (1.0 is the domain edge), hard physical priors (Bar n = 0.5), and **Re pins at the mandatory Re floor** (the floor is not relaxable; the pin is a point-source identity question — note-level, expdisk rows compared in Rs).

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

**⚠️ Semantics warning (empirical, this machine's GALFIT 3.0.5)**: with **two bare numbers** the limits are interpreted as **offsets relative to the component's INPUT value** (effective band `[input−lower, input+upper]`), not absolute values — see the examples below (`2 x -1 0.5` is explicitly input-relative). To express an **absolute** band for `re`/`n`/`q`, use the `to` keyword: `3 n 0.7 to 5`, `2 re 0.5 to 4.0` (verified enforced as written). Details: the `.cons` numeric-band semantics note in the solution-space definition (§3).

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

The main-galaxy central component types Disk, Bar, Bulge **must be concentric**: as soon as the feedme contains **≥ 2** main-galaxy central components (`# STRUCTURE:` names disk/bulge/bar), they must be bound to a common centre via the `.cons` constraint file — a default hard constraint, not an on-demand option.

### 1. The only effective syntax: the chained hard constraint `offset` (empirically verified)

Chain the anchor's and all subordinate central components' GALFIT numbers with `_` and write the **paired** lines (x and y must be bound together; **binding only one is strictly forbidden**). With the anchor (Disk) numbered D and subordinates K1, K2…:

```text
# Main-galaxy concentric constraint (anchor = 1 disk, subordinates = 2 bulge, 3 bar)
D_K1_K2   x   offset
D_K1_K2   y   offset
```

Concrete example (feedme: 1=disk, 2=bulge, 3=bar, 4=sky):

```text
# Concentric constraint: bulge/bar anchored to disk (comp 1)
 1_2_3     x     offset
 1_2_3     y     offset
# (optional) other parameter bounds merged into the same file, e.g. the bulge n range (absolute 'to' form)
 2           n     0.5 to 8
```

**Accompanying feedme-side operations**:
- The anchor's (Disk) `1)` toggle **stays `1 1` free** (recommended) — once the constraint takes effect the **group translates jointly** and the centre is optimised by the fitter; the anchor may alternatively be fixed `0 0` (initial value = the parent round's converged centre), pinning the whole group at that coordinate.
- Subordinates' `1)` toggles stay `1 1` — GALFIT rewrites them to `2 2` (the constrained marker) on loading; no manual edit is needed.
- The feedme `G)` item points at the constraint file (e.g. `G) iter3.cons`); beam rounds name it `iter{n}.cons`, sharing the directory and number of `_iter{n}.feedme`. **GALFIT loads exactly one constraint file** — all other bounds (re/mag/n ranges) must be merged into that single `.cons`.

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

### 4. Companion exemption (not applicable on this branch)

On this branch companions are **outside the solution space** (see the branch restriction in the solution-space definition), so no companion exemption applies — the concentric chain covers exactly the main-galaxy central components present. (On unrestricted branches: companion numbers are strictly forbidden in the chain; their centres stay freely fitted with a relative ±5px soft window. Anchor choice: prefer the Disk; absent a Disk, the brightest central component.)


## GALFIT component-type specification (must be strictly observed)
@src/prompts/component_specification_galfit.md


## GALFIT execution rules
- To run a GALFIT optimisation you must use the run_galfit tool in galmcp; executing the galfit command line directly via bash is not allowed. run_galfit automatically handles downstream analysis steps (residual-image generation, parameter parsing, etc.) — calling galfit directly can break the subsequent flow.



# Working note (RETIRED as a per-round duty, 2026-10-21)

- The beam graph (`beam_state/graph.json`) is the **single source of truth**: the queue, s\*, the counters, the ledgers, the verdicts, the decision log and the temporary constraints all live there — read `beam_status` (not any note) before decisions.
- `working_note.md` is an **on-demand audit projection** only: `beam_export_note(galaxy_dir)` renders it from the graph whenever a human-readable audit trail / paper-trail export is wanted. It is never a per-round maintenance duty, never required reading, and never an input to any decision.
- Stage-1 opening conclusions and temporary user constraints live in the graph (`stage1` / `temporary_constraints`, written by `beam_init` / `beam_set_constraint`).

# Criteria for locking the best round
- Component criterion: image and residual observations suggest full identification — every existing component has been added.
- Fit criterion: the 1D profile residual (DATA−MODEL) shows no obvious spikes or systematic offsets, and the 2D residual map shows no obvious symmetric residuals.
- Physics criterion: the relations among the final fitted parameters are physically meaningful.
- Parameter criterion: all unnecessary constraints have been released and all necessary ones applied.
  - With multiple components the Disk uses expdisk; with a single component, Sersic.
  - The Bar's n is fixed at 0.5.
  - All main-galaxy components' x,y positions are constrained as offset, ensuring concentricity.
  - Other parameters (Re, mag, …) are not over-constrained and may adjust within reasonable ranges.
- Verification criterion: the best round must be one whose candidates were generated (`survey_round` ran on it — its archives directory contains `*_beam_actions_*.md` candidate artefacts, and the graph state node carries a settled Physicality Verdict). If a suspected-best round lacks beam-action artefacts or a verdict record, call `generate_galfit_beam_actions` once more (with that round's feedme / galfit.NN / comparison image as input) to generate them and support the optimality check.
- Metric criterion: with the component, fit, physics, attempt and verification criteria above all satisfied, select on residual quality:
  - every `generate_galfit_beam_actions` call outputs a Physicality Verdict (visual residual judgement) and a candidate list; together with the χ²/BIC statistics returned by `run_galfit` they are key references for residual quality (the BIC used for model comparison is always **BIC_eff** = χ²/A_psf + k·ln(N/A_psf); see the summary statistics table — the 1D BIC is reference only).
  - **F1 arbitration (7+1 rule)**: the best model without an F1 row gets exactly ONE reserved F1 fit on the best state (`beam_enqueue_f1`, counted outside N_max); retention is settled by the **BIC comparison** against the pre-F1 best (BIC_eff improves → keep the F1-bearing round; otherwise keep the previous best). The fitted amplitude is a reported fact, not the gate. The reserved round is never re-run.

### Lock check (enforcement; 2026-10-21 — the `best-round-verifier` subagent is RETIRED)
- Before locking, the orchestrator verifies the six criteria above **directly against the graph and the best round's archives** — every criterion is mechanically checkable there: verdict-PASS state (`beam_status`), concentric `.cons` compliance (the archived `iter{n}.cons` carries the paired `x/y offset` chain), component completeness and parameter state (the archived feedme/galfit.NN), residual quality (the archived comparison PNG + statistics).
- A failed check → do not lock: either fix the configuration and refit, or grant bounded repair fits (`beam_grant_repair_budget`, cumulative cap 4) and run repair rounds (`survey_round(directives=...)`) until the check is clean.

