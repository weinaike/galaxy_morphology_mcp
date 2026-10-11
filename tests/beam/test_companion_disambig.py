"""Tests for the Plate0300 fixes: companion name disambiguation and the
Re=?px display fallback."""

from __future__ import annotations

from beam.candidate_schema import (
    AddParams,
    AddPrimitive,
    Candidate,
    _disambiguate_companion_names,
    parse_survey_response,
)
from beam.digest import _comp_line


# ---------------------------------------------------- companion disambiguation
def _resp_with_add(name: str, x: float = 114.0, y: float = 93.0) -> Candidate:
    return Candidate(
        action_id="X.1-c1",
        primitives=[AddPrimitive(op="add", add=AddParams(
            structure_name=name, component_type="psf", mag=19.5, x_px=x, y_px=y))],
        physical_motivation="test", expected_C_prime="{disk,companion}",
        expected_behavior_tag="add_companion", local_benefit_sigma=0.6)


def test_second_companion_add_is_renamed():
    parent = [{"name": "companion", "type": "psf", "x": 104, "y": 143,
               "mag": 20.1, "re": None, "n": None, "ba": None, "pa": None,
               "toggles": {}}]
    cand = _resp_with_add("companion")
    resp = parse_survey_response({
        "physicality_verdict": {"verdict": "PASS"},
        "candidates": [c.model_dump(exclude_none=True) for c in [cand]]})[0]
    _disambiguate_companion_names(resp, parent)
    add = resp.candidates[0].primitives[0].add
    assert add.structure_name == "companion2"
    assert add.x_px == 114.0 and add.y_px == 93.0   # verbatim physical intent


def test_first_companion_add_unchanged():
    parent = [{"name": "disk", "type": "expdisk"}]
    cand = _resp_with_add("companion")
    resp = parse_survey_response({
        "physicality_verdict": {"verdict": "PASS"},
        "candidates": [c.model_dump(exclude_none=True) for c in [cand]]})[0]
    _disambiguate_companion_names(resp, parent)
    assert resp.candidates[0].primitives[0].add.structure_name == "companion"


def test_companion2_collision_takes_companion3():
    parent = [{"name": "companion"}, {"name": "companion2"}]
    cand = _resp_with_add("companion")
    resp = parse_survey_response({
        "physicality_verdict": {"verdict": "PASS"},
        "candidates": [c.model_dump(exclude_none=True) for c in [cand]]})[0]
    _disambiguate_companion_names(resp, parent)
    assert resp.candidates[0].primitives[0].add.structure_name == "companion3"


def test_non_companion_duplicate_not_renamed():
    # single-slot duplicates stay an error (handled by the strict check)
    parent = [{"name": "bulge", "type": "sersic"}]
    cand = Candidate(
        action_id="X.1-c1",
        primitives=[AddPrimitive(op="add", add=AddParams(
            structure_name="bulge", component_type="sersic",
            mag=18.0, re_px=2.0, x_px=128, y_px=129))],
        physical_motivation="t", expected_C_prime="{disk,bulge}",
        expected_behavior_tag="dup_bulge", local_benefit_sigma=0.5)
    resp = parse_survey_response({
        "physicality_verdict": {"verdict": "PASS"},
        "candidates": [c.model_dump(exclude_none=True) for c in [cand]]})[0]
    _disambiguate_companion_names(resp, parent)
    assert resp.candidates[0].primitives[0].add.structure_name == "bulge"


# ------------------------------------------------------------ Re display fix
def test_comp_line_re_fallback_sersic():
    c = {"name": "bulge", "type": "sersic", "re": 2.5, "mag": 18.0}
    line = _comp_line(c)
    assert "Re=2.5px" in line and "M=18" in line


def test_comp_line_re_fallback_expdisk_effective():
    # raw 're' holds Rs=6.0 -> effective 10.08 is displayed (px contract)
    c = {"name": "disk", "type": "expdisk", "re": 6.0, "mag": 16.0}
    assert "Re=10.08px" in _comp_line(c)
