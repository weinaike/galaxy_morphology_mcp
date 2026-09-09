
## Core Principles

1. **ALWAYS read files first** before making any modifications
2. **NEVER modify the original .lyric file** — write new configs with `_iter{n}` suffix in the galaxy's main directory
3. Use the repository's component specification and the server's logical tool descriptions before editing configs. A client-specific skill may provide convenience documentation, but it is not a workflow prerequisite.
4. **Only use `--fit_method ES`** to run GalfitS
5. **NEVER assume pixel scales** — always use the logical `re_arcsec2pix` MCP tool to convert arcsec to pixels via FITS WCS headers. Do not use hardcoded values like 0.031"/px or 0.063"/px, as images may be drizzle-resampled at different scales.
6. **NEVER use `--readsummary`** to carry parameters between rounds. It uses `astropy.ascii.read` which only parses the `# free parameters:` section, silently missing any parameter that was `vary=0` in the previous round — even if you flip it to `vary=1` in the new config. Instead, manually extract the fitted values from the previous round's `.gssummary` and write them as the `initial_value` of the corresponding parameters in the new `.lyric` file. This keeps each config self-contained and the fit reproducible from the lyric alone.

## Structured Workflow Pilot

The structured workflow bridge is consumed only when `COMPONENT_ANALYSIS_WORKFLOW_PILOT=1` is explicitly enabled. It does not replace the existing GalfitS MCP fitting tools or start a second fitting executor.

- Register each image round with `build_workflow_round_manifest` using explicit lyric, per-band result FITS/HDU, gssummary, comparison PNG, PSF, mask, and band-order references. Do not infer a round with glob or filename guessing.
- `decision_artifact.resolved_decision` is the sole machine action source. Markdown, Working Note, and Agent/VLM prose are explanation and audit evidence only.
- The Agent/VLM may rank policy candidates, explain evidence conflicts, and provide a structured `parameter_plan`; it may not write lyric text, invent an action outside the candidate set, or bypass a policy veto.
- Use `workflow_action_preflight` and `workflow_action_config` to create a new lyric. Run `check_lyric_file` before the existing `run_galfits_image_fitting` MCP tool. Keep `extra_args=["--fit_method", "ES"]` and never use `--readsummary`.
- After each MCP result, call `record_workflow_fit_lifecycle` with raw/resolved decisions, action summary, fit result, refit verdict, `PolicyState`, and downstream handoff.
- After each executed image candidate, call `workflow_evaluate_refit`; consume its structured `ACCEPT_REFIT`, `REJECT_REFIT`, or `STOPPED_NEEDS_REVIEW` result instead of inferring a refit verdict from the Working Note.
- `PROPOSE_REMOVE` remains review-only until its real local residual facts and remove pilot pass. Edge-on Disk is outside this redesign. `CONVERGED` requires `workflow_verify_best_round` to produce a lockable verifier artifact, followed by `workflow_lock_best_round` with that artifact reference.
- `STOPPED_NEEDS_REVIEW` stops only the automatic component loop. With a valid image result, finish the Image report and mark `FIT_AVAILABLE`, `UNLOCKED`, `needs_review=true`, `sed_joint_eligible=false`; without a valid result, mark `FAILED_NEEDS_REVIEW`. In both cases SED and Image-SED remain `NOT_RUN`.

---

## Lyric File Parameter Format

GALFITS uses `.lyric` config files. Key parameter format:

```text
[initial_value, min, max, step, vary]
```

- `vary=1`: free parameter | `vary=0`: fixed parameter

### Critical: never write `min == max`

lmfit raises `ValueError: Parameter '...' has min == max` at load time **regardless of `vary`** — a fixed parameter is NOT exempt. GalfitS aborts before any iteration, leaving an empty output directory.

Rule: even when `vary=0`, always provide a non-degenerate range `[v, v-d, v+d, step, 0]`.

- Bar n=0.5 fixed → `[0.5, 0.4, 0.6, 0.1, 0]` ✅ (not `[0.5, 0.5, 0.5, 0.1, 0]`)
- Disk n=1 fixed   → `[1.0, 0.5, 2.0, 0.1, 0]` ✅ (not `[1.0, 1.0, 1.0, 0.1, 0]`)

The only allowed exception is the all-zero unused-slot convention `[0, 0, 0, 0, 0]` for slots galfits never reads (e.g. `Pb10`–`Pb16`). `check_lyric_file` enforces this and will reject the lyric otherwise.

### Phase-Specific Parameter Flags

| Phase | Ia15 (Use SED) | Pa3-Pa8 (Spatial) | Pa9-Pa16 (SED) |
|-------|---------------|-------------------|----------------|
| 1 (Image only) | 0 | vary=1 | vary=0 |
| 2 (SED only) | 1 | vary=0 | vary=1 |
| 3 (Joint) | 1 | vary=1 | vary=1 |

---

