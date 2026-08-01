# GALFIT Fitting Summary

**Output File:** `/home/wnk/code/galaxy_morphology_mcp/tests/test_data/NGC1097_galfit.fits`

---

## Init. par. file Content

# GALFIT feedme file for NGC1097
#   NGC1097_ddbarf.outgal
# Date: 2026-04-12
#



================================================================================
# IMAGE and GALFIT CONTROL PARAMETERS
A) NGC1097.phot.1_nonan.fits      # Input data image (FITS file)
B) NGC1097_galfit.fits      # Output data image block
C) NGC1097_sigma2014.fits      # Sigma image name (made from data if blank or "none")
D) PSF-1.composite.fits          # Input PSF image and (optional) diffusion kernel
E) 5                   # PSF fine sampling factor relative to data
F) NGC1097.1.finmask_nonan.fits      # Bad pixel mask (FITS image or ASCII coord list)
G) none                # File with parameter constraints (ASCII file)
H) 37   1467 25   1455 # Image region to fit (xmin xmax ymin ymax)
I) 50     50           # Size of the convolution box (x y)
J) 21.097              # Magnitude photometric zeropoint
K) 0.750  0.750        # Plate scale (dx dy)   [arcsec per pixel]
O) regular             # Display type (regular, curses, both)
P) 0                   # Choose: 0=optimize, 1=model, 2=imgblock, 3-subcomps

# INITIAL FITTING PARAMETERS
#
#   For component type, the allowed functions are:
#       sersic, expdisk, edgedisk, devauc, king, nuker, psf,
#       gaussian, moffat, ferrer, and sky.
#
#   Hidden parameters will only appear when they're specified:
#       Bn (n=integer, Bending Modes).
#       C0 (diskyness/boxyness),
#       Fn (n=integer, Azimuthal Fourier Modes).
#       R0-R10 (coordinate rotation, for creating spiral structures).
#       To, Ti, T0-T10 (truncation function).
#
# ------------------------------------------------------------------------------
#   par)    par value(s)    fit toggle(s)    # parameter description
# ------------------------------------------------------------------------------

# Component number: 1
# STRUCTURE: BULGE (n fixed to physical value)
 0) sersic                 #  Component type
 1) 752.60 740.30 1 1      #  Position x, y
 3) 11.50       1          #  Integrated magnitude
 4) 8.0         1          #  R_e (effective radius)   [pix]
 5) 2.0         1          #  Sersic index n (fixed to avoid collapse)
 6) 0.0000      0          #     -----
 7) 0.0000      0          #     -----
 8) 0.0000      0          #     -----
 9) 0.90        1          #  Axis ratio (b/a)
10) -40.0       1          #  Position angle (PA) [deg: Up=0, Left=90]
 Z) 0                      #  Skip this model in output image?  (yes=1, no=0)

================================================================================

# Component number: 2
# STRUCTURE: DISK
 0) expdisk                #  Component type
 1) 752.60 740.30 1 1      #  Position x, y
 3) 9.17        1          #  Integrated magnitude
 4) 70.80       1          #  R_s (disk scale-length) [pix]
 5) 0.0000      0          #     -----
 6) 0.0000      0          #     -----
 7) 0.0000      0          #     -----
 8) 0.0000      0          #     -----
 9) 0.45        1          #  Axis ratio (b/a)
10) -33.23      1          #  Position angle (PA) [deg: Up=0, Left=90]
 Z) 0                      #  Skip this model in output image?  (yes=1, no=0)

================================================================================

# Component number: 3
# STRUCTURE: BAR
 0) ferrer                 #  Component type
 1) 752.60 740.30 1 1      #  Position x, y
 3) 18.0        1          #  Surface brightness at FWHM [mag/arcsec^2]
 4) 200.0       1          #  Outer truncation radius [pix]
 5) 3.0         1          #  Alpha (outer truncation sharpness)
 6) 0.0         1          #  Beta (central slope)
 7) 0.0000      0          #     -----
 8) 0.0000      0          #     -----
 9) 0.25        1          #  Axis ratio (b/a)
10) -45.0       1          #  Position angle (PA) [deg: Up=0, Left=90]
 Z) 0                      #  Skip this model in output image?  (yes=1, no=0)

================================================================================

# Component number: 4
# STRUCTURE: AGN (PSF)
 0) psf                    #  Component type
 1) 752.60 740.30 1 1      #  Position x, y
 3) 12.5        1          #  Integrated magnitude
 4) 0.0000      0          #     -----
 5) 0.0000      0          #     -----
 6) 0.0000      0          #     -----
 7) 0.0000      0          #     -----
 8) 0.0000      0          #     -----
 9) 1.0000      -1         #  (固定 b/a=1)
10) 0.0000      -1         #  (固定 PA=0)
 Z) 0                      #  Skip this model in output image?  (yes=1, no=0)

================================================================================

## Fit log Content
Input image     : NGC1097.phot.1_nonan.fits[37:1467,25:1455] 
Init. par. file : /home/wnk/code/galaxy_morphology_mcp/tests/test_data/NGC1097.feedme 
Restart file    : galfit.01 
Output image    : NGC1097_galfit.fits 
 sersic    : (  752.70,   740.25)   10.30     12.10    0.17    0.91   -36.28
             (    0.00,     0.00)    0.00      0.00    0.00    0.00     0.14
 expdisk   : (  763.68,   753.41)    9.41    133.45    0.72   -43.77
             (    0.09,     0.12)    0.00      0.17    0.00     0.10
 ferrer    : (  750.60,   737.80)  18.56  403.81    5.91  1.08   0.37   -32.84
             (    0.02,     0.02)   0.01    5.28    0.14  0.01   0.00     0.02
 psf       : (  752.73,   740.10)   13.16
             (    0.00,     0.00)    0.00
 Chi^2 = 47897893.57348,  ndof = 1403536
 Chi^2/nu = 34.127 
---

## Observation Metadata

| Property | Value |
|----------|-------|
| Object | NGC1097[37:1467,25:1455] |
| Telescope | Spitzer |
| Instrument | IRAC |
| Filter | Unknown |
| Exposure Time | 1. s |
| Observation Date | Unknown |
| Image Size | 1431 × 1431 pixels |

### World Coordinate System (WCS)

| Parameter | Value |
|-----------|-------|
| CRPIX1 | 750.0 |
| CRPIX2 | 739.0 |
| CRVAL1 (RA) | 41.57955 |
| CRVAL2 (Dec) | -30.2749 |
| CTYPE1 | RA---TAN |
| CTYPE2 | DEC--TAN |

---

*Generated by GALFIT MCP Server*