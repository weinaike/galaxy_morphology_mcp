"""MCP tools for handing a request to an external agent and awaiting its reply."""

from __future__ import annotations

import os
from typing import Annotated, Any

import requests


def _base_url() -> str:
    return os.getenv("COMMUNICATION_SERVICE_URL", "http://127.0.0.1:8010").rstrip("/")


def request_agent(
    request: Annotated[dict[str, Any], "Structured work request for an external agent"],
    request_id: Annotated[str | None, "Optional caller-selected id for idempotent retries"] = None,
) -> dict[str, Any]:
    """Submit a request for an external agent through communication_service."""
    try:
        response = requests.post(
            f"{_base_url()}/api/agent/requests",
            json={"request": request, "request_id": request_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"status": "failure", "error": f"Failed to submit agent request: {exc}"}


def wait_for_agent_response(
    request_id: Annotated[str, "request_id returned by request_agent"],
    timeout_sec: Annotated[int, "Maximum time to wait, in seconds (1-600)"] = 300,
) -> dict[str, Any]:
    """Wait for the external agent to complete a previously submitted request."""
    timeout_sec = max(1, min(timeout_sec, 600))
    try:
        response = requests.get(
            f"{_base_url()}/api/agent/requests/{request_id}/wait",
            params={"timeout_sec": timeout_sec},
            timeout=timeout_sec + 10,
        )
        if response.status_code == 404:
            return {"status": "failure", "error": f"Unknown agent request: {request_id}"}
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"status": "failure", "error": f"Failed while waiting for agent response: {exc}"}
