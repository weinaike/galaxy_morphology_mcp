"""Tests for beam_mb.backend — the multi-band format backend (P2).

Fixtures (real kilogas_mb artifacts):
  mb_root.lyric            KILOGAS_110 root config (single obj0 sersic)
  mb_iter3.lyric/.gssummary KILOGAS_110 iter3 (disk/bulge/bar, 3 bands) +
                           component_attributes.txt for mag cross-check
  mb_iter7_agn.lyric/.gssummary KILOGAS_110 iter7 (N-block AGN run; also
                           the Aa2 single-band pathology)
"""

import pytest

from beam.graph import BeamGraph, _metric_key
from beam_mb.backend import (
    METRIC_KEYS, artifacts, decode_cons_effective, parse_fit, parse_root,
)
from beam_mb.lyric_doc import LyricError, parse_lyric_doc

MB = "mb"


def _p(td, name):
    return str(td / MB / name)


# ------------------------------------------------------------------ parse_root
def test_parse_root_initial_inventory(test_data_dir):
    facts = parse_root(_p(test_data_dir, "mb_root.lyric"))
    assert facts["fit_region"] is None
    assert facts["bands"] == ["decam_g", "decam_r", "decam_z"]
    assert facts["center"] == (326.27075, -7.40769)
    (obj,) = facts["inventory"]
    assert obj["name"] == "singlesersic" and obj["type"] == "sersic"
    assert any("normalised" in n for n in facts["notes"])
    # initial values from the five-tuples, arcsec / sky-PA
    assert obj["re"] == 3.0 and obj["re_effective"] == 3.0
    assert obj["n"] == 2.5 and obj["q"] == 0.5 and obj["pa"] == 45.0
    assert obj["x"] == 0.0 and obj["y"] == 0.0
    assert obj["toggles"] == {"xcen": 1, "ycen": 1, "re": 1, "n": 1,
                              "pa": 1, "q": 1}


def test_parse_root_rejects_invariant_violations(test_data_dir):
    with pytest.raises(LyricError, match="strict invariants"):
        parse_root(_p(test_data_dir, "mb_agn_dangling_band.lyric"))


# ------------------------------------------------------------------ parse_fit
def test_parse_fit_iter3_fitted_inventory(test_data_dir):
    run = {"input_param_file": _p(test_data_dir, "mb_iter3.lyric"),
           "output_param_file": _p(test_data_dir, "mb_iter3.gssummary"),
           "fit_statistics": {"bic": 546297.625, "reduced_chisq": 0.5885,
                              "n_free": 26, "n_data": 927762}}
    parsed = parse_fit(run)
    fitted = {c["name"]: c for c in parsed["fitted_inv"]}
    assert list(fitted) == ["disk", "bulge", "bar"]
    # structural params arcsec / sky-PA straight from the gssummary names
    d = fitted["disk"]
    assert d["re"] == pytest.approx(6.2057, abs=1e-3)
    assert d["n"] == pytest.approx(1.0)          # fixed n=1 disk (fixed section)
    assert d["q"] == pytest.approx(0.4967, abs=1e-3)
    assert d["pa"] == pytest.approx(-54.6316, abs=1e-3)
    assert d["x"] == pytest.approx(-0.2616, abs=1e-3)
    assert fitted["bulge"]["re"] == pytest.approx(0.1310, abs=1e-3)
    assert fitted["bar"]["n"] == pytest.approx(0.5)
    # per-band mags via zp - 2.5*(logM + logNorm), cross-checked against
    # component_attributes.txt (disk g = 16.2989 there)
    assert d["mags"]["decam_g"] == pytest.approx(16.2989, abs=0.01)
    assert d["mags"]["decam_z"] == pytest.approx(15.0386, abs=0.01)
    assert d["mag"] == d["mags"]["decam_g"]      # reference band = first
    # input view = five-tuple initials (iter3's transcription rounded the
    # parent fitted value: init 6.208 vs fitted 6.2057)
    inp = {c["name"]: c for c in parsed["input_inv"]}
    assert inp["disk"]["re"] == pytest.approx(6.208, abs=1e-3)
    assert inp["bulge"]["re"] == pytest.approx(0.3144, abs=1e-3)
    # metrics
    m = parsed["metrics"]
    assert m["bic"] == pytest.approx(546297.625)
    assert m["chi2_nu"] == pytest.approx(0.5885, abs=1e-4)
    assert _metric_key(m) == m["bic"]


def test_parse_fit_iter7_agn_pseudo_component(test_data_dir):
    run = {"input_param_file": _p(test_data_dir, "mb_iter7_agn.lyric"),
           "output_param_file": _p(test_data_dir, "mb_iter7_agn.gssummary"),
           "fit_statistics": {"bic": 183170.5}}
    parsed = parse_fit(run)
    fitted = {c["name"]: c for c in parsed["fitted_inv"]}
    agn = fitted["agn"]
    assert agn["type"] == "psf" and agn["re"] is None
    assert agn["x"] == pytest.approx(0.1069, abs=1e-3)   # xcen_AGN
    assert agn["y"] == pytest.approx(0.0432, abs=1e-3)
    assert fitted["disk"]["re"] == pytest.approx(6.4211, abs=1e-3)


