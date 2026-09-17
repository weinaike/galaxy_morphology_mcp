"""Tests for the candidate-absence watchdog (Plate0300 lens_relax_d incident)
and the survey_notes consumption in build_local_state_description."""

from __future__ import annotations

from beam.candidate_schema import (
    AddParams,
    AddPrimitive,
    Candidate,
    TuneDelta,
    TunePrimitive,
)
from beam.survey import _candidate_absence_watchdog


class _G:
    """Minimal graph stub: state(label) with an inventory + cons_effective."""

    def __init__(self, inventory=None, cons_effective=None):
        class _Inner:
            pass

        self.g = _Inner()
        self.g.graph = {"decision_log": []}
        self._state = {"inventory": inventory or [], "cons_effective": cons_effective or {}}

    def state(self, label):
        return self._state

    def log_decision(self, e):
        self.g.graph["decision_log"].append(e)


def _cand(*prims, motivation="residuals motivate X"):
    return Candidate(
        action_id="X.1-c1", primitives=list(prims),
        physical_motivation=motivation, expected_C_prime="{disk}",
        expected_behavior_tag="t1", local_benefit_sigma=0.5)


def _lens_tune(re_band=None):
    cb = {"re": list(re_band)} if re_band else None
    return TunePrimitive(op="tune", tune=TuneDelta(
        structure_name="lens", param="re_px", value=20.0, cons_bounds=cb))


def _bar_add():
    return AddPrimitive(op="add", add=AddParams(
        structure_name="bar", component_type="sersic",
        mag=17.5, re_px=6.0, q=0.35, pa_deg=45.0, x_px=128, y_px=128))


_LENS_STATE = [{"name": "lens", "type": "sersic", "number": 5, "re": 17.0,
                "mag": 17.9, "n": 0.1, "q": 0.7, "pa": 30.0, "x": 128, "y": 128,
                "toggles": {}}]


class _R:
    def __init__(self, cands):
        self.candidates = cands


def test_lens_relax_d_absence_logged_and_queued():
    g = _G(inventory=_LENS_STATE, cons_effective={"5.re": (9.5, 17.0)})
    cands = [_cand(_lens_tune(re_band=(9.0, 15.0)),   # tighten = path A, not D
                   motivation="Path A: confine the lens to the zone")]
    notes = _candidate_absence_watchdog(g, _R(cands), {"lens_relax_d": True}, "A.5", "s1")
    assert notes and "lens_relax_d" in notes[0]
    assert g.g.graph["survey_notes"] == notes
    assert any(e.get("kind") == "vlm-candidate-absence"
               and e.get("missing") == "lens_relax_d"
               for e in g.g.graph["decision_log"])


def test_lens_relax_d_present_no_note():
    g = _G(inventory=_LENS_STATE, cons_effective={"5.re": (9.5, 17.0)})
    cands = [_cand(_lens_tune(re_band=(9.5, 22.1)),
                   motivation="Path D: relax the self-imposed cap")]
    notes = _candidate_absence_watchdog(g, _R(cands), {"lens_relax_d": True}, "A.5", "s1")
    assert notes == [] and "survey_notes" not in g.g.graph


def test_explicit_waiver_suppresses_note():
    g = _G(inventory=_LENS_STATE, cons_effective={"5.re": (9.5, 17.0)})
    cands = [_cand(_bar_add(),
                   motivation="the lens re_max relaxation is waived: the flat "
                              "degeneration means relaxing would feed an envelope")]
    notes = _candidate_absence_watchdog(g, _R(cands), {"lens_relax_d": True}, "A.5", "s1")
    assert notes == []


def test_flat_bulge_bar_absence():
    g = _G()
    cands = [_cand(_lens_tune(), motivation="lens focus")]
    notes = _candidate_absence_watchdog(g, _R(cands), {"flat_bulge_bar": True}, "A.7", "s2")
    assert any("flat_bulge_bar" in n for n in notes)


def test_notes_consumed_by_local_description(tmp_path):
    from beam.graph import BeamGraph
    from beam.digest import build_local_state_description

    gd = tmp_path / "G"
    gd.mkdir()
    feedme = gd / "root.feedme"
    feedme.write_text(
        "A) image.fits\nG) none\nH) 1 256 1 256\n"
        "# Component number: 1\n# STRUCTURE: singlesersic\n0) sersic\n"
        "1) 128 128 1 1\n3) 16.0 1\n4) 7.0 1\n5) 2 1\n9) 0.7 1\n10) 0 1\nZ) 0\n",
        encoding="utf-8")
    graph = BeamGraph.init(str(gd), str(feedme), stage1={}, psf_fwhm_px=2.0)
    graph.g.graph["survey_notes"] = ["Previous-round candidate-absence notice: test"]
    desc, _trig = build_local_state_description(graph, "A.0")
    assert "candidate-absence notice: test" in desc
    assert graph.g.graph["survey_notes"] == []      # consume-once
    desc2, _ = build_local_state_description(graph, "A.0")
    assert "notice: test" not in desc2
