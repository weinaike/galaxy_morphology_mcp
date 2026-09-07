"""Tests for digest.py (global/local descriptions, triggers, queue digest)
and the additive Prompts loaders for the survey prompt file."""

from beam.digest import (
    build_global_state_description,
    build_local_state_description,
    build_queue_digest,
)


def test_global_digest_sections_and_line_budget(mini_graph):
    graph, label = mini_graph
    text = build_global_state_description(graph)
    for section in ("[Meta]", "[Stage-1 conclusions]", "[State ledger",
                    "[Rollback edges]", "[Verified basins]", "[Refuted hypotheses]",
                    "[Budget]", "[Temporary constraints]"):
        assert section in text, f"missing {section}"
    assert len(text.splitlines()) <= 60
    assert label in text  # ledger line for A.1
    assert "BIC_eff" in text


def test_global_digest_temporary_constraint_verbatim(mini_graph):
    graph, _ = mini_graph
    graph.g.graph["temporary_constraints"] = [
        {"issued": "2026-09-04", "text": "companion exclusion", "active": True,
         "forbid_structures": ["companion"]},
        {"issued": "2026-08-01", "text": "old", "active": False,
         "forbid_structures": []},
    ]
    text = build_global_state_description(graph)
    assert "companion exclusion" in text
    assert "old" not in text  # inactive entries dropped


def test_local_digest_companion_condition_a(tmp_path, test_data_dir):
    import shutil

    from beam.graph import BeamGraph

    gdir = tmp_path / "COMP"
    gdir.mkdir()
    feedme = gdir / "f.feedme"
    feedme.write_text(_COMPANION_FEEDME, encoding="utf-8")
    graph = BeamGraph.init(str(gdir), str(feedme), stage1={})
    label = graph.record_fit({
        "input_param_file": str(feedme), "output_param_file": str(feedme),
        "image_file": "", "summary_file": "", "round_status_file": "",
        "fit_statistics": {"bic_eff": 900.0, "convergence": {"flag": "ok"}}})
    text, triggers = build_local_state_description(graph, label)
    assert "Companion condition A hit" in text
    assert "flux ratio=0.44%" in text
    # injectable fields default to 'not assessed'
    assert "outer residual sign" in text and "not assessed" in text
    assert triggers == {}


_COMPANION_FEEDME = """A) image.fits
B) out.fits
G) none
H) 1 297 1 297
# Component number: 1
# STRUCTURE: disk
0) expdisk
1) 148.0 148.0 1 1
3) 16.0 1
4) 12.0 1
9) 0.7 1
10) 25.0 1
Z) 0

# Component number: 2
# STRUCTURE: companion
0) sersic
1) 200.0 148.0 1 1
3) 21.9 1
4) 2.0 1
5) 1.0 1
9) 0.9 1
10) 25.0 1
Z) 0

# Component number: 3
0) sky
1) 0.0004 0
Z) 0
"""


def test_local_digest_lens_inflation_trigger(tmp_path):
    import os

    from beam.graph import BeamGraph

    gdir = tmp_path / "LENS"
    gdir.mkdir()
    feedme = gdir / "f.feedme"
    feedme.write_text(_LENS_FEEDME, encoding="utf-8")
    cons = gdir / "iter1.cons"
    # self-imposed tight cap: lens(3) re 0.1 to 9.0 with default cap 148.5
    cons.write_text("3  re  0.1 to 9.0\n2  n  0.1 to 8\n", encoding="utf-8")
    feedme_text = feedme.read_text().replace("G) none", "G) iter1.cons")
    feedme.write_text(feedme_text, encoding="utf-8")

    graph = BeamGraph.init(str(gdir), str(feedme), stage1={},
                           psf_fwhm_px=4.0, a_psf_px2=12.57)
    label = graph.record_fit({
        "input_param_file": str(feedme),
        "output_param_file": str(feedme),   # fitted == input for the scan
        "image_file": "", "summary_file": "", "round_status_file": "",
        "fit_statistics": {"bic_eff": 950.0, "convergence": {"flag": "ok"}}})
    text, triggers = build_local_state_description(graph, label)
    # lens Re 8.9 at the 9.0 cap; ratio 8.9/20.2 = 0.44 < 0.85; ΔMag 2.5 > 0.2
    # -> D armed (self-imposed cap, no degeneracy)
    assert "Lens Re inflation signal" in text
    assert "self-imposed" in text
    assert triggers.get("lens_relax_d") is True
    assert "Bound-hit parameter list" in text  # effective-band scan ran


_LENS_FEEDME = """A) image.fits
B) out.fits
G) none
H) 1 297 1 297
# Component number: 1
# STRUCTURE: disk
0) expdisk
1) 148.0 148.0 1 1
3) 16.0 1
4) 12.0 1
9) 0.7 1
10) 25.0 1
Z) 0

# Component number: 2
# STRUCTURE: bulge
0) sersic
1) 148.0 148.0 1 1
3) 18.0 1
4) 2.0 1
5) 4.0 1
9) 0.9 1
10) 25.0 1
Z) 0

# Component number: 3
# STRUCTURE: lens
0) sersic
1) 148.0 148.0 1 1
3) 18.5 1
4) 8.9 1
5) 0.3 1
9) 0.85 1
10) 25.0 1
Z) 0

# Component number: 4
0) sky
1) 0.0004 0
Z) 0
"""


