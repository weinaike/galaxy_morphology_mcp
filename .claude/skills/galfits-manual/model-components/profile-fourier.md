# Fourier Sersic Profile

The Fourier Sersic profile extends the standard Sersic profile with Fourier modes for non-axisymmetric features like spiral arms, bars, and boxy/disky distortions. It is defined by setting `Pa2) sersic_f`.

## Overview

- **Profile Type**: `sersic_f`
- **Use Case**: Non-axisymmetric profiles with spiral/boxy/disky features, bars, spiral arms
- **Parameters**: 32 parameters total (Pa1-Pa24, Pa27-Pa32)
- **Additional Parameters**: Pb17-Pb24 for Fourier modes (not in standard Sersic)

## What Makes It Different from Standard Sersic?

The Fourier Sersic profile adds:
- **Fourier modes** (m) to describe azimuthal variations
- **Rotation curve parameters** (r_in, r_out, alpha, theta_out)
- **Fourier amplitude and angles** (am, theta_m, i_m)

This allows modeling of:
- Spiral arms (m=2 or higher)
- Bars (m=2 with specific orientation)
- Boxy/disky isophotes
- Any non-axisymmetric structure

## Parameter Reference

### Basic Parameters (Pa1-Pa16)

Same as standard Sersic profile - see [Sersic Profile](profile-sersic.md) for details.

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Pa1** | Component name (e.g., 'spiral', 'bar', 'arm') | string |
| **Pa2** | Profile type - must be `sersic_f` | 'sersic_f' |
| **Pa3-Pa16** | Same as Sersic profile | - |

### Fourier Mode Parameters (Pa17-Pa24)

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| **Pa17** | r_in - Inner radius of rotation [arcsec] | [1, 0.3, 3, 0.1, 1] |
| **Pa18** | r_out - Outer radius of rotation [arcsec] | [2, 1, 10, 0.1, 1] |
| **Pa19** | Alpha - Shape of arctan rotation curve | [1, 0.5, 3, 0.1, 1] |
| **Pa20** | theta_out - Maximum rotation angle [degree] | [70, 30, 130, 1, 1] |
| **Pa21** | m - Number of Fourier mode | 1, 2, 3, etc. |
| **Pa22** | am - Fourier amplitude | [0.5, 0, 1, 0.01, 1] |
| **Pa23** | theta_m - Fourier position angle [degree] | [30, 0, 180, 1, 1] |
| **Pa24** | i_m - Fourier projection angle [degree] | [30, 0, 90, 1, 1] |

### Dust Model Parameters (Pa27-Pa32)

Same as standard Sersic profile - see [Sersic Profile](profile-sersic.md).

## Complete Example

```text
# Profile B - Fourier mode disk with spiral arms
Pb1) arm                                        # name of the component
Pb2) sersic_f                                   # profile type
Pb3) [0,-5,5,0.1,1]                             # x-center [arcsec]
Pb4) [0,-5,5,0.1,1]                             # y-center [arcsec]
Pb5) [3.0,0.5,8.0,0.01,1]                       # Re [arcsec]
Pb6) [1,0.5,2,0.1,1]                            # Sersic index (exponential)
Pb7) [0,-90,90,1,1]                             # position angle (PA) [degree]
Pb8) [0.3,0.1,0.8,0.01,1]                       # axis ratio (thin disk)
Pb9) [[-4,-8,-1,0.1,1]]                         # sSFR
Pb10) [[5,0.01,11,0.1,1]]                       # burst age [Gyr]
Pb11) [0.02,0.001,0.04,0.001,1]                 # metallicity
Pb12) [0.7,0.3,5.1,0.1,1]                       # Av [mag]
Pb13) [100,40,200,1,0]                          # stellar velocity dispersion
Pb14) [10.14,8.5,12,0.1,1]                      # log stellar mass
Pb15) burst                                     # SFH type
Pb16) [-2,-4,-2,0.1,0]                          # logU
# Fourier mode parameters
Pb17) [1,0.3,3,0.1,1]                           # r_in, inner radius [arcsec]
Pb18) [2,1,10,0.1,1]                            # r_out, outer radius [arcsec]
Pb19) [1,0.5,3,0.1,1]                           # alpha, rotation curve shape
Pb20) [70,30,130,1,1]                           # theta_out, max rotation angle
Pb21) 2                                         # m, number of Fourier mode
Pb22) [0.5,0.,1,0.01,1]                         # am, Fourier amplitude
Pb23) [30,0,180,1,1]                            # theta_m, Fourier PA
Pb24) [30,0,90,1,1]                             # i_m, Fourier projection angle
# Dust parameters
Pb27) 0                                         # SED model type
Pb28) [8.14,4.5,10,0.1,1]                       # log cold dust mass
Pb29) [1.0, 0.1, 50, 0.1, 0]                    # Umin
Pb30) [1.0, 0.47, 7.32, 0.1, 0]                 # qPAH
Pb31) [1.0, 1.0, 3.0, 0.1, 0]                   # alpha
Pb32) [0.1, 0, 1.0, 0.1, 1]                     # gamma
```

