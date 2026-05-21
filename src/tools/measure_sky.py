"""Measure sky background from a FITS image via isophote exterior sigma-clipped median."""

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats, SigmaClip


def _isophote_outer_mask(data, mask=None):
    """Fit isophotes using sb_profile.fit_data_isophotes, return boolean mask
    of pixels inside the outermost isophote.

    Returns:
        (inner_mask, outer_sma) or (None, None) on failure.
    """
    try:
        from .sb_profile import fit_data_isophotes
    except ImportError:
        return None, None

    sma_max = min(data.shape) * 0.45
    isolist = fit_data_isophotes(data, 0, 0, sma_max=sma_max, mask=mask)
    if isolist is None or len(isolist) == 0:
        return None, None

    valid_iso = [(iso.sma, iso.eps, iso.pa, iso.x0, iso.y0)
                 for iso in isolist if iso.valid and iso.intens > 0]
    if not valid_iso:
        return None, None

    outer_sma, outer_eps, outer_pa, outer_x0, outer_y0 = valid_iso[-1]

    ny, nx = data.shape
    y_grid, x_grid = np.indices((ny, nx))
    dx = x_grid - outer_x0
    dy = y_grid - outer_y0
    cos_pa = np.cos(outer_pa)
    sin_pa = np.sin(outer_pa)
    x_rot = dx * cos_pa + dy * sin_pa
    y_rot = -dx * sin_pa + dy * cos_pa
    r_ellipse = (x_rot / outer_sma) ** 2 + (y_rot / (outer_sma * (1 - outer_eps))) ** 2
    inner_mask = r_ellipse <= 1.0

    return inner_mask, outer_sma


def estimate_sky(data, mask=None, sigma=3.0, maxiters=10):
    """Estimate sky background as the sigma-clipped median of pixels outside
    the outermost isophote. Negative values are acceptable.

    Falls back to sigma-clipped median of all pixels if isophote fitting fails.

    Returns:
        dict with keys: sky, iso_std, std, iso_sma
    """
    valid = data[np.isfinite(data)]
    if mask is not None:
        valid = valid[~mask.ravel()[:len(valid)]]

    _, fallback_median, fallback_std = sigma_clipped_stats(valid, sigma=sigma, maxiters=maxiters)

    iso_median, iso_std, iso_sma = np.nan, np.nan, None
    inner_mask, iso_sma = _isophote_outer_mask(data, mask)
    if inner_mask is not None and iso_sma is not None:
        combined_mask = inner_mask | (~np.isfinite(data))
        if mask is not None:
            combined_mask = combined_mask | mask
        ext_pix = data[~combined_mask]
        if len(ext_pix) > 100:
            _, iso_median, iso_std = sigma_clipped_stats(ext_pix, sigma=sigma, maxiters=maxiters)

    sky = iso_median if np.isfinite(iso_median) else fallback_median
    std = iso_std if np.isfinite(iso_std) else fallback_std

    return {
        "sky": sky,
        "std": std,
        "iso_std": iso_std,
        "iso_sma": iso_sma,
    }
