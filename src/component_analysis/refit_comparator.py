"""Deterministic comparison of baseline and candidate workflow artifacts."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from schemas import validate

from .artifact_adapter import (
    _parse_workflow_summary,
    load_workflow_band_arrays,
    workflow_fit_components,
)


_SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed"}


def _path(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(Path(str(value)).expanduser().resolve())


def _band_result_files(
    manifest_bands: list[Mapping[str, Any]],
    result_files: list[str],
) -> list[str | None]:
    """Align result FITS using the band token in each filename."""
    unused = list(result_files)
    aligned: list[str | None] = []
    for band in manifest_bands:
        band_name = str(band.get("band", "")).lower()
        token = re.search(r"f\d{3,4}[a-z]", band_name)
        token_text = token.group(0) if token else band_name
        match_index = next(
            (
                index
                for index, path in enumerate(unused)
                if token_text and token_text in Path(path).name.lower()
            ),
            None,
        )
        if match_index is None:
            manifest_path = _path(band.get("result_fits"))
            match_index = next(
                (
                    index
                    for index, path in enumerate(unused)
                    if manifest_path and path == manifest_path
                ),
                None,
            )
        if match_index is None and unused:
            match_index = 0
        aligned.append(unused.pop(match_index) if match_index is not None else None)
    return aligned


def _fit_values(
    workflow_manifest: Mapping[str, Any],
    fit_result: Mapping[str, Any] | None,
) -> tuple[str, str | None, list[str], list[str], str | None, str | None, list[str]]:
    raw = dict(fit_result or {})
    mode = str(workflow_manifest["workflow_mode"])
    raw_status = str(raw.get("status", "success")).lower()
    status = "success" if raw_status in _SUCCESS_STATUSES else "failure"
    config_file = _path(raw.get("input_param_file") or raw.get("config_file") or workflow_manifest.get("config_file"))
    if mode == "single-band":
        results = raw.get("result_files") or []
        if not isinstance(results, list):
            results = []
        result_files = [_path(raw.get("optimized_fits_file") or (results[0] if results else None))]
        summaries = [_path(raw.get("summary_file"))]
        comparison = _path(raw.get("image_file") or raw.get("comparison_png"))
        parameter_file = _path(raw.get("output_param_file") or workflow_manifest.get("parameter_file"))
        parameter_files: list[str] = []
    else:
        result_values = raw.get("result_fits") or raw.get("result_files") or []
        summary_values = raw.get("summary_files") or []
        parameter_values = raw.get("params_files") or workflow_manifest.get("parameter_files") or []
        result_files = [_path(value) for value in result_values if value]
        summaries = [_path(value) for value in summary_values if value]
        comparison = _path(raw.get("comparison_png") or raw.get("image_file"))
        parameter_file = None
        parameter_files = [_path(value) for value in parameter_values if value]
    return (
        status,
        config_file,
        [value for value in result_files if value],
        [value for value in summaries if value],
        comparison,
        parameter_file,
        [value for value in parameter_files if value],
    )


def normalize_fit_result(
    workflow_manifest: Mapping[str, Any],
    fit_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize an existing MCP fit return without executing a fitter."""
    validate(dict(workflow_manifest), "workflow_round_manifest")
    (
        status,
        config_file,
        result_files,
        summary_files,
        comparison,
        parameter_file,
        parameter_files,
    ) = _fit_values(workflow_manifest, fit_result)
    bands = []
    manifest_bands = list(workflow_manifest.get("bands", []))
    aligned_result_files = _band_result_files(manifest_bands, result_files)
    for index, band in enumerate(manifest_bands):
        result_file = aligned_result_files[index]
        bands.append({
            "band": band["band"],
            "result_fits": result_file or str(band.get("result_fits", "")),
            "result_hdus": copy.deepcopy(band["result_hdus"]),
        })
    missing: list[str] = []
    if config_file is None or not Path(config_file).is_file():
        missing.append("config_file")
    if not result_files or any(not Path(value).is_file() for value in result_files):
        missing.append("result_files")
    if not summary_files or any(not Path(value).is_file() for value in summary_files):
        missing.append("summary_files")
    if comparison is None or not Path(comparison).is_file():
        missing.append("comparison_png")
    if len(bands) != len(manifest_bands) or any(
        not item["result_fits"] or not Path(item["result_fits"]).is_file()
        for item in bands
    ):
        missing.append("bands")
    if workflow_manifest["workflow_mode"] == "single-band" and len(result_files) != 1:
        missing.append("single_band_result_files")
    if workflow_manifest["workflow_mode"] == "multi-band" and len(result_files) != len(manifest_bands):
        missing.append("multi_band_result_files")
    artifact = {
        "schema_version": "workflow-fit-artifact@v1",
        "workflow_mode": workflow_manifest["workflow_mode"],
        "status": status if not missing else "incomplete",
        "valid": status == "success" and not missing,
        "config_file": config_file,
        "result_files": (
            [value for value in aligned_result_files if value] or result_files
        ),
        "summary_files": summary_files,
        "comparison_png": comparison,
        "parameter_file": parameter_file,
        "parameter_files": parameter_files,
        "constraint_files": [str(value) for value in (fit_result or {}).get("constrain_files", []) if value],
        "bands": bands,
        "missing_fields": sorted(set(missing)),
    }
    validate(artifact, "workflow_fit_artifact")
    return artifact


