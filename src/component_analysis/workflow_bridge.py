"""Structured workflow bridge for the existing MCP fitting tools.

This module consumes explicit round artifacts and decision artifacts. It does
not start GALFIT or GalfitS; the returned MCP contract is executed by the
workflow through the existing fitting tools.
"""

from __future__ import annotations

import copy
import ast
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from schemas import validate

from .policy import (
    PolicyState,
    evaluate_refit_with_policy,
    record_decision_state,
    save_policy_state,
)

WORKFLOW_BRIDGE_VERSION = "workflow-bridge@v1"
WORKFLOW_LIFECYCLE_VERSION = "workflow-lifecycle@v1"
SINGLE_BAND = "single-band"
MULTI_BAND = "multi-band"

_ACTION_TYPES = {
    "PROPOSE_ADD",
    "PROPOSE_REPLACE",
    "PROPOSE_REMOVE",
    "PROMOTE_SINGLE_SERSIC_TO_DISK",
    "REFIT_PARAMETERS",
    "KEEP_AND_CONTINUE",
    "CONVERGED",
}
_DIRECT_AGENT_FIELDS = {
    "feedme",
    "feedme_file",
    "lyric",
    "lyric_file",
    "mcp_call",
    "run_galfit",
    "run_galfits",
}


def _required_file(value: str | Path, field: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field} is required and must be an explicit file path")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{field} does not exist: {path}")
    return str(path)


