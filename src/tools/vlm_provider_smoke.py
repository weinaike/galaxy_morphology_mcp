"""N27 VLM provider smoke test (no GALFIT/GalfitS, no key persistence).

Exercises the real provider end to end from a fresh process:
environment consistency (base URL/model), one controlled prompt with one
image, strict-JSON parsing, and attempt metadata. Prints a PASS/FAIL summary
without ever printing the API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comparison-png",
        required=True,
        help="path to one real comparison PNG used as the smoke image",
    )
    parser.add_argument(
        "--numeric-evidence",
        required=True,
        help="path to one real numeric evidence JSON for target IDs",
    )
    parser.add_argument("--json-out", default=None, help="write summary JSON here")
    args = parser.parse_args()

    from component_analysis.provider import OpenAICompatibleVLM
    from component_analysis.vlm import build_vlm_prompt, parse_vlm_response

    numeric = json.loads(Path(args.numeric_evidence).read_text(encoding="utf-8"))
    round_id = str(numeric["round_id"])
    prompt = build_vlm_prompt(
        round_id=round_id,
        numeric_evidence=numeric,
        target_ids=("central",),
    )
    provider = OpenAICompatibleVLM()
    summary: dict = {
        "schema_version": "workflow-vlm-provider-smoke@1",
        "base_url_host": provider.base_url.split("@")[-1].rstrip("/") if provider.base_url else "(default)",
        "model_id": provider.model_id,
        "prompt_version": "component-analysis-vlm@v2.1",
        "targets_requested": ["central"],
        "api_key_present": bool(provider.api_key),
    }
    ok = True
    try:
        raw = provider(args.comparison_png, prompt)
        summary["response_bytes"] = len(raw.encode("utf-8"))
        evidence, diagnostic = parse_vlm_response(
            raw,
            round_id=round_id,
            numeric_evidence=numeric,
            model_id=provider.model_id,
        )
        summary["parse_status"] = evidence.get("parse_status")
        observations = evidence.get("observations") or []
        summary["covered_targets"] = sorted(
            {str(item.get("target_id")) for item in observations}
        )
        summary["observations"] = observations
        summary["diagnostic"] = diagnostic
        metadata = provider.last_response_metadata
        summary["finish_reason"] = metadata.get("finish_reason")
        summary["token_usage"] = metadata.get("token_usage")
        summary["duration_s"] = metadata.get("duration_s")
        ok = (
            evidence.get("parse_status") == "OK"
            and observations
            and any(item.get("target_id") == "central" for item in observations)
        )
    except Exception as exc:  # noqa: BLE001 - smoke must report, not crash
        ok = False
        summary["error"] = f"{type(exc).__name__}: {exc}"
    summary["status"] = "PASS" if ok else "FAIL"
    if args.json_out:
        target = Path(args.json_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    safe = {key: value for key, value in summary.items() if key != "token_usage"}
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
