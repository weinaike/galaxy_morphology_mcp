import os
os.environ['XLA_PYTHON_CLIENT_PREALLOCATE']='false'

from typing import Annotated, Optional, List
import glob
import numpy as np
from galfits import gsutils
from astropy.io import ascii
from astropy.io import fits
from astropy.wcs import WCS
from astropy.cosmology import Planck18 as cosmo
from pathlib import Path
import re
import subprocess

__all__ = ["ImageFitting", "PureSEDFitting", "ImageSEDFitting"]

ALL_BANDS = [
    "nircam_f115w", "nircam_f150w", "nircam_f200w",
    "nircam_f277w", "nircam_f356w", "nircam_f410m", 
    "nircam_f444w"
]
MAG_ZERO_POINTS = [
    28.96697568756239, 28.96697568756239, 28.96697568756239, 
    27.461825709242483, 27.461825709242483, 27.461825709242483, 
    27.461825709242483
]
BANDS_ZEROPOINTS = {band: zp for band, zp in zip (ALL_BANDS, MAG_ZERO_POINTS)}

z_fit =  3.86500

def load_gs_model(config_lyric, workplace, prior_path = None,): 
    
    '''
    load galfits model from gssummary.
    '''

    Myfitter, targ, fs = gsutils.read_config_file(config = config_lyric
            , workplace=workplace, priorpath=prior_path, )

    summary_file = workplace + "/{0}.gssummary".format(targ)
    if os.path.isfile(summary_file):
        smfile = ascii.read(summary_file)

        ## fill value
        for loopx in range(len(smfile)):

            Myfitter.lmParameters[smfile['pname'][loopx]].value = smfile["best_value"][loopx]

            Myfitter.loose_fix_pars()
            Myfitter.cal_model_image()
    
    return Myfitter, targ

