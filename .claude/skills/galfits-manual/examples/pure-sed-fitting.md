# Pure SED Fitting

Photometric SED fitting models the spectral energy distribution of an astronomical object using broad-band photometry data. In GalfitS, this is done by transforming photometric data points into "mock" images, then applying the standard fitting routine.

## Overview

This example:
- Uses photometric data from FUV to FIR (GALEX → SDSS → 2MASS → WISE)
- Fits stellar, nebular, and dust emission components
- Models galaxy SED without spatial information
- Based on `SEDfit.lyric` from the GalfitS-Public repository

## Input Data Format

Photometric data is typically provided as a table with columns for band, flux, and flux error:

```text
# object.mag
Band    NUV    FUV    sloan_u    sloan_g    sloan_r    ...
Flux    xxx    xxx    xxx        xxx        xxx        ...
Flux_err xxx    xxx    xxx        xxx        xxx        ...
```

## Step 1: Convert Photometry to Mock Images

Use the `photometry_to_img` function from `galfits.gsutils`:

```python
from galfits import gsutils
from astropy.table import Table

# Read photometric data
object_flux = Table.read('./object.mag', format='ascii')
z = object_flux['redshift']

# Define bands from FUV to FIR
Bands = [
    'galex_nuv', 'galex_fuv',
    'sloan_u', 'sloan_g', 'sloan_r', 'sloan_i', 'sloan_z',
    'wise_ch1', 'wise_ch2', 'wise_ch3', 'wise_ch4'
]

# Convert each band to a mock FITS image
for loop, band in enumerate(Bands):
    flux_mjy = object_flux[band]
    flux_err = object_flux[band + '_err']
    outputname = './data/' + band + '.fits'
    gsutils.photometry_to_img(band, flux_mjy, flux_err, z, outputname, unit='mJy')
```

This generates a FITS file for each band containing:
- **HDU 0**: Mock image (single pixel with flux value)
- **HDU 2**: Noise map (flux error)
- **HDU 3**: Secondary data (used for error)

## Step 2: Configure GalfitS

### Region Configuration

```text
R1) MyGalaxy              # Target name
R2) [150.1, 2.5]         # RA, Dec (can be placeholder for pure SED)
R3) 0.05                  # Redshift
```

For pure SED fitting, spatial coordinates (R2) are less important since there's no actual image structure to fit.

### Image Configuration (Per Band)

Each photometric data point becomes an Image section:

```text
# Image A - GALEX NUV
Ia1) [/path/to/galex_nuv.fits, 0]     # Mock image
Ia2) galex_nuv                         # Band name
Ia3) [/path/to/galex_nuv.fits, 2]      # Sigma (error) image
Ia4) [/path/to/galex_nuv.fits, 3]      # Secondary data
Ia5) 1                                 # PSF sampling (N/A for single pixel)
Ia6) [Noimg, 0]                        # No actual science image
Ia7) cR                                # Unit type
Ia8) -1                                # Image size (N/A for SED)
Ia9) 1                                 # Conversion to flux density
Ia10) 28.33                            # Magnitude zeropoint
Ia11) uniform                          # Sky model
Ia12) [[0,-0.5,0.5,0.1,0]]             # Sky parameter
Ia13) 0                                # No position shifts
Ia14) [[0,-5,5,0.1,0],[0,-5,5,0.1,0]]  # Shift range (unused)
Ia15) 1                                # Use SED information
```

#### Key Differences from Imaging Mode

| Parameter | Imaging | Pure SED | Notes |
|-----------|---------|----------|-------|
| **Ia1** | Real image file | Mock image file | Single-pixel FITS |
| **Ia6** | Real sigma image | `[Noimg, 0]` | No actual image |
| **Ia8** | Image size | `-1` | Not applicable |
| **Ia9** | `-1` (auto) | `1` | **Important**: Explicit conversion |
| **Ia10** | Camera zeropoint | From catalog | Critical for flux calibration |

### Why Ia9) = 1 for Pure SED?

Setting `Ia9) 1` explicitly tells GalfitS to convert from image units to flux density (Fλ) using the zeropoint. For photometry data, this ensures the correct transformation:

```
Fλ = flux × 10^(-0.4 × zeropoint)
```

If `Ia9) -1` is used, the default conversion may not match your photometric system.

### Image Atlas

Group all photometric bands into one atlas:

```text
Aa1) 'photometry'              # Atlas name
Aa2) ['a','b','c','d','e','f','g','h','i','j','k']  # All bands
Aa3) 1                         # Same pixel size (trivially true)
Aa4) 0                         # No linking needed
Aa5) []                        # No spectra
Aa6) []                        # No apertures
Aa7) []                        # No reference images
```

### Profile Component

For single-component SED fitting, use one Profile:

