"""Structured lightweight .lyric document model for the multi-band engine.

Why this module exists (docs/design_mb_beam.md §1): the GalfitS-native
``read_config_file`` builds model objects — it imports jax/astropy/
matplotlib, is slow, and its outputs are not plain structured data. The
beam engine needs *data*, not models: the doc model below reuses the
parsing rules already proven by ``tools.modify_lyric.check_lyric_file``
(linear tokenizer, ``ast.literal_eval`` value parsing with plain-string
fallback, file order preserved) and emits structured blocks.

Layering contract:

* **Tokenizer layer** — mirrors ``check_lyric_file`` exactly (same key
  decomposition regex, same inline-comment stripping, same literal-eval
  fallback). ``check_lyric_file`` stays the *validation* gate (five-tuple
  semantics, identifier lint, grouping typos); this module is the
  *emitter*. Malformed input raises :class:`LyricError` — callers parse
  files that already passed the gate.
* **Document layer** — pure dataclasses. Block references are joined by
  the section **prefix label** letters, two independent label spaces:
  ``Aa2`` lists **I-block labels** (which bands the atlas assembles),
  ``Ga2`` lists **P-block labels** (which components the galaxy is made
  of), and an N block belongs to the G block with the **same prefix**
  (``Ga``/``Na``; the ``Na1`` *value* is the nucleus NAME used for lmfit
  parameter building, not the linkage).
* **Strict invariants** (stricter than GalfitS, which silently tolerates
  defined-but-unreferenced blocks; the pathology is real —
  kilogas_mb KILOGAS_110 ``_iter7`` carries three I blocks but
  ``Aa2) ['a']``):
  1. every I label is referenced by ``Aa2`` exactly once;
  2. every P label is referenced by exactly one ``Ga2`` (no dangling
     component, no component shared across galaxies);
  3. every N block's prefix label matches some G block's prefix label.
* **Serialization** is the inverse: unmodified lines are written back
  byte-identically (the original raw text is kept); only rows explicitly
  modified are re-rendered. ``parse -> serialize`` round-trips the file
  unchanged, which the golden tests assert.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "LyricError", "Row", "Block", "LyricDoc",
    "TYPE_ROW_MAP", "SECTION_SCHEMA", "parse_lyric_doc",
]

# ---------------------------------------------------------------------------
# section family schema (mirrors modify_lyric.LYRIC_SECTION_SCHEMA)
# family -> (repeatable (labelled blocks), max row index)
SECTION_SCHEMA = {
    'R': (False, 3),    # Region: R1 name, R2 [ra, dec], R3 scale
    'I': (True, 15),    # Image band: Ix1 image ... Ix15 use-SED
    'S': (True, 5),     # Spectrum: Sx1 file ... Sx5 lsf (optional)
    'A': (True, 7),     # Atlas: Ax1 name ... Ax7 reference images
    'N': (True, 27),    # Nuclei/AGN: Nx1 ... Nx27
    'P': (True, 36),    # Profile: Px1 name, Px2 type ... Px36 (33-36: zezhong dust/nebular)
    'G': (True, 7),     # Galaxy: Gx1 name ... Gx7
    'F': (True, 8),     # Foreground star: Fx1 name ... Fx8
}

# ---------------------------------------------------------------------------
# Per-type semantic row map (design doc §1.4; semantics verified against
# GalfitS/src/galfits/gsutils.py:1238+). Rows hold DIFFERENT quantities per
# profile type — P5 is Re for sersic/sersic_f but Rout for ferrer and rs for
# edgeondisk; edgeondisk has no P8. Accessors dispatch through this table.
#
# slot -> row index. All types additionally share the SED rows P9-P16 and
# P26-P32 (carried verbatim; not slot-mapped).
_FOURIER_ROWS = {'r_in': 17, 'r_out': 18, 'alpha_rc': 19, 'theta_out': 20,
                 'm': 21, 'am': 22, 'theta_m': 23, 'i_arm': 24}
TYPE_ROW_MAP: dict[str, dict[str, int]] = {
    'sersic':     {'re': 5, 'n': 6, 'pa': 7, 'q': 8},
    'sersic_f':   {'re': 5, 'n': 6, 'pa': 7, 'q': 8, **_FOURIER_ROWS},
    'sersic_rf':  {'re': 5, 'n': 6, 'pa': 7, 'q': 8, **_FOURIER_ROWS},
    'sersic_bf':  {'re': 5, 'n': 6, 'pa': 7, 'q': 8, **_FOURIER_ROWS},
    'ferrer':     {'r_out': 5, 'alpha': 6, 'pa': 7, 'q': 8, 'beta': 25},
    'edgeondisk': {'rs': 5, 'hs': 6, 'pa': 7},
    'GauRing':    {'r0': 5, 'sig': 6, 'pa': 7, 'q': 8},
    'Gaussian':   {'re': 5, 'pa': 7, 'q': 8},   # width/PA/q; P6 unused
}
# universal rows shared by every profile type (centre offsets, arcsec)
for _m in TYPE_ROW_MAP.values():
    _m.update({'xcen': 3, 'ycen': 4})

# extra rows REQUIRED by type beyond the common P1-P8 core: a sersic_f block
# missing P17-P24 key-errors inside GalfitS at load time — caught statically
# here instead (missing_type_rows()).
REQUIRED_TYPE_ROWS: dict[str, list[int]] = {
    'sersic_f':  list(range(17, 25)),
    'sersic_rf': list(range(17, 25)),
    'sersic_bf': list(range(17, 25)),
    'ferrer':    [25],
}


class LyricError(ValueError):
    """Structural lyric error (malformed line, unknown family, bad grouping)."""


# ---------------------------------------------------------------------------
# tokenizer

_KEY_RE = re.compile(r'^([A-Z])([A-Za-z]*)(\d+)$')


@dataclass
class Row:
    """One keyed line. ``raw`` preserves the original text so that
    serialization is byte-stable for unmodified rows."""
    lineno: int
    key: str            # full key incl. ')', e.g. 'Pa5)'
    family: str         # 'P'
    label: str          # 'a' (prefix letters; '' for R)
    index: int          # 5
    value: Any          # parsed value (literal or plain string)
    is_literal: bool    # True when ast.literal_eval parsed the value
    raw: str            # original line text (no trailing newline)
    dirty: bool = False

    def render(self) -> str:
        if not self.dirty:
            return self.raw
        if self.is_literal:
            return f"{self.key} {self.value!r}"
        return f"{self.key} {self.value}"


@dataclass
class Block:
    """One labelled section block (I/P/N/G/S/A/F) or the single R block."""
    family: str
    label: str
    rows: dict[int, Row] = field(default_factory=dict)

    # -- raw row access ------------------------------------------------
    def row(self, index: int) -> Optional[Row]:
        return self.rows.get(index)

    def value(self, index: int, default: Any = None) -> Any:
        r = self.rows.get(index)
        return default if r is None else r.value

    def set_value(self, index: int, value: Any) -> None:
        """Modify a row's value in place (marks it dirty for re-render)."""
        r = self.rows.get(index)
        if r is None:
            raise LyricError(
                f"{self.family}{self.label}{index}) does not exist — cannot set")
        r.value = value
        r.dirty = True

    # -- P-block typed accessors (dispatch via TYPE_ROW_MAP) -----------
    @property
    def ptype(self) -> str:
        return str(self.value(2, ''))

    @property
    def name(self) -> str:
        return str(self.value(1, ''))

    def slot_row(self, slot: str) -> Optional[Row]:
        m = TYPE_ROW_MAP.get(self.ptype)
        if not m or slot not in m:
            return None
        return self.rows.get(m[slot])

    def slot_value(self, slot: str, default: Any = None) -> Any:
        r = self.slot_row(slot)
        return default if r is None else r.value

    def slot_five(self, slot: str) -> Optional[tuple]:
        """(init, min, max, step, flag) of a slot whose row is a 5-tuple."""
        v = self.slot_value(slot)
        if isinstance(v, (list, tuple)) and len(v) == 5:
            return tuple(v)
        return None

    def missing_type_rows(self) -> list[int]:
        req = REQUIRED_TYPE_ROWS.get(self.ptype, [])
        return [i for i in req if i not in self.rows]


