"""Read-only expert label adapters used by inventory."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SINGLE_MAPPING = {
    "disk": "disk",
    "bulge": "bulge",
    "bar": "bar",
    "nucleus": "agn",
    "fourier": "fourier_m1",
    "elliptical": "single_sersic",
}
MULTI_MAPPING = {
    "disk": "disk",
    "fourier_m1": "fourier_m1",
    "bulge": "bulge",
    "bar": "bar",
    "agn": "agn",
    "companion": "companion",
    "edge_on_disk": "edge_on_disk",
}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_jwst_single(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    labels: dict[str, dict] = {}
    for raw_id, source_components in raw.items():
        object_id = raw_id.split("_s1_", 1)[0]
        components = [SINGLE_MAPPING[c] for c in source_components if c in SINGLE_MAPPING]
        labels[object_id] = {
            "object_id": object_id,
            "dataset": "jwst_f277w",
            "label_status": "confirmed",
            "source_components": list(source_components),
            "expert_final_components": sorted(set(components)),
            "source_file": str(path.resolve()),
        }
    return labels


def load_gadotti(path: Path) -> dict[str, dict]:
    labels: dict[str, dict] = {}
    candidates = sorted(
        file_path
        for file_path in path.rglob("*.json")
        if file_path.name == "Gadotti_params.json"
        or file_path.name.endswith("_Gadotti_params.json")
    )
    for file_path in candidates:
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        components = []
        # Frozen reading rule (2026-09-15): an elliptical with a measured
        # mag_bulge is a single-component system -> single_sersic; the mag
        # fields only decompose non-elliptical galaxies.
        if raw.get("MType") == "elliptical" and _number(raw.get("mag_bulge")) != 0:
            components = ["single_sersic"]
        else:
            for field, component in (("mag_disk", "disk"), ("mag_bulge", "bulge"), ("mag_bar", "bar")):
                if _number(raw.get(field)) != 0:
                    components.append(component)
        if file_path.name == "Gadotti_params.json":
            object_id = file_path.parent.name
        else:
            object_id = file_path.stem.removesuffix("_Gadotti_params")
        labels[object_id] = {
            "object_id": object_id,
            "dataset": "gadotti",
            "label_status": "confirmed",
            "source_components": components,
            "expert_final_components": sorted(set(components)),
            "source_file": str(file_path.resolve()),
        }
    return labels


def load_multi(path: Path) -> dict[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    labels: dict[str, dict] = {}
    for item in raw.get("objects", []):
        if item.get("label_status") != "confirmed":
            continue
        object_id = str(item["object_id"])
        source_components = list(item.get("final_components", []))
        labels[object_id] = {
            "object_id": object_id,
            "dataset": "jwst_multiband",
            "label_status": item["label_status"],
            "source_label": item.get("source_label"),
            "source_components": source_components,
            "expert_final_components": sorted(
                {MULTI_MAPPING[c] for c in source_components if c in MULTI_MAPPING}
            ),
            "source_file": str(path.resolve()),
        }
    return labels


def normalize_single_object_id(name: str) -> str:
    match = re.match(r"^(obj\d+)", name)
    return match.group(1) if match else name


def normalize_multi_object_id(name: str) -> str:
    return re.sub(r"_2$", "", name)