```text
Pa1) total                       # Component name
Pa2) sersic                     # Profile type (spatial unused)
# Spatial parameters (fixed, unused for SED)
Pa3) [0,-5,5,0.1,0]             # x-center (fixed)
Pa4) [0,-5,5,0.1,0]             # y-center (fixed)
Pa5) [1,0.1,10,0.1,0]           # Re (fixed)
Pa6) [2,0.5,6,0.1,0]            # n (fixed)
Pa7) [0,-90,90,1,0]             # PA (fixed)
Pa8) [0.5,0.1,1,0.01,0]         # q (fixed)
# SED parameters (free)
Pa9)  [[-2,-4,0,0.1,1]]         # log(sSFR)
Pa10) [[5,0.01,13,0.1,1]]       # Burst age [Gyr]
Pa11) [[0.02,0.001,0.04,0.001,1]]  # Metallicity
Pa12) [[0.3,0,5,0.1,1]]         # Av extinction
Pa13) [100,40,300,1,0]          # Velocity dispersion
Pa14) [10,8,12,0.1,1]           # log(stellar mass)
Pa15) conti                      # SFH type
Pa16) [-3,-4,-2,0.1,0]          # logU
# Dust emission
Pa26) [3,0,5,0.1,1]             # 2175Å bump
Pa27) 0                         # Full SED model
Pa28) [7,4,10,0.1,1]            # log(dust mass)
Pa29) [1,0.1,50,0.1,0]          # Umin
Pa30) [1,0.47,7.32,0.1,0]       # qPAH
Pa31) [1,1,3,0.1,0]             # Alpha
Pa32) [0.1,0,1,0.1,0]           # Gamma
```

**Note**: Spatial parameters (Pa3-Pa8) should be fixed (`vary=0`) since they don't affect the SED fit.

### Galaxy Configuration

```text
Ga1) mygal                      # Galaxy name
Ga2) ['a']                      # Single profile
Ga3) [0.05,0.04,0.06,0.01,0]    # Redshift
Ga4) 0.0213                     # Distance modulus
Ga5) [1.,0.5,2,0.05,0]          # Normalization
Ga6) []                         # No emission lines
Ga7) 1                          # Default
```

## Running the Fit

```bash
galfits SEDfit.lyric --work ./sed_output/ --num_s 20000
```

**Expected runtime**: ~1.5 minutes on RTX 4090

## Output Results

### SED Plot

The output `*.sed.png` shows:
- **Black circles**: Observed photometric data points
- **Blue curve**: Stellar emission (young + old stars)
- **Green curve**: Nebular emission (emission lines)
- **Red curve**: Dust emission (IR)
- **Grey curve**: Total model

### Summary File

`*.gssummary` contains:
- Best-fit parameter values with uncertainties
- Reduced χ² for each band
- Log-likelihood and evidence

## Example Band Coverage

| Telescope | Bands | Wavelength Range |
|-----------|-------|------------------|
| GALEX | FUV, NUV | 1350-2800 Å |
| SDSS | ugriz | 3000-10000 Å |
| 2MASS | J, H, Ks | 1.2-2.2 μm |
| WISE | W1-W4 | 3.4-22 μm |
| Spitzer | IRAC | 3.6-8.0 μm |
| Herschel | PACS/SPIRE | 70-500 μm |

## Performance Notes

| Data Coverage | Components | Time (RTX 4090) |
|---------------|------------|-----------------|
| FUV-IR (11 bands) | Stellar + Dust | ~1.5 min |
| UV-optical only | Stellar only | ~0.5 min |
| With nebular | + emission lines | ~2 min |

## Common Issues

### Flux Calibration Error

**Symptom**: SED is offset from data points

**Solution**: Check `Ia10)` zeropoint values:
```text
# For AB magnitude system: m = -2.5 log(Fν) - 48.6
# Zeropoint converts from counts to flux
Ia10) 28.33    # SDSS AB zeropoint example
```

### Poor Dust Fit

**Symptom**: IR data points not fitted well

**Solutions**:
1. Enable dust model fitting: `Pa28) vary=1`
2. Check IR data coverage (need WISE/Spitzer)
3. Consider energy balance constraint

### Dega256 Mass-SFR Degeneracy

**Symptom**: Multiple parameter combinations fit equally well

**Solutions**:
1. Use MSR/MMR constraints (see [Constraints](../constraints/))
2. Add metallicity prior if available
3. Use narrower parameter bounds

## When to Use Pure SED Fitting

| Situation | Recommended? |
|-----------|--------------|
| Only photometric catalog data available | **Yes** |
| Galaxy too faint/distant for spatial decomposition | **Yes** |
| Large sample fitting (thousands of galaxies) | **Yes** |
| Need spatially resolved stellar populations | **No** - use imaging decomposition |

## Related Examples

- [Multi-band Imaging Decomposition](multi-band-imaging.md) - When spatial data is available
- [Imaging + Spectrum Joint](imaging-spectrum-joint.md) - Add spectroscopic constraints

## Reference

- Original file: `SEDfit.lyric` in GalfitS-Public repository
- Function: `galfits.gsutils.photometry_to_img()`
