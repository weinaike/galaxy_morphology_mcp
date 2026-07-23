# Nuclei/AGN Configuration

AGN (Active Galactic Nucleus) models in GalfitS capture the characteristics of the central engine including accretion disk, broad/narrow emission lines, FeII pseudo-continuum, and torus emission.

## Overview

- **Prefix**: `N` (Na, Nb, Nc...)
- **Use Case**: AGN with full SED model, including continuum, emission lines, and torus
- **Parameters**: 27 parameters (Na1-Na27)

## Parameter Reference

### Basic Parameters (Na1-Na5)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Na1** | Names the nucleus component (e.g., 'AGN', 'nucleus') | string |
| **Na2** | Redshift of the nuclei | [0.061, 0.011, 0.111, 0.01, 0] |
| **Na3** | EB-V of Galactic dust reddening | float (e.g., 0.055) |
| **Na4** | X-center [arcsec] | [0, -5, 5, 0.1, 1] |
| **Na5** | Y-center [arcsec] | [0, -5, 5, 0.1, 1] |

### Black Hole Parameters (Na6-Na10)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Na6** | Log black hole mass [solar mass] | [7, 5, 10, 0.1, 0] |
| **Na7** | Log L/LEdd (Eddington ratio) | [-1, -4, 2, 0.1, 0] |
| **Na8** | Black hole spin (a*) | [0, 0, 0.99, 0.01, 0] |
| **Na9** | Intrinsic reddening Av [mag] | [0, 0, 3.1, 0.1, 0] |
| **Na10** | Log L5100 [erg/s] | [43, 41, 47, 0.1, 1] |

### Continuum and Lines (Na11-Na18)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Na11** | Power law indexes | [[1, 0, 4, 0.1, 1], [0.6, 0, 5, 0.1, 1]] |
| **Na12** | Broad emission lines | ['Hg', 'Hb', 'Ha', ...] |
| **Na13** | Narrow emission lines | ['Hb', 'OIII_4959', 'OIII_5007', ...] |
| **Na14** | Number of Gaussian components for broad lines | 1, 2, or 3 |
| **Na15** | Number of Gaussian components for narrow lines | 1, 2, or 3 |
| **Na16** | Add Balmer continuum (0=no, 1=yes) | 0 or 1 |
| **Na17** | Add FeII pseudo-continuum (0=no, 1=yes) | 0 or 1 |
| **Na18** | Continuum model type | 0, 1, 2, 3, or 4 |

### Continuum Model Types (Na18)

| Value | Model | Description |
|-------|-------|-------------|
| 0 | Power law | Simple power law continuum |
| 1 | Broken power law | Power law with break |
| 2 | Thin disk | Accretion disk model |
| 3 | Type 2 | Obscured AGN |
| 4 | Arbitrary | User-defined |

### Torus Parameters (Na19-Na25)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Na19** | Normalization of spectrum (images+spec fitting) | [1., 0.5, 2, 0.05, 0] |
| **Na20** | Add torus (0=no, 1=yes) - emits at [1, 1000] microns | 0 or 1 |
| **Na21** | Log torus luminosity [erg/s] | [41, 39, 44, 0.1, 0] |
| **Na22** | Torus a - power-law index of radial dust distribution | [-0.5, -2.5, -0.25, 0.05, 0] |
| **Na23** | Torus h - dimensionless scale height | [0.5, 0.25, 1.5, 0.05, 0] |
| **Na24** | Torus N0 - number of clouds along equatorial LOS | [7, 5, 10, 0.5, 0] |
| **Na25** | Torus i - inclination angle [degrees] | [15, 0, 90, 5, 0] |

### Additional Parameters (Na26-Na27)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Na26** | Normalization of images_atlas | [1., 0.2, 5, 0.1, 1] |
| **Na27** | Hot dust (1200 K blackbody). Optional — omit if unused; otherwise a list of two 5-tuples `[Lhotdust, Thotdust]` | [[40,38,42,0.1,0],[1000,500,2000,10,0]] |

