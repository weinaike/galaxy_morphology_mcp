
## Core Principles

1. **ALWAYS read files first** before making any modifications
2. **NEVER modify the original .lyric file** — write new configs with `_iter{n}` suffix in the galaxy's main directory
3. **Use `/skill galfits-manual`** to access complete GalfitS parameter documentation before editing configs
4. **Only use `--fit_method ES`** to run GalfitS
5. **NEVER assume pixel scales** — always use `mcp__galmcp__re_arcsec2pix` to convert arcsec to pixels via FITS WCS headers. Do not use hardcoded values like 0.031"/px or 0.063"/px, as images may be drizzle-resampled at different scales.
6. **NEVER use `--readsummary`** to carry parameters between rounds. It uses `astropy.ascii.read` which only parses the `# free parameters:` section, silently missing any parameter that was `vary=0` in the previous round — even if you flip it to `vary=1` in the new config. Instead, manually extract the fitted values from the previous round's `.gssummary` and write them as the `initial_value` of the corresponding parameters in the new `.lyric` file. This keeps each config self-contained and the fit reproducible from the lyric alone.

---

## Lyric File Parameter Format

GALFITS uses `.lyric` config files. Key parameter format:

```text
[initial_value, min, max, step, vary]
```

- `vary=1`: free parameter | `vary=0`: fixed parameter

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
| Edge-on Disk | edgeondisk | h_s (scale-height), R_s (scale-length) |
| AGN/Nucleus | PSF (only when Re < 0.2 px in ALL bands) or Sersic | x, y, mag only |

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
- `run_galfits` automatically creates output directories; do NOT manually create directories

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

Use `/skill galfits-manual` to access the complete GalfitS documentation.

| Edit Task | SKILL Reference |
|-----------|-----------------|
| Add Sersic bulge | model-components/profile-sersic.md |
| Add Sersic bar | model-components/profile-sersic.md |
| Add AGN | model-components/nuclei-agn.md |
| Fix band misalignment | running-galfits.md |
| Enable SED fitting | SKILL.md → Phase-Specific |
| Apply constraints | constraints/ |

---
