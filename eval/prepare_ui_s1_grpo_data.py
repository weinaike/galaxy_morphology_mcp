"""Convert frozen Galaxy parent manifests into UI-S1 single-step JSONL."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

from eval.run_grpo_rollout import _load_parent_context, load_jsonl


def _parse_path_maps(values: list[str]) -> list[tuple[str, str]]:
    mappings = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --path-map {value!r}; expected OLD=NEW")
        old, new = value.split("=", 1)
        if not old or not new:
            raise ValueError(f"invalid --path-map {value!r}; expected OLD=NEW")
        mappings.append((old.rstrip("/"), new.rstrip("/")))
    return mappings


def _remap_string(value: str, mappings: list[tuple[str, str]]) -> str:
    for old, new in mappings:
        if value == old or value.startswith(old + "/"):
            return new + value[len(old) :]
    return value


def remap_paths(value: Any, mappings: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return _remap_string(value, mappings)
    if isinstance(value, list):
        return [remap_paths(item, mappings) for item in value]
    if isinstance(value, dict):
        return {key: remap_paths(item, mappings) for key, item in value.items()}
    return value


def build_ui_s1_row(
    manifest: Mapping[str, Any],
    *,
    index: int,
    tree_cache: dict[str, dict[str, Any]],
    max_steps: int,
    path_mappings: list[tuple[str, str]],
) -> dict[str, Any]:
    from eval.run_exec_eval import build_step_prompt

    tree, parent, synthetic_child = _load_parent_context(manifest, tree_cache)
    system_prompt, user_text, image_path = build_step_prompt(
        parent, synthetic_child, tree, max_steps
    )
    image = Path(str(image_path))
    if not image.is_file():
        raise FileNotFoundError(f"parent image missing: {image}")
    with Image.open(image) as handle:
        width, height = handle.size

    clean_user = (
        user_text[len("<image>\n") :]
        if user_text.startswith("<image>\n")
        else user_text
    )
    target_image = _remap_string(str(image.resolve()), path_mappings)
    target_manifest = remap_paths(copy.deepcopy(dict(manifest)), path_mappings)
    return {
        "data_source": "galaxy_grpo",
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "image": f"file://{target_image}",
                        "width": width,
                        "height": height,
                    },
                    {"type": "text", "text": clean_user},
                ],
            },
        ],
        "check_options": {},
        "extra_info": {
            "index": index,
            "group_id": str(manifest["group_id"]),
            "galaxy_parent": target_manifest,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare UI-S1 horizon=1 Galaxy GRPO JSONL"
    )
    parser.add_argument("--parents", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="rewrite A6000 paths to paths visible inside the A100 container",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"{output} already exists; pass --overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    mappings = _parse_path_maps(args.path_map)
    parents = load_jsonl(args.parents)
    tree_cache: dict[str, dict[str, Any]] = {}
    rows = [
        build_ui_s1_row(
            manifest,
            index=index,
            tree_cache=tree_cache,
            max_steps=args.max_steps,
            path_mappings=mappings,
        )
        for index, manifest in enumerate(parents)
    ]
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {
        "parents": len(rows),
        "data_source": "galaxy_grpo",
        "max_steps": args.max_steps,
        "path_maps": [{"old": old, "new": new} for old, new in mappings],
        "parent_kind_counts": {
            kind: sum(
                row["extra_info"]["galaxy_parent"].get("parent_kind") == kind
                for row in rows
            )
            for kind in ("root", "negative", "nonnegative")
        },
    }
    report_path = output.with_suffix(".report.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"UI-S1 dataset: {output}")


if __name__ == "__main__":
    main()
