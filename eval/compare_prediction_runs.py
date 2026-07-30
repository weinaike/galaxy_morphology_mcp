"""Compare two deterministic SFT prediction JSONL files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eval.evaluate_action import derive_coarse_label, parse_json_spec


def _load(path: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row.get("galaxy_id"), row.get("node_id"), row.get("index"))
            if key in rows:
                raise ValueError(f"duplicate key at {path}:{line_number}: {key}")
            rows[key] = row
    return rows


def compare(left_path: str, right_path: str) -> dict[str, Any]:
    left = _load(left_path)
    right = _load(right_path)
    common = sorted(set(left) & set(right), key=str)
    exact_text = spec_equal = action_equal = both_parse = 0
    disagreements = []
    for key in common:
        left_text = str(left[key].get("prediction", ""))
        right_text = str(right[key].get("prediction", ""))
        left_spec = parse_json_spec(left_text)
        right_spec = parse_json_spec(right_text)
        exact_text += left_text == right_text
        if isinstance(left_spec, dict) and isinstance(right_spec, dict):
            both_parse += 1
            spec_equal += left_spec == right_spec
            left_action = derive_coarse_label(left_spec)
            right_action = derive_coarse_label(right_spec)
            action_equal += left_action == right_action
            if left_spec != right_spec:
                disagreements.append(
                    {
                        "key": key,
                        "left_action": left_action,
                        "right_action": right_action,
                    }
                )
    n = len(common)
    return {
        "left": str(Path(left_path).resolve()),
        "right": str(Path(right_path).resolve()),
        "left_rows": len(left),
        "right_rows": len(right),
        "common_rows": n,
        "left_only": len(set(left) - set(right)),
        "right_only": len(set(right) - set(left)),
        "exact_text_rate": exact_text / n if n else None,
        "both_parse_rate": both_parse / n if n else None,
        "spec_equal_rate": spec_equal / both_parse if both_parse else None,
        "action_equal_rate": action_equal / both_parse if both_parse else None,
        "first_spec_disagreements": disagreements[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    report = compare(args.left, args.right)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()