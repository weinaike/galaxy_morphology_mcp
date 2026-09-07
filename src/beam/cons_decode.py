"""Decode GALFIT ``.cons`` constraint files into effective numeric bands.

Implements the empirically verified encoding contract of this machine's
GALFIT 3.0.5 (CLAUDE.md, ``.cons`` numeric-band semantics note):

* ``<N> <p> <a> to <b>``  -> absolute band, enforced as written;
* ``<N> <p> <a> <b>``     -> bare two-number row, decoded as offsets from the
  component's INPUT value, effective band ``[input-|a|, input+|b|]`` (matches
  both documented cases: ``2 x -1 0.5`` -> [input-1, input+0.5] and the
  KILOGAS_432 incident ``2 n 0.5 8`` with n_init=4 -> [3.5, 12.0]);
* ``<chain> x|y offset``  -> concentric hard-constraint chain (no numeric band);
* ratio / pair rows (``1_5_3_2 re ratio``, ``3/5 re 1 3``) -> no decodable
  per-component band, excluded from bound-hit scanning.

The bound-hit scanner compares fitted values against the **effective** bands
and tags each hit's provenance against the mandatory default bound set
(``original``) vs candidate-driven tightenings (``self-imposed``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# .cons parameter names -> canonical internal names (parse_components keys)
CONS_PARAM_ALIASES = {
    "re": "re",
    "rs": "re",
    "fwhm": "re",
    "n": "n",
    "q": "q",
    "ba": "q",
    "x": "x",
    "y": "y",
    "mag": "mag",
    "pa": "pa",
    "f1a": "f1a",
    "f1p": "f1p",
}

# Parameters the bound-hit scanner reports (shape/mag axes; centres excluded
# from provenance logic but still scannable).
SCANNABLE_PARAMS = {"re", "n", "q", "mag", "pa", "x", "y"}

_BLOCK_START_RE = re.compile(r"^\s*0\)\s*(\S+)")


@dataclass
class ConsRow:
    """One parsed .cons row."""

    comp_spec: str  # component spec as written ("3", "1_2_3_4", "3-7", "3/5")
    param: str  # canonical internal param name
    form: str  # "abs" | "rel" | "rel_pair" | "offset" | "ratio" | "unknown"
    a: float | None = None  # 'to' rows: lo; bare rows: first offset number
    b: float | None = None  # 'to' rows: hi; bare rows: second offset number
    raw: str = ""
    lineno: int = 0

    @property
    def is_single_component(self) -> bool:
        """True when the spec addresses exactly one component by number."""
        return bool(re.fullmatch(r"\d+", self.comp_spec))


@dataclass
class DecodedCons:
    rows: list[ConsRow] = field(default_factory=list)
    chains: dict[str, set[str]] = field(default_factory=dict)  # spec -> {x, y}
    warnings: list[str] = field(default_factory=list)

    def numeric_rows(self) -> list[ConsRow]:
        """Rows addressing a single component with a numeric band."""
        return [
            r
            for r in self.rows
            if r.is_single_component and r.form in {"abs", "rel"}
        ]


@dataclass
class BoundContext:
    """Context needed to judge bound provenance against the default set."""

    psf_fwhm_px: float | None = None
    fit_region: tuple[float, float, float, float] | None = None  # xmin xmax ymin ymax

    def default_band(self, param: str, *, companion: bool = False) -> tuple[float, float] | None:
        """The mandatory default bound set (solution-space definition S3)."""
        if param == "re":
            lo = 0.1 if self.psf_fwhm_px is None else max(0.1, 0.5 * self.psf_fwhm_px)
            if self.fit_region is None:
                return None
            side = max(
                self.fit_region[1] - self.fit_region[0] + 1,
                self.fit_region[3] - self.fit_region[2] + 1,
            )
            return (lo, 0.5 * side)
        if param == "n":
            return (0.1, 8.0)
        if param == "q":
            return (0.05, 1.0)
        if param in {"x", "y"}:
            return (-5.0, 5.0) if companion else (-2.0, 2.0)
        return None


@dataclass
class BoundHit:
    comp_number: int
    name: str
    param: str
    fitted: float
    band: tuple[float, float]
    band_type: str  # "abs-to" | "rel-bare"
    direction: str  # "upper" | "lower"
    provenance: str  # "original" | "self-imposed" | "unspecified"
    exempt: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "comp": f"{self.comp_number}({self.name})",
            "param": self.param,
            "fitted": round(self.fitted, 4),
            "band": [round(self.band[0], 4), round(self.band[1], 4)],
            "band_type": self.band_type,
            "direction": self.direction,
            "provenance": self.provenance,
            "exempt": self.exempt,
            "note": self.note,
        }


def decode_cons_file(cons_file: str) -> DecodedCons:
    """Parse a .cons file into rows / chains / warnings."""
    decoded = DecodedCons()
    if not cons_file or not os.path.exists(cons_file):
        decoded.warnings.append(f"cons file not found: {cons_file!r}")
        return decoded

    with open(cons_file, encoding="utf-8") as f:
        raw_lines = f.readlines()

    for lineno, raw in enumerate(raw_lines, start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if tokens[-1].lower() in {"offset", "ratio"}:
            if len(tokens) != 3:
                decoded.warnings.append(f"cons L{lineno}: unrecognised operator row '{line}'")
                continue
            comp_spec, param = tokens[0], tokens[1].lower()
            row = ConsRow(comp_spec, CONS_PARAM_ALIASES.get(param, param),
                          tokens[-1].lower(), raw=line, lineno=lineno)
            decoded.rows.append(row)
            if row.form == "offset" and row.param in {"x", "y"}:
                decoded.chains.setdefault(comp_spec, set()).add(row.param)
            continue
        # 'to' rows: "<N> <param> <a> to <b>"
        if "to" in tokens[2:]:
            if len(tokens) == 5 and tokens[3].lower() == "to":
                try:
                    a, b = float(tokens[2]), float(tokens[4])
                except ValueError:
                    decoded.warnings.append(f"cons L{lineno}: bad numbers in '{line}'")
                    continue
                param = tokens[1].lower()
                decoded.rows.append(
                    ConsRow(tokens[0], CONS_PARAM_ALIASES.get(param, param), "abs",
                            a=a, b=b, raw=line, lineno=lineno)
                )
            else:
                decoded.warnings.append(f"cons L{lineno}: malformed 'to' row '{line}'")
            continue
        # bare two-number rows / pair rows
        if len(tokens) == 4:
            comp_spec, param = tokens[0], tokens[1].lower()
            try:
                a, b = float(tokens[2]), float(tokens[3])
            except ValueError:
                decoded.warnings.append(f"cons L{lineno}: bad numbers in '{line}'")
                continue
            canonical = CONS_PARAM_ALIASES.get(param, param)
            form = "rel" if re.fullmatch(r"\d+", comp_spec) else "rel_pair"
            decoded.rows.append(
                ConsRow(comp_spec, canonical, form, a=a, b=b, raw=line, lineno=lineno)
            )
            continue
        decoded.warnings.append(f"cons L{lineno}: unrecognised row '{line}'")

    for spec, params in decoded.chains.items():
        missing = {"x", "y"} - params
        if missing:
            decoded.warnings.append(
                f"cons: offset chain '{spec}' binds only {sorted(params)} (x and y must be paired)"
            )
    return decoded


def effective_band(row: ConsRow, input_value: float) -> tuple[float, float] | None:
    """Effective absolute band of a numeric row given the component's INPUT value."""
    if row.form == "abs":
        return (row.a, row.b)
    if row.form == "rel":
        # [input - |a|, input + |b|] — matches both documented cases (module docstring)
        return (input_value - abs(row.a), input_value + abs(row.b))
    return None


