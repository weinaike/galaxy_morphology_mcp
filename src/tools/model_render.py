"""
Lightweight model image renderer for GalfitS.
Renders galaxy model images from .gssummary parameter files using pure numpy/scipy.
保留 extract_component_attributes 及其依赖代码
"""

import re
import numpy as np
from astropy.io import fits
from typing import Any, Annotated, List, Dict
import subprocess
import shutil
import tempfile
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from astropy.io import fits
import datetime
import glob
import os
from .render_original import render_asinh_panel
from .sb_profile import render_sb_profile
from .parse_lyric import parse_image_infos_from_lyric, ImageInfo

# ---------- 常量 ----------
_DEG2RAD = 0.01745329

# ---------- 依赖工具函数 ----------
def parse_gssummary(filepath):
    """
    Parse a .gssummary file into a flat parameter dictionary.
    """
    params = {}
    config_file = None
    in_free = False
    in_fixed = False

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if line.startswith('# config file:'):
                config_file = line.split(':', 1)[1].strip()

            if line.startswith('# free parameters'):
                in_free = True
                in_fixed = False
                continue
            if line.startswith('# fixed parameters'):
                in_free = False
                in_fixed = True
                continue
            if line.startswith('#########################################'):
                in_free = False
                in_fixed = False
                continue

            if line.startswith('#'):
                if in_fixed and 'pname' in line:
                    continue
                continue

            parts = line.split()
            if len(parts) >= 2:
                try:
                    name = parts[0]
                    value = float(parts[1])
                    params[name] = value
                except ValueError:
                    continue

    return params, config_file


