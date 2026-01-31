# Mass-Metallicity Relation (MMR)

The Mass-Metallicity Relation (MMR) prior in GalfitS constrains galaxy metallicity based on stellar mass, following the well-known correlation between these properties.

## Overview

- **Parameter Prefix**: `MMR` (MMRa, MMRb, MMRc...)
- **Purpose**: Constrain metallicity (Z) based on stellar mass
- **Type**: Astrophysical prior with intrinsic scatter
- **Reference**: Kewley & Ellison (2008), Kewley & Dopita (2002)

## MMR Parameters

| Parameter | Description | Format |
|-----------|-------------|--------|
| **MMRa1** | Profile name to apply MMR to | string (e.g., 'total', 'bulge') |
| **MMRa2** | Polynomial coefficients: `[a0, a1, a2, a3, sigma]` | Array of 5 values |

### MMa2 Elements

| Element | Description | Notes |
|---------|-------------|-------|
| **a0, a1, a2, a3** | Polynomial coefficients | Define the relation shape |
| **sigma** | Intrinsic scatter | `=0`: fixed; `>0`: Gaussian scatter; `<0`: disabled |

## MMR Formula

Adopting the Kewley & Ellison (2008) method with KD02 (Kewley & Dopita 2002) metallicity calibration:

```
log(O/H) + 12 = a0 + a1×logM + a2×logM² + a3×logM³ ± σ
```

Where:
- **O/H**: Oxygen abundance (metallicity)
- **M**: Stellar mass in solar masses
- **σ**: Intrinsic scatter

## Configuration Examples

### Kewley & Ellison (2008) with KD02 Method

For star-forming galaxies:

```
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]
```

This means:
- Polynomial: 28.0974 - 7.24×logM + 0.85×logM² - 0.03×logM³
- Scatter: σ = 0.1 (allows some deviation)

### Fixed Relation (No Scatter)

```
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.0]
```

### Disabled MMR

```
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, -0.1]   # sigma < 0
```

## Multiple MMRs

Apply different MMRs to different components:

```
# MMR for star-forming component
MMRa1) disk
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]

# MMR for bulge (typically higher metallicity)
MMRb1) bulge
MMRb2) [29.0, -7.0, 0.8, -0.03, 0.08]
```

## Sigma Values Explained

| Sigma Value | Behavior |
|-------------|----------|
| **sigma < 0** | MMR is **disabled** |
| **sigma = 0** | Exact constraint |
| **sigma > 0** | Gaussian prior with scatter |

### Choosing Sigma

For metallicity relations:
- **Small sigma (0.05-0.08)**: Tight constraint
- **Medium sigma (0.1-0.15)**: Standard (natural scatter of relation)
- **Large sigma (0.2+)**: Weak constraint

## MMR Values from Literature

### Kewley & Ellison (2008) - KD02 Method

```
[28.0974, -7.23631, 0.850344, -0.0318315]
```

### Alternative Calibration Methods

Different metallicity calibrations yield different coefficients. Common alternatives:

| Method | a0 | a1 | a2 | a3 |
|--------|-----|----|----|----|
| KD02 | 28.097 | -7.236 | 0.850 | -0.032 |
| PP04 | ~similar | varies | varies | varies |

## Complete Prior File Example

```
# MMR for star-forming galaxy
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]

# MSR (see mass-size relation)
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# Other parameters...
GP) []
EB) []
```

## When to Use MMR

| Situation | Recommendation |
|-----------|---------------|
| Star-forming galaxies | **Use MMR** (well-established relation) |
| Elliptical galaxies | **Use with caution** (may have different relation) |
| AGN hosts | **Use MMR** (helps constrain stellar population) |
| Low metallicity systems | **Disable MMR** (not applicable) |
| Dwarfs | **Use with caution** (relation may differ) |

## MMR + MSR Combination

MMR is often used together with MSR:

```
# Apply both mass-size and mass-metallicity relations
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]
```

This provides a strong physical prior linking size, mass, and metallicity.

## Command-Line Usage

```bash
PYTHON galfitS.py --config filename.lyric --priorpath priorfile.txt
```

## Metallicity Parameter Context

In GalfitS Profile components:

- **Pa11**: Metallicity Z (where Z = 0.02 is solar)

The MMR prior constrains Pa11 based on the stellar mass (Pa14).

## Common Issues

### Issue: Metallicity goes to bounds

**Symptom**: Pa11 stuck at min or max value

**Solution**: Check that stellar mass is reasonable; increase MMR sigma

### Issue: Unphysical metallicity

**Symptom**: Z < 0 or Z > 0.05

**Solution**: Constrain Pa11 bounds or increase MMR sigma

### Issue: MMR not appropriate

**Symptom**: Using star-forming relation for elliptical

**Solution**: Disable MMR or find appropriate relation for galaxy type

## See Also

- [Mass-Size Relation](mass-size-relation.md) - MSR prior
- [Parameter Files](parameter-files.md) - Direct parameter linking
- [Sersic Profile](../model-components/profile-sersic.md) - Metallicity parameter (Pa11)

## References

- Kewley & Ellison (2008): "Mass-metallicity relation of galaxies: aperture correction, metallicity gradients, and scatter"
- Kewley & Dopita (2002): "Using photoionization models to interpret emission-line flux ratios"
