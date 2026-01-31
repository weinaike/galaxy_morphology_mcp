# Model Components

This section provides detailed documentation for all model components available in GalfitS.

## Component Naming Convention

**Important**: Component letters (a, b, c, etc.) are user-defined and do not determine the component type. The component type is specified by parameters within each section:

- **Profile components (P)**: The `Pa2` parameter determines the profile type (sersic, sersic_f, ferrer, etc.)
- **Nuclei/AGN components (N)**: These are always AGN models
- **Foreground stars (F)**: These are always stellar models
- **Galaxy components (G)**: These combine multiple Profile (P) components

## Component Types

| Component Type | Prefix | Description | Documentation |
|---------------|--------|-------------|---------------|
| **Profile (Galaxy)** | `P` (Pa, Pb, Pc...) | Galaxy structural components. Profile type determined by `Pa2` parameter | [Sersic](profile-sersic.md), [Fourier](profile-fourier.md), [Other](profile-other.md) |
| **Nuclei/AGN** | `N` (Na, Nb, Nc...) | AGN/Nuclei component with full AGN SED model | [Nuclei/AGN](nuclei-agn.md) |
| **Foreground Star** | `F` (Fa, Fb, Fc...) | Foreground star with stellar SED | [Foreground Star](foreground-star.md) |
| **Galaxy** | `G` (Ga, Gb, Gc...) | Combines multiple Profile (P) components into one galaxy | [Galaxy](galaxy.md) |

## Profile Types (specified via Pa2 parameter)

| Profile Type | Value for Pa2 | Use Case |
|-------------|---------------|----------|
| Sersic | `sersic` | Standard galaxy bulge/disk, elliptical profile |
| Fourier Sersic | `sersic_f` | Non-axisymmetric profile with spiral/boxy/disky features |
| Ferrer Bar | `ferrer` | Ferrer profile bar |
| Edge-on Disk | `edgeondisk` | Edge-on disk profile |
| Gaussian Ring | `GauRing` | Ring-like structure |
| Constant | `const` | Constant/flat profile |
| Gaussian | `Gaussian` | Gaussian component |

## Documentation Files

- **[Galaxy Configuration](galaxy.md)** - How to define galaxy models that combine profile components
- **[Sersic Profile](profile-sersic.md)** - Standard Sersic profile for bulges and disks
- **[Fourier Sersic Profile](profile-fourier.md)** - Fourier mode profiles for spiral arms and bars
- **[Other Profile Types](profile-other.md)** - Ferrer, edge-on disk, Gaussian ring, constant, Gaussian
- **[Nuclei/AGN](nuclei-agn.md)** - AGN component with full SED model
- **[Foreground Star](foreground-star.md)** - Foreground star modeling
- **[Parameter Format & Combining](parameter-format.md)** - Parameter format and how to combine components

## Quick Reference

### Parameter Format

All parameters in GalfitS follow a standard five-value array format:

```text
[initial_value, minimum_value, maximum_value, typical_step, vary_flag]
```

- **initial_value**: Starting value for the parameter
- **minimum_value**: Lower bound for the parameter
- **maximum_value**: Upper bound for the parameter
- **typical_step**: Typical variation step (useful for MCMC optimization)
- **vary_flag**: Whether the parameter is free (1) or fixed (0)

See [Parameter Format & Combining](parameter-format.md) for more details.