def parse_component_types(config_file):
    """
    Extract component names and profile types from a .lyric config file.
    """
    components = {}
    profile_key_re = re.compile(r'^P([a-z])1\)')
    current_prefix = None
    current_name = None

    with open(config_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            match_name = re.match(r'^P([a-z])1\)\s*(.*)', line)
            if match_name:
                current_prefix = match_name.group(1)
                current_name = match_name.group(2).strip().split()[0]
                continue

            if current_prefix is not None:
                match_type = re.match(
                    r'^P{0}2\)\s*(.*)'.format(current_prefix), line)
                if match_type:
                    ptype = match_type.group(1).strip().split()[0]
                    if current_name and ptype:
                        components[current_name] = ptype
                    current_prefix = None
                    current_name = None

    return components


def _infer_type(params, comp_name):
    """Try to infer component type from available parameter names."""
    has = lambda s: f'{comp_name}_{s}' in params

    if has('Rout') and has('alpha') and has('beta'):
        if has('r_in') and has('alpha_rc'):
            return 'ferrer_f'
        return 'ferrer'
    if has('rs') and has('hs'):
        return 'edgeondisk'
    if has('r0') and has('sig'):
        if has('r_in') and has('alpha_rc'):
            return 'GauRing_f'
        return 'GauRing'
    if has('Re') and has('n'):
        if has('r_in') and has('alpha_rc'):
            return 'sersic_f'
        if has('r_out'):
            return 'sersic_b'
        if has('r_in') and has('width'):
            return 'sersic_r'
        return 'sersic'
    return 'const'


def _extract_fits_metadata(fits_file, ra=None, dec=None):
    """
    Extract image shape, pixel scale, and reference pixel from a FITS file.
    """
    from astropy.io import fits
    from astropy.wcs import WCS

    with fits.open(fits_file) as hdul:
        header = hdul[0].header
        data_shape = hdul[0].data.shape
        wcs = WCS(header)

    ny, nx = data_shape[0], data_shape[1]
    shape = (ny, nx)

    try:
        from astropy.wcs.utils import proj_plane_pixel_scales
        scales = proj_plane_pixel_scales(wcs) * 3600.
        pixsc = float(scales[0])
    except Exception:
        cdelt1 = abs(header.get('CDELT1')) * 3600.
        pixsc = float(cdelt1)

    x0 = nx / 2.
    y0 = ny / 2.
    if ra is not None and dec is not None:
        try:
            px, py = wcs.all_world2pix(ra, dec, 1)
            x0, y0 = float(px), float(py)
        except Exception:
            pass

    delta_ang = 0.
    try:
        srcXp, srcYp = wcs.all_world2pix(x0 if ra is None else ra,
                                          y0 if dec is None else dec, 1)
        srcPstXY_ra = wcs.all_world2pix(
            (x0 if ra is None else ra) + 1./60,
            (y0 if dec is None else dec), 1)
        srcPstXY_dec = wcs.all_world2pix(
            (x0 if ra is None else ra),
            (y0 if dec is None else dec) + 1./60, 1)
        dx = srcPstXY_dec[0] - srcXp
        dy = srcPstXY_dec[1] - srcYp
        delta_ang = float((np.degrees(np.arctan2(dy, dx)) + 360) % 360)
    except Exception:
        pass

    return shape, pixsc, x0, y0, delta_ang, wcs

# ---------- 核心映射表 ----------
_SIZE_PARAM_MAP = {
    'sersic': 'Re',
    'sersic_b': 'Re',
    'sersic_r': 'Re',
    'sersic_f': 'Re',
    'sersic_bf': 'Re',
    'sersic_rf': 'Re',
    'ferrer': 'Rout',
    'ferrer_f': 'Rout',
    'GauRing': 'r0',
    'GauRing_f': 'r0',
    'edgeondisk': 'rs',
}

_SERSIC_TYPES = {'sersic', 'sersic_b', 'sersic_r', 'sersic_f', 'sersic_bf', 'sersic_rf'}

# ---------- 你需要的主函数 ----------
def extract_component_attributes(
    summary_file,
    config_file=None,
    pixsc=None,
    x0=None,
    y0=None,
    fits_file=None,
    band=None,
    ra=None,
    dec=None,
):
    """
    Extract fitted attributes for every component from a .gssummary file.
    """
    from typing import Any

    # 解析参数文件
    params, summary_config_file = parse_gssummary(summary_file)

    if config_file is None and summary_config_file is not None:
        config_file = summary_config_file

    # 解析图像元数据
    wcs = None
    if fits_file is not None:
        _shape, _pixsc, _x0, _y0, _delta_ang, _wcs = _extract_fits_metadata(
            fits_file, ra=ra, dec=dec)
        if pixsc is None:
            pixsc = _pixsc
        if x0 is None:
            x0 = _x0
        if y0 is None:
            y0 = _y0
        wcs = _wcs
    if pixsc is None:
        raise ValueError("pixsc must be provided (or use fits_file)")
    if x0 is None:
        x0 = 0.
    if y0 is None:
        y0 = 0.

    # 获取组件类型
    comp_types = {}
    if config_file is not None:
        try:
            comp_types = parse_component_types(config_file)
        except Exception:
            pass

    # 提取所有组件名称
    _known_suffixes = {
        'xcen', 'ycen', 'Re', 'n', 'ang', 'axrat',
        'Rout', 'alpha', 'beta', 'rs', 'hs',
        'r0', 'sig', 'r_in', 'r_out',
        'width', 'alpha_rc', 'theta_out', 'm', 'am',
        'theta_m', 'i_arm',
    }
    comp_names = set()
    for key in params:
        if key.startswith('logM_'):
            comp_names.add(key[5:])
        else:
            for suf in _known_suffixes:
                if key.endswith('_' + suf):
                    prefix = key[:-(len(suf) + 1)]
                    if prefix:
                        comp_names.add(prefix)
                    break

    # 组装结果
    result: list[dict[str, Any]] = []
    for comp_name in sorted(comp_names):
        p = lambda s: params.get(f'{comp_name}_{s}')

        # 组件类型
        if comp_name in comp_types:
            ptype = comp_types[comp_name]
        else:
            ptype = _infer_type(params, comp_name)

        # 中心坐标（角秒 → 像素）
        xcen_arcsec = p('xcen') or 0.
        ycen_arcsec = p('ycen') or 0.
        if wcs is not None and ra is not None and dec is not None:
            x_pix, y_pix = wcs.all_world2pix(
                ra + xcen_arcsec / 3600., dec + ycen_arcsec / 3600., 1)
        else:
            x_pix = x0 - xcen_arcsec / pixsc
            y_pix = y0 + ycen_arcsec / pixsc

        # 星等
        mag = None
        for key, val in params.items():
            if band is not None:
                if key == f'Mag_{comp_name}_{band}':
                    mag = float(val)
                    break
            elif key.startswith(f'Mag_{comp_name}_'):
                mag = float(val)
                break

        # 尺寸参数
        re_pix = None
        size_key = _SIZE_PARAM_MAP.get(ptype)
        if size_key is not None:
            size_arcsec = p(size_key)
            if size_arcsec is not None:
                re_pix = size_arcsec / pixsc

        # Sersic 指数
        n_val = p('n') if ptype in _SERSIC_TYPES else None

        # 轴比与位置角
        ba = p('axrat')
        pa = p('ang')

        result.append({
            'name': comp_name,
            'type': ptype,
            'x': float(x_pix),
            'y': float(y_pix),
            'mag': float(mag) if mag is not None else None,
            're': float(re_pix) if re_pix is not None else None,
            'n': float(n_val) if n_val is not None else None,
            'ba': float(ba) if ba is not None else None,
            'pa': float(pa) if pa is not None else None,
        })

    return result

def create_comparison_png(
    lyric_file: str,
    gssummary_file: str,
    result_fits_file_list: List[str],
) -> Dict[str, str] | None:
    image_infos = parse_image_infos_from_lyric(lyric_file)
    pngs = {}
    for image_info in image_infos:
        band = image_info.band
        result_fits_file = [
            fits_file for fits_file in result_fits_file_list if fits_file.find(band) != -1
        ]
        if result_fits_file is None or len(result_fits_file) != 1:
            pngs[band] = "comparison png not created: no unique result fits file found for band %s" % band
            continue
        result_fits_file = result_fits_file[0]

        with fits.open(result_fits_file) as hdul:
            if len(hdul) != 5:
                pngs[band] = "comparison png not created: expected 5 HDUs in result fits file, found %d" % len(hdul)
                continue
            original_data = hdul[4].data    
            model_data = hdul[3].data
            residual_data = hdul[0].data
            mask_data = hdul[1].data
            if mask_data is None:
                mask_data = np.zeros_like(original_data, dtype=np.float)
            mask = np.where(mask_data > 0, 1, 0)

        region = None
        components = extract_component_attributes(
            summary_file=gssummary_file,
            config_file=lyric_file,
            fits_file=image_info.image[0],
            band=image_info.band
        )
        comp_imgs, comp_types = _generate_subcomps(image_info, components)

        fig = plt.figure(figsize=(24, 16))
        gs = GridSpec(2, 3, figure=fig, wspace=0.18, hspace=0.28,
                      width_ratios=[1, 1, 1])
        fig.subplots_adjust(left=0.05, right=0.97, top=0.88, bottom=0.05)

        # === Row 0, Col 0: Original Image (99.5th percentile) ===
        ax1 = fig.add_subplot(gs[0, 0])
        orig_info = render_asinh_panel(ax1, original_data, mask, region=region,
                                       show_isophotes=True)
        title_orig = (
            f"Original Data (vmax=99.5th pctl)\n"
            f"asinh: a={orig_info['asinh_a']:.4f}, vmin={orig_info['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]"
        )
        ax1.set_title(title_orig, fontsize=10, pad=10)
        ax1.set_xlabel('X (pixels)', fontsize=12)
        ax1.set_ylabel('Y (pixels)', fontsize=12)

        # === Row 0, Col 1: Original Image (99.99th percentile) ===
        ax1b = fig.add_subplot(gs[0, 1])
        orig_info_9999 = render_asinh_panel(ax1b, original_data, mask, region=region,
                                            show_isophotes=True, vmax_percentile=99.99)
        title_orig_9999 = (
            f"Original Data (vmax=99.99th pctl)\n"
            f"asinh: a={orig_info_9999['asinh_a']:.4f}, vmin={orig_info_9999['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]"
        )
        ax1b.set_title(title_orig_9999, fontsize=10, pad=10)
        ax1b.set_xlabel('X (pixels)', fontsize=12)
        ax1b.tick_params(labelleft=False)

        # === Row 0, Col 2: Isophote Ellipses ===
        # (placed here so isolist is computed later via SB profile)
        # We'll render isophotes after SB profile computes isolist

        # === Row 1, Col 0: Model Image (same asinh stretch as original 99.5) ===
        ax2 = fig.add_subplot(gs[1, 0])
        if model_data is not None:
            render_asinh_panel(ax2, model_data, mask, region=region,
                               show_isophotes=False, show_mask=False,
                               norm_params=orig_info,
                               components=components,
                               fit_region=region)
        else:
            ax2.text(0.5, 0.5, 'No Model', ha='center', va='center', transform=ax2.transAxes)

        title_model = (
            f"GALFIT Model\n"
            f"Same asinh stretch as original (99.5th pctl)\n"
            f"2*$R_e$ contours of component [cyan]"
        )
        ax2.set_title(title_model, fontsize=10, pad=10)
        ax2.set_xlabel('X (pixels)', fontsize=12)
        ax2.set_ylabel('Y (pixels)', fontsize=12)

        # === Row 1, Col 1: Residual Image (significance map, ±10σ range, seismic) ===
        ax3 = fig.add_subplot(gs[1, 1])
        im3 = None
        if residual_data is not None:
            resid_display = residual_data.copy()
            resid_display[~np.isfinite(resid_display)] = 0

            # Normalize by background std from original image (significance map)
            bg_std = orig_info.get("std", 1.0)
            if bg_std > 0:
                resid_norm = resid_display / bg_std
            else:
                resid_norm = resid_display
            # Set masked pixels to 0 before normalization
            if mask is not None:
                resid_norm[mask > 0] = 0
            # Compute extent for real pixel coordinates from fit_region
            if region is not None:
                xmin, xmax, ymin, ymax = region
                plot_extent = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]
            else:
                plot_extent = None

            im3 = ax3.imshow(resid_norm, cmap='seismic', vmin=-10, vmax=10,
                           origin='lower', extent=plot_extent, interpolation='nearest',
                           aspect='auto')

            # Overlay mask on residual (Opaque White)
            if mask is not None:
                mask_overlay = np.zeros((*mask.shape, 4))
                mask_overlay[mask > 0] = [1, 1, 1, 0.7]
                ax3.imshow(mask_overlay, origin="lower", extent=plot_extent, interpolation='nearest')

        else:
            ax3.text(0.5, 0.5, 'No Residual', ha='center', va='center', transform=ax3.transAxes)

        title_resid = (
            f"Residual/$\\sigma$\n"
            f"Normalized by bg $\\sigma$ of original image\n"
            f"Range: $\\pm$10$\\sigma$, white=masked"
        )
        ax3.set_title(title_resid, fontsize=10, pad=10)
        ax3.set_xlabel('X (pixels)', fontsize=12)
        ax3.tick_params(labelleft=False)

        # Add colorbar for residual (right side)
        if im3 is not None:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=9)
            cbar.set_label('Residual (σ)', fontsize=9)

        # === Row 1, Col 2: 1D Surface Brightness Profile ===
        gs_sb = GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[1, 2],
                                         height_ratios=[3, 1], hspace=0.05)
        ax_sb = fig.add_subplot(gs_sb[0])
        ax_sb_resid = fig.add_subplot(gs_sb[1], sharex=ax_sb)
        # TODO: 传入param_file只是为了获取zero_point和pltscale
        # comp_images和comp_types暂时先传入None，后续如果需要在SB profile里渲染组件图标再完善
        isolist = render_sb_profile(
            ax_sb, ax_sb_resid, original_data, model_data,
            None, components, region,
            comp_images=comp_imgs, comp_types=comp_types,
            mask=mask, zeropoint=image_info.magzp, pixscale=image_info.pixscale
        )

        # === Row 0, Col 2: Isophote Ellipses (rendered after isolist is computed) ===
        from .sb_profile import render_isophote_panel
        ax_iso = fig.add_subplot(gs[0, 2])
        render_isophote_panel(ax_iso, original_data, isolist=isolist,
                              mask=mask, norm_params=orig_info)
        ax_iso.set_xlabel('X (pixels)', fontsize=12)
        ax_iso.tick_params(labelleft=False)

        # Save figure
        fits_dir = os.path.dirname(result_fits_file)
        base_name = os.path.splitext(os.path.basename(result_fits_file))[0]
        png_filename = os.path.join(fits_dir, f"{base_name}_comparison.png")
        target_dpi = 1024 / 15
        plt.savefig(png_filename, dpi=target_dpi)
        plt.close(fig)

        pngs[band] = png_filename
    return pngs
            
    
    
