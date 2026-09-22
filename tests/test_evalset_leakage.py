"""Focused tests for evalset leakage checks (design doc §13)."""

from component_analysis.evalset.leakage import check_manifests


def _manifest(**overrides):
    base = {
        "schema_version": "evaluation-state-manifest@v1",
        "sample_id": "s1",
        "mode": "multi_band",
        "object_id": "104",
        "state_round_id": "output/20260716_175416_obj_104/obj_104.lyric",
        "input_cutoff": "output/20260716_175416_obj_104/obj_104.lyric",
        "source_refs": [
            {"path": "/data/output/20260716_175416_obj_104/obj_104.lyric", "role": "configuration"},
            {"path": "/data/output/20260716_175416_obj_104/obj104_nircam_f115w_result.fits", "role": "binary_fit"},
        ],
        "history_context": [],
    }
    base.update(overrides)
    return base


def test_clean_manifest_passes() -> None:
    report = check_manifests([_manifest()])
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["pending_manual_checks"]


def test_working_note_reference_fails() -> None:
    refs = _manifest()["source_refs"] + [{"path": "/data/output/20260716_175416_obj_104/working_note.md", "role": "notes"}]
    report = check_manifests([_manifest(source_refs=refs)])
    assert report["status"] == "FAIL"
    assert any("Working Note" in item["check"] or "component-analysis" in item["check"] for item in report["violations"])


def test_next_round_reference_fails() -> None:
    refs = _manifest()["source_refs"] + [{"path": "/data/output/20260716_180334_obj_104_iter2/obj_104_iter2.lyric", "role": "configuration"}]
    report = check_manifests([_manifest(source_refs=refs)])
    assert report["status"] == "FAIL"
    assert any("escapes the state round" in item["detail"] for item in report["violations"])


def test_future_history_context_fails() -> None:
    manifest = _manifest(history_context=[{"round_id": "output/20260716_180334_obj_104_iter2/obj_104_iter2.lyric", "action": "historical_context_only"}])
    report = check_manifests([manifest])
    assert report["status"] == "FAIL"
    assert any("later than the state round" in item["detail"] for item in report["violations"])


def test_label_side_field_fails() -> None:
    manifest = _manifest()
    manifest["best_turn"] = "20260716_183809_obj_104_iter6"
    report = check_manifests([manifest])
    assert report["status"] == "FAIL"
    assert any("blacklisted field" in item["detail"] for item in report["violations"])
