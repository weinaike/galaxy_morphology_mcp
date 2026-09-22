"""Deterministic fit-health and best-round evidence extraction (S5).

Parses only machine-verifiable facts from historical artifacts:
- multi-band ``<name>.gssummary``: reduced chi-square and BIC;
- single-band fitted ``galfit.*`` output header: Chi^2/nu;
- object-root ``analysis_report*.md`` trailing JSON line: ``{"best_turn": ...}``.

No optimizer convergence flag exists in either format, so ``fit_converged``
stays ``None``; downstream adjudication must treat it as not collected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def fit_health_from_gssummary(path: Path) -> dict[str, Any] | None:
    """Extract reduced chi-square and BIC from a multi-band gssummary."""
    text = _read_text(path)
    if not text:
        return None
    reduced = re.search(r"^#\s+reduced chisq:\s+([0-9.eE+-]+)", text, re.MULTILINE)
    bic = re.search(r"^#\s+BIC:\s+([0-9.eE+-]+)", text, re.MULTILINE)
    if reduced is None and bic is None:
        return None
    return {
        "fit_converged": None,
        "bic": float(bic.group(1)) if bic else None,
        "reduced_chisq": float(reduced.group(1)) if reduced else None,
        "residual_flags": [],
        "parameter_flags": [],
        "constraint_flags": [],
        "data_quality_flags": [],
    }


def fit_health_from_galfit_output(round_dir: Path) -> dict[str, Any] | None:
    """Extract Chi^2/nu from the last fitted galfit.* output in a single-band round dir."""
    outputs = sorted(
        (item for item in round_dir.glob("galfit.*") if item.is_file() and item.suffix.lstrip(".").isdigit()),
        key=lambda item: item.name,
    )
    for path in reversed(outputs):
        match = re.search(r"#\s+Chi\^2/nu\s+=\s+([0-9.eE+-]+)", _read_text(path))
        if match:
            return {
                "fit_converged": None,
                "bic": None,
                "reduced_chisq": float(match.group(1)),
                "residual_flags": [],
                "parameter_flags": [],
                "constraint_flags": [],
                "data_quality_flags": [],
            }
    return None


def best_turn_from_report(object_dir: Path) -> dict[str, Any] | None:
    """Parse the trailing machine-readable best-turn JSON line from analysis reports."""
    candidates = sorted(object_dir.glob("analysis_report*.md"))
    for report in reversed(candidates):
        for line in reversed(_read_text(report).splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("best_turn"):
                return payload
    return None


def match_round_id(round_ids: list[str], best_turn: str) -> str | None:
    """Map a best_turn directory name onto one of the selected round ids."""
    for round_id in round_ids:
        parts = Path(round_id).parts
        if any(part.startswith(best_turn) or best_turn.startswith(part) for part in parts):
            return round_id
    return None