def number_components(param_file: str, name_file: str | None = None) -> dict[int, dict]:
    """Map GALFIT component numbers -> parse_components-style dicts.

    GALFIT numbers every ``0)`` block (sky included) in file order; the
    concentric .cons chain references those numbers. Sky blocks are excluded
    from the returned mapping (never a constraint target) but still consume
    their number slot. ``name_file`` supplies the ``# STRUCTURE:`` names when
    ``param_file`` is a galfit.NN output (GALFIT drops the comments).
    """
    from tools.parse_feedme import parse_components  # lazy: src layout

    numbers: dict[int, dict] = {}
    names_from = name_file or param_file
    try:
        comps = parse_components(param_file, name_file=names_from)
    except Exception:
        return numbers
    # establish block order by scanning 0) lines
    types_in_order: list[str] = []
    with open(param_file, encoding="utf-8") as f:
        for raw in f:
            m = _BLOCK_START_RE.match(raw)
            if m:
                types_in_order.append(m.group(1).lower())
    luminous = [c for c in comps]
    if len(luminous) > len(types_in_order):
        return numbers
    li = 0
    for number, ctype in enumerate(types_in_order, start=1):
        if ctype == "sky":
            continue
        if li < len(luminous):
            comp = dict(luminous[li])
            comp["number"] = number
            numbers[number] = comp
            li += 1
    return numbers


