"""Shared fixtures for the beam test suite."""

import shutil

import pytest

from beam.graph import BeamGraph


@pytest.fixture
def mini_graph(tmp_path, test_data_dir):
    """A minimal one-fit beam graph over the NGC1097 fixtures -> (graph, 'A.1')."""
    gdir = tmp_path / "MINI"
    gdir.mkdir()
    feedme = gdir / "_iter1.feedme"
    out = gdir / "galfit.01"
    shutil.copy(test_data_dir / "NGC1097.feedme", feedme)
    shutil.copy(test_data_dir / "NGC1097_clean.07", out)
    graph = BeamGraph.init(str(gdir), str(feedme), stage1={"morphology": "test"})
    label = graph.record_fit({
        "input_param_file": str(feedme),
        "output_param_file": str(out),
        "image_file": str(gdir / "cmp.png"),
        "summary_file": str(gdir / "summary.md"),
        "round_status_file": str(gdir / "archives" / "x" / "round_status.json"),
        "fit_statistics": {"bic_eff": 1000.0, "chisq1d_nu": 0.9,
                           "convergence": {"flag": "ok"}},
    })
    return graph, label
