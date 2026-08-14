"""Frozen-SFT grouped rollout for the first Galaxy GRPO validation stage.

This module does not update model parameters.  It separates the expensive
workflow into three resumable commands:

1. ``prepare`` selects and freezes parent states;
2. ``sample`` draws N responses per parent from one fixed SFT checkpoint;
3. ``execute`` runs GALFIT, computes v11, shapes the reward and gates groups.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def preflight_grpo_runtime(work_root: str | Path) -> dict[str, Any]:
    """Fail before rollout when the evaluator runtime is incomplete."""

    required_modules = (
        "numpy",
        "matplotlib",
        "PIL",
        "scipy",
        "astropy",
        "photutils",
        "skimage",
    )
    missing_modules: list[str] = []
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError):
            missing_modules.append(module_name)
    if missing_modules:
        raise RuntimeError(
            "GRPO evaluator Python dependencies are missing: "
            + ", ".join(missing_modules)
        )

    configured_bin = os.getenv("GALFIT_BIN", "galfit")
    galfit_bin = (
        configured_bin
        if os.path.isabs(configured_bin)
        else shutil.which(configured_bin)
    )
    if not galfit_bin or not os.path.isfile(galfit_bin):
        raise RuntimeError(
            f"GALFIT executable is unavailable: GALFIT_BIN={configured_bin!r}"
        )
    if not os.access(galfit_bin, os.X_OK):
        raise RuntimeError(f"GALFIT executable is not executable: {galfit_bin}")
    try:
        probe = subprocess.run(
            [galfit_bin, "-help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"GALFIT preflight failed: {type(exc).__name__}: {exc}"
        ) from exc
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip()[-1000:]
        raise RuntimeError(
            f"GALFIT preflight returned {probe.returncode}: {detail}"
        )

    root = Path(work_root).absolute()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".preflight_", delete=True):
            pass
    except OSError as exc:
        raise RuntimeError(
            f"GRPO work directory is not writable: {root}: {exc}"
        ) from exc

    return {
        "galfit_bin": str(Path(galfit_bin).absolute()),
        "work_root": str(root),
        "python_modules": list(required_modules),
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            rows.append(value)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _prepare_output(path: str | Path, *, overwrite: bool, resume: bool = False) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not (overwrite or resume):
        raise FileExistsError(
            f"{output} already exists; pass --resume or explicitly pass --overwrite"
        )
    if output.exists() and overwrite:
        output.unlink()
    return output


def load_physical_ids(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    with Path(path).open("r", encoding="utf-8") as handle:
        obj = json.load(handle)
    if isinstance(obj, dict):
        values = obj.get("test_physical_ids") or obj.get("test_pids") or []
    elif isinstance(obj, list):
        values = obj
    else:
        raise ValueError("galaxy split file must be a list or dict")
    return {str(value) for value in values}


def checkpoint_fingerprint(adapter_path: str) -> dict[str, Any]:
    """Fingerprint the small trainable adapter, not the multi-GB base model."""

    if adapter_path.lower() == "none":
        return {"adapter_path": "none", "adapter_file": None, "sha256": None}
    root = Path(adapter_path)
    candidates = (
        [root]
        if root.is_file()
        else [root / "adapter_model.safetensors", root / "adapter_model.bin"]
    )
    adapter_file = next((path for path in candidates if path.is_file()), None)
    if adapter_file is None:
        raise FileNotFoundError(f"adapter weights not found under {adapter_path}")
    digest = hashlib.sha256()
    with adapter_file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    stat = adapter_file.stat()
    return {
        "adapter_path": str(root.resolve()),
        "adapter_file": str(adapter_file.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def _is_sft_target(node: Mapping[str, Any]) -> bool:
    from data_gen.extract_training_data import _is_mh_accepted

    return bool(
        node.get("parent_id")
        and node.get("depth", 0) > 0
        and node.get("is_accepted")
        and node.get("status") in (None, "success")
        and not _is_mh_accepted(node)
    )


def classify_parent_kind(node: Mapping[str, Any]) -> str:
    if node.get("parent_id") is None or node.get("depth", 0) == 0:
        return "root"
    if node.get("mh_accepted") or float(node.get("delta_R") or 0.0) < 0:
        return "negative"
    return "nonnegative"


def collect_parent_records(
    trajectories: Sequence[Mapping[str, Any]],
    *,
    excluded_physical_ids: set[str] | None = None,
    parent_source: str = "sft_inputs",
    require_files: bool = True,
) -> tuple[list[dict[str, Any]], Counter]:
    """Collect unique parent states without sampling them yet."""

    from data_gen.dataset_utils import _to_physical_id

    if parent_source not in {"sft_inputs", "all_states"}:
        raise ValueError("parent_source must be 'sft_inputs' or 'all_states'")
    excluded = excluded_physical_ids or set()
    records: list[dict[str, Any]] = []
    stats: Counter = Counter()
    seen: set[tuple[str, str]] = set()

    for tree in trajectories:
        galaxy_id = str(tree.get("galaxy_id", "unknown"))
        physical_id = _to_physical_id(galaxy_id)
        if physical_id in excluded:
            stats["excluded_test_trajectory"] += 1
            continue
        nodes = list(tree.get("nodes", []))
        by_id = {str(node.get("node_id")): node for node in nodes}
        if parent_source == "sft_inputs":
            candidates = [
                by_id[str(node["parent_id"])]
                for node in nodes
                if _is_sft_target(node) and str(node["parent_id"]) in by_id
            ]
        else:
            candidates = [
                node
                for node in nodes
                if node.get("status") in (None, "success")
            ]

        for parent in candidates:
            parent_id = str(parent.get("node_id", "unknown"))
            key = (galaxy_id, parent_id)
            if key in seen:
                stats["duplicate_parent"] += 1
                continue
            seen.add(key)
            feedme_path = parent.get("feedme_path")
            residual_path = parent.get("residual_path")
            summary_path = parent.get("summary_path")
            if require_files and (
                not feedme_path
                or not residual_path
                or not os.path.exists(str(feedme_path))
                or not os.path.exists(str(residual_path))
            ):
                stats["missing_parent_files"] += 1
                continue
            next_step = int(parent.get("step") or parent.get("depth") or 0) + 1
            record = {
                "group_id": f"{galaxy_id}::{parent_id}",
                "galaxy_id": galaxy_id,
                "physical_id": physical_id,
                "parent_id": parent_id,
                "parent_kind": classify_parent_kind(parent),
                "parent_depth": int(parent.get("depth") or 0),
                "next_step": next_step,
                "trajectory_file": os.path.abspath(str(tree.get("_source_file", ""))),
                "feedme_path": feedme_path,
                "residual_path": residual_path,
                "summary_path": summary_path,
                "parent_metrics": dict(parent.get("metrics") or {}),
            }
            records.append(record)
            stats[f"available_{record['parent_kind']}"] += 1
    stats["available_total"] = len(records)
    return records, stats


def select_parent_records(
    records: Sequence[Mapping[str, Any]],
    *,
    max_parents: int,
    seed: int,
    stratify_parent_kind: bool,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    limit = len(records) if max_parents <= 0 else min(max_parents, len(records))
    if not stratify_parent_kind:
        selected = [dict(row) for row in records]
        rng.shuffle(selected)
        return selected[:limit]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        buckets[str(row.get("parent_kind", "unknown"))].append(dict(row))
    for rows in buckets.values():
        rng.shuffle(rows)

    selected: list[dict[str, Any]] = []
    kinds = [kind for kind in ("root", "negative", "nonnegative") if buckets[kind]]
    kinds.extend(sorted(set(buckets) - set(kinds)))
    while len(selected) < limit and kinds:
        remaining = []
        for kind in kinds:
            if buckets[kind] and len(selected) < limit:
                selected.append(buckets[kind].pop())
            if buckets[kind]:
                remaining.append(kind)
        kinds = remaining
    return selected


def _load_parent_context(
    manifest_row: Mapping[str, Any],
    tree_cache: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = str(manifest_row.get("trajectory_file") or "")
    if not source:
        raise ValueError("manifest row has no trajectory_file")
    if source not in tree_cache:
        with open(source, "r", encoding="utf-8") as handle:
            tree_cache[source] = json.load(handle)
    tree = tree_cache[source]
    by_id = {str(node.get("node_id")): node for node in tree.get("nodes", [])}
    parent_id = str(manifest_row["parent_id"])
    if parent_id not in by_id:
        raise KeyError(f"parent {parent_id!r} not found in {source}")
    parent = by_id[parent_id]
    synthetic_child = {
        "step": int(manifest_row.get("next_step") or 1),
        "depth": int(parent.get("depth") or 0) + 1,
    }
    return tree, parent, synthetic_child


def run_inference_group(
    model: Any,
    processor: Any,
    system_content: str,
    user_text: str,
    image_path: str,
    *,
    num_return_sequences: int,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    seed: int,
) -> list[str]:
    """Sample a micro-batch of responses from one frozen multimodal prompt."""

    import torch
    from PIL import Image

    if num_return_sequences < 1:
        raise ValueError("num_return_sequences must be >= 1")
    if temperature <= 0:
        raise ValueError("temperature must be > 0 for grouped stochastic rollout")
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    clean = user_text[len("<image>\n"):] if user_text.startswith("<image>\n") else user_text
    messages = [
        {"role": "system", "content": [{"type": "text", "text": system_content}]},
        {
            "role": "user",
            "content": [
                {"type": "image", "image": f"file://{image_path}"},
                {"type": "text", "text": clean},
            ],
        },
    ]
    text_prompt = processor.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False
    )
    image = Image.open(image_path).convert("RGB")
    inputs = processor(
        text=[text_prompt],
        images=[image],
        padding=True,
        return_tensors="pt",
    ).to(model.device)
    kwargs = {
        "max_new_tokens": max_new_tokens,
        "do_sample": True,
        "temperature": temperature,
        "top_p": top_p,
        "num_return_sequences": num_return_sequences,
    }
    if top_k > 0:
        kwargs["top_k"] = top_k
    with torch.no_grad():
        output_ids = model.generate(**inputs, **kwargs)
    input_len = inputs.input_ids.shape[1]
    generated = output_ids[:, input_len:]
    return list(processor.batch_decode(generated, skip_special_tokens=True))


def _prediction_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["group_id"])].append(row)
    unique_counts = [
        len({str(row.get("prediction", "")).strip() for row in group_rows})
        for group_rows in groups.values()
    ]
    return {
        "num_groups": len(groups),
        "num_candidates": len(rows),
        "generation_errors": sum(bool(row.get("generation_error")) for row in rows),
        "parse_ok": sum(bool(row.get("parse_ok")) for row in rows),
        "parse_rate": (
            sum(bool(row.get("parse_ok")) for row in rows) / len(rows) if rows else 0.0
        ),
        "mean_unique_responses_per_group": (
            sum(unique_counts) / len(unique_counts) if unique_counts else 0.0
        ),
        "groups_with_all_unique_responses": sum(
            count == len(groups[group_id])
            for count, group_id in zip(unique_counts, groups)
        ),
        "action_type_counts": dict(Counter(str(row.get("action_type", "unknown")) for row in rows)),
    }


def _base_rollout_record(prediction: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "group_id",
        "group_index",
        "candidate_id",
        "candidate_index",
        "galaxy_id",
        "physical_id",
        "parent_id",
        "parent_kind",
        "next_step",
        "prediction",
        "action_type",
    )
    return {key: prediction.get(key) for key in keys}


def validate_model_spec_for_execution(spec: Mapping[str, Any]) -> str | None:
    """Return a policy-attributable error before the GALFIT evaluator is called.

    The online policy must emit the complete component specification consumed by
    write_feedme_from_spec. Missing/null/non-numeric component parameters are
    model-output errors, not evaluator failures.
    """

    components = spec.get("components")
    if not isinstance(components, list) or not components:
        return "components_must_be_a_nonempty_list"

    required_by_model = {
        "sersic": ("mag", "re", "n", "q", "pa"),
        "expdisk": ("mag", "re", "q", "pa"),
        "psf": ("mag",),
    }
    for index, component in enumerate(components):
        if not isinstance(component, Mapping):
            return f"component_{index}_must_be_an_object"
        model = str(component.get("model", "")).strip().lower()
        if model not in required_by_model:
            return f"component_{index}_unsupported_model:{model or '<missing>'}"

        for field in required_by_model[model]:
            value = component.get(field)
            if value is None:
                return f"component_{index}_missing_or_null:{field}"
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"component_{index}_non_numeric:{field}"
            if not math.isfinite(float(value)):
                return f"component_{index}_non_finite:{field}"

        for field in ("x", "y"):
            value = component.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"component_{index}_non_numeric:{field}"
            if not math.isfinite(float(value)):
                return f"component_{index}_non_finite:{field}"

    sky = spec.get("sky")
    if sky is not None and not isinstance(sky, Mapping):
        return "sky_must_be_an_object_or_null"
    if isinstance(sky, Mapping) and sky.get("value") is not None:
        try:
            sky_value = float(sky["value"])
        except (TypeError, ValueError):
            return "sky_value_non_numeric"
        if not math.isfinite(sky_value):
            return "sky_value_non_finite"
    return None


async def execute_prediction(
    prediction: Mapping[str, Any],
    manifest_row: Mapping[str, Any],
    *,
    work_root: str,
    max_iter: int,
    use_vlm: bool,
    vlm_model: str | None,
    api_key: str | None,
) -> dict[str, Any]:
    from eval.evaluate_action import parse_json_spec
    from eval.reward_for_grpo import (
        OUTCOME_EVALUATOR_FAILURE,
        OUTCOME_POLICY_EXECUTION_FAILURE,
        OUTCOME_POLICY_INVALID,
        OUTCOME_SUCCESS,
    )

    record = _base_rollout_record(prediction)
    record.update(
        {
            "outcome": None,
            "failure_reason": None,
            "raw_reward": None,
            "vlm_improvement": None,
        }
    )
    if prediction.get("generation_error"):
        record.update(
            outcome=OUTCOME_EVALUATOR_FAILURE,
            failure_stage="model_generation",
            failure_reason=f"generation_error: {prediction['generation_error']}",
        )
        return record

    pred_spec = prediction.get("pred_spec")
    if not isinstance(pred_spec, dict):
        pred_spec = parse_json_spec(str(prediction.get("prediction", "")))
    if not isinstance(pred_spec, dict):
        record.update(
            outcome=OUTCOME_POLICY_INVALID,
            failure_stage="response_parsing",
            failure_reason="no_valid_json_spec",
        )
        return record

    spec_error = validate_model_spec_for_execution(pred_spec)
    if spec_error is not None:
        record.update(
            outcome=OUTCOME_POLICY_INVALID,
            failure_stage="response_validation",
            failure_reason=f"invalid_model_spec: {spec_error}",
        )
        return record

    parent_feedme = manifest_row.get("feedme_path")
    if not parent_feedme or not os.path.exists(str(parent_feedme)):
        record.update(
            outcome=OUTCOME_EVALUATOR_FAILURE,
            failure_stage="evaluator_input",
            failure_reason=f"parent_feedme_missing: {parent_feedme}",
        )
        return record

    from eval.run_exec_eval import (
        execute_galfit_with_spec,
        validate_galfit_reward_artifacts,
    )

    group_index = int(prediction.get("group_index") or 0)
    candidate_index = int(prediction.get("candidate_index") or 0)
    candidate_dir = os.path.join(
        work_root, f"group_{group_index:05d}", f"candidate_{candidate_index:02d}"
    )
    node_id = f"grpo_g{group_index:05d}_c{candidate_index:02d}"
    try:
        galfit_result = await execute_galfit_with_spec(
            pred_spec,
            str(parent_feedme),
            candidate_dir,
            node_id,
            max_iter=max_iter,
        )
    except Exception as exc:
        # Exceptions raised by the executor itself (missing dependencies,
        # rendering bugs, filesystem faults, etc.) are evaluator failures and
        # must be masked. A normally returned GALFIT failure below is instead
        # attributed to the policy action and receives the coarse -1 reward.
        record.update(
            outcome=OUTCOME_EVALUATOR_FAILURE,
            failure_stage="galfit_evaluator",
            failure_reason=f"galfit_evaluator_exception: {type(exc).__name__}: {exc}",
            failure_traceback=traceback.format_exc(),
        )
        return record
    record["galfit_status"] = galfit_result.get("status")
    record["model_feedme_path"] = galfit_result.get("feedme_path")
    record["model_residual_path"] = galfit_result.get("image_file")
    record["model_output_fits_path"] = galfit_result.get("output_fits_file")
    record["model_summary_path"] = galfit_result.get("summary_file")
    record["galfit_diagnostic_file"] = galfit_result.get("galfit_diagnostic_file")
    record["galfit_console_log"] = galfit_result.get("galfit_console_log")
    record["galfit_fit_log"] = galfit_result.get("galfit_fit_log")
    record["galfit_error"] = galfit_result.get("error")
    record["galfit_failure_origin"] = galfit_result.get("failure_origin")

    if galfit_result.get("status") != "success":
        is_evaluator_failure = galfit_result.get("failure_origin") == "evaluator"
        record.update(
            outcome=(
                OUTCOME_EVALUATOR_FAILURE
                if is_evaluator_failure
                else OUTCOME_POLICY_EXECUTION_FAILURE
            ),
            failure_stage=(
                "galfit_artifact_validation"
                if is_evaluator_failure
                else "galfit_execution"
            ),
            failure_reason=str(galfit_result.get("error") or galfit_result.get("status")),
        )
        return record

    try:
        reward_version = os.environ.get("GALAXY_REWARD_VERSION", "v11").strip().lower()
        if reward_version == "v11":
            from eval.reward_for_rl import compute_rl_reward
        elif reward_version == "v12.4":
            from eval.reward_for_rl_v12_4 import compute_rl_reward
        else:
            raise ValueError(f"unsupported GALAXY_REWARD_VERSION={reward_version!r}")
        from eval.validate_reward_alignment import _parse_fitted_components

        summary_path = galfit_result.get("summary_file")
        new_metrics, artifact_errors = validate_galfit_reward_artifacts(
            summary_path, galfit_result.get("image_file")
        )
        if artifact_errors:
            raise ValueError(
                "incomplete GALFIT reward artifacts: " + "; ".join(artifact_errors)
            )
        fitted_components = _parse_fitted_components(summary_path)
        raw_result = compute_rl_reward(
            old_metrics=dict(manifest_row.get("parent_metrics") or {}),
            new_metrics=new_metrics,
            action_spec=pred_spec,
            fitted_components=fitted_components,
        )
        raw_reward = float(raw_result["reward"])
        record.update(
            outcome=OUTCOME_SUCCESS,
            failure_stage=None,
            raw_reward=raw_reward,
            reward_version=raw_result.get("reward_version", reward_version),
            model_metrics=new_metrics,
            bounds_ok=raw_result.get("bounds_ok"),
            bounds_violations=raw_result.get("bounds_violations", []),
            fitted_bounds_ok=raw_result.get("fitted_bounds_ok", True),
            fitted_violations=raw_result.get("fitted_violations", []),
            fitted_components=fitted_components,
            chi2_vetoed=raw_result.get("chi2_vetoed", False),
            r_chi2=raw_result.get("r_chi2"),
            r_bic=raw_result.get("r_bic"),
            effective_r_bic=raw_result.get("effective_r_bic"),
            bic_damping=raw_result.get("bic_damping"),
            r_noise=raw_result.get("r_noise"),
            noise_detail=raw_result.get("noise_detail"),
            structure_ok=raw_result.get("structure_ok"),
            structure_vetoed=raw_result.get("structure_vetoed", False),
            structure_violations=raw_result.get("structure_violations", []),
            structure_warnings=raw_result.get("structure_warnings", []),
            structure_detail=raw_result.get("structure_detail"),
        )
    except Exception as exc:
        record.update(
            outcome=OUTCOME_EVALUATOR_FAILURE,
            failure_stage="reward_evaluator",
            failure_reason=f"reward_error: {type(exc).__name__}: {exc}",
            failure_traceback=traceback.format_exc(),
        )
        return record

    if use_vlm:
        parent_image = manifest_row.get("residual_path")
        model_image = galfit_result.get("image_file")
        if parent_image and model_image and os.path.exists(str(parent_image)) and os.path.exists(str(model_image)):
            try:
                from eval.vlm_reward import vlm_reward_for_step

                vlm_result = await asyncio.to_thread(
                    vlm_reward_for_step,
                    parent_residual_image_path=str(parent_image),
                    parent_summary_path=manifest_row.get("summary_path"),
                    model_new_residual_image_path=str(model_image),
                    model_new_summary_path=galfit_result.get("summary_file"),
                    model_name=vlm_model,
                    api_key=api_key,
                )
                record["vlm_improvement"] = int(vlm_result.get("improvement", 0))
                record["vlm_detail"] = vlm_result
            except Exception as exc:
                record["vlm_error"] = f"{type(exc).__name__}: {exc}"
        else:
            record["vlm_error"] = "parent_or_model_image_missing"
    return record


def execute_prediction_sync(
    prediction: Mapping[str, Any],
    manifest_row: Mapping[str, Any],
    work_root: str,
    max_iter: int,
    use_vlm: bool = False,
    vlm_model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        execute_prediction(
            prediction,
            manifest_row,
            work_root=work_root,
            max_iter=max_iter,
            use_vlm=use_vlm,
            vlm_model=vlm_model,
            api_key=api_key,
        )
    )


def _augment_report(
    report: dict[str, Any],
    raw_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result = dict(report)
    result["format_rate"] = (
        sum(bool(row.get("parse_ok")) for row in prediction_rows) / len(prediction_rows)
        if prediction_rows
        else 0.0
    )
    result["action_type_counts"] = dict(
        Counter(str(row.get("action_type", "unknown")) for row in prediction_rows)
    )
    result["parent_kind_counts"] = dict(
        Counter(str(row.get("parent_kind", "unknown")) for row in raw_rows)
    )
    result["galfit_success_rate"] = (
        sum(row.get("outcome") == "success" for row in raw_rows) / len(raw_rows)
        if raw_rows
        else 0.0
    )
    labeled = [
        row for row in raw_rows if row.get("vlm_improvement") in (0, 1, False, True)
    ]
    result["vlm_labeled_candidates"] = len(labeled)
    return result


def command_prepare(args: argparse.Namespace) -> None:
    from data_gen.extract_training_data import load_trajectories

    output = _prepare_output(args.output, overwrite=args.overwrite)
    trajectories = load_trajectories(args.input_dir)
    excluded = load_physical_ids(args.exclude_galaxies)
    records, stats = collect_parent_records(
        trajectories,
        excluded_physical_ids=excluded,
        parent_source=args.parent_source,
        require_files=not args.allow_missing_files,
    )
    selected = select_parent_records(
        records,
        max_parents=args.max_parents,
        seed=args.seed,
        stratify_parent_kind=args.stratify_parent_kind,
    )
    for index, row in enumerate(selected):
        row["group_index"] = index
    write_jsonl(output, selected)
    report = {
        **dict(stats),
        "selected_total": len(selected),
        "selected_parent_kind_counts": dict(
            Counter(row["parent_kind"] for row in selected)
        ),
        "parent_source": args.parent_source,
        "seed": args.seed,
        "excluded_physical_ids": len(excluded),
    }
    report_path = output.with_suffix(".report.json")
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"parents: {output}")


def command_sample(args: argparse.Namespace) -> None:
    from eval.evaluate_action import derive_coarse_label, parse_json_spec
    from eval.run_eval import load_model_and_processor
    from eval.run_exec_eval import build_step_prompt

    output = _prepare_output(
        args.output, overwrite=args.overwrite, resume=args.resume
    )
    parents = load_jsonl(args.parents)
    existing = load_jsonl(output) if args.resume and output.exists() else []
    completed = {
        (str(row["group_id"]), int(row["candidate_index"])) for row in existing
    }
    run_config = {
        "model_path": os.path.abspath(args.model_path),
        "checkpoint": checkpoint_fingerprint(args.adapter_path),
        "num_candidates": args.num_candidates,
        "sample_batch_size": args.sample_batch_size,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_new_tokens": args.max_new_tokens,
        "max_steps": args.max_steps,
        "seed": args.seed,
        "parents_file": os.path.abspath(args.parents),
    }
    config_path = output.with_suffix(".config.json")
    if args.resume and config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            previous_config = json.load(handle)
        if previous_config != run_config:
            raise ValueError(
                f"resume config mismatch: {config_path}; use a new output or --overwrite"
            )
    else:
        with config_path.open("w", encoding="utf-8") as handle:
            json.dump(run_config, handle, ensure_ascii=False, indent=2)

    model, processor = load_model_and_processor(
        args.model_path, args.adapter_path, use_4bit=not args.no_4bit
    )
    model.eval()
    tree_cache: dict[str, dict[str, Any]] = {}
    mode = "a" if args.resume and output.exists() else "w"
    with output.open(mode, encoding="utf-8") as handle:
        for parent_pos, manifest in enumerate(parents):
            group_index = int(manifest.get("group_index", parent_pos))
            missing = [
                index
                for index in range(args.num_candidates)
                if (str(manifest["group_id"]), index) not in completed
            ]
            if not missing:
                print(f"[{parent_pos + 1}/{len(parents)}] skip complete {manifest['group_id']}")
                continue
            print(
                f"[{parent_pos + 1}/{len(parents)}] {manifest['group_id']} "
                f"kind={manifest.get('parent_kind')} missing={len(missing)}"
            )
            try:
                tree, parent, synthetic_child = _load_parent_context(
                    manifest, tree_cache
                )
                system_prompt, user_text, image_path = build_step_prompt(
                    parent, synthetic_child, tree, args.max_steps
                )
                if not image_path or not os.path.exists(str(image_path)):
                    raise FileNotFoundError(f"parent image missing: {image_path}")
            except Exception as exc:
                for candidate_index in missing:
                    row = {
                        **dict(manifest),
                        "candidate_index": candidate_index,
                        "candidate_id": f"g{group_index:05d}_c{candidate_index:02d}",
                        "prediction": "",
                        "pred_spec": None,
                        "parse_ok": False,
                        "action_type": "unknown",
                        "generation_error": f"prompt_error: {type(exc).__name__}: {exc}",
                    }
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                continue

            for start in range(0, len(missing), args.sample_batch_size):
                indices = missing[start:start + args.sample_batch_size]
                started = time.time()
                try:
                    responses = run_inference_group(
                        model,
                        processor,
                        system_prompt,
                        user_text,
                        str(image_path),
                        num_return_sequences=len(indices),
                        max_new_tokens=args.max_new_tokens,
                        temperature=args.temperature,
                        top_p=args.top_p,
                        top_k=args.top_k,
                        seed=args.seed + group_index * 1000 + start,
                    )
                    if len(responses) != len(indices):
                        raise RuntimeError(
                            f"model returned {len(responses)} sequences for {len(indices)} requests"
                        )
                    error = None
                except Exception as exc:
                    responses = [""] * len(indices)
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = time.time() - started
                for candidate_index, response in zip(indices, responses):
                    spec = parse_json_spec(response)
                    row = {
                        **dict(manifest),
                        "candidate_index": candidate_index,
                        "candidate_id": f"g{group_index:05d}_c{candidate_index:02d}",
                        "prediction": response,
                        "pred_spec": spec,
                        "parse_ok": isinstance(spec, dict),
                        "action_type": derive_coarse_label(spec),
                        "generation_error": error,
                        "generation_elapsed_batch": round(elapsed, 3),
                        "sampling": {
                            "temperature": args.temperature,
                            "top_p": args.top_p,
                            "top_k": args.top_k,
                            "max_new_tokens": args.max_new_tokens,
                            "checkpoint": args.adapter_path,
                        },
                    }
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
    all_rows = load_jsonl(output)
    report = _prediction_summary(all_rows)
    report["run_config"] = run_config
    with output.with_suffix(".report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"predictions: {output}")


async def command_execute_async(args: argparse.Namespace) -> None:
    from eval.validate_grpo_reward import build_replay_report

    preflight = preflight_grpo_runtime(args.work_dir)
    print("runtime preflight:")
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    output = _prepare_output(
        args.output, overwrite=args.overwrite, resume=args.resume
    )
    predictions = load_jsonl(args.predictions)
    parents = {str(row["group_id"]): row for row in load_jsonl(args.parents)}
    existing = load_jsonl(output) if args.resume and output.exists() else []
    completed = {
        (str(row["group_id"]), int(row["candidate_index"])) for row in existing
    }
    pending = [
        row
        for row in predictions
        if (str(row["group_id"]), int(row["candidate_index"])) not in completed
    ]
    semaphore = asyncio.Semaphore(args.galfit_concurrency)

    async def guarded(row: Mapping[str, Any]) -> dict[str, Any]:
        async with semaphore:
            group_id = str(row["group_id"])
            if group_id not in parents:
                result = _base_rollout_record(row)
                result.update(
                    outcome="evaluator_failure",
                    failure_reason="group_missing_from_parent_manifest",
                    raw_reward=None,
                    vlm_improvement=None,
                )
                return result
            use_vlm = bool(
                args.use_vlm
                and (
                    args.vlm_max_per_group <= 0
                    or int(row["candidate_index"]) < args.vlm_max_per_group
                )
            )
            return await execute_prediction(
                row,
                parents[group_id],
                work_root=args.work_dir,
                max_iter=args.max_iter,
                use_vlm=use_vlm,
                vlm_model=args.vlm_model,
                api_key=args.api_key,
            )

    mode = "a" if args.resume and output.exists() else "w"
    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in pending:
        by_group[str(row["group_id"])].append(row)
    with output.open(mode, encoding="utf-8") as handle:
        for group_pos, (group_id, rows) in enumerate(by_group.items(), 1):
            print(f"[{group_pos}/{len(by_group)}] execute {group_id} n={len(rows)}")
            results = await asyncio.gather(*(guarded(row) for row in rows))
            for result in results:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()

    raw_rows = load_jsonl(output)
    report, shaped_rows = build_replay_report(
        raw_rows,
        threshold=args.threshold,
        margin_scale=args.margin_scale,
        margin_weight=args.margin_weight,
    )
    report = _augment_report(report, raw_rows, predictions)
    report_path = output.with_suffix(".report.json")
    details_path = output.with_name(output.stem + "_shaped.jsonl")
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    write_jsonl(details_path, shaped_rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"rollouts: {output}")
    print(f"shaped details: {details_path}")


def command_execute(args: argparse.Namespace) -> None:
    asyncio.run(command_execute_async(args))


def build_parser() -> argparse.ArgumentParser:
    from eval.reward_for_grpo import (
        DEFAULT_MARGIN_SCALE,
        DEFAULT_MARGIN_WEIGHT,
        V11_THRESHOLD,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="freeze a reproducible parent manifest")
    prepare.add_argument("--input-dir", required=True)
    prepare.add_argument("--exclude-galaxies", help="test_galaxies.json to exclude")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--parent-source", choices=("sft_inputs", "all_states"), default="sft_inputs")
    prepare.add_argument("--max-parents", type=int, default=30)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--stratify-parent-kind", action="store_true")
    prepare.add_argument("--allow-missing-files", action="store_true")
    prepare.add_argument("--overwrite", action="store_true")
    prepare.set_defaults(func=command_prepare)

    sample = subparsers.add_parser("sample", help="sample responses without updating the model")
    sample.add_argument("--parents", required=True)
    sample.add_argument("--model-path", required=True)
    sample.add_argument("--adapter-path", required=True)
    sample.add_argument("--output", required=True)
    sample.add_argument("--num-candidates", type=int, default=8)
    sample.add_argument("--sample-batch-size", type=int, default=8)
    sample.add_argument("--temperature", type=float, default=0.8)
    sample.add_argument("--top-p", type=float, default=0.95)
    sample.add_argument("--top-k", type=int, default=50)
    sample.add_argument("--max-new-tokens", type=int, default=4096)
    sample.add_argument("--max-steps", type=int, default=15)
    sample.add_argument("--seed", type=int, default=42)
    sample.add_argument("--no-4bit", action="store_true")
    sample.add_argument("--resume", action="store_true")
    sample.add_argument("--overwrite", action="store_true")
    sample.set_defaults(func=command_sample)

    execute = subparsers.add_parser("execute", help="run GALFIT and build the grouped reward report")
    execute.add_argument("--parents", required=True)
    execute.add_argument("--predictions", required=True)
    execute.add_argument("--output", required=True)
    execute.add_argument("--work-dir", required=True)
    execute.add_argument("--galfit-concurrency", type=int, default=1)
    execute.add_argument("--max-iter", type=int, default=100)
    execute.add_argument("--threshold", type=float, default=V11_THRESHOLD)
    execute.add_argument("--margin-scale", type=float, default=DEFAULT_MARGIN_SCALE)
    execute.add_argument("--margin-weight", type=float, default=DEFAULT_MARGIN_WEIGHT)
    execute.add_argument("--use-vlm", action="store_true")
    execute.add_argument("--vlm-model", default="gemini-3.1-pro-preview")
    execute.add_argument("--vlm-max-per-group", type=int, default=0)
    execute.add_argument("--api-key")
    execute.add_argument("--resume", action="store_true")
    execute.add_argument("--overwrite", action="store_true")
    execute.set_defaults(func=command_execute)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if hasattr(args, "num_candidates") and args.num_candidates < 2:
        raise ValueError("--num-candidates must be >= 2 for a GRPO group")
    if hasattr(args, "sample_batch_size") and args.sample_batch_size < 1:
        raise ValueError("--sample-batch-size must be >= 1")
    if hasattr(args, "galfit_concurrency") and args.galfit_concurrency < 1:
        raise ValueError("--galfit-concurrency must be >= 1")
    args.func(args)


if __name__ == "__main__":
    main()
