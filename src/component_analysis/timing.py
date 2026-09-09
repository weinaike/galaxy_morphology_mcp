"""Timing persistence for structured VLM workflow calls."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Any, Mapping


def galaxy_dir_for(ref_path: str | Path) -> Path:
    """Find the galaxy directory using the existing output-directory convention."""
    path = Path(ref_path).expanduser().resolve()
    current = path if path.is_dir() else path.parent
    for _ in range(8):
        if (current / "output").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return path.parent


def timing_log_path(ref_path: str | Path) -> Path:
    return galaxy_dir_for(ref_path) / "timing_log.md"


def timing_log_enabled() -> bool:
    return os.environ.get("VLM_TIMING_LOG", "0") == "1"


def persist_workflow_timing(
    ref_path: str | Path,
    timing: Mapping[str, Any],
) -> str | None:
    """Append one structured-workflow timing record without exposing credentials."""
    if not timing_log_enabled():
        return None
    target = timing_log_path(ref_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    attempts = list(timing.get("attempts") or [])
    lines = [
        f"## {timing.get('round_id', 'unknown-round')} | "
        f"wall={timing.get('duration_s')}s | model={timing.get('model_id') or 'unknown'}"
    ]
    for item in attempts:
        lines.append(
            f"- attempt {item.get('attempt')}: {item.get('duration_s')}s "
            f"(parse={item.get('parse_status')}, fallback={timing.get('fallback_status')})"
        )
    lines.append("")
    with target.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return str(target)
