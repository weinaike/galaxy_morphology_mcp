"""Tests for src/beam/candidate_schema.py (schema + rule engine, incident regressions)."""

import pytest
from pydantic import ValidationError

from beam.candidate_schema import (
    Candidate,
    Issue,
    SurveyResponse,
    parse_survey_response,
    validate_candidate,
    validate_survey,
)


def comp(name, ctype="sersic", **kw):
    d = {"name": name, "type": ctype, "x": 100.0, "y": 100.0, "mag": 17.0,
         "re": 6.0, "n": 2.0, "ba": 0.8, "pa": 30.0,
         "toggles": {"x": 1, "y": 1, "mag": 1, "re": 1, "n": 1, "ba": 1, "pa": 1}}
    d.update(kw)
    if ctype == "expdisk":
        d["re_effective"] = 1.68 * d["re"]
    else:
        d["re_effective"] = d["re"]
    return d


PARENT = [comp("disk", "expdisk", re=13.7, mag=16.2),   # Re_eff = 23.0
          comp("bulge", re=1.2, n=4.0, ba=0.9, mag=18.7, toggles={"n": 0})]


def make_cand(prims, **kw):
    payload = {
        "primitives": prims,
        "physical_motivation": "test",
        "expected_C_prime": "{disk,bulge,bar}",
        "expected_behavior_tag": "t1",
        "local_benefit_sigma": 0.6,
        "novelty_claim": "new structure {disk,bulge,bar}",
    }
    payload.update(kw)
    return Candidate(**payload)


def codes(issues):
    return [i.code for i in issues]


def _validate(cand, parent=PARENT, combo_counts=None, **kw):
    return validate_candidate(
        cand, parent, combo_counts or {}, 4, 3, [], [], kw.get("ledger", []))


# ------------------------------------------------------------------- schema
def test_parse_survey_response_valid():
    payload = {
        "physicality_verdict": {"verdict": "PASS", "failed_checks": [], "swap_hint": "none"},
        "candidates": [{
            "primitives": [{"op": "tune", "tune": {"structure_name": "bulge",
                                                   "param": "n", "toggle": 1, "value": 4.0}}],
            "physical_motivation": "m", "expected_C_prime": "{disk,bulge}",
            "novelty_claim": "n free untested", "expected_behavior_tag": "bulge_n_free",
            "local_benefit_sigma": 0.7,
        }],
    }
    resp, err = parse_survey_response(payload)
    assert err == "" and resp is not None
    assert resp.physicality_verdict.verdict == "PASS"
    assert resp.candidates[0].primitives[0].tune.param == "n"


def test_parse_survey_response_invalid():
    resp, err = parse_survey_response({"physicality_verdict": {"verdict": "MAYBE"}})
    assert resp is None and err


def test_candidate_primitive_bounds():
    with pytest.raises(ValidationError):
        Candidate(primitives=[], physical_motivation="m", expected_C_prime="c",
                  expected_behavior_tag="t", local_benefit_sigma=0.5)
    with pytest.raises(ValidationError):
        make_cand([{"op": "remove", "remove": "bar"}] * 3)
    with pytest.raises(ValidationError):
        make_cand([{"op": "remove", "remove": "bar"}], local_benefit_sigma=1.5)


# ------------------------------------------------------------------ alphabet
def test_alphabet_violations():
    c = make_cand([{"op": "add", "add": {"structure_name": "ring",
                                         "component_type": "sersic", "re_px": 5.0}}])
    assert "E_ALPHABET" in codes(_validate(c))

    c = make_cand([{"op": "add", "add": {"structure_name": "disk2",
                                         "component_type": "sersic", "re_px": 5.0}}])
    assert "E_ALPHABET" in codes(_validate(c))

    # a lens declared as psf violates the name->type mapping
    c = make_cand([{"op": "add", "add": {"structure_name": "lens",
                                         "component_type": "psf"}}])
    assert "E_ALPHABET" in codes(_validate(c))


def test_param_type_violations():
    # tune releasing the (nonexistent) disk n
    c = make_cand([{"op": "tune", "tune": {"structure_name": "disk", "param": "n",
                                           "toggle": 1, "value": 1.5}}])
    assert "E_PARAM_TYPE" in codes(_validate(c))

    # psf with invented shape params
    c = make_cand([{"op": "add", "add": {"structure_name": "agn", "component_type": "psf",
                                         "re_px": 2.0, "q": 0.9}}])
    assert "E_PARAM_TYPE" in codes(_validate(c))

    # tune on a target absent from the parent inventory
    c = make_cand([{"op": "tune", "tune": {"structure_name": "lens", "param": "q",
                                           "value": 0.8}}])
    assert "E_PARAM_TYPE" in codes(_validate(c))


def test_sky_touch_forbidden():
    c = make_cand([{"op": "tune", "tune": {"structure_name": "sky", "param": "mag",
                                           "value": 0.001}}])
    assert "E_SKY_TOUCH" in codes(_validate(c))
    c = make_cand([{"op": "remove", "remove": "sky"}])
    assert "E_SKY_TOUCH" in codes(_validate(c))


