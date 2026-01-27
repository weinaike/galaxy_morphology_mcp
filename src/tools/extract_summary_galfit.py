

import os
import re
from pyparsing import Any

def extract_galfit_fit_log(log_file_path):
    fit_result_dict = {}
    def is_separator(line):
        stripped_line = line.strip()
        return stripped_line and all(c == '-' for c in stripped_line) and len(stripped_line) > 20
            
    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f.readlines()]  
                
        current_fit = []  
        for line in lines:
            if is_separator(line):
                if current_fit:
                    clean_fit = [l for l in current_fit if l.strip() != ''] 
                    if clean_fit:
                        fit_text = '\n'.join(clean_fit)  
                        fit_key = None
                        for l in clean_fit:
                            if l.strip().startswith('Init. par. file :'):
                                full_path = l.split('Init. par. file :')[-1].strip()
                                fit_key = os.path.basename(full_path)
                                break
                        if fit_key is not None:
                            fit_result_dict[fit_key] = fit_text
                current_fit = []
            else:
                current_fit.append(line)
                    
        return fit_result_dict

    except:
        return {}

def safe_float(value: str) -> float | None:
    """Safely convert a string to float, handling malformed scientific notation.

    Examples of malformed input that gets fixed:
    - '3.414e' -> '3.414' (missing exponent)
    - '1.23e-' -> '1.23' (missing exponent after sign)
    - '-4.56e+' -> '-4.56' (missing exponent after sign)

    Args:
        value: String to convert to float

    Returns:
        Float value or None if conversion fails
    """
    if not isinstance(value, str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    value = value.strip()

    # Fix malformed scientific notation: '3.414e' -> '3.414'
    # Pattern: digit, optional decimal, 'e' or 'e+' or 'e-' with no following digits
    fixed = re.sub(r'([+-]?\d+\.?\d*)[eE][+-]?$', r'\1', value)

    # If the fix didn't change anything and it's still 'e' format, try to handle edge cases
    if fixed == value and ('e' in value or 'E' in value):
        # Remove any trailing 'e', 'e+', 'e-' without digits
        fixed = re.sub(r'[eE][+-]?$', '', value)

    try:
        return float(fixed)
    except (ValueError, OverflowError):
        return None

def parse_fit_log(fits_dir: str) -> dict[str, Any]:
    """Parse GALFIT fit.log file to extract final parameters and statistics.

    The fit.log file contains the final optimized parameters with uncertainties.
    Format example:
        sersic    : (  199.86,   200.61)   26.25      2.79    0.50    0.30     5.26
                   (    0.12,     0.20)    0.06      0.31    0.48    0.09     5.78
        Chi^2 = 1593.91249,  ndof = 8175
        Chi^2/nu = 0.195
    """
    result = {
        "components": [],
        "statistics": {}
    }

    fit_log_path = os.path.join(fits_dir, "fit.log")
    if not os.path.exists(fit_log_path):
        return result

    try:
        with open(fit_log_path, 'r') as f:
            content = f.read()

        lines = content.split('\n')
        i = 0

        # Parse component parameters
        while i < len(lines):
            line = lines[i].strip()

            # Detect component type (e.g., "sersic", "expdisk", "sky")
            # Format: "sersic    : (  x,   y)   mag      r     n      b/a     PA"
            comp_match = re.match(r'^(\w+)\s*:\s*\((.+?)\)', line)
            if comp_match:
                comp_type = comp_match.group(1)
                comp_data = {"type": comp_type, "parameters": {}, "uncertainties": {}}

                # Extract parameter values from current line
                # Values: (x, y) mag radius n b/a PA (some may be "---")
                # Updated regex to handle scientific notation: 1.23e-5, 3.414e+10, etc.
                params_part = line[line.find('('):]
                values = re.findall(r'[-+]?\d*\.?\d+(?:[eE][+-]?\d+)?|---', params_part)

                # Parameter names depend on component type
                if comp_type == "sersic":
                    param_names = ["x", "y", "magnitude", "R_e", "n", "b/a", "PA"]
                elif comp_type == "expdisk":
                    param_names = ["x", "y", "magnitude", "R_s", "---", "b/a", "PA"]
                elif comp_type == "sky":
                    param_names = ["x", "y", "sky", "dsky/dx", "dsky/dy"]
                else:
                    param_names = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]

                # Parse values using safe_float to handle malformed numbers
                for j, val in enumerate(values):
                    if j < len(param_names):
                        name = param_names[j]
                        if val != "---":
                            converted = safe_float(val)
                            if converted is not None:
                                comp_data["parameters"][name] = converted
                            else:
                                comp_data["parameters"][name] = val

                # Next line contains uncertainties
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    uncert_match = re.match(r'^\((.+?)\)', next_line)
                    if uncert_match:
                        # Updated regex to handle scientific notation
                        uncert_values = re.findall(r'[-+]?\d*\.?\d+(?:[eE][+-]?\d+)?|\[\d*\.?\d+(?:[eE][+-]?\d+)?\]|\[---\]', next_line)
                        for j, val in enumerate(uncert_values):
                            if j < len(param_names):
                                name = param_names[j]
                                # Skip values in [brackets] (fixed parameters)
                                if not val.startswith('[') and val != "[---]":
                                    converted = safe_float(val)
                                    if converted is not None:
                                        comp_data["uncertainties"][name] = converted

                result["components"].append(comp_data)
                i += 2
                continue

            # Parse chi-squared statistics - use safe_float for robustness
            chi2_match = re.search(r'Chi\^2\s*=\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)', line)
            if chi2_match:
                chi2_val = safe_float(chi2_match.group(1))
                if chi2_val is not None:
                    result["statistics"]["chi2"] = chi2_val

            ndof_match = re.search(r'ndof\s*=\s*(\d+)', line)
            if ndof_match:
                result["statistics"]["ndof"] = int(ndof_match.group(1))

            chi2nu_match = re.search(r'Chi\^2/nu\s*=\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)', line)
            if chi2nu_match:
                chi2nu_val = safe_float(chi2nu_match.group(1))
                if chi2nu_val is not None:
                    result["statistics"]["chi2_nu"] = chi2nu_val

            i += 1

    except Exception as e:
        result["parse_error"] = str(e)

    return result


