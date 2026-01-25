from astropy.io import fits
import base64
import os
from typing import Any, Annotated
from io import BytesIO
from PIL import Image
import numpy as np

def get_image_from_fits(
    fits_file: Annotated[str, "the path of the original fits file"],
    target: Annotated[str, "original|model|residual"]
):    
    """get the image (encoded as base64) from the fits file"""
    
    try:
        if not os.path.exists(fits_file):
            return None
        
        with fits.open(fits_file) as hdul:
            image_hdu = None
            target = target if target in ["model", "residual"] else "["
            for hdu in hdul:
                object_type = hdu.header.get("OBJECT")
                if object_type and object_type.find(target) != -1:
                    image_hdu = hdu
                    break
            
            if image_hdu is None:
                return None
            
            # normalize the image data
            image_data = image_hdu.data
            mean_val = np.mean(image_data)
            std_val = np.std(image_data)
            clipped_data = np.clip(image_data, mean_val - 3*std_val, mean_val + 3*std_val)
            min_val = np.min(clipped_data)
            max_val = np.max(clipped_data)
            if max_val == min_val:  
                normalized_data = np.zeros_like(clipped_data, dtype=np.uint8)
            else:
                normalized_data = ((clipped_data - min_val) / (max_val - min_val) * 255).astype(np.uint8)
            flipud_data = np.flipud(normalized_data)

            img = Image.fromarray(flipud_data, mode='L')

            # Save PNG file for human viewing (in same directory as FITS file)
            fits_dir = os.path.dirname(fits_file)
            base_name = os.path.splitext(os.path.basename(fits_file))[0]
            target_label = target if target in ["model", "residual"] else "original"
            png_filename = os.path.join(fits_dir, f"{base_name}_{target_label}.png")
            img.save(png_filename)

            return png_filename
    
    except: 
        return None
