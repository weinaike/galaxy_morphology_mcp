"""Shared GALFIT feedme configuration parser."""

import os
import re
from typing import Any


def parse_feedme(config_file: str) -> dict[str, Any]:
    """Parse a GALFIT feedme file to extract file paths and fitting region.

    Resolves relative paths to absolute paths based on the feedme file location.

    Returns dict with keys:
        input, output, sigma, psf, mask, constraint (str or ""),
        fit_region (tuple of (xmin, xmax, ymin, ymax) 1-indexed, or None).
    """
    paths: dict[str, Any] = {
        "input": "",
        "output": "",
        "sigma": "",
        "psf": "",
        "mask": "",
        "constraint": "",
        "fit_region": None,
    }

    config_file = os.path.abspath(config_file)
    with open(config_file) as f:
        content = f.read()

    patterns = {
        "input": r"^A\)\s*(.+?)\s*#",
        "output": r"^B\)\s*(.+?)\s*#",
        "sigma": r"^C\)\s*(.+?)\s*#",
        "psf": r"^D\)\s*(.+?)\s*#",
        "mask": r"^F\)\s*(.+?)\s*#",
        "constraint": r"^G\)\s*(.+?)\s*#",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if value.lower() not in ("none", ""):
                value = value if os.path.isabs(value) else os.path.join(
                    os.path.dirname(config_file), value
                )
                paths[key] = value

    # Parse fitting region H) xmin xmax ymin ymax (1-indexed)
    match_h = re.search(
        r"^H\)\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*#", content, re.MULTILINE
    )
    if match_h:
        paths["fit_region"] = (
            int(match_h.group(1)),
            int(match_h.group(2)),
            int(match_h.group(3)),
            int(match_h.group(4)),
        )

    return paths


def parse_components(param_file: str) -> list[dict[str, Any]]:
    """Parse galaxy model components from a GALFIT feedme or output parameter file.

    Extracts non-sky components with their fitted parameter values.

    Returns list of dicts, each with keys:
        type (str): component type (sersic, expdisk, ferrer, edgedisk, psf, ...)
        x, y (float): center position in image pixel coords
        mag (float): integrated magnitude
        re (float): effective radius / scale length in pixels
        n (float or None): Sersic index (sersic only)
        ba (float): axis ratio b/a
        pa (float): position angle in degrees
    """
    components: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    with open(param_file, 'r') as f:
        for line in f:
            line = line.strip()

            # Detect component start
            m_type = re.match(r'^0\)\s+(\w+)', line)
            if m_type:
                comp_type = m_type.group(1).lower()
                if comp_type == 'sky':
                    current = None
                    continue
                current = {"type": comp_type, "x": 0.0, "y": 0.0, "mag": 0.0,
                           "re": 0.0, "n": None, "ba": 1.0, "pa": 0.0}
                components.append(current)
                continue

            if current is None:
                continue

            # Position: 1) x y ...
            m = re.match(r'^1\)\s+([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)', line)
            if m:
                current["x"] = float(m.group(1))
                current["y"] = float(m.group(2))
                continue

            # Magnitude: 3) mag ...
            m = re.match(r'^3\)\s+([+-]?\d+\.?\d*e?[+-]?\d*)', line, re.IGNORECASE)
            if m:
                current["mag"] = float(m.group(1))
                # For sersic/expdisk: param 3 is mag, param 4 is Re/Rs
                # For ferrer: param 3 is mu, param 4 is R_out
                # For edgedisk: param 3 is mu0, param 4 is h_s, param 5 is R_s
                continue

            # Re / Rs / R_out: 4) value ...
            m = re.match(r'^4\)\s+([+-]?\d+\.?\d*e?[+-]?\d*)', line, re.IGNORECASE)
            if m:
                current["re"] = float(m.group(1))
                continue

            # Sersic n / Ferrer alpha / Edgedisk R_s: 5) value ...
            m = re.match(r'^5\)\s+([+-]?\d+\.?\d*e?[+-]?\d*)', line, re.IGNORECASE)
            if m:
                if current["type"] == "sersic":
                    current["n"] = float(m.group(1))
                elif current["type"] == "edgedisk":
                    current["re"] = float(m.group(1))  # R_s for edgedisk
                continue

            # Axis ratio b/a: 9) value ...
            m = re.match(r'^9\)\s+([+-]?\d+\.?\d*(?:e[+-]?\d+)?)', line, re.IGNORECASE)
            if m:
                current["ba"] = float(m.group(1))
                continue

            # Position angle: 10) value ...
            m = re.match(r'^10\)\s+([+-]?\d+\.?\d*(?:e[+-]?\d+)?)', line, re.IGNORECASE)
            if m:
                current["pa"] = float(m.group(1))
                continue

    return components
