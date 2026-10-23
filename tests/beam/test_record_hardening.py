"""Record-hardening tests (2026-10-23 incident): by-reference recording,
fail-fast record validation, bare-type-name rejection, the survey E_GRAPH_STATE
preflight, and the beam_repair_artifacts channel."""

import json
import shutil

import pytest

from beam.graph import BeamGraph
from beam.survey import survey_round as _survey_round
from beam.tools import (beam_init, beam_record_fit, beam_repair_artifacts)


@pytest.fixture
def galaxy(tmp_path, test_data_dir):
    gdir = tmp_path / "HARD"
    gdir.mkdir()
    shutil.copy(test_data_dir / "NGC1097.feedme", gdir / "_iter1.feedme")
    shutil.copy(test_data_dir / "NGC1097_clean.07", gdir / "galfit.01")
    shutil.copy(test_data_dir / "NGC1097_comparison.png", gdir / "cmp.png")
    shutil.copy(test_data_dir / "NGC1097_summary.md", gdir / "summary.md")
    r = beam_init(str(gdir), str(gdir / "_iter1.feedme"),
                  stage1_morphology="spiral")
    assert r["status"] == "success"
    return gdir


def _round_status(gdir, feedme="_iter1.feedme", out="galfit.01",
                  png="cmp.png", summary="summary.md", bic=1000.0):
    """Synthesise a run_galfit archive's round_status.json by reference."""
    ar = gdir / "archives" / "test_round"
    ar.mkdir(parents=True, exist_ok=True)
    rs = ar / "round_status.json"
    rs.write_text(json.dumps({
        "status": "success",
        "inputs": {"feedme": str(gdir / feedme)},
        "outputs": {"output_param": str(gdir / out),
                    "comparison": str(gdir / png),
                    "summary": str(gdir / summary)},
        "fit_statistics": {"bic_eff": bic, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    }))
    return str(rs)


def _run_json(gdir, **over):
    r = {"input_param_file": str(gdir / "_iter1.feedme"),
         "output_param_file": str(gdir / "galfit.01"),
         "image_file": str(gdir / "cmp.png"),
         "summary_file": str(gdir / "summary.md"),
         "round_status_file": str(gdir / "archives" / "x" / "round_status.json"),
         "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 0.9}}
    (gdir / "archives" / "x").mkdir(parents=True, exist_ok=True)
    (gdir / "archives" / "x" / "round_status.json").write_text("{}")
    r.update(over)
    return json.dumps(r)


# ---------------------------------------------------------------- ① reference
def test_record_by_reference(galaxy):
    rs = _round_status(galaxy)
    r = beam_record_fit(str(galaxy), round_status_file=rs)
    assert r["status"] == "success", r
    assert r["new_state"] == "A.1"
    g = BeamGraph.load(str(galaxy))
    names = {c.get("name") for c in g.state("A.1")["inventory"]}
    assert {"disk", "bulge"} <= names          # names recovered from the feedme
    assert g.state("A.1")["input_inventory"]   # input parse present


def test_record_reference_path_missing(galaxy):
    r = beam_record_fit(str(galaxy),
                        round_status_file=str(galaxy / "nope" / "round_status.json"))
    assert r["status"] == "failure" and "round_status_file" in r["error"]


# ---------------------------------------------------------------- ② fail-fast
def test_record_rejects_missing_fields(galaxy):
    broken = json.loads(_run_json(galaxy))
    broken.pop("input_param_file")             # the 2026-10-23 incident shape
    r = beam_record_fit(str(galaxy), json.dumps(broken), action_id="")
    assert r["status"] == "failure"
    assert r.get("action_id") == ""
    assert "E_RECORD_INCOMPLETE" in r["error"] and "input_param_file" in r["error"]
    # nothing was recorded
    g = BeamGraph.load(str(galaxy))
    assert not [n for n in g.g.nodes if g.g.nodes[n].get("global_iter_id", 0) > 0]


def test_record_rejects_nonexistent_paths(galaxy):
    broken = json.loads(_run_json(galaxy))
    broken["image_file"] = str(galaxy / "ghost.png")
    r = beam_record_fit(str(galaxy), json.dumps(broken))
    assert r["status"] == "failure"
    assert "E_RECORD_PATH_INVALID" in r["error"] and "image_file" in r["error"]


def test_record_rejects_bare_type_names(galaxy):
    # a name-less input feedme: every component name falls back to its type
    nameless = galaxy / "_nameless.feedme"
    nameless.write_text("\n".join(
        l for l in (galaxy / "_iter1.feedme").read_text().splitlines()
        if "# STRUCTURE:" not in l) + "\n")
    r = beam_record_fit(str(galaxy), _run_json(
        galaxy, input_param_file=str(nameless)))
    assert r["status"] == "failure"
    assert "E_RECORD_TYPE_NAMES" in r["error"]
    g = BeamGraph.load(str(galaxy))
    assert not [n for n in g.g.nodes if g.g.nodes[n].get("global_iter_id", 0) > 0]


# ------------------------------------------------------- ③ survey preflight
def test_survey_preflight_aborts_on_type_names(galaxy, monkeypatch):
    # record a healthy A.1, then corrupt it into the incident's lesion shape
    assert beam_record_fit(str(galaxy), _run_json(galaxy))["status"] == "success"
    g = BeamGraph.load(str(galaxy))
    g.g.nodes["A.1"]["inventory"] = [
        {"name": "expdisk", "type": "expdisk"},
        {"name": "sersic", "type": "sersic"}]
    g.g.nodes["A.1"]["input_inventory"] = []
    g.commit()

    called = []
    monkeypatch.setattr("beam.survey._dispatch",
                        lambda *a, **k: called.append(1)
                        or (_ for _ in ()).throw(AssertionError("VLM must not be called")))
    r = _survey_round(str(galaxy), state_label="A.1")
    assert r["status"] == "failure" and r.get("code") == "E_GRAPH_STATE"
    assert "beam_repair_artifacts" in r["error"]
    assert not called


def test_survey_preflight_aborts_on_dangling_artifacts(galaxy):
    assert beam_record_fit(str(galaxy), _run_json(galaxy))["status"] == "success"
    g = BeamGraph.load(str(galaxy))
    g.g.nodes["A.1"]["artifacts"]["galfit_nn"] = str(galaxy / "gone.01")
    g.commit()
    r = _survey_round(str(galaxy), state_label="A.1")
    assert r["status"] == "failure" and r.get("code") == "E_GRAPH_STATE"
    assert "galfit_nn" in r["error"]


# ---------------------------------------------------------------- ④ repair
def test_repair_artifacts_restores_state(galaxy):
    assert beam_record_fit(str(galaxy), _round_status(galaxy) and
                           _run_json(galaxy))["status"] == "success"
    g = BeamGraph.load(str(galaxy))
    metrics_before = dict(g.state("A.1")["metrics"])
    # simulate the lesion: empty input inventory + type names + dangling paths
    g.g.nodes["A.1"]["input_inventory"] = []
    g.g.nodes["A.1"]["inventory"] = [{"name": "expdisk", "type": "expdisk"},
                                     {"name": "sersic", "type": "sersic"}]
    g.g.nodes["A.1"]["artifacts"]["feedme"] = ""
    g.commit()

    r = beam_repair_artifacts(str(galaxy), "A.1", _round_status(galaxy))
    assert r["status"] == "success", r
    g2 = BeamGraph.load(str(galaxy))
    st = g2.state("A.1")
    names = {c.get("name") for c in st["inventory"]}
    assert {"disk", "bulge"} <= names
    assert st["input_inventory"]
    assert st["artifacts"]["feedme"]
    assert st["metrics"] == metrics_before          # measurements untouched
    kinds = [e["kind"] for e in g2.g.graph["decision_log"]]
    assert "artifact-repair" in kinds


def test_repair_refuses_still_bare_names(galaxy):
    assert beam_record_fit(str(galaxy), _run_json(galaxy))["status"] == "success"
    nameless = galaxy / "_nameless.feedme"
    nameless.write_text("\n".join(
        l for l in (galaxy / "_iter1.feedme").read_text().splitlines()
        if "# STRUCTURE:" not in l) + "\n")
    r = beam_repair_artifacts(str(galaxy), "A.1",
                              _round_status(galaxy, feedme="_nameless.feedme"))
    assert r["status"] == "failure"
    assert "E_RECORD_TYPE_NAMES" in r["error"]
