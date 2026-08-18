"""Synthetic FITS and shadow-runner tests for the independent adapter."""

import json

import numpy as np
import pytest

fits = pytest.importorskip("astropy.io.fits")

from component_analysis import (  # noqa: E402
    build_manifest,
    extract_numeric_evidence_from_manifest,
    load_band_arrays,
    run_shadow_round,
)
from schemas import validate  # noqa: E402


def _write_fits(path, data, header=None, hdu=0):
    image = np.asarray(data)
    if hdu == 0:
        fits.PrimaryHDU(image, header=header).writeto(path)
        return
    fits.HDUList(
        [fits.PrimaryHDU(), fits.ImageHDU(image, header=header)]
    ).writeto(path)


def _write_round(tmp_path):
    shape = (32, 32)
    yy, xx = np.indices(shape, dtype=float)
    original = 100.0 * np.exp(-((xx - 16.0) ** 2 + (yy - 16.0) ** 2) / 30.0)
    residual = 0.1 * original
    residual[5, 6] = 12.0
    original[5, 6] += 12.0
    sigma = np.ones(shape)
    mask = np.zeros(shape)
    model = original - residual
    psf = np.exp(-((np.indices((9, 9))[0] - 4.0) ** 2 + (np.indices((9, 9))[1] - 4.0) ** 2) / 4.0)

    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = 16.0
    header["CRPIX2"] = 16.0
    header["CRVAL1"] = 10.0
    header["CRVAL2"] = 20.0
    header["CDELT1"] = -0.001
    header["CDELT2"] = 0.001
    header["BUNIT"] = "MJy/sr"
    header["PA_V3"] = 130.75

    science = tmp_path / "science.fits"
    mask_file = tmp_path / "mask.fits"
    psf_file = tmp_path / "psf.fits"
    result = tmp_path / "obj170_nircam_f200w_result.fits"
    _write_fits(science, original, header, hdu=1)
    _write_fits(mask_file, mask, hdu=1)
    _write_fits(psf_file, psf, hdu=1)
    fits.HDUList(
        [
            fits.PrimaryHDU(residual),
            fits.ImageHDU(mask),
            fits.ImageHDU(sigma),
            fits.ImageHDU(model),
            fits.ImageHDU(original),
        ]
    ).writeto(result)

    lyric = tmp_path / "obj_170_iter1.lyric"
    lyric.write_text(
        "\n".join(
            [
                "R1) obj170",
                "R2) [10.0,20.0]",
                "R3) 0.4",
                f"Ia1) [{science},1]",
                "Ia2) nircam_f200w",
                "Ia3) [none,0]",
                f"Ia4) [{psf_file},1]",
                "Ia5) 1",
                f"Ia6) [{mask_file},1]",
                "Ia7) MJy/sr",
                "Ia8) 0.2",
                "Ia9) 1.0",
                "Ia10) 25.0",
                "Ia11) uniform",
                "Ia12) [[0,-1e5,1e5,0.1,0]]",
                "Ia13) 1",
                "Ia14) [[0,-5,5,0.1,1]]",
                "Ia15) 0",
                "# Profile A",
                "Pa1) disk",
                "Pa2) sersic",
                "Pa6) [2.0,0.5,6.0,0.1,1]",
            ]
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "obj170.gssummary"
    summary.write_text(
        "\n".join(
            [
                "# free parameters:",
                "pname best_value",
                "disk_Re 0.8",
                "disk_n 2.1",
                "disk_axrat 0.7",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    comparison = tmp_path / "all_bands_comparison.png"
    comparison.write_bytes(b"PNG placeholder")
    return lyric, summary, comparison, result


def test_build_manifest_loads_result_hdus_and_numeric_evidence(tmp_path):
    lyric, summary, comparison, result = _write_round(tmp_path)
    manifest = build_manifest(
        round_dir=tmp_path,
        lyric_file=lyric,
        summary_file=summary,
        comparison_png=comparison,
    )
    validate(manifest, "artifact_manifest")
    assert manifest["round_id"] == tmp_path.name
    assert manifest["bands"][0]["result_fits"] == str(result.resolve())
    assert manifest["bands"][0]["validation"] == {
        "paths_exist": True,
        "hdu_layout_valid": True,
        "shape_consistent": True,
        "wcs_valid": True,
        "unit": "MJy/sr",
        "finite_pixel_fraction": 1.0,
    }

    arrays = load_band_arrays(manifest)
    assert len(arrays) == 1
    assert arrays[0].original.shape == (32, 32)
    assert arrays[0].pa_v3_deg == pytest.approx(130.75)
    evidence = extract_numeric_evidence_from_manifest(manifest)
    validate(evidence, "numeric_evidence")
    assert evidence["round_id"] == manifest["round_id"]
    names = {feature["name"] for feature in evidence["features"]}
    assert {
        "source_extent_psf_ratio",
        "outer_isophote_geometry",
        "outer_residual_systematic",
        "single_sersic_n",
        "outer_axis_ratio",
        "bar_isophote_profile",
        "residual_m2_amplitude",
        "central_excess_multiband",
        "central_resolution_measurement",
        "original_m1_amplitude",
        "m1_confusion_present",
        "original_source_matches",
        "bar_fit_parameters",
        "extended_positive_residual",
    } <= names
    single = next(
        feature for feature in evidence["features"] if feature["name"] == "single_sersic_n"
    )
    assert single["value"] == {"n": 2.1, "at_boundary": False}
    regions = [
        region
        for feature in evidence["features"]
        for region in feature.get("candidate_regions", [])
    ]
    assert regions
    assert all(region["region_id"].startswith("candidate_") for region in regions)
    assert all(region["ra_deg"] is not None for region in regions)


def test_shadow_runner_writes_artifacts_without_vlm_or_fitting(tmp_path):
    lyric, summary, comparison, _ = _write_round(tmp_path)
    manifest = build_manifest(
        round_dir=tmp_path,
        lyric_file=lyric,
        summary_file=summary,
        comparison_png=comparison,
    )
    output_dir = tmp_path / "shadow"
    result = run_shadow_round(
        manifest,
        output_dir=output_dir,
        current_components={"disk"},
    )
    assert result["vlm_evidence"]["parse_status"] == "REFUSED"
    assert result["vlm_error"]
    validate(result["decision_artifact"], "decision_artifact")
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "numeric_evidence.json").is_file()
    assert (output_dir / "vlm_prompt.txt").is_file()
    assert (output_dir / "vlm_response.raw.json").read_text() == ""
    assert (output_dir / "vlm_evidence.json").is_file()
    assert (output_dir / "decision_artifact.json").is_file()


def test_shadow_runner_preserves_raw_controlled_vlm_response(tmp_path):
    lyric, summary, comparison, _ = _write_round(tmp_path)
    manifest = build_manifest(
        round_dir=tmp_path,
        lyric_file=lyric,
        summary_file=summary,
        comparison_png=comparison,
    )
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "round_id": manifest["round_id"],
            "parse_status": "OK",
            "observations": [
                {
                    "target_id": "central",
                    "label": "uncertain",
                    "confidence": 0.2,
                }
            ],
        }
    )

    def callback(image_path, prompt):
        assert image_path == str(comparison.resolve())
        assert '"candidate_1"' in prompt
        assert all(
            field not in prompt for field in ("x_pix", "y_pix", "ra_deg", "dec_deg")
        )
        return raw

    callback.model_id = "test-vlm"

    output_dir = tmp_path / "shadow_vlm"
    result = run_shadow_round(
        manifest,
        output_dir=output_dir,
        vlm_callback=callback,
        current_components={"disk"},
    )
    assert result["vlm_error"] is None
    assert result["vlm_evidence"]["model_id"] == "test-vlm"
    assert (output_dir / "vlm_response.raw.json").read_text() == raw
    assert result["vlm_evidence"]["parse_status"] == "OK"
