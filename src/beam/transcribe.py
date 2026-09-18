"""Mechanical candidate transcriber: primitives + parent feedme (structure
template) + parent galfit.NN (warm-start backfill) -> child ``_iter{n}.feedme``
+ ``iter{n}.cons``.

Class contract (§Faithful-Execution Principle, now enforced by code):
* Class-A fields (component type, STRUCTURE name, n/toggle states, declared
  parameter values and bounds, add/remove targets, F1) are written verbatim
  from the candidate — never adjusted; an inapplicable primitive aborts the
  whole transcription (the caller discards the candidate).
* Class-B fills (parameters the candidate did not declare) take the parent
  galfit.NN converged value with the parent round's toggle (warm start);
  a parameter on an ADDED component without a declared value takes a
  spec-prior default.
* The sky block is the manually provided setting of the input feedme and is
  carried verbatim — never backfilled, never modified (not a search dimension).
* The .cons concentric chain (K>=2 central components) and the default bound
  set are written unconditionally, independent of candidate declarations;
  candidate bounds may only TIGHTEN the defaults (intersection).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from beam.cons_decode import EXPDISK_FACTOR, re_floor_px

ROW_RE = re.compile(r"^(\s*)([0-9]+|F\d|B\d|C0|Z)\)\s*(.*)$")
STRUCTURE_COMMENT_RE = re.compile(r"^#\s*STRUCTURE:\s*(\S+)", re.IGNORECASE)
COMPONENT_NO_RE = re.compile(
    r"^#\s*(?:Component|Object)(?:\s+number)?\s*:?\s*(\d+)", re.IGNORECASE)
CENTRAL_STRUCTURES = {"disk", "edgedisk", "bulge", "bar", "lens"}
# Chain membership: the four central types PLUS the outer envelope — the
# offset chain locks INPUT relative positions, so every main-galaxy member
# must be written concentric or the chain faithfully preserves scatter
# (OuterDisk is main-galaxy structure: an off-centre envelope at (143,140)
# vs galaxy centre (149,149) was observed in the KILOGAS_319 shakedown).
CHAIN_STRUCTURES = CENTRAL_STRUCTURES | {"outerdisk"}
_COMPANION_RE = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)
SHAPED_TYPES = {"sersic", "expdisk", "edgedisk", "ferrer", "gaussian", "moffat",
                "devauc", "king", "nuker"}


@dataclass
class Block:
    lines: list[str] = field(default_factory=list)
    name: str | None = None       # STRUCTURE name (lowercase), None for sky
    ctype: str | None = None
    is_sky: bool = False

    def row(self, key: str) -> tuple[int, str] | None:
        """Find a parameter row; key in {'1','3','4','5','9','10','Z'}."""
        for i, line in enumerate(self.lines):
            m = ROW_RE.match(line)
            if m and m.group(2) == key:
                return i, line
        return None


@dataclass
class TranscribeResult:
    ok: bool
    feedme: str = ""
    cons: str = ""
    notes: list[str] = field(default_factory=list)
    numbers: dict[str, int] = field(default_factory=dict)  # name -> GALFIT number


# ------------------------------------------------------------------- parsing
def split_feedme_ordered(text: str) -> tuple[list[str], list[Block]]:
    """Correct splitter: a block starts at its '# Component number:' comment (or
    at a bare '0)' line when the comment is absent); STRUCTURE comments inside
    the preamble belong to the block that follows them."""
    lines = text.splitlines()
    header: list[str] = []
    blocks: list[Block] = []
    current: Block | None = None
    for line in lines:
        starts_new = False
        if COMPONENT_NO_RE.match(line):
            starts_new = True
        elif ROW_RE.match(line) and ROW_RE.match(line).group(2) == "0":
            # a 0) row without a preceding '# Component number:' comment
            if current is None or any(ROW_RE.match(l) and ROW_RE.match(l).group(2) == "0"
                                      for l in current.lines):
                starts_new = True
        if starts_new:
            current = Block()
            blocks.append(current)
        if current is None:
            header.append(line)
        else:
            current.lines.append(line)
    # classify blocks
    for b in blocks:
        for line in b.lines:
            m = ROW_RE.match(line)
            if m and m.group(2) == "0":
                b.ctype = m.group(3).split("#")[0].strip().lower()
                b.is_sky = b.ctype == "sky"
                break
        if b.is_sky:
            b.name = None
        elif b.name is None:
            # STRUCTURE comment may sit at the block's head
            for line in b.lines:
                m = STRUCTURE_COMMENT_RE.match(line)
                if m:
                    b.name = m.group(1).lower()
                    break
    return header, blocks


# ------------------------------------------------------------ row rewriters
def _value_with_toggle(rest: str) -> tuple[list[str], list[str]]:
    toks = rest.split("#")[0].split()
    return toks, rest.split("#", 1)


def set_numeric_row(line: str, value: float) -> str:
    """Replace the FIRST numeric token, keep the rest (toggles, comments)."""
    m = ROW_RE.match(line)
    prefix, key, rest = m.group(1), m.group(2), m.group(3)
    body, comment = (rest.split("#", 1) + [""])[:2] if "#" in rest else (rest, "")
    toks = body.split()
    if not toks:
        return line
    toks[0] = _fmt_num(value)
    out = f"{prefix}{key}) " + "  ".join(toks)
    if comment:
        out += " #" + comment
    return out


def set_position_row(line: str, x: float, y: float) -> str:
    m = ROW_RE.match(line)
    prefix, key, rest = m.group(1), m.group(2), m.group(3)
    body, comment = (rest.split("#", 1) + [""])[:2] if "#" in rest else (rest, "")
    toks = body.split()
    toggles = toks[2:] if len(toks) >= 2 else []
    out = f"{prefix}{key}) {_fmt_num(x)}  {_fmt_num(y)}"
    if toggles:
        out += "  " + "  ".join(toggles)
    if comment:
        out += " #" + comment
    return out


def set_toggle(line: str, toggle: int) -> str:
    m = ROW_RE.match(line)
    prefix, key, rest = m.group(1), m.group(2), m.group(3)
    body, comment = (rest.split("#", 1) + [""])[:2] if "#" in rest else (rest, "")
    toks = body.split()
    if len(toks) >= 2:
        toks[1] = str(int(toggle))
    elif toks:
        toks.append(str(int(toggle)))
    out = f"{prefix}{key}) " + "  ".join(toks)
    if comment:
        out += " #" + comment
    return out


def _fmt_num(v: float) -> str:
    return f"{float(v):.4f}".rstrip("0").rstrip(".") if v == v else str(v)


def _row_values(line: str) -> list[float]:
    """Numeric tokens of a parameter row, excluding the row key ('4)' etc.)."""
    m = ROW_RE.match(line)
    rest = m.group(3) if m else line
    out: list[float] = []
    for tok in rest.split("#")[0].split():
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


# ------------------------------------------------------------------ backfill
def backfill_block(block: Block, converged: dict | None) -> None:
    """Warm-start: rewrite initial values with the parent converged values
    (toggles kept). ``converged`` is a parse_components-style dict."""
    if block.is_sky or converged is None:
        return
    for key, attr, setter in (("1", None, None), ("3", "mag", None),
                              ("4", "re", None), ("5", "n", None),
                              ("9", "ba", None), ("10", "pa", None)):
        found = block.row(key)
        if not found:
            continue
        idx, line = found
        if key == "1":
            if converged.get("x") is not None and converged.get("y") is not None:
                block.lines[idx] = set_position_row(line, converged["x"], converged["y"])
        else:
            v = converged.get(attr)
            if v is not None:
                block.lines[idx] = set_numeric_row(line, v)


# ------------------------------------------------------------------ cons write
def _region_side(fit_region) -> float | None:
    if not fit_region or len(fit_region) != 4:
        return None
    return max(fit_region[1] - fit_region[0] + 1, fit_region[3] - fit_region[2] + 1)


def build_cons(blocks: list[Block], numbers: dict[str, int], *,
               psf_fwhm_px: float | None, fit_region) -> tuple[str, list[str]]:
    """Compose the .cons text: concentric chain + default bound set only.

    Candidates declare INITIAL VALUES, never bands (KILOGAS_120 defect
    follow-up: self-imposed caps vetoed the best round — lens pinned at its
    candidate-declared [10,20]/[0.55,0.95] with BIC_eff 38900 — and forced
    relaxation candidates that crowded the queue). Any ``cons_bounds`` a
    candidate still carries is silently ignored: guiding happens through
    warm-start initial values, misbehaviour through the post-hoc mech checks.
    """
    notes: list[str] = []
    lines: list[str] = [
        "# Constraint file for the mechanised beam search (generated)",
        "# Absolute re/n/q bands use the 'to' keyword form (this GALFIT build",
        "# decodes bare two-number rows as INPUT-RELATIVE offsets).",
    ]
    luminous = [b for b in blocks if not b.is_sky]
    centrals = [b for b in luminous if b.name in CHAIN_STRUCTURES]

    # ---- concentric chain (K >= 2 main-galaxy members, mandatory)
    if len(centrals) >= 2:
        anchor = next((b for b in centrals if b.name in {"disk", "edgedisk"}), None)
        if anchor is None:  # brightest central component
            anchor = min(centrals, key=lambda b: _block_mag(b) or 99.0)
        chain_ids = [str(numbers[anchor.name])] + \
            [str(numbers[b.name]) for b in centrals if b is not anchor]
        chain = "_".join(chain_ids)
        lines.append(f"# Concentric constraint: anchor = {anchor.name} ({numbers[anchor.name]})")
        lines.append(f" {chain}   x   offset")
        lines.append(f" {chain}   y   offset")

    # ---- default bound set (every non-sky component; no candidate tightenings)
    re_floor = re_floor_px(psf_fwhm_px)  # max(0.1, 0.1 × PSF FWHM) — see cons_decode
    side = _region_side(fit_region)
    re_cap = 0.5 * side if side else 500.0

    for b in luminous:
        num = numbers.get(b.name)
        if num is None:
            continue
        is_exp = b.ctype == "expdisk"
        # re band (written in Rs for expdisk — the row bounds Rs)
        if b.row("4"):
            lo, hi = (re_floor / EXPDISK_FACTOR, re_cap / EXPDISK_FACTOR) if is_exp \
                else (re_floor, re_cap)
            lines.append(f" {num}   re   {lo:.4f} to {hi:.4f}"
                         + ("   # expdisk: band in Rs" if is_exp else ""))
        # n band (sersic-type components with an n row only)
        if b.row("5") and b.ctype in {"sersic", "ferrer", "devauc", "king", "nuker",
                                      "gaussian", "moffat"}:
            lines.append(f" {num}   n    0.1000 to 8.0000")
        # q band (shaped components with a 9) row)
        if b.row("9") and b.ctype in SHAPED_TYPES:
            lines.append(f" {num}   q    0.0500 to 1.0000")
        # centres: chain members are bound by the offset; others get windows
        in_chain = len(centrals) >= 2 and b in centrals
        if not in_chain and b.row("1"):
            if _COMPANION_RE.match(b.name or ""):
                lines.append(f" {num}   x    -5  5   # companion window (input-relative)")
                lines.append(f" {num}   y    -5  5")
            elif len(centrals) < 2:
                lines.append(f" {num}   x    -2  2")
                lines.append(f" {num}   y    -2  2")
    return "\n".join(lines) + "\n", notes


def _block_mag(block: Block) -> float | None:
    found = block.row("3")
    if not found:
        return None
    values = _row_values(found[1])
    return values[0] if values else None


# ------------------------------------------------------------------- blocks
def make_block(name: str, ctype: str, params: dict, toggles: dict[str, int],
               template_note: str = "") -> Block:
    """Create a component block from declared candidate parameters."""
    comment = template_note or f"# STRUCTURE: {name}"
    lines = [f"# STRUCTURE: {name}"]
    lines.append(f"0) {ctype}                    #  Component type")
    x, y = params.get("x_px"), params.get("y_px")
    tx = toggles.get("x", 1)
    ty = toggles.get("y", 1)
    lines.append(f"1) {_fmt_num(x if x is not None else 0.0)}  "
                 f"{_fmt_num(y if y is not None else 0.0)}  {tx} {ty}       #  Position x, y")
    lines.append(f"3) {_fmt_num(params.get('mag') if params.get('mag') is not None else 20.0)}"
                 f"        {toggles.get('mag', 1)}             #  Integrated magnitude")
    re_val = params.get("re_px")
    if ctype == "expdisk" and re_val is not None:
        re_val = re_val / EXPDISK_FACTOR
    if ctype != "psf":  # a psf block has NO re/n/q/PA rows (component spec)
        label = "R_s (disk scale-length)" if ctype == "expdisk" else "R_e (effective radius)"
        lines.append(f"4) {_fmt_num(re_val if re_val is not None else 5.0)}        "
                     f"{toggles.get('re', 1)}             #  {label}   [pix]")
        if ctype not in ("expdisk", "edgedisk"):
            n = params.get("n")
            lines.append(f"5) {_fmt_num(n if n is not None else 4.0)}        "
                         f"{toggles.get('n', 1)}             #  Sersic index n")
        lines.append("6) 0.0000      0             #  -----")
    lines.append("7) 0.0000      0             #  -----")
    lines.append("8) 0.0000      0             #  -----")
    if ctype != "psf":
        lines.append(f"9) {_fmt_num(params.get('q') if params.get('q') is not None else 0.8)}"
                     f"        {toggles.get('q', 1)}             #  Axis ratio (b/a)")
        lines.append(f"10) {_fmt_num(params.get('pa_deg') if params.get('pa_deg') is not None else 0.0)}"
                     f"        {toggles.get('pa', 1)}             #  Position angle (PA) [deg: Up=0, Left=90]")
    lines.append("Z) 0")
    f1 = params.get("f1")
    if f1:
        amp = f1.get("amplitude", 0.05)
        phase = f1.get("phase_deg", 0.0)
        lines.insert(-1, f"F1) {_fmt_num(amp)}  {_fmt_num(phase)}  1  1   #  Az. Fourier mode 1")
    return Block(lines=lines, name=name, ctype=ctype, is_sky=False)


def convert_block(block: Block, to_name: str, to_type: str) -> None:
    """singlesersic -> disk (expdisk): retype, rename, Rs = Re/1.68, drop n."""
    for i, line in enumerate(block.lines):
        m = ROW_RE.match(line)
        if m and m.group(2) == "0":
            block.lines[i] = f"{m.group(1)}0) {to_type}"
            block.ctype = to_type
            break
    for i, line in enumerate(block.lines):
        m = STRUCTURE_COMMENT_RE.match(line)
        if m:
            block.lines[i] = f"# STRUCTURE: {to_name}"
            break
    block.name = to_name
    if to_type == "expdisk":
        found = block.row("4")
        if found:
            idx, line = found
            values = _row_values(line)
            if values:
                new_line = set_numeric_row(line, values[0] / EXPDISK_FACTOR)
                block.lines[idx] = _retag_row_comment(new_line, "R_s (disk scale-length)")
        # drop the n row (expdisk has none)
        found = block.row("5")
        if found:
            del block.lines[found[0]]


def _retag_row_comment(line: str, new_comment: str) -> str:
    if "#" in line:
        return line.split("#", 1)[0].rstrip() + f"   #  {new_comment}   [pix]"
    return line


# ------------------------------------------------------------------ main
def transcribe(parent_feedme: str, galfit_nn: str, primitives: list[dict],
               out_feedme: str, out_cons: str, *,
               psf_fwhm_px: float | None = None,
               fit_region=None,
               default_mag_hint: float | None = None) -> TranscribeResult:
    """Apply primitives mechanically; write the child feedme + .cons."""
    notes: list[str] = []
    with open(parent_feedme, encoding="utf-8") as f:
        header, blocks = split_feedme_ordered(f.read())

    # converged values (by block order; galfit.NN drops STRUCTURE comments)
    converged_list: list[dict] = []
    if galfit_nn and os.path.exists(galfit_nn):
        from tools.parse_feedme import parse_components
        try:
            converged_list = parse_components(galfit_nn, name_file=parent_feedme)
        except Exception as e:
            notes.append(f"galfit.NN parse failed ({e}); warm-start skipped")
    luminous_blocks = [b for b in blocks if not b.is_sky]
    if len(converged_list) != len(luminous_blocks):
        notes.append(f"converged count {len(converged_list)} != luminous blocks "
                     f"{len(luminous_blocks)}; warm-start skipped")
        converged_list = [None] * len(luminous_blocks)
    converged_by_name = {}
    for b, conv in zip(luminous_blocks, converged_list):
        if b.name and conv:
            converged_by_name[b.name] = conv

    # warm-start backfill (sky untouched)
    for b in luminous_blocks:
        backfill_block(b, converged_by_name.get(b.name))

    # ---- apply primitives in order (Class-A verbatim; inapplicable -> abort)
    for p in primitives:
        op = p.get("op")
        if op == "remove":
            rm = p.get("remove")
            target = (rm if isinstance(rm, str) else None) or \
                (p.get("target") or p.get("structure_name") or "")
            target = target.lower()
            if not any(b.name == target and not b.is_sky for b in blocks):
                return TranscribeResult(False, notes=[f"remove target not found: {target}"])
            blocks = [b for b in blocks if not (b.name == target and not b.is_sky)]

        elif op == "add":
            entry = p.get("add") or p
            name = (entry.get("structure_name") or "").lower()
            if not name or any(b.name == name for b in blocks):
                return TranscribeResult(False, notes=[f"cannot add '{name}' (missing/duplicate)"])
            params = {k: v for k, v in entry.items()
                      if k in ("mag", "re_px", "n", "q", "pa_deg", "x_px", "y_px", "f1")}
            if entry.get("f1") and name not in {"disk", "edgedisk", "singlesersic"}:
                return TranscribeResult(False, notes=[f"F1 forbidden on '{name}'"])
            toggles = {k.lower(): int(v) for k, v in (entry.get("toggles") or {}).items()}
            # Class-B defaults: undeclared centre -> main-galaxy centre (disk)
            if params.get("x_px") is None or params.get("y_px") is None:
                centre = _main_centre(blocks)
                params.setdefault("x_px", centre[0])
                params.setdefault("y_px", centre[1])
                notes.append(f"{name}: centre defaulted to the main-galaxy centre")
            if params.get("mag") is None and default_mag_hint is not None:
                params["mag"] = default_mag_hint
                notes.append(f"{name}: mag defaulted to {default_mag_hint:.2f} (spec prior)")
            block = make_block(name, (entry.get("component_type") or "sersic").lower(),
                               params, toggles)
            blocks = _insert_before_sky(blocks, block)
            # candidate cons_bounds are deliberately ignored (initial values only)

        elif op == "tune":
            t = p.get("tune") if isinstance(p.get("tune"), dict) else p
            target = (t.get("structure_name") or t.get("target") or "").lower()
            block = next((b for b in blocks if b.name == target and not b.is_sky), None)
            if block is None:
                return TranscribeResult(False, notes=[f"tune target not found: {target}"])
            param = t.get("param")
            value = t.get("value")
            if param is not None and value is not None:
                key = {"mag": "3", "re_px": "4", "n": "5", "q": "9",
                       "pa_deg": "10", "x_px": "1", "y_px": "1"}.get(param)
                if key is None:
                    return TranscribeResult(False, notes=[f"unknown tune param: {param}"])
                if param in {"n", "q", "pa_deg"} and block.ctype == "psf":
                    return TranscribeResult(False, notes=[
                        f"psf component has no {param}"])
                if param == "n" and not block.row("5"):
                    return TranscribeResult(False, notes=[
                        f"{target} has no n row to tune"])
                if param == "re_px":
                    v = value / EXPDISK_FACTOR if block.ctype == "expdisk" else value
                else:
                    v = value
                if param in {"x_px", "y_px"}:
                    found = block.row("1")
                    values = _row_values(found[1])
                    x, y = (values + [0.0, 0.0])[:2]
                    if param == "x_px":
                        x = value
                    else:
                        y = value
                    block.lines[found[0]] = set_position_row(found[1], x, y)
                else:
                    found = block.row(key)
                    if not found:
                        return TranscribeResult(False, notes=[
                            f"{target} has no row {key} for {param}"])
                    block.lines[found[0]] = set_numeric_row(found[1], v)
            if t.get("toggle") is not None and param is not None:
                key = {"mag": "3", "re_px": "4", "n": "5", "q": "9",
                       "pa_deg": "10"}.get(param)
                if key:
                    found = block.row(key)
                    if found:
                        block.lines[found[0]] = set_toggle(found[1], int(t["toggle"]))
            # candidate cons_bounds are deliberately ignored (initial values only)

        elif op == "convert":
            cv = p.get("convert") or {}
            frm = (cv.get("from_name") or "").lower()
            to = (cv.get("to_name") or "").lower()
            block = next((b for b in blocks if b.name == frm), None)
            if block is None:
                return TranscribeResult(False, notes=[f"convert source not found: {frm}"])
            convert_block(block, to or frm, (cv.get("to_type") or block.ctype).lower())

        else:
            return TranscribeResult(False, notes=[f"unknown op: {op}"])

    # ---- concentric normalisation: the offset chain locks INPUT relative
    # positions, so every chain member must be written at the anchor's centre
    # (warm-start scatter inherited from pre-chain rounds would otherwise be
    # faithfully preserved forever)
    _normalize_chain_centres(blocks)

    # ---- renumber and compose
    numbers: dict[str, int] = {}
    out_blocks: list[str] = []
    num = 0
    for b in blocks:
        num += 1
        if b.name and not b.is_sky:
            numbers[b.name] = num
        text = _renumber_block(b, num)
        out_blocks.append(text)

    # header: point G) at the cons file (same dir + basename)
    cons_name = os.path.basename(out_cons)
    header = [_point_g_at(line, cons_name) for line in header]

    feedme_text = "\n".join(header).rstrip("\n") + "\n\n" + \
        "\n\n".join(b.strip("\n") for b in out_blocks) + "\n"

    cons_text, cons_notes = build_cons(
        blocks, numbers, psf_fwhm_px=psf_fwhm_px, fit_region=fit_region)
    notes.extend(cons_notes)

    os.makedirs(os.path.dirname(os.path.abspath(out_feedme)), exist_ok=True)
    with open(out_feedme, "w", encoding="utf-8") as f:
        f.write(feedme_text)
    with open(out_cons, "w", encoding="utf-8") as f:
        f.write(cons_text)
    return TranscribeResult(True, feedme=out_feedme, cons=out_cons,
                            notes=notes, numbers=numbers)


def _normalize_chain_centres(blocks: list[Block]) -> None:
    """Rewrite every chain member's `1)` row at the anchor's centre.

    The GALFIT `offset` chain freezes the members' RELATIVE input positions;
    concentricity therefore must be established on the input side. Anchor =
    disk/edgedisk if present, else the brightest main-galaxy member. Only
    fires when >= 2 CHAIN_STRUCTURES members exist (the chain itself is
    written by build_cons under the same condition).
    """
    members = [b for b in blocks if not b.is_sky and b.name in CHAIN_STRUCTURES]
    if len(members) < 2:
        return
    anchor = next((b for b in members if b.name in {"disk", "edgedisk"}), None)
    if anchor is None:
        anchor = min(members, key=lambda b: _block_mag(b) or 99.0)
    found = anchor.row("1")
    if not found:
        return
    values = _row_values(found[1])
    if len(values) < 2:
        return
    ax, ay = values[0], values[1]
    for b in members:
        if b is anchor:
            continue
        row = b.row("1")
        if not row:
            continue
        b.lines[row[0]] = set_position_row(row[1], ax, ay)


def _main_centre(blocks) -> tuple[float, float]:
    for b in blocks:
        if b.name in {"disk", "edgedisk", "singlesersic"} and not b.is_sky:
            found = b.row("1")
            if found:
                values = _row_values(found[1])
                if len(values) >= 2:
                    return values[0], values[1]
    return (0.0, 0.0)


def _insert_before_sky(blocks: list[Block], block: Block) -> list[Block]:
    sky_idx = next((i for i, b in enumerate(blocks) if b.is_sky), None)
    if sky_idx is None:
        return blocks + [block]
    return blocks[:sky_idx] + [block] + blocks[sky_idx:]


def _renumber_block(block: Block, num: int) -> str:
    lines = []
    renumbered = False
    for line in block.lines:
        if not renumbered:
            m = COMPONENT_NO_RE.match(line)
            if m:
                lines.append(f"# Component number: {num}")
                renumbered = True
                continue
            rm = ROW_RE.match(line)
            if rm and rm.group(2) == "0":
                lines.insert(0 if not any(COMPONENT_NO_RE.match(l) for l in lines)
                             else len(lines),
                             f"# Component number: {num}")
                lines.append(line)
                renumbered = True
                continue
        lines.append(line)
    if not renumbered:
        lines.insert(0, f"# Component number: {num}")
    return "\n".join(lines)


def _point_g_at(line: str, cons_name: str) -> str:
    stripped = line.split("#", 1)[0].strip()
    if stripped.startswith("G)"):
        return f"G) {cons_name}          # File with parameter constraints"
    return line
