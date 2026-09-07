"""Tests for src/beam/cons_decode.py (.cons effective-band decoding + bound-hit scan)."""

from beam.cons_decode import (
    BoundContext,
    decode_cons_file,
    effective_band,
    number_components,
    scan_bound_hits,
)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- decode_cons_file
def test_decode_forms(tmp_path):
    cons = _write(
        tmp_path,
        "iter1.cons",
        "\n".join(
            [
                "# concentric constraint",
                "1_2_3_4     x     offset",
                "1_2_3_4     y     offset",
                "  2         n     0.5 to 8",
                "  3         re    4.0  to 13.5",
                "  5         x     -5    5",
                "  5         y     -5    5",
                "  3         q     0.05 to 1.0",
                "  3-7       mag   -0.5  3",
                "  1_5_3_2   re    ratio",
            ]
        ),
    )
    d = decode_cons_file(cons)
    forms = {(r.comp_spec, r.param): r.form for r in d.rows}
    assert forms[("1_2_3_4", "x")] == "offset"
    assert forms[("2", "n")] == "abs"
    assert forms[("3", "re")] == "abs"
    assert forms[("5", "x")] == "rel"
    assert forms[("3-7", "mag")] == "rel_pair"
    assert forms[("1_5_3_2", "re")] == "ratio"
    assert d.chains == {"1_2_3_4": {"x", "y"}}
    # rel_pair / ratio / operator rows never carry numeric bands
    numeric = {(r.comp_spec, r.param) for r in d.numeric_rows()}
    assert ("3-7", "mag") not in numeric
    assert ("1_2_3_2", "re") not in numeric and ("1_5_3_2", "re") not in numeric


def test_decode_missing_chain_pair_warns(tmp_path):
    cons = _write(tmp_path, "bad.cons", "1_2   x   offset\n")
    d = decode_cons_file(cons)
    assert any("binds only" in w for w in d.warnings)


def test_decode_missing_file():
    d = decode_cons_file("")
    assert d.rows == []
    assert d.warnings


# --------------------------------------------------------------- effective_band
def test_effective_band_relative_semantics():
    """The two documented cases fix the decoding rule [input-|a|, input+|b|]."""
    from beam.cons_decode import ConsRow

    # KILOGAS_432 incident: '2 n 0.5 8' with n_init=4 -> [3.5, 12.0]
    row = ConsRow("2", "n", "rel", a=0.5, b=8)
    assert effective_band(row, 4.0) == (3.5, 12.0)
    # '2 x -1 0.5' -> [input-1, input+0.5]
    row = ConsRow("2", "x", "rel", a=-1.0, b=0.5)
    assert effective_band(row, 100.0) == (99.0, 100.5)
    # companion window '5 x -5 5' -> [input-5, input+5]
    row = ConsRow("5", "x", "rel", a=-5.0, b=5.0)
    assert effective_band(row, 130.0) == (125.0, 135.0)
    # absolute rows are passed through
    row = ConsRow("2", "n", "abs", a=0.5, b=8)
    assert effective_band(row, 4.0) == (0.5, 8.0)


# ------------------------------------------------------------ number_components
def test_number_components_ngc1097(test_data_dir):
    comps = number_components(str(test_data_dir / "NGC1097.feedme"))
    # 4 luminous blocks (bulge/disk/bar/agn), no sky -> numbers 1..4
    assert sorted(comps.keys()) == [1, 2, 3, 4]
    assert comps[1]["name"].lower() == "bulge"
    assert comps[2]["type"] == "expdisk"
    assert comps[3]["name"].lower() == "bar"
    assert comps[4]["type"] == "psf"


def test_number_components_sky_consumes_slot(tmp_path):
    feedme = _write(
        tmp_path,
        "sky.feedme",
        "\n".join(
            [
                "A) img.fits",
                "B) out.fits",
                "G) none",
                "H) 1 100 1 100",
                "# Component number: 1",
                "# STRUCTURE: disk",
                "0) expdisk",
                "1) 50.0 50.0 1 1",
                "3) 16.0 1",
                "4) 10.0 1",
                "9) 0.6 1",
                "10) 30.0 1",
                "Z) 0",
                "# Component number: 2",
                "0) sky",
                "1) 0.0004 0",
                "2) 0.0 0",
                "3) 0.0 0",
                "Z) 0",
            ]
        ),
    )
    comps = number_components(feedme)
    assert sorted(comps.keys()) == [1]  # sky consumed number 2 silently
    assert comps[1]["name"] == "disk"


