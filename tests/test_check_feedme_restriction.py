"""Unit tests for the branch solution-space restriction (restrict-disk-bulge-bar-agn).

Covers the two mechanical gates added to ``tools/beam_actions_galfit``:

1. ``check_feedme_file`` rejects feedmes containing a forbidden component
   (Companion / Lens / OuterDisk / edgedisk) — errors, so the orchestrator
   fixes the structure before any ``run_galfit`` call.
2. ``_scan_forbidden_candidates`` flags VLM-returned candidate actions that
   target a forbidden component (advisory annotation appended to the returned
   Markdown), without false-positiving on Phase-1 diagnostic descriptions.
"""

from pathlib import Path

from tools.beam_actions_galfit import (
    _scan_forbidden_candidates,
    check_feedme_file,
)

RESTRICTION_MARKER = "outside the solution space"

_HEADER = (
    "A) img.fits\n"
    "B) out.fits\n"
    "C) none\n"
    "D) none\n"
    "E) 1\n"
    "F) none\n"
    "G) none\n"
    "H) 1 100 1 100\n"
    "I) 50 50\n"
    "J) 25.0\n"
    "K) 0.75 0.75\n"
    "O) regular\n"
    "P) 0\n\n"
)

_SKY_BLOCK = "# Component number: {n}\n 0) sky\n 1) 100.0    0\n 2) 0.0      0\n 3) 0.0      0\n Z) 0\n"


def _component_block(number: int, name: str, ctype: str) -> str:
    lines = [
        f"# Component number: {number}",
        f"# STRUCTURE: {name.upper()}",
        f" 0) {ctype}",
        " 1) 50.0 50.0 1 1",
        " 3) 15.0     1",
    ]
    if ctype in ("sersic", "expdisk", "edgedisk"):
        lines.append(" 4) 20.0     1")
    if ctype == "sersic":
        lines += [
            " 5) 2.0      1",
            " 6) 0.0      0",
            " 7) 0.0      0",
            " 8) 0.0      0",
            " 9) 0.7      1",
            "10) 30.0     1",
        ]
    elif ctype in ("expdisk", "edgedisk"):
        lines += [
            " 5) 0.0      0",
            " 6) 0.0      0",
            " 7) 0.0      0",
            " 8) 0.0      0",
            " 9) 0.6      1",
            "10) 20.0     1",
        ]
    lines.append(" Z) 0")
    return "\n".join(lines) + "\n\n"


def _make_feedme(tmp_path: Path, components: list[tuple[str, str]]) -> str:
    """Write a minimal feedme with the given (structure_name, type) components + sky."""
    body = _HEADER
    for i, (name, ctype) in enumerate(components, start=1):
        body += _component_block(i, name, ctype)
    body += _SKY_BLOCK.format(n=len(components) + 1)
    path = tmp_path / "test.feedme"
    path.write_text(body, encoding="utf-8")
    return str(path)


class TestCheckFeedmeRestriction:
    """check_feedme_file rejects forbidden components (branch restriction)."""

    def test_companion_sersic_rejected(self, tmp_path):
        feedme = _make_feedme(tmp_path, [("disk", "expdisk"), ("companion", "sersic")])
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        assert any(RESTRICTION_MARKER in e for e in result["errors"])

    def test_companion_psf_rejected(self, tmp_path):
        # companion named psf block: caught by name AND by the psf-multiplicity rule
        feedme = _make_feedme(tmp_path, [("disk", "expdisk"), ("agn", "psf"), ("companion", "psf")])
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        restriction_errors = [e for e in result["errors"] if RESTRICTION_MARKER in e]
        assert len(restriction_errors) >= 2  # name-based + psf-count

    def test_companion2_disambiguation_rejected(self, tmp_path):
        feedme = _make_feedme(
            tmp_path,
            [("disk", "expdisk"), ("companion", "sersic"), ("companion2", "sersic")],
        )
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        # both disambiguated names are flagged
        assert sum(RESTRICTION_MARKER in e for e in result["errors"]) >= 2

    def test_lens_rejected(self, tmp_path):
        feedme = _make_feedme(
            tmp_path, [("disk", "expdisk"), ("bulge", "sersic"), ("lens", "sersic")]
        )
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        assert any(RESTRICTION_MARKER in e for e in result["errors"])

    def test_outerdisk_second_expdisk_rejected(self, tmp_path):
        feedme = _make_feedme(
            tmp_path, [("disk", "expdisk"), ("outerdisk", "expdisk")]
        )
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        # flagged by name AND by the 2nd-expdisk (envelope) rule
        assert sum(RESTRICTION_MARKER in e for e in result["errors"]) >= 2

    def test_edgedisk_type_rejected(self, tmp_path):
        feedme = _make_feedme(tmp_path, [("edgedisk", "edgedisk")])
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        assert any("edgedisk" in e and RESTRICTION_MARKER in e for e in result["errors"])

    def test_satellite_secondary_names_rejected(self, tmp_path):
        feedme = _make_feedme(tmp_path, [("disk", "expdisk"), ("satellite", "psf")])
        result = check_feedme_file(feedme)
        assert result["status"] == "failure"
        assert any(RESTRICTION_MARKER in e for e in result["errors"])

    def test_legal_inventory_passes_restriction(self, tmp_path):
        # positive control: disk + bulge + bar + agn (+ sky) — no restriction errors
        feedme = _make_feedme(
            tmp_path,
            [("disk", "expdisk"), ("bulge", "sersic"), ("bar", "sersic"), ("agn", "psf")],
        )
        result = check_feedme_file(feedme)
        if result["status"] == "failure":
            assert not any(RESTRICTION_MARKER in e for e in result["errors"])
        else:
            names = [c["name"] for c in result["components"]]
            assert set(names) == {"disk", "bulge", "bar", "agn"}

    def test_legal_singlesersic_passes_restriction(self, tmp_path):
        feedme = _make_feedme(tmp_path, [("singlesersic", "sersic")])
        result = check_feedme_file(feedme)
        if result["status"] == "failure":
            assert not any(RESTRICTION_MARKER in e for e in result["errors"])


