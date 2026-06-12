import os
import re

import shutil
from typing import Annotated

from tools.analyze_image import create_vlm_client


TUPLE_SPECIFICATION = """
A 5-tuple parameter specification is defined as [init_value, min_value, max_value, step_size, fitting_flag], where:
init_value: The initial or baseline value of an attribute.
min_value: The lower bound of the attribute range.
max_value: The upper bound of the attribute range.
step_size: The incremental step size used during the fitting process.
fitting_flag: A boolean indicator (1 for fitting/tunable, 0 for fixed). 
"""

LYRIC_TEMPLATE = """
A .lyric configuration file follows the patterns:

# Region information
R1) J0056-0021                  # Target name
R2) [14.13899,-0.36266]         # RA and Dec in degrees
R3) 0.0628                      # Redshift

# One or more image configuration(s): Must start with I (e.g., Ia, Ib, Ic, ...) 
Ia1) [./f115w.fits,0]                 # input image (hdu=0)
Ia2) nircam_f115w                     # band name
Ia3) [./f115w_err.fits,0,1.8]         # sigma image (hdu=0, gain=1.8)
Ia4) [./PSF/F115W_hybrid.fits,0]      # psf image (hdu=0)
Ia5) 1                                # psf sampling factor
Ia6) [./f115w_mask.fits,0]            # mask image (hdu=0)
Ia7) MJy/sr                           # image unit ()
Ia8) 1.2                              # image size in arcsec
Ia9) 4.692087135707939e+20            # conversion
Ia10) 28.96697568756239               # Magnitude zeropoint
Ia11) uniform                         # sky model
Ia12) [[0,-0.5,0.5,0.1,1]]            # sky parameters (list of 5-tuple)
Ia13) 1                               # Allow relative shift (0=no, 1=yes)
Ia14) [[0,-5,5,0.1,1],[0,-5,5,0.1,1]] # shiftx and shifty, each a 5-tuple 
Ia15) 0                               # Use SED information (0=no, 1=yes)

Ib1) [./f277w.fits,0]                 # input image (hdu=0)
Ib2) nircam_f277w                     # band name
Ib3) [./f277w_err.fits,0,1.8]         # sigma image (hdu=0, gain=1.8)
Ib4) [./PSF/F277W_hybrid.fits,0]      # psf image (hdu=0)
Ib5) 1                                # psf sampling factor
Ib6) [./f277w_mask.fits,0]            # mask image (hdu=0)
Ib7) MJy/sr                           # image unit ()
Ib8) 1.2                              # image size in arcsec
Ib9) 6.805655081960954e+20            # ?
Ib10) 27.461825709242483              # Magnitude zeropoint
Ib11) uniform                         # sky model
Ib12) [[0,-0.5,0.5,0.1,1]]            # sky parameters (list of 5-tuple)
Ib13) 1                               # Allow relative shift (0=no, 1=yes)
Ib14) [[0,-5,5,0.1,1],[0,-5,5,0.1,1]] # shiftx and shifty, each a 5-tuple
Ib15) 0                               # Use SED information (0=no, 1=yes)

# One or more image atlas configuration(s): Must start with A (e.g., Aa, Ab, Ac, ...). 
# Atlas groups multiple image configurations (I-prefixed) for joint fitting/analysis.

Aa1) "jwst"                           # Unique name identifier for this atlas
Aa2) ['a', 'b']                       # List of image configuration suffixes (must match I-prefixed configs: Ia→'a', Ib→'b')
Aa3) 1                                # Use same pixel size across all atlas images (0=no, 1=yes)
Aa4) 0                                # Link relative shifts between atlas images (0=no, 1=yes; syncs alignment)
Aa5) []                               # Spectra (empty)
Aa6) []                               # Aperture sizes
Aa7) []                               # Reference images

# Nuclei A (original)
Na1) AGN
Na2) [3.865, 1,12,1,0]
Na3) 0.00714
Na4) [0, -0.1, 0.1, 0.01, 0]
Na5) [0, -2, 2, 0.1, 1]
Na6) [7, 3, 9, 0.1, 1]
Na7) [-1, -4, 2, 0.1, 1]
Na8) [0.495, 0, 0.99, 0.01, 0]
Na9) [0, 0, 4, 0.25, 1]
Na10) [42, 41, 48, 0.1, 1]
Na11) [1, 0, 4, 0.1, 1]
Na12) []
Na13) []
Na14) 1
Na15) 1
Na16) 0
Na17) 0
Na18) 4
Na19) [1, 0.5, 2, 0.05, 0]
Na20) 0
Na21) [41, 39, 44, 0.1, 0]
Na22) [-0.5,-2.5,-0.25,0.05,0]
Na23) [0.5,0.25,1.5,0.05,0]
Na24) [7,5,10,0.5,0]
Na25) [15,0,90,5,0]
Na26) [1.,0.2,5,0.1,0]
Na27) [36.,35.,42.,0.1,0]

# One or more profile components: Must start with P (e.g., Pa, Pb, Pc, ...)

Pa1) bulge                          # Component name
Pa2) sersic                         # Profile type: sersic, sersic_b, sersic_r, sersic_f, sersic_rf, sersic_bf, ferrer, edgeondisk, GauRing, ferrer_f, GauRing_f, const
Pa3) [0,-5,5,0.1,1]                 # x-center
Pa4) [0,-5,5,0.1,1]                 # y-center
Pa5) [1.34,0.06,2.69,0.1,1]         # Effective radius [arcsec]
Pa6) [4,0.5,6,0.1,1]                # Sersic index n
Pa7) [0,-90,90,1,1]                 # Position angle [deg]
Pa8) [0.8,0.6,1,0.01,1]             # Axis ratio b/a
# SED parameters
Pa9)  [[-2,-8,0,0.1,1]]             # log(sSFR) [1/yr]
Pa10) [0,0.1,0.27,0.7,1.86,4.94]    # Burst age or Bins [Gyr]
Pa11) [[0.02,0.001,0.04,0.001,1]]   # Metallicity Z (0.02=Solar)
Pa12) [[0.7,0.3,5.1,0.1,1]]         # V-band extinction Av [mag]
Pa13) [100,40,200,1,0]              # Stellar velocity dispersion [km/s]
Pa14) [10.14,8.5,12,0.1,1]          # log(stellar mass) [Msun]
Pa15) bins                          # SFH type (Acceptable values: burst, conti, bins)
Pa16) [-2,-4,-2,0.1,0]              # logU ionization parameter
# Dust model (DL2014)
Pa26) [3,0,5,0.1,1]                 # 2175Å bump amplitude
Pa27) 0                             # SED model: 0=full, 1=stellar, 2=nebular, 3=dust
Pa28) [8.14,4.5,10,0.1,0]           # log(dust mass) [Msun]
Pa29) [1.0, 0.1, 50, 0.1, 0]        # Umin (min radiation field)
Pa30) [1.0, 0.47, 7.32, 0.1, 0]     # qPAH (PAH fraction)
Pa31) [1.0, 1.0, 3.0, 0.1, 0]       # Alpha (radiation field slope)
Pa32) [0.1, 0, 1.0, 0.1, 0]         # Gamma (illuminated fraction)

# Profile B - Disk
Pb1) disk
Pa2) sersic                         # Profile type: sersic, sersic_b, sersic_r, sersic_f, sersic_rf, sersic_bf, ferrer, edgeondisk, GauRing, ferrer_f, GauRing_f, const
Pb3) [0,-5,5,0.1,1]
Pb4) [0,-5,5,0.1,1]
Pb5) [2.69,0.67,10.75,0.1,1]        # Larger Re for disk
Pb6) [1,0.5,3,0.1,1]                # Lower n for disk
Pb7) [-60,-90,90,1,1]               # Different PA
Pb8) [0.5,0.2,1,0.01,1]             # Thinner disk
# SED parameters (different from bulge)
Pb9)  [[-1,-4,0,0.1,1]]             # Higher sSFR
Pb10) [0,0.1,0.27,0.7,1.86,4.94]    # burst age or Bins [Gyr]
Pb11) [[0.02,0.001,0.04,0.001,1]]   # metallicity Z (0.02=Solar)
Pb12) [[0.7,0.3,5.1,0.1,1]]         # V-band extinction Av [mag]
Pb13) [100,40,200,1,0]              # Stellar velocity dispersion [km/s]
Pb14) [10.64,8.5,12,0.1,1]          # Higher mass for disk
Pb15) bins                          # SFH type (Acceptable values: burst, conti, bins)
Pb16) [-3,-4,-2,0.1,0]              # logU ionization parameter
# Dust model
Pb26) [3,0,5,0.1,1]                 # 2175Å bump amplitude
Pb27) 0                             # SED model: 0=full, 1=stellar, 2=nebular, 3=dust
Pb28) [8.14,4.5,10,0.1,0]           # log(dust mass) [Msun]
Pb29) [1.0, 0.1, 50, 0.1, 0]        # Umin (min radiation field)
Pb30) [1.0, 0.47, 7.32, 0.1, 0]     # qPAH (PAH fraction)
Pb31) [1.0, 1.0, 3.0, 0.1, 0]       # Alpha (radiation field slope)
Pb32) [0.1, 0, 1.0, 0.1, 0]         # Gamma (illuminated fraction)

# One or more galaxies: Ga, Gb, ...
Ga1) mygal                          # Galaxy name
Ga2) ['a','b']                      # Profile components (bulge + disk) owned by the galaxy. NOTE it must match the profile component suffixes (P-prefixed) defined above: Pa→'a', Pb→'b', ...
Ga3) [0.0628,0.0128,0.1128,0.01,0]  # Redshift with range
Ga4) 0.0213                         # Distance modulus
Ga5) [1.,0.5,2,0.05,0]              # Spectrum normalization
Ga6) []                             # Narrow emission lines
Ga7) 1                              # Number of narrow line components
"""

