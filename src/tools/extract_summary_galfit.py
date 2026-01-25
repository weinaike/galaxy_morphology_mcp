

import os
import re
from pyparsing import Any


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


def extract_summary_from_galfit(fits_file: str, log_output: str) -> str | None:
    """Extract comprehensive summary information from GALFIT output.
    
    Combines information from:
    - fit.log: Final parameters, uncertainties, chi-squared statistics
    - FITS headers: Observation metadata, WCS information

    Returns the path to the saved summary Markdown file or None if failed.
    """
    try:
        fits_dir = os.path.dirname(fits_file)
        base_name = os.path.splitext(os.path.basename(fits_file))[0]

        # Parse fit.log for final parameters and statistics
        fit_results = parse_fit_log(fits_dir)

        # Extract FITS metadata
        metadata = extract_fits_metadata(fits_file)

        # Add iteration info from log output
        lines = log_output.split('\n')
        iterations = []
        for line in lines:
            iter_match = re.search(r'Iteration\s*:\s*(\d+)\s+Chi2nu:\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)', line)
            if iter_match:
                chi2nu_val = safe_float(iter_match.group(2))
                if chi2nu_val is not None:
                    iterations.append({
                        "number": int(iter_match.group(1)),
                        "chi2_nu": chi2nu_val
                    })

        # Build Markdown content
        md_lines = []

        # Header
        md_lines.append(f"# GALFIT Fitting Summary")
        md_lines.append("")
        md_lines.append(f"**Output File:** `{fits_file}`")
        md_lines.append("")

        # Statistics section
        md_lines.append("---")
        md_lines.append("")
        md_lines.append("## Fit Statistics")
        md_lines.append("")

        stats = fit_results.get("statistics", {})
        if stats:
            chi2 = stats.get("chi2")
            ndof = stats.get("ndof")
            chi2_nu = stats.get("chi2_nu")

            md_lines.append("| Metric | Value |")
            md_lines.append("|--------|-------|")
            if chi2 is not None:
                md_lines.append(f"| Chi² | {chi2:.5f} |")
            if ndof is not None:
                md_lines.append(f"| Degrees of Freedom | {ndof} |")
            if chi2_nu is not None:
                md_lines.append(f"| Chi²/ν (reduced) | {chi2_nu:.5f} |")
            md_lines.append("")

        # Iterations
        if iterations:
            md_lines.append(f"**Total Iterations:** {len(iterations)}")
            md_lines.append("")

        # Components section
        md_lines.append("---")
        md_lines.append("")
        md_lines.append("## Fitted Components")
        md_lines.append("")

        components = fit_results.get("components", [])
        for i, comp in enumerate(components, 1):
            comp_type = comp.get("type", "unknown")
            md_lines.append(f"### Component {i}: {comp_type.upper()}")
            md_lines.append("")

            params = comp.get("parameters", {})
            uncerts = comp.get("uncertainties", {})

            # Parameter table
            md_lines.append("| Parameter | Value | Uncertainty |")
            md_lines.append("|-----------|-------|-------------|")

            # Common parameter names and their display names
            param_display_names = {
                "x": "Position X",
                "y": "Position Y",
                "magnitude": "Magnitude",
                "R_e": "R_e (pix)",
                "R_s": "R_s (pix)",
                "n": "Sersic n",
                "b/a": "Axis Ratio (b/a)",
                "PA": "Position Angle (°)",
                "sky": "Sky Background",
                "dsky/dx": "dSky/dx",
                "dsky/dy": "dSky/dy",
            }

            for param_key, param_value in params.items():
                display_name = param_display_names.get(param_key, param_key)
                uncert = uncerts.get(param_key)

                # Format value
                if isinstance(param_value, float):
                    value_str = f"{param_value:.5f}"
                else:
                    value_str = str(param_value)

                # Format uncertainty
                if uncert is not None:
                    uncert_str = f"±{uncert:.5f}"
                else:
                    uncert_str = "—"

                md_lines.append(f"| {display_name} | {value_str} | {uncert_str} |")

            md_lines.append("")

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