def test_parse_fit_requires_lyric(test_data_dir):
    with pytest.raises(LyricError, match="input lyric missing"):
        parse_fit({"output_param_file": _p(test_data_dir, "mb_iter3.gssummary")})


# ------------------------------------------------------- decode_cons_effective
def test_decode_cons_effective_five_tuple_bands(test_data_dir):
    bands = decode_cons_effective(_p(test_data_dir, "mb_iter3.lyric"), [])
    assert bands["a.re"] == (0.08, 90)          # disk Re band
    assert bands["c.n"] == (0.4, 0.6)           # bar n=0.5 fixed, non-degenerate
    assert bands["b.q"] == (0.6, 1.0)
    assert "a.m" not in bands                   # plain-value rows carry no band
    sf = decode_cons_effective(_p(test_data_dir, "mb_sersic_f.lyric"), [])
    assert sf["a.am"] == (0.0, 0.5) and sf["a.r_in"] == (0.3, 4.0)


def test_artifacts_mb_keys(test_data_dir, tmp_path):
    import shutil

    # canonical-name archive dir (discovery looks for component_attributes.txt
    # next to the gssummary — the fixture copy is renamed, so rebuild the dir)
    arch = tmp_path / "20260101_000000__iter3"
    arch.mkdir()
    shutil.copy(test_data_dir / MB / "mb_iter3.gssummary", arch / "G.gssummary")
    shutil.copy(test_data_dir / MB / "mb_iter3_component_attributes.txt",
                arch / "component_attributes.txt")
    run = {"input_param_file": _p(test_data_dir, "mb_iter3.lyric"),
           "output_param_file": str(arch / "G.gssummary"),
           "image_file": "cmp.png", "round_status_file": "rs.json"}
    art = artifacts(run)
    assert set(art) == {"lyric", "gssummary", "component_attributes",
                        "comparison_png", "round_status"}
    assert art["component_attributes"] == str(arch / "component_attributes.txt")
    # and NOT discovered when absent
    bare = tmp_path / "bare"
    bare.mkdir()
    shutil.copy(test_data_dir / MB / "mb_iter3.gssummary", bare / "G.gssummary")
    art2 = artifacts({"input_param_file": "l", "output_param_file":
                      str(bare / "G.gssummary")})
    assert art2["component_attributes"] == ""


# ------------------------------------------------------- full record_fit dry run
def test_mb_record_fit_full_path(tmp_path, test_data_dir):
    """BeamGraph(mode=galfits).init + record_fit over real iter3 artifacts —
    the P2 acceptance in miniature (full corpus replay: scripts/replay_mb)."""
    gdir = tmp_path / "MB110"
    gdir.mkdir()
    import shutil
    shutil.copy(test_data_dir / MB / "mb_root.lyric", gdir / "obj.lyric")
    graph = BeamGraph.init(str(gdir), str(gdir / "obj.lyric"),
                           stage1={"morphology": "mb test"},
                           mode="galfits")
    assert graph.mode == "galfits"
    root = graph.state("A.0")
    assert [c["name"] for c in root["inventory"]] == ["singlesersic"]

    label = graph.record_fit({
        "input_param_file": _p(test_data_dir, "mb_iter3.lyric"),
        "output_param_file": _p(test_data_dir, "mb_iter3.gssummary"),
        "image_file": "cmp.png",
        "round_status_file": str(gdir / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic": 546297.625, "reduced_chisq": 0.5885,
                           "n_free": 26, "n_data": 927762},
    })
    node = graph.state(label)
    names = [c["name"] for c in node["inventory"]]
    assert names == ["disk", "bulge", "bar"]
    assert node["metrics"]["bic"] == pytest.approx(546297.625)
    assert node["artifacts"]["lyric"].endswith("mb_iter3.lyric")
    assert node["artifacts"]["gssummary"].endswith("mb_iter3.gssummary")
    assert node["cons_effective"]["c.n"] == (0.4, 0.6)
    # mech checks ran (shared physicality on the arcsec inventory) — and the
    # existence floors genuinely fire on iter3's starved bulge/bar (0.8%/0.2%
    # flux in the reference band): the shared gate is live in mb mode, which
    # the old agent-driven flow never checked
    mech = node.get("mech_checks")
    assert isinstance(mech, list)
    assert any(m["check"] == "flux_share" and m["severity"] == "hard" for m in mech)
    # a VLM PASS verdict is mech-vetoed (existence floor) — best stays unset
    assert graph.g.graph["best_state"] is None
    graph.apply_verdict(label, {"verdict": "PASS", "failed_checks": []})
    assert graph.state(label)["verdict"]["verdict"] == "FAIL"
    assert graph.state(label)["verdict"].get("mech_veto") is True
    assert graph.g.graph["best_state"] is None