## Component Type Quick Reference

| Prefix | Component | Parameters | Example |
|--------|-----------|------------|---------|
| **R** | Region | R1-R3 | `R1) MyGalaxy` |
| **I** | Image | Ia1-Ia15 | `Ia15) 1` # Use SED |
| **S** | Spectrum | Sa1-Sa4 | `Sa1) spectrum.txt` |
| **A** | Atlas | Aa1-Aa7 | `Aa2) ['a','b']` |
| **P** | Profile | Pa1-Pa32 | `Pa2) sersic` |
| **N** | Nuclei/AGN | Na1-Na27 | `Na12) ['Hb','Ha']` |
| **G** | Galaxy | Ga1-Ga7 | `Ga2) ['a','b']` |

### Profile Sub-Types (determined by Pa2)

| Profile Type | Pa2 Value | Use For |
|-------------|-----------|---------|
| Sersic | `sersic` | Bulge, Disk, Bar (any axisymmetric component) |
| Fourier Sersic | `sersic_f` | Spiral arms, non-axisymmetric features |
| Ferrer Bar | `ferrer` | Bar with flat inner core |
| Edge-on Disk | `edgeondisk` | Galaxy viewed edge-on |
| Gaussian Ring | `GauRing` | Ring or lens structure |
| Gaussian | `Gaussian` | Unresolved point source |

### Physical Component → Model Mapping

| Physical Component | Model Type | Key Parameters |
|-------------------|------------|----------------|
| Disk | Sersic, n~1 (can be <1 for smooth disk) | Re = large, q = moderate |
| Bulge | Sersic, n=4 (range 0.1-8) | Re = small, q = round |
| Bar | Sersic, **n=0.5 fixed** | q = 0.2-0.4, PA from image |
| Edge-on Disk | edgeondisk | Pa5 = R_s (scale-length), Pa6 = h_s (scale-height), Pa7 = PA, Pa8 unused/fixed |
| AGN/Nucleus | PSF (only when Re < 0.2 px in ALL bands) or Sersic | x, y, mag only |
| Lens | low-n Sersic | Re between Disk and Bar, q > 0.5; multi-band automatic action is allowed |

### Edge-on Disk Selection Rule

Use `edgeondisk` only for genuinely edge-on disks: first fit a Sersic disk with free axis ratio, then convert to `edgeondisk` only when the fitted disk has **b/a < 0.17** (equivalent to inclination >80° under the thin-disk approximation) **and** the residual/original image shows edge-on vertical structure such as a dust lane or disk thickness. If b/a ≥ 0.17, keep a Sersic disk with n≈1 instead of forcing `edgeondisk`; this preserves the inclination information and avoids over-constraining moderately inclined disks. For `edgeondisk`, Pa5 is `rs`, Pa6 is `hs`, Pa7 is PA, and Pa8 is not used and should be fixed.

Note: In the fitting input and output configurations, the Effective Radius ($R_e$) is strictly defined in units of arcseconds (arcsec). Before evaluating the fitting results, $R_e$ must be dynamically converted into pixel units using the WCS (World Coordinate System) metadata extracted from the corresponding FITS headers. This step is essential to accurately map the analytical model profiles onto the actual observational image grid, especially since the physical pixel scale ($arcsec/\text{pixel}$) varies across different wavebands.

**AGN/PSF replacement rule**: A Bulge may be replaced by a PSF/AGN component **only when its fitted $R_e$ is < 0.2 px in EVERY band** (convert $R_e$ to pixels in each band separately via the FITS WCS). If $R_e$ ≥ 0.2 px in any single band, the component is resolved and must remain a Sersic profile — do not switch to PSF/AGN even if $R_e$ hits the lower bound in the lyric; instead, widen the lower bound and refit.

---

## Config File Management

### Directory Structure
```
obj195/
├── obj_195.lyric                    # Original config (NEVER modify)
├── obj_195_iter2.lyric              # Iteration 2 config
├── obj_195_iter3.lyric              # Iteration 3 config
├── analysis_report_obj195.md        # Final analysis report
└── output/
    ├── 20260525_150747_obj_195/      # Round 1 output
    ├── 20260525_151530_obj_195_iter2/
    └── 20260525_152158_obj_195_iter3/
```

