"""Read-only source inventory for historical component-analysis archives."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from .config import json_dump, sha256_file
from .labels import (
    load_gadotti,
    load_jwst_single,
    load_multi,
    normalize_multi_object_id,
    normalize_single_object_id,
)

HASH_LIMIT_BYTES = 64 * 1024 * 1024
ROUND_RE = re.compile(r"^galfit\.\d+$")
LYRIC_RE = re.compile(r"\.lyric$")


def _role(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("analysis_report"):
        return "analysis_report"
    if name == "working_note.md":
        return "working_note"
    if name == "galfit.feedme" or name.endswith(".lyric"):
        return "configuration"
    if ROUND_RE.match(path.name):
        return "fit_parameter_round"
    if "comparison" in name or name.endswith("_original.png"):
        return "comparison"
    if path.suffix.lower() in {".fits", ".fit"}:
        return "binary_fit"
    return "other"


def _scope(path: Path) -> str:
    for part in path.parts:
        normalized = part.lower().replace("-", "_")
        if (
            normalized in {"sed", "image_sed", "imagesed"}
            or normalized.endswith("_sed")
            or "_sed_" in normalized
            or normalized.startswith("sed_")
        ):
            return "sed"
    return "image"


def _file_record(path: Path) -> dict:
    try:
        stat = path.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
    except OSError:
        return {
            "path": str(path.resolve()),
            "role": _role(path),
            "scope": _scope(path),
            "size_bytes": None,
            "mtime_ns": None,
            "sha256": None,
            "hash_status": "unreadable",
        }
    digest, hash_status = sha256_file(path, max_bytes=HASH_LIMIT_BYTES)
    return {
        "path": str(path.resolve()),
        "role": _role(path),
        "scope": _scope(path),
        "size_bytes": size,
        "mtime_ns": mtime_ns,
        "sha256": digest,
        "hash_status": hash_status,
    }


def _records(root: Path) -> list[dict]:
    records = []
    for directory, _subdirectories, filenames in os.walk(root, onerror=lambda _error: None):
        for filename in sorted(filenames):
            records.append(_file_record(Path(directory) / filename))
    return records


def _object_entry(
    object_path: Path,
    *,
    batch: str,
    mode: str,
    labels: dict[str, dict],
    object_id: str,
) -> tuple[dict, list[dict]]:
    files = _records(object_path)
    label = labels.get(object_id)
    role_counts = Counter(item["role"] for item in files)
    if mode == "single_band":
        rounds = sum(1 for item in files if item["role"] == "fit_parameter_round")
    else:
        rounds = sum(
            1
            for item in files
            if item["scope"] == "image" and LYRIC_RE.search(Path(item["path"]).name)
        )
    entry = {
        "object_id": object_id,
        "original_object_name": object_path.name,
        "batch": batch,
        "path": str(object_path.resolve()),
        "eligible": label is not None and rounds > 0,
        "label": label,
        "file_count": len(files),
        "role_counts": dict(sorted(role_counts.items())),
        "image_round_count": rounds,
        "files": files,
    }
    exclusions = []
    if label is None:
        exclusions.append(
            {
                "schema_version": "evaluation-source-exclusion@v1",
                "mode": mode,
                "object_id": object_id,
                "batch": batch,
                "path": str(object_path.resolve()),
                "reason_code": "NO_CONFIRMED_EXPERT_LABEL",
            }
        )
    if rounds == 0:
        exclusions.append(
            {
                "schema_version": "evaluation-source-exclusion@v1",
                "mode": mode,
                "object_id": object_id,
                "batch": batch,
                "path": str(object_path.resolve()),
                "reason_code": "NO_IMAGE_ROUND_CONFIGURATION",
            }
        )
    return entry, exclusions


def _iter_object_dirs(root: Path, mode: str) -> Iterable[tuple[str, Path, str]]:
    for batch_path in sorted(root.iterdir()):
        if not batch_path.is_dir() or batch_path.name.startswith("."):
            continue
        for object_path in sorted(batch_path.iterdir()):
            if not object_path.is_dir() or object_path.name in {"pngs", "sigma"}:
                continue
            if mode == "multi_band" and not re.match(r"^\d+(?:_2)?$", object_path.name):
                continue
            object_id = (
                normalize_multi_object_id(object_path.name)
                if mode == "multi_band"
                else normalize_single_object_id(object_path.name)
            )
            yield batch_path.name, object_path, object_id


def _dataset_inventory(root: Path, mode: str, labels: dict[str, dict], label_source: Path) -> tuple[dict, list[dict]]:
    entries = []
    exclusions = []
    batches = set()
    for batch, object_path, object_id in _iter_object_dirs(root, mode):
        batches.add(batch)
        entry, entry_exclusions = _object_entry(
            object_path, batch=batch, mode=mode, labels=labels, object_id=object_id
        )
        entries.append(entry)
        exclusions.extend(entry_exclusions)
    eligible = [entry for entry in entries if entry["eligible"]]
    role_counts = Counter()
    scope_counts = Counter()
    for entry in entries:
        role_counts.update(entry["role_counts"])
        scope_counts.update(item["scope"] for item in entry["files"])
    return (
        {
            "mode": mode,
            "root": str(root.resolve()),
            "label_source": str(label_source.resolve()),
            "label_count": len(labels),
            "batch_count": len(batches),
            "object_instance_count": len(entries),
            "eligible_object_instance_count": len(eligible),
            "excluded_object_instance_count": len(entries) - len(eligible),
            "image_round_count": sum(entry["image_round_count"] for entry in entries),
            "role_counts": dict(sorted(role_counts.items())),
            "scope_counts": dict(sorted(scope_counts.items())),
            "objects": entries,
        },
        exclusions,
    )


def _pick(items: list[dict], predicate, used: set[str], mode: str) -> dict | None:
    for item in items:
        key = f"{mode}:{item['object_id']}"
        if key not in used and predicate(item):
            used.add(key)
            return item
    return None


def build_pilot_proposal(datasets: list[dict]) -> dict:
    single = next(item for item in datasets if item["mode"] == "single_band")
    multi = next(item for item in datasets if item["mode"] == "multi_band")
    eligible_single = [item for item in single["objects"] if item["eligible"]]
    eligible_multi = [item for item in multi["objects"] if item["eligible"]]
    used: set[str] = set()
    selected: list[dict] = []

    def add(item: dict | None, reason: str, coverage: list[str], mode: str) -> None:
        if item is None:
            return
        selected.append(
            {
                "mode": mode,
                "object_id": item["object_id"],
                "batches": sorted({candidate["batch"] for candidate in (single["objects"] if mode == "single_band" else multi["objects"]) if candidate["object_id"] == item["object_id"] and candidate["eligible"]}),
                "selection_reason": reason,
                "coverage_tags": coverage,
                "expected_validation_boundary": [
                    "verify historical batch and alias scope",
                    "verify state cutoff and transition evidence",
                    "verify terminal consistency before adjudication",
                ],
                "confirmation_status": "PENDING_USER_CONFIRMATION",
            }
        )

    elliptical = {"obj1845", "obj216", "obj2185", "obj2758"}
    for object_id in sorted(elliptical):
        add(_pick(eligible_single, lambda x, oid=object_id: x["object_id"] == oid, used, "single_band"), "Cover all four confirmed F277W elliptical semantics", ["f277w_elliptical_single_sersic"], "single_band")
    add(_pick(eligible_single, lambda x: x["label"]["dataset"] == "jwst_f277w" and "bar" in x["label"]["source_components"], used, "single_band"), "Cover non-elliptical F277W structure", ["f277w_bar"], "single_band")
    for target, tag in (
        (("disk", "bulge", "bar"), "gadotti_disk_bulge_bar"),
        (("disk", "bar"), "gadotti_disk_bar"),
        (("disk",), "gadotti_disk_only"),
        (("bulge",), "gadotti_bulge_only"),
    ):
        add(
            _pick(
                eligible_single,
                lambda x, target=set(target): x["label"]["dataset"] == "gadotti"
                and set(x["label"]["expert_final_components"]) == target,
                used,
                "single_band",
            ),
            f"Cover exact Gadotti combination: {' + '.join(target)}",
            [tag],
            "single_band",
        )

    multi_targets = [
        ({"edge_on_disk"}, "Cover independent edge-on disk semantic", ["edge_on_disk"]),
        ({"disk", "fourier_m1", "agn"}, "Cover disk(lop) and nucleus mapping", ["disk_lop", "agn"]),
        ({"disk", "fourier_m1", "bulge", "companion"}, "Cover companion and lopsided disk", ["companion", "disk_lop", "bulge"]),
        ({"disk", "bulge", "bar"}, "Cover bar and bulge combination", ["bar", "bulge"]),
        ({"disk", "fourier_m1", "bulge"}, "Cover disk(lop) to disk plus Fourier mapping", ["disk_lop", "bulge"]),
    ]
    for target, reason, coverage in multi_targets:
        add(_pick(eligible_multi, lambda x, target=target: target == set(x["label"]["expert_final_components"]), used, "multi_band"), reason, coverage, "multi_band")
    return {
        "schema_version": "evaluation-pilot-selection-proposal@v1",
        "status": "PROPOSAL_REQUIRES_USER_CONFIRMATION",
        "target_object_count": len(selected),
        "selection_policy": "deterministic coverage-first; not random and not first-N",
        "required_review": ["confirm exact object list", "confirm batch scope", "confirm uncovered semantic cases"],
        "objects": selected,
    }


def build_coverage_matrix(datasets: list[dict]) -> dict:
    coverage = Counter()
    for dataset in datasets:
        for entry in dataset["objects"]:
            if not entry["eligible"]:
                continue
            for component in entry["label"]["expert_final_components"]:
                coverage[f"{dataset['mode']}:{component}"] += 1
    return {
        "schema_version": "evaluation-coverage-matrix@v1",
        "eligible_object_instances_by_component": dict(sorted(coverage.items())),
        "notes": ["Counts are object instances before transition deduplication.", "No action verdicts are assigned in inventory."],
    }


def build_inventory(single_root: Path, multi_root: Path, output_dir: Path, config: dict) -> dict:
    single_jwst_path = single_root / "jwst_32_gt.json"
    multi_label_path = multi_root / "expert-final-labels.json"
    single_labels = load_gadotti(single_root)
    single_labels.update(load_jwst_single(single_jwst_path))
    multi_labels = load_multi(multi_label_path)
    single_dataset, single_exclusions = _dataset_inventory(
        single_root, "single_band", single_labels, single_jwst_path
    )
    multi_dataset, multi_exclusions = _dataset_inventory(
        multi_root, "multi_band", multi_labels, multi_label_path
    )
    datasets = [single_dataset, multi_dataset]
    inventory = {
        "schema_version": "evaluation-source-inventory@v1",
        "run_id": config["run_id"],
        "design_sha256": config["design_sha256"],
        "adjudication_rule_version": config["adjudication_rule_version"],
        "hash_policy": {"max_bytes": HASH_LIMIT_BYTES, "oversized_files": "recorded without digest"},
        "datasets": datasets,
    }
    exclusions = single_exclusions + multi_exclusions
    coverage = build_coverage_matrix(datasets)
    proposal = build_pilot_proposal(datasets)
    json_dump(output_dir / "evaluation-source-inventory.json", inventory)
    json_dump(output_dir / "coverage-matrix.json", coverage)
    json_dump(output_dir / "pilot-selection-proposal.json", proposal)
    with (output_dir / "evaluation-source-exclusions.jsonl").open("w", encoding="utf-8") as handle:
        for item in exclusions:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return {"inventory": inventory, "coverage": coverage, "proposal": proposal, "exclusions": exclusions}
