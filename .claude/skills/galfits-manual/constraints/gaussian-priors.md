# Gaussian Priors (GP)

The Gaussian Priors parameter allows you to apply Gaussian (normal) distribution priors to specific parameters instead of uniform priors.

## Overview

- **Parameter**: `GP`
- **Purpose**: Apply Gaussian priors to specific parameters
- **Type**: Prior constraint (probabilistic)
- **Use Case**: When you have prior knowledge about parameter values

## What Are Gaussian Priors?

### Uniform Prior (Default)

By default, parameters have uniform priors within their bounds:

```
P(parameter) ∝ 1    for min ≤ parameter ≤ max
```

All values within bounds are equally likely.

### Gaussian Prior

With Gaussian priors, values closer to the mean are more likely:

```
P(parameter) ∝ exp(-((parameter - μ)² / (2σ²)))
```

Where:
- **μ**: Mean value (specified in prior)
- **σ**: Standard deviation (spread of prior)

## GP Parameter Format

```
GP) ['param1', 'param2', 'param3', ...]
```

Lists parameter names to apply Gaussian priors to.

## How It Works

1. **Identify parameters**: List parameters that need Gaussian priors in `GP)`
2. **Specify prior values**: In the parameter file or initial config, set reasonable initial values
3. **Apply during fitting**: GalfitS uses Gaussian likelihood for these parameters

### Parameter Names

Common parameter names include:

| Category | Examples |
|----------|----------|
| **Stellar mass** | `total_logM`, `bulge_logM` |
| **Sersic index** | `bulge_n`, `disk_n` |
| **Effective radius** | `bulge_Re`, `disk_Re` |
| **Metallicity** | `total_Z`, `bulge_Z` |
| **Dust extinction** | `total_Av` |

Exact names depend on your component names.

## Configuration Examples

### Single Parameter with Gaussian Prior

```
# Apply Gaussian prior to stellar mass
GP) ['total_logM']
```

During fitting, `total_logM` will have a Gaussian prior centered at its initial value with an implicit sigma.

### Multiple Parameters

```
# Apply Gaussian priors to multiple parameters
GP) ['bulge_n', 'disk_n', 'total_logM']
```

### No Gaussian Priors (Default)

```
# All parameters have uniform priors
GP) []
```

## When to Use Gaussian Priors

| Situation | Use Gaussian Prior? |
|-----------|---------------------|
| Have photometric redshift estimate | **Yes** - Apply to redshift parameter |
| Know approximate stellar mass | **Yes** - Apply to logM parameter |
| High-quality size measurement | **Yes** - Apply to Re parameter |
| Exploring new parameter space | **No** - Uniform priors more appropriate |
| Poor prior knowledge | **No** - Uniform priors avoid bias |

## Complete Prior File Example

```
# MSR
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# MMR
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]

# Gaussian priors on specific parameters
GP) ['bulge_n', 'disk_Re', 'total_logM']

# Energy balance
EB) []
```

## Implicit vs Explicit Priors

### Implicit Priors (GP)

When using `GP)`, the prior mean is the **initial value** from the parameter file:

```
# In config or .params file
bulge_n  4.0  1.0  8.0  0.1  True   # Initial value = 4.0

# In prior file
GP) ['bulge_n']                          # Prior: N(4.0, σ_default)
```

The standard deviation (σ) is determined internally by GalfitS.

### Explicit Priors (MSR, MMR, etc.)

For MSR, MMR, AGN, the prior mean and sigma are **explicitly specified**:

```
MSRa2) [0.6, 0.75, 0.1]    # μ=0.6, α=0.75, σ=0.1
```

## GP vs MSR/MMR

| Feature | GP | MSR/MMR |
|---------|-----|---------|
| **Prior mean** | Initial value | Explicitly specified |
| **Sigma** | Default (implicit) | Explicitly specified |
| **Relation** | None | Physical relation (M-Re, M-Z) |
| **Use case** | General prior | Astrophysical relation |

## Combining GP with Other Priors

GP works together with other priors:

```
# MSR: Constrains Re based on M
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# GP: Additional Gaussian prior on Re
GP) ['total_Re']

# Result: Re constrained by both MSR and GP
```

## Command-Line Usage

```bash
PYTHON galfitS.py --config filename.lyric --priorpath priorfile.txt
```

The prior file contains the `GP` parameter.

## Best Practices

1. **Use meaningful parameters**: Apply GP to parameters with physical meaning
2. **Consider initial values**: Set reasonable initial values as these become the prior means
3. **Don't over-constrain**: Avoid applying GP to too many parameters
4. **Document your priors**: Keep track of which parameters have GP and why

## Common Issues

### Issue: Prior dominates likelihood

**Symptom**: Result stays near initial value regardless of data

**Solution**: Check if prior is too tight; consider removing GP for that parameter

### Issue: Parameter name not found

**Symptom**: GP has no effect on parameter

**Solution**: Verify exact parameter name in `.params` file; component names matter

### Issue: Multiple components with same name

**Symptom**: GP applies to wrong component

**Solution**: Use full parameter names including component identifier

## When NOT to Use Gaussian Priors

| Situation | Reason |
|-----------|--------|
| First exploration of parameter space | Uniform priors avoid bias |
| Poor initial value estimate | Gaussian prior will bias toward wrong value |
| Strong degeneracy | May not help; consider MSR/MMR instead |
| Testing methodology | Uniform priors allow more exploration |

## See Also

- [Mass-Size Relation](mass-size-relation.md) - MSR prior (explicit Gaussian)
- [Mass-Metallicity Relation](mass-metallicity-relation.md) - MMR prior (explicit Gaussian)
- [AGN Constraints](agn-constraints.md) - AGN-specific priors
- [Parameter Files](parameter-files.md) - Direct parameter linking
