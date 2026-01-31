# Sersic Profile

The Sersic profile is the standard profile for modeling galaxy components like bulges and disks. It is defined by setting `Pa2) sersic`.

## Overview

- **Profile Type**: `sersic`
- **Use Case**: Standard galaxy bulge/disk, elliptical profile, any axisymmetric galaxy component
- **Parameters**: 32 parameters total (Pa1-Pa16, Pa26-Pa32)

## Parameter Reference

### Basic Parameters (Pa1-Pa8)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Pa1** | Component name (e.g., 'bulge', 'disk', 'core') | string |
| **Pa2** | Profile type - must be `sersic` | 'sersic' |
| **Pa3** | X-center [arcsec] relative to region center | [0, -5, 5, 0.1, 1] |
| **Pa4** | Y-center [arcsec] relative to region center | [0, -5, 5, 0.1, 1] |
| **Pa5** | Effective radius (Re) [arcsec] | [2.3, 0.02, 4.6, 0.01, 1] |
| **Pa6** | Sersic index n | [4, 1, 6, 0.1, 1] |
| **Pa7** | Position angle (PA) [degrees] | [0, -90, 90, 1, 1] |
| **Pa8** | Axis ratio (q = b/a) | [0.8, 0.6, 1, 0.01, 1] |

### SED Parameters (Pa9-Pa16)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Pa9** | Specific star formation rate (sSFR) of star-forming component | [[-4, -8, -1, 0.1, 1]] |
| **Pa10** | Burst stellar age [Gyr] | [[5, 0.01, 11, 0.1, 1]] |
| **Pa11** | Metallicity Z (0.02 = Solar) | [0.02, 0.001, 0.04, 0.001, 1] |
| **Pa12** | Dust extinction Av [mag] | [0.7, 0.3, 5.1, 0.1, 1] |
| **Pa13** | Stellar velocity dispersion [km/s] | [100, 40, 200, 1, 0] |
| **Pa14** | Log stellar mass [solar mass] | [10.14, 8.5, 12, 0.1, 1] |
| **Pa15** | Star formation history type | 'burst', 'conti', or 'bins' |
| **Pa16** | Nebular ionization parameter logU | [-2, -4, -2, 0.1, 0] |

### Dust Model Parameters (Pa26-Pa32)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Pa26** | Amplitude of 2175A bump on extinction curve | [3, 0, 5, 0.1, 0] |
| **Pa27** | SED model type (0: full, 1: stellar only, 2: nebular only, 3: dust only, 4: full with dust) | 0 |
| **Pa28** | Log cold dust mass | [8.14, 4.5, 10, 0.1, 1] |
| **Pa29** | Umin - minimum radiation field | [1.0, 0.1, 50, 0.1, 0] |
| **Pa30** | qPAH - mass fraction of PAH | [1.0, 0.47, 7.32, 0.1, 0] |
| **Pa31** | Alpha - powerlaw slope of U | [1.0, 1.0, 3.0, 0.1, 0] |
| **Pa32** | Gamma - fraction illuminated by star-forming region | [0.1, 0, 1.0, 0.1, 1] |

## Complete Example

```text
# Profile A - Sersic bulge
Pa1) bulge                                      # name of the component
Pa2) sersic                                     # profile type
Pa3) [0,-5,5,0.1,1]                             # x-center [arcsec]
Pa4) [0,-5,5,0.1,1]                             # y-center [arcsec]
Pa5) [2.296,0.023,4.592,0.01,1]                 # Re [arcsec]
Pa6) [4,1,6,0.1,1]                              # Sersic index (de Vaucouleurs)
Pa7) [0,-90,90,1,1]                             # position angle (PA) [degree]
Pa8) [0.8,0.6,1,0.01,1]                         # axis ratio (q = b/a)
Pa9) [[-4,-8,-1,0.1,1]]                         # specific star formation rate
Pa10) [[5,0.01,11,0.1,1]]                       # burst stellar age [Gyr]
Pa11) [0.02,0.001,0.04,0.001,1]                 # metallicity [Z=0.02=Solar]
Pa12) [0.7,0.3,5.1,0.1,1]                       # Av dust extinction [mag]
Pa13) [100,40,200,1,0]                          # stellar velocity dispersion [km/s]
Pa14) [10.14,8.5,12,0.1,1]                      # log stellar mass [solar mass]
Pa15) burst                                     # star formation history type
Pa16) [-2,-4,-2,0.1,0]                          # logU nebular ionization parameter
Pa26) [3,0,5,0.1,0]                             # amplitude of the 2175A bump
Pa27) 0                                         # SED model type (full)
Pa28) [8.14,4.5,10,0.1,1]                       # log cold dust mass
Pa29) [1.0, 0.1, 50, 0.1, 0]                    # Umin
Pa30) [1.0, 0.47, 7.32, 0.1, 0]                 # qPAH
Pa31) [1.0, 1.0, 3.0, 0.1, 0]                   # alpha
Pa32) [0.1, 0, 1.0, 0.1, 1]                     # gamma
```

## Common Sersic Index Values

| Component Type | Typical n Range | Description |
|---------------|----------------|-------------|
| Exponential disk | n = 1 | Typical spiral galaxy disk |
| de Vaucouleurs bulge | n = 4 | Classical elliptical/bulge |
| Core/Sersic | n > 4 | Brightest cluster galaxies, cores |
| Pseudo-bulge | n = 1-2 | Disk-like bulges |
| Elliptical | n = 4-8 | Elliptical galaxies |

## Phase-Specific Configuration

For multi-phase fitting, the vary parameter (5th value) controls which parameters are free:

| Phase | Spatial (Pa3-Pa8) | SED (Pa9-Pa16) |
|-------|-------------------|----------------|
| 1 (Image only) | vary=1 | vary=0 |
| 2 (SED only) | vary=0 | vary=1 |
| 3 (Joint) | vary=1 | vary=1 |

## Tips for Good Convergence

1. **Initial Re (Pa5)**: Estimate from visual inspection or use half-light radius
2. **Initial Sersic index (Pa6)**: Start with n=4 for bulges, n=1 for disks
3. **Axis ratio (Pa8)**: Keep reasonable (0.1-1.0) to avoid degeneracy with PA
4. **Center (Pa3, Pa4)**: Start near image center or photometric center
5. **SED parameters**: For Phase 1, fix these; for Phase 2, free them

## See Also

- [Fourier Sersic Profile](profile-fourier.md) - For non-axisymmetric features
- [Galaxy Configuration](galaxy.md) - How to combine profiles into galaxies
- [Parameter Format & Combining](parameter-format.md) - General parameter information
