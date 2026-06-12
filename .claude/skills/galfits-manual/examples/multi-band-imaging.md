# Multi-band Imaging Decomposition

Performing multi-band imaging decomposition with simultaneous SED modeling is a fundamental task with GalfitS. This example demonstrates a bulge-disk decomposition using SDSS and 2MASS imaging data.

## Overview

This example:
- Fits 10 photometric bands: SDSS ugriz + 2MASS JHK + GALEX FUV/NUV
- Decomposes galaxy into bulge and disk components
- Simultaneously models stellar population SEDs for each component
- Uses `Ia15) 1` to enable full SED fitting

## Example: Bulge-Disk Decomposition

Based on `quickstart.lyric` from the GalfitS-Public repository.

### Region Configuration

```text
# Region information
R1) J0056-0021              # Target name
R2) [14.13899,-0.36266]     # RA, Dec [degrees]
R3) 0.0628                  # Redshift
```

### Image Configuration

For each band, configure an Image section. Here's the SDSS u-band example:

```text
# Image A - SDSS u
Ia1) [sdssu_cut.fits,0]     # Science image
Ia2) sloan_u                # Band name
Ia3) [sdssu_cut.fits,2,1.8] # Sigma image (HDU=2, gain=1.8)
Ia4) [sdssu_psf.fits,0]     # PSF image
Ia5) 1                      # PSF fine sampling factor
Ia6) [sdssu_cut.fits,1]     # Bad pixel mask
Ia7) cR                     # Image unit (cR = counts)
Ia8) 13.44                  # Image size [arcsec]
Ia9) -1                     # Unit conversion (default)
Ia10) 26.466                # Magnitude zeropoint
Ia11) uniform               # Sky model
Ia12) [[0,-0.5,0.5,0.1,0]]  # Sky parameter
Ia13) 0                     # Allow relative shift (0=no, 1=yes)
Ia14) [[0,-5,5,0.1,0],[0,-5,5,0.1,0]]  # [shiftx, shifty]
Ia15) 1                     # Use SED information
```

**Repeat for additional bands** (Ib for g-band, Ic for r-band, etc.) with corresponding filenames.

#### Key Parameters Explained

| Parameter | Description |
|-----------|-------------|
| **Ia2** | Band name - must match GalfitS SED database |
| **Ia3** | Sigma image: `[filename, HDU, gain]` |
| **Ia8** | Image stamp size in arcseconds |
| **Ia9** | Conversion factor to flux density (-1 = auto) |
| **Ia10** | Photometric zeropoint for magnitude calculation |
| **Ia13** | Enable band position shifts (for alignment) |
| **Ia14** | Shift range: `[[x_min,x_max,x_step,x_vary], [y...]]` |
| **Ia15** | **Critical**: 1 = use SED, 0 = magnitude-only |

### Image Atlas Configuration

Group bands by instrument/telescope:

```text
# SDSS atlas
Aa1) 'sdss'                # Atlas name
Aa2) ['a','b','c','d','e'] # Image letters (u,g,r,i,z)
Aa3) 1                     # Same pixel size
Aa4) 0                     # Link relative shifts
Aa5) []                    # Spectra (empty)
Aa6) []                    # Aperture sizes
Aa7) []                    # Reference images

# 2MASS atlas
Ab1) '2mass'
Ab2) ['f','g','h']         # J, H, Ks bands
Ab3) 1
Ab4) 0
Ab5) []
Ab6) []
Ab7) []

# GALEX atlas
Ac1) 'galex'
Ac2) ['i','j']             # FUV, NUV
Ac3) 1
Ac4) 0
Ac5) []
Ab6) []
Ac7) []
```

### Profile Components

Define bulge and disk components with SED parameters:

