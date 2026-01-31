---
name: galfits-manual
description: Galaxy imaging spectra fitting tool for multi-band image decomposition, SED modeling, and joint analysis. Covers .lyric config files, command-line arguments, and MCP interface.
---

# GalfitS Configuration Guide

This skill provides comprehensive guidance for writing GalfitS configuration files (`.lyric` format). GalfitS extends GALFIT to simultaneously fit multiple photometric bands with SED constraints.

## Quick Start

To create a new GalfitS config file:

1. **Identify your fitting phase:**
   - **Phase 1**: Image-only fitting (spatial parameters free, SED fixed)
   - **Phase 2**: SED-only fitting (spatial fixed, SED free)
   - **Phase 3**: Joint optimization (all parameters free)

2. **Configure data input** (Region → Images → Spectra → Atlas)
3. **Define model components**:
   - **Profile components (P)**: Galaxy structural components with types specified by `Pa2` parameter
     - `sersic` - Standard bulge/disk profile
     - `sersic_f` - Fourier mode profile (spiral arms, bars, etc.)
     - `ferrer`, `edgeondisk`, `GauRing`, `const`, `Gaussian` - Other profiles
   - **Nuclei/AGN components (N)**: AGN with full SED model
   - **Foreground stars (F)**: Stellar sources with blackbody SED
   - **Galaxy components (G)**: Combine multiple Profile (P) components
4. **Set parameter constraints** (optional)
5. **Run GalfitS** with appropriate command-line options

## Navigation

| Topic | File | Description |
|-------|------|-------------|
| **Data Configuration** | [data-config.md](data-config.md) | Region (R1-R3), Images (Ia1-Ia15), Spectra (Sa1-Sa4), Atlas (Aa1-Aa7) |
| **Model Components** | [model-components/](model-components/) | Galaxy, Profile (Sersic, Fourier), Nuclei/AGN, Foreground Star |
| **Parameter Constraints** | [constraints/](constraints/) | MSR, MMR, SFH, AGN relations, parameter files, priors |
| **Configuration Examples** | [examples/](examples/) | Multi-band, SED-only, spectrum, joint fitting examples |
| **Running GalfitS** | [running-galfits.md](running-galfits.md) | Command-line arguments, fitting methods, usage examples |

## Component Type Quick Reference

GalfitS has TWO main categories of components. Understanding which category you need is the first step.

---

### 📊 Category 1: Data Components (Input Configuration)

**Purpose**: Define your observational data (images, spectra) before fitting.

| Component | Prefix | Parameters | Use For | Docs |
|-----------|--------|------------|---------|------|
| **Region** | `R` | R1-R3 | Target name, coordinates, redshift | [data-config.md](data-config.md) |
| **Image** | `I` | Ia1-Ia15 | Single-band image data, PSF, sigma, mask | [data-config.md](data-config.md) |
| **Spectrum** | `S` | Sa1-Sa4 | Optical/IR spectrum data | [data-config.md](data-config.md) |
| **Atlas** | `A` | Aa1-Aa7 | Group images/spectra for joint fitting | [data-config.md](data-config.md) |

---

### 🌌 Category 2: Model Components (What You're Fitting)

**Purpose**: Define the physical components that make up your source.

#### Component Hierarchy

```
Model Components
│
├── 🔹 Galaxy (G) ──────────────► Combines Profiles into ONE physical galaxy
│    └── contains Profile (P) components
│
├── 🔹 Profile (P) ─────────────► Galaxy structural component (bulge, disk, bar...)
│    └── Type determined by Pa2 parameter
│
├── 🔹 Nuclei/AGN (N) ──────────► AGN with full SED model (independent component)
│
└── 🔹 Foreground Star (F) ────► Star in Milky Way (independent component)
```

#### Detailed Model Component Reference

