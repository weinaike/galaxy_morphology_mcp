# Parameter Format & Combining Components

This document describes the standard parameter format used throughout GalfitS and explains how to combine multiple components into a complete model.

## Parameter Format

All parameters in GalfitS follow a standard five-value array format:

```text
[initial_value, minimum_value, maximum_value, typical_step, vary_flag]
```

### Parameter Elements

| Element | Description | Example |
|---------|-------------|---------|
| **initial_value** | Starting value for the parameter | 2.5 |
| **minimum_value** | Lower bound for the parameter | 0.1 |
| **maximum_value** | Upper bound for the parameter | 10.0 |
| **typical_step** | Typical variation step (useful for MCMC) | 0.1 |
| **vary_flag** | Whether parameter is free (1) or fixed (0) | 1 |

### Examples

```text
# Free parameter with range 0.5-5.0
[2.5, 0.5, 5.0, 0.1, 1]

# Fixed parameter (will not vary during fitting)
[1.0, 0.5, 2.0, 0.1, 0]

# Parameter with very wide range
[10.0, -100, 100, 1.0, 1]

# Small variation step for precise parameter
[0.8, 0.6, 1.0, 0.01, 1]
```

## Phase-Specific Configuration

For multi-phase fitting, the `vary_flag` (5th element) controls which parameters are free in each phase.

### Profile Parameters (Sersic example)

| Parameter | Phase 1 (Image only) | Phase 2 (SED only) | Phase 3 (Joint) |
|-----------|---------------------|-------------------|-----------------|
| **Spatial** (Pa3-Pa8) | vary=1 | vary=0 | vary=1 |
| **SED** (Pa9-Pa16) | vary=0 | vary=1 | vary=1 |

### Example Configuration

```text
# Phase 1: Spatial free, SED fixed
Pa5) [2.5, 0.5, 5.0, 0.1, 1]                    # Free in Phase 1
Pa9) [[-4, -8, -1, 0.1, 0]]                     # Fixed in Phase 1

# Phase 2: Spatial fixed, SED free
Pa5) [2.5, 0.5, 5.0, 0.1, 0]                    # Fixed in Phase 2
Pa9) [[-4, -8, -1, 0.1, 1]]                     # Free in Phase 2

# Phase 3: All free
Pa5) [2.5, 0.5, 5.0, 0.1, 1]                    # Free in Phase 3
Pa9) [[-4, -8, -1, 0.1, 1]]                     # Free in Phase 3
```

## Combining Components

### Galaxy Components (G)

Galaxy components combine multiple Profile (P) components into one physical galaxy:

```text
# Define three profiles
Pa1) bulge
Pa2) sersic
... (bulge parameters)

Pb1) disk
Pb2) sersic
... (disk parameters)

Pc1) bar
Pb2) sersic_f
... (bar parameters)

# Combine into a galaxy
Ga1) host
Ga2) ['a', 'b', 'c']  # Includes all three profiles
Ga3) [0.05, 0.04, 0.06, 0.01, 0]
# ... remaining galaxy parameters
```

### Component Naming

The letters in `Ga2` must correspond to defined Profile components:

| Letter in Ga2 | Profile Component |
|---------------|-------------------|
| 'a' | Pa (Profile A) |
| 'b' | Pb (Profile B) |
| 'c' | Pc (Profile C) |
| ... | ... |

### Multiple Galaxies

You can define multiple galaxies for interacting systems:

```text
# Primary galaxy
Ga1) primary
Ga2) ['a', 'b']                                # bulge + disk

# Companion galaxy
Gb1) companion
Gb2) ['c']                                      # single component
```

## Parameter Linking via Constraint Files

For advanced parameter linking, use constraint files (.constrain):

### Constraint File Format

```python
def Update_Constraints(pardictlc):
    # Link AGN center to host galaxy center
    pardictlc['AGN_x'] = pardictlc['host_x']
    pardictlc['AGN_y'] = pardictlc['host_y']

    # Link disk position angle to bulge
    pardictlc['disk_PA'] = pardictlc['bulge_PA'] + 10
```

### See Also

- [Constraints](../constraints.md) - Full documentation on parameter constraints

## Typical Step Size Guidelines

