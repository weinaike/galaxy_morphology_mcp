# AGN Constraints

The AGN constraints in GalfitS implement parameter relationships for Active Galactic Nuclei, including black hole mass scaling relations and emission line correlations.

## Overview

- **Parameter Prefix**: `AGN` (AGNa, AGNb, AGNc...)
- **Purpose**: Apply AGN-specific astrophysical priors
- **Type**: Astrophysical prior with probabilistic constraints
- **Use Case**: AGN fitting, especially when emission lines are weak or absent

## AGN Parameters

| Parameter | Description | Format |
|-----------|-------------|--------|
| **AGNa1** | AGN component name | string (e.g., 'myagn', 'AGN') |
| **AGNa2** | Profile for Mbh-M correlation | string (e.g., 'total', 'bulge', 'none') |
| **AGNa3** | Mbh-M relation `[logA, alpha, sigma]` | Array of 3 values |
| **AGNa4** | Hα flux to L5100 `[intercept, slope, sigma]` | Array of 3 values |
| **AGNa5** | Hβ flux to L5100 `[intercept, slope, sigma]` | Array of 3 values |
| **AGNa6** | List of broad emission lines | List of strings |
| **AGNa7** | Broad line ratios to Hβ | Array of floats |
| **AGNa8** | [OIII] flux relation parameters | Array of 3 values |
| **AGNa9** | List of narrow emission lines | List of strings |
| **AGNa10** | Narrow line ratios to [OIII] | Array of floats |
| **AGNa11** | Profile for center linking | string or 'none' |
| **AGNa12** | R_FeII value (FeII/Hβ ratio) | float |

---

## Black Hole Mass - Stellar Mass Relation

### Formula

```
log(Mbh) = logA + α × logM(stellar) + σ
```

### Mbh-Mbulge Relation

From Kormendy & Ho (2013), Equation 10:

```
AGNa1) myagn
AGNa2) bulge           # Apply to bulge component
AGNa3) [-4.18, 1.17, 0.28]
```

### Mbh-Mtotal Relation

From Greene et al. (2020), Table 6:

```
AGNa1) myagn
AGNa2) total           # Apply to total galaxy component
AGNa3) [-7.0, 1.39, 0.79]
```

### When to Use Each

| Situation | Use |
|-----------|-----|
| Classical bulge present | Mbh-Mbulge (AGNa2 = 'bulge') |
| No distinct bulge | Mbh-Mtotal (AGNa2 = 'total') |
| Pseudobulge | May need different relation |
| No constraint needed | AGNa2 = 'none' |

---

## Broad Emission Line Constraints

### Hα and Hβ to L5100 Correlation

From Greene & Ho (2005), links broad line luminosity to continuum:

```
log(L_Hα) = a × log(L_5100) + b + σ
log(L_Hβ) = a × log(L_5100) + b + σ
```

#### Configuration

```
AGNa4) [-46.19, 1.157, 0.2]    # Hα: intercept=-46.19, slope=1.157, sigma=0.2
AGNa5) [-45.70, 1.133, 0.2]    # Hβ: intercept=-45.70, slope=1.133, sigma=0.2
```

#### When Useful

This is particularly useful for **imaging-only fitting** where:
- Emission lines are not directly observed
- Broad lines contribute significantly to broadband flux
- Need to constrain AGN luminosity from photometry

### Other Broad Lines

From Stern & Laor (2012/2013), based on SDSS type 1 AGNs:

```
AGNa6) ['Hg', 'Pag', 'Pad', 'Pae', 'OVI', 'Lya', 'NV', 'SiIV', 'CIV', 'HeII', 'CIII', 'MgII', 'Pab', 'Brg']
AGNa7)  [0.35, 0.4, 0.225, 0.15, 1.48, 7.41, 1.48, 1.19, 3.70, 1.07, 1.67, 1.67, 1.0, 0.8]
```

**AGNa7** sets luminosity ratios to broad Hβ.

#### Broad Line Reference

| Line | AGNa7 Value | Ratio to Hβ |
|------|-------------|--------------|
| Hγ (Hg) | 0.35 | Hγ/Hβ = 0.35 |
| Paγ | 0.4 | Paγ/Hβ = 0.4 |
| Pδ | 0.225 | Pδ/Hβ = 0.225 |
| Pε | 0.15 | Pε/Hβ = 0.15 |
| He II | 1.48 | HeII/Hβ = 1.48 |
| Mg II | 1.0 | MgII/Hβ = 1.0 |
| Pβ | 0.8 | Pβ/Hβ = 0.8 |

---

## [OIII] λ5007 Constraints

From Stern & Laor (2012/2013):

```
AGNa8) [-34.05, 0.833, 0.36]    # [OIII] relation parameters
AGNa9) ['Hb', 'OIII_4959', 'OI_6302', 'OI_6365', 'Ha', 'NII_6583', 'NII_6549', 'SII_6716', 'SII_6731']
AGNa10) [0.214, 0.336, 0.03, 0.03, 0.685, 0.302, 0.102, 0.13, 0.13]
```

**AGNa10** sets narrow line luminosity ratios to [OIII] λ5007.

#### Narrow Line Reference