class TestScanForbiddenCandidates:
    """_scan_forbidden_candidates flags only candidate-declaration lines."""

    def test_add_lens_flagged(self):
        md = (
            "## Phase 1\nsome visual text\n\n"
            "# Beam Action Candidates (branch=A, parent=A.1, depth=2)\n\n"
            "## Candidate 1\n- **action_id**: A-A.1-cand-1\n- **primitives**:\n"
            "  1. add(Lens, n=0.3 free, q=0.8, [Re_min, Re_init, Re_max]=[4,5,6]px)\n"
        )
        hits = _scan_forbidden_candidates(md)
        assert len(hits) == 1
        assert "add(Lens" in hits[0]

    def test_add_companion_and_tune_companion_flagged(self):
        md = (
            "# Beam Action Candidates\n"
            "## Candidate 1\n  1. add(Companion, x=95, y=128, psf)\n"
            "## Candidate 2\n  1. tune(companion, x_real=97, y_real=130)\n"
        )
        hits = _scan_forbidden_candidates(md)
        assert len(hits) == 2

    def test_edgedisk_switch_flagged(self):
        md = (
            "# Beam Action Candidates\n"
            "## Candidate 1\n  1. tune(Disk, →edgedisk)\n"
            "## Candidate 2\n  1. tune(Disk, ->edgedisk)\n"
        )
        hits = _scan_forbidden_candidates(md)
        assert len(hits) == 2

    def test_outerdisk_add_flagged(self):
        md = (
            "# Beam Action Candidates\n"
            "## Candidate 1\n  1. add(OuterDisk, n=0.8 free, Re_init=40px)\n"
        )
        assert len(_scan_forbidden_candidates(md)) == 1

    def test_phase1_diagnostic_text_not_flagged(self):
        # Phase-1 diagnostic descriptions (before the candidates heading) are knowledge,
        # not actions — no false positives allowed.
        md = (
            "## Phase 1\n"
            "- a lens-type bump was observed at r~12px (outside this branch's action "
            "space, left unfitted); a companion-type bright blob sits at px(95,128).\n"
            "- the residual shows an embedded-companion signature\n\n"
            "# Beam Action Candidates (branch=A, parent=A.1, depth=2)\n\n"
            "## Candidate 1\n- **primitives**:\n"
            "  1. add(Bulge, n=4 fixed, [Re_min, Re_init, Re_max]=[2,3,4]px)\n"
        )
        assert _scan_forbidden_candidates(md) == []

    def test_legal_candidates_not_flagged(self):
        md = (
            "# Beam Action Candidates\n"
            "## Candidate 1\n  1. add(Bar, n=0.5 fixed, q=0.3, PA=45)\n"
            "## Candidate 2\n  1. tune(disk, Re_init=30px)\n"
            "## Candidate 3\n  1. remove(agn)\n"
            "## Candidate 4\n  1. tune(bulge, n_free)\n"
        )
        assert _scan_forbidden_candidates(md) == []

    def test_no_candidates_section_returns_empty(self):
        md = "## Physicality Verdict\n- verdict: PASS\n- failed_checks: none\n- swap_hint: none\n"
        assert _scan_forbidden_candidates(md) == []
