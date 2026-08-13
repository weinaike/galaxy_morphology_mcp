"""Frozen artifact schemas (v1.0) for the component-analysis redesign.

Four contracts defined in docs/component-analysis/redesign.md section 3:

- ``artifact_manifest``: layer-1 input, explicit artifact pointers per round.
- ``numeric_evidence``: layer-1 output, fact-type measurements only.
- ``vlm_evidence``: layer-2 output, controlled morphology labels.
- ``decision_artifact``: layer-3 output, one action per round with rule trace.

Schema files are JSON Schema draft 2020-12 and are versioned via the
``schema_version`` const inside each file. Breaking changes require a new
version const and a new file; do not mutate a frozen version in place.
"""

import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).parent

SCHEMA_NAMES = (
    "artifact_manifest",
    "numeric_evidence",
    "vlm_evidence",
    "decision_artifact",
)

_cache: dict[str, dict] = {}


def load_schema(name: str) -> dict:
    """Return the parsed JSON Schema for one of SCHEMA_NAMES."""
    if name not in SCHEMA_NAMES:
        raise ValueError(f"unknown schema {name!r}, expected one of {SCHEMA_NAMES}")
    if name not in _cache:
        path = SCHEMA_DIR / f"{name}.schema.json"
        with open(path, encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return _cache[name]


def validate(instance: dict, name: str) -> None:
    """Validate ``instance`` against the named schema.

    Raises jsonschema.ValidationError on the first violation.
    """
    jsonschema.validate(
        instance=instance,
        schema=load_schema(name),
        cls=jsonschema.Draft202012Validator,
    )


def iter_errors(instance: dict, name: str) -> list:
    """Return all validation errors (empty list means valid)."""
    validator = jsonschema.Draft202012Validator(load_schema(name))
    return sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
