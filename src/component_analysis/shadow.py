"""Non-invasive shadow runner for one component-analysis round."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from schemas import validate

from .artifact_adapter import extract_numeric_evidence_from_manifest
from .policy import PolicyState, decide_proposal_with_policy
from .vlm import (
    build_vlm_prompt,
    make_unavailable_vlm_evidence,
    parse_vlm_response,
)

VLMCallback = Callable[[str, str], str]


def _components_from_lyric(lyric_file: str) -> set[str]:
    components: set[str] = set()
    pattern = re.compile(r"^P[a-z]1\)\s+(\S+)")
    with open(lyric_file, encoding="utf-8") as handle:
        for line in handle:
            match = pattern.match(line.strip())
            if match:
                components.add(match.group(1).lower())
    return components


def _fingerprint(*artifacts: dict[str, Any]) -> str:
    encoded = json.dumps(artifacts, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_json(output_dir: Path, name: str, artifact: dict[str, Any]) -> str:
    path = output_dir / name
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def run_shadow_round(
    manifest: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    vlm_callback: VLMCallback | None = None,
    current_components: Iterable[str] | None = None,
    policy_state: PolicyState | None = None,
) -> dict[str, Any]:
    """Run numeric, controlled VLM and rules layers without fitting actions.

    vlm_callback receives (comparison_png, prompt) and returns raw model text.
    If omitted, the run is explicitly marked REFUSED and policy performs its
    existing numeric-only degradation path.
    """
    validate(manifest, "artifact_manifest")
    round_id = manifest["round_id"]
    output_path = Path(output_dir).expanduser().resolve() if output_dir else None
    manifest_ref = (
        str(output_path / "manifest.json")
        if output_path
        else manifest["lyric_file"]
    )
    numeric = extract_numeric_evidence_from_manifest(
        manifest,
        manifest_ref=manifest_ref,
    )
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)
        _write_json(output_path, "manifest.json", manifest)
        numeric_ref = _write_json(output_path, "numeric_evidence.json", numeric)
    else:
        manifest_ref = manifest["lyric_file"]
        numeric_ref = f"memory:{round_id}:numeric_evidence"

    prompt = build_vlm_prompt(round_id=round_id, numeric_evidence=numeric)
    prompt_ref = None
    if output_path:
        prompt_ref = output_path / "vlm_prompt.txt"
        prompt_ref.write_text(prompt, encoding="utf-8")

    vlm_error: str | None = None
    raw_response: str | None = None
    model_id = getattr(vlm_callback, "model_id", None)
    if vlm_callback is None:
        vlm = make_unavailable_vlm_evidence(
            round_id=round_id,
            status="REFUSED",
            model_id=model_id,
        )
        vlm_error = "VLM callback not configured; numeric-only shadow run"
    elif not manifest.get("comparison_png"):
        vlm = make_unavailable_vlm_evidence(round_id=round_id, status="PARSE_FAILED")
        vlm_error = "comparison_png is missing from the manifest"
    else:
        try:
            raw_response = vlm_callback(manifest["comparison_png"], prompt)
            vlm, vlm_error = parse_vlm_response(
                raw_response,
                round_id=round_id,
                numeric_evidence=numeric,
                model_id=model_id,
            )
        except TimeoutError:
            vlm = make_unavailable_vlm_evidence(
                round_id=round_id,
                status="TIMEOUT",
                model_id=model_id,
            )
            vlm_error = "VLM callback timed out"
        except PermissionError:
            vlm = make_unavailable_vlm_evidence(
                round_id=round_id,
                status="REFUSED",
                model_id=model_id,
            )
            vlm_error = "VLM callback was refused"

    if output_path:
        raw_ref = output_path / "vlm_response.raw.json"
        raw_ref.write_text(raw_response or "", encoding="utf-8")
        vlm_ref = _write_json(output_path, "vlm_evidence.json", vlm)
    else:
        vlm_ref = f"memory:{round_id}:vlm_evidence"

    components = (
        set(current_components)
        if current_components is not None
        else _components_from_lyric(manifest["lyric_file"])
    )
    decision = decide_proposal_with_policy(
        round_id=round_id,
        numeric_evidence=numeric,
        vlm_evidence=vlm,
        current_components=components,
        state=policy_state or PolicyState(),
        evidence_fingerprint=_fingerprint(numeric, vlm),
        evidence_refs={
            "numeric_evidence": numeric_ref,
            "vlm_evidence": vlm_ref,
            "manifest": manifest_ref,
            "previous_round": None,
        },
    )
    validate(decision, "decision_artifact")
    if output_path:
        decision_ref = _write_json(output_path, "decision_artifact.json", decision)
    else:
        decision_ref = f"memory:{round_id}:decision_artifact"

    return {
        "manifest": manifest,
        "numeric_evidence": numeric,
        "vlm_evidence": vlm,
        "decision_artifact": decision,
        "vlm_error": vlm_error,
        "output_dir": str(output_path) if output_path else None,
        "artifact_refs": {
            "manifest": manifest_ref,
            "numeric_evidence": numeric_ref,
            "vlm_evidence": vlm_ref,
            "decision_artifact": decision_ref,
            "prompt": str(prompt_ref) if prompt_ref else None,
        },
    }
