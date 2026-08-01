# Imaging + Spectrum Joint Fitting

Joint fitting of imaging and spectroscopic data combines spatial constraints from multi-band images with detailed spectral information from optical/IR spectra, providing the most complete galaxy characterization.

## Overview

This example:
- Simultaneously fits multi-band imaging data + optical spectrum
- Uses spatial information to constrain galaxy structure
- Uses spectral information to constrain stellar populations and AGN
- Achieves better degeneracy breaking than imaging or spectrum alone
- Requires linking spatial and spectral components

## When to Use Joint Fitting

| Situation | Recommended Approach |
|-----------|---------------------|
| High-quality imaging + spectrum available | **Joint fitting** (this example) |
| Only imaging data | [Multi-band Imaging](multi-band-imaging.md) |
| Only photometric catalog | [Pure SED Fitting](pure-sed-fitting.md) |
| Only spectrum data | [Spectrum Fitting](spectrum-fitting.md) |

## Configuration Strategy

### Key Concept: Atlas Integration

The key to joint fitting is properly configuring the **Image Atlas** to include both images and spectra:

```text
Aa1) 'joint'                   # Atlas name
Aa2) ['a','b','c']             # Images (e.g., u, g, r bands)
Aa3) 1                         # Same pixel size
Aa4) 0                         # No linking
Aa5) ['spectrum']              # **Include spectrum here**
Aa6) [[2]]                     # Aperture size [arcsec]
Aa7) [0]                       # Reference image index
```

### Parameter Linking

In joint fitting, the same Profile component must describe:
1. **Spatial structure** (from images)
2. **SED/stellar population** (from images + spectrum)

This means:
- Pa3-Pa8 (spatial) are constrained by **images**
- Pa9-Pa16 (SED) are constrained by **both images and spectrum**
- Emission lines (if any) are constrained by **spectrum**

## Example Configuration

### Region Information

```text
R1) J1234+5678              # Target name
R2) [188.45, 56.78]         # RA, Dec [degrees]
R3) 0.05                    # Redshift
```

### Spectrum Input

```text
# Spectrum A
Sa1) spectrum.txt           # Spectrum file
Sa2) 1                      # Conversion factor
Sa3) [3800., 7200.]         # Wavelength range [Å]
Sa4) 0                      # Template resolution
```

### Image Configuration

```text
# Image A - g-band
Ia1) [g_band.fits, 0]
Ia2) sloan_g
Ia3) [g_band_sigma.fits, 2, 1.8]
Ia4) [g_psf.fits, 0]
Ia5) 1
Ia6) [g_mask.fits, 0]
Ia7) cR
Ia8) 15.0
Ia9) -1
Ia10) 28.33
Ia11) uniform
Ia12) [[0,-0.5,0.5,0.1,0]]
Ia13) 0
Ia14) [[0,-5,5,0.1,0],[0,-5,5,0.1,0]]
Ia15) 1                      # Use SED

# Image B - r-band
Ib1) [r_band.fits, 0]
Ib2) sloan_r
Ib3) [r_band_sigma.fits, 2, 1.8]
Ib4) [r_psf.fits, 0]
Ib5) 1
Ib6) [r_mask.fits, 0]
Ib7) cR
Ib8) 15.0
Ib9) -1
Ib10) 27.04
Ib11) uniform
Ib12) [[0,-0.5,0.5,0.1,0]]
Ib13) 0
Ib14) [[0,-5,5,0.1,0],[0,-5,5,0.1,0]]
Ib15) 1

# Image C - i-band
Ic1) [i_band.fits, 0]
Ic2) sloan_i
Ic3) [i_band_sigma.fits, 2, 1.8]
Ic4) [i_psf.fits, 0]
Ic5) 1
Ic6) [i_mask.fits, 0]
Ic7) cR
Ic8) 15.0
Ic9) -1
Ic10) 26.67
Ic11) uniform
Ic12) [[0,-0.5,0.5,0.1,0]]
Ic13) 0
Ic14) [[0,-5,5,0.1,0],[0,-5,5,0.1,0]]
Ic15) 1
```

### Atlas Configuration (Critical)

