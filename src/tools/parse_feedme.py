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


# Comment line naming a component block, e.g. "# STRUCTURE: BULGE".
# GALFIT input feedme files carry it right above each "0) <type>" line;
# GALFIT output parameter files (galfit.NN) silently drop it, so names must
# be recovered from the paired input file (see parse_components name_file).
STRUCTURE_RE = re.compile(r'^#\s*STRUCTURE:\s*(\S+)', re.IGNORECASE)


def parse_components(param_file: str, name_file: str | None = None) -> list[dict[str, Any]]:
    """Parse galaxy model components from a GALFIT feedme or output parameter file.

    Extracts non-sky components with their fitted parameter values.

    Args:
        param_file: feedme or GALFIT output parameter file (galfit.NN). When
            parsing galfit.NN the fitted values are read there, but the
            "# STRUCTURE:" naming comments are dropped by GALFIT — pass the
            paired input feedme as *name_file* to recover component names by
            block order (sky blocks excluded on both sides).
        name_file: optional input feedme used only as a name source when
            *param_file* itself carries no "# STRUCTURE:" comments.

    Returns list of dicts, each with keys:
        type (str): component type (sersic, expdisk, ferrer, edgedisk, psf, ...)
        name (str): semantic component name from the "# STRUCTURE:" comment
            (lower-cased), e.g. disk/bulge/bar/lens/companion/agn; falls back
            to the type name (with a numeric suffix for repeats) when absent.
        x, y (float): center position in image pixel coords
        mag (float): integrated magnitude
        re (float): effective radius / scale length in pixels
        n (float or None): Sersic index (sersic only)
        ba (float): axis ratio b/a
        pa (float): position angle in degrees
        toggles (dict): per-parameter fit toggles (1=free, 0=fixed) keyed by
            x, y, mag, re, n, ba, pa (absent when not present in the file)
    """
    components: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_name: str | None = None

    def _record_toggle(key: str, line: str, pattern: str) -> None:
        """Store the fit-toggle column of a "N) value toggle" parameter line."""
        assert current is not None
        tm = re.match(pattern, line, re.IGNORECASE)
        if tm:
            try:
                current["toggles"][key] = int(float(tm.group(1)))
            except ValueError:
                pass

    with open(param_file, 'r') as f:
        for line in f:
            stripped = line.strip()

            # Semantic name comment preceding the component block. Captured
            # unconditionally: the comment for block N+1 arrives while block N
            # is still "open" (current != None), so gating on current would
            # drop every name after the first component.
            m_struct = STRUCTURE_RE.match(stripped)
            if m_struct:
                pending_name = m_struct.group(1).lower()
                continue

            line = stripped

            # Detect component start
            m_type = re.match(r'^0\)\s+(\w+)', line)
            if m_type:
                comp_type = m_type.group(1).lower()
                if comp_type == 'sky':
                    current = None
                    pending_name = None
                    continue
                current = {"type": comp_type, "name": pending_name or comp_type,
                           "x": 0.0, "y": 0.0, "mag": 0.0,
                           "re": 0.0, "n": None, "ba": 1.0, "pa": 0.0,
                           "toggles": {}}
                pending_name = None
                components.append(current)
                continue

            if current is None:
                continue

            # Position: 1) x y tx ty ...
            m = re.match(r'^1\)\s+([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)'
                         r'(?:\s+([01]))?(?:\s+([01]))?', line)
            if m:
                current["x"] = float(m.group(1))
                current["y"] = float(m.group(2))
                if m.group(3) is not None:
                    current["toggles"]["x"] = int(m.group(3))
                if m.group(4) is not None:
                    current["toggles"]["y"] = int(m.group(4))
                continue

            # Magnitude: 3) mag ...
            m = re.match(r'^3\)\s+([+-]?\d+\.?\d*e?[+-]?\d*)', line, re.IGNORECASE)
            if m:
                current["mag"] = float(m.group(1))
                _record_toggle("mag", line, r'^3\)\s+\S+\s+([01])')
                # For sersic/expdisk: param 3 is mag, param 4 is Re/Rs
                # For ferrer: param 3 is mu, param 4 is R_out
                # For edgedisk: param 3 is mu0, param 4 is h_s, param 5 is R_s
                continue

            # Re / Rs / R_out: 4) value ...
            m = re.match(r'^4\)\s+([+-]?\d+\.?\d*e?[+-]?\d*)', line, re.IGNORECASE)
            if m:
                current["re"] = float(m.group(1))
                _record_toggle("re", line, r'^4\)\s+\S+\s+([01])')
                continue

            # Sersic n / Ferrer alpha / Edgedisk R_s: 5) value ...
            m = re.match(r'^5\)\s+([+-]?\d+\.?\d*e?[+-]?\d*)', line, re.IGNORECASE)
            if m:
                if current["type"] == "sersic":
                    current["n"] = float(m.group(1))
                    _record_toggle("n", line, r'^5\)\s+\S+\s+([01])')
                elif current["type"] == "edgedisk":
                    current["re"] = float(m.group(1))  # R_s for edgedisk
                continue

            # Axis ratio b/a: 9) value ...
            m = re.match(r'^9\)\s+([+-]?\d+\.?\d*(?:e[+-]?\d+)?)', line, re.IGNORECASE)
            if m:
                current["ba"] = float(m.group(1))
                _record_toggle("ba", line, r'^9\)\s+\S+\s+([01])')
                continue

            # Position angle: 10) value ...
            m = re.match(r'^10\)\s+([+-]?\d+\.?\d*(?:e[+-]?\d+)?)', line, re.IGNORECASE)
            if m:
                current["pa"] = float(m.group(1))
                _record_toggle("pa", line, r'^10\)\s+\S+\s+([01])')
                continue

    # GALFIT output files (galfit.NN) drop the "# STRUCTURE:" naming comments:
    # recover names by block order from the paired input feedme when provided.
    have_struct_names = any(STRUCTURE_RE.match(l.strip())
                            for l in open(param_file).read().splitlines()
                            if l.strip())
    if not have_struct_names and name_file and os.path.exists(name_file):
        named = parse_components(name_file)
        if len(named) == len(components):
            for comp, src in zip(components, named):
                comp["name"] = src["name"]

    _ensure_unique_names(components)
    return components


def _ensure_unique_names(components: list[dict[str, Any]]) -> None:
    """Disambiguate repeated semantic names (companion2, disk3, ...)."""
    seen: dict[str, int] = {}
    for comp in components:
        base = comp.get("name") or comp["type"]
        count = seen.get(base, 0) + 1
        seen[base] = count
        comp["name"] = base if count == 1 else f"{base}{count}"
