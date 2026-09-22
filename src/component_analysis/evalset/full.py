"""S8 full extraction: full selection builder and ``full prepare`` stage.

Round enumeration follows the confirmed formal-pilot generator rule
(ROADMAP 2026-09-15): rounds are grouped by timestamp directory and each
directory contributes exactly one fitted output; the live root configuration
(``galfit.feedme`` input, base ``.lyric``) is only a semantic source. Archive
copies (single band ``archives/<ts>/galfit.NN``) and GalfitS output-directory
lyrics (multi band ``output/<ts>_*/``) are preferred over root-level files to
avoid double counting a round as input plus output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .approval import validate_approval_for_full
from .config import json_dump
from .pilot import build_pilot
from schemas import validate as schema_validate

_ROUND_NUMBER_RE = re.compile(r"^galfit\.(\d+)$")
# Same timestamp pattern as leakage.py: archives are often written post-hoc in
# one batch, so file mtimes scramble the round order; the timestamp embedded in
# the round directory name is the actual chronology.
_ROUND_TS_RE = re.compile(r"(\d{8}[T_]\d{6})")


def _round_sort_key(relative: str, record: dict[str, Any]) -> tuple[str, int, str]:
    match = _ROUND_TS_RE.search(relative)
    return (match.group(1) if match else "", record.get("mtime_ns") or 0, relative)


def _relative_records(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    root = Path(entry["path"])
    records: dict[str, dict[str, Any]] = {}
    for record in entry["files"]:
        try:
            records[str(Path(record["path"]).relative_to(root))] = record
        except ValueError:
            continue
    return records


def enumerate_rounds(entry: dict[str, Any], mode: str) -> list[str]:
    """Relative round paths for one inventory entry, ordered by file mtime."""
    records = _relative_records(entry)
    if mode == "single_band":
        groups: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
        for relative, record in records.items():
            match = _ROUND_NUMBER_RE.match(Path(relative).name)
            if record.get("role") != "fit_parameter_round" or match is None:
                continue
            groups.setdefault(str(Path(relative).parent), []).append((int(match.group(1)), relative, record))
        archive_groups = {parent: items for parent, items in groups.items() if "archives" in Path(parent).parts}
        if archive_groups:
            picks = [max(items, key=lambda item: item[0]) for items in archive_groups.values()]
        else:
            # No archived rounds: each root-level fitted output is one round.
            picks = [item for items in groups.values() for item in items]
        ordered = sorted(picks, key=lambda item: _round_sort_key(item[1], item[2]))
        return [relative for _, relative, _ in ordered]

    lyric_groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for relative, record in records.items():
        name = Path(relative).name
        if record.get("scope") != "image" or not name.endswith(".lyric") or name.endswith("_for_image_sed_fitting.lyric"):
            continue
        lyric_groups.setdefault(str(Path(relative).parent), []).append((relative, record))
    output_groups = {parent: items for parent, items in lyric_groups.items() if Path(parent).parts[:1] == ("output",)}
    picks: list[tuple[str, dict[str, Any]]] = []
    if output_groups:
        for parent, items in output_groups.items():
            dir_name = Path(parent).name
            relative = next((rel for rel, _ in items if Path(rel).stem == dir_name), sorted(rel for rel, _ in items)[0])
            picks.append((relative, dict(records[relative])))
    else:
        # No output directories: root-level lyrics are the round configs.
        picks = list(lyric_groups.get(".", []))
    ordered = sorted(picks, key=lambda item: _round_sort_key(item[0], item[1]))
    return [relative for relative, _ in ordered]


def build_full_selection(inventory: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Selection covering every eligible object x batch instance, plus a skip list."""
    objects: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_keys: set[tuple[str, str, str]] = set()
    for dataset in inventory["datasets"]:
        mode = dataset["mode"]
        for entry in dataset["objects"]:
            if not entry["eligible"]:
                continue
            batch = entry["batch"]
            key = (mode, entry["object_id"], batch)
            disambiguated = key in used_keys
            if disambiguated:
                # Mirror build_pilot's instance lookup: same-batch `_2` variants
                # ride under "<batch>:<original_object_name>" so both directory
                # trees produce candidates with unique sample ids.
                batch = f"{entry['batch']}:{entry['original_object_name']}"
            used_keys.add((mode, entry["object_id"], batch))
            rounds = enumerate_rounds(entry, mode)
            if not rounds:
                skipped.append({
                    "mode": mode,
                    "object_id": entry["object_id"],
                    "batch": batch,
                    "reason_code": "NO_RECOVERABLE_IMAGE_ROUND",
                })
                continue
            objects.append({
                "mode": mode,
                "object_id": entry["object_id"],
                "batch": batch,
                "rounds": rounds,
                "selection_reason": "S8 full extraction: every eligible object x batch instance",
                "coverage_tags": ["full_extraction"] + (["instance_disambiguation"] if disambiguated else []),
            })
    selection = {
        "schema_version": "evaluation-pilot-selection@v1",
        "status": "USER_AUTHORIZED_FULL",
        "inventory_run_id": inventory["run_id"],
        "design_sha256": inventory["design_sha256"],
        "adjudication_rule_version": inventory["adjudication_rule_version"],
        "selection_policy": "S8 full extraction over every eligible object x batch instance; one fitted-output round per timestamp directory, archive/output copies preferred over live root configurations",
        "objects": objects,
    }
    schema_validate(selection, "evaluation_pilot_selection")
    return selection, skipped


def prepare_full(
    approval_path: Path,
    pilot_run_dir: Path,
    inventory_path: Path,
    output_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    schema_validate(inventory, "evaluation_source_inventory")
    validate_approval_for_full(approval_path, pilot_run_dir, inventory_path, inventory)
    selection, skipped = build_full_selection(inventory)
    result = build_pilot(selection, inventory, output_dir, config, run_kind="full", min_rounds=1, require_inputs=True)
    json_dump(output_dir / "full-skip-list.json", {"schema_version": "evaluation-full-skip-list@v1", "skipped": skipped})
    return {**result, "skipped": skipped}