def extract_fits_metadata(fits_file: str) -> dict[str, Any]:
    """Extract metadata from GALFIT output FITS file headers.

    Extracts observation information, WCS data, and image properties.
    """
    metadata = {}

    try:
        from astropy.io import fits

        with fits.open(fits_file) as hdul:
            # Look for the original data extension (has OBJECT header with input info)
            for hdu in hdul:
                header = hdu.header
                object_name = header.get("OBJECT", "")

                # Found the original data extension
                if object_name and "[" in object_name:
                    metadata["object"] = object_name
                    metadata["telescope"] = header.get("TELESCOP", "Unknown")
                    metadata["instrument"] = header.get("INSTRUME", "Unknown")
                    metadata["filter"] = header.get("FILTER", "Unknown")
                    metadata["exptime"] = header.get("EXPTIME", "Unknown")
                    metadata["date_obs"] = header.get("DATE-OBS", "Unknown")

                    # Image dimensions
                    if hasattr(hdu, "data") and hdu.data is not None:
                        metadata["image_size"] = {
                            "width": hdu.data.shape[1] if len(hdu.data.shape) > 1 else 1,
                            "height": hdu.data.shape[0]
                        }

                    # WCS information
                    metadata["wcs"] = {
                        "crpix1": header.get("CRPIX1"),
                        "crpix2": header.get("CRPIX2"),
                        "crval1": header.get("CRVAL1"),
                        "crval2": header.get("CRVAL2"),
                        "cd1_1": header.get("CD1_1"),
                        "cd1_2": header.get("CD1_2"),
                        "cd2_1": header.get("CD2_1"),
                        "cd2_2": header.get("CD2_2"),
                        "ctype1": header.get("CTYPE1"),
                        "ctype2": header.get("CTYPE2"),
                    }
                    break

    except Exception as e:
        metadata["extract_error"] = str(e)

    return metadata

