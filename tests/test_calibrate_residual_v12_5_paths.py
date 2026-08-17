from pathlib import Path

from eval import calibrate_residual_v12_5 as calibration


def test_resolver_prefers_output_beside_node_summary(tmp_path, monkeypatch):
    archive = tmp_path / "archives" / "node_archive"
    archive.mkdir(parents=True)
    shared_output = tmp_path / "shared_output.fits"
    shared_output.touch()
    local_output = archive / "shared_output.fits"
    local_output.touch()
    summary = archive / "node_summary.md"
    summary.write_text(
        f"**Output File:** {shared_output}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        calibration,
        "_looks_like_galfit_output",
        lambda value: Path(value).is_file(),
    )

    resolved = calibration._resolve_output_fits({"summary_path": str(summary)})

    assert resolved == str(local_output.resolve())
    assert resolved != str(shared_output.resolve())


def test_resolver_does_not_scan_unrelated_newest_fits(tmp_path, monkeypatch):
    archive = tmp_path / "archives" / "node_archive"
    archive.mkdir(parents=True)
    summary = archive / "node_summary.md"
    summary.write_text(
        "**Output File:** missing_node_output.fits\n",
        encoding="utf-8",
    )
    unrelated = tmp_path / "archives" / "later_node" / "unrelated.fits"
    unrelated.parent.mkdir(parents=True)
    unrelated.touch()

    monkeypatch.setattr(
        calibration,
        "_looks_like_galfit_output",
        lambda value: Path(value).is_file(),
    )

    assert calibration._resolve_output_fits({"summary_path": str(summary)}) is None


def test_same_file_detects_identical_parent_and_child(tmp_path):
    output = tmp_path / "node.fits"
    output.touch()

    assert calibration._same_file(str(output), str(output))
