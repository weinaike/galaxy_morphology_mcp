# Data Configuration

This document provides detailed information about configuring input data in GalfitS configuration files, including region information, images, spectra, and image atlases.

## Configuration Example

```text
# Region information
R1) PG0050+124                                  # name of the target
R2) [13.3955833333,12.6933888889]               # sky coordinate of the target [RA, Dec]
R3) 0.061                                       # redshift of the target

# Image A
Ia1) [hst_0_F438W_cut.fits,0]                   # input data image (FITS file)
Ia2) wfc3_f438w                                 # band of the image
Ia3) [hst_0_F438W_cut.fits,2]                   # sigma image (automatic calculate from data if blank or "none")
Ia4) [psf_f438w_cv.fits,0]                      # PSF image
Ia5) 1                                          # PSF fine sampling factor relative to data
Ia6) [hst_0_F438W_cut.fits,1]                   # bad pixel mask image (use empty mask if blank or "none")
Ia7) cR                                         # unit of the image
Ia8) 22.9602936792869                           # size to make cutout image region for fitting, unit arcsec
Ia9) 1.5061352645923459e+18                     # conversion factor from erg/s/cm^2/A to image unit, -1 for default
Ia10) 27.0                                      # magnitude photometric zeropoint
Ia11) uniform                                   # sky model
Ia12) [[0,-0.5,0.5,0.1,1]]                      # sky parameter, [[value, min, max, step, vary]]
Ia13) 0                                         # allow relative shifting
Ia14) [[0,-0.5,0.5,0.1,0],[0,-0.5,0.5,0.1,0]]   # [shiftx, shifty]
Ia15) 1

# Spectrum
Sa1) PG0050+124_spec.txt                        # input spectrum file
Sa2) 1                                          # conversion from spectrum unit to 1e-17 erg/s/cm^2/A
Sa3) [4000,8500]                                # fitting wavelength range
Sa4) 0                                          # use high resolution stellar template

# Image atlas
Aa1) hst                                        # name of the image atlas
Aa2) ['a', 'b']                                 # images in this atlas
Aa3) 0                                          # whether the images have same pixel size
Aa4) 0                                          # link relative shiftings
Aa5) ['a']                                      # spectra
Aa6) [[2]]                                      # aperture size, fiber-[radius], long slit-[PA, a, b], PA respect to positive RA
Aa7) [0]                                        # references images
```

---

## Region Configuration

In GalfitS, configuration files typically begin by defining a region containing the target of interest. The region is defined by directly providing information about the target using three key parameters, each starting with the letter R:

- **R1** - Names the target (e.g., PG0050+124). This name is used as a prefix for output file names.
- **R2** - Specifies the sky coordinates of the target in Right Ascension and Declination. This coordinate information serves as a central point for future image cutouts in imaging fitting. When there are multiple objects of interest, a common center can be used for all.
- **R3** - Indicates the target's redshift, which is essential for transforming the image unit to luminosity based on the luminosity distance at this redshift.

---

## Image Configuration

For fittings involving images, the image input section is indicated by the letter I. Since GalfitS typically handles multiple images, each image should be assigned a distinct character (e.g., Ia, Ib, Ic for three input images). For each image, a comprehensive set of 15 parameters is required:

