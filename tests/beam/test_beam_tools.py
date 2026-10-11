"""Tests for the stage-1 MCP tool wrappers (beam_init / beam_record_fit /
beam_mark_failure / beam_status) and their FastMCP registration."""

import asyncio
import importlib
import json
import shutil
import sys
from unittest.mock import patch

import pytest

from beam.tools import beam_init, beam_mark_failure, beam_record_fit, beam_status


@pytest.fixture
def galaxy(tmp_path, test_data_dir):
    gdir = tmp_path / "GALAXY"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    shutil.copy(test_data_dir / "NGC1097_clean.07", gdir / "galfit.01")
    shutil.copy(test_data_dir / "PSF-1.composite.fits", gdir / "PSF-1.composite.fits")
    return gdir


def _run_result(galaxy, *, bic_eff=1000.0):
    return {
        "status": "success",
        "input_param_file": str(galaxy / "_iter1.feedme"),
        "output_param_file": str(galaxy / "galfit.01"),
        "image_file": str(galaxy / "cmp.png"),
        "summary_file": str(galaxy / "summary.md"),
        "round_status_file": str(galaxy / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": bic_eff, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    }


def test_beam_init_record_status_roundtrip(galaxy):
    r = beam_init(
        str(galaxy), str(galaxy / "_iter1.feedme"),
        stage1_morphology="barred spiral",
        stage1_bar_lop_json=json.dumps({"bar": {"detected": True, "pa_deg": 45}}),
    )
    assert r["status"] == "success"
    assert r["root_state"] == "A.0"
    assert (galaxy / "beam_state" / "graph.json").exists()
    assert r["psf_fwhm_px"] is not None  # measured from the fixture PSF

    rr = beam_record_fit(str(galaxy), json.dumps(_run_result(galaxy)),
                         verdict_json=json.dumps({"verdict": "PASS"}))
    assert rr["status"] == "success"
    assert rr["new_state"] == "A.1"
    assert rr["is_best"] is True
    assert rr["counters"]["n_executed"] == 1

    st = beam_status(str(galaxy))
    assert st["status"] == "success"
    assert st["best_state"] == "A.1"
    assert st["termination"]["conditions"] == ["queue_empty"]
    assert st["termination"]["stop"] is True  # no blockers, no pending candidates


def test_beam_init_missing_paths(tmp_path):
    assert beam_init(str(tmp_path / "nope"), str(tmp_path / "nope.feedme"))["status"] == "failure"
    r = beam_init(str(tmp_path), str(tmp_path / "missing.feedme"))
    assert r["status"] == "failure"


def test_beam_record_fit_auto_fails_on_mech_hard(galaxy):
    """KILOGAS_231 incident regression: a driver that never settles verdicts
    (no survey_round, no verdict_json) must not bypass the physicality gate —
    a state carrying mech-hard entries is auto-settled FAIL at record time."""
    # make the fixture bar round (q=0.75 > 0.6) so a hard mech check surely fires
    fitted = galaxy / "galfit.01"
    fitted.write_text(fitted.read_text().replace("9) 0.3699", "9) 0.7500"),
                      encoding="utf-8")
    beam_init(str(galaxy), str(galaxy / "_iter1.feedme"))
    res = json.dumps(_run_result(galaxy))
    rr = beam_record_fit(str(galaxy), res)  # no verdict_json
    st = beam_status(str(galaxy))
    node = next(s for s in st["states"] if s["label"] == "A.1")
    assert node["verdict"] == "FAIL"
    assert rr["is_best"] is False
    assert st["best_state"] is None
    assert any("no admissible best state" in w for w in st.get("warnings", []))


def test_beam_record_fit_rejects_bad_payload(galaxy):
    beam_init(str(galaxy), str(galaxy / "_iter1.feedme"))
    r = beam_record_fit(str(galaxy), "not json")
    assert r["status"] == "failure"


def test_beam_mark_failure(galaxy):
    beam_init(str(galaxy), str(galaxy / "_iter1.feedme"))
    beam_record_fit(str(galaxy), json.dumps(_run_result(galaxy)))
    # enqueue a fake pending candidate directly on the graph
    from beam.graph import BeamGraph

    g = BeamGraph.load(str(galaxy))
    aid = g.add_pending({"sigma": 0.5, "primitives": [{"op": "remove", "target": "bar"}]},
                        source_session="s", parent_label="A.1")
    g.commit()
    r = beam_mark_failure(str(galaxy), aid, "galfit_timeout")
    assert r["status"] == "success"
    assert r["counters"]["n_failed"] == 1


def test_fastmcp_registration():
    """The four beam tools register under the GALFIT_BIN branch."""
    with patch.dict(sys.modules, {}), patch.dict(
        "os.environ", {"GALFIT_BIN": "/usr/bin/true"}
    ):
        sys.path.insert(0, "src")
        try:
            import mcp_server

            mcp_server = importlib.reload(mcp_server)
            tools = asyncio.run(mcp_server.app.list_tools())
            names = {t.name for t in tools}
            assert {"beam_init", "beam_record_fit", "beam_mark_failure", "beam_status"} <= names
        finally:
            sys.path.remove("src") if "src" in sys.path else None
