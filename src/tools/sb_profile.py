"""1D Surface Brightness Profile rendering for GALFIT comparison plots.

Provides isophote-based radial profile extraction and matplotlib rendering
of data vs model surface brightness with a residual sub-panel.
"""

import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse as EllipsePatch
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

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


def _run_strategy_grid(image_data, x_center, y_center, maxsma,
                       pa_rad, eps_val, fix_center=False, fix_pa=False):
    """Run the strategy/PA/eps grid search. Returns best IsophoteList or None."""
    strategies = [
        {"sma0": 10.0, "integrmode": "median", "step": 0.2},
        {"sma0": 5.0, "integrmode": "bilinear", "step": 0.15},
        {"sma0": 20.0, "integrmode": "median", "step": 0.2},
        {"sma0": 3.0, "integrmode": "bilinear", "step": 0.1},
    ]
    best_result = None
    for strat in strategies:
        if fix_pa and pa_rad is not None:
            pa_grid = [pa_rad]
        else:
            pa_grid = [pa_rad, 0.0, np.radians(90)]
        for pa in pa_grid:
            if pa is None:
                pa = 0.0
            for ev in [eps_val, 0.1, 0.3, 0.5] if eps_val is None else [eps_val]:
                try:
                    geo = EllipseGeometry(x_center, y_center,
                                          strat["sma0"], ev, pa)
                    ellipse = Ellipse(image_data, geometry=geo)
                    kwargs = dict(
                        sma0=strat["sma0"], minsma=1.0, maxsma=maxsma,
                        step=strat["step"], linear=False,
                        integrmode=strat["integrmode"], sclip=3.0, nclip=3,
                    )
                    if fix_center:
                        kwargs["fix_center"] = True
                    if fix_pa:
                        kwargs["fix_pa"] = True
                    isolist = ellipse.fit_image(**kwargs)
                    if len(isolist) > 5:
                        return isolist
                    if best_result is None or len(isolist) > len(best_result):
                        best_result = isolist
                except Exception:
                    continue
    return best_result


def fit_data_isophotes(image_data, x_center, y_center,
                       pa_deg=None, eps=None, sma_max=None, mask=None):
    """Fit isophotes using photutils with two-pass center refinement.

    Pass 1: free center — fit to determine the average center from inner isophotes.
    Pass 2: fixed center — re-fit with the refined center locked in place.
    """
    if not HAS_PHOTUTILS:
        return None
    if mask is not None:
        image_data = np.ma.array(image_data, mask=mask > 0)
    if np.any(np.isnan(image_data)):
        image_data = np.nan_to_num(image_data, nan=0.0)
    maxsma = sma_max or min(1200.0, max(image_data.shape) * 0.45)
    ny, nx = image_data.shape
    edge_dist = min(x_center, y_center, nx - x_center, ny - y_center)
    maxsma = min(maxsma, edge_dist * 0.9)

    pa_rad = np.radians(pa_deg) if pa_deg is not None else None
    eps_val = eps if eps is not None else None

    # --- Pass 1: free center ---
    pass1 = _run_strategy_grid(image_data, x_center, y_center, maxsma,
                               pa_rad, eps_val, fix_center=False)
    if pass1 is None or len(pass1) == 0:
        return None

    # Find where centers start to drift: use isophotes whose center
    # is consistent with the running median (within 1.5 * MAD).
    valid_isos = [iso for iso in pass1 if iso.valid]
    if len(valid_isos) == 0:
        return pass1
    xs = np.array([iso.x0 for iso in valid_isos])
    ys = np.array([iso.y0 for iso in valid_isos])
    # Cumulative median from smallest sma outward
    cx = np.array([np.median(xs[:i+1]) for i in range(len(xs))])
    cy = np.array([np.median(ys[:i+1]) for i in range(len(ys))])
    dx = xs - cx
    dy = ys - cy
    mad_x = np.median(np.abs(dx))
    mad_y = np.median(np.abs(dy))
    stable = (np.abs(dx) < 1.5 * mad_x + 0.5) & (np.abs(dy) < 1.5 * mad_y + 0.5)
    # Take all stable isophotes up to the first large jump
    if np.any(~stable):
        cutoff = np.argmax(~stable)
        if cutoff == 0:
            cutoff = len(stable)
    else:
        cutoff = len(stable)
    x_refined = np.median(xs[:cutoff])
    y_refined = np.median(ys[:cutoff])

    # Circular mean of PA from stable isophotes
    pas = np.array([iso.pa for iso in valid_isos[:cutoff]])
    pa_refined = np.arctan2(np.mean(np.sin(pas)), np.mean(np.cos(pas)))

    # --- Pass 2: fixed refined center + fixed PA ---
    pass2 = _run_strategy_grid(image_data, x_refined, y_refined, maxsma,
                               pa_refined, eps_val,
                               fix_center=True, fix_pa=True)
    return pass2 if pass2 is not None else pass1


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
                      comp_images=None, comp_types=None, mask=None,
                      isophote_output_path=None):
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
        return

    if param_file is None or model_data is None:
        ax_main.text(0.5, 0.5, 'SB Profile unavailable (missing data)',
                     ha='center', va='center', transform=ax_main.transAxes,
                     fontsize=11, color='gray')
        _style_resid_axes(ax_resid)
        return

    zeropoint, pltscale = parse_photometry_params(param_file)

    # Compute center in cropped-image 0-indexed coordinates
    x_cen = original_data.shape[1] / 2.0
    y_cen = original_data.shape[0] / 2.0
    init_pa, init_eps = None, None
    if components:
        c0 = components[0]
        if fit_region is not None:
            x_cen = c0["x"] - fit_region[0]
            y_cen = c0["y"] - fit_region[2]
        if c0.get("pa"):
            init_pa = c0["pa"]
        if 0 < c0.get("ba", 1) < 1:
            init_eps = 1.0 - c0["ba"]

    sma_max = min(original_data.shape) * 0.45
    isolist = fit_data_isophotes(original_data, x_cen, y_cen,
                                  pa_deg=init_pa, eps=init_eps,
                                  sma_max=sma_max, mask=mask)
    if isolist is None or len(isolist) == 0:
        ax_main.text(0.5, 0.5, 'SB Profile unavailable (isophote fitting failed)',
                     ha='center', va='center', transform=ax_main.transAxes,
                     fontsize=11, color='gray')
        _style_resid_axes(ax_resid)
        return

    # Optionally save isophote overlay as standalone PNG
    if isophote_output_path:
        save_isophote_ellipses(original_data, isolist, isophote_output_path,
                               mask=mask)

    sma_data = isolist.sma
    intens_data = isolist.intens
    mu_data = intensity_to_sb(intens_data, zeropoint, pltscale)
    valid = np.isfinite(mu_data) & (intens_data > 0)
    sma_data = sma_data[valid]
    mu_data = mu_data[valid]

    # Model profile using same geometry
    geometry = [(iso.sma, iso.eps, np.degrees(iso.pa), iso.x0, iso.y0)
                for iso in isolist if iso.valid]
    sma_model, intens_model = extract_profile(model_data, geometry, mask=mask)
    mu_model = intensity_to_sb(intens_model, zeropoint, pltscale)

    # Main SB panel
    ax_main.scatter(sma_data, mu_data, s=8, facecolors='none',
                    edgecolors='black', linewidths=0.4, zorder=5, label='Data')
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
    ax_main.invert_yaxis()
    ax_main.set_xlim(sma_data[sma_data > 0].min() * 0.8, sma_data.max() * 1.1)
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
                             edgecolors='black', linewidths=0.5)
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


