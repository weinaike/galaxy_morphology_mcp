"""Best-effort callbacks emitted after a fitting round has completed."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

import requests


def existing_artifacts(values: Iterable[Any]) -> list[str]:
    """Return unique, absolute paths for files that currently exist."""
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


def notify_fit_round(callback_url: str | None, event: dict[str, Any]) -> dict[str, Any]:
    """POST a completed-round event without turning callback failures into fit failures."""
    target = callback_url or os.getenv("FIT_ROUND_CALLBACK_URL")
    if not target:
        return {"attempted": False, "status": "not_configured"}

    headers = {"Content-Type": "application/json"}
    token = os.getenv("FIT_ROUND_CALLBACK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(target, json=event, headers=headers, timeout=10)
        return {
            "attempted": True,
            "status": "sent" if response.ok else "failed",
            "callback_url": target,
            "http_status": response.status_code,
        }
    except requests.RequestException as exc:
        return {
            "attempted": True,
            "status": "failed",
            "callback_url": target,
            "error": str(exc),
        }