def _comp_value(comp: dict, param: str) -> float | None:
    key = {"re": "re", "n": "n", "q": "ba", "mag": "mag", "pa": "pa", "x": "x", "y": "y"}[param]
    v = comp.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _provenance(row: ConsRow, band: tuple[float, float], ctx: BoundContext,
                companion: bool) -> str:
    default = ctx.default_band(row.param, companion=companion)
    if default is None:
        return "unspecified"
    eps = 1e-6 + 0.01 * max(abs(default[0]), abs(default[1]))
    if row.form == "rel":
        # input-relative rows: the offsets are the band as written — compare
        # those against the default window (comparing the effective absolute
        # band mislabels the default companion ±5 window as self-imposed)
        same = abs((row.a or 0.0) - default[0]) <= eps \
            and abs((row.b or 0.0) - default[1]) <= eps
        return "original" if same else "self-imposed"
    same = abs(band[0] - default[0]) <= eps and abs(band[1] - default[1]) <= eps
    return "original" if same else "self-imposed"


def scan_bound_hits(
    decoded: DecodedCons,
    input_comps: dict[int, dict],
    fitted_comps: dict[int, dict],
    ctx: BoundContext,
    *,
    hit_rel_tol: float = 0.02,
) -> list[BoundHit]:
    """List every bound-hit parameter (fitted value at an effective band edge).

    Criterion (workflow d.ii): |fitted - bound| <= 2% * |bound|. Unbounded
    parameters never hit. Standing exemptions are flagged, not suppressed:
    q upper at the 1.0 domain edge; Re lower bounds at the PSF scale (<= 1 px);
    the bar n hard prior.
    """
    hits: list[BoundHit] = []
    companion_names = ("comp", "companion", "secondary", "satellite")
    for row in decoded.numeric_rows():
        number = int(row.comp_spec)
        inp = input_comps.get(number)
        fit = fitted_comps.get(number)
        if inp is None or fit is None or row.param not in SCANNABLE_PARAMS:
            continue
        input_value = _comp_value(inp, row.param)
        fitted_value = _comp_value(fit, row.param)
        if input_value is None or fitted_value is None:
            continue
        band = effective_band(row, input_value)
        if band is None:
            continue
        name = fit.get("name") or str(number)
        is_companion = any(tag in str(name).lower() for tag in companion_names)
        provenance = _provenance(row, band, ctx, is_companion)
        direction = ""
        if abs(fitted_value - band[1]) <= hit_rel_tol * max(abs(band[1]), 1e-9):
            direction = "upper"
        elif abs(fitted_value - band[0]) <= hit_rel_tol * max(abs(band[0]), 1e-9):
            direction = "lower"
        if not direction:
            continue
        exempt, note = False, ""
        if row.param == "q" and direction == "upper" and band[1] >= 1.0:
            exempt, note = True, "q<=1 domain edge: not relaxable"
        if row.param == "re" and direction == "lower" and band[0] <= 1.0:
            exempt, note = True, "PSF-scale Re floor: point-source identity question, not relaxable"
        if row.param == "n" and "bar" in str(name).lower():
            exempt, note = True, "bar n=0.5 hard prior: not relaxable"
        hits.append(
            BoundHit(
                comp_number=number,
                name=name,
                param=row.param,
                fitted=fitted_value,
                band=band,
                band_type="abs-to" if row.form == "abs" else "rel-bare",
                direction=direction,
                provenance=provenance,
                exempt=exempt,
                note=note,
            )
        )
    return hits


def scan_cons_bound_hits(
    cons_file: str,
    input_param_file: str,
    fitted_param_file: str,
    *,
    psf_fwhm_px: float | None = None,
    fit_region: tuple[float, float, float, float] | None = None,
) -> tuple[list[dict], list[str]]:
    """Convenience digest-facing API: decode + effective bands + bound-hit scan.

    Returns ``(hits_as_dicts, warnings)``.
    """
    decoded = decode_cons_file(cons_file)
    input_comps = number_components(input_param_file)
    # GALFIT drops the # STRUCTURE: comments in its output: recover the names
    # from the input feedme (companion default windows depend on them)
    fitted_comps = number_components(fitted_param_file, name_file=input_param_file)
    ctx = BoundContext(psf_fwhm_px=psf_fwhm_px, fit_region=fit_region)
    hits = scan_bound_hits(decoded, input_comps, fitted_comps, ctx)
    return [h.to_dict() for h in hits], decoded.warnings