```text
# Profile A - Bulge
Pa1) bulge                          # Component name
Pa2) sersic                         # Profile type
Pa3) [0,-5,5,0.1,1]                 # x-center
Pa4) [0,-5,5,0.1,1]                 # y-center
Pa5) [1.34,0.06,2.69,0.1,1]         # Effective radius [arcsec]
Pa6) [4,0.5,6,0.1,1]                # Sersic index n
Pa7) [0,-90,90,1,1]                 # Position angle [deg]
Pa8) [0.8,0.6,1,0.01,1]             # Axis ratio b/a
# SED parameters
Pa9)  [[-2,-8,0,0.1,1]]             # log(sSFR) [1/yr]
Pa10) [[5,0.01,10,0.1,1]]           # Burst age [Gyr]
Pa11) [[0.02,0.001,0.04,0.001,1]]   # Metallicity Z (0.02=Solar)
Pa12) [[0.7,0.3,5.1,0.1,1]]         # V-band extinction Av [mag]
Pa13) [100,40,200,1,0]              # Stellar velocity dispersion [km/s]
Pa14) [10.14,8.5,12,0.1,1]          # log(stellar mass) [Msun]
Pa15) burst                          # SFH type: burst/conti
Pa16) [-2,-4,-2,0.1,0]              # logU ionization parameter
# Dust model (DL2014)
Pa26) [3,0,5,0.1,1]                 # 2175Å bump amplitude
Pa27) 0                             # SED model: 0=full, 1=stellar, 2=nebular, 3=dust
Pa28) [8.14,4.5,10,0.1,0]           # log(dust mass) [Msun]
Pa29) [1.0, 0.1, 50, 0.1, 0]        # Umin (min radiation field)
Pa30) [1.0, 0.47, 7.32, 0.1, 0]     # qPAH (PAH fraction)
Pa31) [1.0, 1.0, 3.0, 0.1, 0]       # Alpha (radiation field slope)
Pa32) [0.1, 0, 1.0, 0.1, 0]         # Gamma (illuminated fraction)

# Profile B - Disk
Pb1) disk
Pb2) sersic
Pb3) [0,-5,5,0.1,1]
Pb4) [0,-5,5,0.1,1]
Pb5) [2.69,0.67,10.75,0.1,1]        # Larger Re for disk
Pb6) [1,0.5,3,0.1,1]                # Lower n for disk
Pb7) [-60,-90,90,1,1]               # Different PA
Pb8) [0.5,0.2,1,0.01,1]             # Thinner disk
# SED parameters (different from bulge)
Pb9)  [[-1,-4,0,0.1,1]]             # Higher sSFR
Pb10) [[5,0.01,10,0.1,1]]
Pb11) [[0.02,0.001,0.04,0.001,1]]
Pb12) [[0.7,0.3,5.1,0.1,1]]
Pb13) [100,40,200,1,0]
Pb14) [10.64,8.5,12,0.1,1]          # Higher mass for disk
Pb15) conti                          # Continuous SFH
Pb16) [-3,-4,-2,0.1,0]
# Dust model
Pb26) [3,0,5,0.1,1]
Pb27) 0
Pb28) [8.14,4.5,10,0.1,0]
Pb29) [1.0, 0.1, 50, 0.1, 0]
Pb30) [1.0, 0.47, 7.32, 0.1, 0]
Pb31) [1.0, 1.0, 3.0, 0.1, 0]
Pb32) [0.1, 0, 1.0, 0.1, 0]
```

### Galaxy Configuration

Combine profiles into a physical galaxy:

```text
# Galaxy A
Ga1) mygal                        # Galaxy name
Ga2) ['a','b']                    # Profile components (bulge + disk)
Ga3) [0.0628,0.0128,0.1128,0.01,0] # Redshift with range
Ga4) 0.0213                       # Distance modulus
Ga5) [1.,0.5,2,0.05,0]            # Spectrum normalization
Ga6) []                           # Narrow emission lines
Ga7) 1                            # Number of narrow line components
```

## Running the Fit

```bash
galfits quickstart.lyric --work ./output/ --num_s 20000
```

## Output Files

After completion, check these files:

| File | Description |
|------|-------------|
| `*.gssummary` | Optimization summary with χ² and parameters |
| `*.params` | Fitted parameter table |
| `*.sed.png` | SED plot showing stellar/nebular/dust contributions |
| `*.residual.png` | Residual images for each band |

## Pure Morphology Mode

To fit only spatial structure (magnitudes per band, no SED):

```text
# Set for ALL images
Ia15) 0     # Disable SED modeling
```

In this mode:
- Only magnitude of each component/band is fitted
- SED parameters (Pa9-Pa32) are ignored
- Must set `Ia10)` (magnitude zeropoint) correctly
- Useful for traditional photometry

## Common Issues

### Band Misalignment

**Symptom**: Blue/red color split in residuals

**Solution**: Enable relative shifts:
```text
Ia13) 1                              # Enable shifts
Ia14) [[0,-5,5,0.1,1],[0,-5,5,0.1,1]] # Allow fitting
```

### Poor Convergence

**Symptom**: Reduced χ² is very high

**Solutions**:
1. Check initial parameter values are reasonable
2. Expand parameter bounds (min/max values)
3. Verify PSF images are correct
4. Check sigma images for bad pixels

### Parameter Hits Limit

**Symptom**: Parameter stays at min or max value

**Solution**: Expand bounds:
```text
# Before
Pa5) [1.34,0.06,2.69,0.1,1]  # Hitting max

# After
Pa5) [1.34,0.06,10.0,0.1,1]  # Expand max
```

## Related Examples

- [Pure SED Fitting](pure-sed-fitting.md) - Photometric data only
- [Imaging + Spectrum Joint](imaging-spectrum-joint.md) - Add spectroscopic constraints

## Reference

- Original file: `quickstart.lyric` in GalfitS-Public repository
- Data: SDSS + 2MASS + GALEX for galaxy J0056-0021
