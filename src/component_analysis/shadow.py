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
from .policy import PolicyState, decide_proposal_with_policy
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
        isophote_cache=isophote_cache,
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

    vlm_image = manifest.get("comparison_png")
    overlay_ref: str | None = None
    if output_path and manifest.get("comparison_png"):
        overlay_ref = create_candidate_overlay(
            manifest,
            numeric,
            output_path / "candidate_overlay.png",
        )
        vlm_image = overlay_ref

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
    elif not vlm_image:
        vlm = make_unavailable_vlm_evidence(round_id=round_id, status="PARSE_FAILED")
        vlm_error = "comparison_png and candidate overlay are missing from the manifest"
    else:
        try:
            raw_response = vlm_callback(vlm_image, prompt)
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
            "candidate_overlay": overlay_ref,
        },
    }
