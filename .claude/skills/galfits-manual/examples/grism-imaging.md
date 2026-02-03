# Grism Imaging Fitting

Grism (or grizzly) data combines spatial and spectral information in a 2D format, allowing for spatially resolved spectral analysis across a field of view.

## Overview

Grism fitting in GalfitS analyzes data where:
- Each spatial position has a spectrum (data cube format)
- Or each wavelength slice is an image (grism image format)

This example demonstrates how to configure GalfitS for grism/grizzly data analysis.

## Data Format

Grism data typically comes in two formats:

### Format 1: Data Cube

```
[x, y, λ] → flux(x, y, λ)
```

### Format 2: Grism Images

```
Multiple images at different wavelengths
λ₁.fits, λ₂.fits, λ₃.fits, ...
```

## Status

This feature is under active development.

For grism data analysis:
1. Contact GalfitS developers for latest grism support
2. Consider extracting 1D spectra and using [Imaging + Spectrum Joint Fitting](imaging-spectrum-joint.md)
3. Or use [Spectrum Fitting](spectrum-fitting.md) on spatially extracted spectra

## Alternative Approaches

If you have grism data, you can:

### Option 1: Extract 1D Spectra

Extract spectra from different spatial regions (e.g., center, outskirts) and fit separately:

```text
# Create separate spectrum files
center_spectrum.txt
outer_spectrum.txt

# Fit each with spectrum fitting
# See: spectrum-fitting.md
```

### Option 2: Collapse to Broadband Images

Combine grism data into broadband images and use imaging decomposition:

```text
# Sum wavelength ranges to create images
g_band.fits  # 400-500 nm
r_band.fits  # 500-700 nm

# Fit with multi-band imaging
# See: multi-band-imaging.md
```

## Future Documentation

Full grism configuration examples will be added in future GalfitS releases. For now, please refer to:

- GalfitS-Public repository issues and discussions
- GalfitS development team

## Related Examples

- [Imaging + Spectrum Joint Fitting](imaging-spectrum-joint.md) - For combined spatial/spectral analysis
- [Multi-band Imaging](multi-band-imaging.md) - For broadband image decomposition
- [Spectrum Fitting](spectrum-fitting.md) - For 1D spectral analysis
