from eval.run_exec_eval import validate_galfit_reward_artifacts


def test_reward_artifacts_reject_placeholder_metrics_and_missing_image(tmp_path):
    summary = tmp_path / "incomplete_summary.md"
    summary.write_text("# GALFIT Fitting Summary\n\n## Init. par. file Content\n", encoding="utf-8")

    metrics, errors = validate_galfit_reward_artifacts(summary, None)

    assert metrics == {"chi2_nu": 9999.0, "chi2": 999999.0, "ndof": 0}
    assert "metric_missing:bic" in errors
    assert "metric_sentinel:chi2_nu" in errors
    assert "metric_invalid:ndof<=0" in errors
    assert "comparison_image_missing" in errors


def test_reward_artifacts_accept_complete_metrics_and_image(tmp_path):
    summary = tmp_path / "complete_summary.md"
    summary.write_text(
        "Chi^2/nu = 1.25\n"
        "Chi^2 = 125.0\n"
        "ndof = 100\n"
        "| BIC | 150.0 |\n",
        encoding="utf-8",
    )
    image = tmp_path / "comparison.png"
    image.write_bytes(b"png-placeholder")

    metrics, errors = validate_galfit_reward_artifacts(summary, image)

    assert errors == []
    assert metrics["chi2_nu"] == 1.25
    assert metrics["bic"] == 150.0