def test_run_result_from_flat_list_outputs(tmp_path, test_data_dir):
    """Regression (real-data 2026-11-03): this machine's run_galfits writes
    round_status.json with ``outputs`` as a FLAT PATH LIST (the
    existing_artifacts result), not the dict keyed summary_files/
    comparison_png. The by-reference builder must sniff the mb artifacts
    out of the list — beam_record_fit crashed with "'list' object has no
    attribute 'get'" before the sniff."""
    import json as _json

    import shutil

    from beam.tools import _run_result_from_round_status, _validate_run_result

    arch = tmp_path / "20261103_161946__iter1"
    arch.mkdir()
    shutil.copy(test_data_dir / MB / "mb_iter3.lyric", arch / "_iter1.lyric")
    shutil.copy(test_data_dir / MB / "mb_iter3.gssummary", arch / "G.gssummary")
    shutil.copy(test_data_dir / MB / "mb_iter3_component_attributes.txt",
                arch / "component_attributes.txt")
    (arch / "all_bands_comparison.png").write_bytes(b"png")
    rs_path = arch / "round_status.json"
    rs_path.write_text(_json.dumps({
        "stage": "galfits_finished", "status": "success",
        "inputs": {"lyric": str(arch / "_iter1.lyric")},
        "outputs": [str(arch / "G.gssummary"),
                    str(arch / "all_bands_comparison.png"),
                    str(arch / "component_attributes.txt")],
        "fit_statistics": {"reduced_chisq": 0.91, "bic": 1088963.625},
    }))
    rr = _run_result_from_round_status(str(rs_path))
    assert rr["input_param_file"].endswith("_iter1.lyric")
    assert rr["output_param_file"].endswith("G.gssummary")
    assert rr["image_file"].endswith("all_bands_comparison.png")
    assert rr["summary_file"].endswith("G.gssummary")
    assert _validate_run_result(rr, "") is None
    from beam_mb.backend import parse_fit
    parsed = parse_fit(rr)
    assert parsed["metrics"]["bic"] == 1088963.625
    assert parsed["metrics"]["chi2_nu"] == 0.91


def test_parse_root_normalises_starter_name(test_data_dir):
    """obj0 -> singlesersic at the GRAPH level (the mb analog of galfit's
    root.feedme name-insertion — GalfitS keeps names in P1 rows, so no
    file rewrite: the root file itself stays untouched)."""
    r = parse_root(_p(test_data_dir, "mb_root.lyric"))
    (obj,) = r["inventory"]
    assert obj["name"] == "singlesersic"
    assert any("normalised" in n for n in r["notes"])
    # the FILE is untouched: reparsing still sees obj0, and re-normalising
    # is idempotent at the graph layer
    from beam_mb.lyric_doc import parse_lyric_doc
    assert parse_lyric_doc(_p(test_data_dir, "mb_root.lyric")) \
        .components[0].name == "obj0"
    # a meaningful name survives as-is (no false renames)
    import shutil as _sh
    import pathlib as _pl
    named = _pl.Path(_p(test_data_dir, "mb_root.lyric")).read_text() \
        .replace("Pa1) obj0", "Pa1) disk")
    t = _pl.Path(_p(test_data_dir, "mb_root.lyric")).parent
    # (in-place fixture editing is unsafe — parse from text via tmp file)
    import tempfile as _tf
    with _tf.NamedTemporaryFile("w", suffix=".lyric", delete=False) as fh:
        fh.write(named)
        tmp_named = fh.name
    r2 = parse_root(tmp_named)
    assert r2["inventory"][0]["name"] == "disk" and not r2["notes"]


def test_parconstrain_auto_discovery(tmp_path):
    """The image-SED joint phase forgot --parconstrain (real-data
    2026-11-04): the SED-generated config lives in a fresh output
    workplace with no sibling constrain. The runner auto-discovers the
    source round's file — explicit extra_args still win, K<=1 rounds
    attach nothing."""
    from tools.run_galfits import _discover_parconstrain

    gal = tmp_path / "G"
    (gal / "output" / "20261104_120000__iter7_sed").mkdir(parents=True)
    (gal / "_iter7.lyric").write_text("R1) x")
    (gal / "iter7.constrain").write_text("def U(p): pass")

    # image-fitting config: sibling discovered
    assert _discover_parconstrain(str(gal / "_iter7.lyric")) == \
        str(gal / "iter7.constrain")
    # SED joint config in the fresh workplace: galaxy-root file found via
    # the iter{N} extracted from the stem
    joint = gal / "output" / "20261104_120000__iter7_sed" / \
        "_iter7_for_image_sed_fitting.lyric"
    joint.write_text("R1) x")
    assert _discover_parconstrain(str(joint)) == str(gal / "iter7.constrain")
    # K<=1 round: no constrain exists -> nothing attached
    (gal / "_iter2.lyric").write_text("R1) x")
    assert _discover_parconstrain(str(gal / "_iter2.lyric")) is None