## Complete Example

```text
# Nuclei A
Na1) AGN                                        # Name of the nuclear component, which must follow the rules for legal identifiers (letters, digits, and underscores only)
Na2) [0.061,0.011,0.111,0.01,0]                 # redshift of the nuclei
Na3) 0.055                                      # the EB-V of Galactic dust reddening
Na4) [0,-5,5,0.1,1]                             # x-center [arcsec]
Na5) [0,-5,5,0.1,1]                             # y-center [arcsec]
Na6) [7,5,10,0.1,0]                             # log black hole mass [solar mass]
Na7) [-1,-4,2,0.1,0]                            # log L/LEdd
Na8) [0,0,0.99,0.01,0]                          # black hole spin, a star
Na9) [0,0,3.1,0.1,0]                            # intrinsic reddening Av [mag]
Na10) [43,41,47,0.1,1]                          # log L5100 [erg/s]
Na11) [[1,0,4,0.1,1], [0.6, 0, 5, 0.1,1]]       # power law indexes
Na12) ['Hg','Hb','Ha']                          # broad emission lines
Na13) ['Hg','Hb','Ha','OIII_4959','OIII_5007','NII_6549','NII_6583','SII_6716','SII_6731'] # narrow emission lines
Na14) 2                                         # number of Gaussian components for broad lines
Na15) 2                                         # number of Gaussian components for narrow lines
Na16) 0                                         # add Balmer continuum
Na17) 1                                         # add FeII
Na18) 0                                         # continuum model (power law)
Na19) [1.,0.5,2,0.05,0]                         # normalization of spectrum
Na20) 0                                         # add torus
Na21) [41,39,44,0.1,0]                          # log torus luminosity [erg/s]
Na22) [-0.5,-2.5,-0.25,0.05,0]                  # torus a
Na23) [0.5,0.25,1.5,0.05,0]                     # torus h
Na24) [7,5,10,0.5,0]                            # torus N0
Na25) [15,0,90,5,0]                             # torus i
Na26) [1.,0.2,5,0.1,1]                          # normalization of images_atlas
Na27) [[40,38,42,0.1,0],[1000,500,2000,10,0]]   # optional; [Lhotdust, Thotdust] (two 5-tuples)
```

## Broad Emission Lines (Na12)

Available broad lines typically include:
- **Hydrogen**: Hg (4103Å), Hb (4861Å), Ha (6563Å)
- **Helium**: HeII_4686, HeI_5876
- **Other**: Pag, Pad, Pae, OVI, Lya, NV, SiIV, CIV, CIII, MgII, Pab, Brg

## Narrow Emission Lines (Na13)

Available narrow lines typically include:
- **Hydrogen**: Hb, Ha
- **Oxygen**: [OII]_3727, [OIII]_4959, [OIII]_5007, OI_6302, OI_6365
- **Nitrogen**: NII_6549, NII_6583
- **Sulfur**: SII_6716, SII_6731

## Common Configurations

### Type 1 AGN (Unobscured)

```text
Na16) 0                                         # No Balmer continuum needed (covered by power law)
Na17) 1                                         # Include FeII (strong in Type 1)
Na18) 0                                         # Power law continuum
Na20) 0                                         # Optional torus
```

### Type 2 AGN (Obscured)

```text
Na12) []                                        # No broad lines (obscured)
Na13) ['Hb','OIII_4959','OIII_5007','NII_6583'] # Only narrow lines
Na14) 1                                         # Single narrow component
Na15) 1
Na18) 3                                         # Type 2 model
Na20) 1                                         # Include torus (important)
```

### Low-Luminosity AGN

```text
Na10) [42, 40, 44, 0.1, 1]                      # Lower L5100
Na14) 1                                         # Single broad component sufficient
Na15) 1
Na17) 0                                         # FeII often weak
```

## Tips for Good Convergence

