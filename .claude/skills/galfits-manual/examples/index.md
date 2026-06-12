# Configuration Examples

This section provides complete configuration examples for different GalfitS use cases. Each example demonstrates a specific application scenario.

## Available Examples

| Example | Description | Use When |
|---------|-------------|----------|
| **[Multi-band Imaging Decomposition](multi-band-imaging.md)** | Bulge-disk decomposition with simultaneous SED fitting across multiple bands | You have multi-band imaging data and want to decompose galaxy structure |
| **[Pure SED Fitting](pure-sed-fitting.md)** | Photometric SED fitting from UV to IR using mock images | You only have photometric data points (no images) |
| **[Spectrum Fitting](spectrum-fitting.md)** | AGN/host galaxy spectrum decomposition with emission lines | You have optical/IR spectrum data |
| **[Imaging + Spectrum Joint Fitting](imaging-spectrum-joint.md)** | Simultaneous fitting of images and spectra | You have both imaging and spectroscopic data |
| **[Grism Imaging Fitting](grism-imaging.md)** | Grism data analysis | You have grism/grizzly data |

## Quick Start Example

For a quick introduction to GalfitS, see the **[Multi-band Imaging Decomposition](multi-band-imaging.md)** example which is based on `quickstart.lyric` from the official GalfitS repository.

## Reference Example Files

All example configuration files are available in the GalfitS-Public repository:
- `quickstart.lyric` - Multi-band bulge-disk decomposition
- `SEDfit.lyric` - Pure SED fitting from FUV to FIR
- `AGNspectrum.lyric` - AGN spectrum decomposition

## Performance Benchmarks

Approximate fitting times on different hardware (for reference):

| Example | RTX 4090 | Notes |
|---------|----------|-------|
| Multi-band imaging | ~2-5 mins | Depends on number of bands and components |
| Pure SED fitting | ~1.5 mins | FUV to FIR with dust models |
| Spectrum fitting | ~0.5 mins | AGN spectrum with emission lines |

## File Organization

Example configurations typically follow this structure:

```
your-experiment/
├── config.lyric           # Main configuration file
├── images/                # Science images
│   ├── band_a.fits
│   ├── band_b.fits
│   └── ...
├── psf/                   # PSF images
├── sigma/                 # Noise maps
├── masks/                 # Mask images (optional)
└── output/                # Results directory
    ├── *.gssummary        # Optimization summary
    ├── *.params           # Fitted parameters
    ├── *.sed.png          # SED plot
    └── *.residual.png     # Residual images
```

## Parameter Format Reminder

All GalfitS parameters use the format:

```
[initial_value, min, max, step, vary]
```

- `initial_value`: Starting parameter value
- `min`: Minimum allowed value
- `max`: Maximum allowed value
- `step`: Typical step size for optimization
- `vary`: 1 = free parameter, 0 = fixed parameter