The `typical_step` (4th element) is used by MCMC samplers:

| Parameter Type | Typical Step | Notes |
|---------------|--------------|-------|
| Position (arcsec) | 0.1 - 0.5 | Small step for precise centering |
| Re (arcsec) | 0.01 - 0.1 | Smaller for compact components |
| Sersic index n | 0.1 | Moderate steps |
| Position angle (deg) | 1 - 5 | Depends on constraint quality |
| Axis ratio | 0.01 - 0.05 | Small steps for precise flattening |
| SED parameters | 0.1 - 0.5 | Larger steps for SED space |

## Setting Reasonable Bounds

Good bounds help convergence and prevent unphysical solutions:

### Position (Pa3, Pa4)

```text
# Center of fitting region
[0, -size/2, size/2, 0.1, 1]
# where size is from Ia8
```

### Effective Radius (Pa5)

```text
# Should be positive and less than image size
[2.0, 0.1, 20.0, 0.1, 1]
```

### Sersic Index (Pa6)

```text
# Physical range
[2.0, 0.5, 8.0, 0.1, 1]
```

### Position Angle (Pa7)

```text
# Full range of angles
[0, -90, 90, 1, 1]
# or
[0, 0, 180, 1, 1]
```

### Axis Ratio (Pa8)

```text
# Must be between 0 and 1
[0.7, 0.1, 1.0, 0.01, 1]
```

## Common Pitfalls

### Issue: Parameter hits limit

**Symptom**: Parameter stays at min or max value in results

**Solution**: Expand the bounds:
```text
# Before
Pa5) [2.0, 0.5, 4.0, 0.1, 1]                  # Hit max

# After
Pa5) [4.0, 1.0, 10.0, 0.1, 1]                 # Expand max
```

### Issue: min > max

**Symptom**: Error during initialization

**Solution**: Ensure min < max for all parameters

### Issue: vary_flag inconsistent

**Symptom**: Expected parameter doesn't vary

**Solution**: Check that 5th element is 1 for free parameters

### Issue: Component letter mismatch

**Symptom**: Component not found error

**Solution**: Ensure letters in Ga2 match defined profiles:
```text
# Wrong
Ga2) ['a', 'c']                                # Pc not defined

# Correct
Ga2) ['a', 'b']                                # Pa and Pb defined
```

## Best Practices

1. **Start with reasonable bounds**: Use visual inspection or literature values

2. **Conservative in Phase 1**: Fix SED parameters, only free spatial

3. **Check results**: Verify parameters aren't hitting bounds

4. **Use constraints**: Link related parameters to reduce degeneracy

5. **Iterate refinement**: Use Phase 1 results as initial values for Phase 3

## Quick Reference Tables

### Parameter Type Summary

| Component | Prefix | Key Parameters |
|-----------|--------|----------------|
| Profile (P) | Pa, Pb, Pc... | Pa2 (type), Pa3-Pa8 (spatial), Pa9-Pa16 (SED) |
| Nuclei/AGN (N) | Na, Nb, Nc... | Na4-Na5 (position), Na6-Na10 (BH), Na11-Na18 (continuum/lines) |
| Foreground Star (F) | Fa, Fb, Fc... | Fa2-Fa3 (position), Fa4-Fa7 (stellar properties) |
| Galaxy (G) | Ga, Gb, Gc... | Ga1 (name), Ga2 (component list) |

### Profile Type Quick Reference

| Type | Pa2 Value | Parameters |
|------|-----------|------------|
| Sersic | `sersic` | Pa1-Pa16, Pa26-Pa32 |
| Fourier Sersic | `sersic_f` | Pa1-Pa24, Pa27-Pa32 |
| Ferrer Bar | `ferrer` | Profile-specific |
| Edge-on Disk | `edgeondisk` | Profile-specific |
| Gaussian Ring | `GauRing` | Profile-specific |
| Constant | `const` | Profile-specific |
| Gaussian | `Gaussian` | Profile-specific |

## See Also

- [Index](index.md) - Overview of all model components
- [Galaxy Configuration](galaxy.md) - Combining profiles into galaxies
- [Constraints](../constraints.md) - Advanced parameter linking
