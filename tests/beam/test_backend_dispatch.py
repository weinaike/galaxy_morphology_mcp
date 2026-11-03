"""P0 seam-abstraction tests: format-backend dispatch on meta["mode"].

galfit behaviour must be unchanged (the full suite covers that); these
tests pin the NEW plumbing only: default mode, persistence through
commit/load, loud failure of the galfits stub, and the "bic" head of
the metric-key chain (mb's gssummary BIC; galfit never sets it).
"""

import pytest

from beam.graph import BeamGraph, _backend_for, _metric_key


def test_default_mode_is_galfit(mini_graph):
    graph, _ = mini_graph
    assert graph.mode == "galfit"
    assert graph.backend().__name__ == "beam.backend"


def test_mode_persists_through_load(mini_graph):
    graph, label = mini_graph
    galaxy_dir = graph.g.graph["meta"]["galaxy_dir"]
    reloaded = BeamGraph.load(galaxy_dir)
    assert reloaded.mode == "galfit"
    assert reloaded.state(label)["label"] == label


def test_legacy_graph_without_mode_field_loads_as_galfit(mini_graph):
    graph, _ = mini_graph
    galaxy_dir = graph.g.graph["meta"]["galaxy_dir"]
    graph.g.graph["meta"].pop("mode", None)   # simulate a pre-P0 graph
    graph.commit()
    assert BeamGraph.load(galaxy_dir).mode == "galfit"


def test_unknown_mode_rejected(mini_graph):
    graph, _ = mini_graph
    graph.g.graph["meta"]["mode"] = "martian"
    with pytest.raises(ValueError, match="unknown beam mode"):
        graph.backend()


def test_galfits_stub_fails_loudly(tmp_path, test_data_dir):
    """mode='galfits' init must raise the stub's NotImplementedError, not
    silently run galfit parsing on a lyric file."""
    import shutil

    gdir = tmp_path / "MB"
    gdir.mkdir()
    lyric = gdir / "obj.lyric"
    shutil.copy(test_data_dir / "mb" / "mb_root.lyric", lyric)
    with pytest.raises(NotImplementedError, match="P2"):
        BeamGraph.init(str(gdir), str(lyric), stage1={"morphology": "t"},
                       mode="galfits")


def test_f1_reserved_refused_on_mb_mode(mini_graph):
    graph, _ = mini_graph
    graph.g.graph["meta"]["mode"] = "galfits"
    aid, reason = graph.add_f1_reserved(0.05, 30.0)
    assert aid is None and "sersic_f" in reason


def test_metric_key_bic_head():
    # mb: gssummary BIC wins
    assert _metric_key({"bic": 100.0, "bic_eff": 200.0}) == 100.0
    # galfit: unchanged fall-through (fit_statistics never carries "bic")
    assert _metric_key({"bic_eff": 200.0, "bic1d": 150.0}) == 200.0
    assert _metric_key({"bic1d": 150.0, "chi2_nu": 1.1}) == 150.0
    assert _metric_key({"chi2_nu": 1.1}) == 1.1
    assert _metric_key({}) is None


def test_backend_registry_shapes():
    import beam.backend as gf
    import beam_mb.backend as mb

    assert gf.MODE == "galfit" and mb.MODE == "galfits"
    for fn in ("parse_root", "parse_fit", "record_guard",
               "decode_cons_effective", "artifacts", "root_artifacts"):
        assert callable(getattr(gf, fn)) and callable(getattr(mb, fn))
    # the galfit root_artifacts keeps the historical key
    assert set(gf.root_artifacts("/x/y.feedme")) == {"feedme"}
    assert set(mb.root_artifacts("/x/y.lyric")) == {"lyric"}
    assert _backend_for("GALFITS").MODE == "galfits"   # case-insensitive
