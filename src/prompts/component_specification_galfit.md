# Component Addition Specification

## GALFIT component-type rules (must be strictly followed)
- To add a Disk, use component type `expdisk`.
- To add a Bulge, use component type `sersic`.
- When the added Bulge has Re < 0.2 pixel, the type must be changed to `psf` (it has collapsed to a point source); when Re is in the 0.2–0.5 pixel border zone, you may create a competing `psf` variant for comparison — adopt the `psf` only if the residuals are clearly better, otherwise keep `sersic`; for Re > 0.5 px keep the `sersic` type.
- To add a Bar, use a `sersic` model with n = 0.5 [fixed].
- To add a Lens, use a `sersic` model with n < 0.5 and q > 0.5; the initial values follow the total-order chain Re_disk > Re_lens > Re_bar > Re_bulge (compare only the central components that actually exist: remove the missing ones from the chain and require strict decrease over the survivors).
- If the galaxy already has a Disk component and the outskirts are still unfitted, a second Disk or Sérsic component may be added (typically with large Re and n < 1) to capture the more extended structure.
- If the galaxy is an elliptical and a single component suffices, use a `sersic` model directly:
  - However, if a single-`sersic` fit returns an axis ratio q < 0.5, the elliptical classification must be revisited — a disk is implied, and where there is a disk, a bulge or further components should be considered.
- Azimuthal Fourier modes may only be attached to a Disk component, never to a Bulge or Bar. For a single-component model they may be attached to the (single) Sersic.

## Reference for setting initial component parameters

The following acquisition rules are common to all model types:
- x and y (centre position): read the pixel coordinates of the component's brightness peak directly from the image. When multiple components are concentric (e.g. bulge + disk), add a constraint that binds their initial x, y together to enforce concentricity. (No co-alignment of PA is required.)
- mag (integrated magnitude): for multi-component fits, set the initial value by adjusting from the existing Sérsic magnitude. Avoid initial values that are so far off that the fit fails:
  - a. Inter-component flux contrast is best described in three tiers: "Comparable",
  - b. "Faint" (about 1/3),
  - c. "Much fainter" (1–1.5 mag fainter).
- b/a (axis ratio): estimate visually. 1 = circular; the flatter, the closer to 0.
- PA (position angle): the angle of the major axis measured counterclockwise from the +Y axis (usually North). Estimate the initial value from the original image; for a Bar this initial value is especially important.
- When splitting a single component into two (e.g. replacing one Sérsic with Bulge + Disk):
  - Flux split: partition the measured total flux 3:7 or 4:6 and convert each share into a magnitude for the bulge and the disk.
  - Size split:
    - The bulge's initial R_e is typically 1/5 to 1/3 of the total photometric radius;
    - The disk's initial R_e must be larger than the single Sérsic's Re (the exact value follows the mid- and outer-range behaviour of the 1D surface-brightness residual profile: the Disk must be able to carry the flux in that region).
    - The initial R_s of an `expdisk` can be estimated as the measured disk half-light radius divided by 1.678.
- If Bar signatures are visible in the residual map and the original image, consider adding a Bar component:
  - Initial Bar parameters:
    - n fixed at 0.5,
    - initial axis ratio b/a in the range 0.2–0.4,
    - PA initialised from the measured major-axis direction of the bar in the image,
    - size parameter R_e set between those of the bulge and the disk,
    - mag set following the flux-split tiers above,
  - When adding the Bar, also adjust the Disk's initial Re so that the overall budget remains sensible.
**Always base estimates and edits on the previous round's fitting results (a copy thereof) so that successive rounds improve incrementally — never restart from scratch.**

## Component parameter definitions
1. sersic — commonly used for BULGE / Bar

```
0) sersic                 #  Component type
1) <x>  <y>  1 1          #  Position x, y
3) <mag>       1          #  Integrated magnitude
4) <R_e>       1          #  R_e (effective radius) [pix]
5) <n>         1          #  Sersic index n (de Vaucouleurs n=4)
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) 0.0000      0          #  -----
9) <b/a>       1          #  Axis ratio (b/a)
10) <PA>       1          #  Position angle (PA) [deg]
Z) 0                      #  Skip this model? (yes=1, no=0)
```

---
1. expdisk — commonly used for DISK

```
0) expdisk                #  Component type
1) <x>  <y>  1 1          #  Position x, y
3) <mag>       1          #  Integrated magnitude
4) <R_s>       1          #  R_s (disk scale-length) [pix]
5) 0.0000      0          #  -----
6) 0.0000      0          #  -----
7) 0.0000      0          #  -----
8) 0.0000      0          #  -----
9) <b/a>       0          #  Axis ratio (b/a)
10) <PA>       0          #  Position angle (PA) [deg: Up=0, Left=90]
Z) 0                      #  Skip this model?
```