### Rules
- New config files must be written to the **galaxy's main directory** (where the original .lyric is), with `_iter{n}` suffix
- To reuse parameters from an earlier fitting, manually extract the fitted values from the previous round's `.gssummary` and write them as the `initial_value` of the corresponding parameters in the new `.lyric` file. **Do NOT use `--readsummary`** (see Core Principles #6).
- If you need to constrain galaxy components to share the same center (e.g., make bulge, bar and disk have identical centers), complete the following three steps:
    - In the .lyric configuration file, set the x and y parameters (maybe Pb3, Pb4, Pc3, Pc4, it depends) of bulge and bar to fixed.
    - Create a constraint file named iter{n}.constrain with the following content (python function):
          def Update_Constraints(pardictlc):
              pardictlc['bulge_xcen'] = pardictlc['bar_xcen'] = 1 * pardictlc['disk_xcen']
              pardictlc['bulge_ycen'] = pardictlc['bar_ycen'] = 1 * pardictlc['disk_ycen']
    - Add the parameter --parconstrain iter{n}.constrain when calling Galfits fitting methods to load this constraint file.
    - For multi-band AGN/Nuclei components, the center parameter names are `xcen_agn` and `ycen_agn`, not `agn_xcen` or `agn_ycen`. Use these exact names in `.constrain` files when tying AGN centers to other components.
- When including companion galaxies in the fitting:
    - The galaxy central coordinates must be constrained in the Lyric file to prevent positional drift of companion galaxies during the fitting process. The offset between the model center and the detected galaxy center is generally limited to within 5 pixels. 
    - The position unit for all galaxy components in the Lyric file is arcsec; unit conversion from pixels to arcsec is therefore required beforehand. This conversion must be executed externally using the mcp tool rather than being calculated manually.
    - Generally, companion galaxies are physically smaller and less luminous than the main (host) galaxy. When configuring fitting components for a companion galaxy in a .lyric file, you must set a significantly tighter upper boundary for its effective radius ($R_e$) compared to that of the main galaxy . Use the main galaxy's $R_e$ as a reference prior to prevent the companion's parameters from expanding unreasonably or disrupting the host galaxy's fitting convergence.
- `run_galfits` automatically creates output directories; do NOT manually create directories

For the structured pilot, `workflow_action_config` is the only config-writing entrypoint for an action. The original lyric remains unchanged, and every generated lyric must pass `check_lyric_file` before fitting. A valid image fit with `STOPPED_NEEDS_REVIEW` is a review-complete handoff, not a license to lock the best round. The logical `workflow_verify_best_round` and `workflow_lock_best_round` tools are the only structured lock path; the `mcp__galmcp__...` names below are client-specific aliases/examples only.

---

## Available MCP Tools

### mcp__galmcp__run_galfits_image_fitting
Execute GalfitS multi-band image fitting.
- `config_file`: Absolute path to .lyric config file (REQUIRED)
- `extra_args`: Additional CLI args, e.g. `["--fit_method", "ES"]`
- `timeout_sec`: Optional (default: 3600)

### mcp__galmcp__run_galfits_sed_fitting
Execute SED fitting based on image fitting results.
- `config_file`: Path to the .lyric config used for the best image fitting
- `image_fitting_workplace`: Path to the best image fitting output directory
- `extra_args`: e.g. `["--fit_method", "ES"]`
- Returns: New .lyric config file for Image-SED joint fitting

### mcp__galmcp__run_galfits_image_sed_fitting
Execute Image-SED joint fitting.
- `config_file`: Path to the .lyric config generated by SED fitting step
- `extra_args`: e.g. `["--fit_method", "ES"]`

### mcp__galmcp__component_analysis
Analyze fitting results and provide component adjustment strategy.
- `image_file`: Path to the combined stamp PNG (Original|Model|Residual)
- `summary_file`: Path to .gssummary file
- `mode`: 'single-band' or 'multi-band'
- `custom_instructions`: Context for the analysis

### mcp__galmcp__render_original
Render original science image with contours and mask overlay.
- `config_file`: Path to .lyric config file

### mcp__galmcp__view_original_image
Classify galaxy morphology from original image using VLM.
- `image_file`: Path to galaxy image PNG
- `source_id`: Source identifier
- `custom_instructions`: Analysis guidance

### Important GalfitS CLI Parameters

| Parameter | Purpose | When to Use |
|-----------|---------|-------------|
| `--fit_method ES` | Evolution Strategy optimizer | All fitting rounds (REQUIRED) |
| `--parconstrain <file>` | Apply center/parameter constraints | When sharing params across components |
| `--prior <file>` | Apply mass/size constraints | When prior file available |

**Deprecated — do NOT use:** `--readsummary`. It uses `astropy.ascii.read` which only parses the `# free parameters:` section, silently dropping any parameter that was `vary=0` in the previous round. Manually copy fitted values from `.gssummary` into the new `.lyric` instead (see Core Principles #6).

---

## SKILL Reference

Use the repository component specification and the server's logical tool descriptions to access the complete GalfitS documentation. `/skill galfits-manual` is optional client-side help, not a required workflow dependency.

| Edit Task | SKILL Reference |
|-----------|-----------------|
| Add Sersic bulge | model-components/profile-sersic.md |
| Add Sersic bar | model-components/profile-sersic.md |
| Add AGN | model-components/nuclei-agn.md |
| Fix band misalignment | running-galfits.md |
| Enable SED fitting | SKILL.md → Phase-Specific |
| Apply constraints | constraints/ |

---