```text
Aa1) joint                    # Atlas name
Aa2) ['a','b','c']            # g, r, i images
Aa3) 1                        # Same pixel size
Aa4) 0                        # No linking
Aa5) ['spectrum']             # Include spectrum
Aa6) [[2]]                    # Aperture: 2 arcsec (matches spectrum extraction)
Aa7) [0]                      # Reference image (0 = first image)
```

**Important notes**:
- `Aa5)` lists the **spectrum letter** (not image letters)
- `Aa6)` aperture size should match your spectrum extraction
- `Aa7)` reference image determines spatial coordinate system

### Host Galaxy Profile

```text
# Profile A - Bulge
Pa1) bulge
Pa2) sersic
# Spatial - constrained by IMAGES
Pa3) [0,-5,5,0.1,1]           # x-center (free, fit from images)
Pa4) [0,-5,5,0.1,1]           # y-center
Pa5) [1.5,0.1,5,0.1,1]        # Re [arcsec]
Pa6) [3,0.5,6,0.1,1]          # Sersic n
Pa7) [20,-90,90,1,1]          # PA [deg]
Pa8) [0.7,0.3,1,0.01,1]       # Axis ratio
# SED - constrained by IMAGES + SPECTRUM
Pa9)  [[-2,-4,0,0.1,1]]       # log(sSFR)
Pa10) [[8,0.01,13,0.1,1]]     # Burst age
Pa11) [[0.02,0.001,0.04,0.001,1]]  # Metallicity
Pa12) [[0.5,0,3,0.1,1]]       # Av
Pa13) [100,40,300,1,0]        # Velocity dispersion
Pa14) [10.5,9,11.5,0.1,1]     # log(stellar mass)
Pa15) burst                    # SFH type
Pa16) [-2.5,-4,-2,0.1,0]      # logU
# Dust model
Pa26) [3,0,5,0.1,1]
Pa27) 0
Pa28) [7,4,10,0.1,0]
Pa29) [1,0.1,50,0.1,0]
Pa30) [1,0.47,7.32,0.1,0]
Pa31) [1,1,3,0.1,0]
Pa32) [0.1,0,1,0.1,0]

# Profile B - Disk
Pb1) disk
Pb2) sersic
# Spatial
Pb3) [0,-5,5,0.1,1]
Pb4) [0,-5,5,0.1,1]
Pb5) [3.0,1.0,8,0.1,1]        # Larger Re for disk
Pb6) [1,0.5,2,0.1,1]          # Lower n
Pb7) [25,-90,90,1,1]          # Different PA
Pb8) [0.4,0.2,1,0.01,1]       # Thinner
# SED (different from bulge)
Pb9)  [[-1,-3,0,0.1,1]]       # Higher sSFR
Pb10) [[5,0.01,10,0.1,1]]
Pb11) [[0.02,0.001,0.04,0.001,1]]
Pb12) [[0.3,0,3,0.1,1]]
Pb13) [80,40,200,1,0]
Pb14) [10.5,9,11.5,0.1,1]
Pb15) conti
Pb16) [-3,-4,-2,0.1,0]
Pb26) [3,0,5,0.1,1]
Pb27) 0
Pb28) [7,4,10,0.1,0]
Pb29) [1,0.1,50,0.1,0]
Pb30) [1,0.47,7.32,0.1,0]
Pb31) [1,1,3,0.1,0]
Pb32) [0.1,0,1,0.1,0]
```

### AGN Component (if present)

```text
# Nuclei A
Na1) agn
Na2) [0,-5,5,0.1,0]           # Position (fixed to center)
Na3) [0,-5,5,0.1,0]
Na4) [0.05,0.01,0.5,0.01,0]   # Unresolved point source
Na5) [6,1,10,0.1,0]
Na6) [0,-90,90,1,0]
Na7) [0.1,0.1,1,0.01,0]
# Continuum (constrained by SPECTRUM)
Na8) [[-2,-8,0,0.1,1]]       # log L/LEdd
Na9) [[8,6,10,0.1,1]]        # log Mbh
Na10) [43,41,47,0.1,1]       # log L5100
Na11) [[1,0,4,0.1,1], [0.6, 0, 5, 0.1,0]]
# Emission lines (constrained by SPECTRUM)
Na12) ['Hb','Ha']            # Broad lines
Na13) ['Hb','OIII_5007','Ha','NII_6583']  # Narrow lines
Na14) 2                       # Broad components
Na15) 2                       # Narrow components
Na16) 0
Na17) 1                       # FeII
Na18) 0
```

