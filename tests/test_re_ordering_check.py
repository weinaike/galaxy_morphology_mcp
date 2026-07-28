"""Unit tests for ``src/tools/re_ordering_check.py``.

The fixtures are generated programmatically from helper builders, each
producing a minimal valid (``.lyric``, ``.gssummary``) pair tailored to the
scenario under test. The lyric contains only the lines that
``parse_component_types`` and ``_parse_n_block_labels`` actually scan; the
gssummary contains only the parameter lines that ``parse_gssummary`` records.

A reference real-world fixture (``Plate0436_MJD51883_Fiber493_r`` iter3,
which has a known ``bar_Re > disk_Re`` violation) is also embedded as a
regression case to lock the parser's behavior against the actual file
format produced by GalfitS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.re_ordering_check import check_re_ordering, _parse_n_block_labels


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #
def _build_lyric(
    p_block_labels: list[tuple[str, str, str]],
    n_block_labels: list[str] | None = None,
) -> str:
    """Build a minimal .lyric string.

    Args:
        p_block_labels: list of (prefix_letter, label, profile_type) tuples,
            e.g. [('a', 'disk', 'sersic'), ('b', 'bulge', 'sersic')].
        n_block_labels: optional list of N-block labels (AGN/nucleus).
    """
    lines: list[str] = [
        "R1) test_galaxy",
        "R2) [10.0, 20.0]",
        "R3) 0.4",
        "",
        "# image atlas",
        "Aa1) 'img list'",
        "Aa2) ['a']",
        "",
    ]
    for letter, label, ptype in p_block_labels:
        lines.append(f"# Component {letter}")
        lines.append(f"P{letter}1) {label}")
        lines.append(f"P{letter}2) {ptype}")
        lines.append(f"P{letter}5) [1.0, 0.01, 50.0, 0.1, 1]")
        lines.append("")
    if n_block_labels:
        for i, label in enumerate(n_block_labels):
            letter = chr(ord('a') + i)
            lines.append(f"# N-block {letter}")
            lines.append(f"N{letter}1) {label}")
            lines.append(f"N{letter}4) [0.0, -1.0, 1.0, 0.1, 1]")
            lines.append(f"N{letter}5) [0.0, -1.0, 1.0, 0.1, 1]")
            lines.append("")
    lines.append("Ga1) mygal")
    ga2_list = [f"'{letter}'" for letter, _, _ in p_block_labels]
    lines.append(f"Ga2) [{', '.join(ga2_list)}]")
    return "\n".join(lines) + "\n"


def _build_gssummary(
    free_params: dict[str, float] | None = None,
    fixed_params: dict[str, float] | None = None,
) -> str:
    """Build a minimal .gssummary string matching GalfitS output format."""
    lines = [
        "# target: test_galaxy",
        "# config file: /tmp/test.lyric",
        "# fitting mode: images - photometry",
        "# reduced chisq: 0.5",
        "# BIC: 100.0",
        "# free parameters:",
        "pname    best_value",
    ]
    for name, value in (free_params or {}).items():
        lines.append(f"{name}    {value:.4f}")
    lines.append("# fixed parameters:")
    lines.append("# pname    fixed_value")
    for name, value in (fixed_params or {}).items():
        lines.append(f"{name}    {value:.4f}")
    lines.append("#########################################")
    return "\n".join(lines) + "\n"


def _write_pair(
    tmp_path: Path,
    lyric: str,
    gssummary: str,
    name: str = "test",
) -> tuple[str, str]:
    """Write a (lyric, gssummary) pair into tmp_path; return absolute paths."""
    lyric_path = tmp_path / f"{name}.lyric"
    summary_path = tmp_path / f"{name}.gssummary"
    lyric_path.write_text(lyric, encoding="utf-8")
    summary_path.write_text(gssummary, encoding="utf-8")
    return str(lyric_path), str(summary_path)


# --------------------------------------------------------------------------- #
# Pass scenarios
# --------------------------------------------------------------------------- #
class TestPass:
    def test_pass_full_chain(self, tmp_path):
        """All four center components present, strictly decreasing Re → pass."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "lens", "sersic"),
            ("c", "bar", "sersic"),
            ("d", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 10.0, "lens_Re": 5.0, "bar_Re": 2.0, "bulge_Re": 0.5,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "full_chain")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        assert result["expected_chain"] == "re_disk > re_lens > re_bar > re_bulge"
        assert result["violations"] == []
        assert result["swappable_overall"] is False
        assert result["custom_instructions_hint"] == ""
        assert {c["role"] for c in result["components"]} == {"disk", "lens", "bar", "bulge"}

    def test_pass_subsequence_no_lens(self, tmp_path):
        """{Disk, Bar, Bulge} — Lens absent → chain correctly drops Lens."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bar", "sersic"),
            ("c", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 7.0, "bar_Re": 3.0, "bulge_Re": 0.4,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "no_lens")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        assert result["expected_chain"] == "re_disk > re_bar > re_bulge"

    def test_pass_subsequence_no_bar(self, tmp_path):
        """{Disk, Lens, Bulge} — Bar absent → chain correctly drops Bar."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "lens", "sersic"),
            ("c", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 8.0, "lens_Re": 4.0, "bulge_Re": 0.3,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "no_bar")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        assert result["expected_chain"] == "re_disk > re_lens > re_bulge"

    def test_pass_two_component_disk_bulge(self, tmp_path):
        """Only {Disk, Bulge} → chain degrades to re_disk > re_bulge."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({"disk_Re": 5.0, "bulge_Re": 0.5})
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "disk_bulge")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        assert result["expected_chain"] == "re_disk > re_bulge"

    def test_pass_single_component_no_check(self, tmp_path):
        """Only Disk present → trivially passes (no pair to compare)."""
        lyric = _build_lyric([("a", "disk", "sersic")])
        gssummary = _build_gssummary({"disk_Re": 5.0})
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "disk_only")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        assert result["violations"] == []


# --------------------------------------------------------------------------- #
# Violation scenarios
# --------------------------------------------------------------------------- #
class TestViolations:
    def test_violation_bulge_gt_disk_swappable(self, tmp_path):
        """{Disk, Bulge} with bulge_Re > disk_Re → swappable=True."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({"disk_Re": 1.0, "bulge_Re": 3.0})
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "swap")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "fail"
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v["pair"] == ["disk", "bulge"]
        assert v["involves_bar_or_lens"] is False
        assert v["swappable"] is True
        assert result["swappable_overall"] is True
        assert "交换 disk ↔ bulge" in result["custom_instructions_hint"]

    def test_violation_bar_gt_disk_not_swappable(self, tmp_path):
        """bar_Re > disk_Re → involves Bar, not swappable."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bar", "sersic"),
            ("c", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 5.0, "bar_Re": 12.0, "bulge_Re": 0.5,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "bar_gt_disk")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "fail"
        v = result["violations"][0]
        assert v["pair"] == ["disk", "bar"]
        assert v["involves_bar_or_lens"] is True
        assert v["swappable"] is False
        assert result["swappable_overall"] is False
        assert "严禁交换标签" in result["custom_instructions_hint"]

    def test_violation_lens_le_bar(self, tmp_path):
        """Full chain with lens_Re < bar_Re → violation involves Lens."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "lens", "sersic"),
            ("c", "bar", "sersic"),
            ("d", "bulge", "sersic"),
        ])
        # lens_Re (1.0) < bar_Re (3.0) → violation
        gssummary = _build_gssummary({
            "disk_Re": 10.0, "lens_Re": 1.0, "bar_Re": 3.0, "bulge_Re": 0.3,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "lens_le_bar")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "fail"
        # Find the lens-vs-bar violation (other pairs pass)
        v = next(v for v in result["violations"] if v["pair"] == ["lens", "bar"])
        assert v["involves_bar_or_lens"] is True
        assert v["swappable"] is False
        assert result["swappable_overall"] is False

    def test_violation_custom_instructions_hint_format(self, tmp_path):
        """custom_instructions_hint must contain expected chain + violation line."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bar", "sersic"),
            ("c", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 1.0, "bar_Re": 5.0, "bulge_Re": 0.5,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "hint")

        result = check_re_ordering(summary_path, lyric_path)
        hint = result["custom_instructions_hint"]

        assert "期望链：re_disk > re_bar > re_bulge" in hint
        assert "re_disk" in hint and "re_bar" in hint
        assert "拟合失败" in hint or "视为拟合失败" in hint


# --------------------------------------------------------------------------- #
# Exclusion scenarios
# --------------------------------------------------------------------------- #
class TestExclusions:
    def test_agn_n_block_excluded(self, tmp_path):
        """N-block label (agn) appears in excluded, not in components."""
        lyric = _build_lyric(
            p_block_labels=[
                ("a", "disk", "sersic"),
                ("b", "bulge", "sersic"),
            ],
            n_block_labels=["agn"],
        )
        gssummary = _build_gssummary({"disk_Re": 5.0, "bulge_Re": 0.5})
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "with_agn")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        agn_entries = [e for e in result["excluded"] if e["role"] == "agn"]
        assert len(agn_entries) == 1
        assert agn_entries[0]["label"] == "agn"
        # AGN must NOT appear in the components list used for comparison
        assert not any(c["label"] == "agn" for c in result["components"])

    def test_agn_n_block_under_nucleus_label(self, tmp_path):
        """N-block label named 'nucleus' (not 'agn') → still excluded as AGN."""
        lyric = _build_lyric(
            p_block_labels=[
                ("a", "disk", "sersic"),
                ("b", "bulge", "sersic"),
            ],
            n_block_labels=["nucleus"],
        )
        gssummary = _build_gssummary({"disk_Re": 5.0, "bulge_Re": 0.5})
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "with_nucleus")

        result = check_re_ordering(summary_path, lyric_path)

        excluded_labels = {e["label"] for e in result["excluded"]}
        assert "nucleus" in excluded_labels
        assert result["status"] == "pass"

    def test_companion_excluded(self, tmp_path):
        """P-block label 'companion1' → excluded as companion."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bulge", "sersic"),
            ("c", "companion1", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 5.0, "bulge_Re": 0.5, "companion1_Re": 2.0,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "with_comp")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        comp_entries = [e for e in result["excluded"] if e["role"] == "companion"]
        assert len(comp_entries) == 1
        assert comp_entries[0]["label"] == "companion1"
        assert not any(c["label"] == "companion1" for c in result["components"])

    def test_other_label_excluded_with_warning(self, tmp_path):
        """Unrecognized P-block label → excluded as 'other' + warning."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bulge", "sersic"),
            ("c", "mystery_blob", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 5.0, "bulge_Re": 0.5, "mystery_blob_Re": 2.0,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "with_other")

        result = check_re_ordering(summary_path, lyric_path)

        other_entries = [e for e in result["excluded"] if e["role"] == "other"]
        assert len(other_entries) == 1
        assert other_entries[0]["label"] == "mystery_blob"
        assert any("mystery_blob" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
class TestEdgeCases:
    def test_label_case_insensitive(self, tmp_path):
        """Labels 'Disk', 'BULGE', 'Bar' (mixed case) → classified correctly."""
        lyric = _build_lyric([
            ("a", "Disk", "sersic"),
            ("b", "BULGE", "sersic"),
            ("c", "Bar", "sersic"),
        ])
        # NOTE: parameter names in summary must match label verbatim
        gssummary = _build_gssummary({
            "Disk_Re": 7.0, "Bar_Re": 3.0, "BULGE_Re": 0.4,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "case")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        roles = {c["role"] for c in result["components"]}
        assert roles == {"disk", "bulge", "bar"}

    def test_re_in_fixed_section(self, tmp_path):
        """Re value in the 'fixed parameters' section must still be read."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bulge", "sersic"),
        ])
        # disk_Re is free; bulge_Re is FIXED
        gssummary = _build_gssummary(
            free_params={"disk_Re": 5.0},
            fixed_params={"bulge_Re": 0.5},
        )
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "fixed_re")

        result = check_re_ordering(summary_path, lyric_path)

        assert result["status"] == "pass"
        bulge = next(c for c in result["components"] if c["role"] == "bulge")
        assert abs(bulge["re_arcsec"] - 0.5) < 1e-9

    def test_missing_re_field_returns_error(self, tmp_path):
        """Missing Re for a present component → status='error' path is NOT
        triggered at the top level (the component is skipped with a warning
        instead, and the check proceeds with whatever components have Re).

        This documents the current design: only file-level / parser-level
        failures raise status='error'; per-component missing Re degrades
        gracefully.
        """
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "bulge", "sersic"),
        ])
        # bulge_Re deliberately absent
        gssummary = _build_gssummary({"disk_Re": 5.0})
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "missing_re")

        result = check_re_ordering(summary_path, lyric_path)

        # Disk alone remains → trivially passes (no pair)
        assert result["status"] == "pass"
        assert any("bulge_Re" in w or "bulge" in w for w in result["warnings"])

    def test_file_not_found_returns_error(self, tmp_path):
        """Missing summary file → status='error', no exception raised."""
        lyric = _build_lyric([("a", "disk", "sersic")])
        lyric_path, _ = _write_pair(tmp_path, lyric, _build_gssummary({}), "lf")
        result = check_re_ordering("/nonexistent/summary.gssummary", lyric_path)
        assert result["status"] == "error"
        assert "error_message" in result

    def test_multiple_components_same_role(self, tmp_path):
        """Two disks (disk, disk2) → take max, emit warning."""
        lyric = _build_lyric([
            ("a", "disk", "sersic"),
            ("b", "disk2", "sersic"),
            ("c", "bulge", "sersic"),
        ])
        gssummary = _build_gssummary({
            "disk_Re": 5.0, "disk2_Re": 8.0, "bulge_Re": 0.5,
        })
        lyric_path, summary_path = _write_pair(tmp_path, lyric, gssummary, "two_disks")

        result = check_re_ordering(summary_path, lyric_path)

        # Max disk Re (8.0) is used; chain still passes against bulge (0.5)
        assert result["status"] == "pass"
        assert any("disk" in w.lower() and "max" in w.lower() for w in result["warnings"])


