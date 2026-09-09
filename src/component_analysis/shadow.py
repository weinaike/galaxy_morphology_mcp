"""Non-invasive shadow runner for one component-analysis round."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, MutableMapping

from schemas import validate

from .artifact_adapter import extract_numeric_evidence_from_manifest
from .candidate_overlay import create_candidate_overlay
from .policy import (
    PolicyState,
    decide_proposal_with_policy,
    record_decision_state,
    save_policy_state,
)
from .vlm import (
    build_vlm_prompt,
    make_unavailable_vlm_evidence,
    parse_vlm_response,
)

VLMCallback = Callable[[str, str], str]


_COMPONENT_NAME_MAP = {
    "agn": "agn",
    "bar": "bar",
    "bulge": "bulge",
    "companion": "companion",
    "disk": "disk",
    "edge-on": "edge_on_disk",
    "edge_on": "edge_on_disk",
    "edge_on_disk": "edge_on_disk",
    "edgeondisk": "edge_on_disk",
    "lens": "lens",
    "neighbor": "companion",
    "neighbour": "companion",
    "nucleus": "agn",
}
_PROFILE_NAME_RE = re.compile(r"^P([a-z])1\)\s+(\S+)", re.IGNORECASE)
_PROFILE_TYPE_RE = re.compile(r"^P([a-z])2\)\s+(\S+)", re.IGNORECASE)
_FOURIER_MODE_RE = re.compile(r"^P([a-z])21\)\s+(\S+)", re.IGNORECASE)


def _semantic_name(raw_name: str, comments: str, profile_type: str | None) -> str | None:
    normalized = raw_name.strip().lower().replace("-", "_")
    if profile_type and profile_type.lower().replace("-", "_") in {
        "edgeondisk",
        "edge_on_disk",
    }:
        return "edge_on_disk"
    if normalized in _COMPONENT_NAME_MAP:
        return _COMPONENT_NAME_MAP[normalized]

    comment_lower = comments.lower()
    matches = [
        (comment_lower.find(token), component)
        for token, component in (
            ("edge-on disk", "edge_on_disk"),
            ("edge_on_disk", "edge_on_disk"),
            ("edgeondisk", "edge_on_disk"),
            ("nucleus", "agn"),
            ("agn", "agn"),
            ("companion", "companion"),
            ("neighbor", "companion"),
            ("neighbour", "companion"),
            ("bulge", "bulge"),
            ("bar", "bar"),
            ("disk", "disk"),
        )
        if comment_lower.find(token) >= 0
    ]
    return min(matches)[1] if matches else None


def _components_from_lyric(lyric_file: str) -> set[str]:
    """Return current semantic components from a historical GALFIT lyric.

    Older runs often called profiles obj0/obj1. Those names are normalized
    using nearby profile comments; an unresolved generic profile is rejected
    instead of being passed to the rules layer as a fake component. sersic_f
    with P?21) 1 contributes the explicit Fourier m=1 marker.
    """
    components: set[str] = set()
    profiles: list[dict[str, str | None]] = []
    lines = Path(lyric_file).read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = _PROFILE_NAME_RE.match(line.strip())
        if not match:
            continue
        prefix, raw_name = match.groups()
        profile_type = None
        fourier_mode = None
        for later_line in lines[index + 1 : index + 40]:
            type_match = _PROFILE_TYPE_RE.match(later_line.strip())
            if type_match and type_match.group(1).lower() == prefix.lower():
                profile_type = type_match.group(2).lower()
            mode_match = _FOURIER_MODE_RE.match(later_line.strip())
            if mode_match and mode_match.group(1).lower() == prefix.lower():
                fourier_mode = mode_match.group(2).strip("[] ,")
            if _PROFILE_NAME_RE.match(later_line.strip()):
                break
        comments = "\n".join(
            previous.strip()
            for previous in lines[max(0, index - 8) : index]
            if previous.strip().startswith("#")
        )
        profiles.append(
            {
                "raw_name": raw_name.lower(),
                "profile_type": profile_type,
                "fourier_mode": fourier_mode,
                "semantic": _semantic_name(raw_name, comments, profile_type),
            }
        )

    for profile in profiles:
        if profile["semantic"] is None and profile["raw_name"] == "obj0" and profile["profile_type"] in {
            "sersic",
            "sersic_f",
        }:
            profile["semantic"] = "disk"

    unresolved = [profile["raw_name"] for profile in profiles if profile["semantic"] is None]
    if unresolved:
        raise ValueError(
            f"Unable to normalize lyric profile names {unresolved} in {lyric_file}"
        )
    for profile in profiles:
        semantic = profile["semantic"]
        if semantic:
            components.add(semantic)
        if (
            semantic == "disk"
            and profile["profile_type"] == "sersic_f"
            and profile["fourier_mode"] == "1"
        ):
            components.add("fourier_m1")
    return components


def _fingerprint(*artifacts: dict[str, Any]) -> str:
    encoded = json.dumps(artifacts, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _artifact_to_workflow_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt the frozen artifact manifest to the shared workflow service."""
    bands = []
    for band in manifest["bands"]:
        bands.append(
            {
                "band": band["band"],
                "science_fits": band["science_fits"],
                "science_hdu": band["science_hdu"],
                "sigma_fits": None,
                "sigma_hdu": band["sigma_hdu"],
                "mask_fits": None,
                "mask_hdu": band["mask_hdu"],
                "psf_fits": band.get("psf_fits"),
                "psf_hdu": band.get("psf_hdu"),
                "result_fits": band["result_fits"],
                "result_hdus": {
                    "original_hdu": band["original_hdu"],
                    "model_hdu": band["model_hdu"],
                    "residual_hdu": band["residual_hdu"],
                },
                "pixscale_arcsec": band["pixscale_arcsec"],
                "fit_region": band["fit_region"],
                "validation": band.get("validation", {}),
            }
        )
    workflow = {
        "schema_version": "1.0",
        "workflow_mode": "multi-band",
        "round_id": manifest["round_id"],
        "object_id": manifest.get("galaxy_id") or manifest["round_id"],
        "config_file": manifest["lyric_file"],
        "result_files": [item["result_fits"] for item in bands],
        "summary_file": manifest["summary_file"],
        "comparison_png": manifest.get("comparison_png"),
        "working_note_file": None,
        "constraint_files": [],
        "parameter_file": None,
        "parameter_files": [],
        "bands": bands,
    }
    validate(workflow, "workflow_round_manifest")
    return workflow


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
    isophote_cache: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run numeric, controlled VLM and rules layers without fitting actions.

    vlm_callback receives (candidate_overlay_png, prompt) and returns raw model text.
    If omitted, the run is explicitly marked REFUSED and policy performs its
    existing numeric-only degradation path.
    """
    validate(manifest, "artifact_manifest")
    from .decision_service import build_workflow_proposal, resolve_workflow_proposal

    workflow_manifest = _artifact_to_workflow_manifest(manifest)
    proposal = build_workflow_proposal(
        workflow_manifest,
        output_dir=output_dir,
        vlm_callback=vlm_callback,
        current_components=current_components,
        isophote_cache=isophote_cache,
        previous_round_ref=(policy_state.last_decision_ref if policy_state else None),
    )
    state = policy_state if policy_state is not None else PolicyState(
        object_id=workflow_manifest["object_id"]
    )
    resolved = resolve_workflow_proposal(
        proposal,
        state=state,
        output_dir=output_dir,
    )
    provider = proposal["provider"]
    output_path = Path(output_dir).expanduser().resolve() if output_dir else None
    return {
        "shadow_mode": "proposal_only",
        "manifest": manifest,
        "numeric_evidence": proposal["numeric_evidence"],
        "vlm_evidence": proposal["vlm_evidence"],
        "decision_artifact": resolved["decision"],
        "policy_state": resolved["policy_state"],
        "vlm_error": provider.get("error"),
        "vlm_attempts": provider.get("attempts", []),
        "output_dir": str(output_path) if output_path else None,
        "artifact_refs": {
            "manifest": proposal["manifest_ref"],
            "numeric_evidence": resolved["decision"].get("evidence_refs", {}).get("numeric_evidence"),
            "vlm_evidence": resolved["decision"].get("evidence_refs", {}).get("vlm_evidence"),
            "decision_artifact": resolved["decision_ref"],
            "prompt": provider.get("prompt_ref"),
            "candidate_overlay": str(output_path / "candidate_overlay.png") if output_path and (output_path / "candidate_overlay.png").is_file() else None,
            "policy_state": resolved["state_ref"],
            "vlm_attempts": str(output_path / "vlm_response.attempts.json") if output_path else None,
        },
    }