- **Ia1** - The input data image file, which is a two-element list: the first element is the path of the FITS file, and the second specifies the FITS extension storing the image array.
- **Ia2** - The band of the image.
- **Ia3** - The sigma image, following the same format as the data image. If this list's first element is blank or set to "none", GalfitS automatically calculates a sigma image from the data image using the same method as in GALFIT. Accurate sigma image estimation is crucial for the likelihood value and for balancing weights across different images.
- **Ia4** - The PSF image, in the same format as the data image.
- **Ia5** - The PSF fine sampling factor relative to the data.
- **Ia6** - The bad pixel mask image, which uses '1' for masking and '0' for unmasked pixels. If the first element is blank or "none", an empty mask will be used.
- **Ia7** - The unit of the image; although it does not affect fitting, it serves as a reminder of the image unit and can be ignored if unclear.
- **Ia8** - The size for the cutout image region intended for fitting. When a single float (e.g., s) is provided, the image is cut into a 2s x 2s square box, centered on R2, during fitting. If a list (e.g., [s1, s2, s3, s4]) is provided, a rectangular cutout spanning Delta RA from -s1 to s2 and Delta Dec from -s3 to s4, centered on R2, is used.
- **Ia9** - The conversion factor from erg s^-1 cm^-2 A^-1 to the image unit. If set to -1, GalfitS will use a default value derived from the magnitude zero point of the standard image filter. However, for images like those from 2MASS where the magnitude zero point is not constant, caution is needed with the value of Ia9.
- **Ia10** - The magnitude photometric zeropoint, used mainly in single-band imaging fitting without SED information (Ia15=0). In multi-band image fittings, this parameter can be ignored.
- **Ia11** - The sky model, where both "uniform" and "polynomial" models are acceptable. These represent the background emission during fitting using either a constant value or a polynomial function. To avoid unnecessary parameter degeneracy, users are advised to first perform a sky subtraction and then fix the sky model to uniformly zero.
- **Ia12** - The sky model parameters, which is a list equal in length to one plus the polynomial function's order (a uniform sky model is a zeroth-order polynomial). Each list element is a five-value array: [initial value, minimum value, maximum value, typical variation step, fixed or not]. The typical variation step is useful when applying MCMC optimization, and the last value indicates whether the parameter is free (1) or fixed (0). This five-value array format is standard for initializing parameters in GalfitS.
- **Ia13** - Settings for allowing relative image shifting, typically used when WCS consistency between images, especially from different instruments, is not exact.
- **Ia14** - The shift parameters in the x and y directions, in pixel units, defined using the standard five-value array.
- **Ia15** - When set to 0, shifts GalfitS to a pure photometric tool, akin to GALFIT. By default, it is set to 1, which employs SED information for fitting multi-band images.

---

## Spectrum Configuration

GalfitS is capable of performing fittings solely with spectra or combined fittings with both images and spectra. To input a spectrum, the configuration process starts with the letter S (e.g., Sa, Sb for the first and second spectra when multiple spectra are used). Each spectrum requires four specific parameters:

- **Sa1** - The input spectrum file, which is a standard text file containing three columns: wavelength in angstroms (A), flux, and flux error.
- **Sa2** - The conversion factor from the spectrum unit to 1 x 10^-17 erg s^-1 cm^-2 A^-1. This conversion is crucial to ensure that the spectrum is in the correct unit for analysis.
- **Sa3** - The fitting wavelength range, in units of A, specifies the spectrum segment to be used in the fitting process.
- **Sa4** - Decides whether to use a high-resolution stellar template. If set to 1, the high-resolution template from Starburst99 with a resolution of approximately 30 km s^-1 is employed, which is essential for deriving stellar velocity dispersion. However, it's important to note that the Starburst99 high-resolution template covers only the 3000-7000 A range. Therefore, model spectra outside this range will remain at low resolution. For studies focusing on stellar velocity dispersion, it is recommended to set Sa3 to [3000, 7000].

---

## Image Atlas Configuration

In GalfitS, the final step in defining the input data is integrating the images and spectra into an image atlas object. Configuring an image atlas begins with the letter A and involves seven parameters. Like images and spectra, multiple image atlases are distinguished by identifiers such as Aa, Ab, etc.:

- **Aa1** - Names the image atlas; for example, "hst" could represent the instrument used for the image set.
- **Aa2** - Lists the images included in this atlas; for instance, if images Ia and Ib are included, Aa2 should be ['a', 'b'].
- **Aa3** - Indicates whether the images in the atlas have the same pixel size, with a value of 0 signifying inconsistencies.
- **Aa4** - Links relative shifts among the images, enhancing alignment accuracy within the atlas and avoiding unnecessary parameter degeneracy.
- **Aa5** - Lists the spectra associated with the atlas, similar to the inclusion of images. Notably, both Aa2 and Aa5 can be empty lists, signifying either spectra-only or images-only fitting.
- **Aa6** - Specifies the aperture/slit formation of the input spectrum. The format varies: a single float indicates a fiber ([radius]), and three floats represent a long slit ([PA, a, b]), with PA aligned with positive RA, and a and b as the length and width of the slit in arcseconds.
- **Aa7** - Lists reference images. For example, [0] implies that the first image in this image atlas is used for model spectra calculation. It is advisable to use the image with the highest spatial resolution to ensure the most accurate spectrum integration in the forward modeling process.
