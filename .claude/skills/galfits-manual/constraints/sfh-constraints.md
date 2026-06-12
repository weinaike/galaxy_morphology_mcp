# Star Formation History (SFH) Constraints

The Star Formation History (SFH) constraints in GalfitS allow you to apply priors on the SFH parameters of galaxy components.

## Overview

- **Parameter Prefix**: `SFH` (SFHa, SFHb, SFHc...)
- **Purpose**: Constrain SFH parameters with prior distributions
- **Type**: Astrophysical prior with Gaussian scatter on parameters
- **Use Case**: When external SFH information is available or to constrain degeneracy

## SFH Parameters

| Parameter | Description | Format |
|-----------|-------------|--------|
| **SFHa1** | Profile name to apply SFH constraint to | string (e.g., 'total', 'disk') |
| **SFHa2** | SFH type | string ('exponential', etc.) |
| **SFHa3** | Initial values and ranges for SFH parameters | Array of `[value, min, max, step, vary]` for each parameter |
| **SFHa4** | Prior values and sigmas for SFH parameters | Array of `[prior_value, prior_sigma]` for each parameter |

## Exponential SFH

The most common SFH type is exponential:

### Formula

```
SFR(t) = SFR0 × exp((t - t0) / tau)
```

Where:
- **SFR0**: Current star formation rate (log scale)
- **tau**: e-folding timescale
- **t0**: Time offset / lookback time

### Parameters

| Parameter | Description | Typical Range |
|-----------|-------------|---------------|
| **logSFR0** | Log of current SFR | -1 to 2 (linear) |
| **tau** | e-folding timescale [Gyr] | 0.1 to 10 Gyr |
| **t0** | Time offset [Gyr] | 0 to 1 Gyr |

### Configuration Example

```
SFHa1) total
SFHa2) exponetial
SFHa3) [[1., -0.5, 2, 0.1, 1],      # logSFR0: initial=1, range=[-0.5, 2]
         [0.15, -0.2, 0.6, 0.1, 1],   # tau: initial=0.15, range=[-0.2, 0.6]
         [0.65, 0., 1.3, 0.1, 1]]     # t0: initial=0.65, range=[0, 1.3]
SFHa4) [[1.04, 0.16],               # logSFR0 prior: value=1.04, sigma=0.16
         [0.15, 0.04],                # tau prior: value=0.15, sigma=0.04
         [0.65, 0.18]]                # t0 prior: value=0.65, sigma=0.18
```

## SFHa3 vs SFHa4

| Parameter | SFHa3 | SFHa4 |
|-----------|-------|-------|
| **Purpose** | Initial values and ranges | Prior distribution |
| **Format** | `[value, min, max, step, vary]` | `[prior_mean, prior_sigma]` |
| **Usage** | Defines fitting bounds | Gaussian prior during fitting |

### Understanding SFHa3 and SFHa4

For each SFH parameter:

```
SFHa3) [initial_value, min, max, step, vary_flag]
SFHa4) [prior_mean, prior_sigma]
```

The prior (SFHa4) influences the likelihood during fitting:

```
likelihood ∝ exp(-((parameter - prior_mean) / prior_sigma)²)
```

## Multiple SFH Constraints

Apply different SFH constraints to different components:

```
# SFH for disk component
SFHa1) disk
SFHa2) exponetial
SFHa3) [[1., -0.5, 2, 0.1, 1], [0.15, -0.2, 0.6, 0.1, 1], [0.65, 0., 1.3, 0.1, 1]]
SFHa4) [[1.04, 0.16], [0.15, 0.04], [0.65, 0.18]]

# SFH for bulge (typically quiescent)
SFHb1) bulge
SFHb2) exponetial
SFHb3) [[-1., -2, 0, 0.1, 1], [0.5, 0.1, 2, 0.1, 1], [0.5, 0, 1, 0.1, 1]]
SFHb4) [[-1.0, 0.2], [0.5, 0.1], [0.5, 0.1]]
```

## Complete Prior File Example

```
# SFH for total galaxy
SFHa1) total
SFHa2) exponetial
SFHa3) [[1.,-0.5,2,0.1,1],[0.15,-0.2,0.6,0.1,1],[0.65,0.,1.3,0.1,1]]
SFHa4) [[1.04,0.16],[0.15,0.04],[0.65,0.18]]

# MSR
MSRa1) total
MSRa2) [0.6, 0.75, 0.1]

# MMR
MMRa1) total
MMRa2) [28.0974, -7.23631, 0.850344, -0.0318315, 0.1]

# Other parameters...
GP) []
EB) []
```

## When to Use SFH Constraints

| Situation | Recommendation |
|-----------|---------------|
| Good external SFH data available | **Use SFH** with tight priors |
| Fitting fails to converge | **Use SFH** to constrain degeneracy |
| Exploring SFH space | **Don't use SFH** or use very weak priors |
| Spectrum fitting | **Often useful** to constrain SFH |
| Imaging-only fitting | **May not need SFH** constraints |

## Choosing Prior Values

### From External SFH Fitting

If you have SFH results from other codes (e.g., PROSPECTOR, FAST):

```
SFHa4) [[external_logSFR0, external_error],
         [external_tau, external_error],
         [external_t0, external_error]]
```

### Conservative Priors

When uncertain, use wider priors:

```
SFHa4) [[1.0, 0.5],      # Large sigma for logSFR0
         [1.0, 0.5],      # Large sigma for tau
         [0.5, 0.3]]      # Large sigma for t0
```

## Command-Line Usage

```bash
PYTHON galfitS.py --config filename.lyric --priorpath priorfile.txt
```

## SFH in Profile Components

The SFH constraint relates to Profile SED parameters:

| SFH Parameter | Related Profile Parameter |
|---------------|---------------------------|
| logSFR0 | Pa9 (sSFR) |
| tau | Related to Pa10 (burst age) |
| t0 | Related to overall SFH shape |

## Common Issues

### Issue: SFH prior forces unrealistic values

**Symptom**: SFR or tau seems unphysical

**Solution**: Check prior values; increase sigma in SFHa4

### Issue: Degeneracy not resolved

**Symptom**: Multiple SFH parameter combinations fit equally well

**Solution**: Use SFH constraints with reasonable priors based on galaxy type

### Issue: Wrong SFH type

**Symptom**: Exponential SFH doesn't describe the galaxy

**Solution**: Consider other SFH types or disable SFH constraint

## SFH Types Summary

While 'exponential' is most common, other types may be supported:

| Type | Description | Use Case |
|------|-------------|----------|
| `exponential` | SFR ∝ exp(-t/τ) | Most galaxies |
| `delayed` | SFR ∝ t × exp(-t/τ) | Specific SFH scenarios |
| `burst` | Single burst | Post-starburst galaxies |
| `bins` | Binned SFH | Complex SFH reconstruction |

## See Also

- [Mass-Size Relation](mass-size-relation.md) - MSR prior
- [Mass-Metallicity Relation](mass-metallicity-relation.md) - MMR prior
- [Sersic Profile](../model-components/profile-sersic.md) - SED parameters (Pa9-Pa16)
- [Parameter Files](parameter-files.md) - Direct parameter linking
