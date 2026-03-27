import os
import re
from typing import Annotated

# Define required parameters in a centralized set for easy maintenance
REQUIRED_PARAMETERS = {
    "total_f_cont_bin1",
    "total_f_cont_bin2",
    "total_f_cont_bin3",
    "total_f_cont_bin4",
    "total_f_cont_bin5",
    "total_Av_value",
    "logM_total",
    "total_Z_value",
}

# Precompile regex patterns for better performance (compile once)
PATTERNS = {
    "Px9": re.compile(
        r"(P[a-z]9\)\s*\[\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\],\[)"
        r"(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)"
        r"(\]\])"
    ),
    "Px11": re.compile(r"(P[a-z]11\)\s*\[\[)(-?\d+\.?\d*)(,.*?\]\])"),
    "Px12": re.compile(r"(P[a-z]12\)\s*\[\[)(-?\d+\.?\d*)(,.*?\]\])"),
    "Px14": re.compile(r"(P[a-z]14\)\s*\[)(-?\d+\.?\d*)(.*\])"),
}


def parse_gssummary(gssummary_file: str) -> dict:
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
        if pname in REQUIRED_PARAMETERS:
            try:
                results[pname] = float(best_value)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid numeric value for parameter {pname}: {best_value}")

    # Check for missing required parameters
    missing_params = REQUIRED_PARAMETERS - results.keys()
    if missing_params:
        raise ValueError(f"Missing required parameters in gssummary: {sorted(missing_params)}")

    return results


def replace_Px9(text: str, values: list) -> str:
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
    return PATTERNS["Px9"].sub(replacement, text)


def replace_single_value(text: str, pattern_key: str, value: float) -> str:
    """Unified function to replace single numeric values (eliminates code duplication).

    Handles Px11, Px12, Px14 patterns.
    """
    return PATTERNS[pattern_key].sub(rf"\g<1>{value}\g<3>", text)


def update_lyric_with_gssummary(
    lyric_file: Annotated[str, "Path to the lyric configuration file"],
    gssummary_file: Annotated[str, "Path to the .gssummary file or raw content string"]
) -> dict:
    """Assign fitted values from gssummary into the lyric configuration file.

    Args:
        lyric_file: Path to the target lyric file to be updated.
        gssummary_file: Path to the .gssummary result file OR raw text content.

    Returns:
        Dictionary with status and message:
        - status: "success" or "error"
        - content: Detailed result or error message
    """
    try:
        # Parse parameter values from gssummary
        summary_data = parse_gssummary(gssummary_file)

        # Read original lyric file
        with open(lyric_file, encoding="utf-8") as f:
            lyric_content = f.read()

        # Apply all value replacements
        bin_values = [summary_data[f"total_f_cont_bin{i}"] for i in range(1, 6)]
        lyric_content = replace_Px9(lyric_content, bin_values)
        lyric_content = replace_single_value(lyric_content, "Px12", summary_data["total_Av_value"])
        lyric_content = replace_single_value(lyric_content, "Px14", summary_data["logM_total"])
        lyric_content = replace_single_value(lyric_content, "Px11", summary_data["total_Z_value"])

        # NOTE: comment out the followig line if you want to overwrite the original lyric file
        lyric_file = os.path.splitext(lyric_file)[0] + "_assigned.lyric"

        # Write updated content back to file
        with open(lyric_file, "w", encoding="utf-8") as f:
            f.write(lyric_content)

        return {
            "status": "success",
            "content": f"Successfully assigned gssummary values to {lyric_file}."
        }

    except Exception as e:
        return {
            "status": "error",
            "content": f"Failed to assign gssummary values: {str(e)}"
        }


if __name__ == "__main__":
    # Test gssummary content for demo
    test_gssummary = """
###
pname best_value uncertainty
total_f_cont_bin1 0.1 0.01
total_f_cont_bin2 0.2 0.02
total_f_cont_bin3 0.3 0.03
total_f_cont_bin4 0.4 0.04
total_f_cont_bin5 0.5 0.05
total_Av_value 1.0 0.2
logM_total 10.0 0.5
total_Z_value 0.02 0.002
    """

    # Target lyric file path
    test_lyric_path = "GALFITS_examples/latest/configs/obj104"
    update_lyric_with_gssummary(test_lyric_path, test_gssummary)