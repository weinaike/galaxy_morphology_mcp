"""Frozen artifact schemas (v1.0) for the component-analysis redesign.

Four contracts defined in docs/component-analysis/redesign.md section 3:

- ``artifact_manifest``: layer-1 input, explicit artifact pointers per round.
- ``numeric_evidence``: layer-1 output, fact-type measurements only.
- ``vlm_evidence``: layer-2 output, controlled morphology labels.
- ``decision_artifact``: layer-3 output, one action per round with rule trace.
- ``workflow_round_manifest``: explicit single/multi-band workflow inputs.
- ``workflow_lifecycle``: replayable state and MCP handoff for one round.
- ``workflow_proposal``: replayable evidence and raw candidate proposal.

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
    "workflow_round_manifest",
    "workflow_lifecycle",
    "agent_recommendation",
    "workflow_proposal",
    "workflow_batch_manifest",
    "workflow_fit_artifact",
    "verifier_assessment",
    "workflow_verifier",
)

_cache: dict[str, dict] = {}

_SCHEMA_VERSIONS = {
    "artifact_manifest": ("1.0",),
    "numeric_evidence": ("1.0",),
    "vlm_evidence": ("1.0",),
    "decision_artifact": ("1.0", "1.1"),
    "workflow_round_manifest": ("1.0",),
    "workflow_lifecycle": ("1.0",),
    "agent_recommendation": ("1.0",),
    "workflow_proposal": ("1.0",),
    "workflow_batch_manifest": ("workflow-batch-manifest@v1",),
    "workflow_fit_artifact": ("workflow-fit-artifact@v1",),
    "verifier_assessment": ("verifier-assessment@v1",),
    "workflow_verifier": ("workflow-verifier@v1",),
}


def _schema_key(name: str, schema_version: str) -> str:
    return f"{name}@{schema_version}"


def _schema_path(name: str, schema_version: str) -> Path:
    if name == "decision_artifact" and schema_version == "1.1":
        return SCHEMA_DIR / "decision_artifact_v1_1.schema.json"
    if name == "agent_recommendation" and schema_version == "1.0":
        return SCHEMA_DIR / "agent_recommendation.schema.json"
    return SCHEMA_DIR / f"{name}.schema.json"


def load_schema(name: str, schema_version: str | None = None) -> dict:
    """Return a parsed schema, preserving the frozen v1.0 default."""
    if name not in SCHEMA_NAMES:
        raise ValueError(f"unknown schema {name!r}, expected one of {SCHEMA_NAMES}")
    versions = _SCHEMA_VERSIONS[name]
    version = schema_version or versions[0]
    if version not in versions:
        raise ValueError(
            f"unsupported {name} schema version {version!r}; expected one of {versions}"
        )
    key = _schema_key(name, version)
    if key not in _cache:
        path = _schema_path(name, version)
        with open(path, encoding="utf-8") as f:
            _cache[key] = json.load(f)
    return _cache[key]


def validate(instance: dict, name: str) -> None:
    """Validate ``instance`` against the named schema.

    Raises jsonschema.ValidationError on the first violation.
    """
    schema_version = instance.get("schema_version") if name in {"decision_artifact", "workflow_round_manifest", "workflow_lifecycle"} else None
    jsonschema.validate(
        instance=instance,
        schema=load_schema(name, schema_version=schema_version),
        cls=jsonschema.Draft202012Validator,
    )


def iter_errors(instance: dict, name: str) -> list:
    """Return all validation errors (empty list means valid)."""
    schema_version = instance.get("schema_version") if name in {"decision_artifact", "workflow_round_manifest", "workflow_lifecycle"} else None
    validator = jsonschema.Draft202012Validator(
        load_schema(name, schema_version=schema_version)
    )
    return sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
