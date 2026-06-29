import re
from typing import Annotated, Any, List
import ast
from dataclasses import dataclass
import os
from astropy.io import fits, ascii
from astropy.wcs import WCS
from galfits import gsutils
import numpy as np
import jax
import jax.numpy as jnp

@dataclass
class RegionInfo:
    object: str
    ra: float
    dec: float
    red_shift: float

@dataclass
class ImageInfo:
    image: Any
    band: Any
    sigma: Any
    psf: Any
    psf_sampling: int
    mask: Any
    unit: str
    fitting_area: Any # in arcsec, the "Ix8" parameter in .lyric
    conversion: Any
    magzp: float
    skymodel: str
    skyparameter: List[List[float]]
    shift: float
    shift_param: List[List[float]]
    use_sed: int
    image_label: str # used for label only
    pixscale: float = None
    fitting_region: tuple[int, int, int, int] = None # (xmin, xmax, ymin, ymax) in pixel coordinates


def calculate_fitting_region(x, y, pixscale, ix8, src_x=None, src_y=None):
    """Calculate the fitting region bounds used by GalfitS ``img_cut``.

    Reproduces the cutout logic in ``GalfitS/src/galfits/images.py:img_cut``
    (lines 1454-1477):

    1. Convert *ix8* (arcsec) to pixel radius:  ``cutsize_int = int(ix8 / pixscale)``
    2. Build a symmetric box around the source pixel position.
    3. Clamp to the image boundaries ``[0, x]`` / ``[0, y]``.

    Args:
        x: Image width  (NAXIS1, number of columns).
        y: Image height (NAXIS2, number of rows).
        pixscale: Pixel scale in arcsec/pixel (as returned by
            ``proj_plane_pixel_scales``).
        ix8: Fitting-area half-width in arcsec (the ``Ix8`` value in the
            ``.lyric`` config).
        src_x: Source X pixel position (1-based).  Defaults to image centre.
        src_y: Source Y pixel position (1-based).  Defaults to image centre.

    Returns:
        ``(xmin, xmax, ymin, ymax)`` – the integer pixel bounds of the
        fitting region (0-based, exclusive upper).
    """
    if src_x is None:
        src_x = x / 2.0
    if src_y is None:
        src_y = y / 2.0

    cutsize_int = int(ix8 / pixscale)
    xmin = max(int(src_x) - cutsize_int, 0)
    xmax = min(int(src_x) + cutsize_int, x)
    ymin = max(int(src_y) - cutsize_int, 0)
    ymax = min(int(src_y) + cutsize_int, y)

    return (xmin, xmax, ymin, ymax)


def _resolve_path_pair(value, config_dir):
    """Resolve relative path in a [path, hdu] pair to absolute path."""
    if isinstance(value, list) and len(value) >= 1 and isinstance(value[0], str):
        if not os.path.isabs(value[0]):
            value[0] = os.path.normpath(os.path.join(config_dir, value[0]))
    return value

def parse_region_info_from_lyric(path_or_text: str) -> RegionInfo:
    """Extract object name, RA, Dec, and redshift from a given text or path.

    Args:
        path_or_text: A string that may contain region info or be a path itself.
    Returns:
        A RegionInfo object.
    """
    if os.path.isfile(path_or_text):
        with open(path_or_text, 'r') as f:
            content = f.read()
    else:
        content = path_or_text
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith('#')]

    pattern = re.compile(r'^R(\d+)\)\s*(.+?)\s*$')

    region = {}
    for line in lines:
        line = line.split('#')[0].strip()
        match = pattern.match(line)
        if not match:
            continue
        index, value = match.groups()
        try:
            value = ast.literal_eval(value)
        except:
            pass
        region[int(index)] = value

    object_name = region.get(1)
    if isinstance(object_name, (list, tuple)):
        object_name = object_name[0] if object_name else None

    ra_dec = region.get(2)
    if isinstance(ra_dec, (list, tuple)) and len(ra_dec) >= 2:
        ra = float(ra_dec[0])
        dec = float(ra_dec[1])
    else:
        ra = None
        dec = None

    red_shift = region.get(3)
    if red_shift is not None:
        red_shift = float(red_shift)

    return RegionInfo(
        object=object_name,
        ra=ra,
        dec=dec,
        red_shift=red_shift,
    )

