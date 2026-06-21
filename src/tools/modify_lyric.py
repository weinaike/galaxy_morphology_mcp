import ast
import os
import re

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

# Lyric section schema: family -> (repeatable, max_index).
# repeatable=False means the family carries no label letter (R1, R2, R3);
# repeatable=True means it carries a label letter (Ia, Ib, Pb, Gb, ...).
# Index ranges come from the galfits-manual skill docs and gsutils.read_config_file.
LYRIC_SECTION_SCHEMA = {
    'R': (False, 3),    # Region: R1 name, R2 [ra, dec], R3 redshift
    'I': (True, 15),    # Image band: Ix1 input image ... Ix15 use-SED
    'S': (True, 5),     # Spectrum: Sx1 file, Sx2 flux factor, Sx3 windows, Sx4 hires, Sx5 lsf (optional)
    'A': (True, 7),     # Atlas: Ax1 name ... Ax7 reference images
    'N': (True, 27),    # Nuclei/AGN: Nx1 ... Nx27
    'P': (True, 32),    # Profile: Px1 name, Px2 type ... Px32
    'G': (True, 7),     # Galaxy: Gx1 name ... Gx7 narrow-line components
    'F': (True, 8),     # Foreground star: Fx1 name ... Fx8 use-SED
}


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _looks_like_5tuple(v):
    """True if v is a [init, min, max, step, flag] numeric 5-tuple."""
    return (isinstance(v, (list, tuple)) and len(v) == 5
            and all(_is_number(e) for e in v))


def _validate_5tuple(t, key, lineno, errors):
    """Validate a [init, min, max, step, flag] 5-tuple, appending any problems.

    Rules:
      - flag must be 0 or 1;
      - init must lie within [min, max] inclusive;
      - for a free parameter (flag=1) min must be strictly less than max;
        for a fixed parameter (flag=0) min == max is allowed -- it is the
        standard way to pin a value (e.g. bar Sersic n fixed to 0.5, or an
        unused slot zeroed as [0,0,0,0,0]) -- so only min > max is rejected.
    """
    init, lo, hi, _step, flag = t
    if flag not in (0, 1):
        errors.append(f"Line {lineno}: {key} flag must be 0 or 1, got {flag!r}")
    if not (lo < hi):
        errors.append(
            f"Line {lineno}: {key} requires min < max, got min={lo}, max={hi}"
        )
    if not (lo <= init <= hi):
        errors.append(f"Line {lineno}: {key} init {init} outside [{lo}, {hi}]")


