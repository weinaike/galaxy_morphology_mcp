from astropy.io import fits
from astropy.wcs import WCS
from astropy.coordinates import Angle
import warnings
import sys
import os
from contextlib import contextmanager
from typing import Annotated, Tuple, Union


# 定义跨平台的上下文管理器：临时屏蔽stdout/stderr输出
@contextmanager
def suppress_stdout_stderr():
    """
    临时重定向stdout和stderr到空设备，屏蔽所有输出
    兼容 Windows/Linux/macOS 所有系统
    """
    devnull_path = os.devnull  # Windows="nul", Linux/macOS="/dev/null"
    with open(devnull_path, 'w') as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = devnull
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


def pix2radec(
    pix_x: Annotated[Union[int, float], "X coordinate of the pixel in astronomical image (integer pixel position)"],
    pix_y: Annotated[Union[int, float], "Y coordinate of the pixel in astronomical image (integer pixel position)"],
    fits_file: Annotated[str, "File path of the FITS image file (e.g., '/data/obs.fits')"],
    pixel_based: Annotated[int, "Pixel coordinate system type: 1 for 1-based (astronomical standard), 0 for 0-based (programming standard)"] = 1
) -> Annotated[Tuple[str, str], "Tuple of (RA, DEC) in human-readable format: RA as hh:mm:ss, DEC as ±dd:mm:ss"]:
    """
    Convert pixel coordinates (x, y) in a FITS astronomical image to RA-DEC celestial coordinates.
    
    This function parses the WCS (World Coordinate System) information from the FITS header,
    handles pixel coordinate system conversion (1-based/0-based), and returns RA-DEC in a readable format.
    All warning/output from astropy WCS initialization is suppressed to avoid interfering with stdio communication.
    
    Args:
        pix_x: X pixel position in the image (integer)
        pix_y: Y pixel position in the image (integer)
        fits_file: Full path to the FITS file containing WCS metadata
        pixel_based: Pixel indexing type (1=1-based, 0=0-based), default is 1 (astronomical standard)
    
    Returns:
        Tuple[str, str]: 
            - First element: Right Ascension (RA) in "hh:mm:ss" format (e.g., "14:20:00")
            - Second element: Declination (DEC) in "±dd:mm:ss" format (e.g., "+53:00:00")
    
    Raises:
        RuntimeError: If WCS parsing or coordinate conversion fails (e.g., invalid FITS file, wrong pixel coordinates)
    """
    # 禁用astropy的WCS相关警告
    warnings.filterwarnings('ignore', category=UserWarning)
    warnings.filterwarnings('ignore', category=RuntimeWarning)
    warnings.filterwarnings('ignore', module='astropy.wcs')

    try:
        # 屏蔽stdout/stderr，杜绝额外输出
        with suppress_stdout_stderr():
            with fits.open(fits_file) as hdul:
                header = hdul[0].header
                wcs = WCS(header)
        
        # 像素坐标转RA-DEC（度为单位）
        radec_deg = wcs.wcs_pix2world([(pix_x, pix_y)], pixel_based)[0]
        ra_deg, dec_deg = radec_deg.tolist()

        # # 转换为易读的时:分:秒/度:分:秒格式
        # ra_hms = Angle(ra_deg, unit='deg').to_string(unit='hour', sep=':')
        # dec_dms = Angle(dec_deg, unit='deg').to_string(unit='deg', sep=':')

        # return ra_hms, dec_dms
        return ra_deg, dec_deg
    
    except Exception as e:
        raise RuntimeError(f"Failed to convert pixel to RA-DEC: {str(e)}")


# 测试示例
if __name__ == "__main__":
    try:
        ra, dec = pix2radec(30.1, 30.2, "/home/jiangbo/GALFITS_examples/41926/f115w.fits", pixel_based=1)
        print(f"RA: {ra}, DEC: {dec}")
    except RuntimeError as e:
        print(f"Error: {e}")