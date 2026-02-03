# Spectrum Fitting

Spectrum fitting in GalfitS performs detailed decomposition of galaxy and AGN spectra, modeling stellar continuum, nebular emission, dust extinction, and AGN components simultaneously.

## Overview

This example:
- Fits optical AGN spectrum (3600-7000 Å)
- Models host galaxy stellar population
- Includes AGN continuum and broad/narrow emission lines
- Decomposes emission line profiles
- Based on `AGNspectrum.lyric` from GalfitS-Public

## Input Spectrum Format

The spectrum is provided as a plain text table (no header) with 3 columns:

```text
# wavelength(A)  flux(Flambda)  flux_error
3000  xxx  xxx
3001  xxx  xxx
3002  xxx  xxx
...
```

Example file: `GreeneHo2004_id2.txt`

## Configuration

### Region (Simplified for Spectrum)

For spectrum-only fitting, spatial coordinates are optional:

```text
R1) J0249-0815              # Target name
# R2) - Can be omitted or placeholder
# R3) - Redshift can be specified in Ga3 instead
```

### Spectrum Input

Configure the spectrum data:

```text
# Spectrum A
Sa1) GreeneHo2004_id2.txt    # Spectrum filename
Sa2) 1                       # Conversion to 1e-17 Fλ units
Sa3) [3600., 7000.]          # Wavelength range [Å] (rest frame)
Sa4) 0                       # High-res stellar template (0=150km/s, 1=30km/s)
```

#### Parameters Explained

| Parameter | Description |
|-----------|-------------|
| **Sa1** | Path to spectrum text file |
| **Sa2** | Conversion factor to 10⁻¹⁷ erg/s/cm²/Å |
| **Sa3** | Fitting wavelength range (rest frame) |
| **Sa4** | Stellar template resolution: 0 = default (150 km/s), 1 = high-res (30 km/s) for velocity dispersion measurement |

### Image Atlas with Spectrum

Integrate spectrum into an atlas:

```text
# Atlas A
Aa1) sdss                    # Atlas name (can be placeholder)
Aa2) []                      # No images
Aa3) 1                       # Same pixel size
Aa4) 1                       # Link shifts
Aa5) ['a']                   # Include spectrum 'a'
Aa6) [[2]]                   # Aperture size (2 arcsec)
Aa7) [-1]                    # No reference image needed
```

### Host Galaxy Profile

Define stellar population parameters:

```text
# Profile A - Host Galaxy
Pa1) host
Pa2) sersic
# Spatial parameters (unused for spectrum)
Pa3) [0,-5,5,0.1,0]          # x-center (fixed)
Pa4) [0,-5,5,0.1,0]          # y-center (fixed)
Pa5) [1,0.1,10,0.1,0]        # Re (fixed)
Pa6) [2,0.5,6,0.1,0]         # n (fixed)
Pa7) [0,-90,90,1,0]          # PA (fixed)
Pa8) [0.5,0.1,1,0.01,0]      # q (fixed)
# SED parameters (active)
Pa9)  [[-2,-4,0,0.1,1]]      # log(sSFR)
Pa10) [[5,0.01,11,0.1,1]]    # Burst age [Gyr]
Pa11) [[0.02,0.001,0.04,0.001,0]]  # Metallicity
Pa12) [[0.7,0.3,5.1,0.1,1]]  # Av
Pa13) [100,40,300,1,1]       # **Velocity dispersion [km/s]** (free to measure)
Pa14) [10.14,8.5,12,0.1,1]   # log(stellar mass)
Pa15) conti                   # Continuous SFH
Pa16) [-3,-4,-2,0.1,0]       # logU
Pa26) [3,0,5,0.1,1]
Pa27) 0
Pa28) [8.14,4.5,10,0.1,0]
Pa29) [1.0, 0.1, 50, 0.1, 0]
Pa30) [1.0, 0.47, 7.32, 0.1, 0]
Pa31) [1.0, 1.0, 3.0, 0.1, 0]
Pa32) [0.1, 0, 1.0, 0.1, 0]
```

**Note for velocity dispersion**: Set `Pa13)` last value to 1 to measure σ★. Use high-res template (`Sa4) 1`).

### Nuclei/AGN Component

Configure AGN continuum and emission lines:

