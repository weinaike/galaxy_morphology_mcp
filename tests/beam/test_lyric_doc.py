"""Tests for beam_mb.lyric_doc — the multi-band .lyric document model.

Fixtures are real kilogas_mb files (old agent-driven workflow products):
  mb_root.lyric             KILOGAS_110 root config (single obj0 sersic)
  mb_iter4.lyric            KILOGAS_110 _iter4 (disk/bulge/bar, 3 bands)
  mb_sersic_f.lyric         KILOGAS_216 _iter11 (disk = sersic_f, P17-P24)
  mb_agn_dangling_band.lyric KILOGAS_110 _iter7 (N-block AGN + the
                            Aa2=['a'] single-band pathology — three I
                            blocks defined, only one assembled)
"""

import pytest

from beam_mb.lyric_doc import (
    LyricError, TYPE_ROW_MAP, parse_lyric_doc,
)

FIX = {
    "root": "mb/mb_root.lyric",
    "iter4": "mb/mb_iter4.lyric",
    "sersic_f": "mb/mb_sersic_f.lyric",
    "agn_dangling": "mb/mb_agn_dangling_band.lyric",
}


def _load(test_data_dir, key):
    return parse_lyric_doc(str(test_data_dir / FIX[key]))


# ------------------------------------------------------------------ round-trip
@pytest.mark.parametrize("key", list(FIX))
def test_roundtrip_byte_identical(test_data_dir, key):
    """parse -> serialize reproduces the file byte-for-byte (unmodified
    rows keep their original raw text)."""
    path = test_data_dir / FIX[key]
    original = path.read_text()
    doc = parse_lyric_doc(str(path))
    assert doc.serialize() == original


# ------------------------------------------------------------------ structure
def test_root_structure(test_data_dir):
    doc = _load(test_data_dir, "root")
    assert [c.name for c in doc.components] == ["obj0"]
    assert doc.components[0].ptype == "sersic"
    assert doc.band_names == ["decam_g", "decam_r", "decam_z"]
    assert doc.center == (326.27075, -7.40769)
    assert list(doc.galaxies) == ["a"]
    assert doc.galaxies["a"].value(2) == ["a"]
    assert not doc.nuclei


def test_iter4_structure(test_data_dir):
    doc = _load(test_data_dir, "iter4")
    # components in FILE order, names from P1
    assert [(c.label, c.name, c.ptype) for c in doc.components] == [
        ("a", "disk", "sersic"), ("b", "bulge", "sersic"), ("c", "bar", "sersic")]
    # disk is sersic with n=1 FIXED (the GalfitS disk convention)
    disk = doc.component("disk")
    assert disk.slot_five("n") == (1.0, 0.5, 2.0, 0.1, 0)
    assert disk.slot_five("re") == (6.2057, 0.08, 90, 0.08, 1)
    assert disk.slot_five("pa") == (-54.6316, -180, 180, 1, 1)
    assert disk.slot_five("q") == (0.4967, 0.1, 1, 0.02, 1)
    # bar n=0.5 (initial value = five-tuple[0]) with a non-degenerate band
    bar = doc.component("bar")
    assert bar.slot_five("n") == (0.5, 0.4, 0.6, 0.1, 0)


def test_sersic_f_rows(test_data_dir):
    doc = _load(test_data_dir, "sersic_f")
    disk = doc.component("disk")
    assert disk.ptype == "sersic_f"
    # the eight Fourier rows P17-P24
    assert disk.slot_five("r_in") == (3.137, 0.3, 4.0, 0.1, 1)
    assert disk.slot_five("r_out") == (8.0103, 3.0, 20.0, 0.1, 1)
    assert disk.slot_five("am") == (0.0905, 0.0, 0.5, 0.01, 1)
    assert disk.slot_five("theta_m") == (128.41, 0, 180, 1, 1)
    assert disk.slot_five("i_arm") == (55.32, 0, 90, 1, 1)
    # P21 m is a PLAIN VALUE (not a five-tuple) — GalfitS hard-fixes it
    assert disk.slot_value("m") == 1
    assert disk.slot_five("m") is None
    # P25 stays absent (ferrer's beta slot)
    assert disk.row(25) is None
    assert disk.missing_type_rows() == []
    # the other components are plain sersic and must not expose Fourier slots
    assert doc.component("bar").slot_row("am") is None


def test_type_row_map_registry():
    assert TYPE_ROW_MAP["sersic"]["re"] == 5
    assert TYPE_ROW_MAP["ferrer"]["r_out"] == 5 and "re" not in TYPE_ROW_MAP["ferrer"]
    assert "q" not in TYPE_ROW_MAP["edgeondisk"]


# ------------------------------------------------------------------ invariants
def test_invariants_pass_on_clean_files(test_data_dir):
    for key in ("root", "iter4", "sersic_f"):
        assert _load(test_data_dir, key).strict_invariant_errors() == [], key


