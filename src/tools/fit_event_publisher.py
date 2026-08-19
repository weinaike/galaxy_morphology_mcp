"""Optional fit-round event publishing, separate from fitting tool APIs."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

import requests


# modified by zl: normalize fitting outputs into an event artifact list.
def existing_artifacts(values: Iterable[Any]) -> list[str]:
    artifacts: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidates = value if isinstance(value, (list, tuple, set)) else [value]
        for candidate in candidates:
            if not candidate:
                continue
            path = os.path.abspath(os.fspath(candidate))
            if os.path.isfile(path) and path not in seen:
                seen.add(path)
                artifacts.append(path)
    return artifacts


# modified by zl: publish optional service events without adding transport fields to tool APIs.
def publish_fit_round(event: dict[str, Any]) -> dict[str, Any]:
    """Publish through the configured adapter; become a no-op outside the service."""
    target = os.getenv("FIT_ROUND_EVENT_URL", os.getenv("FIT_ROUND_CALLBACK_URL", "")).strip()
    if not target:
        return {"attempted": False, "status": "not_configured"}
    headers = {"Content-Type": "application/json"}
    token = os.getenv("FIT_ROUND_EVENT_TOKEN", os.getenv("FIT_ROUND_CALLBACK_TOKEN", ""))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.post(target, json=event, headers=headers, timeout=10)
        return {"attempted": True, "status": "sent" if response.ok else "failed",
                "http_status": response.status_code}
    except requests.RequestException as exc:
        return {"attempted": True, "status": "failed", "error": str(exc)}
