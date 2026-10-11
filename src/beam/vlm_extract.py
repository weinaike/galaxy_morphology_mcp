"""Robust extraction of the survey JSON object from raw VLM markdown output."""

from __future__ import annotations

import json
import re

_FENCED_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)```", re.DOTALL)
_MARKER = "physicality_verdict"


def extract_survey_json(text: str) -> tuple[dict | None, str]:
    """Return (payload, "") on success or (None, reason).

    Strategy: (1) fenced code blocks containing the marker key, in order;
    (2) brace-balanced substrings containing the marker (for unfenced JSON
    embedded in prose). Never a whole-body regex.
    """
    if not text or not text.strip():
        return None, "empty VLM output"

    last_err: str | None = None
    for match in _FENCED_RE.finditer(text):
        payload, err = _try_load(match.group(1))
        if payload is not None:
            return payload, ""
        last_err = err
    # fallback: brace-balanced scan
    payload, err = _scan_balanced(text)
    if payload is not None:
        return payload, ""
    return None, f"no parseable JSON with '{_MARKER}' found ({last_err or 'no candidate object'})"


def _try_load(raw: str) -> tuple[dict | None, str]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"JSONDecodeError: {e.msg} (line {e.lineno})"
    if isinstance(obj, dict) and _MARKER in obj:
        return obj, ""
    return None, f"object lacks the '{_MARKER}' key"


def _scan_balanced(text: str) -> tuple[dict | None, str]:
    """Outermost brace-balanced substrings containing the marker key."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    payload, err = _try_load(text[start:i + 1])
                    if payload is not None:
                        return payload, ""
                    start = -1
    return None, "no balanced object with the marker key"