def parse_model_hdu_header(header) -> dict[str, Any]:
    """Parse GALFIT model HDU header to extract components and statistics.

    The model HDU header contains:
    - Component info: COMP_1, COMP_2, ... followed by parameter keys like 1_XC, 1_YC, 1_MAG, etc.
    - Statistics: CHISQ, NDOF, NFREE, NFIX, CHI2NU

    Component parameter format in header:
    - Fitted parameters: 'value +/- error' e.g., '199.9807 +/- 0.1175'
    - Fixed parameters: '[value]' e.g., '[200.5000]' (error is 0)

    Component parameter mapping (example for Sersic):
    - XC, YC: center position
    - MAG: magnitude
    - RE: effective radius
    - N: Sersic index
    - AR: axis ratio
    - PA: position angle

    For sky component:
    - XC, YC: center position
    - SKY: sky background
    - DSDX: dSky/dx
    - DSDY: dSky/dy
    """
    result = {
        "init": None,
        "components": [],
        "statistics": {}
    }

    try:
        if "INIT" in header:
            result["init"] = header["INIT"]
        # Extract statistics
        if "CHISQ" in header:
            result["statistics"]["chi2"] = safe_float(str(header["CHISQ"]))
        if "NDOF" in header:
            result["statistics"]["ndof"] = int(header["NDOF"]) if header["NDOF"] is not None else None
        if "NFREE" in header:
            result["statistics"]["nfree"] = int(header["NFREE"]) if header["NFREE"] is not None else None
        if "NFIX" in header:
            result["statistics"]["nfix"] = int(header["NFIX"]) if header["NFIX"] is not None else None
        if "CHI2NU" in header:
            result["statistics"]["chi2_nu"] = safe_float(str(header["CHI2NU"]))

        # Find all component headers (COMP_1, COMP_2, etc.)
        comp_numbers = []
        for key in header.keys():
            key_str = str(key)
            if key_str.startswith("COMP_"):
                try:
                    comp_num = int(key_str.split("_")[1])
                    comp_numbers.append(comp_num)
                except (ValueError, IndexError):
                    continue

        # Sort component numbers
        comp_numbers.sort()

        # Determine component type based on available parameters
        comp_type_map = {
            # Sersic component has: XC, YC, MAG, RE, N, AR, PA
            ("XC", "YC", "MAG", "RE", "N", "AR", "PA"): "sersic",
            # Exponential disk has: XC, YC, MAG, RE (similar to Sersic but treated differently)
            ("XC", "YC", "MAG"): "expdisk",
            # Sky component has: XC, YC, SKY, DSDX, DSDY
            ("XC", "YC", "SKY", "DSDX", "DSDY"): "sky",
        }

        # Map parameter names to the names used in the old format
        param_name_map = {
            "XC": "x",
            "YC": "y",
            "MAG": "magnitude",
            "RE": "R_e",
            "N": "n",
            "AR": "b/a",
            "PA": "PA",
            "SKY": "sky",
            "DSDX": "dsky/dx",
            "DSDY": "dsky/dy",
        }

        # Extract each component's parameters
        for comp_num in comp_numbers:
            comp_data = {
                "parameters": {},
                "uncertainties": {}
            }

            # Get all parameter keys for this component (e.g., 1_XC, 1_YC, 1_MAG, etc.)
            prefix = f"{comp_num}_"
            param_keys = []
            for key in header.keys():
                key_str = str(key)
                if key_str.startswith(prefix):
                    param_name = key_str[len(prefix):]
                    param_keys.append(param_name)

            # Determine component type based on available parameters
            comp_type = "unknown"
            for type_params, type_name in comp_type_map.items():
                if all(p in param_keys for p in type_params):
                    comp_type = type_name
                    break
            comp_data["type"] = comp_type

            # Extract parameter values (format: "value +/- error" or "[value]")
            for param_key in param_keys:
                old_param_name = param_name_map.get(param_key, param_key.lower())
                value_str = str(header[prefix + param_key])

                # Check if it's a fixed parameter in brackets [value]
                bracket_match = re.match(r'\[([^\]]+)\]', value_str)
                if bracket_match:
                    # Fixed parameter: extract value from brackets, error is 0
                    value = safe_float(bracket_match.group(1))
                    if value is not None:
                        comp_data["parameters"][old_param_name] = value
                        comp_data["uncertainties"][old_param_name] = 0.0
                    continue

                # Parse fitted parameter: "value +/- error"
                # Match scientific notation: e.g., "1.350e-04 +/- 7.181e-05"
                pm_match = re.match(r'([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*\+/-\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)', value_str)
                if pm_match:
                    value = safe_float(pm_match.group(1))
                    error = safe_float(pm_match.group(2))
                    if value is not None:
                        comp_data["parameters"][old_param_name] = value
                    if error is not None:
                        comp_data["uncertainties"][old_param_name] = error
                    continue

                # Try to parse as plain number
                value = safe_float(value_str)
                if value is not None:
                    comp_data["parameters"][old_param_name] = value

            result["components"].append(comp_data)

    except Exception as e:
        result["parse_error"] = str(e)

    return result


