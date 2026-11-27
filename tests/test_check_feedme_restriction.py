"""Unit tests for the branch solution-space restriction (restrict-disk-bulge-bar-agn).

Covers the mechanical gate in ``tools/beam_actions_galfit``:
``check_feedme_file`` rejects feedmes containing a forbidden component
(Companion / Lens / OuterDisk / edgedisk) — errors, so the orchestrator
fixes the structure before any ``run_galfit`` call. (The retired
``generate_galfit_beam_actions`` markdown scanner was deleted with the tool.)
"""

from pathlib import Path

from tools.beam_actions_galfit import check_feedme_file

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
        if name == "disk":
            # the disk mandate: sersic with n=1 FIXED
            lines += [
                " 5) 1.0      0",
                " 6) 0.0      0",
                " 7) 0.0      0",
                " 8) 0.0      0",
                " 9) 0.7      1",
                "10) 30.0     1",
            ]
        else:
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
            [("disk", "sersic"), ("bulge", "sersic"), ("bar", "sersic"), ("agn", "psf")],
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



# ------------------- branch generation gate (candidate_schema, post-merge)
def test_candidate_schema_restricted_generation():
    """add/tune/remove targeting forbidden slots are E_RESTRICTED_SPACE;
    recognition stays free (no gate on descriptions — only on actions)."""
    import sys
    sys.path.insert(0, "src")
    from beam.candidate_schema import validate_candidate, parse_survey_response

    def codes(prims):
        payload = {                   "candidates": [{"primitives": prims,
                                   "physical_motivation": "m",
                                   "expected_C_prime": "c",
                                   "novelty_claim": "n",
                                   "expected_behavior_tag": "t",
                                   "pred_bic": 100.0}]}
        resp, _ = parse_survey_response(payload)
        parent = [{"name": "disk", "type": "expdisk"},
                  {"name": "bulge", "type": "sersic"}]
        return [i.code for i in validate_candidate(
            resp.candidates[0], parent, {}, 2, 1, [], [], [])]

    assert "E_RESTRICTED_SPACE" in codes(
        [{"op": "add", "add": {"structure_name": "lens",
                               "component_type": "sersic", "re_px": 8.0}}])
    assert "E_RESTRICTED_SPACE" in codes(
        [{"op": "add", "add": {"structure_name": "companion",
                               "component_type": "psf"}}])
    assert "E_RESTRICTED_SPACE" in codes(
        [{"op": "add", "add": {"structure_name": "outerdisk",
                               "component_type": "sersic", "re_px": 30.0}}])
    assert "E_RESTRICTED_SPACE" in codes(
        [{"op": "tune", "tune": {"structure_name": "lens", "param": "q",
                                  "value": 0.7}}])
    # legal targets unaffected (agn admission aside)
    assert "E_RESTRICTED_SPACE" not in codes(
        [{"op": "add", "add": {"structure_name": "bar",
                               "component_type": "sersic", "re_px": 10.0,
                               "n": 0.5}}])
