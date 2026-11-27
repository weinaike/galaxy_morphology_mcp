"""Unit tests for pure-SED mock lyric generation — parent P-block row copying.

Covers the Pa33-36 carry-over fix: when the parent lyric (the locked image-fit
round) carries SED pass-through rows for a component, the generated
pure_sed.lyric must copy them verbatim under the mock's own label; rows the
parent lacks fall back to the built-in defaults.
"""

import sys
from pathlib import Path

import pytest

# Add src to path (mirrors conftest for direct execution)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.galfits_fitting import (  # noqa: E402
    SED_PASS_THROUGH_ROWS,
    extract_parent_sed_rows,
    generate_pure_sed_fitting_lyric,
    normalize_pa27_zezhong,
)

PARENT_LYRIC = """# Region information
R1) mygal
R2) [180.0, 45.0]
R3) 0.05

# Profile A
Pa1) disk
Pa2) expdisk
Pa3) [0.0,-0.3,0.3,0.1,0]
Pa5) [7.2,0.1,60,0.1,1]
Pa9) [[-1.5,-8,0,0.1,1],[-1.5,-8,0,0.1,1],[-1.5,-8,0,0.1,1],[-1.5,-8,0,0.1,1],[-1.5,-8,0,0.1,1]]
Pa11) [[0.002,0.001,0.04,0.001,1]]
Pa15) bins
Pa27) 0
Pa33) [[0.008,0.0001,0.04,0.001,0]]
Pa34) [1.1,0,4,1,0]
Pa35) [1.4,0,2,0.3,0]
Pa36) [-0.3,-1,0.4,0.1,0]

# Profile B
Pb1) bulge
Pb2) sersic
Pb3) [0.0,-0.3,0.3,0.1,0]
Pb33) [[0.004,0.0001,0.04,0.001,0]]
Pb34) [0.3,0,4,1,0]
"""


def test_extract_parent_sed_rows_copies_pass_through_rows_only():
    rows = extract_parent_sed_rows(PARENT_LYRIC, "disk")

    # Pa33-36 copied verbatim from the parent
    assert rows[33] == "[[0.008,0.0001,0.04,0.001,0]]"
    assert rows[34] == "[1.1,0,4,1,0]"
    assert rows[35] == "[1.4,0,2,0.3,0]"
    assert rows[36] == "[-0.3,-1,0.4,0.1,0]"
    # other pass-through rows present on the parent are carried too
    assert rows[11] == "[[0.002,0.001,0.04,0.001,1]]"
    assert rows[27] == "0"
    # structural rows are never part of the result
    assert 3 not in rows
    assert 5 not in rows
    # every key is a legal pass-through row
    assert set(rows) <= set(SED_PASS_THROUGH_ROWS)
    # rows the parent lacks are absent (fallback happens at write time)
    assert 12 not in rows


def test_extract_parent_sed_rows_matches_component_by_name():
    """The label lookup must bind to the right component: bulge's rows come
    from the Pb block, not the Pa block."""
    rows = extract_parent_sed_rows(PARENT_LYRIC, "bulge")
    assert rows[33] == "[[0.004,0.0001,0.04,0.001,0]]"
    assert rows[34] == "[0.3,0,4,1,0]"
    assert 35 not in rows  # Pb35 absent in the parent


def test_extract_parent_sed_rows_unknown_profile_returns_empty():
    assert extract_parent_sed_rows(PARENT_LYRIC, "nucleus") == {}
    assert extract_parent_sed_rows("", "disk") == {}


def _make_band_fits(path):
    """Minimal WCS-carrying FITS for the mock generator's header/shape read."""
    from astropy.io import fits
    from astropy.wcs import WCS

    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [180.0, 45.0]
    wcs.wcs.crpix = [10.5, 10.5]
    wcs.wcs.cdelt = [-0.0007, 0.0007]
    header = wcs.to_header()
    data = np.zeros((20, 20), dtype="float32") if "np" in dir() else None
    import numpy as np  # noqa: F811  (local to keep module import surface small)

    fits.PrimaryHDU(data=np.zeros((20, 20), dtype="float32"), header=header).writeto(
        path, overwrite=True
    )
    return str(path)