def extract_summary_from_galfit(fits_file: str, config_file: str = None) -> str | None:
    """Extract comprehensive summary information from GALFIT FITS output file.

    Reads all information from the FITS file header (model HDU):
    - Component parameters and uncertainties
    - Chi-squared statistics (CHISQ, NDOF, CHI2NU)
    - Observation metadata, WCS information

    This is more reliable than reading from fit.log because:
    - Each FITS file contains its own fitting results
    - Multiple GALFIT runs can be executed in parallel
    - No issues with log file being overwritten/append

    Returns the path to the saved summary Markdown file or None if failed.
    """
    try:
        from astropy.io import fits

        fits_dir = os.path.dirname(fits_file)
        base_name = os.path.splitext(os.path.basename(fits_file))[0]

        # Find the model HDU (OBJECT = 'model')
        fit_results = None
        metadata = {}

        with fits.open(fits_file) as hdul:
            for hdu in hdul:
                header = hdu.header
                object_name = header.get("OBJECT", "")

                # Model HDU - contains fitting results
                if object_name == "model":
                    fit_results = parse_model_hdu_header(header)

                # Original data HDU - contains observation metadata
                if object_name and "[" in object_name:
                    metadata["object"] = object_name
                    metadata["telescope"] = header.get("TELESCOP", "Unknown")
                    metadata["instrument"] = header.get("INSTRUME", "Unknown")
                    metadata["filter"] = header.get("FILTER", "Unknown")
                    metadata["exptime"] = header.get("EXPTIME", "Unknown")
                    metadata["date_obs"] = header.get("DATE-OBS", "Unknown")

                    # Image dimensions
                    if hasattr(hdu, "data") and hdu.data is not None:
                        metadata["image_size"] = {
                            "width": hdu.data.shape[1] if len(hdu.data.shape) > 1 else 1,
                            "height": hdu.data.shape[0]
                        }

                    # WCS information
                    metadata["wcs"] = {
                        "crpix1": header.get("CRPIX1"),
                        "crpix2": header.get("CRPIX2"),
                        "crval1": header.get("CRVAL1"),
                        "crval2": header.get("CRVAL2"),
                        "cd1_1": header.get("CD1_1"),
                        "cd1_2": header.get("CD1_2"),
                        "cd2_1": header.get("CD2_1"),
                        "cd2_2": header.get("CD2_2"),
                        "ctype1": header.get("CTYPE1"),
                        "ctype2": header.get("CTYPE2"),
                    }

        if fit_results is None:
            raise ValueError("Could not find model HDU in FITS file")

        # Build Markdown content
        md_lines = []

        # Header
        md_lines.append(f"# GALFIT Fitting Summary")
        md_lines.append("")
        md_lines.append(f"**Output File:** `{fits_file}`")
        md_lines.append("")

        # input configuration
        md_lines.append("---")
        md_lines.append("")
        md_lines.append("## Init. par. file Content")
        md_lines.append("")
        if config_file:
            with open(config_file) as f: 
                md_lines.append(f.read())

        fit_log_path = os.path.join(os.path.dirname(config_file) if config_file else ".", "fit.log")    
        if os.path.exists(fit_log_path):
            fit_log = extract_galfit_fit_log(fit_log_path)
            fit_result = fit_log.get(os.path.abspath(config_file), None) or \
                fit_log.get(config_file, None) or fit_log.get(os.path.basename(config_file), None)

            if fit_result:
                md_lines.append("## Fit log Content")
                md_lines.append(fit_result)

        # Observation metadata section
        md_lines.append("---")
        md_lines.append("")
        md_lines.append("## Observation Metadata")
        md_lines.append("")

        md_lines.append("| Property | Value |")
        md_lines.append("|----------|-------|")
        md_lines.append(f"| Object | {metadata.get('object', 'Unknown')} |")
        md_lines.append(f"| Telescope | {metadata.get('telescope', 'Unknown')} |")
        md_lines.append(f"| Instrument | {metadata.get('instrument', 'Unknown')} |")
        md_lines.append(f"| Filter | {metadata.get('filter', 'Unknown')} |")
        md_lines.append(f"| Exposure Time | {metadata.get('exptime', 'Unknown')} s |")
        md_lines.append(f"| Observation Date | {metadata.get('date_obs', 'Unknown')} |")

        img_size = metadata.get("image_size", {})
        if img_size:
            md_lines.append(f"| Image Size | {img_size.get('width')} × {img_size.get('height')} pixels |")

        md_lines.append("")

        # WCS information
        wcs = metadata.get("wcs", {})
        if wcs and any(v is not None for v in wcs.values()):
            md_lines.append("### World Coordinate System (WCS)")
            md_lines.append("")
            md_lines.append("| Parameter | Value |")
            md_lines.append("|-----------|-------|")
            md_lines.append(f"| CRPIX1 | {wcs.get('crpix1', 'N/A')} |")
            md_lines.append(f"| CRPIX2 | {wcs.get('crpix2', 'N/A')} |")
            md_lines.append(f"| CRVAL1 (RA) | {wcs.get('crval1', 'N/A')} |")
            md_lines.append(f"| CRVAL2 (Dec) | {wcs.get('crval2', 'N/A')} |")
            md_lines.append(f"| CTYPE1 | {wcs.get('ctype1', 'N/A')} |")
            md_lines.append(f"| CTYPE2 | {wcs.get('ctype2', 'N/A')} |")
            md_lines.append("")

        # Footer
        md_lines.append("---")
        md_lines.append("")
        md_lines.append("*Generated by GALFIT MCP Server*")

        # Write to Markdown file
        summary_filename = os.path.join(fits_dir, f"{base_name}_summary.md")
        with open(summary_filename, 'w') as f:
            f.write('\n'.join(md_lines))

        return summary_filename

    except Exception as e:
        # Try to save error information
        try:
            fits_dir = os.path.dirname(fits_file)
            base_name = os.path.splitext(os.path.basename(fits_file))[0]
            summary_filename = os.path.join(fits_dir, f"{base_name}_summary.md")

            with open(summary_filename, 'w') as f:
                f.write(f"# GALFIT Fitting Summary\n\n")
                f.write(f"**Error:** {str(e)}\n\n")
                f.write("Could not extract complete summary information.\n")

            return summary_filename
        except:
            return None


if __name__ == '__main__':
    result = extract_summary_from_galfit(
        fits_file="/home/jiangbo/galfit/galfit_examples_0128/goodsn_5536/archives/20260130T134751/goodsn_5536_f160w_galfit.fits",
        config_file="/home/jiangbo/galfit/galfit_examples_0128/goodsn_5536/archives/20260130T134751/goodsn_5536_f160w.feedme"
    )

    print(result)