def create_comparison_png_v2(
    lyric_file: str,
    gssummary_file: str,
    result_fits_file_list: List[str],
) -> str | None:
    """
    Create a single multi-band comparison PNG with all bands stacked vertically.

    Each band occupies 2 rows (6 panels):
      Row 0: Original (99.5) | Original (99.99) | Isophotes
      Row 1: Model           | Residual / sigma  | SB Profile

    Band name header above each group, horizontal separator between bands.
    Returns the path to the saved PNG, or None if no valid bands found.
    """
    image_infos = parse_image_infos_from_lyric(lyric_file)

    # --- Collect valid band data ---
    band_data = []
    for image_info in image_infos:
        band = image_info.band
        matched = [f for f in result_fits_file_list if band in f]
        if len(matched) != 1:
            continue
        result_fits_file = matched[0]

        with fits.open(result_fits_file) as hdul:
            if len(hdul) != 5:
                continue
            original_data = hdul[4].data
            model_data = hdul[3].data
            residual_data = hdul[0].data
            mask_data = hdul[1].data
            if mask_data is None:
                mask_data = np.zeros_like(original_data, dtype=float)
            mask = np.where(mask_data > 0, 1, 0)

        components = extract_component_attributes(
            summary_file=gssummary_file,
            config_file=lyric_file,
            fits_file=image_info.image[0],
            band=image_info.band,
        )

        comp_imgs, comp_types = _generate_subcomps(image_info, components)

        band_data.append({
            'band': band,
            'image_info': image_info,
            'result_fits_file': result_fits_file,
            'original_data': original_data,
            'model_data': model_data,
            'residual_data': residual_data,
            'mask': mask,
            'components': components,
            'comp_imgs': comp_imgs,
            'comp_types': comp_types,
        })

    if not band_data:
        return None

    n_bands = len(band_data)

    # --- Build GridSpec height_ratios ---
    # Per band: header(0.08) + row0(1) + row1(1); between bands: separator(0.04)
    height_ratios = []
    for i in range(n_bands):
        height_ratios.append(0.08)   # header
        height_ratios.append(1.0)    # plot row 0
        height_ratios.append(1.0)    # plot row 1
        if i < n_bands - 1:
            height_ratios.append(0.04)  # separator
    n_rows = len(height_ratios)

    fig_height = 14 * n_bands
    fig = plt.figure(figsize=(24, fig_height))
    gs = GridSpec(n_rows, 3, figure=fig,
                  wspace=0.18, hspace=0.30,
                  width_ratios=[1, 1, 1],
                  height_ratios=height_ratios)
    fig.subplots_adjust(left=0.05, right=0.97, top=0.97, bottom=0.02)

    # --- Render each band ---
    current_row = 0
    for band_idx, bdata in enumerate(band_data):
        image_info = bdata['image_info']
        original_data = bdata['original_data']
        model_data = bdata['model_data']
        residual_data = bdata['residual_data']
        mask = bdata['mask']
        components = bdata['components']
        comp_imgs = bdata['comp_imgs']
        comp_types = bdata['comp_types']
        region = None

        # ---- Header row (band name) ----
        ax_header = fig.add_subplot(gs[current_row, :])
        ax_header.set_axis_off()
        ax_header.text(
            0.5, 0.3, f"Band: {bdata['band']}",
            transform=ax_header.transAxes,
            fontsize=16, fontweight='bold',
            ha='center', va='center',
        )
        current_row += 1

        r0 = current_row       # first plot row
        r1 = current_row + 1   # second plot row

        # ---- Row 0, Col 0: Original (99.5th percentile) ----
        ax1 = fig.add_subplot(gs[r0, 0])
        orig_info = render_asinh_panel(
            ax1, original_data, mask, region=region, show_isophotes=True)
        ax1.set_title(
            f"Original Data (vmax=99.5th pctl)\n"
            f"asinh: a={orig_info['asinh_a']:.4f}, "
            f"vmin={orig_info['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]",
            fontsize=10, pad=10)
        ax1.set_xlabel('X (pixels)', fontsize=12)
        ax1.set_ylabel('Y (pixels)', fontsize=12)

        # ---- Row 0, Col 1: Original (99.99th percentile) ----
        ax1b = fig.add_subplot(gs[r0, 1])
        orig_info_9999 = render_asinh_panel(
            ax1b, original_data, mask, region=region,
            show_isophotes=True, vmax_percentile=99.99)
        ax1b.set_title(
            f"Original Data (vmax=99.99th pctl)\n"
            f"asinh: a={orig_info_9999['asinh_a']:.4f}, "
            f"vmin={orig_info_9999['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]",
            fontsize=10, pad=10)
        ax1b.set_xlabel('X (pixels)', fontsize=12)
        ax1b.tick_params(labelleft=False)

        # ---- Row 1, Col 0: Model ----
        ax2 = fig.add_subplot(gs[r1, 0])
        if model_data is not None:
            render_asinh_panel(
                ax2, model_data, mask, region=region,
                show_isophotes=False, show_mask=False,
                norm_params=orig_info,
                components=components,
                fit_region=region)
        else:
            ax2.text(0.5, 0.5, 'No Model',
                     ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title(
            f"GALFIT Model\n"
            f"Same asinh stretch as original (99.5th pctl)\n"
            f"2*$R_e$ contours of component [cyan]",
            fontsize=10, pad=10)
        ax2.set_xlabel('X (pixels)', fontsize=12)
        ax2.set_ylabel('Y (pixels)', fontsize=12)

        # ---- Row 1, Col 1: Residual / sigma ----
        ax3 = fig.add_subplot(gs[r1, 1])
        im3 = None
        if residual_data is not None:
            resid_display = residual_data.copy()
            resid_display[~np.isfinite(resid_display)] = 0

            bg_std = orig_info.get("std", 1.0)
            resid_norm = resid_display / bg_std if bg_std > 0 else resid_display
            if mask is not None:
                resid_norm[mask > 0] = 0
            plot_extent = None
            if region is not None:
                xmin, xmax, ymin, ymax = region
                plot_extent = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]

            im3 = ax3.imshow(
                resid_norm, cmap='seismic', vmin=-10, vmax=10,
                origin='lower', extent=plot_extent,
                interpolation='nearest', aspect='auto')

            if mask is not None:
                mask_overlay = np.zeros((*mask.shape, 4))
                mask_overlay[mask > 0] = [1, 1, 1, 0.7]
                ax3.imshow(mask_overlay, origin='lower',
                           extent=plot_extent, interpolation='nearest')
        else:
            ax3.text(0.5, 0.5, 'No Residual',
                     ha='center', va='center', transform=ax3.transAxes)

        ax3.set_title(
            f"Residual/$\\sigma$\n"
            f"Normalized by bg $\\sigma$ of original image\n"
            f"Range: $\\pm$10$\\sigma$, white=masked",
            fontsize=10, pad=10)
        ax3.set_xlabel('X (pixels)', fontsize=12)
        ax3.tick_params(labelleft=False)

        if im3 is not None:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=9)
            cbar.set_label('Residual (σ)', fontsize=9)

        # ---- Row 1, Col 2: 1D SB Profile ----
        gs_sb = GridSpecFromSubplotSpec(
            2, 1, subplot_spec=gs[r1, 2],
            height_ratios=[3, 1], hspace=0.05)
        ax_sb = fig.add_subplot(gs_sb[0])
        ax_sb_resid = fig.add_subplot(gs_sb[1], sharex=ax_sb)
        isolist = render_sb_profile(
            ax_sb, ax_sb_resid, original_data, model_data,
            None, components, region,
            comp_images=comp_imgs, comp_types=comp_types,
            mask=mask,
            zeropoint=image_info.magzp,
            pixscale=image_info.pixscale,
        )

        # ---- Row 0, Col 2: Isophote Ellipses (needs isolist) ----
        from .sb_profile import render_isophote_panel
        ax_iso = fig.add_subplot(gs[r0, 2])
        render_isophote_panel(
            ax_iso, original_data, isolist=isolist,
            mask=mask, norm_params=orig_info)
        ax_iso.set_xlabel('X (pixels)', fontsize=12)
        ax_iso.tick_params(labelleft=False)

        current_row = r1 + 1

        # ---- Separator row (between bands) ----
        if band_idx < n_bands - 1:
            ax_sep = fig.add_subplot(gs[current_row, :])
            ax_sep.set_axis_off()
            ax_sep.axhline(y=0.5, color='gray', linewidth=2)
            current_row += 1

    # --- Save ---
    output_dir = os.path.dirname(band_data[0]['result_fits_file'])
    png_filename = os.path.join(output_dir, "all_bands_comparison.png")
    target_dpi = 1024 / 15
    plt.savefig(png_filename, dpi=target_dpi)
    plt.close(fig)

    return png_filename

