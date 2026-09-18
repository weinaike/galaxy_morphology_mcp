"""Tests for src/beam/transcribe.py (mechanical candidate transcription)."""

import pytest

from beam.transcribe import transcribe

PARENT_FEEDME = """================================================================================
# IMAGE and GALFIT CONTROL PARAMETERS
A) image.fits          # Input data image (FITS file)
B) galfit_out.fits     # Output data image block
C) sigma.fits          # Sigma image name
D) psf.fits            # PSF image
E) 1                   # PSF fine sampling factor relative to data
F) mask.fits           # Bad pixel mask
G) none File with parameter constraints
H) 1   297  1   297    # Image region to fit
I) 55      55          # Size of the convolution box
J) 22.5                # Magnitude photometric zeropoint
K) 0.262  0.262        # Plate scale
O) regular             # Display type
P) 0                   # Choose: 0=optimize

# INITIAL FITTING PARAMETERS
# ------------------------------------------------------------------------------
# Component number: 1
# STRUCTURE: disk
 0) expdisk                 #  Component type
 1) 148.000  148.000  1  1      #  Position x, y
 3) 16.5000        1             #  Integrated magnitude
 4) 12.000        1             #  R_s (disk scale-length) [pix]
 9) 0.650        1             #  Axis ratio (b/a)
10) 27.00        1             #  Position angle
 Z) 0                      #  Skip this model?

# Component number: 2
# STRUCTURE: bulge
 0) sersic                 #  Component type
 1) 148.000  148.000  1  1      #  Position x, y
 3) 18.2000        1             #  Integrated magnitude
 4) 2.000        1             #  R_e (effective radius)   [pix]
 5) 4.000        0             #  Sersic index n
 9) 0.900        1             #  Axis ratio (b/a)
10) 27.00        1             #  Position angle
 Z) 0                      #  Skip this model?

# Component number: 3
0) sky                       #  Component type
 1) 0.000401008      0             #  Sky background [ADUs] - fixed
 2) 0.0000      0             #  dsky/dx
 3) 0.0000      0             #  dsky/dy
 Z) 0
================================================================================
"""

# converged values (galfit.NN style: same blocks, new numbers)
GALFIT_NN = """================================================================================
# IMAGE and GALFIT CONTROL PARAMETERS
A) image.fits
B) galfit_out.fits
G) none
H) 1   297  1   297

# Component number: 1
 0) expdisk
 1) 147.512  148.104  1  1
 3) 16.8123        1
 4) 13.4400        1
 9) 0.7122        1
10) 29.1500        1
 Z) 0

# Component number: 2
 0) sersic
 1) 147.512  148.104  1  1
 3) 17.9530        1
 4) 2.4120        1
 5) 4.0000        0
 9) 0.8811        1
10) 31.0000        1
 Z) 0

# Component number: 3
 0) sky
 1) 0.000401008      0
 Z) 0
================================================================================
"""


@pytest.fixture
def parent(tmp_path):
    f = tmp_path / "_iter1.feedme"
    f.write_text(PARENT_FEEDME, encoding="utf-8")
    nn = tmp_path / "galfit.01"
    nn.write_text(GALFIT_NN, encoding="utf-8")
    return str(f), str(nn)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_warm_start_backfill_and_sky_verbatim(parent, tmp_path):
    out_f, out_c = str(tmp_path / "_iter2.feedme"), str(tmp_path / "iter2.cons")
    r = transcribe(parent[0], parent[1], [], out_f, out_c,
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r.ok, r.notes
    text = _read(out_f)
    # undeclared parameters backfilled from galfit.NN converged values
    assert "16.8123" in text and "13.44" in text and "2.412" in text and "17.953" in text
    # toggles kept from the parent round (bulge n still fixed: value backfilled, toggle 0)
    assert " 5) 4  0" in text
    # sky carried verbatim - never backfilled
    assert "0.000401008" in text
    # G) repointed at the generated cons
    assert "G) iter2.cons" in text
    # cons: K=2 centrals -> paired offset chain anchored on the disk
    cons = _read(out_c)
    assert "1_2   x   offset" in cons and "1_2   y   offset" in cons
    # default bounds present, 'to' form; expdisk band written in Rs
    assert "1   re   0.2381 to 88.3929" in cons  # [0.4,148.5]/1.68 (floor = 0.1*FWHM)
    assert "2   re   0.4000 to 148.5000" in cons
    assert "2   n    0.1000 to 8.0000" in cons
    assert "q    0.0500 to 1.0000" in cons


def test_add_bar_with_triplet_and_chain(parent, tmp_path):
    out_f, out_c = str(tmp_path / "_iter3.feedme"), str(tmp_path / "iter3.cons")
    prims = [{"op": "add", "add": {
        "structure_name": "bar", "component_type": "sersic",
        "mag": 17.6, "re_px": 5.5, "n": 0.5, "q": 0.35, "pa_deg": 45.0,
        "toggles": {"n": 0},
        # cons_bounds are IGNORED (initial values only — no candidate-declared bands)
        "cons_bounds": {"re": [4.5, 10.0], "n": None, "q": None, "center_window": None}}}]
    r = transcribe(parent[0], parent[1], prims, out_f, out_c,
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297),
                   default_mag_hint=17.6)
    assert r.ok, r.notes
    text = _read(out_f)
    assert "# STRUCTURE: bar" in text
    assert "0) sersic" in text
    assert "5) 0.5" in text          # n=0.5 fixed (toggle 0)
    assert "9) 0.35" in text and "10) 45" in text
    # bar block sits before the sky block
    assert text.index("# STRUCTURE: bar") < text.index("0) sky")
    # chain now includes the bar (number 3), anchor disk first
    cons = _read(out_c)
    assert "1_2_3   x   offset" in cons and "1_2_3   y   offset" in cons
    # candidate-declared bands are ignored: the bar gets the DEFAULT re band
    assert "3   re   4.5000 to 10.0000" not in cons
    assert "3   re   0.4000 to 148.5000" in cons


