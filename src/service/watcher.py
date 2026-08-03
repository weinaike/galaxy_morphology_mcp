import asyncio
import glob
import json
import os
from typing import Awaitable, Callable


RoundStatusCallback = Callable[[str, dict], Awaitable[None]]


def discover_round_status_files(root_dir: str) -> list[str]:
    pattern = os.path.join(os.path.abspath(root_dir), "archives", "*", "round_status.json")
    return sorted(glob.glob(pattern))


async def watch_round_status_files(
    root_dir: str,
    callback: RoundStatusCallback,
    stop_event: asyncio.Event,
    *,
    poll_interval: float = 0.5,
) -> None:
    """Emit each round status created while a task is running."""
    seen = set(discover_round_status_files(root_dir))

    async def emit_new_files() -> None:
        for status_file in discover_round_status_files(root_dir):
            if status_file in seen:
                continue
            seen.add(status_file)
            try:
                with open(status_file, encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                payload = {"stage": "round_status_read_failed", "status": "failure", "error": str(exc)}
            await callback(status_file, payload)

    while not stop_event.is_set():
        await emit_new_files()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass
    await emit_new_files()
