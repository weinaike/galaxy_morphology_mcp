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
    """Align result FITS strictly: manifest explicit path first, band token second.

    A band with no unambiguous match raises instead of falling back to a
    positional result (fail-closed); cross-band residual comparison must never
    silently pair the wrong files.
    """
    unused = list(result_files)
    aligned: list[str | None] = []
    for band in manifest_bands:
        band_name = str(band.get("band", "")).lower()
        manifest_path = _path(band.get("result_fits"))
        match_index = next(
            (
                index
                for index, path in enumerate(unused)
                if manifest_path and path == manifest_path
            ),
            None,
        )
        if match_index is None:
            token = re.search(r"f\d{3,4}[a-z]", band_name)
            token_text = token.group(0) if token else band_name
            token_matches = [
                index
                for index, path in enumerate(unused)
                if token_text and token_text in Path(path).name.lower()
            ]
            if len(token_matches) == 1:
                match_index = token_matches[0]
        if match_index is None:
            if not unused:
                # No result files left to pair: an incompleteness case that
                # normalize_fit_result reports through missing_fields.
                aligned.append(None)
                continue
            raise ValueError(
                f"could not unambiguously match a result FITS for band "
                f"{band.get('band')!r} from {unused}; refusing positional fallback"
            )
        aligned.append(unused.pop(match_index))
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


def _fit_quality_ok(manifest: Mapping[str, Any]) -> str:
    """ES convergence proxy (Q1, 2026-10-27 ruling).

    ``yes``: every parsed band has a finite, positive reduced chisq with dof>0
    (or a finite top-level reduced chisq when no per-band lines exist) AND the
    optimizer generation count is recorded. ``no``: any parsed reduced chisq is
    non-finite or non-positive, or dof<=0. ``inconclusive``: summaries cannot
    be parsed. GalfitS ES emits no optimizer-converged flag, so a true
    ``optimizer_converged`` is reported as NOT_APPLICABLE for this version.
    """
    try:
        summary = _parse_workflow_summary(str(manifest["summary_file"]))
    except (OSError, ValueError):
        return "inconclusive"
    bands = summary.get("bands") or []
    checks: list[tuple[float, float]] = []
    if bands:
        for band in bands:
            reduced = band.get("reduced_chisq")
            dof = band.get("dof")
            if reduced is None or dof is None:
                return "inconclusive"
            if not np.isfinite(float(reduced)) or float(reduced) <= 0 or float(dof) <= 0:
                return "no"
            checks.append((float(reduced), float(dof)))
    else:
        reduced = summary.get("reduced_chisq")
        if reduced is None:
            return "inconclusive"
        if not np.isfinite(float(reduced)) or float(reduced) <= 0:
            return "no"
    generations = _summary_generations(manifest)
    if generations is None:
        # The ES generation count only exists in GalfitS multi-band
        # summaries; single-band GALFIT output has no equivalent field.
        if str(manifest.get("workflow_mode", "single-band")) != "multi-band":
            return "yes"
        return "inconclusive"
    return "yes"


def _summary_generations(manifest: Mapping[str, Any]) -> int | None:
    """Parse the optimizer generation count recorded by GalfitS ES."""
    try:
        content = Path(str(manifest["summary_file"])).read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"num_generations:\s*(\d+)", content)
    if not match:
        return None
    generations = int(match.group(1))
    return generations if generations >= 1 else None


def _band_reduced_chisq(manifest: Mapping[str, Any]) -> dict[str, float] | None:
    """Per-band reduced chisq from the official GalfitS summary (2026-11-02
    ruling 10); returns None when any band lacks the value."""
    try:
        summary = _parse_workflow_summary(str(manifest["summary_file"]))
    except (OSError, ValueError):
        return None
    bands = summary.get("bands") or []
    values = {
        str(item.get("band")): float(item.get("reduced_chisq"))
        for item in bands
        if item.get("reduced_chisq") is not None
    }
    return values if bands and len(values) == len(bands) else None


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
    # 2026-11-02 ruling 10: residual_outcome comes from the official GalfitS
    # per-band reduced chisq (mean across bands); the self-computed L1 score
    # is retired. Any missing band -> fail-closed inconclusive.
    baseline_band_rc = (
        _band_reduced_chisq(baseline_manifest) if baseline_artifact["valid"] else None
    )
    candidate_band_rc = (
        _band_reduced_chisq(candidate_manifest) if candidate_artifact["valid"] else None
    )
    if not baseline_artifact["valid"] or not candidate_artifact["valid"]:
        residual_outcome = "inconclusive"
    elif not baseline_band_rc or not candidate_band_rc:
        residual_outcome = "inconclusive"
    else:
        common = sorted(set(baseline_band_rc) & set(candidate_band_rc))
        if not common or len(common) != len(baseline_band_rc):
            residual_outcome = "inconclusive"
        else:
            baseline_mean = sum(baseline_band_rc[b] for b in common) / len(common)
            candidate_mean = sum(candidate_band_rc[b] for b in common) / len(common)
            if candidate_mean < baseline_mean:
                residual_outcome = "improved"
            elif candidate_mean > baseline_mean:
                residual_outcome = "worse"
            else:
                residual_outcome = "equivalent"
    candidate_physical, boundary_hits, degeneracy_warnings = _parameter_health(candidate_manifest)
    _, baseline_boundary_hits, _ = _parameter_health(baseline_manifest)
    # Q3 comparison semantics: only boundaries the candidate newly introduces
    # (candidate set minus baseline set) participate in rejection.
    boundary_new_or_unresolved = sorted(set(boundary_hits) - set(baseline_boundary_hits))
    if candidate_artifact["valid"]:
        fit_converged = "yes"
    elif str((candidate_fit or {}).get("status", "")).lower() in {"failure", "failed"}:
        fit_converged = "no"
    else:
        fit_converged = "inconclusive"
    fit_quality_ok = _fit_quality_ok(candidate_manifest) if candidate_artifact["valid"] else "inconclusive"
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
        "fit_quality_ok": fit_quality_ok,
        "optimizer_converged": "NOT_APPLICABLE",
        "residual_outcome": residual_outcome,
        "one_d_residual_outcome": "NOT_APPLICABLE",
        "parameters_physical": candidate_physical,
        "boundary_hits": boundary_hits,
        "boundary_hits_baseline": baseline_boundary_hits,
        "boundary_hits_new_or_unresolved": boundary_new_or_unresolved,
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
                "baseline": (baseline_band_rc and round(sum(baseline_band_rc.values()) / len(baseline_band_rc), 6)) or None,
                "candidate": (candidate_band_rc and round(sum(candidate_band_rc.values()) / len(candidate_band_rc), 6)) or None,
                "metric": "mean of GalfitS per-band reduced chisq (2026-11-02 ruling 10)",
                "per_band_baseline": baseline_band_rc,
                "per_band_candidate": candidate_band_rc,
            },
            "parameter_health": {
                "candidate": candidate_physical,
                "boundary_hits": boundary_hits,
                "baseline_boundary_hits": baseline_boundary_hits,
                "boundary_hits_new_or_unresolved": boundary_new_or_unresolved,
                "degeneracy_warnings": degeneracy_warnings,
            },
            "fit_statistics": {
                "bic": bic,
                "reduced_chisq": reduced_chisq,
                "fit_quality_ok": fit_quality_ok,
            },
        },
    }