def parse_image_infos_from_lyric(path_or_text: str) -> List[ImageInfo]:
    """Extract FITS file paths from a given text or path.

    Args:
        path_or_text: A string that may contain FITS file paths or be a path itself.
    Returns:
        A list of ImageInfo objects.
    """
    config_dir = None
    if os.path.isfile(path_or_text):
        config_dir = os.path.dirname(os.path.abspath(path_or_text))
        with open(path_or_text, 'r') as f:
            content = f.read()
    else:
        content = path_or_text
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith('#')]

    pattern = re.compile(r'^I([A-Za-z])(\d+)\)\s*(.+?)\s*$')

    config_groups = {}
    for line in lines:
        line = line.split('#')[0].strip()
        match = pattern.match(line)
        if not match:
            continue
        label, index, value = match.groups()
        try:
            if index in ("1", "3", "4", "6"):
                value = value.strip("[]").split(",")
                if len(value) == 1:
                    value.append(0)
                else:
                    value = [value[0].strip(), int(value[1].strip())]
                if config_dir:
                    value = _resolve_path_pair(value, config_dir)
            else:
                value = ast.literal_eval(value)
        except:
            pass
        if label not in config_groups:
            config_groups[label] = {}
        config_groups[label][int(index)] = value

    image_infos = []
    for label in sorted(config_groups.keys()):
        group = config_groups[label]
        values = [group.get(i, None) for i in range(1, 16)]
        values.append(label)
        info = ImageInfo(*values)
        with fits.open(info.image[0]) as hdul:
            header = hdul[0].header
            data_shape = hdul[0].data.shape
            wcs = WCS(header)

        try:
            from astropy.wcs.utils import proj_plane_pixel_scales
            scales = proj_plane_pixel_scales(wcs) * 3600.
            pixsc = float(scales[0])
        except Exception:
            cdelt1 = abs(header.get('CDELT1')) * 3600.
            pixsc = float(cdelt1)
        info.pixscale = pixsc
        y, x = fits.getdata(info.image[0]).shape
        info.fitting_region = calculate_fitting_region(x, y, info.pixscale, ix8=info.fitting_area)

        image_infos.append(info)

    return image_infos    

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


