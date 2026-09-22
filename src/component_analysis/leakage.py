"""Independent leakage checks over pilot state manifests (design doc §13).

Six checks; check 6 (manual file sampling) is recorded as an open requirement
in the report instead of being faked programmatically.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ALLOWED_ROLES = {"configuration", "fit_parameter_round", "binary_fit", "comparison", "constraint_file", "log", "notes", "other"}
_EXCLUDED_NAME_MARKERS = ("working_note", "analysis_report", "final_report", "component_analysis", "best_round", "best_turn")
_JSON_FIELD_BLACKLIST = ("best_turn", "historical_best_round_id", "action_verdict", "expert_final_components", "next_action", "resolved_decision")

_CHECK_PATH_ROLES = "path roles are within the state whitelist"
_CHECK_ROUND_CUTOFF = "source refs are confined to the state round directory"
_CHECK_JSON_FIELDS = "manifest JSON carries no label-side or future-action fields"
_CHECK_MARKDOWN_SOURCES = "no Working Note / final report / component-analysis sources enter the state"
_CHECK_REF_CUTOFF = "all referenced rounds are the current round or earlier"
_CHECK_MANUAL_SAMPLING = "manual sampling of state files (required before adjudication)"


def check_manifests(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    checks_run: list[str] = []

    for manifest in manifests:
        sample_id = manifest["sample_id"]
        state_dir = str(Path(manifest["state_round_id"]).parent)
        refs = manifest.get("source_refs", [])
        for ref in refs:
            path = str(ref.get("path", ""))
            name = path.rsplit("/", 1)[-1].lower()
            role = ref.get("role", "")
            if role not in _ALLOWED_ROLES:
                violations.append({"sample_id": sample_id, "check": _CHECK_PATH_ROLES, "path": path, "detail": f"role {role!r} not whitelisted"})
            if any(marker in name for marker in _EXCLUDED_NAME_MARKERS):
                violations.append({"sample_id": sample_id, "check": _CHECK_MARKDOWN_SOURCES, "path": path, "detail": "excluded artifact name"})
            if state_dir and state_dir not in path:
                violations.append({"sample_id": sample_id, "check": _CHECK_ROUND_CUTOFF, "path": path, "detail": "reference escapes the state round directory"})
        serialized = json.dumps(manifest, ensure_ascii=False)
        for field in _JSON_FIELD_BLACKLIST:
            if re.search(rf'"{field}"\s*:', serialized):
                violations.append({"sample_id": sample_id, "check": _CHECK_JSON_FIELDS, "path": manifest["state_round_id"], "detail": f"blacklisted field {field!r}"})
        for entry in manifest.get("history_context", []):
            if entry.get("round_id") and entry["round_id"] != manifest["state_round_id"]:
                violations.append({"sample_id": sample_id, "check": _CHECK_REF_CUTOFF, "path": str(entry.get("round_id")), "detail": "history context references a non-current round"})

    checks_run = [_CHECK_PATH_ROLES, _CHECK_ROUND_CUTOFF, _CHECK_JSON_FIELDS, _CHECK_MARKDOWN_SOURCES, _CHECK_REF_CUTOFF]
    status = "PASS" if not violations else "FAIL"
    return {
        "schema_version": "evaluation-leakage-report@v1",
        "status": status,
        "checks": checks_run,
        "violations": violations,
        "pending_manual_checks": [_CHECK_MANUAL_SAMPLING],
    }