TASK = """
### The original configuration (filename: {original_lyric_file})
```
{original_configuration}
```

### Instructions 
#### How to modify
{instruction}
#### How to output
The new content should be wrapped as follows, to ensure it can be directly saved as a .lyric file. Only provide the new content without any additional explanation or text.:
```lyric
<new content>
```

### Hints
#### lyric configuration template
{lyric_template}

#### 5-tuple parameter specification
{tuple_specification}
"""

def modify_lyric(
    original_lyric_file: Annotated[str, "the file name of original configuration in lyric format"],
    instruction: Annotated[str, "Instruction on how to modify the original configuration (e.g., add/delete a profile component, refine parameters of existing components). Be as specific as possible."],
    new_lyric_file: Annotated[str, "the file name of the new configuration in lyric format. If same as original_lyric_file, it will overwrite the original file."],
) -> str:
    """
    It modifies a lyric file (e.g., add/delete a profile component, refine parameters of existing profile components). Note that the instruction should be as specific as possible to ensure the best result. The new configuration will be saved to a new lyric file. If the new lyric file name is the same as the original one, it will overwrite the original file.

    Args:
        original_lyric_file: The file name of original configuration in lyric format
        instruction: Instruction on how to modify the original configuration
        new_lyric_file: The file name of the new configuration in lyric format. If same as original_lyric_file, it will overwrite the original file.

    Returns:
        str: result message indicating success or failure, and error message if failure.
    """
    client, error = create_vlm_client()
    if error:
        return {
            "status": "failure",
            "error": error
        }

    try :
        with open(original_lyric_file) as f:
            original_config = f.read()    
    except Exception as e:
        return {
            "status": "failure",
            "error": f"Read file error: {str(e)}"
        }        

    task = TASK.format(
        original_lyric_file=original_lyric_file,
        original_configuration=original_config,
        instruction=instruction,
        lyric_template=LYRIC_TEMPLATE,
        tuple_specification=TUPLE_SPECIFICATION,
    )    

    messages = [ 
        {
            "role": "user", 
            "content": task,
        }
    ]

    result = client.chat_completions_create(
        messages=messages,
        max_tokens=10240    
    )

    if isinstance(result, dict) and "content" in result:
        # extract content between ```lyric and ``` by regex
        content = result["content"]
        match = re.search(r'```lyric\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            new_content = match.group(1)
            try:
                with open(new_lyric_file, 'w') as f:
                    f.write(new_content)

                return {"status": "success", "message": f"New lyric file saved to {new_lyric_file}"}    
            except Exception as e:
                message = f"Write file error: {str(e)}"
        else:
            message = "Failed to extract new content from the response. Please ensure the response is wrapped in ```lyric ... ```."       
    else:
        message = f"Unexpected response format: {result}"

    return { "status": "failure", "error": message }       

def check_lyric_file(lyric_file: str) -> dict:
    """
    It checks if the lyric file is valid by trying to parse it. If the file is valid, it returns {"status": "success"}. If the file is invalid, it returns {"status": "failure", "error": error_message}.

    Args:
        lyric_file: The file name of the lyric file to be checked.

    Returns:
        dict: A dictionary indicating success or failure, and error message if failure.
    """
    class DummyWorkplace:
        def __enter__(self):
            import tempfile
            self.dummy_dir = tempfile.mkdtemp(prefix="check_lyric_")
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            if hasattr(self, 'dummy_dir') and os.path.exists(self.dummy_dir):
                shutil.rmtree(self.dummy_dir)

    with DummyWorkplace() as workplace:
        try:
            from galfits.gsutils import read_config_file
            _ = read_config_file(lyric_file, workplace.dummy_dir)
            return {"status": "success", "message": f"{lyric_file} is a valid lyric file."}
        except Exception as e:
            return {"status": "failure", "message": f"{lyric_file} is an invalid lyric file. Error: {str(e)}"}
        

def TEST_add_AGN():
    content = modify_lyric(
        original_lyric_file="/home/jiangbo/GALFITS_examples/41926_experiments/41926_ps.lyric",
        instruction="add an AGN as the template.",
        new_lyric_file="/home/jiangbo/GALFITS_examples/41926_ps_add_agn.lyric"
    )        
    
def TEST_add_profile_component():    
    content = modify_lyric(
        original_lyric_file="/home/jiangbo/GALFITS_examples/41926_experiments/41926_db_nosed.lyric",
        instruction="add a bar profile component.",
        new_lyric_file="/home/jiangbo/GALFITS_examples/41926_db_nosed_add_bar.lyric"
    )        
    
def TEST_add_profile_component2():    
    content = modify_lyric(
        original_lyric_file="/home/jiangbo/GALFITS_examples/41926_experiments/41926_ps_iter1.lyric",
        instruction="add a sersic profile component to fit the galaxy.",
        new_lyric_file="/home/jiangbo/GALFITS_examples/41926_db_nosed_add_bar.lyric"
    )        
    
def TEST_add_profile_component3():    
    content = modify_lyric(
        original_lyric_file="/home/jiangbo/GALFITS_examples/latest/configs/obj692",
        instruction="Add a bulge profile component to the galaxy a, i.e., the first galaxy.",
        new_lyric_file="/tmp/obj692_A_galaxy_add_bulge.lyric"
    )    

def TEST_delete_profile_component1():
    content = modify_lyric(
        original_lyric_file="/home/jiangbo/GALFITS_examples/latest/configs/obj692",
        instruction="Delete the first profile component of the galaxy bc, i.e., the second galaxy.",
        new_lyric_file="/tmp/obj692_BC_galaxy_delete_first_component.lyric"
    )    

def TEST_delete_profile_component2():
    content = modify_lyric(
        original_lyric_file="/home/jiangbo/GALFITS_examples/latest/configs/obj692",
        instruction="Delete the second profile component of the galaxy bc, i.e., the second galaxy.",
        new_lyric_file="/tmp/obj692_BC_galaxy_delete_second_component.lyric"
    )    
    
if __name__ == '__main__':
    
    #TEST_add_profile_component3()
    TEST_delete_profile_component1()