def test_generate_pure_sed_lyric_copies_parent_rows_and_falls_back(tmp_path):
    import numpy as np  # noqa: F401  (module import already needs numpy)
    from astropy.io import fits as astropy_fits

    fits_path = _make_band_fits(tmp_path / "decam_r.fits")
    band_fits_pairs = {"decam_r": ("decam_r", fits_path)}
    mock_root = tmp_path / "mock" / "mygal" / "disk"
    mock_root.mkdir(parents=True)

    parent_rows = extract_parent_sed_rows(PARENT_LYRIC, "disk")
    generate_pure_sed_fitting_lyric(
        profile_name="disk",
        mock_profile_root=str(mock_root),
        bands=["decam_r"],
        band_fits_pairs=band_fits_pairs,
        z_fit=0.05,
        parent_sed_rows=parent_rows,
    )

    text = (mock_root / "pure_sed.lyric").read_text(encoding="utf-8")

    # parent's Pa33-36 re-emitted verbatim under the mock's own label 'a'
    assert "Pa33) [[0.008,0.0001,0.04,0.001,0]]" in text
    assert "Pa34) [1.1,0,4,1,0]" in text
    assert "Pa35) [1.4,0,2,0.3,0]" in text
    assert "Pa36) [-0.3,-1,0.4,0.1,0]" in text
    # mock-free rows keep the parent's init values verbatim, EXCEPT Pa27:
    # the legacy 0 is rewritten to 1 (flux-identical under zezhong, and
    # Pa27=0 leaves zred_list empty -> display crash)
    assert "Pa27) 1" in text
    # MOCK_FREE rows keep the parent's init/min/max/step but the vary flag is
    # forced back to 1 (the image-stage parent freezes them at vary=0)
    assert "Pa11) [[0.002, 0.001, 0.04, 0.001, 1]]" in text
    assert "Pa9) [[-1.5, -8, 0, 0.1, 1], [-1.5, -8, 0, 0.1, 1], [-1.5, -8, 0, 0.1, 1], [-1.5, -8, 0, 0.1, 1], [-1.5, -8, 0, 0.1, 1]]" in text
    # rows the parent lacks fall back to the built-in defaults (vary=1)
    assert "Pa12) [[0.7,0,5.1,0.1,1]]" in text
    assert "Pa14) [9,6,12,0.1,1]" in text
    assert "Pa26) [3,0,5,0.1,1]" in text
    # the structural rows stay the generator's own mock defaults
    assert "Pa5) [0.2,0.1,1.7,0.1,0]" in text
    assert "Pa1) disk" in text


def test_generate_pure_sed_lyric_without_parent_rows_uses_defaults(tmp_path):
    fits_path = _make_band_fits(tmp_path / "decam_r.fits")
    band_fits_pairs = {"decam_r": ("decam_r", fits_path)}
    mock_root = tmp_path / "mock" / "mygal" / "bulge"
    mock_root.mkdir(parents=True)

    generate_pure_sed_fitting_lyric(
        profile_name="bulge",
        mock_profile_root=str(mock_root),
        bands=["decam_r"],
        band_fits_pairs=band_fits_pairs,
        z_fit=0.05,
    )

    text = (mock_root / "pure_sed.lyric").read_text(encoding="utf-8")
    # legacy parent (no rows passed): every SED row comes from the defaults,
    # and the Pa33-36 defaults keep the zezhong GalfitS read_config_file happy
    assert "Pa33) [[0.004,0.0001,0.04,0.001,0]]" in text
    assert "Pa34) [0.3,0,4,1,0]" in text
    assert "Pa35) [1,0,2,0.3,0]" in text
    assert "Pa36) [-0.7,-1,0.4,0.1,0]" in text
    assert "Pa15) bins" in text
    # the default Pa27 '0' is normalized to 1 as well (single code path)
    assert "Pa27) 1" in text
    assert "Pa27) 0" not in text


def test_normalize_pa27_zezhong_rewrites_only_legacy_zeros():
    text = (
        "Pa1) disk\n"
        "Pa26) [3, 0, 5, 0.1, 1]\n"   # row 26 untouched (0 inside a tuple)
        "Pa27) 0\n"                    # legacy zero -> 1
        "Pb27) 0.0\n"                  # float zero -> 1
        "Pc27) 3\n"                    # explicit mode passes through
        "Pd27) T\n"                    # template mode passes through
        "Ia27) 0\n"                    # not a P block — untouched
        "Pa9) [[-2, -8, 0, 0.1, 1]]\n"
    )
    out = normalize_pa27_zezhong(text)
    assert "Pa27) 1\n" in out
    assert "Pb27) 1\n" in out
    assert "Pc27) 3\n" in out
    assert "Pd27) T\n" in out
    assert "Ia27) 0\n" in out
    assert "Pa26) [3, 0, 5, 0.1, 1]\n" in out
    assert "Pa9) [[-2, -8, 0, 0.1, 1]]\n" in out
