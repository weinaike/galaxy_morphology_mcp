# Galaxy Configuration

Galaxies are defined by combining one or more profile components (Sersic, Fourier Sersic, etc.) into a galaxy model.

## Overview

A galaxy component in GalfitS:
- Uses the `G` prefix (Ga, Gb, Gc, etc.)
- Combines multiple Profile (P) components
- Allows components to share SED parameters while having independent spatial parameters
- Is defined by 7 parameters (Ga1-Ga7)

## Galaxy Parameters (Ga)

A galaxy model requires seven parameters:

```text
# Galaxy A
Ga1) host                                       # name of the galaxy
Ga2) ['a','b']                                  # profile component included
Ga3) [0.061,0.011,0.111,0.01,0]                 # galaxy redshift
Ga4) 0.055                                      # the EB-V of Galactic dust reddening
Ga5) [1.,0.8,1.2,0.05,1]                        # normalization of spectrum when images+spec fitting
Ga6) []                                         # narrow lines in nebular
Ga7) 1                                          # number of components for narrow lines
```

## Parameter Descriptions

| Parameter | Description | Format |
|-----------|-------------|--------|
| **Ga1** | Names the galaxy (e.g., 'host', 'companion') | string |
| **Ga2** | Lists the profile components included in the galaxy model (e.g., ['a', 'b']). Each letter corresponds to a profile defined with `P` prefix | list of strings |
| **Ga3** | Defines the galaxy's redshift | [initial_value, min, max, step, vary] |
| **Ga4** | Specifies the EB-V for Galactic dust reddening | float |
| **Ga5** | Sets the normalization of the spectrum for combined image and spectrum fitting | [initial_value, min, max, step, vary] |
| **Ga6** | Configure narrow lines in the nebular component | list of strings |
| **Ga7** | Number of components for narrow lines | integer |

## Example: Bulge + Disk Galaxy

```text
# Define bulge profile
Pa1) bulge
Pa2) sersic
Pa3) [0,-5,5,0.1,1]                             # x-center [arcsec]
Pa4) [0,-5,5,0.1,1]                             # y-center [arcsec]
Pa5) [1.0,0.1,5.0,0.01,1]                        # Re [arcsec] - smaller
Pa6) [4,1,6,0.1,1]                              # Sersic index - de Vaucouleurs
Pa7) [0,-90,90,1,1]                             # position angle [degree]
Pa8) [0.8,0.6,1,0.01,1]                         # axis ratio
# ... SED parameters ...

# Define disk profile
Pb1) disk
Pb2) sersic
Pb3) [0,-5,5,0.1,1]                             # x-center [arcsec]
Pb4) [0,-5,5,0.1,1]                             # y-center [arcsec]
Pb5) [3.0,0.5,10.0,0.01,1]                      # Re [arcsec] - larger
Pb6) [1,0.5,2,0.1,1]                            # Sersic index - exponential
Pa7) [0,-90,90,1,1]                             # position angle [degree]
Pa8) [0.3,0.1,0.9,0.01,1]                       # axis ratio - thinner
# ... SED parameters ...

# Combine into a galaxy
Ga1) host
Ga2) ['a', 'b']  # Includes both bulge (a) and disk (b) profiles
Ga3) [0.05,0.04,0.06,0.01,0]                 # fixed redshift
Ga4) 0.02                                     # low Galactic extinction
Ga5) [1.,0.8,1.2,0.05,1]
Ga6) []
Ga7) 1
```

## Example: Complex Galaxy with Bar

```text
# Define three profiles: bulge, disk, bar
Pa1) bulge
Pa2) sersic
# ... bulge parameters ...

Pb1) disk
Pb2) sersic
# ... disk parameters ...

Pc1) bar
Pc2) sersic_f                                  # Fourier mode for bar
# ... bar parameters including Fourier modes ...

# Combine all three
Ga1) host
Ga2) ['a', 'b', 'c']  # bulge, disk, and bar
# ... rest of galaxy parameters ...
```

## Key Points

1. **Shared SED**: When multiple profiles are combined in a galaxy, they share the same SED framework by default, allowing physical consistency between components.

2. **Independent Spatial Parameters**: Each profile maintains its own spatial parameters (center, Re, n, PA, q) for flexibility in modeling galaxy structure.

3. **Component Letters**: The letters in `Ga2` (e.g., 'a', 'b', 'c') correspond to the Profile components (Pa, Pb, Pc) defined elsewhere in the config file.

4. **Multiple Galaxies**: You can define multiple galaxies (Ga, Gb, Gc, etc.) to model interacting systems or multiple objects in the same field.

## Common Patterns

| Galaxy Type | Profile Components | Typical Sersic Indices |
|-------------|-------------------|------------------------|
| Elliptical | Single Sersic | n = 4-8 |
| Spiral (bulge+disk) | Two Sersic | bulge n=4, disk n=1 |
| Spiral with bar | Sersic + Sersic + Fourier | bulge n=4, disk n=1, bar with Fourier |
| Lenticular | Sersic + Sersic | bulge n=2-4, disk n=1 |
| Irregular | Multiple Fourier profiles | Varies |

See also: [Parameter Format & Combining](parameter-format.md) for more on how to effectively combine components.