| Component | Prefix | Parameters | Purpose | Documentation |
|-----------|--------|------------|---------|---------------|
| **Galaxy** | `G` | Ga1-Ga7 | Container that **combines** multiple Profile (P) components into one physical galaxy | [galaxy.md](model-components/galaxy.md) |
| **Profile** | `P` | Pa1-Pa32 | Structural component (bulge, disk, bar). **Type set by Pa2**: | See below ↓ |
| **Nuclei/AGN** | `N` | Na1-Na27 | Central AGN with continuum, emission lines, torus | [nuclei-agn.md](model-components/nuclei-agn.md) |
| **Foreground Star** | `F` | Fa1-Fa8 | Milky Way star with blackbody SED | [foreground-star.md](model-components/foreground-star.md) |

---

#### Profile (P) Sub-Types (determined by `Pa2` parameter)

| Profile Type | Pa2 Value | Use When You See... | Documentation |
|-------------|-----------|--------------------|---------------|
| **Sersic** | `sersic` | Elliptical galaxy, bulge, disk, any axisymmetric structure | [profile-sersic.md](model-components/profile-sersic.md) |
| **Fourier Sersic** | `sersic_f` | Spiral arms, bar, non-axisymmetric features | [profile-fourier.md](model-components/profile-fourier.md) |
| **Ferrer Bar** | `ferrer` | Bar with flat inner core | [profile-other.md](model-components/profile-other.md) |
| **Edge-on Disk** | `edgeondisk` | Galaxy viewed edge-on | [profile-other.md](model-components/profile-other.md) |
| **Gaussian Ring** | `GauRing` | Ring or lens structure | [profile-other.md](model-components/profile-other.md) |
| **Gaussian** | `Gaussian` | Unresolved point source | [profile-other.md](model-components/profile-other.md) |
| **Constant** | `const` | Flat background | [profile-other.md](model-components/profile-other.md) |

---

### 🎯 Quick Decision Guide

**What do you want to model?**

| Your Goal | Which Component? |
|-----------|------------------|
| **A galaxy with bulge + disk** | Galaxy (G) containing 2+ Profile (P) components |
| **Just the bulge or disk** | Single Profile (P) component |
| **Spiral arms or a bar** | Profile with `Pa2) sersic_f` |
| **An AGN / central black hole** | Nuclei/AGN (N) component |
| **A star in the field** | Foreground Star (F) component |
| **Multiple galaxies** | Multiple Galaxy (G) components: Ga, Gb... |
| **Galaxy + AGN together** | Galaxy (G) for host + Nuclei/AGN (N) for center |

---

### 📝 Component Letter Rules

**Important**: Letters (a, b, c...) are **user-defined**, NOT fixed to types.

| ❌ Wrong | ✅ Correct |
|---------|-----------|
| Pa = Sersic<br>Pb = Fourier<br>Pc = AGN | Pa2) determines type:<br>- Pa2) `sersic` → Sersic<br>- Pa2) `sersic_f` → Fourier<br>- Use Na prefix for AGN |

**Example**:
```text
# All three are Sersic profiles, just with different parameters
Pa1) bulge    Pa2) sersic
Pb1) disk     Pb2) sersic     ← Same type!
Pc1) bar      Pc2) sersic_f   ← Different type (Fourier)

# AGN is a DIFFERENT component type (N prefix, not P)
Na1) AGN      (not Pa1)       ← Separate component type
```

---

### Parameter Format Reminder

All parameters use: `[value, min, max, step, vary]`

## Phase-Specific Configuration

| Phase | Ia15 (Use SED) | Spatial (Pa3-Pa8) | SED (Pa9-Pa16) |
|-------|---------------|-------------------|----------------|
| 1 | 0 | vary=1 | vary=0 |
| 2 | 1 | vary=0 | vary=1 |
| 3 | 1 | vary=1 | vary=1 |

## Running GalfitS

### Basic Command

```bash
galfits config.lyric --work ./output
```

For detailed command-line arguments, fitting methods, and usage examples, see **[running-galfits.md](running-galfits.md)**.

### Quick Reference

| Task | Command |
|------|---------|
| **Quick fit** | `galfits config.lyric --work ./output` |
| **Bayesian analysis** | `galfits config.lyric --work ./output --fit_method dynesty --nlive 200` |
| **Refine previous** | `galfits config.lyric --work ./output --readpar previous.params` |
| **With constraints** | `galfits config.lyric --work ./output --priorpath priors.txt` |

## Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Parameter hits limit | Expand min/max bounds |
| Band misalignment | Set `Ia13) 1` and adjust `Ia14)` shift ranges |
| Off-center residual | Add new component at that position |
| Circular center residual | Add bulge/AGN component |
| Bar-like residual | Add bar component (set `Pa2) sersic_f`) |
| Fitting fails to converge | See [running-galfits.md](running-galfits.md) for troubleshooting |

## File Structure

```
your-config/
├── SKILL.md                    # This file - main entry point
├── running-galfits.md          # Command-line arguments & usage
├── data-config.md              # Data input configuration
├── model-components/           # Model component documentation
│   ├── index.md                # Component overview
│   ├── galaxy.md               # Galaxy configuration
│   ├── profile-sersic.md       # Sersic profile
│   ├── profile-fourier.md      # Fourier Sersic profile
│   ├── profile-other.md        # Other profile types
│   ├── nuclei-agn.md           # Nuclei/AGN configuration
│   ├── foreground-star.md      # Foreground star configuration
│   └── parameter-format.md     # Parameter format and combining
├── constraints/                # Parameter constraints & priors
│   ├── index.md                # Constraints overview
│   ├── parameter-files.md      # .params and .constrain file usage
│   ├── mass-size-relation.md   # MSR prior for size-mass correlation
│   ├── mass-metallicity-relation.md  # MMR prior for stellar mass-metallicity
│   ├── sfh-constraints.md      # Star formation history priors
│   ├── agn-constraints.md      # AGN-specific constraints
│   ├── gaussian-priors.md      # GP parameter for Gaussian priors
│   └── energy-balance.md       # EB parameter for dust-stellar energy balance
├── examples/                   # Configuration examples
│   ├── index.md                # Examples overview
│   ├── multi-band-imaging.md   # Multi-band bulge-disk decomposition
│   ├── pure-sed-fitting.md     # Photometric SED fitting
│   ├── spectrum-fitting.md     # AGN/host spectrum decomposition
│   ├── imaging-spectrum-joint.md  # Joint imaging+spectra fitting
│   └── grism-imaging.md        # Grism data analysis (placeholder)
└── templates/                  # Config templates (optional)
```

## Usage

To use this configuration guide, refer to the detailed documentation files:

- **Data Configuration**: [data-config.md](data-config.md) - Region, Images, Spectra, Atlas
- **Model Components**: [model-components/](model-components/) - Galaxy, Profile, Nuclei/AGN, Foreground Star
- **Parameter Constraints**: [constraints/](constraints/) - Parameter files, MSR, MMR, SFH, AGN, priors
- **Configuration Examples**: [examples/](examples/) - Multi-band, SED-only, spectrum fitting
- **Running GalfitS**: [running-galfits.md](running-galfits.md) - Command-line options, fitting methods

### Model Components Quick Links

- **[Galaxy](model-components/galaxy.md)** - Combine profile components into galaxies
- **[Sersic Profile](model-components/profile-sersic.md)** - Standard bulge/disk profile
- **[Fourier Sersic](model-components/profile-fourier.md)** - Bars and spiral arms
- **[Other Profiles](model-components/profile-other.md)** - Ferrer, edge-on, ring, Gaussian
- **[Nuclei/AGN](model-components/nuclei-agn.md)** - AGN with full SED model
- **[Foreground Star](model-components/foreground-star.md)** - Stellar sources
- **[Parameter Format](model-components/parameter-format.md)** - Parameter combining and format

### Constraints Quick Links

- **[Parameter Files](constraints/parameter-files.md)** - .params and .constrain file usage
- **[Mass-Size Relation](constraints/mass-size-relation.md)** - MSR prior for galaxy size-mass correlation
- **[Mass-Metallicity Relation](constraints/mass-metallicity-relation.md)** - MMR prior for stellar mass-metallicity
- **[SFH Constraints](constraints/sfh-constraints.md)** - Star formation history priors
- **[AGN Constraints](constraints/agn-constraints.md)** - Black hole mass, emission line relations
- **[Gaussian Priors](constraints/gaussian-priors.md)** - GP parameter for Gaussian priors
- **[Energy Balance](constraints/energy-balance.md)** - EB parameter for dust-stellar energy balance

---