def check_lyric_file(lyric_file: Annotated[str, "path to the lyric file"]) -> dict:
    """
    Lightweight validation of a .lyric configuration file.

    This validates syntax and basic structure WITHOUT importing galfits, jax,
    astropy, or matplotlib -- avoiding the heavy module loading that the full
    ``galfits.gsutils.read_config_file`` triggers on every call. Use this when
    you only need to know whether the file is syntactically well-formed and
    structurally complete; it does not load images or build models.

    Checks performed:
      * file is readable;
      * every non-comment line has a ``)`` key/value separator and a
        well-formed key (e.g. ``R1)``, ``Ia3)``, ``Pb5)``);
      * values are parseable (Python literal, else falls back to plain string,
        mirroring ``parse_config_file``);
      * required region keys ``R1)``, ``R2)``, ``R3)`` are present;
      * ``R2)`` unpacks to ``(ra, dec)`` and ``R3)`` is numeric;
      * duplicate keys are flagged (almost always a typo);
      * within each labelled section family (``I``/``A``/``P``/``N``/``G``/``S``)
        keys are grouped by their label: a ``{F}{label}{N>1})`` line must sit
        inside the block declared by ``{F}{label}1)``. This catches typos such
        as ``Pc14)`` written inside a ``Pb1) ... Pb32)`` block;
      * the section family is known (``R``/``I``/``S``/``A``/``N``/``P``/``G``/``F``),
        non-repeatable families (``R``) carry no label, repeatable ones require
        one, and every key's index is within the family's valid range;
      * any value that is a 5-tuple ``[init, min, max, step, flag]`` (or a list
        of such 5-tuples) is checked: ``flag`` is 0 or 1, ``init`` lies within
        ``[min, max]`` inclusive, and ``min < max`` for all parameters.

    Args:
        lyric_file: The file name of the lyric file to be checked.

    Returns:
        dict: ``{"status": "success"|"failure", "message": ...}``. On success a
        summary of detected sections is appended to the message; on failure the
        message lists every problem found.
    """
    if not os.path.exists(lyric_file):
        return {"status": "failure",
                "message": f"{lyric_file} is an invalid lyric file. Error: file does not exist"}

    try:
        with open(lyric_file, 'r') as f:
            lines = f.readlines()
    except OSError as e:
        return {"status": "failure",
                "message": f"{lyric_file} is an invalid lyric file. Error: cannot read file ({e})"}

    config_data = {}
    key_counts = {}
    errors = []
    sections = {}
    # decompose key into family (one uppercase letter), label letters, index.
    # e.g. 'R1' -> ('R', '', 1), 'Ia15' -> ('I', 'a', 15), 'Pc14' -> ('P', 'c', 14)
    key_decomp_re = re.compile(r'^([A-Z])([A-Za-z]*)(\d+)$')
    # for each labelled family, the label of the component whose block is
    # currently "open" (i.e. the most recent {family}{label}1 declaration)
    current_label = {}

    for lineno, raw in enumerate(lines, start=1):
        # mirror galfits.gsutils.parse_config_file: drop inline comment + strip
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue

        if ')' not in line:
            errors.append(f"Line {lineno}: missing ')' separator: {raw.strip()!r}")
            continue

        key_part, _, value_part = line.partition(')')
        key_part = key_part.strip()
        value_str = value_part.strip()

        dm = key_decomp_re.match(key_part)
        if not dm:
            errors.append(f"Line {lineno}: malformed key {key_part!r}")
            continue

        family, label, index = dm.group(1), dm.group(2), int(dm.group(3))

        # mirror parse_config_file: try literal eval, fall back to raw string.
        # ast.literal_eval is used instead of eval for safety; the lyric format
        # only ever contains literals so behaviour is equivalent.
        try:
            value = ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            value = value_str

        key = key_part + ')'
        key_counts[key] = key_counts.get(key, 0) + 1
        config_data[key] = value

        # ---- section schema checks ----
        if family not in LYRIC_SECTION_SCHEMA:
            errors.append(f"Line {lineno}: unknown section family {family!r} in {key}")
            continue
        repeatable, max_index = LYRIC_SECTION_SCHEMA[family]
        if repeatable and not label:
            errors.append(
                f"Line {lineno}: {key} is missing its label letter "
                f"(expected e.g. {family}a{index})"
            )
        if not repeatable and label:
            errors.append(
                f"Line {lineno}: section {family!r} is not repeatable; "
                f"{key} should be {family}{index})"
            )
        if index < 1 or index > max_index:
            errors.append(
                f"Line {lineno}: {key} index {index} is out of range "
                f"1..{max_index} for family {family}"
            )

        # ---- component-block grouping (labelled families only) ----
        if label and repeatable:
            sections.setdefault(family, set()).add(label)
            if index == 1:
                # declaration line: opens this component's block
                current_label[family] = label
            else:
                cur = current_label.get(family)
                if cur is None:
                    errors.append(
                        f"Line {lineno}: {key} appears before any "
                        f"{family}*) declaration (missing {family}{label}1?)"
                    )
                elif label != cur:
                    errors.append(
                        f"Line {lineno}: {key} sits inside {family}-component "
                        f"'{cur}' but uses label '{label}' "
                        f"(expected {family}{cur}{index})"
                    )

        # ---- 5-tuple value validation ([init, min, max, step, flag]) ----
        if _looks_like_5tuple(value):
            _validate_5tuple(value, key, lineno, errors)
        elif (isinstance(value, (list, tuple)) and len(value) > 0
              and all(_looks_like_5tuple(e) for e in value)):
            for e in value:
                _validate_5tuple(e, key, lineno, errors)

    # duplicate keys
    for key, count in key_counts.items():
        if count > 1:
            errors.append(f"Duplicate key {key!r} appears {count} times (likely a typo)")

    # required region block (read_config_file accesses these unconditionally)
    for req in ('R1)', 'R2)', 'R3)'):
        if req not in config_data:
            errors.append(f"Missing required key {req!r}")

    # light value checks on the region block
    r2 = config_data.get('R2)')
    if 'R2)' in config_data and not (isinstance(r2, (list, tuple)) and len(r2) == 2):
        errors.append(f"R2) must be [ra, dec], got {r2!r}")
    r3 = config_data.get('R3)')
    if 'R3)' in config_data and not isinstance(r3, (int, float)):
        errors.append(f"R3) must be a numeric redshift, got {r3!r}")

    if errors:
        bullet = "\n  - ".join(errors)
        return {"status": "failure",
                "message": f"{lyric_file} is an invalid lyric file.\n  - {bullet}"}

    # found = {k: sorted(v) for k, v in sections.items() if v}
    return {"status": "success",
            "message": f"{lyric_file} is a valid lyric file. "}
        

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
    #TEST_delete_profile_component1()
    print(check_lyric_file("/home/jiangbo/jwst/104/output/20260617_161456_obj_104_iter2/obj_104_iter2.lyric"))
    print(check_lyric_file("/tmp/obj_104_iter2.lyric"))