def _comparison_conditions(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if baseline.get("workflow_mode") != candidate.get("workflow_mode"):
        reasons.append("workflow_mode")
    baseline_bands = list(baseline.get("bands", []))
    candidate_bands = list(candidate.get("bands", []))
    if [item.get("band") for item in baseline_bands] != [item.get("band") for item in candidate_bands]:
        reasons.append("band_order")
    for index, (left, right) in enumerate(zip(baseline_bands, candidate_bands, strict=False)):
        for key in ("science_fits", "science_hdu", "sigma_fits", "sigma_hdu", "mask_fits", "mask_hdu", "psf_fits", "psf_hdu", "fit_region"):
            if left.get(key) != right.get(key):
                reasons.append(f"bands[{index}].{key}")
    return {"comparable": not reasons, "reasons": reasons}


def _residual_score(manifest: Mapping[str, Any]) -> float | None:
    try:
        arrays = load_workflow_band_arrays(dict(manifest))
    except (OSError, ValueError, RuntimeError, IndexError):
        return None
    scores: list[float] = []
    for band in arrays:
        residual = np.asarray(band.residual, dtype=float)
        valid = np.isfinite(residual)
        if band.mask is not None:
            valid &= ~np.asarray(band.mask, dtype=bool)
        if band.sigma is not None:
            sigma = np.asarray(band.sigma, dtype=float)
            valid &= np.isfinite(sigma) & (sigma > 0)
            values = np.abs(residual[valid] / sigma[valid])
        else:
            values = np.abs(residual[valid])
        if values.size:
            score = float(np.mean(values))
            if np.isfinite(score):
                scores.append(score)
    return float(np.mean(scores)) if scores else None


def _parameter_health(manifest: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    try:
        components = workflow_fit_components(manifest)
    except (OSError, ValueError, RuntimeError):
        return "inconclusive", [], ["component_parameters_unavailable"]
    if not components:
        return "inconclusive", [], ["component_parameters_unavailable"]
    boundary_hits: list[str] = []
    warnings: list[str] = []
    incomplete = False
    for component in components:
        label = str(component.get("model_label") or component.get("name") or "unknown")
        profile_type = str(component.get("type") or "").lower()
        required_parameters = ["ba"]
        if profile_type not in {"psf", "moffat", "gaussian"}:
            required_parameters.insert(0, "re")
        for parameter in required_parameters:
            value = component.get(parameter)
            if value is None:
                incomplete = True
            elif not np.isfinite(float(value)) or float(value) <= 0 or (parameter == "ba" and float(value) > 1):
                return "no", boundary_hits, [f"{label}.{parameter}_invalid"]
        n_value = component.get("n")
        if n_value is not None and not np.isfinite(float(n_value)):
            return "no", boundary_hits, [f"{label}.n_invalid"]
        for item in component.get("parameter_health", []):
            if item.get("at_boundary") is True:
                boundary_hits.append(f"{label}.{item.get('parameter')}")
    if incomplete:
        return "inconclusive", boundary_hits, warnings
    return "yes", boundary_hits, warnings


def _summary_metric(manifest: Mapping[str, Any], key: str) -> float | None:
    try:
        value = _parse_workflow_summary(str(manifest["summary_file"])).get(key)
    except (OSError, ValueError):
        return None
    return float(value) if isinstance(value, (int, float)) and np.isfinite(value) else None


def compare_refit_artifacts(
    *,
    baseline_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
    baseline_fit: Mapping[str, Any] | None,
    candidate_fit: Mapping[str, Any] | None,
    candidate_action_type: str,
) -> dict[str, Any]:
    """Create the policy input and audit evidence from two fitted rounds."""
    baseline_artifact = normalize_fit_result(baseline_manifest, baseline_fit)
    candidate_artifact = normalize_fit_result(candidate_manifest, candidate_fit)
    conditions = _comparison_conditions(baseline_manifest, candidate_manifest)
    baseline_score = _residual_score(baseline_manifest) if baseline_artifact["valid"] else None
    candidate_score = _residual_score(candidate_manifest) if candidate_artifact["valid"] else None
    if not baseline_artifact["valid"] or not candidate_artifact["valid"]:
        residual_outcome = "inconclusive"
    elif baseline_score is None or candidate_score is None:
        residual_outcome = "inconclusive"
    elif candidate_score < baseline_score:
        residual_outcome = "improved"
    elif candidate_score > baseline_score:
        residual_outcome = "worse"
    else:
        residual_outcome = "equivalent"
    candidate_physical, boundary_hits, degeneracy_warnings = _parameter_health(candidate_manifest)
    if candidate_artifact["valid"]:
        fit_converged = "yes"
    elif str((candidate_fit or {}).get("status", "")).lower() in {"failure", "failed"}:
        fit_converged = "no"
    else:
        fit_converged = "inconclusive"
    bic_simple = _summary_metric(baseline_manifest, "bic")
    bic_complex = _summary_metric(candidate_manifest, "bic")
    bic = None
    if bic_simple is not None and bic_complex is not None:
        bic = {
            "bic_simple": bic_simple,
            "bic_complex": bic_complex,
            "bic_gain": bic_simple - bic_complex,
            "comparable": bool(conditions["comparable"]),
            "formula": "BIC_gain = BIC_simple - BIC_complex",
        }
    reduced_baseline = _summary_metric(baseline_manifest, "reduced_chisq")
    reduced_candidate = _summary_metric(candidate_manifest, "reduced_chisq")
    reduced_chisq = None
    if reduced_baseline is not None and reduced_candidate is not None:
        reduced_chisq = {
            "baseline": reduced_baseline,
            "candidate": reduced_candidate,
            "delta": reduced_candidate - reduced_baseline,
            "comparable": bool(conditions["comparable"]),
            "formula": (
                "reduced_chisq_delta = candidate - baseline; negative is lower"
            ),
        }
    evaluation = {
        "candidate_action_type": candidate_action_type,
        "fit_converged": fit_converged,
        "residual_outcome": residual_outcome,
        "parameters_physical": candidate_physical,
        "boundary_hits": boundary_hits,
        "degeneracy_warnings": degeneracy_warnings,
        "bic": bic,
        "reduced_chisq": reduced_chisq,
    }
    return {
        "evaluation": evaluation,
        "evidence": {
            "schema_version": "workflow-refit-evidence@v1",
            "baseline_artifact": baseline_artifact,
            "candidate_artifact": candidate_artifact,
            "comparison_conditions": conditions,
            "residual_score": {
                "baseline": baseline_score,
                "candidate": candidate_score,
                "metric": "mean absolute residual normalized by sigma when sigma is available",
            },
            "parameter_health": {
                "candidate": candidate_physical,
                "boundary_hits": boundary_hits,
                "degeneracy_warnings": degeneracy_warnings,
            },
            "fit_statistics": {
                "bic": bic,
                "reduced_chisq": reduced_chisq,
            },
        },
    }