def _optional_file(value: str | Path | None, field: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _required_file(value, field)


def _parse_pixscale(config_file: str) -> float | None:
    content = Path(config_file).read_text(encoding="utf-8")
    match = re.search(r"^K\)\s*([0-9.eE+-]+)", content, re.MULTILINE)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def _fit_region(config_file: str, shape: tuple[int, int]) -> list[int]:
    from tools.parse_feedme import parse_feedme

    region = parse_feedme(config_file).get("fit_region")
    if region is None:
        height, width = shape
        return [0, width, 0, height]
    xmin, xmax, ymin, ymax = region
    return [max(xmin - 1, 0), xmax, max(ymin - 1, 0), ymax]


def _result_hdus(result_file: str) -> tuple[dict[str, int], tuple[int, int]]:
    try:
        from astropy.io import fits
    except ModuleNotFoundError as exc:
        raise RuntimeError("workflow manifest construction requires astropy") from exc

    role_indexes: dict[str, int] = {}
    with fits.open(result_file, memmap=False) as hdul:
        for index, hdu in enumerate(hdul):
            label = " ".join(
                str(hdu.header.get(key, ""))
                for key in ("OBJECT", "EXTNAME", "COMPONENT")
            ).lower()
            for role in ("original", "model", "residual"):
                if role in label and role not in role_indexes:
                    role_indexes[role] = index
        if len(role_indexes) != 3:
            fallback = (1, 2, 3) if len(hdul) >= 4 else (0, 1, 2)
            role_indexes = dict(zip(("original", "model", "residual"), fallback))
        try:
            shapes = tuple(
                tuple(hdul[role_indexes[role]].data.shape)
                for role in ("original", "model", "residual")
            )
        except (AttributeError, IndexError) as exc:
            raise ValueError(
                "result FITS has no usable original/model/residual arrays: "
                f"{result_file}"
            ) from exc
        if len(set(shapes)) != 1 or len(shapes[0]) != 2:
            raise ValueError(
                "result FITS arrays must be matching two-dimensional images: "
                f"{result_file}"
            )
    return (
        {
            "original_hdu": role_indexes["original"],
            "model_hdu": role_indexes["model"],
            "residual_hdu": role_indexes["residual"],
        },
        shapes[0],
    )


def _normalise_band_record(record: Mapping[str, Any], index: int) -> dict[str, Any]:
    required = (
        "band",
        "science_fits",
        "science_hdu",
        "result_fits",
        "result_hdus",
        "fit_region",
    )
    missing = [field for field in required if field not in record]
    if missing:
        raise ValueError(f"band {index} is missing fields: {missing}")
    result = dict(record)
    result["band"] = str(result["band"])
    result["science_fits"] = _required_file(
        result["science_fits"], f"bands[{index}].science_fits"
    )
    result["result_fits"] = _required_file(
        result["result_fits"], f"bands[{index}].result_fits"
    )
    result["science_hdu"] = int(result["science_hdu"])
    result["result_hdus"] = {
        key: int(result["result_hdus"][key])
        for key in ("original_hdu", "model_hdu", "residual_hdu")
    }
    for field in ("sigma_fits", "mask_fits", "psf_fits"):
        result[field] = _optional_file(
            result.get(field), f"bands[{index}].{field}"
        )
    for field in ("sigma_hdu", "mask_hdu", "psf_hdu"):
        if result.get(field) is not None:
            result[field] = int(result[field])
    result["fit_region"] = [int(value) for value in result["fit_region"]]
    if len(result["fit_region"]) != 4:
        raise ValueError(f"bands[{index}].fit_region must contain four coordinates")
    return result


def build_single_band_workflow_manifest(
    *,
    config_file: str | Path,
    optimized_fits_file: str | Path,
    summary_file: str | Path,
    comparison_png: str | Path | None = None,
    working_note_file: str | Path | None = None,
    constraint_file: str | Path | None = None,
    parameter_file: str | Path | None = None,
    object_id: str | None = None,
    round_id: str | None = None,
    band: str = "single",
) -> dict[str, Any]:
    """Build a single-band manifest from explicit feedme and result paths."""
    from tools.parse_feedme import parse_feedme

    config = _required_file(config_file, "config_file")
    result = _required_file(optimized_fits_file, "optimized_fits_file")
    summary = _required_file(summary_file, "summary_file")
    comparison = _optional_file(comparison_png, "comparison_png")
    note = _optional_file(working_note_file, "working_note_file")
    parsed = parse_feedme(config)
    science = _required_file(parsed.get("input"), "feedme input")
    science_hdu = 0
    _, result_shape = _result_hdus(result)
    try:
        from astropy.io import fits

        with fits.open(science, memmap=False) as hdul:
            science_data = hdul[science_hdu].data
            if science_data is None or len(science_data.shape) != 2:
                raise ValueError(f"single-band science HDU is not a 2D image: {science}")
            shape = tuple(science_data.shape)
    except IndexError as exc:
        raise ValueError(
            f"single-band science FITS has no usable HDU 0: {science}"
        ) from exc
    fit_region = _fit_region(config, shape)
    fit_shape = (fit_region[3] - fit_region[2], fit_region[1] - fit_region[0])
    if shape != result_shape and fit_shape != result_shape:
        raise ValueError(
            "single-band result shape matches neither science image nor H) fit region: "
            f"{science}={shape}, {result}={result_shape}, fit_region={fit_region}"
        )
    parsed_constraint = parsed.get("constraint")
    constraint = _optional_file(
        constraint_file or parsed_constraint, "constraint_file"
    )
    parameter = _optional_file(parameter_file, "parameter_file")
    mask = _optional_file(parsed.get("mask"), "feedme mask")
    sigma = _optional_file(parsed.get("sigma"), "feedme sigma")
    psf = _optional_file(parsed.get("psf"), "feedme psf")
    hdu_map, _ = _result_hdus(result)
    band_record = {
        "band": band,
        "science_fits": science,
        "science_hdu": science_hdu,
        "sigma_fits": sigma,
        "sigma_hdu": 0 if sigma else None,
        "mask_fits": mask,
        "mask_hdu": 0 if mask else None,
        "psf_fits": psf,
        "psf_hdu": 0 if psf else None,
        "result_fits": result,
        "result_hdus": hdu_map,
        "pixscale_arcsec": _parse_pixscale(config) or 1.0,
        "fit_region": fit_region,
        "validation": {
            "paths_explicit": True,
            "science_shape": list(shape),
            "result_shape": list(result_shape),
            "fit_region_crop": fit_shape == result_shape,
        },
    }
    manifest = {
        "schema_version": "1.0",
        "workflow_mode": SINGLE_BAND,
        "round_id": round_id or Path(result).parent.name,
        "object_id": object_id or Path(config).stem,
        "config_file": config,
        "result_files": [result],
        "summary_file": summary,
        "comparison_png": comparison,
        "working_note_file": note,
        "constraint_files": [constraint] if constraint else [],
        "parameter_file": parameter,
        "parameter_files": [],
        "bands": [_normalise_band_record(band_record, 0)],
    }
    validate(manifest, "workflow_round_manifest")
    return manifest


def build_multi_band_workflow_manifest(
    *,
    config_file: str | Path,
    summary_file: str | Path,
    bands: Sequence[Mapping[str, Any]],
    comparison_png: str | Path | None = None,
    working_note_file: str | Path | None = None,
    constraint_files: Sequence[str | Path] = (),
    parameter_files: Sequence[str | Path] = (),
    object_id: str,
    round_id: str,
) -> dict[str, Any]:
    """Build a multi-band manifest from explicit per-band result references."""
    config = _required_file(config_file, "config_file")
    summary = _required_file(summary_file, "summary_file")
    if not bands:
        raise ValueError("bands must contain at least one explicit band record")
    records = [
        _normalise_band_record(record, index)
        for index, record in enumerate(bands)
    ]
    constraints = [
        _required_file(value, f"constraint_files[{index}]")
        for index, value in enumerate(constraint_files)
    ]
    parameters = [
        _required_file(value, f"parameter_files[{index}]")
        for index, value in enumerate(parameter_files)
    ]
    manifest = {
        "schema_version": "1.0",
        "workflow_mode": MULTI_BAND,
        "round_id": str(round_id),
        "object_id": str(object_id),
        "config_file": config,
        "result_files": [record["result_fits"] for record in records],
        "summary_file": summary,
        "comparison_png": _optional_file(comparison_png, "comparison_png"),
        "working_note_file": _optional_file(
            working_note_file, "working_note_file"
        ),
        "constraint_files": constraints,
        "parameter_file": None,
        "parameter_files": parameters,
        "bands": records,
    }
    validate(manifest, "workflow_round_manifest")
    return manifest


def build_workflow_analysis_artifact(
    *,
    workflow_manifest: Mapping[str, Any],
    decision_artifact: Mapping[str, Any],
    component_analysis_file: str | Path | None = None,
    working_note_file: str | Path | None = None,
    agent_recommendation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Join human-readable analysis with the validated machine decision.

    The Markdown files remain audit evidence. They are deliberately not parsed
    for actions, so a prose recommendation cannot create a second execution
    path beside ``resolved_decision``.
    """
    manifest = copy.deepcopy(dict(workflow_manifest))
    decision = copy.deepcopy(dict(decision_artifact))
    validate(manifest, "workflow_round_manifest")
    validate(decision, "decision_artifact")
    if agent_recommendation is not None:
        validate_agent_recommendation(
            agent_recommendation,
            decision.get("candidate_actions", []),
        )
    artifact = {
        "schema_version": "1.0",
        "bridge_version": WORKFLOW_BRIDGE_VERSION,
        "workflow_mode": manifest["workflow_mode"],
        "object_id": manifest["object_id"],
        "round_id": manifest["round_id"],
        "manifest_ref": manifest["config_file"],
        "narrative_refs": {
            "component_analysis": _optional_file(
                component_analysis_file, "component_analysis_file"
            ),
            "working_note": _optional_file(
                working_note_file or manifest.get("working_note_file"),
                "working_note_file",
            ),
        },
        "numeric_evidence_ref": (decision.get("evidence_refs") or {}).get(
            "numeric_evidence"
        ),
        "vlm_evidence_ref": (decision.get("evidence_refs") or {}).get(
            "vlm_evidence"
        ),
        "rule_trace": copy.deepcopy(decision.get("rule_trace", [])),
        "raw_decision": copy.deepcopy(decision.get("raw_decision")),
        "resolved_decision": decision,
        "candidate_actions": copy.deepcopy(decision.get("candidate_actions", [])),
        "agent_recommendation": copy.deepcopy(agent_recommendation),
        "execution_authority": "policy.resolved_decision",
    }
    return artifact


def preflight_action(
    decision_artifact: Mapping[str, Any],
    workflow_manifest: Mapping[str, Any],
    *,
    current_model_labels: Sequence[str] = (),
    allow_remove: bool = False,
    remove_pilot_passed: bool = False,
) -> dict[str, Any]:
    """Validate one resolved action before a config is generated or fitted."""
    decision = copy.deepcopy(dict(decision_artifact))
    manifest = copy.deepcopy(dict(workflow_manifest))
    validate(decision, "decision_artifact")
    validate(manifest, "workflow_round_manifest")
    action = decision.get("action")
    candidate_actions = decision.get("candidate_actions") or []
    candidate_types = [
        item.get("action", {}).get("action_type")
        for item in candidate_actions
        if isinstance(item, Mapping) and isinstance(item.get("action"), Mapping)
    ]
    summary = {
        "raw_action_type": (
            (decision.get("raw_decision") or {}).get("action") or {}
        ).get("action_type"),
        "resolved_action_type": (
            action.get("action_type") if isinstance(action, Mapping) else None
        ),
        "candidate_action_types": candidate_types,
        "executed_action_type": None,
        "refit_verdict": None,
        "fallback": (decision.get("automation") or {}).get("resolution"),
        "needs_review": bool(
            (decision.get("automation") or {}).get("needs_review", False)
        ),
    }
    if action is None:
        return {
            "bridge_version": WORKFLOW_BRIDGE_VERSION,
            "ok": False,
            "mode": "review_only",
            "reason_code": "NO_RESOLVED_ACTION",
            "reason": "resolved decision has no executable action",
            "next_transition": "REVIEW",
            "should_write_config": False,
            "should_fit": False,
            "action_summary": summary,
        }
    if not isinstance(action, Mapping):
        raise ValueError("decision action must be an object or null")
    action_type = action.get("action_type")
    if action_type not in _ACTION_TYPES | {"INCONCLUSIVE"}:
        raise ValueError(f"unsupported workflow action: {action_type!r}")
    selected = [
        item for item in candidate_actions if item.get("status") == "SELECTED"
    ]
    if len(selected) > 1:
        raise ValueError("decision contains more than one SELECTED candidate action")
    target = action.get("target_model_label")
    if target and current_model_labels and target not in set(current_model_labels):
        return {
            "bridge_version": WORKFLOW_BRIDGE_VERSION,
            "ok": False,
            "mode": "review_only",
            "reason_code": "UNKNOWN_MODEL_LABEL",
            "reason": (
                "target model label is not present in the current model: "
                f"{target}"
            ),
            "action_summary": summary,
        }
    if action_type == "PROPOSE_REMOVE" and not (allow_remove and remove_pilot_passed):
        return {
            "bridge_version": WORKFLOW_BRIDGE_VERSION,
            "ok": False,
            "mode": "review_only",
            "reason_code": "REMOVE_FEATURE_DISABLED",
            "reason": (
                "PROPOSE_REMOVE requires an explicit feature flag and a passed remove pilot"
            ),
            "next_transition": "REVIEW",
            "should_write_config": False,
            "should_fit": False,
            "action_summary": summary,
        }
    if action_type in {"INCONCLUSIVE", "KEEP_AND_CONTINUE", "CONVERGED"}:
        next_transition = (
            "VERIFY_BEST_ROUND"
            if action_type == "CONVERGED"
            else "REVIEW"
        )
        return {
            "bridge_version": WORKFLOW_BRIDGE_VERSION,
            "ok": False,
            "mode": "review_only",
            "reason_code": f"{action_type}_DOES_NOT_FIT",
            "reason": "this decision requires review or verification and does not start a fit",
            "next_transition": next_transition,
            "should_write_config": False,
            "should_fit": False,
            "action_summary": summary,
        }
    if action_type == "PROPOSE_ADD" and manifest["workflow_mode"] == SINGLE_BAND:
        component = action.get("component")
        if component not in {"disk", "bulge", "bar", "psf"}:
            return {
                "bridge_version": WORKFLOW_BRIDGE_VERSION,
                "ok": False,
                "mode": "review_only",
                "reason_code": "SINGLE_BAND_ACTION_TEMPLATE_UNAVAILABLE",
                "reason": (
                    "single-band automatic add requires an existing deterministic "
                    f"template; component {component!r} is not supported"
                ),
                "action_summary": summary,
            }
    if action_type == "PROPOSE_REPLACE" and action.get("replace_to") == "edge_on_disk":
        return {
            "bridge_version": WORKFLOW_BRIDGE_VERSION,
            "ok": False,
            "mode": "review_only",
            "reason_code": "EDGE_ON_DISK_OUT_OF_SCOPE",
            "reason": "Edge-on Disk optimization is outside this workflow redesign",
            "next_transition": "REVIEW",
            "should_write_config": False,
            "should_fit": False,
            "action_summary": summary,
        }
    if action_type == "PROPOSE_REPLACE" and action.get("replace_to") not in {
        "disk", "bulge", "bar", "agn", "companion", "lens",
    }:
        return {
            "bridge_version": WORKFLOW_BRIDGE_VERSION,
            "ok": False,
            "mode": "review_only",
            "reason_code": "REPLACE_TEMPLATE_UNAVAILABLE",
            "reason": "replacement target has no deterministic multi-band template",
            "next_transition": "REVIEW",
            "should_write_config": False,
            "should_fit": False,
            "action_summary": summary,
        }
    if action_type == "REFIT_PARAMETERS" and not action.get("parameter_changes"):
        raise ValueError("REFIT_PARAMETERS requires at least one parameter change")
    summary["action_validated"] = True
    return {
        "bridge_version": WORKFLOW_BRIDGE_VERSION,
        "ok": True,
        "mode": "execute",
        "reason_code": "ACTION_PREFLIGHT_PASSED",
        "action": copy.deepcopy(dict(action)),
        "next_transition": "RUN_FIT",
        "should_write_config": True,
        "should_fit": True,
        "action_summary": summary,
    }


def validate_agent_recommendation(
    recommendation: Mapping[str, Any],
    candidate_actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate bounded Agent/VLM ranking without granting execution authority."""
    value = copy.deepcopy(dict(recommendation))
    try:
        validate(value, "agent_recommendation")
    except Exception as exc:
        raise ValueError(f"invalid agent recommendation schema: {exc}") from exc
    required = (
        "schema_version",
        "recommended_rule_id",
        "rationale",
        "evidence_refs",
        "uncertainty",
    )
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError(f"agent recommendation is missing fields: {missing}")
    forbidden = sorted(_DIRECT_AGENT_FIELDS.intersection(value))
    if forbidden:
        raise ValueError(
            "agent recommendation cannot contain execution fields: "
            f"{forbidden}"
        )
    candidates_by_rule = {
        str(item.get("rule_id")): item
        for item in candidate_actions
        if item.get("rule_id")
    }
    rule_id = str(value["recommended_rule_id"])
    if rule_id not in candidates_by_rule:
        raise ValueError(
            "agent recommendation references a rule outside candidate set: "
            f"{rule_id}"
        )
    priorities = value.get("candidate_priorities", [])
    if not isinstance(priorities, list):
        raise ValueError("candidate_priorities must be a list")
    for item in priorities:
        if (
            not isinstance(item, Mapping)
            or str(item.get("rule_id")) not in candidates_by_rule
        ):
            raise ValueError(
                "candidate_priorities contains a rule outside candidate set"
            )
    suggested = value.get("suggested_action")
    if suggested is not None and suggested != candidates_by_rule[rule_id].get("action"):
        raise ValueError(
            "suggested_action must exactly match the selected rule candidate"
        )
    value["execution_authority"] = "policy.resolved_decision"
    return value


def next_config_path(source_config: str | Path, *, round_index: int | None = None) -> str:
    """Return a new sibling config path without touching the source config."""
    source = Path(_required_file(source_config, "source_config"))
    if round_index is None:
        match = re.search(r"_iter(\d+)$", source.stem)
        round_index = int(match.group(1)) + 1 if match else 1
    if round_index < 1:
        raise ValueError("round_index must be positive")
    base = re.sub(r"_iter\d+$", "", source.stem)
    return str(source.with_name(f"{base}_iter{round_index}{source.suffix}"))


def prepare_mcp_fit_call(
    workflow_manifest: Mapping[str, Any],
    *,
    config_file: str | Path | None = None,
    preflight: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the existing MCP fit call; this function never runs the fit."""
    validate(dict(workflow_manifest), "workflow_round_manifest")
    if preflight is not None and not preflight.get("ok"):
        raise ValueError(
            "cannot prepare MCP call after failed preflight: "
            f"{preflight.get('reason_code')}"
        )
    config = _required_file(
        config_file or workflow_manifest["config_file"], "config_file"
    )
    mode = workflow_manifest["workflow_mode"]
    if mode == SINGLE_BAND:
        return {
            "tool": "run_galfit",
            "arguments": {"config_file": config, "options": []},
            "execute_by": "workflow_agent",
            "executed_here": False,
        }
    extra_args = ["--fit_method", "ES"]
    constraint_files = workflow_manifest.get("constraint_files") or []
    if mode == MULTI_BAND and constraint_files:
        extra_args.extend(["--parconstrain", str(constraint_files[-1])])
    return {
        "tool": "run_galfits_image_fitting",
        "arguments": {
            "config_file": config,
            "extra_args": extra_args,
        },
        "execute_by": "workflow_agent",
        "executed_here": False,
    }


_SINGLE_PARAMETER_LINES = {
    "mag": 3,
    "re": 4,
    "n": 5,
    "q": 9,
    "pa": 10,
}
_MULTI_PARAMETER_LINES = {
    "x": 3,
    "y": 4,
    "re": 5,
    "n": 6,
    "pa": 7,
    "q": 8,
}


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid fitting parameter value")
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _single_component_number(
    target: str,
    block_numbers: Sequence[int],
    content: str | None = None,
) -> int:
    label = str(target).strip().lower()
    object_match = re.fullmatch(r"obj(\d+)", label)
    if object_match:
        index = int(object_match.group(1))
        if index < len(block_numbers):
            return block_numbers[index]
    if re.fullmatch(r"\d+", label):
        number = int(label)
        if number in block_numbers:
            return number
    semantic_match = re.fullmatch(r"([a-z_]+?)(\d+)?", label)
    if content is not None and semantic_match:
        role = semantic_match.group(1)
        occurrence = int(semantic_match.group(2) or "1")
        aliases = {
            "disk": {"disk", "expdisk"},
            "bulge": {"bulge", "devauc"},
            "bar": {"bar"},
            "psf": {"psf", "agn", "nucleus"},
            "agn": {"psf", "agn", "nucleus"},
            "nucleus": {"psf", "agn", "nucleus"},
        }
        roles = aliases.get(role, {role})
        matches = []
        for number, start, end in _single_blocks(content):
            block = content[start:end].lower()
            type_match = re.search(r"(?m)^\s*0\)\s*([a-z_]+)", block)
            component_type = type_match.group(1) if type_match else ""
            header_comments = " ".join(
                line.split("#", 1)[1]
                for line in block.splitlines()
                if "#" in line
            )
            if component_type in roles or role in header_comments:
                matches.append(number)
        if len(matches) >= occurrence:
            return matches[occurrence - 1]
    raise ValueError(
        f"single-band target model label has no unambiguous component mapping: {target!r}"
    )


def _single_blocks(content: str) -> list[tuple[int, int, int]]:
    pattern = re.compile(r"(?m)^\s*#\s*(?:Object|Component)\s+number:\s*(\d+)")
    matches = list(pattern.finditer(content))
    return [
        (int(match.group(1)), match.start(), matches[index + 1].start() if index + 1 < len(matches) else len(content))
        for index, match in enumerate(matches)
    ]


def _replace_galfit_line(
    block: str,
    line_number: int,
    operation: str,
    value: Any = None,
) -> str:
    pattern = re.compile(rf"(?m)^(\s*{line_number}\))([^#\n]*)(.*)$")
    match = pattern.search(block)
    if not match:
        raise ValueError(f"GALFIT component has no {line_number}) parameter line")
    body = match.group(2).strip().split()
    comment = match.group(3)
    if not body:
        raise ValueError(f"GALFIT parameter line {line_number}) is empty")
    if operation in {"SET_INITIAL", "FIX_VALUE", "FREE_VALUE"}:
        if value is not None:
            body[0] = _format_scalar(value)
        if operation == "FIX_VALUE":
            if len(body) < 2:
                body.append("0")
            body[1] = "0"
        elif operation == "FREE_VALUE":
            if len(body) < 2:
                body.append("1")
            body[1] = "1"
    else:
        raise ValueError(f"operation {operation} is not a direct GALFIT line edit")
    replacement = f"{match.group(1)} {' '.join(body)}{comment}"
    return block[: match.start()] + replacement + block[match.end() :]


def _append_single_constraints(
    config_text: str,
    target_config: str,
    source_config: str,
    constraints: Sequence[str],
    remove_constraints: Sequence[tuple[int, str]] = (),
    constraint_reference: str | Path | None = None,
) -> tuple[str, bool]:
    """Copy, edit and append the source GALFIT constraint file atomically later."""
    target_constraint = Path(target_config).with_suffix(".cons")
    displayed_constraint = str(constraint_reference or target_constraint)
    if target_constraint.exists():
        raise FileExistsError(f"refusing to overwrite generated constraint file: {target_constraint}")
    source_constraint = None
    from tools.parse_feedme import parse_feedme

    parsed = parse_feedme(source_config)
    raw_constraint = parsed.get("constraint")
    if raw_constraint and str(raw_constraint).strip().lower() != "none":
        candidate = Path(str(raw_constraint)).expanduser()
        if not candidate.is_absolute():
            candidate = Path(source_config).resolve().parent / candidate
        if candidate.is_file():
            source_constraint = candidate
    existing = source_constraint.read_text(encoding="utf-8") if source_constraint else ""
    existing_lines = existing.splitlines()
    for target, parameter in remove_constraints:
        aliases = {parameter.lower()}
        if parameter.lower() == "x,y":
            aliases.update({"x", "y"})
        kept: list[str] = []
        removed = False
        for line in existing_lines:
            fields = line.split("#", 1)[0].split()
            target_token = fields[0] if fields else ""
            parameter_token = fields[1].lower() if len(fields) > 1 else ""
            target_match = target_token == str(target) or bool(
                re.search(rf"(?:^|[_/-]){re.escape(str(target))}(?:$|[_/-])", target_token)
            )
            if target_match and parameter_token in aliases:
                removed = True
                continue
            kept.append(line)
        if not removed:
            raise ValueError(
                f"requested single-band constraint was not found: {target} {parameter}"
            )
        existing_lines = kept
    existing = "\n".join(existing_lines)
    if not constraints and not existing.strip() and not remove_constraints:
        return config_text, False
    existing_set = {line.strip() for line in existing.splitlines() if line.strip()}
    additions = [line for line in constraints if line.strip() not in existing_set]
    target_constraint.write_text(
        existing.rstrip() + ("\n" if existing.strip() and additions else "") + "\n".join(additions) + "\n",
        encoding="utf-8",
    )
    lines = config_text.splitlines(keepends=True)
    updated = False
    for index, line in enumerate(lines):
        if re.match(r"^\s*G\)", line):
            suffix = ""
            if "#" in line:
                suffix = " #" + line.split("#", 1)[1].rstrip("\n")
            lines[index] = f"G) {displayed_constraint}{suffix}\n"
            updated = True
            break
    if not updated:
        lines.insert(0, f"G) {displayed_constraint} # generated action constraints\n")
    return "".join(lines), True


def _apply_single_action(
    source_config: str,
    target_config: str,
    action: Mapping[str, Any],
    constraint_reference: str | Path | None = None,
) -> list[str]:
    content = Path(target_config).read_text(encoding="utf-8")
    blocks = _single_blocks(content)
    if not blocks:
        raise ValueError("single-band config has no numbered component blocks")
    numbers = [item[0] for item in blocks]
    constraints: list[str] = []
    remove_constraints: list[tuple[int, str]] = []
    action_type = action["action_type"]
    if action_type == "REFIT_PARAMETERS":
        for change in action.get("parameter_changes", []):
            parameter = str(change["parameter"]).lower()
            target = _single_component_number(change["target_model_label"], numbers, content)
            block_info = next(item for item in _single_blocks(content) if item[0] == target)
            block = content[block_info[1] : block_info[2]]
            operation = change["operation"]
            if parameter == "x,y":
                if operation == "LINK_CENTER":
                    reference = _single_component_number(change["reference_model_label"], numbers, content)
                    constraints.extend([f"{target}_{reference} x offset", f"{target}_{reference} y offset"])
                    continue
                values = change.get("value")
                if not isinstance(values, (list, tuple)) or len(values) != 2:
                    raise ValueError("single-band x,y SET_INITIAL requires a two-value list")
                line = re.compile(r"(?m)^(\s*1\))([^#\n]*)(.*)$").search(block)
                if not line:
                    raise ValueError("GALFIT component has no 1) position line")
                tokens = line.group(2).strip().split()
                if len(tokens) < 4:
                    raise ValueError("GALFIT position line must contain x, y and two vary flags")
                tokens[0], tokens[1] = _format_scalar(values[0]), _format_scalar(values[1])
                if operation == "FIX_VALUE":
                    tokens[2:] = ["0", "0"]
                elif operation == "FREE_VALUE":
                    tokens[2:] = ["1", "1"]
                replacement = f"{line.group(1)} {' '.join(tokens)}{line.group(3)}"
                block = block[: line.start()] + replacement + block[line.end() :]
            elif parameter == "profile":
                block = _replace_galfit_line(block, 0, operation, change.get("value"))
            elif parameter in _SINGLE_PARAMETER_LINES:
                line_number = _SINGLE_PARAMETER_LINES[parameter]
                if operation == "SET_BOUNDS":
                    lower, upper = change.get("lower"), change.get("upper")
                    if lower is None or upper is None or lower >= upper:
                        raise ValueError("SET_BOUNDS requires lower < upper")
                    constraints.append(f"{target} {parameter} {_format_scalar(lower)} {_format_scalar(upper)}")
                    continue
                if operation == "REMOVE_NONREQUIRED_CONSTRAINT":
                    remove_constraints.append((target, parameter))
                    continue
                block = _replace_galfit_line(block, line_number, operation, change.get("value"))
            else:
                raise ValueError(f"unsupported single-band parameter: {parameter}")
            content = content[: block_info[1]] + block + content[block_info[2] :]
    elif action_type == "PROPOSE_ADD":
        component = action.get("component")
        if component not in {"disk", "bulge", "bar", "psf"}:
            raise ValueError(
                f"single-band automatic add has no deterministic template for {component!r}"
            )
        from tools.modify_feedme import add_components

        content = add_components(content, [component])
    elif action_type == "PROPOSE_REMOVE":
        target = _single_component_number(action["target_model_label"], numbers, content)
        block_info = next(item for item in _single_blocks(content) if item[0] == target)
        content = content[: block_info[1]] + content[block_info[2] :]
    else:
        raise ValueError(f"single-band action config writer does not implement {action_type}")
    content, generated_constraints = _append_single_constraints(
        content,
        target_config,
        source_config,
        constraints,
        remove_constraints,
        constraint_reference,
    )
    Path(target_config).write_text(content, encoding="utf-8")
    return [str(Path(target_config).with_suffix(".cons"))] if generated_constraints else []


def _validate_single_action_mapping(source_config: str, action: Mapping[str, Any]) -> None:
    """Validate source component references before creating a new round file."""
    content = Path(source_config).read_text(encoding="utf-8")
    blocks = _single_blocks(content)
    if not blocks:
        raise ValueError("single-band config has no numbered component blocks")
    numbers = [item[0] for item in blocks]
    if action["action_type"] == "REFIT_PARAMETERS":
        for change in action.get("parameter_changes", []):
            _single_component_number(change["target_model_label"], numbers, content)
            if str(change["parameter"]).lower() == "x,y" and change["operation"] == "LINK_CENTER":
                _single_component_number(
                    change["reference_model_label"], numbers, content
                )
    elif action["action_type"] == "PROPOSE_REMOVE":
        _single_component_number(action["target_model_label"], numbers, content)


def _profile_blocks(content: str) -> list[tuple[str, int, int]]:
    pattern = re.compile(r"(?m)^\s*P([A-Za-z])1\)\s*\S+.*$")
    matches = list(pattern.finditer(content))
    galaxy_starts = [match.start() for match in re.finditer(r"(?m)^\s*G[A-Za-z]1\)", content)]
    blocks = []
    for index, match in enumerate(matches):
        next_profile = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        next_galaxy = next((start for start in galaxy_starts if start > match.start()), len(content))
        blocks.append((match.group(1), match.start(), min(next_profile, next_galaxy)))
    return blocks


def _find_profile_prefix(content: str, target: str) -> str:
    blocks = _profile_blocks(content)
    target_lower = str(target).lower()
    for prefix, start, end in blocks:
        block = content[start:end]
        name_match = re.search(rf"(?m)^\s*P{re.escape(prefix)}1\)\s*(\S+)", block)
        if name_match and name_match.group(1).lower() == target_lower:
            return prefix
        if prefix.lower() == target_lower:
            return prefix
    obj_match = re.fullmatch(r"obj(\d+)", target_lower)
    if obj_match:
        index = int(obj_match.group(1))
        if index < len(blocks):
            return blocks[index][0]
    raise ValueError(f"multi-band target model label not found in lyric: {target}")


def _next_profile_prefix(content: str) -> str:
    existing = {prefix.lower() for prefix, _, _ in _profile_blocks(content)}
    for code in range(ord("a"), ord("z") + 1):
        candidate = chr(code)
        if candidate not in existing:
            return candidate
    raise ValueError("no unused single-letter GalfitS profile label is available")


def _replace_lyric_tuple(
    block: str,
    prefix: str,
    field: int,
    operation: str,
    value: Any = None,
    lower: Any = None,
    upper: Any = None,
) -> str:
    pattern = re.compile(rf"(?m)^(\s*P{re.escape(prefix)}{field}\)\s*)([^#\n]*)(.*)$")
    match = pattern.search(block)
    if not match:
        raise ValueError(f"lyric profile {prefix} has no P{prefix}{field}) line")
    raw = match.group(2).strip()
    try:
        parsed = ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"lyric parameter P{prefix}{field}) is not parseable") from exc
    if not isinstance(parsed, list) or len(parsed) < 5 or not all(
        isinstance(item, (int, float)) for item in parsed[:5]
    ):
        raise ValueError(f"lyric parameter P{prefix}{field}) is not a numeric 5-tuple")
    if operation in {"SET_INITIAL", "FIX_VALUE", "FREE_VALUE"} and value is not None:
        parsed[0] = value
    if operation == "SET_BOUNDS":
        if lower is None or upper is None or lower >= upper:
            raise ValueError("SET_BOUNDS requires lower < upper")
        parsed[1], parsed[2] = lower, upper
        if not parsed[1] <= parsed[0] <= parsed[2]:
            raise ValueError("SET_BOUNDS must contain the initial value")
    if operation == "FIX_VALUE":
        parsed[4] = 0
    elif operation == "FREE_VALUE":
        parsed[4] = 1
    replacement = f"{match.group(1)}{repr(parsed)}{match.group(3)}"
    return block[: match.start()] + replacement + block[match.end() :]


def _apply_multi_action(
    source_config: str,
    target_config: str,
    action: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None,
) -> list[str]:
    content = Path(target_config).read_text(encoding="utf-8")
    action_type = action["action_type"]
    constraints: list[str] = []
    if action_type == "REFIT_PARAMETERS":
        for change in action.get("parameter_changes", []):
            target_prefix = _find_profile_prefix(content, change["target_model_label"])
            parameter = str(change["parameter"]).lower()
            operation = change["operation"]
            block_info = next(item for item in _profile_blocks(content) if item[0] == target_prefix)
            block = content[block_info[1] : block_info[2]]
            if parameter == "profile":
                pattern = re.compile(rf"(?m)^(\s*P{re.escape(target_prefix)}2\)\s*)([^#\n]*)(.*)$")
                match = pattern.search(block)
                if not match:
                    raise ValueError(f"lyric profile {target_prefix} has no profile type line")
                replacement = f"{match.group(1)}{change.get('value')}{match.group(3)}"
                block = block[: match.start()] + replacement + block[match.end() :]
            elif parameter in _MULTI_PARAMETER_LINES:
                block = _replace_lyric_tuple(
                    block,
                    target_prefix,
                    _MULTI_PARAMETER_LINES[parameter],
                    operation,
                    change.get("value"),
                    change.get("lower"),
                    change.get("upper"),
                )
            elif parameter == "x,y" and operation == "LINK_CENTER":
                reference_prefix = _find_profile_prefix(content, change["reference_model_label"])
                target_name = re.search(rf"(?m)^\s*P{re.escape(target_prefix)}1\)\s*(\S+)", block).group(1)
                ref_block_info = next(item for item in _profile_blocks(content) if item[0] == reference_prefix)
                ref_block = content[ref_block_info[1] : ref_block_info[2]]
                reference_name = re.search(rf"(?m)^\s*P{re.escape(reference_prefix)}1\)\s*(\S+)", ref_block).group(1)
                constraints.append(
                    "def Update_Constraints(pardictlc):\n"
                    f"    pardictlc['{target_name}_xcen'] = 1 * pardictlc['{reference_name}_xcen']\n"
                    f"    pardictlc['{target_name}_ycen'] = 1 * pardictlc['{reference_name}_ycen']\n"
                )
                continue
            else:
                raise ValueError(f"unsupported multi-band parameter: {parameter}")
            content = content[: block_info[1]] + block + content[block_info[2] :]
    elif action_type == "PROPOSE_ADD":
        plan = dict((agent_recommendation or {}).get("parameter_plan") or {})
        if "raw_text" in plan or "config_text" in plan:
            raise ValueError("parameter_plan cannot contain raw configuration text")
        component = str(action.get("component"))
        profile_name = str(plan.get("profile_name") or component)
        profile_type = str(plan.get("profile_type") or "sersic")
        template_target = str(plan.get("template_model_label") or "")
        template_prefix = _find_profile_prefix(content, template_target) if template_target else _profile_blocks(content)[0][0]
        template_info = next(item for item in _profile_blocks(content) if item[0] == template_prefix)
        new_prefix = str(plan.get("new_prefix") or _next_profile_prefix(content))
        if not re.fullmatch(r"[A-Za-z]", new_prefix) or any(item[0].lower() == new_prefix.lower() for item in _profile_blocks(content)):
            raise ValueError(f"invalid or duplicate new profile prefix: {new_prefix}")
        block = content[template_info[1] : template_info[2]]
        block = re.sub(rf"P{re.escape(template_prefix)}(?=\d+\))", f"P{new_prefix}", block)
        block = re.sub(rf"(?m)^(\s*P{re.escape(new_prefix)}1\)\s*)\S+", rf"\g<1>{profile_name}", block)
        block = re.sub(rf"(?m)^(\s*P{re.escape(new_prefix)}2\)\s*)\S+", rf"\g<1>{profile_type}", block)
        parameters = plan.get("parameters") or {}
        if not isinstance(parameters, Mapping):
            raise ValueError("parameter_plan.parameters must be an object")
        if component == "lens" and not {"re", "n", "q"}.issubset(parameters):
            raise ValueError("Lens parameter_plan requires re, n and q")
        for parameter, planned in parameters.items():
            field = _MULTI_PARAMETER_LINES.get(str(parameter).lower())
            if field is None:
                continue
            tuple_value = planned.get("value") if isinstance(planned, Mapping) else planned
            if isinstance(tuple_value, (list, tuple)) and len(tuple_value) >= 5:
                pattern = re.compile(rf"(?m)^(\s*P{re.escape(new_prefix)}{field}\)\s*)[^#\n]*(.*)$")
                match = pattern.search(block)
                if match:
                    block = block[: match.start()] + f"{match.group(1)}{repr(list(tuple_value))}{match.group(2)}" + block[match.end() :]
        insert_at = min((match.start() for match in re.finditer(r"(?m)^\s*G[A-Za-z]1\)", content)), default=len(content))
        content = content[:insert_at] + block.rstrip() + "\n\n" + content[insert_at:]
        galaxy_label = str(plan.get("galaxy_label") or "a")
        galaxy_pattern = re.compile(rf"(?m)^(\s*G{re.escape(galaxy_label)}2\)\s*)([^#\n]*)(.*)$")
        galaxy_match = galaxy_pattern.search(content)
        if not galaxy_match:
            raise ValueError(f"galaxy block G{galaxy_label}2) not found for new profile")
        members = ast.literal_eval(galaxy_match.group(2).strip())
        if not isinstance(members, list):
            raise ValueError(f"G{galaxy_label}2) is not a profile list")
        members.append(new_prefix)
        content = content[: galaxy_match.start()] + f"{galaxy_match.group(1)}{repr(members)}{galaxy_match.group(3)}" + content[galaxy_match.end() :]
    else:
        raise ValueError(f"multi-band action config writer does not implement {action_type}")
    if constraints:
        constraint_path = Path(target_config).with_suffix(".constrain")
        if constraint_path.exists():
            raise FileExistsError(f"refusing to overwrite generated constraint file: {constraint_path}")
        constraint_path.write_text("\n".join(constraints) + "\n", encoding="utf-8")
        return_constraints = [str(constraint_path)]
    else:
        return_constraints = []
    Path(target_config).write_text(content, encoding="utf-8")
    return return_constraints


def _profile_blocks(content: str) -> list[tuple[str, int, int]]:
    """Return P blocks without swallowing following N or G blocks."""
    pattern = re.compile(r"(?m)^\s*P([A-Za-z])1\)\s*\S+.*$")
    matches = list(pattern.finditer(content))
    boundaries = [
        match.start()
        for match in re.finditer(r"(?m)^\s*[NG][A-Za-z]1\)", content)
    ]
    blocks = []
    for index, match in enumerate(matches):
        next_profile = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        next_other = next((start for start in boundaries if start > match.start()), len(content))
        blocks.append((match.group(1), match.start(), min(next_profile, next_other)))
    return blocks


def _nucleus_prefixes(content: str) -> set[str]:
    return {match.group(1).lower() for match in re.finditer(r"(?m)^\s*N([A-Za-z])1\)", content)}


def _next_prefixed_label(content: str, prefix: str) -> str:
    pattern = re.compile(rf"(?m)^\s*{re.escape(prefix)}([A-Za-z])1\)")
    existing = {match.group(1).lower() for match in pattern.finditer(content)}
    for code in range(ord("a"), ord("z") + 1):
        candidate = chr(code)
        if candidate not in existing:
            return candidate
    raise ValueError(f"no unused {prefix} label is available")


def _profile_name(content: str, prefix: str) -> str:
    block_info = next(item for item in _profile_blocks(content) if item[0] == prefix)
    block = content[block_info[1] : block_info[2]]
    match = re.search(rf"(?m)^\s*P{re.escape(prefix)}1\)\s*(\S+)", block)
    if not match:
        raise ValueError(f"profile {prefix} has no component name")
    return match.group(1)


def _profile_initial(content: str, prefix: str, field: int) -> float:
    block_info = next(item for item in _profile_blocks(content) if item[0] == prefix)
    block = content[block_info[1] : block_info[2]]
    pattern = re.compile(rf"(?m)^\s*P{re.escape(prefix)}{field}\)\s*([^#\n]+)")
    match = pattern.search(block)
    if not match:
        raise ValueError(f"profile {prefix} has no P{prefix}{field}) line")
    parsed = ast.literal_eval(match.group(1).strip())
    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], (int, float)):
        raise ValueError(f"profile {prefix} P{prefix}{field}) has no numeric initial value")
    return float(parsed[0])


def _replace_lyric_scalar(block: str, prefix: str, field: int, value: Any) -> str:
    pattern = re.compile(rf"(?m)^(\s*P{re.escape(prefix)}{field}\)\s*)([^#\n]*)(.*)$")
    match = pattern.search(block)
    if not match:
        raise ValueError(f"lyric profile {prefix} has no P{prefix}{field}) line")
    return block[: match.start()] + f"{match.group(1)}{_format_scalar(value)}{match.group(3)}" + block[match.end() :]


def _replace_profile_type(block: str, prefix: str, profile_type: str) -> str:
    pattern = re.compile(rf"(?m)^(\s*P{re.escape(prefix)}2\)\s*)([^#\n]*)(.*)$")
    match = pattern.search(block)
    if not match:
        raise ValueError(f"lyric profile {prefix} has no profile type line")
    return block[: match.start()] + f"{match.group(1)}{profile_type}{match.group(3)}" + block[match.end() :]


def _planned_tuple(
    parameters: Mapping[str, Any],
    name: str,
    default: list[Any],
) -> list[Any]:
    planned = parameters.get(name)
    value = planned.get("value") if isinstance(planned, Mapping) else planned
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple)) or len(value) < 5:
        raise ValueError(f"parameter_plan.parameters[{name!r}] must be a numeric 5-tuple")
    if not all(isinstance(item, (int, float)) for item in value[:5]):
        raise ValueError(f"parameter_plan.parameters[{name!r}] must be a numeric 5-tuple")
    return list(value)


def _multi_profile_template(
    prefix: str,
    name: str,
    component: str,
    parameters: Mapping[str, Any],
    band_count: int,
) -> str:
    defaults = {
        "disk": (3.0, 1.0, 0.3),
        "bulge": (1.0, 4.0, 0.8),
        "bar": (0.8, 0.5, 0.3),
        "lens": (1.0, 0.3, 0.7),
        "companion": (0.12, 2.0, 0.8),
    }
    if component not in defaults:
        raise ValueError(f"no multi-band P template exists for {component!r}")
    re_default, n_default, q_default = defaults[component]
    re_tuple = _planned_tuple(parameters, "re", [re_default, 0.01, 12.0, 0.01, 1])
    n_tuple = _planned_tuple(parameters, "n", [n_default, 0.1, 8.0, 0.1, 1])
    q_tuple = _planned_tuple(parameters, "q", [q_default, 0.05, 1.0, 0.01, 1])
    if component == "disk":
        n_tuple[0], n_tuple[4] = 1.0, 0
    if component == "bar":
        n_tuple[0], n_tuple[4] = 0.5, 0
    x_tuple = _planned_tuple(parameters, "x", [0.0, -5.0, 5.0, 0.1, 1])
    y_tuple = _planned_tuple(parameters, "y", [0.0, -5.0, 5.0, 0.1, 1])
    pa_tuple = _planned_tuple(parameters, "pa", [0.0, -180.0, 180.0, 1.0, 1])
    sed_bands = max(int(band_count), 1)
    lines = [
        f"P{prefix}1) {name}",
        f"P{prefix}2) sersic",
        f"P{prefix}3) {repr(x_tuple)}",
        f"P{prefix}4) {repr(y_tuple)}",
        f"P{prefix}5) {repr(re_tuple)}",
        f"P{prefix}6) {repr(n_tuple)}",
        f"P{prefix}7) {repr(pa_tuple)}",
        f"P{prefix}8) {repr(q_tuple)}",
        f"P{prefix}9) " + repr([[-2.0, -8.0, 0.0, 0.1, 0] for _ in range(sed_bands)]),
        f"P{prefix}10) [1, 0.01, 11, 0.1, 0]",
        f"P{prefix}11) [[0.02, 0.001, 0.04, 0.001, 0]]",
        f"P{prefix}12) [[0.7, 0.0, 5.1, 0.1, 0]]",
        f"P{prefix}13) [100, 40, 200, 1, 0]",
        f"P{prefix}14) [9.0, 6, 12, 0.1, 0]" if component == "companion" else f"P{prefix}14) [10.14, 8.5, 12, 0.1, 0]",
        f"P{prefix}15) bins",
        f"P{prefix}16) [-2, -4, -2, 0.1, 0]",
        f"P{prefix}26) [3, 0, 5, 0.1, 0]",
        f"P{prefix}27) 0",
        f"P{prefix}28) [8.14, 4.5, 10, 0.1, 0]",
        f"P{prefix}29) [1.0, 0.1, 50, 0.1, 0]",
        f"P{prefix}30) [1.0, 0.47, 7.32, 0.1, 0]",
        f"P{prefix}31) [1.0, 1.0, 3.0, 0.1, 0]",
        f"P{prefix}32) [0.1, 0, 1.0, 0.1, 1]",
    ]
    return "\n".join(lines) + "\n"


def _fourier_lines(prefix: str) -> list[str]:
    return [
        f"P{prefix}17) [0.1, 0.01, 3.0, 0.1, 1]",
        f"P{prefix}18) [1.0, 0.1, 10.0, 0.1, 1]",
        f"P{prefix}19) [1.0, 0.5, 3.0, 0.1, 1]",
        f"P{prefix}20) [0.0, 0.0, 180.0, 1.0, 0]",
        f"P{prefix}21) 1",
        f"P{prefix}22) [0.05, 0.0, 0.6, 0.01, 1]",
        f"P{prefix}23) [0.0, 0.0, 180.0, 1.0, 1]",
        f"P{prefix}24) [30.0, 0.0, 90.0, 1.0, 1]",
    ]


def _enable_fourier_m1(content: str, prefix: str, parameters: Mapping[str, Any]) -> str:
    block_info = next(item for item in _profile_blocks(content) if item[0] == prefix)
    block = content[block_info[1] : block_info[2]]
    block = _replace_profile_type(block, prefix, "sersic_f")
    if not re.search(rf"(?m)^\s*P{re.escape(prefix)}21\)", block):
        marker = re.search(rf"(?m)^\s*P{re.escape(prefix)}26\)", block)
        insertion = "\n".join(_fourier_lines(prefix)) + "\n"
        block = block[: marker.start()] + insertion + block[marker.start() :] if marker else block.rstrip() + "\n" + insertion
    block = _replace_lyric_scalar(block, prefix, 21, 1)
    block = _replace_lyric_tuple(block, prefix, 20, "SET_INITIAL", 0.0)
    for parameter, field in {"r_in": 17, "r_out": 18, "alpha": 19, "theta_out": 20, "am": 22, "theta_m": 23, "i_m": 24}.items():
        planned = parameters.get(parameter)
        if planned is None:
            continue
        value = planned.get("value") if isinstance(planned, Mapping) else planned
        if isinstance(value, (list, tuple)) and len(value) >= 5:
            block = _replace_lyric_tuple(block, prefix, field, "SET_INITIAL", value[0])
            block = _replace_lyric_tuple(block, prefix, field, "SET_BOUNDS", value[1], value[1], value[2])
    return content[: block_info[1]] + block + content[block_info[2] :]


def _galaxy_block_info(content: str, label: str) -> tuple[str, int, int]:
    matches = list(re.finditer(r"(?m)^\s*G([A-Za-z])1\)\s*([^#\n]+)", content))
    for index, match in enumerate(matches):
        if match.group(1).lower() == label.lower() or match.group(2).strip().lower() == label.lower():
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            return match.group(1), match.start(), end
    raise ValueError(f"galaxy block not found: {label}")


def _add_profile_to_galaxy(content: str, galaxy_label: str, prefix: str) -> str:
    actual, start, end = _galaxy_block_info(content, galaxy_label)
    block = content[start:end]
    match = re.search(rf"(?m)^(\s*G{re.escape(actual)}2\)\s*)([^#\n]*)(.*)$", block)
    if not match:
        raise ValueError(f"galaxy block G{actual} has no profile member list")
    members = ast.literal_eval(match.group(2).strip())
    if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
        raise ValueError(f"galaxy block G{actual}2) is not a profile list")
    if prefix not in members:
        members.append(prefix)
    block = block[: match.start()] + f"{match.group(1)}{repr(members)}{match.group(3)}" + block[match.end() :]
    return content[:start] + block + content[end:]


def _insert_before_model_blocks(content: str, block: str) -> str:
    locations = [match.start() for match in re.finditer(r"(?m)^\s*[NG][A-Za-z]1\)", content)]
    insert_at = min(locations, default=len(content))
    return content[:insert_at] + block.rstrip() + "\n\n" + content[insert_at:]


def _append_companion_galaxy(content: str, prefix: str, profile_prefix: str, name: str) -> str:
    galaxy_prefix = _next_prefixed_label(content, "G")
    return content.rstrip() + (
        f"\n\n# independent companion galaxy\n"
        f"G{galaxy_prefix}1) {name}\n"
        f"G{galaxy_prefix}2) ['{profile_prefix}']\n"
        f"G{galaxy_prefix}3) [0.1, 0.0, 1.0, 0.1, 0]\n"
        f"G{galaxy_prefix}4) 0\n"
        f"G{galaxy_prefix}5) [1.0, 0.5, 2.0, 0.05, 0]\n"
        f"G{galaxy_prefix}6) []\n"
        f"G{galaxy_prefix}7) 1\n"
    )


def _nucleus_template(prefix: str, name: str, parameters: Mapping[str, Any]) -> str:
    x = parameters.get("x", [0.0, -5.0, 5.0, 0.1, 1])
    y = parameters.get("y", [0.0, -5.0, 5.0, 0.1, 1])
    x = x.get("value") if isinstance(x, Mapping) else x
    y = y.get("value") if isinstance(y, Mapping) else y
    return "\n".join([
        f"N{prefix}1) {name}",
        f"N{prefix}2) [0.1, 0.0, 1.0, 0.1, 0]",
        f"N{prefix}3) 0.0",
        f"N{prefix}4) {repr(x)}",
        f"N{prefix}5) {repr(y)}",
        f"N{prefix}6) [7, 5, 10, 0.1, 0]",
        f"N{prefix}7) [-1, -4, 2, 0.1, 0]",
        f"N{prefix}8) [0, 0, 0.99, 0.01, 0]",
        f"N{prefix}9) [0, 0, 3.1, 0.1, 0]",
        f"N{prefix}10) [43, 41, 47, 0.1, 1]",
        f"N{prefix}11) [[1, 0, 4, 0.1, 1], [0.6, 0, 5, 0.1, 1]]",
        f"N{prefix}12) ['Hg', 'Hb', 'Ha']",
        f"N{prefix}13) ['Hg', 'Hb', 'Ha']",
        f"N{prefix}14) 1",
        f"N{prefix}15) 1",
        f"N{prefix}16) 0",
        f"N{prefix}17) 0",
        f"N{prefix}18) 0",
        f"N{prefix}19) [1.0, 0.5, 2.0, 0.05, 0]",
        f"N{prefix}20) 0",
        f"N{prefix}21) [41, 39, 44, 0.1, 0]",
        f"N{prefix}22) [-0.5, -2.5, -0.25, 0.05, 0]",
        f"N{prefix}23) [0.5, 0.25, 1.5, 0.05, 0]",
        f"N{prefix}24) [7, 5, 10, 0.5, 0]",
        f"N{prefix}25) [15, 0, 90, 5, 0]",
        f"N{prefix}26) [1.0, 0.2, 5.0, 0.1, 1]",
        f"N{prefix}27) [[40, 38, 42, 0.1, 0]]",
    ]) + "\n"


def _constraint_target_match(line: str, target: str, parameter: str) -> bool:
    if target not in line:
        return False
    aliases = {parameter.lower()}
    if parameter.lower() == "x,y":
        aliases.update({"x", "y", "xcen", "ycen"})
    return any(
        re.search(
            rf"(?:^|['_\-]){re.escape(alias)}(?:['_\-]|$)",
            line.lower(),
        )
        for alias in aliases
    )


def _merge_multi_constraints(
    existing_texts: Sequence[str],
    additions: Sequence[str],
    removals: Sequence[tuple[str, str]],
) -> str | None:
    preamble: list[str] = []
    body: list[str] = []
    for text in existing_texts:
        lines = text.splitlines()
        in_function = False
        for line in lines:
            if re.match(r"^\s*def\s+Update_Constraints\s*\(", line):
                in_function = True
                continue
            if in_function and (not line.strip() or line.startswith((" ", "\t"))):
                if line.strip():
                    body.append("    " + line.strip())
                continue
            if in_function:
                in_function = False
            if line.strip():
                preamble.append(line)
    for target, parameter in removals:
        kept = [line for line in body if not _constraint_target_match(line, target, parameter)]
        if len(kept) == len(body):
            raise ValueError(f"requested multi-band constraint was not found: {target} {parameter}")
        body = kept
    for line in additions:
        normalized = line.strip()
        if normalized and normalized not in {item.strip() for item in body}:
            body.append("    " + normalized)
    unique_preamble = list(dict.fromkeys(preamble))
    unique_body = list(dict.fromkeys(body))
    if not unique_preamble and not unique_body:
        return None
    lines = unique_preamble
    if unique_body:
        lines.extend(["def Update_Constraints(pardictlc):", *unique_body])
    return "\n".join(lines).rstrip() + "\n"


def _apply_multi_action(
    source_config: str,
    target_config: str,
    action: Mapping[str, Any],
    agent_recommendation: Mapping[str, Any] | None,
    existing_constraint_files: Sequence[str | Path] = (),
) -> list[str]:
    content = Path(target_config).read_text(encoding="utf-8")
    action_type = action["action_type"]
    constraints: list[str] = []
    removals: list[tuple[str, str]] = []
    plan = dict((agent_recommendation or {}).get("parameter_plan") or {})
    if "raw_text" in plan or "config_text" in plan:
        raise ValueError("parameter_plan cannot contain raw configuration text")
    parameters = plan.get("parameters") or {}
    if not isinstance(parameters, Mapping):
        raise ValueError("parameter_plan.parameters must be an object")
    if action_type == "REFIT_PARAMETERS":
        for change in action.get("parameter_changes", []):
            target_prefix = _find_profile_prefix(content, change["target_model_label"])
            parameter = str(change["parameter"]).lower()
            operation = change["operation"]
            block_info = next(item for item in _profile_blocks(content) if item[0] == target_prefix)
            block = content[block_info[1] : block_info[2]]
            if parameter == "profile":
                if operation != "SET_INITIAL" or not isinstance(change.get("value"), str):
                    raise ValueError("multi-band profile changes only support SET_INITIAL")
                block = _replace_profile_type(block, target_prefix, change["value"])
            elif parameter == "x,y":
                if operation == "LINK_CENTER":
                    reference_prefix = _find_profile_prefix(content, change["reference_model_label"])
                    target_name = _profile_name(content, target_prefix)
                    reference_name = _profile_name(content, reference_prefix)
                    constraints.extend([
                        f"pardictlc['{target_name}_xcen'] = 1 * pardictlc['{reference_name}_xcen']",
                        f"pardictlc['{target_name}_ycen'] = 1 * pardictlc['{reference_name}_ycen']",
                    ])
                    continue
                values = change.get("value")
                if not isinstance(values, (list, tuple)) or len(values) != 2:
                    raise ValueError("multi-band x,y direct edit requires a two-value list")
                for field, value in ((3, values[0]), (4, values[1])):
                    block = _replace_lyric_tuple(block, target_prefix, field, operation, value, change.get("lower"), change.get("upper"))
            elif parameter in _MULTI_PARAMETER_LINES:
                if operation == "REMOVE_NONREQUIRED_CONSTRAINT":
                    removals.append((_profile_name(content, target_prefix), parameter))
                    continue
                block = _replace_lyric_tuple(
                    block,
                    target_prefix,
                    _MULTI_PARAMETER_LINES[parameter],
                    operation,
                    change.get("value"),
                    change.get("lower"),
                    change.get("upper"),
                )
            else:
                raise ValueError(f"unsupported multi-band parameter: {parameter}")
            content = content[: block_info[1]] + block + content[block_info[2] :]
    elif action_type == "PROPOSE_ADD":
        component = str(action.get("component"))
        if component == "fourier_m1":
            try:
                prefix = _find_profile_prefix(content, "disk")
            except ValueError:
                profiles = _profile_blocks(content)
                if len(profiles) != 1 or re.search(rf"(?m)^\s*P{re.escape(profiles[0][0])}2\)\s+s(?:ersic_f?)\b", content) is None:
                    raise ValueError("Fourier m=1 requires an existing Disk or single Sersic profile")
                prefix = profiles[0][0]
            content = _enable_fourier_m1(content, prefix, parameters)
        elif component == "agn":
            nucleus_prefix = _next_prefixed_label(content, "N")
            nucleus_name = str(plan.get("nucleus_name") or "AGN")
            content = _insert_before_model_blocks(content, _nucleus_template(nucleus_prefix, nucleus_name, parameters))
            profiles = _profile_blocks(content)
            if profiles:
                constraints.extend([
                    f"pardictlc['{nucleus_name}_xcen'] = 1 * pardictlc['{_profile_name(content, profiles[0][0])}_xcen']",
                    f"pardictlc['{nucleus_name}_ycen'] = 1 * pardictlc['{_profile_name(content, profiles[0][0])}_ycen']",
                ])
        elif component in {"disk", "bulge", "bar", "lens", "companion"}:
            new_prefix = str(plan.get("new_prefix") or _next_profile_prefix(content))
            if not re.fullmatch(r"[A-Za-z]", new_prefix) or any(item[0].lower() == new_prefix.lower() for item in _profile_blocks(content)):
                raise ValueError(f"invalid or duplicate new profile prefix: {new_prefix}")
            name = str(plan.get("profile_name") or component)
            band_count = len(re.findall(r"(?m)^\s*I[A-Za-z]1\)", content)) or 1
            if component == "lens":
                disk_prefix = _find_profile_prefix(content, "disk")
                bar_prefix = _find_profile_prefix(content, "bar")
                disk_re = _profile_initial(content, disk_prefix, 5)
                bar_re = _profile_initial(content, bar_prefix, 5)
                re_tuple = _planned_tuple(parameters, "re", [(disk_re + bar_re) / 2.0, 0.01, 12.0, 0.01, 1])
                n_tuple = _planned_tuple(parameters, "n", [0.3, 0.1, 0.49, 0.05, 1])
                q_tuple = _planned_tuple(parameters, "q", [0.7, 0.51, 1.0, 0.01, 1])
                if not disk_re > re_tuple[0] > bar_re:
                    raise ValueError("Lens requires Re_disk > Re_lens > Re_bar")
                if n_tuple[0] >= 0.5:
                    raise ValueError("Lens requires n_lens < 0.5")
                if q_tuple[0] <= 0.5:
                    raise ValueError("Lens requires q_lens > 0.5")
                parameters = dict(parameters)
                parameters.update({"re": re_tuple, "n": n_tuple, "q": q_tuple})
            block = _multi_profile_template(new_prefix, name, component, parameters, band_count)
            content = _insert_before_model_blocks(content, block)
            if component == "companion":
                content = _append_companion_galaxy(content, new_prefix, new_prefix, str(plan.get("galaxy_name") or "companion"))
            else:
                host = str(plan.get("galaxy_label") or "a")
                content = _add_profile_to_galaxy(content, host, new_prefix)
        else:
            raise ValueError(f"no deterministic multi-band template exists for {component!r}")
    elif action_type == "PROMOTE_SINGLE_SERSIC_TO_DISK":
        target_prefix = _find_profile_prefix(content, action["target_model_label"])
        block_info = next(item for item in _profile_blocks(content) if item[0] == target_prefix)
        block = content[block_info[1] : block_info[2]]
        # GalfitS uses a Sersic profile with n fixed to one for an exponential Disk.
        block = _replace_profile_type(block, target_prefix, "sersic")
        block = _replace_lyric_tuple(block, target_prefix, 6, "FIX_VALUE", 1.0)
        marker = re.compile(rf"(?m)^(\s*P{re.escape(target_prefix)}1\)\s*[^#\n]+)(.*)$")
        match = marker.search(block)
        if match and "semantic_label: disk" not in match.group(2).lower():
            block = block[: match.start()] + f"{match.group(1)} # semantic_label: disk{match.group(2)}" + block[match.end() :]
        content = content[: block_info[1]] + block + content[block_info[2] :]
    elif action_type == "PROPOSE_REPLACE":
        target_prefix = _find_profile_prefix(content, action["target_model_label"])
        target_component = str(action["replace_to"])
        profile_type = {
            "disk": "sersic",
            "bulge": "sersic",
            "bar": "ferrer",
            "agn": "psf",
            "companion": "sersic",
            "lens": "sersic",
        }[target_component]
        block_info = next(item for item in _profile_blocks(content) if item[0] == target_prefix)
        block = content[block_info[1] : block_info[2]]
        block = _replace_profile_type(block, target_prefix, profile_type)
        name_match = re.compile(rf"(?m)^(\s*P{re.escape(target_prefix)}1\)\s*)\S+(.*)$").search(block)
        if name_match:
            block = block[: name_match.start()] + f"{name_match.group(1)}{target_component} # semantic_label: {target_component}{name_match.group(2)}" + block[name_match.end() :]
        if target_component == "disk":
            block = _replace_lyric_tuple(block, target_prefix, 6, "FIX_VALUE", 1.0)
        elif target_component == "bar":
            block = _replace_lyric_tuple(block, target_prefix, 6, "FIX_VALUE", 0.5)
        content = content[: block_info[1]] + block + content[block_info[2] :]
    elif action_type == "PROPOSE_REMOVE":
        profiles = _profile_blocks(content)
        if len(profiles) <= 1:
            raise ValueError("cannot remove the only multi-band profile")
        target_prefix = _find_profile_prefix(content, action["target_model_label"])
        target_name = _profile_name(content, target_prefix)
        block_info = next(item for item in profiles if item[0] == target_prefix)
        content = content[: block_info[1]] + content[block_info[2] :]
        galaxy_matches = list(re.finditer(r"(?m)^(\s*G([A-Za-z])2\)\s*)([^#\n]+)(.*)$", content))
        for match in reversed(galaxy_matches):
            try:
                members = ast.literal_eval(match.group(3).strip())
            except (SyntaxError, ValueError):
                continue
            if not isinstance(members, list) or target_prefix not in members:
                continue
            members = [item for item in members if item != target_prefix]
            if not members:
                raise ValueError(f"removing {target_name} would leave an empty galaxy member list")
            content = content[: match.start()] + f"{match.group(1)}{repr(members)}{match.group(4)}" + content[match.end() :]
        # A removal must not copy an old constraint file that still references the deleted profile.
        existing_constraint_files = ()
    else:
        raise ValueError(f"multi-band action config writer does not implement {action_type}")
    existing_texts = [Path(value).read_text(encoding="utf-8") for value in existing_constraint_files if Path(value).is_file()]
    merged = _merge_multi_constraints(existing_texts, constraints, removals)
    if merged is None:
        return_constraints = []
    else:
        constraint_path = Path(target_config).with_suffix(".constrain")
        if constraint_path.exists():
            raise FileExistsError(f"refusing to overwrite generated constraint file: {constraint_path}")
        constraint_path.write_text(merged, encoding="utf-8")
        return_constraints = [str(constraint_path)]
    Path(target_config).write_text(content, encoding="utf-8")
    return return_constraints


def _validate_generated_config(config_file: str | Path, workflow_mode: str) -> None:
    """Run the existing mode parser before exposing a generated config to MCP."""
    if workflow_mode == SINGLE_BAND:
        from tools.parse_feedme import parse_components, parse_feedme

        parsed = parse_feedme(str(config_file))
        if not parsed:
            raise ValueError("generated single-band feedme could not be parsed")
        if not parse_components(str(config_file)):
            raise ValueError("generated single-band feedme has no model components")
        return
    from tools.parse_lyric import parse_component_types

    component_types = parse_component_types(str(config_file))
    if not component_types:
        raise ValueError("generated multi-band lyric has no profile components")
    content = Path(config_file).read_text(encoding="utf-8")
    for prefix, start, end in _profile_blocks(content):
        block = content[start:end]
        if not re.search(rf"(?m)^\s*P{re.escape(prefix)}2\)\s+\S+", block):
            raise ValueError(f"generated profile {prefix} has no profile type")
    for match in re.finditer(r"(?m)^\s*G([A-Za-z])2\)\s*([^#\n]+)", content):
        try:
            members = ast.literal_eval(match.group(2).strip())
        except (SyntaxError, ValueError) as exc:
            members = match.group(2).strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]*", members):
                raise ValueError(f"generated galaxy G{match.group(1)}2) is not parseable") from exc
        if isinstance(members, str):
            members = [members]
        if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
            raise ValueError(f"generated galaxy G{match.group(1)}2) is not a profile list")
    for match in re.finditer(r"(?m)^\s*N([A-Za-z])1\)\s*", content):
        end_match = re.search(r"(?m)^\s*[NG][A-Za-z]1\)", content[match.end() :])
        end = match.end() + end_match.start() if end_match else len(content)
        block = content[match.start() : end]
        missing = [
            field for field in range(1, 28)
            if not re.search(rf"(?m)^\s*N{re.escape(match.group(1))}{field}\)", block)
        ]
        if missing:
            raise ValueError(f"generated nucleus {match.group(1)} is missing fields: {missing}")


def apply_action_to_config(
    decision_artifact: Mapping[str, Any],
    workflow_manifest: Mapping[str, Any],
    *,
    target_config: str | Path,
    current_model_labels: Sequence[str] = (),
    agent_recommendation: Mapping[str, Any] | None = None,
    allow_remove: bool = False,
    remove_pilot_passed: bool = False,
) -> dict[str, Any]:
    """Write one safe action config and return the existing MCP fit contract."""
    preflight = preflight_action(
        decision_artifact,
        workflow_manifest,
        current_model_labels=current_model_labels,
        allow_remove=allow_remove,
        remove_pilot_passed=remove_pilot_passed,
    )
    if not preflight.get("ok"):
        return preflight
    if agent_recommendation is not None:
        validate_agent_recommendation(
            agent_recommendation,
            decision_artifact.get("candidate_actions", []),
        )
    action = preflight["action"]
    source_config = str(workflow_manifest["config_file"])
    config_path = str(Path(target_config).expanduser().resolve())
    if action["action_type"] in {"KEEP_AND_CONTINUE", "CONVERGED"}:
        config_path = source_config
        constraint_files: list[str] = []
    else:
        if workflow_manifest["workflow_mode"] == SINGLE_BAND:
            _validate_single_action_mapping(source_config, action)
        target = Path(config_path)
        if target.exists():
            raise FileExistsError(
                f"refusing to overwrite existing action config: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=target.suffix, dir=target.parent
        )
        os.close(fd)
        temporary = Path(temporary_name)
        generated_constraints: list[str] = []
        try:
            shutil.copy2(source_config, temporary)
            if workflow_manifest["workflow_mode"] == SINGLE_BAND:
                generated_constraints = _apply_single_action(
                    source_config,
                    temporary,
                    action,
                    target.with_suffix(".cons"),
                )
            else:
                generated_constraints = _apply_multi_action(
                    source_config,
                    temporary,
                    action,
                    agent_recommendation,
                    workflow_manifest.get("constraint_files") or [],
                )
            _validate_generated_config(temporary, workflow_manifest["workflow_mode"])
            target_constraints = [
                target.with_suffix(Path(value).suffix) for value in generated_constraints
            ]
            if any(path.exists() for path in target_constraints):
                raise FileExistsError(
                    f"refusing to overwrite generated constraint file: {target_constraints}"
                )
            os.replace(temporary, target)
            constraint_files = []
            for source_constraint, target_constraint in zip(
                generated_constraints, target_constraints, strict=True
            ):
                os.replace(source_constraint, target_constraint)
                constraint_files.append(str(target_constraint))
        except Exception:
            temporary.unlink(missing_ok=True)
            for value in generated_constraints:
                Path(value).unlink(missing_ok=True)
            raise
    manifest_for_call = copy.deepcopy(dict(workflow_manifest))
    manifest_for_call["config_file"] = config_path
    manifest_for_call["constraint_files"] = constraint_files
    call = prepare_mcp_fit_call(manifest_for_call, config_file=config_path, preflight=preflight)
    preflight["config_file"] = config_path
    preflight["constraint_files"] = constraint_files
    preflight["mcp_call"] = call
    preflight["action_summary"]["config_written"] = action["action_type"] not in {"KEEP_AND_CONTINUE", "CONVERGED"}
    return preflight


def _fit_result_refs(fit_result: Mapping[str, Any]) -> tuple[bool, dict[str, str]]:
    status = str(fit_result.get("status", "")).lower()
    refs: dict[str, str] = {}
    if fit_result.get("schema_version") == "workflow-fit-artifact@v1":
        for key in (
            "config_file",
            "comparison_png",
            "parameter_file",
        ):
            value = fit_result.get(key)
            if value:
                refs[key] = str(value)
        for source_key, prefix in (
            ("result_files", "result_files"),
            ("summary_files", "summary_files"),
            ("parameter_files", "parameter_files"),
        ):
            values = fit_result.get(source_key)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if value:
                        refs[f"{prefix}[{index}]"] = str(value)
        return bool(fit_result.get("valid")), refs
    for key in (
        "input_param_file",
        "optimized_fits_file",
        "summary_file",
        "image_file",
        "comparison_png",
        "workplace",
        "log_path",
    ):
        value = fit_result.get(key)
        if value:
            refs[key] = str(value)
    for source_key, prefix in (
        ("result_fits", "result_fits"),
        ("summary_files", "summary_files"),
    ):
        values = fit_result.get(source_key)
        if isinstance(values, list):
            for index, value in enumerate(values):
                if value:
                    refs[f"{prefix}[{index}]"] = str(value)
    if status not in {"success", "succeeded", "ok", "completed"}:
        return False, refs
    if "optimized_fits_file" in fit_result:
        required = ("input_param_file", "optimized_fits_file", "summary_file", "image_file")
        return all(fit_result.get(key) for key in required), refs
    if "result_fits" in fit_result:
        required = ("result_fits", "summary_files", "comparison_png")
        return bool(fit_result.get("result_fits")) and all(fit_result.get(key) for key in required), refs
    return False, refs


def _raw_decision_only(decision: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(decision)
    nested = value.get("raw_decision")
    if isinstance(nested, Mapping):
        return copy.deepcopy(dict(nested))
    required = {"state", "action", "rule_trace"}
    if not required.issubset(value):
        raise ValueError("raw_decision must contain state, action and rule_trace")
    return copy.deepcopy(value)


def _action_summary(
    raw_decision: Mapping[str, Any],
    resolved_decision: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw_action = raw_decision.get("action")
    resolved_action = resolved_decision.get("action")
    summary = {
        "raw_action_type": (
            raw_action.get("action_type")
            if isinstance(raw_action, Mapping)
            else None
        ),
        "resolved_action_type": (
            resolved_action.get("action_type")
            if isinstance(resolved_action, Mapping)
            else None
        ),
        "candidate_action_types": [
            item.get("action", {}).get("action_type")
            for item in resolved_decision.get("candidate_actions", [])
            if isinstance(item, Mapping) and isinstance(item.get("action"), Mapping)
        ],
        "executed_action_type": None,
        "refit_verdict": None,
        "fallback": (resolved_decision.get("automation") or {}).get("resolution"),
        "needs_review": bool(
            (resolved_decision.get("automation") or {}).get("needs_review", False)
        ),
    }
    if existing:
        summary.update(dict(existing))
    action_type = (
        resolved_action.get("action_type")
        if isinstance(resolved_action, Mapping)
        else None
    )
    if summary["executed_action_type"] is None and action_type in _ACTION_TYPES:
        summary["executed_action_type"] = action_type
    evaluation = resolved_decision.get("refit_evaluation")
    if summary["refit_verdict"] is None and isinstance(evaluation, Mapping):
        values = {
            evaluation.get("fit_converged"),
            evaluation.get("residual_outcome"),
            evaluation.get("parameters_physical"),
        }
        if "no" in values or "worse" in values:
            summary["refit_verdict"] = "REJECTED"
        elif "inconclusive" in values:
            summary["refit_verdict"] = "INCONCLUSIVE"
        else:
            summary["refit_verdict"] = "ACCEPTED"
    return summary


def build_lifecycle_record(
    *,
    workflow_manifest: Mapping[str, Any],
    raw_decision: Mapping[str, Any],
    resolved_decision: Mapping[str, Any],
    policy_state: PolicyState,
    fit_result: Mapping[str, Any] | None = None,
    verifier_status: str | None = None,
    action_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the replayable workflow status and downstream handoff record."""
    validate(dict(workflow_manifest), "workflow_round_manifest")
    raw_only = _raw_decision_only(raw_decision)
    validate(dict(resolved_decision), "decision_artifact")
    fit_valid, refs = _fit_result_refs(fit_result or {})
    workflow_status = resolved_decision.get("workflow_status")
    if workflow_status == "STOPPED_NEEDS_REVIEW":
        completion = "FIT_AVAILABLE" if fit_valid else "FAILED_NEEDS_REVIEW"
        best_status = "UNLOCKED"
        next_step = (
            "complete_report_and_review"
            if fit_valid
            else "manual_recovery_required"
        )
    elif workflow_status == "CONVERGED":
        completion = "FIT_AVAILABLE" if fit_valid else "FAILED_NEEDS_REVIEW"
        best_status = "VERIFIED" if verifier_status == "PASS" else "PENDING_VERIFIER"
        next_step = (
            "lock_best_round_after_verifier"
            if fit_valid
            else "manual_recovery_required"
        )
    elif fit_valid:
        completion = "FIT_AVAILABLE"
        best_status = "PENDING_VERIFIER"
        next_step = "evaluate_refit_or_analyze_next_round"
    else:
        completion = "FAILED_NEEDS_REVIEW"
        best_status = "UNLOCKED"
        next_step = "bounded_fit_retry_or_manual_review"
    handoff = {
        "image_result_available": fit_valid,
        # A valid Image result is not enough: the lock tool flips this only
        # after verifier PASS and successful best-round lock.
        "sed_joint_eligible": False,
        "best_round_status": best_status,
        "preserve_sed_joint_logic": True,
    }
    state_snapshot = policy_state.to_dict()
    state_snapshot["verifier_status"] = (
        verifier_status or policy_state.verifier_status
    )
    state_snapshot["fit_completion_status"] = completion
    state_snapshot["best_round_status"] = best_status
    return {
        "schema_version": "1.0",
        "bridge_version": WORKFLOW_BRIDGE_VERSION,
        "lifecycle_version": WORKFLOW_LIFECYCLE_VERSION,
        "workflow_mode": workflow_manifest["workflow_mode"],
        "object_id": workflow_manifest["object_id"],
        "round_id": workflow_manifest["round_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_ref": workflow_manifest["config_file"],
        "raw_decision": raw_only,
        "resolved_decision": copy.deepcopy(dict(resolved_decision)),
        "policy_state": state_snapshot,
        "fit_result": {
            "valid": fit_valid,
            "refs": refs,
            "raw": copy.deepcopy(dict(fit_result or {})),
        },
        "fit_completion_status": completion,
        "best_round_status": best_status,
        "needs_review": workflow_status == "STOPPED_NEEDS_REVIEW"
        or completion == "FAILED_NEEDS_REVIEW",
        "next_step": next_step,
        "downstream_handoff": handoff,
        "action_summary": _action_summary(raw_only, resolved_decision, action_summary),
    }


def _clean_lifecycle_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove MCP convenience metadata before validating or persisting an artifact."""
    artifact = copy.deepcopy(dict(value))
    artifact.pop("lifecycle_file", None)
    artifact.pop("fit_valid", None)
    validate(artifact, "workflow_lifecycle")
    return artifact


def _persist_lifecycle_artifact(path: Path, value: Mapping[str, Any]) -> None:
    """Persist a schema-only lifecycle artifact at the requested path."""
    artifact = _clean_lifecycle_artifact(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def record_fit_lifecycle(
    *,
    state: PolicyState,
    workflow_manifest: Mapping[str, Any],
    raw_decision: Mapping[str, Any],
    resolved_decision: Mapping[str, Any],
    fit_result: Mapping[str, Any] | None = None,
    verifier_status: str | None = None,
    decision_ref: str | None = None,
    state_file: str | Path | None = None,
    lifecycle_file: str | Path | None = None,
    action_summary: Mapping[str, Any] | None = None,
    config_ref: str | None = None,
) -> dict[str, Any]:
    """Record decision and fit references in object state and optional files."""
    raw_only = _raw_decision_only(raw_decision)
    resolved = dict(resolved_decision)
    if "raw_decision" not in resolved and "schema_version" in resolved:
        resolved["raw_decision"] = raw_only
    record_decision_state(
        state,
        raw_decision=raw_only,
        resolved_decision=resolved,
        round_id=str(workflow_manifest["round_id"]),
        decision_ref=decision_ref,
        config_ref=config_ref or str(workflow_manifest["config_file"]),
    )
    valid, refs = _fit_result_refs(fit_result or {})
    state.current_result_refs.update(refs)
    state.verifier_status = verifier_status
    lifecycle = build_lifecycle_record(
        workflow_manifest=workflow_manifest,
        raw_decision=raw_decision,
        resolved_decision=resolved,
        policy_state=state,
        fit_result=fit_result,
        verifier_status=verifier_status,
        action_summary=action_summary,
    )
    validate(lifecycle, "workflow_lifecycle")
    state.fit_completion_status = lifecycle["fit_completion_status"]
    state.best_round_status = lifecycle["best_round_status"]
    state.needs_review = lifecycle["needs_review"]
    if state_file:
        save_policy_state(state, state_file)
    lifecycle_artifact = _clean_lifecycle_artifact(lifecycle)
    if lifecycle_file:
        target = Path(lifecycle_file).expanduser().resolve()
        _persist_lifecycle_artifact(target, lifecycle_artifact)
        response = copy.deepcopy(lifecycle_artifact)
        response["lifecycle_file"] = str(target)
        response["fit_valid"] = valid
        return response
    return lifecycle_artifact


def evaluate_workflow_refit(
    *,
    state: PolicyState,
    round_id: str,
    component: str,
    refit_evaluation: Mapping[str, Any],
    candidate_action_type: str | None = None,
    candidate_reason_code: str | None = None,
    evidence_refs: Mapping[str, Any] | None = None,
    state_file: str | Path | None = None,
    decision_ref: str | None = None,
    config_ref: str | None = None,
) -> dict[str, Any]:
    """Evaluate one completed candidate and persist its object-level state."""
    decision = evaluate_refit_with_policy(
        round_id=round_id,
        component=component,
        refit_evaluation=dict(refit_evaluation),
        state=state,
        candidate_action_type=candidate_action_type,
        candidate_reason_code=candidate_reason_code,
        evidence_refs=dict(evidence_refs or {}),
    )
    validate(decision, "decision_artifact")
    raw_decision = _raw_decision_only(decision)
    record_decision_state(
        state,
        raw_decision=raw_decision,
        resolved_decision=decision,
        round_id=round_id,
        decision_ref=decision_ref,
        config_ref=config_ref,
    )
    if state_file:
        save_policy_state(state, state_file)

    action_type = (decision.get("action") or {}).get("action_type")
    candidate_type = decision.get("refit_evaluation", {}).get("candidate_action_type")
    summary = _action_summary(raw_decision, decision, None)
    summary["executed_action_type"] = candidate_type
    summary["refit_verdict"] = {
        "ACCEPT_REFIT": "ACCEPTED",
        "REJECT_REFIT": "REJECTED",
        "INCONCLUSIVE": "INCONCLUSIVE",
    }.get(action_type)
    return {
        "decision": decision,
        "policy_state": state.to_dict(),
        "action_summary": summary,
    }


def complete_workflow_candidate(
    *,
    state: PolicyState,
    baseline_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
    baseline_fit_result: Mapping[str, Any] | None,
    candidate_fit_result: Mapping[str, Any] | None,
    component: str,
    candidate_action_type: str,
    candidate_reason_code: str | None = None,
    evidence_refs: Mapping[str, Any] | None = None,
    state_file: str | Path | None = None,
    lifecycle_file: str | Path | None = None,
    evidence_file: str | Path | None = None,
    decision_ref: str | None = None,
) -> dict[str, Any]:
    """Compare two existing MCP fit results and persist one lifecycle event."""
    from .refit_comparator import compare_refit_artifacts

    comparison = compare_refit_artifacts(
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit=baseline_fit_result,
        candidate_fit=candidate_fit_result,
        candidate_action_type=candidate_action_type,
    )
    refs = dict(evidence_refs or {})
    refs.setdefault("manifest", str(candidate_manifest["config_file"]))
    decision = evaluate_refit_with_policy(
        state=state,
        round_id=str(candidate_manifest["round_id"]),
        component=component,
        refit_evaluation=comparison["evaluation"],
        candidate_action_type=candidate_action_type,
        candidate_reason_code=candidate_reason_code,
        evidence_refs=refs,
    )
    validate(decision, "decision_artifact")
    raw_decision = _raw_decision_only(decision)
    accepted = decision.get("action", {}).get("action_type") == "ACCEPT_REFIT"
    summary = _action_summary(raw_decision, decision, None)
    summary["executed_action_type"] = candidate_action_type
    summary["refit_verdict"] = "ACCEPTED" if accepted else (
        "INCONCLUSIVE"
        if decision.get("action", {}).get("action_type") == "INCONCLUSIVE"
        else "REJECTED"
    )
    candidate_artifact = comparison["evidence"]["candidate_artifact"]
    lifecycle = record_fit_lifecycle(
        state=state,
        workflow_manifest=candidate_manifest,
        raw_decision=raw_decision,
        resolved_decision=decision,
        fit_result=candidate_artifact,
        verifier_status=None,
        decision_ref=decision_ref,
        state_file=state_file,
        lifecycle_file=lifecycle_file,
        action_summary=summary,
        config_ref=(
            str(candidate_manifest["config_file"])
            if accepted
            else str(baseline_manifest["config_file"])
        ),
    )
    lifecycle = _clean_lifecycle_artifact(lifecycle)
    if not accepted:
        baseline_artifact = comparison["evidence"]["baseline_artifact"]
        state.current_config_ref = baseline_artifact.get("config_file")
        state.current_result_refs = _fit_result_refs(baseline_artifact)[1]
        state.pending_action = None
        if state_file:
            save_policy_state(state, state_file)
        lifecycle["policy_state"] = state.to_dict()
        lifecycle = _clean_lifecycle_artifact(lifecycle)
        if lifecycle_file:
            target = Path(lifecycle_file).expanduser().resolve()
            _persist_lifecycle_artifact(target, lifecycle)
    if evidence_file:
        target = Path(evidence_file).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(comparison["evidence"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        comparison["evidence_ref"] = str(target)
    return {
        "decision": decision,
        "evaluation": comparison["evaluation"],
        "evidence": comparison["evidence"],
        "baseline_artifact": comparison["evidence"]["baseline_artifact"],
        "candidate_artifact": comparison["evidence"]["candidate_artifact"],
        "rollback": {
            "applied": not accepted,
            "config_ref": str(baseline_manifest["config_file"]) if not accepted else None,
        },
        "lifecycle": lifecycle,
        "action_summary": summary,
    }


def copy_config_for_action(source_config: str | Path, target_config: str | Path) -> str:
    """Copy a source config to a new round path without overwriting either file."""
    source = Path(_required_file(source_config, "source_config"))
    target = Path(target_config).expanduser().resolve()
    if target == source:
        raise ValueError("action config must be different from source config")
    if target.exists():
        raise FileExistsError(
            f"refusing to overwrite existing action config: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return str(target)