### Galaxy Configuration

```text
Ga1) mygalaxy
Ga2) ['a','b']                # Bulge + disk
Ga3) [0.05,0.045,0.055,0.01,0] # Redshift
Ga4) 0.0185                   # Distance modulus
Ga5) [1.,0.5,2,0.05,0]
Ga6) ['Hb','OIII_5007','Ha','NII_6583']  # Emission lines
Ga7) 2                        # Components
```

## Running the Fit

```bash
galfits joint_fit.lyric --work ./joint_output/ --num_s 30000
```

Increase `--num_s` compared to imaging-only fits due to additional parameters from spectrum.

## Output Files

| File | Description |
|------|-------------|
| `*.gssummary` | Combined fit statistics |
| `*.sed.png` | SED plot with spectrum data points |
| `*.spec.png` | Spectrum fit with all components |
| `*.residual.png` | Image residuals for each band |

## Advantages of Joint Fitting

### 1. Broken Degeneracies

| Parameter | Imaging Only | Spectrum Only | Joint |
|-----------|--------------|---------------|-------|
| **Stellar mass** | ⚠️ Degenerate with age/dust | ✓ Constrained by absorption lines | ✓✓ Best constrained |
| **SFR** | ⚠️ Degenerate with dust | ✓ From emission lines | ✓✓ Best constrained |
| **Structural parameters** | ✓ From images | ⚠️ Not constrained | ✓ From images |

### 2. Consistent SED

- Same stellar population model describes both images and spectrum
- Ensures physical consistency across wavelengths
- Better constraints on dust extinction (Pa12)

### 3. AGN-Star Decomposition

- AGN continuum better constrained by spectrum
- Host galaxy structure constrained by images
- Reduces AGN-host degeneracy

## Common Issues

### Spectrum Extraction Aperture Mismatch

**Symptom**: Systematic offset in flux between images and spectrum

**Solution**: Match `Aa6)` to actual spectrum extraction aperture:
```text
# If spectrum extracted with 2" radius
Aa6) [[2]]    # 2 arcsec aperture
```

### Different Spatial Coverage

**Symptom**: Images cover larger area than spectrum

**Solution**: This is expected. The spectrum aperture selects a sub-region of the image. GalfitS handles this automatically.

### Convergence Issues

**Symptom**: χ² not decreasing or parameters oscillating

**Solutions**:
1. Start from imaging-only fit results
2. Use conservative initial values
3. Increase `--num_s` to 50000+
4. Fix some parameters initially, then free them

### Wavelength Calibration Mismatch

**Symptom**: Emission lines offset in wavelength

**Solution**: Check spectrum wavelength calibration. Apply redshift correction if needed:
```text
Sa3) [3800/(1+z), 7200/(1+z)]    # Observed frame range
```

## Workflow Recommendation

### Step 1: Imaging-Only Fit

First run with images only to get good spatial parameters:
```text
# Set Aa5) [] to exclude spectrum
Aa5) []
```

### Step 2: Use Imaging Results as Initial Values

Copy spatial parameters (Pa3-Pa8) from imaging fit to joint fit config.

### Step 3: Run Joint Fit

Enable spectrum and run full joint fit:
```text
Aa5) ['spectrum']
```

### Step 4: Iterate

Examine `.params` file and adjust bounds as needed.

## Performance

| Machine | Time (imaging+spec) |
|---------|---------------------|
| RTX 4090 | ~3-5 min |

Longer than imaging-only due to spectrum calculations.

## Related Examples

- [Multi-band Imaging](multi-band-imaging.md) - Imaging-only workflow
- [Spectrum Fitting](spectrum-fitting.md) - Spectrum-only workflow
- [Pure SED Fitting](pure-sed-fitting.md) - Photometry-only

## Reference

- Combine configuration from [Multi-band Imaging](multi-band-imaging.md) and [Spectrum Fitting](spectrum-fitting.md)
- Key: atlas `Aa5)` parameter to include spectrum
