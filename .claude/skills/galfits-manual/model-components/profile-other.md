# Other Profile Types

This section describes additional profile types available in GalfitS beyond the standard Sersic and Fourier Sersic profiles.

## Available Profile Types

| Profile Type | Value for Pa2 | Use Case | Complexity |
|-------------|---------------|----------|------------|
| Ferrer Bar | `ferrer` | Galactic bars with flat inner region | Specialized |
| Edge-on Disk | `edgeondisk` | Edge-on disk galaxies | Specialized |
| Gaussian Ring | `GauRing` | Ring-like structures | Specialized |
| Constant | `const` | Constant/flat surface brightness | Simple |
| Gaussian | `Gaussian` | Simple symmetric components | Simple |

---

## Ferrer Bar Profile

The Ferrer bar profile is a specialized profile for modeling galactic bars with a flat inner region.

### Overview

- **Profile Type**: `ferrer`
- **Use Case**: Galactic bars with flat inner core, sharp outer edge
- **Features**: Flat inner core, power-law outer decline, sharp truncation

### Example

```text
# Profile - Ferrer bar profile
Pa1) bar
Pa2) ferrer                                     # profile type
Pa3) [0,-5,5,0.1,1]                             # x-center [arcsec]
Pa4) [0,-5,5,0.1,1]                             # y-center [arcsec]
# ... additional Ferrer-specific parameters ...
# SED parameters (Pa9-Pa32) same as Sersic
```

### When to Use

- Bar shows flat inner brightness profile
- Bar has sharp outer edge
- Fourier Sersic doesn't capture bar shape adequately

---

## Edge-on Disk Profile

Specialized profile for edge-on disk galaxies with vertical structure.

### Overview

- **Profile Type**: `edgeondisk`
- **Use Case**: Edge-on disk galaxies with prominent dust lanes
- **Features**: Models vertical structure and scale height

### Example

```text
# Profile - Edge-on disk profile
Pa1) edgeon_disk
Pa2) edgeondisk                                 # profile type
Pa3) [0,-5,5,0.1,1]                             # x-center
Pa4) [0,-5,5,0.1,1]                             # y-center
# ... additional edge-on specific parameters ...
# SED parameters (Pa9-Pa32) same as Sersic
```

### When to Use

- Galaxy is highly inclined (> 80 degrees)
- Vertical structure is important
- Dust lane is prominent

---

## Gaussian Ring Profile

Models ring-like or annular structures in galaxies.

### Overview

- **Profile Type**: `GauRing`
- **Use Case**: Ring structures, lensed features, annular components
- **Features**: Gaussian radial profile at specific radius

### Example

```text
# Profile - Gaussian ring profile
Pa1) ring
Pa2) GauRing                                    # profile type
Pa3) [0,-5,5,0.1,1]                             # x-center
Pa4) [0,-5,5,0.1,1]                             # y-center
# ... additional ring parameters (radius, width) ...
# SED parameters (Pa9-Pa32) same as Sersic
```

### When to Use

- Obvious ring structure (e.g., Hoag-type galaxy)
- Resonant ring (inner or outer ring)
- Lensing arcs or rings

---

## Constant Profile

Models constant/flat surface brightness regions.

### Overview

- **Profile Type**: `const`
- **Use Case**: Background subtraction, flat regions, scattered light
- **Features**: Uniform surface brightness

### Example

```text
# Profile - Constant profile
Pa1) background
Pa2) const                                      # profile type
# Parameters typically include flux level only
```

### When to Use

- Modeling scattered light
- Flat background regions
- Calibration purposes

**Note**: For most sky modeling, use the `Ia11` and `Ia12` parameters instead of a constant profile component.

---

## Gaussian Profile

Gaussian profiles for simple symmetric components.

### Overview

- **Profile Type**: `Gaussian`
- **Use Case**: Unresolved sources, simple compact components
- **Features**: Gaussian surface brightness profile

### Example

```text
# Profile - Gaussian profile
Pa1) core
Pa2) Gaussian                                   # profile type
Pa3) [0,-5,5,0.1,1]                             # x-center
Pa4) [0,-5,5,0.1,1]                             # y-center
Pa5) [0.5,0.1,2,0.01,1]                         # sigma [arcsec]
# ... amplitude/flux parameter ...
# SED parameters (Pa9-Pa32) same as Sersic
```

### When to Use

- Compact nucleus (when not using full AGN model)
- Unresolved background sources
- Simple symmetric components

---

## Profile Selection Guide

| Observation | Recommended Profile | Alternative |
|-------------|---------------------|-------------|
| Elliptical galaxy | `sersic` (n=4) | - |
| Spiral disk | `sersic` (n=1) | - |
| Bar with flat core | `ferrer` | `sersic_f` (m=2) |
| Bar/spiral arms | `sersic_f` (m=2) | - |
| Ring structure | `GauRing` | - |
| Edge-on disk | `edgeondisk` | `sersic` with high flattening |
| Unresolved source | `Gaussian` | AGN component |
| Flat background | `const` | Use Ia11/Ia12 instead |

## Parameter Notes

1. **SED parameters**: All profile types share the same SED parameter structure (Pa9-Pa32) as the standard Sersic profile

2. **Spatial parameters**: Each profile type has its own set of spatial parameters beyond Pa3-Pa4 (center)

3. **Initial values**: For specialized profiles, consult literature or perform visual inspection for reasonable starting values

4. **Parameter linking**: When fitting the same physical component across multiple bands, consider linking structural parameters via constraint files

## See Also

- [Sersic Profile](profile-sersic.md) - Standard Sersic profile
- [Fourier Sersic Profile](profile-fourier.md) - For bars and spiral arms
- [Galaxy Configuration](galaxy.md) - How to combine into galaxies
- [Parameter Format & Combining](parameter-format.md) - General parameter information
