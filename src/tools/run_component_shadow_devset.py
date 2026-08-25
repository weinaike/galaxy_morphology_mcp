"""Run component-analysis shadow over a round-level dev set."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from component_analysis import OpenAICompatibleVLM, build_manifest, run_shadow_round
from component_analysis.shadow import _components_from_lyric


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary-file", required=True, type=Path)
    parser.add_argument("--numeric-only", action="store_true", help="skip the VLM callback and explicitly run the numeric degradation path")
    parser.add_argument("--vlm-timeout", type=float, default=360.0, help="per-request VLM timeout in seconds (default: 360)")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    return parser.parse_args()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _round_paths(input_root: Path, entry: dict[str, Any]) -> dict[str, Path]:
    round_dir = (input_root / entry["round_dir"]).expanduser().resolve()
    return {
        "round_dir": round_dir,
        "lyric": (round_dir / entry["lyric_file"]).resolve(),
        "summary": (round_dir / entry["summary_file"]).resolve(),
        "comparison": (round_dir / entry["comparison_png"]).resolve(),
    }


def _run_one(
    entry: dict[str, Any],
    *,
    input_root: Path,
    output_dir: Path,
    vlm_callback: Any | None = None,
    isophote_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = _round_paths(input_root, entry)
    current_components = sorted(_components_from_lyric(str(paths["lyric"])))
    expected_components = sorted(entry["source_components"])
    if current_components != expected_components:
        raise ValueError(
            f"normalized lyric components {current_components} do not match "
            f"dataset source_components {expected_components} for {entry['round_id']}"
        )
    manifest = build_manifest(
        round_dir=paths["round_dir"],
        lyric_file=paths["lyric"],
        summary_file=paths["summary"],
        comparison_png=paths["comparison"],
        round_id=entry["round_id"],
    )
    artifact_dir = output_dir / f"{entry['object_id']}_{entry['round_id']}"
    result = run_shadow_round(
        manifest,
        output_dir=artifact_dir,
        vlm_callback=vlm_callback,
        current_components=current_components,
        isophote_cache=isophote_cache,
    )
    decision = result["decision_artifact"]
    automation = decision.get("automation", {})
    quality_statuses = Counter(
        "passed" if band.get("passed") else "failed"
        for band in result["numeric_evidence"].get("band_quality", [])
    )
    return {
        "object_id": entry["object_id"],
        "round_id": entry["round_id"],
        "round_dir": str(paths["round_dir"]),
        "source_components": entry["source_components"],
        "normalized_current_components": current_components,
        "expert_final_components": entry["expert_final_components"],
        "relation_to_expert_final": entry["relation_to_expert_final"],
        "missing_components": entry["missing_components"],
        "extra_components": entry["extra_components"],
        "band_count": len(manifest["bands"]),
        "gssummary": entry["gssummary"],
        "numeric_feature_count": len(result["numeric_evidence"].get("features", [])),
        "band_quality": dict(quality_statuses),
        "decision_state": decision["state"],
        "action_type": decision["action"]["action_type"],
        "vlm_parse_status": result["vlm_evidence"]["parse_status"],
        "vlm_model_id": result["vlm_evidence"].get("model_id"),
        "vlm_error": result["vlm_error"],
        "rule_trace": [
            {
                "rule_id": item["rule_id"],
                "outcome": item["outcome"],
                "detail": item.get("detail"),
            }
            for item in decision["rule_trace"]
        ],
        "automation": automation,
        "artifact_dir": str(artifact_dir.resolve()),
    }


def main() -> int:
    args = _parse_args()
    dataset_path = args.dataset.expanduser().resolve()
    input_root = args.input_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    samples = dataset.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("dataset.samples must be a non-empty list")
    if not (0 <= args.shard_index < args.shard_count):
        raise ValueError("shard-index must be within shard-count")
    object_ids = sorted({str(entry["object_id"]) for entry in samples})
    shard_objects = {
        object_id
        for index, object_id in enumerate(object_ids)
        if index % args.shard_count == args.shard_index
    }
    samples = [entry for entry in samples if str(entry["object_id"]) in shard_objects]

    vlm_callback = None
    if not args.numeric_only:
        vlm_callback = OpenAICompatibleVLM(timeout=args.vlm_timeout)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    isophote_cache: dict[str, Any] = {}
    for entry in samples:
        try:
            rows.append(
                _run_one(
                    entry,
                    input_root=input_root,
                    output_dir=output_dir,
                    vlm_callback=vlm_callback,
                    isophote_cache=isophote_cache,
                )
            )
        except Exception as exc:  # keep the summary useful for batch diagnostics
            failures.append(
                {
                    "object_id": str(entry.get("object_id")),
                    "round_id": str(entry.get("round_id")),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    summary = {
        "schema_version": "1.0",
        "runner": "component-shadow-devset@v1",
        "dataset": str(dataset_path),
        "input_root": str(input_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "numeric_only": args.numeric_only,
        "vlm_callback": None if vlm_callback is None else {"provider": "OpenAICompatibleVLM", "model_id": vlm_callback.model_id},
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "shard_object_ids": sorted(shard_objects),
        "total_entries": len(samples),
        "successful_entries": len(rows),
        "failed_entries": len(failures),
        "action_counts": dict(Counter(row["action_type"] for row in rows)),
        "relation_counts": dict(Counter(row["relation_to_expert_final"] for row in rows)),
        "vlm_status_counts": dict(Counter(row["vlm_parse_status"] for row in rows)),
        "vlm_error_count": sum(bool(row["vlm_error"]) for row in rows),
        "full_vlm_successful_entries": sum(row["vlm_parse_status"] == "OK" for row in rows),
        "needs_review_count": sum(
            bool(row["automation"].get("needs_review")) for row in rows
        ),
        "failures": failures,
        "samples": rows,
    }
    _write_json(args.summary_file.expanduser().resolve(), summary)
    print(
        f"shadowed {len(rows)}/{len(samples)} rounds; "
        f"needs_review={summary['needs_review_count']}; "
        f"failures={len(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