def save_isophote_ellipses(image_data, isolist, output_path, mask=None):
    """Draw all fitted isophote ellipses on the image and save as a PNG.

    Args:
        image_data: 2D image array (cropped to fit region).
        isolist: IsophoteList from fit_data_isophotes.
        output_path: Path to save the output PNG file.
        mask: Optional 2D mask array (mask>0 = bad pixel).
    """
    if isolist is None or len(isolist) == 0:
        return
    if not output_path:
        return

    try:
        fig, ax = plt.subplots(figsize=(8, 8))

        display_data = image_data.copy()
        if mask is not None:
            display_data = np.ma.array(display_data, mask=mask > 0)
        vmin = np.percentile(image_data[image_data > 0], 1) if np.any(image_data > 0) else 0
        vmax = np.percentile(image_data[image_data > 0], 99) if np.any(image_data > 0) else 1
        ax.imshow(display_data, origin='lower', cmap='gray',
                  vmin=vmin, vmax=vmax, norm='asinh')

        sma_values = np.array([iso.sma for iso in isolist if iso.valid])
        if len(sma_values) == 0:
            plt.close(fig)
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
                               linewidth=0.8, alpha=0.85)
            ax.add_patch(ell)

        sm = ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        cbar = fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label('Semi-major Axis [pixels]', fontsize=11)

        ax.set_title('Isophote Ellipses', fontsize=13)
        ax.set_xlabel('X [pixels]', fontsize=11)
        ax.set_ylabel('Y [pixels]', fontsize=11)
        ax.set_xlim(-0.5, image_data.shape[1] - 0.5)
        ax.set_ylim(-0.5, image_data.shape[0] - 0.5)

        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        fig.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
    except Exception:
        pass


def _style_resid_axes(ax):
    """Apply shared styling to the residual axes."""
    ax.set_ylabel(r'$\Delta\mu$ (Data $-$ Model)', fontsize=11)
    ax.set_xlabel(r'Semi-major Axis [pixels]', fontsize=11)
    ax.grid(True, which='both', alpha=0.3, linestyle='--')