# ---------------------------------------------------------------- bound-hit scan
def _comp(name, ctype, **vals):
    d = {"name": name, "type": ctype, "x": 50.0, "y": 50.0, "mag": 17.0,
         "re": 5.0, "n": 4.0, "ba": 0.7, "pa": 30.0}
    d.update(vals)
    return d


def test_phantom_bound_hit_regression(tmp_path):
    """Regression (KILOGAS_432 A.10): a bare-number band must be decoded
    input-relative; fitted 7.9 inside the effective [3.5, 12] band is NOT a hit,
    and a naive comparison against the raw numbers 0.5..8 would phantom-hit."""
    cons = _write(tmp_path, "iter1.cons", "  2   n   0.5  8\n")
    decoded = decode_cons_file(cons)
    inputs = {1: _comp("disk", "expdisk"), 2: _comp("bulge", "sersic", n=4.0)}
    fitted = {1: _comp("disk", "expdisk"), 2: _comp("bulge", "sersic", n=7.9)}
    ctx = BoundContext(psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    hits = scan_bound_hits(decoded, inputs, fitted, ctx)
    assert hits == []
    # fitted 11.9 sits at the *effective* upper bound 12.0 -> hit, rel-bare band
    fitted = {1: _comp("disk", "expdisk"), 2: _comp("bulge", "sersic", n=11.9)}
    hits = scan_bound_hits(decoded, inputs, fitted, ctx)
    assert len(hits) == 1
    assert hits[0].param == "n" and hits[0].direction == "upper"
    assert hits[0].band_type == "rel-bare"


def test_provenance_default_vs_tightened(tmp_path):
    cons = _write(
        tmp_path,
        "iter2.cons",
        "1  re  2.0 to 148.5\n2  q  0.05 to 1.0\n3  re  0.1 to 9.0\n",
    )
    decoded = decode_cons_file(cons)
    inputs = {
        1: _comp("disk", "expdisk", re=40.0),
        2: _comp("bulge", "sersic", ba=1.0),
        3: _comp("bar", "sersic", re=8.9),
    }
    fitted = {
        1: _comp("disk", "expdisk", re=147.0),
        2: _comp("bulge", "sersic", ba=0.99),
        3: _comp("bar", "sersic", re=8.9),
    }
    ctx = BoundContext(psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    hits = {(h.comp_number, h.param): h for h in scan_bound_hits(decoded, inputs, fitted, ctx)}
    # region side 297 -> default re cap 148.5 -> the 148.5 row is "original"
    assert hits[(1, "re")].provenance == "original"
    # q at the 1.0 domain edge is a standing exemption (flagged, not relaxable)
    assert hits[(2, "q")].exempt is True
    # the 9.0 cap differs from the default -> self-imposed
    assert hits[(3, "re")].provenance == "self-imposed"


def test_psf_scale_re_floor_exemption(tmp_path):
    cons = _write(tmp_path, "iter3.cons", "2  re  0.5 to 12.0\n")
    decoded = decode_cons_file(cons)
    inputs = {1: _comp("disk", "expdisk"), 2: _comp("bulge", "sersic", re=0.5)}
    fitted = {1: _comp("disk", "expdisk"), 2: _comp("bulge", "sersic", re=0.5)}
    ctx = BoundContext(psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    hits = scan_bound_hits(decoded, inputs, fitted, ctx)
    assert len(hits) == 1 and hits[0].exempt is True
    assert "PSF-scale" in hits[0].note


def test_bar_n_prior_exemption(tmp_path):
    cons = _write(tmp_path, "iter4.cons", "3  n  0.5 to 0.5\n")
    decoded = decode_cons_file(cons)
    inputs = {3: _comp("bar", "sersic", n=0.5)}
    fitted = {3: _comp("bar", "sersic", n=0.5)}
    ctx = BoundContext(psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    hits = scan_bound_hits(decoded, inputs, fitted, ctx)
    assert len(hits) == 1 and hits[0].exempt is True
    assert "hard prior" in hits[0].note


def test_unbounded_params_never_hit(tmp_path):
    cons = _write(tmp_path, "iter5.cons", "2  q  0.05 to 1.0\n")
    decoded = decode_cons_file(cons)
    inputs = {1: _comp("disk", "expdisk", re=40.0), 2: _comp("bulge", "sersic")}
    # disk re unbounded at 5000 (absurd) -> never reported
    fitted = {1: _comp("disk", "expdisk", re=5000.0), 2: _comp("bulge", "sersic", ba=0.4)}
    ctx = BoundContext(psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert scan_bound_hits(decoded, inputs, fitted, ctx) == []