# -------------------------------------------------------------- multiplicity
def test_multiplicity():
    c = make_cand([{"op": "add", "add": {"structure_name": "bulge",
                                         "component_type": "sersic", "re_px": 5.0}}])
    assert "E_MULTIPLICITY" in codes(_validate(c))  # second bulge

    c = make_cand([{"op": "add", "add": {"structure_name": "edgedisk",
                                         "component_type": "edgedisk", "re_px": 30.0}}])
    assert "E_MULTIPLICITY" in codes(_validate(c))  # disk + edgedisk


# ------------------------------------------------- SingleSersic conversion
def test_single_conversion_bundling():
    single = [comp("singlesersic", re=12.0, n=2.5)]
    c = make_cand([{"op": "add", "add": {"structure_name": "bulge",
                                         "component_type": "sersic", "re_px": 2.0}}])
    assert "E_SINGLE_CONVERSION" in codes(_validate(c, parent=single))

    c = make_cand([
        {"op": "convert", "convert": {"from_name": "singlesersic", "to_name": "disk",
                                       "to_type": "expdisk"}},
        {"op": "add", "add": {"structure_name": "bulge", "component_type": "sersic",
                              "re_px": 2.0, "cons_bounds": {"re": [1.5, 3.0]}}},
    ])
    issues = _validate(c, parent=single)
    assert "E_SINGLE_CONVERSION" not in codes(issues)
    assert "E_RE_CHAIN" not in codes(issues)


# ----------------------------------------------------------------- Re chain
def test_re_chain_triplet_adjacency_incident():
    """Real incident: bulge Re=3.5, disk Re=23, bar triplet [2,4,8] invades the
    bulge; the correct triplet is [4.5,6,10]."""
    parent = [comp("disk", "expdisk", re=13.7), comp("bulge", re=3.5)]
    bad = make_cand([{"op": "add", "add": {
        "structure_name": "bar", "component_type": "sersic", "re_px": 4.0,
        "cons_bounds": {"re": [2.0, 8.0]}}}])
    assert "E_RE_CHAIN" in codes(_validate(bad, parent=parent))

    good = make_cand([{"op": "add", "add": {
        "structure_name": "bar", "component_type": "sersic", "re_px": 6.0,
        "cons_bounds": {"re": [4.5, 10.0]}}}])
    assert "E_RE_CHAIN" not in codes(_validate(good, parent=parent))


def test_re_chain_init_only():
    parent = [comp("disk", "expdisk", re=13.7), comp("bulge", re=3.5)]
    c = make_cand([{"op": "add", "add": {"structure_name": "lens",
                                         "component_type": "sersic", "re_px": 30.0}}])
    issues = _validate(c, parent=parent)
    assert any(i.code == "E_RE_CHAIN" and "outer" in i.message for i in issues)


def test_re_chain_3pct_tolerance():
    """Relaxed total order: an inner Re may exceed the adjacent outer Re by at
    most 3% without firing E_RE_CHAIN; beyond 3% still fires. Applies to both
    the hypo-pair check (tune) and the add-time initial-value adjacency.
    (comp(disk, expdisk, re=8.0) -> Re_effective = 13.44 px.)"""
    parent = [comp("disk", "expdisk", re=8.0), comp("bulge", re=3.5)]
    # hypo pair: bulge 13.7 vs disk 13.44 = +1.9% -> within tolerance
    within = make_cand([{"op": "tune", "tune": {"structure_name": "bulge",
                                                "param": "re_px", "value": 13.7}}])
    assert "E_RE_CHAIN" not in codes(_validate(within, parent=parent))
    # bulge 14.2 vs disk 13.44 = +5.7% -> beyond tolerance
    beyond = make_cand([{"op": "tune", "tune": {"structure_name": "bulge",
                                                "param": "re_px", "value": 14.2}}])
    assert "E_RE_CHAIN" in codes(_validate(beyond, parent=parent))
    # add-time adjacency: lens re_init 13.7 vs disk 13.44 = +1.9% -> passes
    add_within = make_cand([{"op": "add", "add": {"structure_name": "lens",
                                                  "component_type": "sersic",
                                                  "re_px": 13.7}}])
    assert "E_RE_CHAIN" not in codes(_validate(add_within, parent=parent))
    # lens re_init 15.0 vs disk 13.44 = +11.6% -> fires
    add_beyond = make_cand([{"op": "add", "add": {"structure_name": "lens",
                                                  "component_type": "sersic",
                                                  "re_px": 15.0}}])
    assert "E_RE_CHAIN" in codes(_validate(add_beyond, parent=parent))


# ----------------------------------------------------------------- combo cap
def test_combo_exhausted():
    counts = {"bar+bulge+disk": 4}
    parent = [comp("disk", "expdisk", re=13.7), comp("bulge", re=1.2), comp("bar", re=5.0)]
    c = make_cand([{"op": "tune", "tune": {"structure_name": "bar", "param": "pa_deg",
                                           "value": 90.0}}])
    assert "E_COMBO_EXHAUSTED" in codes(_validate(c, parent=parent, combo_counts=counts))