def test_tune_re_convert_and_remove(parent, tmp_path):
    out_f, out_c = str(tmp_path / "_iter4.feedme"), str(tmp_path / "iter4.cons")
    prims = [
        {"op": "tune", "tune": {"structure_name": "disk", "param": "re_px",
                                "value": 25.0}},   # effective Re -> Rs=14.881
        {"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                "value": 2.0, "toggle": 1,
                                "cons_bounds": {"n": [0.5, 8.0], "re": None,
                                                 "q": None, "center_window": None}}},
    ]
    r = transcribe(parent[0], parent[1], prims, out_f, out_c,
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r.ok, r.notes
    text = _read(out_f)
    assert "4) 14.881" in text            # 25/1.68 written into the Rs row
    assert " 5) 2  1" in text             # bulge n tuned and freed (toggle 1)
    cons = _read(out_c)
    # candidate-declared n band ignored: default band only
    assert "2   n    0.5000 to 8.0000" not in cons
    assert "2   n    0.1000 to 8.0000" in cons

    # remove path: drop the bulge -> single central, no chain, +/-2 windows
    r2 = transcribe(parent[0], parent[1], [{"op": "remove", "remove": "bulge"}],
                    str(tmp_path / "_iter5.feedme"), str(tmp_path / "iter5.cons"),
                    psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r2.ok
    text2 = _read(r2.feedme)
    assert "STRUCTURE: bulge" not in text2 and "0) sky" in text2
    cons2 = _read(r2.cons)
    import re as _re
    assert not _re.search(r"^\s*\d+(_\d+)+\s+[xy]\s+offset", cons2, _re.M)  # no chain
    assert "1   x    -2  2" in cons2 and "1   y    -2  2" in cons2


def test_convert_singlesersic_to_disk(tmp_path):
    single = """A) image.fits
B) galfit_out.fits
G) none
H) 1 297 1 297

# Component number: 1
# STRUCTURE: singlesersic
0) sersic
1) 148.0 148.0 1 1
3) 15.0 1
4) 20.0 1
5) 2.5 1
9) 0.75 1
10) 27.0 1
Z) 0

# Component number: 2
0) sky
1) 0.0004 0
2) 0.0 0
3) 0.0 0
Z) 0
"""
    f = tmp_path / "single.feedme"
    f.write_text(single, encoding="utf-8")
    nn = tmp_path / "nn"
    nn.write_text(single, encoding="utf-8")  # converged == input (fixture only)
    prims = [
        {"op": "convert", "convert": {"from_name": "singlesersic", "to_name": "disk",
                                       "to_type": "expdisk"}},
        {"op": "add", "add": {"structure_name": "bulge", "component_type": "sersic",
                               "re_px": 3.0, "n": 4.0, "q": 0.9, "pa_deg": 27.0,
                               "mag": 18.2, "toggles": {"n": 0}}},
    ]
    r = transcribe(str(f), str(nn), prims,
                   str(tmp_path / "_iter2.feedme"), str(tmp_path / "iter2.cons"),
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r.ok, r.notes
    text = _read(r.feedme)
    assert "# STRUCTURE: disk" in text and "0) expdisk" in text
    # Rs = 20/1.68 = 11.9048 in the 4) row
    assert "4) 11.9048" in text
    # expdisk block has no 5) n row anymore
    disk_block = text[text.index("# STRUCTURE: disk"):text.index("# STRUCTURE: bulge")]
    assert "5)" not in disk_block
    assert r.numbers["disk"] == 1 and r.numbers["bulge"] == 2


def test_psf_companion_block_has_no_shape_rows(parent, tmp_path):
    """Regression (KILOGAS_319 shakedown): a psf companion must carry ONLY
    position/mag rows — no re/n/q/PA defaults and no re/n/q cons bands."""
    out_f, out_c = str(tmp_path / "_iter7.feedme"), str(tmp_path / "iter7.cons")
    prims = [{"op": "add", "add": {"structure_name": "companion",
                                    "component_type": "psf",
                                    "mag": 19.0, "x_px": 185.0, "y_px": 205.0}}]
    r = transcribe(parent[0], parent[1], prims, out_f, out_c,
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r.ok, r.notes
    text = _read(out_f)
    start = text.index("# STRUCTURE: companion")
    block = text[start:text.index("0) sky", start)]
    for row in ("4)", "5)", "9)", "10)"):
        assert row not in block, f"psf block must not carry a {row} row"
    assert "3) 19" in block
    cons = _read(out_c)
    import re as _re
    assert not _re.search(r"^ 3   re ", cons, _re.M)  # no re band for a psf


def test_outerdisk_in_chain_and_centres_normalized(parent, tmp_path):
    """Regression (KILOGAS_319 shakedown): the outer envelope must join the
    concentric chain, and all chain members' input centres must be normalized
    to the anchor (the offset chain locks INPUT relative positions —
    warm-start scatter would otherwise be preserved forever)."""
    out_f, out_c = str(tmp_path / "_iter8.feedme"), str(tmp_path / "iter8.cons")
    prims = [
        {"op": "remove", "remove": "bulge"},
        {"op": "add", "add": {"structure_name": "outerdisk", "component_type": "expdisk",
                               "mag": 18.5, "re_px": 90.0, "q": 0.6, "pa_deg": 27.0,
                               "x_px": 143.9, "y_px": 140.9}},  # deliberately off-centre
    ]
    r = transcribe(parent[0], parent[1], prims, out_f, out_c,
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r.ok, r.notes
    text = _read(out_f)
    # outerdisk centre normalized to the disk (anchor) centre
    assert "1) 147.512  148.104" in text  # anchor centre appears twice (disk + outerdisk)
    assert "143.9" not in text.split("# STRUCTURE: outerdisk")[1].split("Z)")[0]
    # chain includes outerdisk (disk=1, outerdisk=2 after bulge removal)
    cons = _read(out_c)
    assert "1_2   x   offset" in cons and "1_2   y   offset" in cons
    # no stray position window for outerdisk (chain-bound)
    import re as _re
    assert not _re.search(r"^ 2   x ", cons, _re.M)


def test_class_a_violations_abort_whole(parent, tmp_path):
    # tune a target that does not exist
    r = transcribe(parent[0], parent[1],
                   [{"op": "tune", "tune": {"structure_name": "lens",
                                            "param": "q", "value": 0.8}}],
                   str(tmp_path / "x.feedme"), str(tmp_path / "x.cons"))
    assert not r.ok and any("not found" in n for n in r.notes)
    # F1 on a forbidden target
    r2 = transcribe(parent[0], parent[1],
                    [{"op": "add", "add": {"structure_name": "bulge2",
                                            "component_type": "sersic", "re_px": 3.0,
                                            "f1": {"amplitude": 0.05, "phase_deg": 30}}}],
                    str(tmp_path / "y.feedme"), str(tmp_path / "y.cons"))
    assert not r2.ok and any("F1" in n for n in r2.notes)


def test_check_feedme_file_accepts_output(parent, tmp_path):
    from tools.beam_actions_galfit import check_feedme_file

    out_f, out_c = str(tmp_path / "_iter6.feedme"), str(tmp_path / "iter6.cons")
    prims = [{"op": "add", "add": {"structure_name": "bar", "component_type": "sersic",
                                    "mag": 17.6, "re_px": 5.5, "n": 0.5, "q": 0.35,
                                    "pa_deg": 45.0, "toggles": {"n": 0},
                                    "cons_bounds": {"re": [4.5, 10.0]}}}]
    r = transcribe(parent[0], parent[1], prims, out_f, out_c,
                   psf_fwhm_px=4.0, fit_region=(1, 297, 1, 297))
    assert r.ok, r.notes
    check = check_feedme_file(out_f)
    assert check["status"] == "success", check.get("errors") or check.get("error")
    names = sorted(c["name"] for c in check["components"])
    assert names == ["bar", "bulge", "disk"]