# ---------------------------------------------------------------------------
# document

@dataclass
class LyricDoc:
    """Structured view of one .lyric file."""
    lines: list[Any] = field(default_factory=list)   # Row | raw str, in order

    region: Block = None                              # type: ignore
    images: dict[str, Block] = field(default_factory=dict)      # I: label -> block
    spectra: dict[str, Block] = field(default_factory=dict)     # S
    atlases: dict[str, Block] = field(default_factory=dict)     # A
    components: list[Block] = field(default_factory=list)       # P, file order
    nuclei: dict[str, Block] = field(default_factory=dict)      # N: prefix label
    galaxies: dict[str, Block] = field(default_factory=dict)    # G: prefix label
    foreground_stars: dict[str, Block] = field(default_factory=dict)  # F

    # -- lookups --------------------------------------------------------
    def component_by_label(self, label: str) -> Optional[Block]:
        for c in self.components:
            if c.label == label:
                return c
        return None

    def component(self, name: str) -> Optional[Block]:
        for c in self.components:
            if c.name == name:
                return c
        return None

    @property
    def atlas(self) -> Optional[Block]:
        return next(iter(self.atlases.values()), None)

    def band_of_label(self, label: str) -> Optional[str]:
        b = self.images.get(label)
        return None if b is None else b.value(2)

    def label_of_band(self, band: str) -> Optional[str]:
        for lbl, b in self.images.items():
            if b.value(2) == band:
                return lbl
        return None

    @property
    def band_names(self) -> list[str]:
        """Assembled bands, in Aa2 order (the fitting band set)."""
        a = self.atlas
        if a is None:
            return []
        return [self.band_of_label(lbl) or lbl for lbl in (a.value(2) or [])]

    @property
    def center(self) -> Optional[tuple]:
        v = self.region.value(2) if self.region else None
        return tuple(v) if isinstance(v, (list, tuple)) and len(v) == 2 else None

    # -- strict invariants (design doc §1.3) ----------------------------
    def strict_invariant_errors(self) -> list[str]:
        errs: list[str] = []

        # (1) I labels vs Aa2 references (each exactly once)
        a = self.atlas
        if a is not None:
            refs = a.value(2) or []
        else:
            refs = []
            errs.append("no A block: Aa2 cannot be checked (missing atlas)")
        counts: dict[str, int] = {}
        for r in refs:
            counts[r] = counts.get(r, 0) + 1
        for lbl in counts:
            if lbl not in self.images:
                errs.append(f"Aa2 references unknown I label {lbl!r}")
        for lbl in self.images:
            n = counts.get(lbl, 0)
            if n == 0:
                errs.append(
                    f"dangling I block '{lbl}' (band {self.band_of_label(lbl)!r}): "
                    f"defined but never referenced by Aa2 {refs!r}")
            elif n > 1:
                errs.append(f"Aa2 references I label {lbl!r} {n} times")

        # (2) P labels vs Ga2 membership (each exactly one galaxy)
        owner: dict[str, list[str]] = {}
        for glbl, g in self.galaxies.items():
            for plbl in (g.value(2) or []):
                owner.setdefault(plbl, []).append(glbl)
        for plbl, glbls in owner.items():
            if self.component_by_label(plbl) is None:
                errs.append(f"G{glbls[0]}2 references unknown P label {plbl!r}")
        for c in self.components:
            glbls = owner.get(c.label, [])
            if not glbls:
                errs.append(
                    f"dangling P block '{c.label}' (component {c.name!r}): "
                    f"defined but referenced by no Ga2")
            elif len(glbls) > 1:
                errs.append(
                    f"P label '{c.label}' referenced by galaxies {glbls} "
                    f"(a component may belong to exactly one galaxy)")

        # (3) N blocks attach to a G block by PREFIX label
        for nlbl in self.nuclei:
            if nlbl not in self.galaxies:
                errs.append(
                    f"dangling N block '{nlbl}': no G block shares its label "
                    f"(AGN must attach to a galaxy via the Ga/Na prefix)")

        # (4) per-type required rows (load-time KeyError prevention)
        for c in self.components:
            for i in c.missing_type_rows():
                errs.append(
                    f"component {c.name!r} ({c.ptype}) is missing required "
                    f"row P{c.label}{i})")
        return errs

    # -- serialization ---------------------------------------------------
    def serialize(self) -> str:
        out = []
        for ln in self.lines:
            out.append(ln.render() if isinstance(ln, Row) else ln)
        return "\n".join(out) + "\n"