## Fourier Mode Interpretation

### Mode Number (m = Pa21)

| m Value | Use Case | Description |
|---------|----------|-------------|
| m = 0 | Axisymmetric | Reduces to standard Sersic |
| m = 1 | Lopsided | One-sided distortion |
| m = 2 | Bar/Two-armed spiral | Bar or two spiral arms |
| m = 3 | Three-armed spiral | Three spiral arms |
| m = 4+ | Multi-arm | Multiple spiral arms |

### Rotation Curve Parameters

- **r_in (Pa17)**: Inner radius where Fourier modes start acting
- **r_out (Pa18)**: Outer radius where Fourier modes stop
- **alpha (Pa19)**: Shape of rotation curve transition
  - Small values: Sharp transition
  - Large values: Gradual transition
- **theta_out (Pa20)**: Total rotation angle from r_in to r_out

### Fourier Amplitude (am = Pa22)

- **am = 0**: No Fourier distortion (axisymmetric)
- **am = 0.1-0.3**: Mild non-axisymmetry
- **am = 0.3-0.6**: Moderate (typical bars)
- **am > 0.6**: Strong (prominent spiral arms)

### Angle Parameters

- **theta_m (Pa23)**: Position angle of the Fourier mode (orientation of bar/spiral arms)
- **i_m (Pa24)**: Projection/inclination angle of the mode

## Common Use Cases

### Bar Modeling

```text
Pb1) bar
Pb2) sersic_f
# ... standard Sersic parameters ...
Pb17) [0.5, 0.1, 2, 0.1, 1]                    # Bar starts near center
Pb18) [3, 1, 6, 0.1, 1]                         # Bar extends outward
Pb19) [1, 0.5, 3, 0.1, 1]                       # Smooth rotation
Pb20) [90, 30, 150, 1, 1]                       # Bar has structure
Pb21) 2                                         # Two-fold symmetry (bar)
Pb22) [0.4, 0.1, 0.8, 0.01, 1]                  # Moderate amplitude
Pb23) [45, 0, 180, 1, 1]                        # Bar PA = 45 degrees
Pb24) [30, 0, 60, 1, 1]                         # Moderate inclination
```

### Two-Armed Spiral

```text
Pb1) spiral
Pb2) sersic_f
# ... exponential disk parameters ...
Pb17) [1.5, 0.5, 3, 0.1, 1]                    # Spiral starts at 1.5"
Pb18) [8, 3, 15, 0.1, 1]                        # Extends to outer disk
Pb19) [2, 0.5, 4, 0.1, 1]                       # Gradual winding
Pb20) [180, 90, 270, 1, 1]                      # Full winding
Pb21) 2                                         # Two arms
Pb22) [0.2, 0.05, 0.5, 0.01, 1]                 # Moderate amplitude
Pb23) [90, 0, 180, 1, 1]                        # Arm starting angle
Pb24) [20, 0, 45, 1, 1]                         # Slight inclination
```

## Tips for Good Convergence

1. **Start with standard Sersic**: First fit with standard Sersic profile, then add Fourier modes
2. **Fix SED parameters**: When optimizing structural parameters, fix SED to reduce degeneracy
3. **Constraint r_in < r_out**: Always ensure inner radius is smaller than outer radius
4. **Initial amplitude**: Start with small am (~0.1-0.3) and increase if needed
5. **Mode number**: Use m=2 for most bars and spiral arms; higher modes may be unstable

## When to Use Fourier Sersic vs Standard Sersic

| Residual Pattern | Recommended Action |
|------------------|-------------------|
| Smooth, axisymmetric | Standard Sersic is sufficient |
| Bar-like positive residual | Add Fourier Sersic with m=2 |
| Spiral arm patterns | Add Fourier Sersic with m=2 or higher |
| Boxy/disky isophotes | Add Fourier Sersic with m=4 |
| Off-center features | Consider separate component instead |

## See Also

- [Sersic Profile](profile-sersic.md) - Standard axisymmetric Sersic profile
- [Other Profile Types](profile-other.md) - Ferrer bars, edge-on disks, etc.
- [Galaxy Configuration](galaxy.md) - How to combine into galaxies