| Line | AGNa10 Value | Ratio to [OIII] |
|------|-------------|-----------------|
| Hβ | 0.214 | Hβ/[OIII] = 0.214 |
| [OIII] 4959 | 0.336 | [OIII]4959/[OIII]5007 ≈ 1/3 |
| OI 6302 | 0.03 | OI/[OIII] = 0.03 |
| OI 6365 | 0.03 | OI/[OIII] = 0.03 |
| Hα | 0.685 | Hα/[OIII] = 0.685 |
| NII 6583 | 0.302 | NII/[OIII] = 0.302 |
| NII 6549 | 0.102 | NII/[OIII] = 0.102 |
| SII 6716 | 0.13 | SII/[OIII] = 0.13 |
| SII 6731 | 0.13 | SII/[OIII] = 0.13 |

---

## Center Linking

Link AGN position to host galaxy component:

```
AGNa11) total     # Link AGN center to 'total' profile
# or
AGNa11) none      # No center linking
```

### When to Use

| Situation | AGNa11 |
|-----------|--------|
| AGN at galaxy center | Use profile name ('total', 'bulge') |
| Offset AGN | Use 'none' |
| Merging system | Use 'none' |

---

## FeII Pseudo-continuum

From Boroson & Green (1992):

```
AGNa12) 0.6    # R_FeII = FeII/Hβ
```

### R_FeII Definition

```
R_FeII = FeII(4434-4684) / Hβ(4847-4887)
```

### Typical Values

| AGN Type | R_FeII |
|----------|--------|
| Population A | R_FeII > 1 |
| Population B | R_FeII < 0.5 |
| Typical AGN | 0.5 - 1.0 |

---

## Complete AGN Prior Example

```
# AGN constraints
AGNa1) myagn
AGNa2) total               # Mbh-Mtotal relation
AGNa3) [-7.0, 1.39, 0.79]  # Greene et al. 2020
AGNa4) [-46.19, 1.157, 0.2]   # Hα to L5100
AGNa5) [-45.70, 1.133, 0.2]   # Hβ to L5100
AGNa6) ['Hg', 'Pag', 'Pad', 'Pae', 'OVI', 'Lya', 'NV', 'SiIV', 'CIV', 'HeII', 'CIII', 'MgII', 'Pab', 'Brg']
AGNa7) [0.35, 0.4, 0.225, 0.15, 1.48148148, 7.40740741, 1.48148148, 1.18518519, 3.7037037, 1.07407407, 1.66666667, 1.66666667, 1.0, 0.8]
AGNa8) [-34.05, 0.833, 0.36]  # [OIII] relation
AGNa9) ['Hb', 'OIII_4959', 'OI_6302', 'OI_6365', 'Ha', 'NII_6583', 'NII_6549', 'SII_6716', 'SII_6731']
AGNa10) [0.214, 0.336, 0.03, 0.03, 0.685, 0.302, 0.102, 0.13, 0.13]
AGNa11) total              # Link AGN to host center
AGNa12) 0.6                 # FeII/Hβ ratio
```

---

## When to Use AGN Constraints

| Situation | Recommended Constraints |
|-----------|------------------------|
| **Imaging-only fitting** | AGNa4, AGNa5 (broad lines affect broadband) |
| **Spectrum fitting** | Use all parameters as appropriate |
| **Type 1 AGN** | Use all AGN constraints |
| **Type 2 AGN** | AGNa2 = 'none', modify AGNa6/AGNa9 |
| **No visible AGN** | Don't use AGN component at all |
| **Weak AGN** | Use larger sigma values |

---

## Common Configurations by AGN Type

### Type 1 AGN (Broad Lines Visible)

```
AGNa2) total or bulge      # Mbh-M relation
AGNa6) ['Hg', 'Hb', 'Ha']  # Include broad lines
AGNa9) ['Hb', 'OIII...']   # Include narrow lines
AGNa11) total             # Link to host
AGNa12) 0.6               # FeII included
```

### Type 2 AGN (Only Narrow Lines)

```
AGNa2) total or bulge
AGNa6) []                  # No broad lines
AGNa9) ['Hb', 'OIII...']   # Only narrow lines
AGNa11) total
AGNa12) 0.6               # May or may not include FeII
```

### Low-Luminosity AGN

```
AGNa2) total              # Mbh-M relation still holds
AGNa3) [-7.0, 1.39, 1.0]  # Larger sigma
AGNa4) [-46.19, 1.157, 0.5]  # Larger scatter
AGNa5) [-45.70, 1.133, 0.5]
AGNa12) 0.2               # Weaker FeII
```

---

## Command-Line Usage

```bash
PYTHON galfitS.py --config filename.lyric --priorpath priorfile.txt
```

---

## See Also

- [Nuclei/AGN](../model-components/nuclei-agn.md) - AGN component configuration
- [Parameter Files](parameter-files.md) - Direct parameter linking
- [Mass-Size Relation](mass-size-relation.md) - MSR prior

## References

- Kormendy & Ho (2013): "Scaling Relations from Bulges to Supermassive Black Holes"
- Greene et al. (2020): "The Mass of Black Holes in Star-Forming Galaxies"
- Greene & Ho (2005): "Active Galactic Nuclei in the Sloan Digital Sky Survey. I. Sample"
- Stern & Laor (2012): "Type 1 AGN at low z - narrow-line ratios"
- Stern & Laor (2013): "Type 1 AGN at low z - [OIII]λ5007 luminosity"
- Boroson & Green (1992): "The Stellar Population of Broad-Line Active Galactic Nuclei"
