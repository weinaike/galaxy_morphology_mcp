# Parameter Constraints & Astrophysical Priors

This section describes how to apply parameter constraints and astrophysical priors in GalfitS to reduce parameter degeneracies and incorporate physical knowledge into the fitting process.

## Overview

GalfitS provides **two main methods** for constraining parameters:

| Method | Type | Description | When to Use |
|--------|------|-------------|-------------|
| **Parameter Files** | Hard/Soft constraints | Direct parameter linking via `.params` and `.constrain` files | Simple parameter relationships, AGN line correlations |
| **Astrophysical Priors** | Probabilistic constraints | MSR, MMR, SFH, AGN relations with scatter | Incorporate known physical relations with uncertainty |

## Documentation Files

| Topic | File | Description |
|-------|------|-------------|
| **Parameter Files** | [parameter-files.md](parameter-files.md) | `.params` and `.constrain` file format and usage |
| **Mass-Size Relation** | [mass-size-relation.md](mass-size-relation.md) | MSR prior for galaxy size-mass correlation |
| **Mass-Metallicity Relation** | [mass-metallicity-relation.md](mass-metallicity-relation.md) | MMR prior for stellar mass-metallicity correlation |
| **SFH Constraints** | [sfh-constraints.md](sfh-constraints.md) | Star formation history prior constraints |
| **AGN Constraints** | [agn-constraints.md](agn-constraints.md) | AGN-specific parameter relations |
| **Gaussian Priors** | [gaussian-priors.md](gaussian-priors.md) | GP parameter for Gaussian priors |
| **Energy Balance** | [energy-balance.md](energy-balance.md) | EB parameter for energy balance constraints |

## Constraint Type Summary

| Constraint Type | Parameter Prefix | Purpose | Reference |
|----------------|------------------|---------|-----------|
| **MSR** | `MSR` (MSRa, MSRb...) | Mass-size relation | van der Wel+14 |
| **MMR** | `MMR` (MMRa, MMRb...) | Mass-metallicity relation | Kewley & Ellison+08 |
| **SFH** | `SFH` (SFHa, SFHb...) | Star formation history | - |
| **AGN** | `AGN` (AGNa, AGNb...) | Black hole mass, emission lines | Kormendy & Ho+13, Greene+20 |
| **Gaussian Prior** | `GP` | Parameter-specific Gaussian priors | - |
| **Energy Balance** | `EB` | UV-optical/dust energy balance | CIGALE |

## Quick Decision Guide

**What constraint do you need?**

| Your Goal | Which Method? |
|-----------|---------------|
| **Link AGN center to host galaxy** | Parameter Files → Constraint File |
| **Link emission line fluxes to luminosity** | Parameter Files → Constraint File |
| **Apply known mass-size relation** | Astrophysical Priors → MSR |
| **Apply mass-metallicity relation** | Astrophysical Priors → MMR |
| **Constrain star formation history** | Astrophysical Priors → SFH |
| **Link black hole mass to stellar mass** | Astrophysical Priors → AGN |
| **Apply Gaussian prior to specific parameter** | Astrophysical Priors → GP |
| **Ensure energy balance between stars and dust** | Astrophysical Priors → EB |

## Command-Line Usage

### Parameter Files

```bash
PYTHON galfitS.py --config filename --readpar pfile --parconstrain cfile
```

- `--readpar`: Path to parameter file (`.params`)
- `--parconstrain`: Path to constraint file (`.constrain`)

### Astrophysical Priors

```bash
PYTHON galfitS.py --config filename --priorpath priorfile
```

- `--priorpath`: Path to prior file containing MSR, MMR, AGN, etc.

## How Parameter Files Are Generated

After executing a GalfitS run, two files are automatically generated in the specified `savepath` directory:

1. **`targetname.params`** - Machine-readable table with all parameters
2. **`targetname.constrain`** - Python file for complex constraint functions

These files can be edited and reused in subsequent runs.

## Key Concepts

### Hard vs Soft Constraints

| Type | Description | Example |
|------|-------------|---------|
| **Hard Constraint** | Parameter is exactly linked to another | `agn_x = host_x` |
| **Soft Constraint** | Prior with scatter (probabilistic) | MSR with sigma=0.1 |

### When to Use Each

- **Parameter Files**: Use when you need exact relationships between parameters
- **Astrophysical Priors**: Use when incorporating physical relations with known scatter

## File Structure

```
constraints/
├── index.md                      # This file - overview
├── parameter-files.md            # .params and .constrain usage
├── mass-size-relation.md         # MSR configuration
├── mass-metallicity-relation.md  # MMR configuration
├── sfh-constraints.md            # SFH prior configuration
├── agn-constraints.md            # AGN relations
├── gaussian-priors.md            # GP parameter
└── energy-balance.md             # EB parameter
```

## See Also

- [Model Components](../model-components/) - Component configuration
- [Parameter Format](../model-components/parameter-format.md) - General parameter information
