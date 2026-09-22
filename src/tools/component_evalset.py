"""CLI for read-only component-analysis evaluation-set inventory, pilot, approval, and full extraction."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ and __package__.startswith("src."):
    source_root = Path(__file__).resolve().parents[1]
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from component_analysis.evalset.config import build_run_config, json_dump
from component_analysis.evalset.inventory import build_inventory
from component_analysis.evalset.pilot import build_pilot
from schemas import validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inventory", "pilot", "validate-pilot", "approve", "full", "adjudicate"))
    parser.add_argument("--single-band-root", type=Path, default=None)
    parser.add_argument("--multi-band-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/component-analysis-evalset"))
    parser.add_argument("--selection-manifest", type=Path, default=None)
    parser.add_argument("--inventory", type=Path, default=None)
    parser.add_argument("--pilot-run", type=Path, default=None, help="existing pilot run directory for validate-pilot/approve/full")
    parser.add_argument("--approved-by", default=None, help="explicit approver name recorded in the approval artifact")
    parser.add_argument("--approval-artifact", type=Path, default=None, help="schema-valid evaluation-pilot-approval@v1 JSON for full prepare")
    parser.add_argument("--stage", choices=("prepare", "freeze"), default=None, help="full mode stage")
    parser.add_argument("--full-run", type=Path, default=None, help="full run directory for adjudicate")
    parser.add_argument("--redo", action="store_true", help="adjudicate: rewrite the adjudications file instead of appending")
    args = parser.parse_args(argv)
    if args.mode == "adjudicate":
        if args.full_run is None:
            parser.error("adjudicate requires --full-run")
    if args.mode in {"pilot", "validate-pilot"} and (args.selection_manifest is None or args.inventory is None):
        parser.error(f"{args.mode} requires --selection-manifest and --inventory")
    if args.mode == "validate-pilot" and args.pilot_run is None:
        parser.error("validate-pilot requires --pilot-run")
    if args.mode == "approve" and (args.pilot_run is None or args.inventory is None or args.approved_by is None):
        parser.error("approve requires --pilot-run, --inventory, and --approved-by (explicit user authorization)")
    if args.mode == "full":
        if args.stage not in {"prepare", "freeze"}:
            parser.error("full requires --stage prepare or freeze")
        if args.stage == "prepare" and (args.approval_artifact is None or args.inventory is None or args.pilot_run is None):
            parser.error("full prepare requires --approval-artifact, --inventory, and --pilot-run")
        if args.stage == "freeze" and (args.full_run is None or args.inventory is None):
            parser.error("full freeze requires --full-run and --inventory")

    if args.mode == "full" and args.stage == "freeze":
        from component_analysis.evalset.freeze import freeze_full

        result = freeze_full(args.full_run, args.inventory)
        print(args.full_run)
        print(f"frozen pools: {result['counts']}")
        print(f"benchmark samples: {result['manifest']['sample_count']} across {result['manifest']['object_count']} objects")
        return 0

    if args.mode == "adjudicate":
        from component_analysis.evalset.adjudication_full import adjudicate_run

        result = adjudicate_run(args.full_run, redo=args.redo)
        print(args.full_run)
        print(f"adjudication records: {result['records']}")
        print(f"pools: {result['summary']['pool_totals']}")
        print(f"verdicts: {result['summary']['verdict_totals']}")
        return 0

    if args.mode == "approve":
        from component_analysis.evalset.approval import write_pilot_approval

        approval_id = f"evalset-approval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        output_path = args.output_root / f"{approval_id}.json"
        if output_path.exists():
            parser.error(f"approval artifact already exists: {output_path}")
        approval = write_pilot_approval(args.pilot_run, args.inventory, args.approved_by, output_path, approval_id=approval_id)
        print(output_path)
        print(f"pilot run promoted to PILOT_APPROVED: {approval['pilot_run_id']}")
        return 0

    if args.mode == "validate-pilot":
        selection = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        from component_analysis.evalset.validation import validate_pilot_run

        report = validate_pilot_run(args.pilot_run, selection, inventory)
        print(args.pilot_run)
        print(f"validation status: {report['status']}")
        for failure in report["failures"]:
            print(f"FAIL: {failure}")
        return 0 if report["status"] == "PASS" else 1

    config = build_run_config(
        args.mode,
        output_root=args.output_root,
        single_band_root=args.single_band_root or Path("/media/data/galfit_run_history"),
        multi_band_root=args.multi_band_root or Path("/media/data/galfits_run_history"),
        selection_manifest=args.selection_manifest,
        approval_artifact=args.approval_artifact if args.mode == "full" else None,
    )
    output_dir = Path(config.output_root) / config.run_id
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(f"output directory is non-empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    json_dump(output_dir / "run-config.json", config.as_dict())

    if args.mode == "inventory":
        json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": config.run_id, "status": "INVENTORY_RUNNING"})
        result = build_inventory(Path(config.single_band_root), Path(config.multi_band_root), output_dir, config.as_dict())
        json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": config.run_id, "status": "INVENTORY_READY", "object_counts": {dataset["mode"]: dataset["eligible_object_instance_count"] for dataset in result["inventory"]["datasets"]}, "exclusion_count": len(result["exclusions"])})
        print(output_dir)
        print(f"single_band eligible instances: {result['inventory']['datasets'][0]['eligible_object_instance_count']}")
        print(f"multi_band eligible instances: {result['inventory']['datasets'][1]['eligible_object_instance_count']}")
        print(f"pilot proposal objects: {result['proposal']['target_object_count']}")
        return 0

    if args.mode == "full":
        from component_analysis.evalset.full import prepare_full

        json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": config.run_id, "status": "FULL_PREPARING"})
        try:
            result = prepare_full(args.approval_artifact, args.pilot_run, args.inventory, output_dir, config.as_dict())
        except Exception:
            json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": config.run_id, "status": "FAILED"})
            raise
        json_dump(
            output_dir / "run-status.json",
            {
                "schema_version": "evaluation-run-status@v1",
                "run_id": config.run_id,
                "status": "FULL_REVIEW_REQUIRED",
                "object_counts": {
                    "single_band": sum(1 for row in result["selection_rows"] if row["mode"] == "single_band"),
                    "multi_band": sum(1 for row in result["selection_rows"] if row["mode"] == "multi_band"),
                },
            },
        )
        transitions = sum(1 for c in result["candidates"] if c["canonical_action"]["action_type"] != "CONVERGED")
        converged = sum(1 for c in result["candidates"] if c["canonical_action"]["action_type"] == "CONVERGED")
        print(output_dir)
        print(f"full selection objects: {len(result['selection_rows'])} (skipped: {len(result['skipped'])})")
        print(f"full candidates: {len(result['candidates'])} (transitions: {transitions}, converged: {converged})")
        print(f"aliases: {len(result['aliases'])}")
        return 0

    selection = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    selection_schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "evaluation_pilot_selection.schema.json").read_text(encoding="utf-8"))
    import jsonschema

    jsonschema.validate(selection, selection_schema, cls=jsonschema.Draft202012Validator)
    validate(inventory, "evaluation_source_inventory")
    json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": config.run_id, "status": "PILOT_RUNNING"})
    result = build_pilot(selection, inventory, output_dir, config.as_dict())
    json_dump(output_dir / "run-status.json", {"schema_version": "evaluation-run-status@v1", "run_id": config.run_id, "status": "PILOT_REVIEW_REQUIRED", "object_count": len(result["selection_rows"]), "candidate_count": len(result["candidates"])})
    print(output_dir)
    print(f"pilot objects: {len(result['selection_rows'])}")
    print(f"pilot candidates: {len(result['candidates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