def parse_lyric_doc(path_or_text: str) -> LyricDoc:
    """Parse a .lyric file (path) or raw text into a LyricDoc.

    Raises LyricError on malformed structure (duplicate keys, unknown
    families, broken block grouping). Five-tuple semantics and identifier
    lint remain check_lyric_file's job — run the gate first on files from
    outside the engine.
    """
    if os.path.exists(path_or_text):
        with open(path_or_text, 'r') as f:
            lines = f.readlines()
    else:
        lines = [l + "\n" for l in path_or_text.splitlines()]
        if not path_or_text.strip():
            lines = []

    doc = LyricDoc()
    current: dict[str, Block] = {}   # family -> open block
    seen_keys: dict[str, int] = {}

    for lineno, rawline in enumerate(lines, start=1):
        raw = rawline.rstrip("\n")
        line = raw.split('#', 1)[0].strip()
        if not line:
            doc.lines.append(raw)
            continue
        if ')' not in line:
            raise LyricError(f"line {lineno}: missing ')' separator: {raw!r}")
        key_part, _, value_str = line.partition(')')
        key_part = key_part.strip()
        value_str = value_str.strip()
        dm = _KEY_RE.match(key_part)
        if not dm:
            raise LyricError(f"line {lineno}: malformed key {key_part!r}")
        family, label, index = dm.group(1), dm.group(2), int(dm.group(3))
        if family not in SECTION_SCHEMA:
            raise LyricError(f"line {lineno}: unknown section family {family!r}")
        repeatable, max_index = SECTION_SCHEMA[family]
        if index < 1 or index > max_index:
            raise LyricError(
                f"line {lineno}: index {index} out of range 1..{max_index} "
                f"for family {family}")

        key = key_part + ')'
        seen_keys[key] = seen_keys.get(key, 0) + 1

        try:
            value = ast.literal_eval(value_str)
            is_literal = True
        except (ValueError, SyntaxError):
            value = value_str
            is_literal = False
        row = Row(lineno, key, family, label, index, value, is_literal, raw)

        # block grouping: {F}{label}1 opens the block; subsequent
        # {F}{label}{n>1} must land in it (mirrors check_lyric_file)
        if not repeatable:
            if family == 'R':
                if doc.region is None:
                    doc.region = Block('R', '')
                doc.region.rows[index] = row
        else:
            if not label:
                raise LyricError(
                    f"line {lineno}: {key} is missing its label letter "
                    f"(expected e.g. {family}a{index})")
            if index == 1:
                blk = Block(family, label)
                blk.rows[index] = row
                current[family] = blk
                _register_block(doc, blk)
            else:
                blk = current.get(family)
                if blk is None:
                    raise LyricError(
                        f"line {lineno}: {key} appears before any {family}*) "
                        f"declaration (missing {family}{label}1?)")
                if blk.label != label:
                    raise LyricError(
                        f"line {lineno}: {key} sits inside {family}-block "
                        f"'{blk.label}' but uses label '{label}' "
                        f"(expected {family}{blk.label}{index})")
                blk.rows[index] = row
        doc.lines.append(row)

    for key, n in seen_keys.items():
        if n > 1:
            raise LyricError(f"duplicate key {key!r} appears {n} times")
    if doc.region is None:
        raise LyricError("missing required R block (R1/R2/R3)")
    return doc


def _register_block(doc: LyricDoc, blk: Block) -> None:
    if blk.family == 'I':
        doc.images[blk.label] = blk
    elif blk.family == 'S':
        doc.spectra[blk.label] = blk
    elif blk.family == 'A':
        doc.atlases[blk.label] = blk
    elif blk.family == 'P':
        doc.components.append(blk)
    elif blk.family == 'N':
        doc.nuclei[blk.label] = blk
    elif blk.family == 'G':
        doc.galaxies[blk.label] = blk
    elif blk.family == 'F':
        doc.foreground_stars[blk.label] = blk