```text
# Nuclei A - AGN
Na1) agn                     # Component name
# Spatial (unused)
Na2) [0,-5,5,0.1,0]          # x-center
Na3) [0,-5,5,0.1,0]          # y-center
Na4) [0.1,0.01,1,0.1,0]      # Re
Na5) [6,1,10,0.1,0]          # n
Na6) [0,-90,90,1,0]          # PA
Na7) [0.1,0.1,1,0.01,0]      # q
# Continuum
Na8) [[-2,-8,0,0.1,1]]       # log L/LEdd
Na9) [[8,6,10,0.1,1]]        # log Mbh [Msun]
Na10) [43,41,47,0.1,1]       # log L5100 [erg/s]
# Power-law continuum
Na11) [[1,0,4,0.1,1], [0.6, 0, 5, 0.1,0]]  # Power law indices [α1, α2]
# Emission lines
Na12) ['Hg','Hb','HeII_4686','Ha']  # Broad lines
Na13) ['Hg','Hb','HeII_4686','OIII_4959','OIII_5007','HeI','Ha','OI_6302','NII_6549','NII_6583','SII_6716','SII_6731']  # Narrow lines
Na14) 2                       # Number of broad line components
Na15) 2                       # Number of narrow line components
Na16) 0                       # Add Balmer continuum (0=no, 1=yes)
Na17) 1                       # Add FeII (0=no, 1=yes)
Na18) 0                       # Continuum model: 0=power law, 1=broken, 2=thin disk
```

#### Emission Lines Reference

| Line Type | Wavelength | Notes |
|-----------|------------|-------|
| **Hg** | 4340 Å | Balmer Hγ |
| **Hb** | 4861 Å | Balmer Hβ |
| **Ha** | 6563 Å | Balmer Hα |
| **HeII_4686** | 4686 Å | He II |
| **OIII_4959** | 4959 Å | [O III] |
| **OIII_5007** | 5007 Å | [O III] |
| **OI_6302** | 6302 Å | [O I] |
| **NII_6549** | 6549 Å | [N II] |
| **NII_6583** | 6583 Å | [N II] |
| **SII_6716** | 6716 Å | [S II] |
| **SII_6731** | 6731 Å | [S II] |

### Galaxy Configuration

```text
Ga1) myagn                   # Galaxy name
Ga2) ['a']                   # Host profile
Ga3) [0.0628,0.06,0.07,0.01,0]  # Redshift
Ga4) 0.0213                  # Distance modulus
Ga5) [1.,0.5,2,0.05,0]       # Normalization
Ga6) []                      # Narrow lines (handled in AGN component)
Ga7) 1                       # Components
```

## Running the Fit

```bash
galfits AGNspectrum.lyric --work ./result/ --num_s 20000
```

**Expected runtime**: ~0.5 minutes on RTX 4090

## Output

The result includes:
- `*.gssummary` - Optimization summary
- `*.params` - Fitted parameters
- `*.png` - Spectrum plot with all components labeled
- Residual panel showing data-model difference

## Manipulating Parameter Files

After the initial fit, you can refine the fit by editing the `.params` file and re-running:

```bash
galfits AGNspectrum.lyric --work ./newresult/ --num_s 20000 --readpar J0249-0815.params
```

### Common Adjustments

#### 1. Adjust Hβ Broad Line Central Wavelength

```text
# In .params file
HbAGNb1wid  17.22    8.27    82.66   0.1  True  None
HbAGNb1peak 9975.02  0.0     99750.2 99.75 True  None
HbAGNb1cen  4862.68  4812.68 4912.68 0.1   True  None
HbAGNb2wid  34.44    13.78   82.66   0.1   True  None
HbAGNb2peak 997.50   0.0     99750.2 99.75 True  None
HbAGNb2cen  4862.68  4762.68 4962.68 0.1   True  None  # Expanded range
HbAGNb3wid  34.44    13.78   82.66   0.1   True  None
HbAGNb3peak 997.50   0.0     99750.2 99.75 True  None
HbAGNb3cen  4862.68  4762.68 4962.68 0.1   True  None  # Expanded range
```

#### 2. Reduce HeII Broad Components

To fit HeII with only 1 component instead of 3:

