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
| **Na27** | Log luminosity of 1200 K black body for hot dust [erg/s] | [40, 38, 42, 0.1, 0] |

## Complete Example

```text
# Nuclei A
Na1) AGN                                        # name of the nuclei component
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
Na27) [40,38,42,0.1,0]                          # log luminosity of 1200 K black body
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

## When to Use AGN Component vs Point Source

| Situation | Recommended Approach |
|-----------|---------------------|
| Clear AGN with broad lines | Use full AGN component |
| Only point source in images | Consider Gaussian or point source profile |
| Optical data only | Simplified AGN (no torus) |
| Infrared data included | Full AGN with torus |
| Spectrum fitting available | Full AGN to constrain with lines |

## See Also

- [Galaxy Configuration](galaxy.md) - How to combine AGN with host galaxy
- [Parameter Format & Combining](parameter-format.md) - General parameter information
- [Constraints](../constraints.md) - AGN parameter constraints and priors