# ------------------------------------------------------------------- ledgers
def test_r1_ledger_equivalence_requires_novelty():
    from beam.signature import canonical_signature

    same = canonical_signature(PARENT)  # the tune below lands exactly on it
    c = make_cand([{"op": "tune", "tune": {"structure_name": "bulge", "param": "mag",
                                           "value": 18.7}}], novelty_claim="")
    assert "E_R1_LEDGER" in codes(_validate(c, ledger=[same]))
    c2 = make_cand([{"op": "tune", "tune": {"structure_name": "bulge", "param": "mag",
                                            "value": 18.7}}],
                   novelty_claim="≡A.3 but n free axis untested")
    assert "E_R1_LEDGER" not in codes(_validate(c2, ledger=[same]))


def test_r2_closed_form_warns():
    c = make_cand([{"op": "remove", "remove": "bulge"}])
    issues = _validate(c)
    assert any(i.code == "E_R2_EXACT" and i.severity == "warn" for i in issues)


# ------------------------------------------------------------ AGN admission
def test_agn_admission_rule():
    # parent bulge Re=2.0: clearly outside the collapse zone -> error
    parent = [comp("disk", "expdisk", re=13.7), comp("bulge", re=2.0)]
    c = make_cand([{"op": "add", "add": {"structure_name": "agn",
                                         "component_type": "psf"}}])
    assert "E_AGN_ADMISSION" in codes(_validate(c, parent=parent))

    # border zone 0.2-0.5px: competing variant, allowed
    parent = [comp("disk", "expdisk", re=13.7), comp("bulge", re=0.3)]
    assert "E_AGN_ADMISSION" not in codes(_validate(c, parent=parent))


# ------------------------------------------------------- companion timing
def test_embedded_companion_timing():
    # companion well inside 2*Re_disk (=46px) with no bulge/bar
    parent = [comp("disk", "expdisk", re=13.7)]
    c = make_cand([{"op": "add", "add": {"structure_name": "companion",
                                         "component_type": "sersic", "re_px": 2.0,
                                         "x_px": 110.0, "y_px": 100.0}}])
    assert "E_COMPANION_TIMING" in codes(_validate(c, parent=parent))
    # with a bulge established: allowed
    parent = [comp("disk", "expdisk", re=13.7), comp("bulge", re=1.2)]
    assert "E_COMPANION_TIMING" not in codes(_validate(c, parent=parent))
    # outer companion (r ~ 60px > 2*Re): allowed even without bulge
    c2 = make_cand([{"op": "add", "add": {"structure_name": "companion",
                                          "component_type": "sersic", "re_px": 2.0,
                                          "x_px": 160.0, "y_px": 100.0}}])
    assert "E_COMPANION_TIMING" not in codes(_validate(c2, parent=parent))


# ---------------------------------------------------- temporary constraints
def test_temporary_constraint_companion_exclusion():
    tc = [{"issued": "2026-09-04", "text": "companion exclusion", "active": True,
           "forbid_structures": ["companion"]}]
    c = make_cand([{"op": "add", "add": {"structure_name": "companion",
                                         "component_type": "sersic", "re_px": 2.0,
                                         "x_px": 160.0, "y_px": 100.0}}])
    assert "E_TEMP_CONSTRAINT" in codes(validate_candidate(
        c, PARENT, {}, 4, 3, [], tc, []))


# ------------------------------------------------------------- survey level
_GOOD_PAYLOAD = {
    "physicality_verdict": {"verdict": "PASS", "failed_checks": [], "swap_hint": "none"},
    "candidates": [
        {"primitives": [{"op": "tune", "tune": {"structure_name": "bulge",
                                                "param": "n", "toggle": 1, "value": 4.0}}],
         "physical_motivation": "m", "expected_C_prime": "{disk,bulge}",
         "novelty_claim": "n axis", "expected_behavior_tag": "a",
         "local_benefit_sigma": 0.6},
        {"primitives": [{"op": "tune", "tune": {"structure_name": "agn",
                                                "param": "mag", "value": 12.5}}],
         "physical_motivation": "m", "expected_C_prime": "{disk,bulge,agn}",
         "novelty_claim": "mag axis", "expected_behavior_tag": "b",
         "local_benefit_sigma": 0.5},
    ],
}


def test_validate_survey_counts_and_tags(mini_graph):
    import copy

    graph, label = mini_graph
    good = SurveyResponse(**copy.deepcopy(_GOOD_PAYLOAD))
    report = validate_survey(good, graph, label)
    assert report.ok, [i.message for i in report.errors]

    payload = copy.deepcopy(_GOOD_PAYLOAD)
    payload["candidates"][1]["expected_behavior_tag"] = "a"
    dup_tags = SurveyResponse(**payload)
    assert any(i.code == "E_TAG_DUP" for i in validate_survey(dup_tags, graph, label).errors)

    payload = copy.deepcopy(_GOOD_PAYLOAD)
    payload["candidates"][0]["queue_reorder"] = [{"action_id": "NOPE", "new_rank": 1}]
    reorder = SurveyResponse(**payload)
    assert any(i.code == "E_QUEUE_REORDER"
               for i in validate_survey(reorder, graph, label).errors)