def lyric_to_feedme(image_info: ImageInfo, components, feedme_file):
    """
    Convert a .lyric config file to a Galfit .feedme file for a given image.
    """
    with open(feedme_file, 'w') as f:
        f.write(f"# Generated .feedme from \n")
        f.write(f"A) {image_info.image[0]}  # Input data image\n")
        f.write("B) model.fits  # Output model image\n")
        f.write(f"C) {image_info.sigma[0]} # Sigma image\n")
        f.write(f"D) {image_info.psf[0]}  # PSF image (optional)\n")
        f.write(f"E) 1 # PSF fine sampling factor relative to data\n")
        f.write(f"F) {image_info.mask[0]}  # Bad pixel mask (optional)\n")
        f.write("G) none  # Parameter constraints (optional)\n")
        y, x = fits.getdata(image_info.image[0]).shape
        f.write(f"H) 1 {x}  1 {y} # Image region to fit (xmin xmax ymin ymax)\n")
        f.write("I) 100 100  # Size of convolution box (x y)\n")
        f.write(f"J) {image_info.magzp}  # Magnitude zero point\n")
        f.write(f"K) {image_info.pixscale} {image_info.pixscale} # Plate scale (dx dy)\n")
        f.write("O) regular  # Display type\n")
        f.write("P) 3  # Choose: 0=optimize, 1=model, 2=imgblock, 3=subcomps\n\n")

        for idx, component in enumerate(components):
            comp_name = component['name']
            comp_type = component['type']
            f.write(f"# component number: {idx+1}\n")
            f.write(f"0) {comp_type}  # Component type\n")
            if comp_type == "sersic":
                f.write(f"1) {component['x']} {component['y']} 1 1 # Position x, y [pixel]\n")
                f.write(f"3) {component['mag']} 1 # Integrated magnitude\n")
                f.write(f"4) {component['re']}  1 # Effective radius [pixel]\n")
                f.write(f"5) {component['n']}  1 # Sersic index\n")
                f.write(f"6) 0.0000 0 # reserved \n")
                f.write(f"7) 0.0000 0 # reserved \n")
                f.write(f"8) 0.0000 0 # reserved \n")
                f.write(f"9) {component['ba']}  1 # Axis ratio (b/a)\n")
                f.write(f"10) {component['pa']}  1# Position angle (PA) [degrees]\n")
                f.write("Z) 0  # Skip this component in output model? (yes=1, no=0)\n\n")
            elif comp_type == "psf":
                f.write(f"1) {component['x']} {component['y']} 1 1 # Position x, y [pixel]\n")
                f.write(f"3) {component['mag']} 1 # Integrated magnitude\n")
                f.write(f"4) 0.0000 0 # reserved \n")
                f.write(f"5) 0.0000 0 # reserved \n")
                f.write(f"6) 0.0000 0 # reserved \n")
                f.write(f"7) 0.0000 0 # reserved \n")
                f.write(f"8) 0.0000 0 # reserved \n")
                f.write(f"9) 1.0000 -1 # axis ration (b/a) \n")
                f.write(f"10) 0.0000 -1 # position angle (PA) [degrees]\n")
                f.write("Z) 0  # Skip this component in output model? (yes=1, no=0)\n\n")
            elif comp_type == "sky":
                pass
            else:
                pass

