# Energy Balance (EB)

The Energy Balance parameter in GalfitS ensures that the stellar continuum luminosity absorbed in the UV/optical band equals the dust luminosity, maintaining physical consistency in the SED model.

## Overview

- **Parameter**: `EB`
- **Purpose**: Enforce energy balance between stellar and dust components
- **Type**: Physical constraint
- **Reference**: CIGALE SED fitting code

## What is Energy Balance?

### Physical Principle

In galaxy SED modeling, energy balance means:

```
L_absorbed(UV/optical) = L_emitted(IR)
```

Stellar light that is absorbed by dust must be re-emitted in the infrared by dust.

### Without Energy Balance

Without this constraint:
- Stellar and dust components can be fitted independently
- May lead to unphysical solutions (e.g., too much dust absorption, insufficient IR emission)

### With Energy Balance

When enabled:
- Dust luminosity is coupled to absorbed stellar light
- Ensures physically consistent SED
- More realistic modeling of galaxy energy budget

## EB Parameter Format

```
EB) ['profilename1', 'profilename2', ...]
```

Lists profile names to apply energy balance to.

## Configuration Examples

### Apply to All Components

```
# Apply energy balance to 'total' profile
EB) ['total']
```

### Apply to Multiple Components

```
# Apply energy balance to multiple profiles
EB) ['bulge', 'disk']
```

### No Energy Balance (Default)

```
# Energy balance disabled
EB) []
```

## Complete Prior File Example

```
# MSR
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# MMR
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]

# Energy balance
EB) ['total']

# Gaussian priors
GP) []
```

## When to Use Energy Balance

| Situation | Recommendation |
|-----------|---------------|
| **Full UV-optical-IR SED fitting** | **Use EB** - ensures consistency |
| **Optical-only fitting** | **Don't use EB** - no IR data to verify |
| **Strong dust content** | **Use EB** - important for dusty galaxies |
| **Quenched galaxies** | **May not need EB** - little dust |
| **Testing methodology** | **Skip EB** - adds complexity |

## Energy Balance in Different Galaxy Types

### Star-Forming Galaxies

**Recommended**: Use energy balance

```
EB) ['total']    # or ['disk'] if modeling separately
```

Reason: Significant dust absorption and IR emission.

### Elliptical Galaxies

**Optional**: Energy balance less critical

```
EB) []           # Often not needed for gas-poor systems
```

Reason: Little dust to absorb UV/optical light.

### AGN Host Galaxies

**Recommended**: Use energy balance for host component

```
EB) ['host']     # Apply to host, not AGN
```

The AGN itself has separate dust modeling (torus, hot dust).

## How Energy Balance Works

### Dust Model Parameters

Energy balance affects DL2014 dust model parameters (Pa28-Pa32):

| Parameter | Description |
|-----------|-------------|
| **Pa28** | Log cold dust mass |
| **Pa29** | Umin (minimum radiation field) |
| **Pa30** | qPAH (PAH fraction) |
| **Pa31** | Alpha (radiation field slope) |
| **Pa32** | Gamma (illuminated fraction) |

### Calculation

1. GalfitS calculates stellar light absorbed by dust
2. Sets dust luminosity equal to absorbed amount
3. Adjusts dust parameters to satisfy energy balance

## EB vs Other Constraints

| Feature | EB | MSR | MMR |
|---------|-----|-----|-----|
| **Type** | Physical constraint | Astrophysical prior | Astrophysical prior |
| **Components affected** | Dust parameters | Structural parameters | Metallicity parameter |
| **Can combine with** | All other constraints | MSR, MMR, GP | MSR, MMR, GP |

## Combining EB with Other Constraints

Energy balance works with all other constraints:

```
# MSR: Constrains size
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# MMR: Constrains metallicity
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]

# EB: Ensures energy consistency
EB) ['total']

# GP: Gaussian priors on specific parameters
GP) ['bulge_n', 'disk_n']
```

## Command-Line Usage

```bash
PYTHON galfitS.py --config filename.lyric --priorpath priorfile.txt
```

The prior file contains the `EB` parameter.

## Impact on Fitting

### Benefits

1. **Physical consistency**: Ensures realistic energy budget
2. **Better dust constraints**: Dust parameters more physically meaningful
3. **Improved degeneracy breaking**: Links stellar and dust emission

### Potential Drawbacks

1. **Increased complexity**: More parameters to fit
2. **Longer convergence**: May require more iterations
3. **Not always needed**: For optical-only data, EB has little effect

## Common Issues

### Issue: Fitting fails with EB enabled

**Symptom**: Convergence problems or errors

**Solutions**:
- Check that IR data is available
- Verify dust parameters have reasonable bounds
- Consider removing EB if not essential

### Issue: Dust parameters hit limits

**Symptom**: Pa28-Pa32 at min or max values

**Solution**: Expand bounds or use weaker EB constraint

### Issue: No effect on fit

**Symptom**: Enabling EB doesn't change results

**Possible causes**:
- No IR data to constrain dust emission
- Dust contribution minimal compared to stellar

## Best Practices

1. **Use with full SED**: EB most useful with UV-to-IR data
2. **Check data coverage**: Ensure IR coverage for dust emission
3. **Start without EB**: First fit without EB, then enable
4. **Verify reasonableness**: Check that dust parameters are physical

## See Also

- [Sersic Profile](../model-components/profile-sersic.md) - Dust parameters (Pa26-Pa32)
- [Mass-Size Relation](mass-size-relation.md) - MSR prior
- [Mass-Metallicity Relation](mass-metallicity-relation.md) - MMR prior
- [Gaussian Priors](gaussian-priors.md) - GP parameter

## References

- CIGALE SED fitting code: Energy balance assumption
- Noll et al. (2009): "Analysis of the mid-infrared spectra of galaxies"