1. **Start with simple model**: Begin with power law continuum + basic lines, then add complexity

2. **Fix black hole parameters**: For imaging fitting, Na6-Na9 are often fixed to reasonable values

3. **L5100 as anchor**: Na10 (log L5100) is often well-constrained by photometry

4. **Line components**: Start with Na14=1, Na15=1 (single component); increase only if residuals show complex line profiles

5. **FeII**: Only include if spectrum shows strong FeII emission (common in high-Eddington ratio AGN)

6. **Torus**: Important for infrared data; can be omitted for optical-only fitting

## image-only Fitting Configuration (Ia15 = 0)

GalfitS's N block was designed primarily for SED joint fitting — the AGN SED shape (set by `log L5100`, power-law index, torus, etc.) is only well-constrained when SED data is available. In **image-only mode** (phase 1, `Ia15=0`), the SED shape information is missing, and the three flux-related parameters become degenerate:

- **Na10 (log L5100)** and **Na26 (Ni_agn)** are both absolute-scale parameters — raising the SED overall (Na10) vs scaling the image multiplier (Na26) have the same effect on the rendered pixels.
- **Na11 (power-law index)** has no information when wavelength coverage is sparse (a power-law slope needs multiple wavelength points to determine).

### Minimum constrained configuration (image-only)

| Parameter | Setting | Reason |
|-----------|---------|--------|
| **Na10** | `fixed` to physical prior (42 weak AGN / 43 Seyfert) | Redundant with Na26 in image-only; anchor to physical value |
| **Na26** | `free` — the **only** flux degree of freedom | Directly linear-multiplies the AGN model image (`imm += Ni_agn * model.generate_image(...)`) |
| **Na11** | see decision table below | Depends on wavelength coverage |
| **Na18** | `4` (Arbitrary continuum) | Pairs with fixed Na11 |
| Na6-Na9 | `fixed` | BH mass/spin/Av not constrained by imaging |
| Na20 | `0` (no torus) | Torus emits in IR; irrelevant for optical-only |

### Na11 decision table

| Wavelength coverage | Na11 setting | Example |
|---------------------|--------------|---------|
| Sparse (≤3 nearby points, insufficient to anchor a slope) | **must be fixed** — slope has no information, free → hits boundary → AGN flux diverges | `[1, 0, 4, 0.1, 0]` (QSO prior α≈1) |
| Broad coverage spanning a wide λ range (e.g., griz+JHK) | can be `free` — SED shape constrained by flux ratios across bands | `[[1,0,4,0.1,1],[0.6,0,5,0.1,1]]` |

### Reference template (image-only, sparse wavelength coverage)

```text
# Minimum-risk AGN config for image-only fitting (sparse wavelength coverage)
Na1) agn                              # valid Python identifier (letters/digits/underscores only)
Na2) [<z>, ..., 0]                    # redshift fixed
Na3) <EBV>                            # Galactic EB-V
Na4) [<xcen>, -1, 1, 0.01, 0]        # x-center fixed (tie to disk via constrain)
Na5) [<ycen>, -1, 1, 0.01, 0]        # y-center fixed (tie to disk via constrain)
Na6) [7,5,10,0.1,0]                   # log BH mass fixed
Na7) [-1,-4,2,0.1,0]                  # log L/LEdd fixed
Na8) [0,0,0.99,0.01,0]                # spin fixed
Na9) [0,0,3.1,0.1,0]                  # intrinsic Av fixed
Na10) [42,41,47,0.1,0]                # log L5100 = 42 fixed (weak-AGN prior)
Na11) [1,0,4,0.1,0]                   # power-law index = 1 fixed ← key anti-degeneracy
Na12) []                              # no broad lines (imaging-only)
Na13) []                              # no narrow lines
Na14) 0                               # no broad-line Gaussian components
Na15) 0                               # no narrow-line Gaussian components
Na16) 0                               # no Balmer continuum
Na17) 0                               # no FeII
Na18) 4                               # Arbitrary continuum mode (pairs with fixed Na11)
Na19) [1,0.5,2,0.05,0]                # spectrum normalization fixed
Na20) 0                               # no torus
Na21) [41,39,44,0.1,0]                # torus params (inactive since Na20=0)
Na22) [-0.5,-2.5,-0.25,0.05,0]
Na23) [0.5,0.25,1.5,0.05,0]
Na24) [7,5,10,0.5,0]
Na25) [15,0,90,5,0]
Na26) [1.0,0.2,5,0.1,1]               # ← ONLY flux degree of freedom, free
Na27) [[36,35,42,0.1,0],[1000,500,2000,10,0]]   # optional; [Lhotdust, Thotdust] (two 5-tuples)
```