# --------------------------------------------------------------------------- #
# N-block label parser unit test
# --------------------------------------------------------------------------- #
class TestNBlockParser:
    def test_parse_n_block_labels_basic(self, tmp_path):
        lyric = _build_lyric(
            p_block_labels=[("a", "disk", "sersic")],
            n_block_labels=["agn"],
        )
        path = tmp_path / "n.lyric"
        path.write_text(lyric, encoding="utf-8")
        labels = _parse_n_block_labels(str(path))
        assert labels == {"agn"}

    def test_parse_n_block_labels_multiple(self, tmp_path):
        lyric = _build_lyric(
            p_block_labels=[("a", "disk", "sersic")],
            n_block_labels=["agn", "nucleus"],
        )
        path = tmp_path / "n2.lyric"
        path.write_text(lyric, encoding="utf-8")
        labels = _parse_n_block_labels(str(path))
        assert labels == {"agn", "nucleus"}

    def test_parse_n_block_labels_none(self, tmp_path):
        lyric = _build_lyric([("a", "disk", "sersic")])
        path = tmp_path / "n3.lyric"
        path.write_text(lyric, encoding="utf-8")
        labels = _parse_n_block_labels(str(path))
        assert labels == set()


# --------------------------------------------------------------------------- #
# Real-world regression test (from Plate0436 iter3 — known bar > disk violation)
# --------------------------------------------------------------------------- #
class TestRealWorldRegression:
    """Regression case: the actual iter3 output of Plate0436_MJD51883_Fiber493_r
    has bar_Re=12.04 > disk_Re=6.97, which the check must flag as a Bar-involving,
    non-swappable violation.
    """

    REAL_LYRIC = """\
# galfits config file iter3 (regression fixture, trimmed)
R1) Plate0436_MJD51883_Fiber493_r
R2) [119.18219,44.85668]
R3) 0.40000

Pa1) disk
Pa2) sersic

Pb1) bulge
Pb2) sersic

Pc1) bar
Pc2) sersic

Ga1) mygal
Ga2) ['a','b','c']
"""

    REAL_GSSUMMARY = """\
# target: Plate0436_MJD51883_Fiber493_r
# config file: /home/jiangbo/SDSS/Plate0436_MJD51883_Fiber493_r/_iter3.lyric
# fitting mode: images - photometry
# reduced chisq: 0.30854031443595886
# BIC: 18652.154296875
# free parameters:
pname    best_value
disk_xcen    -0.5432
disk_ycen    0.1212
disk_Re    6.9673
disk_ang    88.6581
disk_axrat    0.3817
bulge_xcen    -0.3311
bulge_ycen    -0.0240
bulge_Re    0.2513
bulge_ang    39.5618
bulge_axrat    0.5002
bar_xcen    0.1725
bar_ycen    -1.7275
bar_Re    12.0386
bar_ang    -175.4862
bar_axrat    0.6000
# fixed parameters:
# pname    fixed_value
disk_n    1.0000
bulge_n    4.0000
bar_n    0.5000
#########################################
"""

    def test_iter3_bar_gt_disk_detected(self, tmp_path):
        lyric_path, summary_path = _write_pair(
            tmp_path, self.REAL_LYRIC, self.REAL_GSSUMMARY, "iter3",
        )
        result = check_re_ordering(summary_path, lyric_path)

        # Must be flagged as fail
        assert result["status"] == "fail"

        # Must have exactly the bar-vs-disk violation
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v["pair"] == ["disk", "bar"]
        assert v["left_re_arcsec"] == pytest.approx(6.9673, abs=1e-4)
        assert v["right_re_arcsec"] == pytest.approx(12.0386, abs=1e-4)
        assert v["involves_bar_or_lens"] is True
        assert v["swappable"] is False

        # Overall swap flag must be False (Bar involvement forbids label swap)
        assert result["swappable_overall"] is False

        # Expected chain for {disk, bar, bulge}
        assert result["expected_chain"] == "re_disk > re_bar > re_bulge"

        # Hint must carry the hard constraint about Bar/Lens
        hint = result["custom_instructions_hint"]
        assert "严禁交换标签" in hint
        assert "re_disk" in hint and "re_bar" in hint
