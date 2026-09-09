"""Tests for structured workflow VLM timing persistence."""

from concurrent.futures import ThreadPoolExecutor

from component_analysis.decision_service import _run_vlm
from component_analysis.timing import persist_workflow_timing


def _timing(round_id):
    return {
        "round_id": round_id,
        "model_id": "test-model",
        "duration_s": 0.01,
        "fallback_status": "OK",
        "attempts": [
            {
                "attempt": 1,
                "duration_s": 0.01,
                "parse_status": "OK",
            }
        ],
    }


def test_timing_log_respects_switch(tmp_path, monkeypatch):
    ref = tmp_path / "galaxy" / "output" / "round" / "config.lyric"
    ref.parent.mkdir(parents=True)
    monkeypatch.setenv("VLM_TIMING_LOG", "0")

    assert persist_workflow_timing(ref, _timing("disabled")) is None
    assert not (tmp_path / "galaxy" / "timing_log.md").exists()


def test_timing_log_records_retry_and_parse_status(tmp_path, monkeypatch):
    ref = tmp_path / "galaxy" / "output" / "round" / "config.lyric"
    ref.parent.mkdir(parents=True)
    monkeypatch.setenv("VLM_TIMING_LOG", "1")

    def callback(image, prompt):
        raise ValueError("malformed response")

    callback.model_id = "test-model"
    _, provider, _ = _run_vlm(
        round_id="round-1",
        numeric={
            "schema_version": "1.0",
            "round_id": "round-1",
            "manifest_ref": "memory:round-1",
            "features": [],
            "band_quality": [],
        },
        image="comparison.png",
        callback=callback,
        timing_ref_path=str(ref),
    )

    assert len(provider["timing"]["attempts"]) == 2
    assert all(item["parse_status"] == "PARSE_FAILED" for item in provider["timing"]["attempts"])
    log = (tmp_path / "galaxy" / "timing_log.md").read_text(encoding="utf-8")
    assert log.count("attempt ") == 2
    assert "model=test-model" in log


def test_timing_log_concurrent_append_is_complete(tmp_path, monkeypatch):
    ref = tmp_path / "galaxy" / "output" / "round" / "config.lyric"
    ref.parent.mkdir(parents=True)
    monkeypatch.setenv("VLM_TIMING_LOG", "1")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: persist_workflow_timing(ref, _timing(f"round-{index}")), range(20)))

    log = (tmp_path / "galaxy" / "timing_log.md").read_text(encoding="utf-8")
    assert log.count("## round-") == 20
    assert log.count("attempt ") == 20
