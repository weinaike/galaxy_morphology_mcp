import numpy as np

from tools import bar_lopsidedness_detection as mod


def test_apply_fit_region_uses_feedme_one_indexed_inclusive_bounds():
    image = np.arange(25).reshape(5, 5)
    mask = image % 2 == 0

    cropped_image, cropped_mask = mod._apply_fit_region(
        image, mask, (1, 3, 2, 4), one_indexed_inclusive=True
    )

    np.testing.assert_array_equal(cropped_image, image[1:4, 0:3])
    np.testing.assert_array_equal(cropped_mask, mask[1:4, 0:3])


def test_isophote_table_entry_handles_empty_step2_csv(tmp_path, monkeypatch):
    step2 = tmp_path / "step2.csv"
    step3 = tmp_path / "step3.csv"
    step2.write_text("", encoding="utf-8")
    step3.write_text("sma_pix,sma_arcsec\n1,0.02\n2,0.04\n", encoding="utf-8")

    def fake_classify(df_s2, df_s3, pixscale, survey_uc, band_or_survey):
        assert df_s2.empty
        assert len(df_s3) == 2
        assert pixscale == 0.02
        assert survey_uc == "JWST"
        assert band_or_survey == "F200W"
        return {
            "bar_result": {
                "bar_detected": True,
                "e_max": 0.5,
                "bar_pa_mean": 30.0,
                "failure_reason": None,
            },
            "dolfi": {
                "lopsided_dolfi": True,
                "A1_mean": 0.12,
                "phi1_mean": 45.0,
                "r50": 0.02,
                "r90": 0.04,
            },
            "center": {
                "lopsided_center": False,
                "dr_norm": np.nan,
                "x_trend_r": np.nan,
                "y_trend_r": np.nan,
            },
            "is_lopsided": False,
        }

    monkeypatch.setattr(mod, "_classify_profiles", fake_classify)

    result = mod.detect_bar_lopsidedness_from_isophote_tables(
        str(step2), str(step3), "JWST", band="F200W"
    )

    assert result["status"] == "success"
    assert result["bar"]["detected"] is True
    assert result["lopsidedness"]["detected"] is False
    assert result["lopsidedness"]["dolfi_detected"] is True
    assert result["lopsidedness"]["center_detected"] is False
