# Foreground Star Configuration

Foreground stars in GalfitS are modeled as simple stellar sources with blackbody SEDs. These are typically used to model stars in our own Galaxy that appear in the field of view.

## Overview

- **Prefix**: `F` (Fa, Fb, Fc...)
- **Use Case**: Modeling foreground stars with stellar SEDs
- **Parameters**: 8 parameters (Fa1-Fa8)

## Parameter Reference

| Parameter | Description | Format | Typical Values |
|-----------|-------------|--------|----------------|
| **Fa1** | Names the foreground star (e.g., 'star', 'fg_star') | string | - |
| **Fa2** | X-center [arcsec] | [value, min, max, step, vary] | [0, -5, 5, 0.1, 1] |
| **Fa3** | Y-center [arcsec] | [value, min, max, step, vary] | [0, -5, 5, 0.1, 1] |
| **Fa4** | Effective temperature [K] | [value, min, max, step, vary] | [5500, 300, 50000, 1, 1] |
| **Fa5** | logL [Lsun] at 1 kpc | [value, min, max, step, vary] | [0, -1, 3, 0.1, 1] |
| **Fa6** | Surface gravity [cm s^-2] | [value, min, max, step, vary] | [3, 0, 5, 0.1, 1] |
| **Fa7** | Metallicity Z (0.02 = Solar) | [value, min, max, step, vary] | [0.02, 1e-5, 0.04, 0.01, 1] |
| **Fa8** | Use SED information (0=no, 1=yes) | integer | 0 or 1 |

## Complete Example

```text
# Foreground star A
Fa1) star                                       # name of the foreground star
Fa2) [3.5, 2, 5, 0.1, 1]                        # x-center [arcsec]
Fa3) [-2.1, -4, 0, 0.1, 1]                      # y-center [arcsec]
Fa4) [5500., 300, 50000, 1., 1]                 # effective temperature [K]
Fa5) [0, -1, 3, 0.1, 1]                         # logL [Lsun] at 1 kpc
Fa6) [3, 0, 5, 0.1, 1]                          # logg [cm s^-2]
Fa7) [0.02, 1e-5, 0.04, 0.01, 1]                # metallicity Z
Fa8) 1                                          # use SED information
```

## Parameter Descriptions

### Position Parameters (Fa2, Fa3)

The center coordinates are specified in arcseconds relative to the region center (defined in R2).

### Temperature (Fa4)

Effective temperature in Kelvin. This determines the stellar SED shape:

| Spectral Type | Temperature [K] | Color |
|---------------|-----------------|-------|
| O | 30000-50000 | Blue |
| B | 10000-30000 | Blue-white |
| A | 7500-10000 | White |
| F | 6000-7500 | Yellow-white |
| G | 5200-6000 | Yellow (Sun-like) |
| K | 3700-5200 | Orange |
| M | 2400-3700 | Red |

### Luminosity (Fa5)

Logarithm of luminosity in solar units (Lsun), assuming a distance of 1 kpc. The actual flux scales with distance squared.

For a star at actual distance d (in kpc):
```
Flux ∝ L / d²
```

### Surface Gravity (Fa6)

Surface gravity log(g) in cm s^-2. Typical values:
- Main sequence stars: log(g) ≈ 4-5
- Giants: log(g) ≈ 2-3
- Supergiants: log(g) ≈ 0-1

### Metallicity (Fa7)

Metallicity Z, where 0.02 is solar. Range:
- Metal-poor: Z < 0.01
- Solar: Z ≈ 0.02
- Metal-rich: Z > 0.03

### SED Flag (Fa8)

- **Fa8 = 0**: Treat as simple point source without SED
- **Fa8 = 1**: Use full stellar SED model

## Common Stellar Types

### Main Sequence Star (Sun-like)

```text
Fa1) star
Fa2) [0, -1, 1, 0.1, 1]
Fa3) [0, -1, 1, 0.1, 1]
Fa4) [5778, 5000, 6500, 100, 1]                # Solar temperature
Fa5) [0, -0.5, 0.5, 0.1, 1]                   # Solar luminosity
Fa6) [4.44, 4, 5, 0.1, 1]                     # Solar log(g)
Fa7) [0.02, 0.01, 0.03, 0.01, 1]              # Solar metallicity
Fa8) 1
```

### Red Giant

```text
Fa1) red_giant
Fa2) [5, 4, 6, 0.1, 1]
Fa3) [3, 2, 4, 0.1, 1]
Fa4) [4000, 3000, 5000, 100, 1]               # Cooler
Fa5) [2, 1, 3, 0.1, 1]                        # More luminous
Fa6) [2.5, 2, 3, 0.1, 1]                      # Lower surface gravity
Fa7) [0.02, 0.01, 0.03, 0.01, 1]
Fa8) 1
```

### Hot Blue Star

```text
Fa1) blue_star
Fa2) [-3, -4, -2, 0.1, 1]
Fa3) [4, 3, 5, 0.1, 1]
Fa4) [15000, 10000, 30000, 500, 1]            # Very hot
Fa5) [1.5, 1, 2, 0.1, 1]                      # Luminous
Fa6) [4.5, 4, 5, 0.1, 1]                      # Main sequence
Fa7) [0.02, 0.01, 0.03, 0.01, 1]
Fa8) 1
```

## When to Use Foreground Stars

| Situation | Recommended Approach |
|-----------|---------------------|
| Point-like source in field | Foreground star component |
| Source has known spectral type | Set appropriate temperature |
| Very bright star | May need to mask instead of model |
| Crowded field | Multiple foreground star components |
| Source at galaxy redshift | Use Profile component instead |

## Tips for Good Convergence

1. **Initial temperature**: Estimate from photometric colors if available

2. **Position accuracy**: Center position should be accurate to within ~1 pixel

3. **Luminosity**: For initial fit, can leave wide range; will be constrained by data

4. **Multiple stars**: If field is crowded, add multiple foreground star components

5. **Masking option**: For very bright stars that saturate or have strong diffraction spikes, consider masking instead of modeling

## Fitting Without SED (Fa8 = 0)

If `Fa8 = 0`, the star is treated as a simple point source without SED information. In this mode:

- Only spatial parameters matter (Fa2, Fa3)
- Flux is determined per-band independently
- Useful when SED modeling is not important

```text
# Simple point source (no SED)
Fa1) star
Fa2) [0, -1, 1, 0.1, 1]
Fa3) [0, -1, 1, 0.1, 1]
Fa4) [5000, 3000, 10000, 100, 0]               # Ignored when Fa8=0
Fa5) [0, -1, 1, 0.1, 0]                       # Ignored
Fa6) [4, 3, 5, 0.1, 0]                        # Ignored
Fa7) [0.02, 0.01, 0.03, 0.01, 0]              # Ignored
Fa8) 0                                          # No SED
```

## Common Issues

### Issue: Modeled star is too bright/faint

**Solution**: Adjust Fa5 (logL). Remember this is at 1 kpc, so adjust for actual distance.

### Issue: Wrong color across bands

**Solution**: Adjust Fa4 (temperature). Higher T = bluer, lower T = redder.

### Issue: Star affects galaxy fit

**Solution**:
1. Verify star position is accurate
2. Consider masking the star region with bad pixel mask
3. Use separate spectrum fitting if star contaminates galaxy spectrum

## See Also

- [Galaxy Configuration](galaxy.md) - How foreground stars relate to galaxy models
- [Data Configuration](../data-config.md) - Bad pixel masks for excluding bright stars
- [Parameter Format & Combining](parameter-format.md) - General parameter information