Key parameter: R_s (disk scale length)
- R_s (scale length): the distance over which the surface brightness falls by a factor of e (about 2.718). Its relation to the effective radius is R_e ≈ 1.678 R_s. Initialisation: if you know the disk's half-light radius (from photometry or a visual estimate of the disk's extent), divide by 1.678 to get the initial R_s. By eye, R_s is roughly 1/3 to 1/4 of the disk's overall visible radius.

Note (this workflow): the template's `9) b/a` and `10) PA` toggles shown as `0` above are **not** used here — in this workflow the Disk's q and PA are **free** (`1`); oblique disk configurations are legal search directions (see the solution-space definition in CLAUDE.md).

---
1. psf — (commonly used for an AGN / a star / an extremely compact nucleus)

```
0) psf                    #  Component type
1) <x>  <y>  1 1          #  Position x, y
3) <mag>       1          #  Integrated magnitude
Z) 0                      #  Skip this model?
```

Key parameters: only the x, y position and the integrated magnitude; all shape parameters are fixed.
- x, y (centre position): must be extremely precise. Usually lock onto the brightest pixel of the image.
- mag (magnitude): if there is an obvious compact bright core (e.g. an AGN), estimate the point source's magnitude; a small-aperture photometry measurement is a reasonable initial value.

Note (companions, this workflow): choose the companion's type by the **area rule** (beam prompt C3): `psf` when the blob is unresolved (A_blob ≤ 1.5·A_psf with A_psf = π·(FWHM_PSF/2)², and not visibly elongated), `sersic` when resolved (A_blob ≥ 2.3·A_psf or elongated major/minor ≳ 1.3); the border zone defaults to `psf`. A `sersic` companion collapsing to Re < 0.2 px has become a point source and switches to `psf`.


---
1. sky — used to model the background

```
0) sky
1) <sky>      1       # sky background       [ADU counts]
2) 0.000      0       # dsky/dx (sky gradient in x)
3) 0.000      0       # dsky/dy (sky gradient in y)
Z) 0                  #  Skip this model in output image?  (yes=1, no=0)
```
The <sky> value must reference the sky-background data shown on the 1D SB profile; before fitting, <sky> must be fixed to the sky-background value.

Note (this workflow): the sky is **never fitted** — the sky block (value + toggle) is the manually provided setting of the input feedme and is carried verbatim into every `_iter{n}.feedme`; it is never backfilled from converged values and never freed or re-tuned (the sky is not a search dimension; see the solution-space definition in CLAUDE.md).

## Higher-order component parameters — used only when fitting higher-order structural features.
The parameters C0, B1, B2, F1, F2, etc. listed below are hidden from the user unless explicitly requested. These can be tagged on to the end of any previous component except, of course, the PSF and the sky — If a Fourier or Bending amplitude is set to 0 initially, GALFIT will reset it to a value of 0.01. To prevent GALFIT from doing so, one can set it to any other value.

```
- Bending modes
B1)  0.07      1       # Bending mode 1 (shear)
B2)  0.01      1       # Bending mode 2 (banana shape)
B3)  0.03      1       # Bending mode 3 (S-shape)

- Azimuthal fourier modes
F1)  0.07  30.1  1  1  # Az. Fourier mode 1, amplitude and phase angle (amplitude above the 0.02 threshold may be kept; no need to remove it)

- Traditional diskyness/boxyness parameter c
C0) 0.1         0      # traditional diskyness(-)/boxyness(+)
```

## How to set sensible constraints (best practice)

Constraints are the safety net that stops the optimiser from "running away", but a net woven too tight strangles legitimate exploration. The recommended pipeline strategy:

- Impose physically motivated soft bounds (hard bounds) via the `.cons` constraint file, drawing absolute intervals for the key parameters that are safe yet not cramped:
  - Centre coordinates (x, y): constrain within ±2 to ±5 pixels of the initial value (relax for heavily disturbed mergers). Never let the galaxy centre drift to the image edge.
  - Effective radius R_e: minimum 0.1 pixel (or half the PSF size), maximum 1/2 or 1/3 of the image side length, preventing the model from inflating without bound while trying to fit a flat background.
  - Sérsic index n: the parameter most prone to running away. For genuine galaxy structure, physically sensible n lies roughly in 0.1–8.0; enforce that range.
  - Axis ratio b/a: constrain to 0.05–1.0 so a low-SNR disk cannot be crushed into a physically meaningless infinitely thin line.

Note (this workflow): in the single-band beam flow these recommended bounds are not optional — the orchestrator writes them as the **mandatory default bound set** every round, merged into `iter{n}.cons`; see the solution-space definition in CLAUDE.md (which also fixes the Re floor at `max(0.1, 0.5 × PSF FWHM)` px and the Re cap at half the fit-region side).

```text
# Component/    parameter   constraint    Comment
# operation (see below)   range

  3_2_1_9        x          offset      # Hard constraint: Constrains the
  3_2_1_9        y          offset      # x,y parameter for components 3, 2,
                                        # 1, and 9 to have RELATIVE positions
                                        # defined by the initial parameter file.
```