### Lyric format pitfalls when introducing an N block

These are triggered only when the N block is present (pure-P-block configs don't hit them):

- **Aa1 must be a valid Python identifier**: N-block uses Aa1 to build parameter names `Ni_<Na1>_<Aa1>`. A value with spaces like `'img list'` runs fine for pure-P configs but is rejected by lmfit once an N block exists. Replace spaces with underscores: `'img_list'`.
- **Ga2 does NOT include N-block labels**: The N block is an independent member of `model_list = Nucleus + FGstars + Galaxies` (see `gsfit.py`), not registered via Ga2. Ga2 lists P-block labels only. Adding an N-block label like `'d'` to Ga2 raises `'d' is not in list` during `read_config_file`.
- **Na27 format**: Either omit entirely, or a list of two 5-tuples `[L...,T...]` (Lhotdust, Thotdust). A bare `Na27) 0` is rejected by `check_lyric_file`.

Once the fit converges in image-only mode, transition to phase 2/3 (`Ia15=1`, SED joint fitting) to break the degeneracy — then Na10 and Na11 can be released with physical meaning.

## Degeneracy Diagnostics (when image-only AGN fit goes wrong)

If an image-only AGN fit shows any of the following symptoms in `.gssummary`, the root cause is almost always Na11 (or Na10) being left free against the guidance above. Apply the fix from the *Minimum constrained configuration* (fix Na10 and Na11, keep only Na26 free, set Na18=4):

- `agnplC` hits the Na11 upper bound (=4) — power-law index twisted to over-brighten the AGN
- Host `logNorm_<comp>_<band>` collapse below -9 (equiv. Mag > 24) — AGN has swallowed the flux
- `Ni_agn` pinned at a Na26 boundary while χ² stays huge
- reduced χ² jumps from ~0.5 to 10³-10⁴; BIC explodes by 10⁵-10⁶

Tightening Na26's range alone does **not** work — the optimizer escapes through Na11.

## When to Use AGN Component (N block)

In GalfitS, the central AGN/Nucleus is **always** configured as an N block (Na1-Na27). Do not use a P-block profile (e.g. `Pa2) psf`, `Pa2) Gaussian`) for the central AGN — GalfitS P blocks do not have a `psf` profile type.

| Situation | Recommended Approach |
|-----------|---------------------|
| Clear AGN with broad lines | Use full AGN component (N block) |
| Only point source in images | Use N block with simplified parameters (fix Na6-Na9, free Na10/Na26) |
| Optical data only | Simplified AGN (no torus, Na20=0) |
| Infrared data included | Full AGN with torus (Na20=1) |
| Spectrum fitting available | Full AGN to constrain with lines |
| Bulge Re collapsed (< 0.2 px all bands) | Replace Bulge P-block Sersic with N-block AGN |
| Bulge Re in boundary zone (0.2–0.5 px) | Try N-block AGN as competing model, compare residuals |

## See Also

- [Galaxy Configuration](galaxy.md) - How to combine AGN with host galaxy
- [Parameter Format & Combining](parameter-format.md) - General parameter information
- [Constraints](../constraints.md) - AGN parameter constraints and priors
