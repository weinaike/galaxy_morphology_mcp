"""Configuration and provenance helpers for the component evalset CLI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ADJUDICATION_RULE_VERSION = "component-evalset-v2"
DEFAULT_SINGLE_ROOT = Path("/media/data/galfit_run_history")
DEFAULT_MULTI_ROOT = Path("/media/data/galfits_run_history")
DESIGN_DOCUMENT = Path("docs/component-analysis/evaluation-set-design.md")


def sha256_file(path: Path, *, max_bytes: int | None = None) -> tuple[str | None, str]:
    """Return a file digest and status without reading oversized binaries by default."""
    size = path.stat().st_size
    if max_bytes is not None and size > max_bytes:
        return None, "skipped_size_limit"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None, "unreadable"
    return digest.hexdigest(), "hashed"


def json_dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


@dataclass(frozen=True)
class RunConfig:
    schema_version: str
    run_id: str
    mode: str
    design_document: str
    design_sha256: str
    adjudication_rule_version: str
    single_band_root: str
    multi_band_root: str
    output_root: str
    selection_manifest: str | None = None
    approval_artifact: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def build_run_config(
    mode: str,
    *,
    output_root: Path,
    single_band_root: Path = DEFAULT_SINGLE_ROOT,
    multi_band_root: Path = DEFAULT_MULTI_ROOT,
    run_id: str | None = None,
    selection_manifest: Path | None = None,
    approval_artifact: Path | None = None,
) -> RunConfig:
    design_path = DESIGN_DOCUMENT.resolve()
    digest, status = sha256_file(design_path)
    if status != "hashed" or digest is None:
        raise RuntimeError(f"unable to hash design document: {design_path}")
    generated_id = datetime.now(timezone.utc).strftime("evalset-%Y%m%dT%H%M%SZ")
    return RunConfig(
        schema_version="evaluation-run-config@v1",
        run_id=run_id or generated_id,
        mode=mode,
        design_document=str(design_path),
        design_sha256=digest,
        adjudication_rule_version=ADJUDICATION_RULE_VERSION,
        single_band_root=str(single_band_root.resolve()),
        multi_band_root=str(multi_band_root.resolve()),
        output_root=str(output_root.resolve()),
        selection_manifest=str(selection_manifest.resolve()) if selection_manifest else None,
        approval_artifact=str(approval_artifact.resolve()) if approval_artifact else None,
    )