def extract_fits_metadata(fits_file, ra=None, dec=None):
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
        _shape, _pixsc, _x0, _y0, _delta_ang, _wcs = extract_fits_metadata(
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
        # 注意：Galfit 的 PA 定义为从 +y 逆时针到 +x 的角度，Galfits的PA是相对于正北方向逆时针旋转到半长轴的角度，而天文中通常定义为从北向东的角度。因此需要转换：
        pa = (pa + _delta_ang + 90) % 360 if pa is not None else None

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

def generate_feedme(image_info: ImageInfo, components, feedme_file):
    """
    Generate a Galfit .feedme file for the given image and components.
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
        xmin, xmax, ymin, ymax = image_info.fitting_region
        f.write(f"H) {xmin} {xmax}  {ymin} {ymax} # Image region to fit (xmin xmax ymin ymax)\n")
        f.write("I) 100 100  # Size of convolution box (x y)\n")
        f.write(f"J) {image_info.magzp}  # Magnitude zero point\n")
        f.write(f"K) {image_info.pixscale} {image_info.pixscale} # Plate scale (dx dy)\n")
        f.write("O) regular  # Display type\n")
        f.write("P) 3  # Choose: 0=optimize, 1=model, 2=imgblock, 3=subcomps\n\n")

        for idx, component in enumerate(components):
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

# def generate_subcomps(image_info: ImageInfo, components) -> tuple[list, list] | None:
#     """Generate individual component images via GALFIT subcomps mode (P=3).

#     Returns (comp_images, comp_types) where comp_types are raw GALFIT type
#     strings (e.g. "sersic", "expdisk"), or None on failure.
#     """
#     tmpdir = tempfile.mkdtemp(prefix="galfits_subcomps_")
#     try:
#         subcomps_feedme = os.path.join(tmpdir, "subcomps.feedme")
#         generate_feedme(image_info, components=components, feedme_file=subcomps_feedme)
#         galfit_bin = os.getenv("GALFIT_BIN", "galfit")
#         subprocess.run(
#             [galfit_bin, subcomps_feedme],
#             cwd=tmpdir,
#             capture_output=True, text=True, timeout=300,
#         )
#         subcomps_path = os.path.join(tmpdir, "subcomps.fits")
#         if not os.path.exists(subcomps_path):
#             return (None, None)

#         comp_images = []
#         comp_types = []
#         known_components = {"sersic", "expdisk", "edgedisk", "devauc", "king",
#                             "nuker", "psf", "gaussian", "moffat", "ferrer", "sky"}
#         with fits.open(subcomps_path) as hdul:
#             for i in range(1, len(hdul)):
#                 obj = hdul[i].header.get("OBJECT", f"Component {i-1}")
#                 if obj.lower() not in known_components:
#                     continue
#                 comp_images.append(hdul[i].data.astype(np.float64))
#                 comp_types.append(obj.lower())

#         return (comp_images, comp_types) if comp_images else (None, None)

#     except Exception:
#         return (None, None)
#     finally:
#         shutil.rmtree(tmpdir, ignore_errors=True)

def generate_subcomps(lyric_file, gssummary_file):
    if not os.path.exists(lyric_file):
        return None
    if not os.path.exists(gssummary_file):
        return None
    workplace = os.path.dirname(os.path.abspath(gssummary_file))

    Myfitter, targ, fs = gsutils.read_config_file(lyric_file, workplace)
    smfile = ascii.read(gssummary_file)
    for loopx in range(len(smfile)):
        name, best_value = smfile['pname'][loopx], smfile['best_value'][loopx]
        if np.isnan(best_value) or name not in Myfitter.lmParameters:
            continue
        Myfitter.lmParameters[name].value = best_value
    Myfitter.loose_fix_pars()
    pardict = Myfitter.pardict
    Myfitter.cal_model_image()

    GSdata = Myfitter.GSdata
    gmodel_list = Myfitter.gmodel_list  # galaxy models in imagefitter_phot
    # Collect (gmodel, key) pairs across all galaxy models
    comp_keys = [(gm, k) for gm in gmodel_list for k in gm.subCs.keys()]
    print('total components per band:', len(comp_keys),
          '->', [(gm.name + ':' + k) for gm, k in comp_keys])

    all_results = {}

    for loop_group, group_image_indices in enumerate(GSdata.imageset):
        # mass_map state: regenerate at GROUP grid (matches imagefitter_phot pipeline)
        ny, nx = GSdata.imagesizes[loop_group]
        group_transpar = GSdata.coordinates_transfer_paras[loop_group]
        for gm in gmodel_list:
            gm.generate_mass_map((ny, nx), transpar=group_transpar)

        for im_idx in group_image_indices:
            im = GSdata.get_image(im_idx)
            band = im.band
            sky = float(pardict['sky_{0}'.format(band)])
            cut_image_sub = np.asarray(im.cut_image, dtype=float) - sky
            image_model_sub = np.asarray(im.model_image, dtype=float) - sky

            # Per-band scale_and_translate params (mirror imagefitter_phot.cal_model_image:3602-3609)
            nyl, nxl = im.cut_image.shape
            scale0 = group_transpar['pixsc'] / im.coordinates_transfer_para['pixsc']
            scale = jnp.array([scale0, scale0], dtype=jnp.float32)
            shiftx = (im.coordinates_transfer_para['x0shift']
                      + im.coordinates_transfer_para['x0']
                      - (group_transpar['x0shift'] + group_transpar['x0']) * scale0
                      + 0.5)
            shifty = (im.coordinates_transfer_para['y0shift']
                      + im.coordinates_transfer_para['y0']
                      - (group_transpar['y0shift'] + group_transpar['y0']) * scale0
                      + 0.5)
            trans = jnp.array([shifty, shiftx], dtype=jnp.float32)

            comp_images, comp_names = [], []
            for gm, key in comp_keys:
                logN = pardict['logNorm_{0}_{1}'.format(key, band)]
                imm0 = (10.0 ** logN) * gm.mass_map[key]                # group-grid component
                imm = jax.image.scale_and_translate(imm0, (nyl, nxl), (0, 1), scale, trans, 'cubic')
                imm = imm / scale0 ** 2                                  # flux conservation
                imm = jax.scipy.signal.fftconvolve(imm, im.PSF, mode='same')
                imm = imm * im.phys_to_counts_rate
                arr = np.asarray(imm, dtype=float)
                assert arr.shape == cut_image_sub.shape, (
                    'shape mismatch band {} comp {}: {} vs {}'.format(
                        band, key, arr.shape, cut_image_sub.shape))
                comp_images.append(arr)
                comp_names.append('{}_{}'.format(gm.name, key))

            all_results[band] = dict(
                data=cut_image_sub, model=image_model_sub,
                comp_images=comp_images, comp_names=comp_names,
            )

    return all_results

def TEST_parse_image_infos_from_lyric_success():
    path = "/home/jiangbo/GALFITS_examples/40/obj40.lyric"
    image_infos = parse_image_infos_from_lyric(path)
    for image_info in image_infos:
        print(image_info)

def TEST_parse_image_infos_from_lyric_failure():
    path = "/home/jiangbo/GALFITS_examples/40/obj40.lyr"
    image_infos = parse_image_infos_from_lyric(path)
    for image_info in image_infos:
        print(image_info)
    
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

        generate_feedme(image_info, components, feedme_file)
        print(f"Generated .feedme file at: {feedme_file}")


if __name__ == '__main__':
    TEST_parse_image_infos_from_lyric_success()