def test_local_digest_flat_bulge_trigger(tmp_path):
    from beam.graph import BeamGraph

    gdir = tmp_path / "FLAT"
    gdir.mkdir()
    feedme = gdir / "f.feedme"
    feedme.write_text(_FLAT_FEEDME, encoding="utf-8")
    graph = BeamGraph.init(str(gdir), str(feedme), stage1={})
    label = graph.record_fit({
        "input_param_file": str(feedme), "output_param_file": str(feedme),
        "image_file": "", "summary_file": "", "round_status_file": "",
        "fit_statistics": {"bic_eff": 940.0, "convergence": {"flag": "ok"}}})
    text, triggers = build_local_state_description(graph, label)
    # bulge q=0.3 <0.5, |PA diff|=45>20, n=1.2 free in (0.5,2.5), disk q=0.9>0.5
    assert "Flat-Bulge->Bar trigger values" in text
    assert "HOLDS" in text
    assert triggers.get("flat_bulge_bar") is True


_FLAT_FEEDME = """A) image.fits
B) out.fits
G) none
H) 1 297 1 297
# Component number: 1
# STRUCTURE: disk
0) expdisk
1) 148.0 148.0 1 1
3) 16.0 1
4) 12.0 1
9) 0.9 1
10) 25.0 1
Z) 0

# Component number: 2
# STRUCTURE: bulge
0) sersic
1) 148.0 148.0 1 1
3) 18.0 1
4) 3.0 1
5) 1.2 1
9) 0.3 1
10) 70.0 1
Z) 0

# Component number: 3
0) sky
1) 0.0004 0
Z) 0
"""


def test_local_digest_signal_b_trajectory(tmp_path):
    from beam.graph import BeamGraph

    gdir = tmp_path / "TRAJ"
    gdir.mkdir()
    graph = None
    for i, bar_mag in enumerate((17.52, 17.96, 18.14)):
        feedme = gdir / f"f{i}.feedme"
        feedme.write_text(_bar_feedme(bar_mag), encoding="utf-8")
        if graph is None:
            graph = BeamGraph.init(str(gdir), str(feedme), stage1={})
            label = graph.record_fit({
                "input_param_file": str(feedme), "output_param_file": str(feedme),
                "image_file": "", "summary_file": "", "round_status_file": "",
                "fit_statistics": {"bic_eff": 1000.0 - i, "convergence": {"flag": "ok"}}})
        else:
            label = graph.record_fit({
                "input_param_file": str(feedme), "output_param_file": str(feedme),
                "image_file": "", "summary_file": "", "round_status_file": "",
                "fit_statistics": {"bic_eff": 1000.0 - i, "convergence": {"flag": "ok"}}},
                parent_label=label)
    text, _triggers = build_local_state_description(graph, label)
    assert "Flux-misallocation signal B" in text
    assert "17.52" in text and "18.14" in text


def _bar_feedme(bar_mag):
    return f"""A) image.fits
B) out.fits
G) none
H) 1 297 1 297
# Component number: 1
# STRUCTURE: disk
0) expdisk
1) 148.0 148.0 1 1
3) 16.0 1
4) 12.0 1
9) 0.7 1
10) 25.0 1
Z) 0

# Component number: 2
# STRUCTURE: bar
0) sersic
1) 148.0 148.0 1 1
3) {bar_mag} 1
4) 5.0 1
5) 0.5 0
9) 0.3 1
10) 45.0 1
Z) 0

# Component number: 3
0) sky
1) 0.0004 0
Z) 0
"""


def test_queue_digest_lists_flags(mini_graph):
    graph, label = mini_graph
    from beam.candidate_schema import Candidate
    from beam.enqueue import ingest

    ingest(graph, [Candidate(primitives=[
        {"op": "tune", "tune": {"structure_name": "bulge", "param": "n",
                                "toggle": 1, "value": 4.0}}],
        physical_motivation="m", expected_C_prime="c", novelty_claim="n axis",
        expected_behavior_tag="n_free", local_benefit_sigma=0.2)],
        session_id="s1", parent_label=label)
    digest = build_queue_digest(graph)
    assert "n_free" in digest
    assert "floor_n_release" in digest


# ------------------------------------------------------- prompt file loading
def test_survey_prompt_loaders_render():
    from prompts import prompts

    ve = prompts.get_survey_visual_extraction(global_state_description="GS_TEST")
    assert "GS_TEST" in ve and "multimodal visual feature extraction" in ve
    cg = prompts.get_survey_candidate_generation(
        summary_content="SUM_TEST", global_state_description="GS2",
        local_state_description="LS_TEST", branch_id="A", parent_label="A.4",
        depth=3, queue_digest="QD_TEST", directives="DIR_TEST")
    for marker in ("SUM_TEST", "GS2", "LS_TEST", "QD_TEST", "DIR_TEST",
                   "physicality_verdict", "E_RE_CHAIN"):
        assert marker in cg, f"missing {marker}"
    # str.replace rendering leaves no braces mishandled: the JSON example intact
    assert '"physicality_verdict"' in cg