def _generate_subcomps(image_info: ImageInfo, components) -> tuple[list, list] | None:
    """Generate individual component images via GALFIT subcomps mode (P=3).

    Returns (comp_images, comp_types) where comp_types are raw GALFIT type
    strings (e.g. "sersic", "expdisk"), or None on failure.
    """
    tmpdir = tempfile.mkdtemp(prefix="galfits_subcomps_")
    try:
        subcomps_feedme = os.path.join(tmpdir, "subcomps.feedme")
        lyric_to_feedme(image_info, components=components, feedme_file=subcomps_feedme)
        galfit_bin = os.getenv("GALFIT_BIN", "galfit")
        subprocess.run(
            [galfit_bin, subcomps_feedme],
            cwd=tmpdir,
            capture_output=True, text=True, timeout=300,
        )
        subcomps_path = os.path.join(tmpdir, "subcomps.fits")
        if not os.path.exists(subcomps_path):
            return None

        comp_images = []
        comp_types = []
        known_components = {"sersic", "expdisk", "edgedisk", "devauc", "king",
                            "nuker", "psf", "gaussian", "moffat", "ferrer", "sky"}
        with fits.open(subcomps_path) as hdul:
            for i in range(1, len(hdul)):
                obj = hdul[i].header.get("OBJECT", f"Component {i-1}")
                if obj.lower() not in known_components:
                    continue
                comp_images.append(hdul[i].data.astype(np.float64))
                comp_types.append(obj.lower())

        return (comp_images, comp_types) if comp_images else None

    except Exception:
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def TEST_lyric_to_feedme():
    lyric_file = "/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/obj_28.lyric"
    gssummary_file = "/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/obj28.gssummary"
    feedme_file = "/tmp/output.feedme"

    image_infos = parse_image_infos_from_lyric(lyric_file)

    for image_info in image_infos:
        components = extract_component_attributes(
            summary_file=gssummary_file,
            config_file=lyric_file,
            fits_file=image_info.image[0],
            band=image_info.band,
        )

        lyric_to_feedme(image_info, components, feedme_file)
        print(f"Generated .feedme file at: {feedme_file}")

def TEST_create_comparison_png():
    lyric_file = "/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/obj_28.lyric"
    gssummary_file = "/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/obj28.gssummary"
    result_fits_file_list = glob.glob("/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/*_result.fits")

    pngs = create_comparison_png(lyric_file, gssummary_file, result_fits_file_list)
    print("Generated comparison PNGs:", pngs)

def TEST_create_comparison_png_v2():
    lyric_file = "/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/obj_28.lyric"
    gssummary_file = "/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/obj28.gssummary"
    result_fits_file_list = glob.glob("/home/jiangbo/obj_list/28/output/20260519_115017_obj_28/*_result.fits")

    pngs = create_comparison_png_v2(lyric_file, gssummary_file, result_fits_file_list)
    print("Generated comparison PNGs:", pngs)

if __name__ == '__main__':
    TEST_create_comparison_png_v2()