```text
HeII_4686AGNb1wid  16.60    7.97    79.67   0.1  True   None
HeII_4686AGNb1peak 5994.81  0.0     59948.1 59.95 True   None
HeII_4686AGNb1cen  4687.02  4637.02 4737.02 0.1  True   None
HeII_4686AGNb2wid  33.20    13.28   79.67   0.1  False  None   # Disabled
HeII_4686AGNb2peak 0.0      0.0     59948.1 59.95 False  None   # Zero peak
HeII_4686AGNb2cen  4687.02  4487.02 4887.02 0.1  False  None
HeII_4686AGNb3wid  33.20    13.28   79.67   0.1  False  None   # Disabled
HeII_4686AGNb3peak 0.0      0.0     59948.1 59.95 False  None
HeII_4686AGNb3cen  4687.02  4487.02 4887.02 0.1  False  None
```

#### 3. Add Broad Wings to OIII

Fit broad + narrow OIII components by linking parameters:

```text
# [OIII] 4959 - Linked to 5007
OIII_4959AGNb1wid  17.57    8.43    84.32   0.1  False  0.990*OIII_5007AGNb1wid
OIII_4959AGNb1peak 1000.0   0.0     1725464 172.55 False  0.336*OIII_5007AGNb1peak
OIII_4959AGNb1cen  4950.30  4910.30 5010.30 0.1  False  0.990*OIII_5007AGNb1cen
OIII_4959AGNb2wid  35.13    14.05   84.32   0.1  False  None
OIII_4959AGNb2peak 0.0      0.0     93883.4 93.88 False  None
OIII_4959AGNb2cen  4960.30  4760.30 5160.30 0.1  False  None

# [OIII] 5007 - Free component with broad wing
OIII_5007AGNb1wid  17.74    1.42    85.13   0.1  True   None
OIII_5007AGNb1peak 3000.0   0.0     172546  172.55 True   None
OIII_5007AGNb1cen  5000.0   4958.24 5058.24 0.1  True   None
OIII_5007AGNb2wid  35.47    14.19   85.13   0.1  False  None
OIII_5007AGNb2peak 0.0      0.0     172546  172.55 False  None
OIII_5007AGNb2cen  5008.24  4808.24 5208.24 0.1  False  None
```

The linking syntax `0.990*OIII_5007AGNb1wid` forces the 4959 line width to be 99% of the 5007 line width.

## Emission Line Components

### Broad Lines (Na12)

Typical broad lines for Type 1 AGN:
- Hδ, Hγ, Hβ, Hα (Balmer series)
- He II 4686

### Narrow Lines (Na13)

Common narrow lines:
- [O III] 4959, 5007 - Strong forbidden lines
- [N II] 6549, 6583
- [S II] 6716, 6731
- [O I] 6302

### Component Numbers

| Parameter | Meaning |
|-----------|---------|
| **Na14** | Number of Gaussian components for each **broad** line |
| **Na15** | Number of Gaussian components for each **narrow** line |

Typical values:
- `Na14) 2-3` - Broad lines often need 2-3 components (core + wing)
- `Na15) 2-3` - Narrow lines may need multiple components for asymmetry

## Continuum Models

| Na18 Value | Model | Use Case |
|------------|-------|----------|
| **0** | Single power law | Most AGN |
| **1** | Broken power law | UV-optical break |
| **2** | Thin disk | Accretion disk modeling |

## Performance

| Machine | Time |
|---------|------|
| RTX 4090 | ~0.5 min |

For spectra with many emission lines or high resolution, increase `--num_s` to 50000+ for convergence.

## Common Issues

### Velocity Dispersion Not Converging

**Symptom**: Pa13 (σ★) hits bounds or has large uncertainty

**Solutions**:
1. Use high-res template: `Sa4) 1`
2. Ensure good stellar absorption features (Mg b, Fe lines)
3. Check wavelength range includes 4000-6000 Å

### Emission Line Not Fitted

**Symptom**: Residual shows unmodeled emission line

**Solutions**:
1. Add line to Na12 or Na13
2. Increase number of components (Na14/Na15)
3. Check line is defined in `emission_lines.py`

### Poor Continuum Fit

**Symptom**: Systematic residual in continuum shape

**Solutions**:
1. Try broken power law: `Na18) 1`
2. Check host galaxy contribution
3. Verify wavelength calibration

## Related Examples

- [Imaging + Spectrum Joint Fitting](imaging-spectrum-joint.md) - Combine spatial and spectral constraints
- [Pure SED Fitting](pure-sed-fitting.md) - Photometry-only fitting

## Reference

- Original file: `AGNspectrum.lyric` in GalfitS-Public repository
- Example data: `GreeneHo2004_id2.txt`
- Emission lines defined in: `galfits/emission_lines.py`