def extract_band_fits_pairs(config_lyric):
    pattern1 = re.compile(r'^I([a-z])1\)\s*(.+)', re.IGNORECASE)  # Match Ix1)
    pattern2 = re.compile(r'^I([a-z])2\)\s*(.+)', re.IGNORECASE)  # Match Ix2)

    band_fits_pairs = {}  
    temp = {}  

    with open(config_lyric, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            match1 = pattern1.match(line)
            if match1:
                img_label = match1.group(1)  # a, b, c...
                fits_file = match1.group(2).strip()
                temp[img_label] = {'1': fits_file}
                continue

            match2 = pattern2.match(line)
            if match2:
                img_label = match2.group(1)
                band = match2.group(2).strip()
                if img_label in temp:
                    temp[img_label]['2'] = band
                    band_fits_pairs[band] = (img_label, temp[img_label]['1'])

    return band_fits_pairs

def calculate_profile_fluxes(config_lyric, workplace, prior_path=None):
    '''
    Estimate fluxes of each component in each band using the best-fit model from galfits, which can be used as a reference for pure SED fitting. 

    config_lyric: str
        the path to the galfits config file (.lyric)
    workplace: str
        the path to the galfits workplace, where the gssummary file can be found.
    prior_path: str or None
        optional, the path to the prior file used in galfits fitting, which will be passed to load_gs_model function. 
    '''

    fluxes = {} # path: model_name/profile_name/band -> (flux, flux_error)
    Myfitter, _ = load_gs_model(config_lyric=config_lyric, workplace=workplace, prior_path=prior_path)
    bands = Myfitter.GSdata.allbands ## some sources lack some bands' images

    for model in Myfitter.model_list:
        if not hasattr(model, "subnames"):
            # skip AGNs and Stars
            continue
        fluxes[model.name] = {}
        for profile_name in model.subnames: # for each profile/component in current galaxy
            fluxes[model.name][profile_name] = {}
            for band in bands:
                img = Myfitter.GSdata.get_image(band)

                logNorm = Myfitter.pardict[f"logNorm_{profile_name}_{band}"]
                logMass = Myfitter.pardict[f"logM_{profile_name}"]
                mag = img.magzp - 2.5*(logNorm + logMass)
                flux_mJy = 3631* 10**(-0.4*mag) * 1e3 ## mmJy = 1e-3 Jy

                fluxes[model.name][profile_name][band] = (flux_mJy, flux_mJy * 0.1)

    return fluxes

def generate_pure_sed_fitting_lyric(*, profile_name, mock_profile_root, bands, band_fits_pairs, ebv=0.1):
    original_fits = band_fits_pairs[bands[0]][1].strip("[]").split(",")[0].strip() # can any of these bands be used ?
    header = fits.getheader(original_fits) 
    shape = fits.getdata(original_fits).shape 
    ra,dec = WCS(header).all_pix2world((shape[0]+1)/2, (shape[1]+1)/2, 1)

    with open(os.path.join(mock_profile_root, "pure_sed.lyric"), "w") as filp:
        filp.write("# This is a galfitS configuration file for galaxy "+str(profile_name)+"\n")
        filp.write("# The config file provide a galfitS setup to perform a single sersic SED fitting with multi-band images.\n")

        # Region information
        filp.write("# Region information\n")
        filp.write('R1) '+str(profile_name)+'\n')  # name of the target
        filp.write('R2) ['+str(ra)+','+str(dec)+']\n')  # sky coordinate of the target [RA, Dec]
        filp.write('R3) '+str(z_fit)+' \n\n') # redshift of the target
        imgatlas = []

        for band in bands:    
            imagel = band_fits_pairs[band][0]

            imgatlas.append(imagel)
            mockfile = os.path.join(mock_profile_root, f'{band}.fits') # mock path 

            filp.write('# Image '+imagel.upper()+' \n')
            filp.write('I'+imagel+'1)  [' + mockfile + ',0] \n') #sci image
            filp.write('I'+imagel+f'2)  {band}\n') # band name
            filp.write('I'+imagel+'3)  [' + mockfile + ',2] \n') # sigma image
            filp.write('I'+imagel+'4)  [' + mockfile + ',3]\n') #psf image
            filp.write('I'+imagel+'5)  1\n') # PSF fine sampling factor relative to data
            filp.write('I'+imagel+'6)  [Noimg,0]\n') #mask image
            filp.write('I'+imagel+'7)  cR\n') # unit of the image
            filp.write('I'+imagel+'8)  -1 \n') # size to make cutout image region for fitting, unit arcsec
            filp.write('I'+imagel+'9)  1 \n') # Conversion from image unit to flambda, -1 for default                 ## why ? for pure sed fitting, it must be specified as 1.
            filp.write('I'+imagel+f'10) {BANDS_ZEROPOINTS[band]}\n') # Magnitude photometric zeropoint                                 ## mag zp calculate
            filp.write('I'+imagel+'11) uniform\n') # sky model
            filp.write('I'+imagel+'12) [[0,-0.5,0.5,0.1,0]]\n') # sky parameter, (value, min, max, step)
            filp.write('I'+imagel+'13) 0\n') # allow relative shifting
            filp.write('I'+imagel+'14) [[0,-5,5,0.1,0],[0,-5,5,0.1,0]]\n') # [shiftx, shifty]
            filp.write('I'+imagel+'15) 1\n\n') # Use SED information

        age= round(cosmo.age(z_fit).value,2)-0.2 
        age_list = [0] + list(np.logspace(-1, np.log10(age), 5))


        filp.write("# Image atlas\n")
        filp.write("Aa1) 'all'\n") # name of the image atlas
        filp.write("Aa2) "+str(imgatlas)+"\n") # images in this atlas
        filp.write('Aa3) 0\n') # whether the images have same pixel size
        filp.write('Aa4) 0\n') # link relative shiftings
        filp.write('Aa5) []\n') # spectra
        filp.write('Aa6) []\n') # aperture size
        filp.write('Aa7) []\n\n') # references images
        
        filp.write("# Profile A\n")
        filp.write(f'Pa1) {profile_name}\n') # name of the component
        filp.write('Pa2) sersic\n') # profile type
        filp.write('Pa3) [0,-0.3,0.3,0.1,0]\n') # x-center [arcsec]
        filp.write('Pa4) [0,-0.3,0.3,0.1,0]\n') # y-center [arcsec]
        filp.write('Pa5) [0.2,0.1,1.7,0.1,0]\n') # effective radius [arcsec]
        filp.write('Pa6) [2,0.5,6,0.1,0]\n') # Sersic index
        filp.write('Pa7) [0,-90,90,1,0]\n') # position angle (PA) [degrees: Up=0, Left=90]
        filp.write('Pa8) [0.8,0.5,1,0.01,0]\n') # axis ratio (b/a) [0.1=round, 1=flat]
        filp.write(f'Pa9) [[-2,-8,0,0.1,1],[-2,-8,0,0.1,1],[-2,-8,0,0.1,1],[-2,-8,0,0.1,1],[-2,-8,0,0.1,1]]\n') # contemporary log star formation fraction         ## sfr
        filp.write(f'Pa10) [{round(age_list[0],2)}, {round(age_list[1],2)}, {round(age_list[2], 2)}, {round(age_list[3],2)}, {round(age_list[4],2)}, {round(age_list[5],2)}]\n') # burst stellar age [Gyr]          ## age 
        filp.write('Pa11) [[0.001,0.001,0.04,0.001,1]]\n') # metallicity [Z=0.02=Solar]
        filp.write('Pa12) [[0.7,0,5.1,0.1,1]]\n') # Av dust extinction [mag]
        filp.write('Pa13) [100,40,200,1,0]\n') # stellar velocity dispersion
        filp.write('Pa14) [9,6,12,0.1,1]\n') # log stellar mass
        filp.write('Pa15) bins \n') # star formation history type: burst/conti                    ## change to bins 
        filp.write('Pa16) [-2,-4,-2,0.1,0]\n') # logU nebular ionization parameter
        filp.write('Pa26) [3,0,5,0.1,1]\n') # amplitude of the 2175A bump on extinction curve
        filp.write('Pa27) 0\n') # SED model, 0: full; 1: stellar only; 2: nebular only; 3: dust only
        filp.write('Pa28) [8.14,4.5,10,0.1,0]\n') # log dust mass
        filp.write('Pa29) [1.0, 0.1, 50, 0.1, 0]\n') # Umin, minimum radiation field
        filp.write('Pa30) [1.0, 0.47, 7.32, 0.1, 0]\n') # qPAH, mass fraction of PAH
        filp.write('Pa31) [1.0, 1.0, 3.0, 0.1, 0]\n') # alpha, powerlaw slope of U
        filp.write('Pa32) [0.1, 0, 1.0, 0.1, 0]\n\n') # gamma, fraction illuminated by star forming region

        # Galaixes
        filp.write("# Galaxy A\n")
        filp.write('Ga1) mygal\n') # name of the galaxy
        filp.write("Ga2) ['a']\n") # profile component
        filp.write('Ga3) ['+str(z_fit)+',0.01,12.0,0.01,0]\n') # galaxy redshift
        filp.write(f'Ga4) {ebv}\n') # the EB-V of Galactic dust reddening 
        filp.write('Ga5) [1.0,0.5,2,0.05,0]\n') # normalization of spectrum when images+spec fitting
        filp.write('Ga6) []\n') # narrow lines in nebular
        filp.write('Ga7) 1\n\n') # number of components for narrow lines

def generate_mock_files_for_pure_sed(
    fluxes, mock_root, z_fit, band_fits_pairs
):
    os.makedirs(mock_root, exist_ok=True)
    for galaxy_name in fluxes.keys():
        os.makedirs(os.path.join(mock_root, galaxy_name), exist_ok=True)
        for profile_name in fluxes[galaxy_name].keys():
            mock_profile_root = os.path.join(mock_root, galaxy_name, profile_name)
            os.makedirs(mock_profile_root, exist_ok=True)

            # creates a fits file for each band 
            for band, (flux, flux_err) in fluxes[galaxy_name][profile_name].items():
                output_fits = os.path.join(mock_profile_root, band + ".fits")
                gsutils.photometry_to_img(flux, flux_err, z_fit, output_fits, band, unit='mJy')

            # creates a pure_sed.lyric file for current profile/component    
            generate_pure_sed_fitting_lyric(
                profile_name=profile_name, 
                mock_profile_root=mock_profile_root, 
                bands=list(fluxes[galaxy_name][profile_name].keys()), 
                band_fits_pairs=band_fits_pairs
            )
        
def guess_mass(
    config_lyric: Annotated[str, "Path to the galfits config file (.lyric)"], 
    workplace: Annotated[str, "Path to the galfits workplace where gssummary can be found"],
    mock_root: Annotated[str, "Path to the mock root directory. It uses the default directory structure if None"] = None
) -> Annotated[dict, "A dict containing the status and content of the mass estimation result. The status can be 'success' or 'error', and the content provides detailed information about the result or error message."]:
    """
    Estimate the fluxes of each component in each band using the model from galfits, and then generate mock files for pure SED fitting. The generated mock files include a pure_sed.lyric file for each profile/component, and a fits file for each band. The pure_sed.lyric file provides the galfitS setup for pure SED fitting, and the fits files provide the input images for fitting. The generated mock files can be used to perform pure SED fitting for each component in each galaxy, and the fitting results can be synced back to the original lyric file using the update_lyric_with_gssummary function.
    """

    try:
        # Each item in band_fits_pairs follows the pattern: 
        #     band_name: (<image_label such as 'a', 'b', ...>, <the image fits file>)
        band_fits_pairs = extract_band_fits_pairs(config_lyric)

        fluxes = calculate_profile_fluxes(config_lyric=config_lyric, workplace=workplace)

        if mock_root is None:
            mock_root = Path(os.path.dirname(lyric_file)) / f"{Path(lyric_file).stem}_mock"
            mock_root = str(mock_root)
        mock_root = mock_root + "/" if not mock_root.endswith("/") else mock_root

        generate_mock_files_for_pure_sed(fluxes=fluxes, mock_root=mock_root, z_fit=z_fit, band_fits_pairs=band_fits_pairs)

        return {
            "status": "success",
            "message": f"Successfully generated mock files for pure SED fitting at {mock_root}. You can run run_pure_sed_fitting function to perform the fitting, and then use update_lyric_with_gssummary function to sync the fitting results back to the original lyric file."
        }
    except ValueError as e:
        return {
            "status": "error",
            "message": f"Failed to generate mock files for pure SED fitting: {str(e)}"
        }

def do_pure_sed_fitting(
    mock_root: Annotated[str, "Path to the mock root directory."], 
    args: Annotated[Optional[str|List[str]], "Additional command line arguments for galfitS fitting. It can be a single string or a list of strings."] = None
) -> Annotated[List[dict], "Each dict contains the status and content of the fitting result for each profile in each galaxy. The status can be 'success' or 'failed', and the content provides detailed information about the fitting result or error message."]:
    results = [] 
    args = args or []
    if isinstance(args, str):
        args = [args]
    mock_root = Path(mock_root) if not isinstance(mock_root, Path) else mock_root
    for galaxy_name in [d.name for d in mock_root.iterdir() if d.is_dir()]:
        mock_galaxy_root = mock_root / galaxy_name
        for profile_name in [d.name for d in mock_galaxy_root.iterdir() if d.is_dir()]:
            lyric_file = os.path.join(mock_root, galaxy_name, profile_name, "pure_sed.lyric")
            cmd_working_dir = os.path.join(mock_root, galaxy_name, profile_name)
            workplace = os.path.join(cmd_working_dir, "result")

            r = ImageFitting(lyric_file, workplace, args)
            results.append(r)

    return results            

def ImageFitting(
    path: Annotated[str, "Path to the galfits config file (.lyric)"], 
    workplace: Annotated[str, "Path to the galfits workplace where gssummary can be found"],
    args: Annotated[Optional[str|List[str]], "Additional command line arguments for galfits fitting. It can be a single string or a list of strings."] = None
) -> Annotated[dict, "A dict containing the status and content of the galfits fitting result. The status can be 'success' or 'error', and the content provides detailed information about the result or error message."]:
    args = args or []
    if isinstance(args, str):
        args = [args]
    command = ["python", "-m", "galfits.galfitS", "--config", f'{path}', '--workplace', f'{workplace}'] + args
    try:
        cpi = subprocess.run(
            command,
            cwd=os.path.dirname(path),
            capture_output=True,
            text=True,
            check=True,
            timeout=600,  # 10 minute timeout
        )
        return {
            "status": "success",
            "message": f"run galfits successfully for {path}"
        }
    except subprocess.CalledProcessError as e:
        return {
            "status": "error",
            "message": f"run galfits failed for {path}: {e.stderr}"
        }
    except TimeoutError as e:
        return {
            "status": "error",
            "message": f"run galfits timedout: {e}"
        }    
    except Exception as e:
        return {
            "status": "error",
            "message": f"run galfit failed: {str(e)}"
        }    

ImageSEDFitting = ImageFitting

# Define required parameters in a centralized set for easy maintenance
REQUIRED_PARAMETERS = {
    "{profile_name}_f_cont_bin1",
    "{profile_name}_f_cont_bin2",
    "{profile_name}_f_cont_bin3",
    "{profile_name}_f_cont_bin4",
    "{profile_name}_f_cont_bin5",
    "{profile_name}_Av_value",
    "logM_{profile_name}",
    "{profile_name}_Z_value",
}

# Precompile regex patterns for better performance (compile once)
PATTERNS = {
    "Px9": \
        r"(P{label}9\)\s*\[\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\]\])",
    "Px11": r"(P{label}11\)\s*\[\[)(-?\d+\.?\d*)(,.*?\]\])",
    "Px12": r"(P{label}12\)\s*\[\[)(-?\d+\.?\d*)(,.*?\]\])",
    "Px14": r"(P{label}14\)\s*\[)(-?\d+\.?\d*)(.*\])",
}


def parse_gssummary(gssummary_file: str, profile_name: str) -> dict:
    """Parse gssummary file or raw text content and extract required parameters.

    Args:
        gssummary_file: Path to the .gssummary file OR raw text content.

    Returns:
        Dictionary containing parsed parameter key-value pairs.

    Raises:
        ValueError: If required parameters are missing or values are invalid numbers.
    """
    results = {}

    # Read content from file or use input directly as text
    if os.path.isfile(gssummary_file):
        with open(gssummary_file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = gssummary_file

    for line in text.splitlines():
        line = line.strip()
        # Skip empty lines and comment lines
        if not line or line.startswith("#"):
            continue

        # Safe split to avoid crashes from malformed lines
        parts = line.split(maxsplit=2)
        if len(parts) < 2:
            continue

        pname, best_value = parts[0], parts[1]
        required_parameters = set([p.format(profile_name=profile_name) for p in REQUIRED_PARAMETERS]) 
        if pname in required_parameters:
            try:
                results[pname] = float(best_value)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid numeric value for parameter {pname}: {best_value}")

    # Check for missing required parameters
    missing_params = required_parameters - results.keys()
    if missing_params:
        raise ValueError(f"Missing required parameters in gssummary: {sorted(missing_params)}")

    return results

def search_profile_label_by_name(text: str, profile_name: str):
    pattern = re.compile(
        rf'^P([a-z])1\)\s*{re.escape(profile_name)}\b',
        re.MULTILINE
    )
    label_match = pattern.search(text)
    if not label_match:
        raise ValueError(f"profile: {profile_name} not found!")
    
    label = label_match.group(1)  
    return label

def replace_Px9(label: str, text: str, values: list) -> str:
    """Replace 5 values in the Px9 pattern block.

    Args:
        text: Original lyric file content.
        values: List of 5 float values to replace in order.

    Returns:
        Modified text with updated Px9 values.
    """
    v1, v2, v3, v4, v5 = values
    replacement = (
        r"\g<1>"
        f"{v1},\g<3>,\g<4>,\g<5>,\g<6>"
        r"\g<7>"
        f"{v2},\g<9>,\g<10>,\g<11>,\g<12>"
        r"\g<13>"
        f"{v3},\g<15>,\g<16>,\g<17>,\g<18>"
        r"\g<19>"
        f"{v4},\g<21>,\g<22>,\g<23>,\g<24>"
        r"\g<25>"
        f"{v5},\g<27>,\g<28>,\g<29>,\g<30>"
        r"\g<31>"
    )
    return re.compile(PATTERNS["Px9"].format(label=label)).sub(replacement, text)


def replace_single_value(pattern_key: str, label: str, text: str, value: float) -> str:
    """Unified function to replace single numeric values (eliminates code duplication).

    Handles Px11, Px12, Px14 patterns.
    """
    return re.compile(PATTERNS[pattern_key].format(label=label)).sub(rf"\g<1>{value}\g<3>", text)


def update_lyric_with_gssummaries(
    lyric_file: Annotated[str, "Path to the lyric configuration file"],
    mock_root: Annotated[str, "Path to the mock root directory. It will search the default directory structure if None"] = None,
    new_lyric_file: Annotated[str, "Path to save the updated lyric file. If None, it will overwrite the original lyric file."] = None
) -> dict:
    """Assign fitted values from gssummary into the lyric configuration file.

    The default directory structure is as follows:

    |
    +---- <your_config>.lyric
    |
    +---- <your_config>_mock/
                |
                +---- <galaxy1>/
                |         |
                |         +---- <component1>/
                |         |          |
                |         |          +---- <XXXX>.fits
                |         |          |
                |         |          +---- pure_sed.lyric
                |         |          |
                |         |          +---- result/
                |         |                  |
                |         |                  +---- <component1>.constrain
                |         |                  |
                |         |                  +---- <component1>.gssummary
                |         |                  |
                |         |                  +---- <component1>.params
                |         |                  |
                |         |                  +---- <component1>SED_model.png
                |         |
                |         +---- <next component...>/
                |         |
                |
                |
                +---- <next galaxy>/
                |

    Args:
        lyric_file: Path to the target lyric file to be updated.
        mock_root: Path to the mock root directory.

    Returns:
        Dictionary with status and message:
        - status: "success" or "error"
        - content: Detailed result or error message
    """
    try:
        with open(lyric_file, encoding="utf-8") as f:
            lyric_content = f.read()

        if mock_root is None:
            mock_root = Path(os.path.dirname(lyric_file)) / f"{Path(lyric_file).stem}_mock"
            mock_root = str(mock_root)
        mock_root = mock_root + "/" if not mock_root.endswith("/") else mock_root

        gssummary_files = glob.glob(f"{mock_root}**/*.gssummary", recursive=True)    
        for gssummary_file in gssummary_files:
            profile_name = Path(os.path.basename(gssummary_file)).stem
            label = search_profile_label_by_name(lyric_content, profile_name)
            summary_data = parse_gssummary(gssummary_file, profile_name)

            # Apply all value replacements
            bin_values = [summary_data[f"{profile_name}_f_cont_bin{i}"] for i in range(1, 6)]
            lyric_content = replace_Px9(label, lyric_content, bin_values)
            lyric_content = replace_single_value("Px12", label, lyric_content, summary_data[f"{profile_name}_Av_value"])
            lyric_content = replace_single_value("Px14", label, lyric_content, summary_data[f"logM_{profile_name}"])
            lyric_content = replace_single_value("Px11", label, lyric_content, summary_data[f"{profile_name}_Z_value"])

        # Write updated content back to file
        new_lyric_file = new_lyric_file if new_lyric_file else lyric_file
        with open(new_lyric_file, "w", encoding="utf-8") as f:
            f.write(lyric_content)

        return {
            "status": "success",
            "message": f"Successfully assigned gssummary values to {new_lyric_file}."
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to assign gssummary values: {str(e)}"
        }

def PureSEDFitting(lyric_file, workplace, new_lyric_file, mock_root=None, args=[]):
    if mock_root is None:            
        mock_root = Path(os.path.dirname(lyric_file)) / f"{Path(lyric_file).stem}_mock"
        mock_root = str(mock_root)
    result = guess_mass(config_lyric=lyric_file, workplace=workplace, mock_root=mock_root)
    if result["status"] != "success":
        return result

    results = do_pure_sed_fitting(mock_root=mock_root, args=args)
    failed_results = [result for result in results if result["status"] != "success"]
    if len(failed_results) != 0:
        return {"status": "failed", "message": "\n".join([r["message"] for r in failed_results])}

    result = update_lyric_with_gssummaries(lyric_file, mock_root, new_lyric_file)
    if result["status"] != "success":
        return result
    
    return {"status": "success", "message": "pure sed fitting success"}

if __name__ == '__main__':
    lyric_file = "/home/jiangbo/galaxy_morphology_mcp/GALFITS_examples/latest/configs/obj692"
    new_lyric_file = "/tmp/updated.lyric"
    workplace = "/home/jiangbo/galaxy_morphology_mcp/GALFITS_examples/latest/results/obj692"
    args = ["--fit_method", "ES"]

    result = ImageFitting(path=lyric_file, workplace=workplace, args=args)
    print(result)

    result = PureSEDFitting(lyric_file=lyric_file, workplace=workplace, new_lyric_file=new_lyric_file, mock_root=None, args=args)
    print(result)

    result = ImageSEDFitting(path=new_lyric_file, workplace=workplace + "_2", args=args)
    print(result)

