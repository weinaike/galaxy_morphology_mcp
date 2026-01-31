# Mass-Size Relation (MSR)

The Mass-Size Relation (MSR) prior in GalfitS constrains galaxy size based on stellar mass, following the known correlation between these properties.

## Overview

- **Parameter Prefix**: `MSR` (MSRa, MSRb, MSRc...)
- **Purpose**: Constrain effective radius (Re) based on stellar mass
- **Type**: Astrophysical prior with intrinsic scatter
- **Reference**: van der Wel et al. (2014), Table 1

## MSR Parameters

| Parameter | Description | Format |
|-----------|-------------|--------|
| **MSRa1** | Profile name to apply MSR to | string (e.g., 'total', 'bulge', 'disk') |
| **MSRa2** | Relation parameters: `[logA, alpha, sigma]` | Array of 3 values |

### MSRa2 Elements

| Element | Description | Notes |
|---------|-------------|-------|
| **logA** | Intercept of the relation | Sets baseline size |
| **alpha** | Slope of the relation | How size scales with mass |
| **sigma** | Intrinsic scatter | `=0`: fixed; `>0`: Gaussian scatter; `<0`: disabled |

## MSR Formula

Following van der Wel et al. (2014), Table 1:

```
log(Re/kpc) = logA + α × log(M/M⊙ - 10.7) + σ
```

Where:
- **Re**: Effective radius in kiloparsecs
- **M⊙**: Stellar mass in solar masses
- **σ**: Intrinsic scatter (Gaussian)

## Configuration Examples

### Early-Type Galaxies at z ~ 0.25

From van der Wel et al. (2014):

```
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]
```

This means:
- Intercept: logA = 0.6
- Slope: α = 0.75 (size increases with mass)
- Scatter: σ = 0.1 (allows some deviation)

### Late-Type Galaxies at z ~ 0.25

```
MSRa1) disk
MSRa2) [-0.74, 0.22, 0.13]
```

Disks have different scaling than ellipticals.

### Fixed Relation (No Scatter)

For exact constraint (not recommended generally):

```
MSRa1) total
MSRa2) [0.6, 0.75, 0.0]    # sigma = 0
```

### Disabled MSR

To not apply MSR:

```
MSRa1) total
MSRa2) [0.6, 0.75, -0.1]   # sigma < 0
```

## Multiple MSRs

You can apply different MSRs to different profiles:

```
# MSR for bulge
MSRa1) bulge
MSRa2) [0.6, 0.75, 0.1]

# MSR for disk
MSRb1) disk
MSRb2) [-0.74, 0.22, 0.13]

# MSR for total galaxy
MSRc1) total
MSRc2) [0.1, 0.5, 0.15]
```

## Sigma Values Explained

| Sigma Value | Behavior |
|-------------|----------|
| **sigma < 0** | MSR is **disabled** (not applied) |
| **sigma = 0** | Exact constraint (no scatter allowed) |
| **sigma > 0** | Gaussian prior with given scatter width |

### Choosing Sigma

- **Small sigma (0.05-0.08)**: Tight constraint, high confidence in relation
- **Medium sigma (0.1-0.15)**: Standard constraint, allows natural scatter
- **Large sigma (0.2+)**: Weak constraint, barely influences fit

## MSR Values from Literature

### van der Wel et al. (2014)

| Galaxy Type | Redshift | logA | alpha | sigma |
|-------------|----------|------|-------|-------|
| Early-type | z ~ 0.25 | 0.60 | 0.75 | 0.10 |
| Early-type | z ~ 0.75 | 0.42 | 0.72 | 0.10 |
| Late-type | z ~ 0.25 | -0.74 | 0.22 | 0.13 |
| Late-type | z ~ 0.75 | -0.95 | 0.20 | 0.13 |

### Typical Usage

For most applications at low redshift (z < 0.3):

```
# For elliptical/S0 galaxies
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# For spiral galaxies
MSRa1) total
MSRa2) [-0.7, 0.2, 0.15]
```

## Complete Prior File Example

```
# MSR for early-type galaxy
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# MMR (see mass-metallicity relation)
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315]

# Other parameters...
GP) []
EB) []
```

## When to Use MSR

| Situation | Recommendation |
|-----------|---------------|
| Fitting well-studied galaxy types | **Use MSR** (improves constraints) |
| Fitting unusual/peculiar galaxies | **Disable MSR** (sigma < 0) |
| High redshift (z > 1) | **Be cautious** - relations may evolve |
| Poor initial Re estimate | **Use MSR** to guide solution |
| Testing new physics | **Increase sigma** or disable |

## Command-Line Usage

```bash
PYTHON galfitS.py --config filename.lyric --priorpath priorfile.txt
```

The prior file contains the MSR parameters (MSRa1, MSRa2).

## Common Issues

### Issue: MSR forces unrealistic size

**Symptom**: Re is far from visual estimate

**Solution**: Increase sigma or disable MSR for that component

### Issue: MSR causes convergence problems

**Symptom**: Fitting fails to converge

**Solution**: Check that stellar mass is reasonable; MSR is sensitive to mass

### Issue: Different redshift

**Symptom**: Using low-z MSR for high-z galaxy

**Solution**: Consult literature for appropriate relation at your redshift

## See Also

- [Mass-Metallicity Relation](mass-metallicity-relation.md) - MMR prior
- [Parameter Files](parameter-files.md) - Direct parameter linking
- [Sersic Profile](../model-components/profile-sersic.md) - Re parameter definition

## References

- van der Wel et al. (2014): "The MASSIVE Survey: A GEMINI/GMOS spectroscopic study of 100 brightest cluster galaxies"
