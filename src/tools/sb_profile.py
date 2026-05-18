"""1D Surface Brightness Profile rendering for GALFIT comparison plots.

Provides isophote-based radial profile extraction and matplotlib rendering
of data vs model surface brightness with a residual sub-panel.
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse as EllipsePatch
from matplotlib.colors import Normalize

try:
    from photutils.isophote import EllipseSample, Ellipse
    from photutils.isophote.geometry import EllipseGeometry
    HAS_PHOTUTILS = True
except ImportError:
    HAS_PHOTUTILS = False

DEFAULT_COLORS = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728',
                  '#9467bd', '#8c564b', '#e377c2', '#7f7f7f']


def parse_photometry_params(param_file: str) -> tuple[float, float]:
    """Parse zeropoint (J) and plate scale (K) from a GALFIT parameter file."""
    zeropoint, pltscale = 21.097, 0.750
    try:
        with open(param_file) as f:
            for line in f:
                s = line.strip()
                if s.startswith("J)"):
                    zeropoint = float(s.split()[1])
                elif s.startswith("K)"):
                    pltscale = float(s.split()[1])
    except Exception:
        pass
    return zeropoint, pltscale


def fit_data_isophotes(image_data, x_center, y_center,
                       pa_deg=None, eps=None, sma_max=None, mask=None):
    """Fit isophotes with fixed center (peak pixel), 2-step approach.

    Step 1: Fixed center, free PA/eps, large maxsma -> find outer boundary + derive PA
    Step 2: Fixed center, fixed PA (from Step 1), free eps, bounded maxsma
    """
    if not HAS_PHOTUTILS:
        return None
    if np.any(np.isnan(image_data)):
        image_data = np.nan_to_num(image_data, nan=0.0)

    dim = min(image_data.shape)

    # ---- Peak pixel as center ----
    if mask is not None and np.any(mask > 0):
        masked = image_data.copy()
        masked[mask > 0] = -np.inf
        peak_y, peak_x = np.unravel_index(np.argmax(masked), masked.shape)
    else:
        peak_y, peak_x = np.unravel_index(np.argmax(image_data), image_data.shape)
    cx, cy = float(peak_x), float(peak_y)

    if mask is not None and np.any(mask > 0):
        image_data = np.ma.MaskedArray(image_data, mask=mask > 0)

    # ---- Initial geometry from image second moments ----
    img_pos = np.maximum(image_data, 0)
    if hasattr(img_pos, 'filled'):
        img_pos = img_pos.filled(0)
    total = np.sum(img_pos)
    if total > 0:
        y_grid, x_grid = np.indices(image_data.shape)
        x2 = np.sum((x_grid - cx)**2 * img_pos) / total
        y2 = np.sum((y_grid - cy)**2 * img_pos) / total
        xy = np.sum((x_grid - cx) * (y_grid - cy) * img_pos) / total
        e0 = 0.2
        pa0 = 0.5 * np.arctan2(2 * xy, x2 - y2)
    else:
        e0, pa0 = 0.2, 0.0

    # ---- Edge noise for outer boundary ----
    edge = max(3, int(dim * 0.1))
    edge_mask = np.zeros(image_data.shape, dtype=bool)
    edge_mask[:edge, :] = True; edge_mask[-edge:, :] = True
    edge_mask[:, :edge] = True; edge_mask[:, -edge:] = True
    edge_pixels = image_data[edge_mask]
    if hasattr(edge_pixels, 'compressed'):
        edge_pixels = edge_pixels.compressed()
    from astropy.stats import sigma_clipped_stats
    _, _, bg_std = sigma_clipped_stats(edge_pixels, sigma=3.0) if len(edge_pixels) > 10 else (0, 0.0, 1.0)
    intensity_threshold = bg_std * 1.0

    maxsma = sma_max or (dim / 2 * 0.9)

    # ---- sma0 retry list ----
    sma0_list = sorted(set(min(s, dim//2 - 2) for s in [3, 5, 10]))

    # ---- Step 1: fixed center, free PA/eps, large maxsma ----
    iso_step1 = None
    maxsma_bounded = None

    for sma0 in sma0_list:
        try:
            geometry = EllipseGeometry(
                x0=int(round(cx)), y0=int(round(cy)),
                sma=sma0, eps=e0, pa=pa0
            )
            ellipse = Ellipse(image_data, geometry)
            iso_step1 = ellipse.fit_image(
                fix_center=True, fix_pa=False, fix_eps=False,
                minsma=1, maxsma=maxsma, step=0.2, maxgerr=0.5
            )
            if iso_step1 is not None and len(iso_step1.sma) > 0:
                # Find outer boundary
                indices = np.where(iso_step1.intens < intensity_threshold)[0]
                out_idx = indices[0] if len(indices) > 0 else len(iso_step1.sma) - 1
                maxsma_bounded = iso_step1.sma[min(out_idx, len(iso_step1.sma) - 1)]
                break
        except Exception:
            continue

    if iso_step1 is None or len(iso_step1.sma) == 0:
        return None

    # ---- Derive PA from Step 1 isophotes within outer boundary ----
    pas = np.array([iso.pa for iso in iso_step1
                    if iso.valid and iso.sma <= maxsma_bounded])
    if len(pas) > 0:
        pa_refined = np.arctan2(np.mean(np.sin(pas)), np.mean(np.cos(pas)))
    else:
        pa_refined = pa0

    # ---- Step 2: fixed center, fixed PA, free eps ----
    iso_best = None

    for sma0 in sma0_list:
        try:
            geometry2 = EllipseGeometry(
                x0=int(round(cx)), y0=int(round(cy)),
                sma=sma0, eps=e0, pa=pa_refined
            )
            ellipse2 = Ellipse(image_data, geometry2)
            iso_best = ellipse2.fit_image(
                fix_center=True, fix_pa=True, fix_eps=False,
                minsma=1, maxsma=maxsma, step=0.1, maxgerr=0.5
            )

            if iso_best is not None and len(iso_best.sma) > 0:
                break
        except Exception:
            continue

    return iso_best if iso_best is not None else iso_step1


def extract_profile(image_data, geometry, x_offset=0, y_offset=0, mask=None):
    """Extract 1D radial profile using pre-fitted isophote geometry.

    Args:
        image_data: 2D image array.
        geometry: List of (sma, eps, pa_deg, x0, y0) tuples.
        x_offset: Offset to subtract from isophote x-center.
        y_offset: Offset to subtract from isophote y-center.
        mask: 2D mask array (mask>0 = bad pixel).

    Returns:
        (sma_array, intensity_array) as numpy arrays.
    """
    if not HAS_PHOTUTILS:
        return np.array([]), np.array([])
    if mask is not None:
        image_data = np.ma.array(image_data, mask=mask > 0)
    sma_arr, intensity_arr = [], []
    for sma, eps, pa_deg, x0, y0 in geometry:
        if sma < 1:
            continue
        try:
            sample = EllipseSample(image_data, sma,
                                   x0=x0 - x_offset, y0=y0 - y_offset,
                                   eps=eps, position_angle=np.radians(pa_deg))
            s = sample.extract()
            if len(s) == 0:
                continue
            intensities = s[2]
            if len(intensities) > 0:
                med = np.median(intensities)
                if med > 1e-5:
                    sma_arr.append(sma)
                    intensity_arr.append(med)
        except Exception:
            continue
    return np.array(sma_arr), np.array(intensity_arr)


def intensity_to_sb(intensity, zeropoint, pixscale):
    """Convert intensity to surface brightness (mag/arcsec²)."""
    with np.errstate(divide='ignore', invalid='ignore'):
        return -2.5 * np.log10(intensity / pixscale ** 2) + zeropoint


def render_sb_profile(ax_main, ax_resid, original_data, model_data,
                      param_file, components, fit_region,
                      comp_images=None, comp_types=None, mask=None):
    """Render 1D SB profile onto a pair of (main, residual) axes.

    Fits isophotes on the original data, extracts profiles for both data and
    model, converts to mag/arcsec², then draws scatter/line plots plus a
    log-scale inset and a Δμ residual panel.

    If photutils is unavailable or isophote fitting fails, a placeholder
    message is drawn instead.

    Args:
        ax_main: Matplotlib Axes for the main SB profile.
        ax_resid: Matplotlib Axes for the residual (Δμ) panel (sharex with ax_main).
        original_data: 2D original image array (cropped to fit region).
        model_data: 2D model image array (same shape as original_data).
        param_file: Path to GALFIT parameter file for zeropoint/plate scale.
        components: List of component dicts from parse_components (may be None).
        fit_region: (xmin, xmax, ymin, ymax) in 1-indexed pixels, or None.
    """
    if not HAS_PHOTUTILS:
        ax_main.text(0.5, 0.5, 'SB Profile unavailable (photutils not installed)',
                     ha='center', va='center', transform=ax_main.transAxes,
                     fontsize=11, color='gray')
        _style_resid_axes(ax_resid)
        return None

    if param_file is None or model_data is None:
        ax_main.text(0.5, 0.5, 'SB Profile unavailable (missing data)',
                     ha='center', va='center', transform=ax_main.transAxes,
                     fontsize=11, color='gray')
        _style_resid_axes(ax_resid)
        return None

    zeropoint, pltscale = parse_photometry_params(param_file)

    sma_max = min(original_data.shape) * 0.45
    isolist = fit_data_isophotes(original_data, 0, 0,
                                  sma_max=sma_max, mask=mask)
    if isolist is None or len(isolist) == 0:
        ax_main.text(0.5, 0.5, 'SB Profile unavailable (isophote fitting failed)',
                     ha='center', va='center', transform=ax_main.transAxes,
                     fontsize=11, color='gray')
        _style_resid_axes(ax_resid)
        return None

    sma_data = isolist.sma
    intens_data = isolist.intens
    int_err_data = getattr(isolist, 'int_err', np.zeros_like(intens_data))
    mu_data = intensity_to_sb(intens_data, zeropoint, pltscale)
    # Propagate intensity error to SB error: dmu = (2.5 / (ln10 * intens)) * int_err
    with np.errstate(divide='ignore', invalid='ignore'):
        muerr_data = (2.5 / (np.log(10) * intens_data)) * int_err_data
    valid = np.isfinite(mu_data) & (intens_data > 0) & np.isfinite(muerr_data)
    sma_data = sma_data[valid]
    mu_data = mu_data[valid]
    muerr_data = muerr_data[valid]

    # Model profile using same geometry
    geometry = [(iso.sma, iso.eps, np.degrees(iso.pa), iso.x0, iso.y0)
                for iso in isolist if iso.valid]
    sma_model, intens_model = extract_profile(model_data, geometry, mask=mask)
    mu_model = intensity_to_sb(intens_model, zeropoint, pltscale)

    # Main SB panel
    # ax_main.scatter(sma_data, mu_data, s=8, facecolors='none',
                    # edgecolors='black', linewidths=0.4, zorder=5, label='Data')
    ax_main.errorbar(sma_data, mu_data, yerr=muerr_data, fmt='o', mfc='none',
                     mec='black', ecolor='black', markersize=3,
                     linewidth=0.4, zorder=5, label='Data')
    ax_main.plot(sma_model, mu_model, 'r--', linewidth=1.2,
                 zorder=4, label='Total Model')

    # Component profiles (image-based from GALFIT subcomps)
    if comp_images and comp_types:
        comp_fluxes = [np.nansum(img) for img in comp_images]
        total_model_flux = np.sum(comp_fluxes)
        comp_fractions = [f / total_model_flux if total_model_flux > 0 else 0
                          for f in comp_fluxes]

        for i, (comp_img, comp_type) in enumerate(zip(comp_images, comp_types)):
            sma_c, intens_c = extract_profile(comp_img, geometry, mask=mask)
            if len(sma_c) == 0:
                continue
            mu_c = intensity_to_sb(intens_c, zeropoint, pltscale)

            color = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
            if comp_type.lower() == 'sersic' and components and i < len(components):
                n_val = components[i].get('n')
                label = f'sersic(n={n_val:.2f}) {comp_fractions[i]:.3f}' if n_val is not None else f'sersic {comp_fractions[i]:.3f}'
            else:
                label = f'{comp_type} {comp_fractions[i]:.3f}'
            ax_main.plot(sma_c, mu_c, '-', color=color, linewidth=1.2,
                         zorder=3, label=label)

    ax_main.set_xscale('log')
    ax_main.set_ylabel(r'Surface Brightness [mag arcsec$^{-2}$]', fontsize=11)
    ax_main.set_xlim(sma_data[sma_data > 0].min() * 0.8, sma_data.max() * 1.1)
    ax_main.set_ylim(mu_data.min() * 0.95, mu_data.max() * 1.05)
    ax_main.invert_yaxis()
    ax_main.legend(loc='lower left', fontsize=9,
                   frameon=True, fancybox=True, framealpha=0.7)
    ax_main.set_title('1D Surface Brightness Profile', fontsize=11)
    ax_main.grid(True, which='both', alpha=0.1, linestyle='--')
    ax_main.tick_params(labelbottom=False)

    # Residual panel: Δμ = μ_data − μ_model
    if len(sma_model) > 2:
        from scipy.interpolate import interp1d
        common_min = max(sma_data.min(), sma_model.min())
        common_max = min(sma_data.max(), sma_model.max())
        cmask = (sma_data >= common_min) & (sma_data <= common_max)
        sma_common = sma_data[cmask]
        mu_data_c = mu_data[cmask]
        model_interp = interp1d(sma_model, mu_model,
                                kind='linear', bounds_error=False,
                                fill_value=np.nan)
        residual = mu_data_c - model_interp(sma_common)
        vresid = np.isfinite(residual)
        if np.any(vresid):
            ax_resid.axhline(0, color='gray', linewidth=0.8)
            ax_resid.scatter(sma_common[vresid], residual[vresid],
                             s=8, facecolors='none',
                             edgecolors='black', linewidths=0.7)
            # Mark out-of-range points (|Δμ| > 0.5) with red triangles
            out_hi = vresid & (residual > 0.5)
            out_lo = vresid & (residual < -0.5)
            if np.any(out_hi):
                ax_resid.scatter(sma_common[out_hi],
                                 np.full(np.sum(out_hi), 0.42),
                                 s=25, c='red', marker='v', zorder=5,
                                 clip_on=True)
            if np.any(out_lo):
                ax_resid.scatter(sma_common[out_lo],
                                 np.full(np.sum(out_lo), -0.42),
                                 s=25, c='red', marker='^', zorder=5,
                                 clip_on=True)
            ax_resid.set_ylim(0.5, -0.5)

    _style_resid_axes(ax_resid)

    return isolist


def render_isophote_panel(ax, image_data, isolist=None, mask=None,
                          norm_params=None):
    """Render isophote ellipses onto an existing axes (for embedding in comparison figure).

    Args:
        isolist: Pre-fitted IsophoteList from render_sb_profile.
        norm_params: Dict with vmin, vmax, asinh_a from render_asinh_panel.
                     If provided, render base image with the same stretch as panel 1.
    """
    if not HAS_PHOTUTILS:
        ax.text(0.5, 0.5, 'photutils not installed',
                ha='center', va='center', transform=ax.transAxes,
                fontsize=11, color='gray')
        return

    if isolist is None or len(isolist) == 0:
        ax.set_title('Isophote Ellipses', fontsize=11)
        return

    # Render base image with same stretch as panel 1
    if norm_params is not None:
        from astropy.visualization import simple_norm
        snorm = simple_norm(image_data, 'asinh',
                            vmin=norm_params["vmin"], vmax=norm_params["vmax"],
                            asinh_a=norm_params["asinh_a"])
        ax.imshow(image_data, origin='lower', cmap='Greys_r', norm=snorm)

    # Mask overlay (same style as first column)
    if mask is not None and np.any(mask > 0):
        mask_overlay = np.zeros((*mask.shape, 4))
        mask_overlay[mask > 0] = [0, 0, 0, 0.7]
        ax.imshow(mask_overlay, origin='lower')

    sma_values = np.array([iso.sma for iso in isolist if iso.valid])
    if len(sma_values) == 0:
        return
    norm = Normalize(vmin=sma_values.min(), vmax=sma_values.max())
    cmap = plt.cm.plasma

    for iso in isolist:
        if not iso.valid:
            continue
        width = 2.0 * iso.sma
        height = 2.0 * iso.sma * (1.0 - iso.eps)
        angle = np.degrees(iso.pa)
        color = cmap(norm(iso.sma))
        ell = EllipsePatch(xy=(iso.x0, iso.y0), width=width, height=height,
                           angle=angle, fill=False, edgecolor=color,
                           linewidth=0.6, alpha=0.85)
        ax.add_patch(ell)

    ax.set_title('Isophote Ellipses', fontsize=11)
    ax.tick_params(labelsize=9)


def _style_resid_axes(ax):
    """Apply shared styling to the residual axes."""
    ax.set_ylabel(r'$\Delta\mu$ (Data $-$ Model)', fontsize=11)
    ax.set_xlabel(r'Semi-major Axis [pixels]', fontsize=11)
    ax.grid(True, which='both', alpha=0.3, linestyle='--')