def test_invariant_dangling_band_real_pathology(test_data_dir):
    """KILOGAS_110 _iter7: three I blocks defined, Aa2 assembles only 'a' —
    exactly the silent-band-drop the strict invariant forbids."""
    doc = _load(test_data_dir, "agn_dangling")
    errs = doc.strict_invariant_errors()
    assert any("dangling I block 'b'" in e and "decam_r" in e for e in errs)
    assert any("dangling I block 'c'" in e for e in errs)
    # the N block itself attaches legally (Ga/Na share the prefix)
    assert not any("N block" in e for e in errs)
    assert list(doc.nuclei) == ["a"]
    assert doc.nuclei["a"].value(1) == "AGN"   # Na1 value is the NAME


def test_invariant_dangling_component(test_data_dir):
    doc = _load(test_data_dir, "iter4")
    doc.galaxies["a"].set_value(2, ["a", "b"])     # drop 'c' (bar) from Ga2
    errs = doc.strict_invariant_errors()
    assert any("dangling P block 'c'" in e and "bar" in e for e in errs)


def test_invariant_shared_component(test_data_dir):
    doc = _load(test_data_dir, "root")
    # forge a second galaxy referencing the same P label
    from beam_mb.lyric_doc import Block, Row
    fake = Block("G", "b")
    fake.rows[1] = Row(0, "Gb1)", "G", "b", 1, "b", True, "Gb1) b")
    fake.rows[2] = Row(0, "Gb2)", "G", "b", 2, ["a"], True, "Gb2) ['a']")
    doc.galaxies["b"] = fake
    errs = doc.strict_invariant_errors()
    assert any("referenced by galaxies ['a', 'b']" in e for e in errs)


def test_invariant_unknown_references(test_data_dir):
    doc = _load(test_data_dir, "iter4")
    doc.atlas.set_value(2, ["a", "b", "c", "z"])   # Aa2 -> unknown I label
    errs = doc.strict_invariant_errors()
    assert any("unknown I label 'z'" in e for e in errs)
    doc2 = _load(test_data_dir, "iter4")
    doc2.galaxies["a"].set_value(2, ["a", "b", "x"])  # Ga2 -> unknown P label
    errs2 = doc2.strict_invariant_errors()
    assert any("unknown P label 'x'" in e for e in errs2)


def test_invariant_duplicate_band_reference(test_data_dir):
    doc = _load(test_data_dir, "iter4")
    doc.atlas.set_value(2, ["a", "a", "b", "c"])
    errs = doc.strict_invariant_errors()
    assert any("references I label 'a' 2 times" in e for e in errs)


def test_invariant_dangling_nucleus(test_data_dir):
    doc = _load(test_data_dir, "root")
    from beam_mb.lyric_doc import Block, Row
    fake = Block("N", "q")
    fake.rows[1] = Row(0, "Nq1)", "N", "q", 1, "AGN", True, "Nq1) 'AGN'")
    doc.nuclei["q"] = fake
    errs = doc.strict_invariant_errors()
    assert any("dangling N block 'q'" in e for e in errs)


def test_missing_sersic_f_rows(test_data_dir):
    doc = _load(test_data_dir, "sersic_f")
    disk = doc.component("disk")
    del disk.rows[19]                              # rip out Pa19 (alpha_rc)
    assert disk.missing_type_rows() == [19]
    errs = doc.strict_invariant_errors()
    assert any("missing required row Pa19" in e for e in errs)


# ------------------------------------------------------------------ edits
def test_dirty_row_rerender_and_reparse(test_data_dir):
    doc = _load(test_data_dir, "iter4")
    disk = doc.component("disk")
    disk.set_value(5, [7.5, 0.08, 90, 0.08, 1])
    text = doc.serialize()
    # the modified line re-renders; everything else untouched
    assert "Pa5) [7.5, 0.08, 90, 0.08, 1]" in text
    doc2 = parse_lyric_doc(text)
    assert doc2.component("disk").slot_five("re") == (7.5, 0.08, 90, 0.08, 1)
    assert doc2.component("bulge").slot_five("re") == (0.3144, 0.131, 0.524, 0.08, 1)
    assert doc2.strict_invariant_errors() == []


# ------------------------------------------------------------------ malformed
def test_malformed_inputs_raise(test_data_dir):
    with pytest.raises(LyricError, match="malformed key"):
        parse_lyric_doc("R1) ok\nPAX) bad\n")
    with pytest.raises(LyricError, match="missing '\\)' separator"):
        parse_lyric_doc("R1) ok\nnot a lyric line\n")
    with pytest.raises(LyricError, match="before any P"):
        parse_lyric_doc("R1) x\nR2) [1, 2]\nR3) 0.1\nPa2) sersic\n")
    with pytest.raises(LyricError, match="duplicate key"):
        parse_lyric_doc("R1) x\nR1) y\nR2) [1, 2]\nR3) 0.1\n")
    with pytest.raises(LyricError, match="missing required R block"):
        parse_lyric_doc("Pa1) disk\nPa2) sersic\n")
    # inline comments are stripped for parsing, raw preserved on serialize
    doc = parse_lyric_doc("R1) x\nR2) [1, 2]\nR3) 0.1\n# header\n")
    assert doc.serialize() == "R1) x\nR2) [1, 2]\nR3) 0.1\n# header\n"
