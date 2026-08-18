"""Controlled VLM evidence adapter for the shadow component-analysis path.

The existing fitting workflow does not import this module. It turns numeric
candidate IDs into a constrained morphology prompt and validates the returned
JSON before layer 3 can consume it.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema

from schemas import load_schema, validate

PROMPT_VERSION = "component-analysis-vlm@v1"

_OBSERVATION_PROPERTIES = load_schema("vlm_evidence")["properties"]["observations"][
    "items"
]["properties"]
CONTROLLED_LABELS = tuple(_OBSERVATION_PROPERTIES["label"]["enum"])
QUALITY_FLAGS = tuple(_OBSERVATION_PROPERTIES["quality_flags"]["items"]["enum"])

_EXCLUSIVE_LABEL_GROUPS = (
    frozenset({"disk_like", "spheroid_like"}),
    frozenset({"independent_source", "clump", "diffraction_psf", "tidal_feature"}),
    frozenset({"bar_like", "diffraction_psf"}),
    frozenset({"peanut_x", "diffraction_psf"}),
)
_NEUTRAL_LABELS = frozenset({"none", "uncertain"})
_LOW_QUALITY_FLAGS = frozenset(
    {"low_image_quality", "saturated_display", "ambiguous_stretch"}
)
_UNAVAILABLE_STATUSES = frozenset({"PARSE_FAILED", "TIMEOUT", "REFUSED"})


def allowed_target_ids(numeric_evidence: dict[str, Any]) -> tuple[str, ...]:
    """Return the only target IDs the VLM may describe.

    'central' is always available. Every other ID must have been issued by
    the numeric layer in a 'candidate_regions' entry. Coordinates are
    deliberately not returned to the prompt builder.
    """
    validate(numeric_evidence, "numeric_evidence")
    targets = ["central"]
    seen = {"central"}
    for feature in numeric_evidence["features"]:
        for region in feature.get("candidate_regions", []):
            target_id = region["region_id"]
            if target_id == "central":
                raise ValueError("numeric candidate region_id 'central' is reserved")
            if target_id not in seen:
                targets.append(target_id)
                seen.add(target_id)
    return tuple(targets)


def build_vlm_prompt(
    *,
    round_id: str,
    numeric_evidence: dict[str, Any],
) -> str:
    """Build the versioned label-only prompt for one comparison image."""
    if numeric_evidence.get("round_id") != round_id:
        raise ValueError("numeric evidence round_id does not match requested round")

    target_ids = allowed_target_ids(numeric_evidence)
    contract = {
        "schema_version": "1.0",
        "round_id": round_id,
        "parse_status": "OK",
        "observations": [
            {
                "target_id": "central",
                "label": "uncertain",
                "confidence": 0.0,
                "evidence_regions": [],
                "quality_flags": [],
                "notes": None,
            }
        ],
    }
    return "\n".join(
        (
            "你负责 comparison PNG 的受控形态标注，不负责成分增删决策。",
            f"prompt_version: {PROMPT_VERSION}",
            f"round_id: {round_id}",
            "只能描述数值层已经给出的 target_id："
            + json.dumps(target_ids, ensure_ascii=False),
            "label 只能取以下枚举值："
            + json.dumps(CONTROLLED_LABELS, ensure_ascii=False),
            "quality_flags 只能取以下枚举值："
            + json.dumps(QUALITY_FLAGS, ensure_ascii=False),
            (
                "坐标、半径、参数值、AGN 身份、add/remove/replace 动作和"
                "新的 target_id 均禁止输出。"
            ),
            (
                "证据不足时使用 uncertain；图像质量不足时使用 uncertain "
                "并填写对应 quality_flags。"
            ),
            "同一 target 可以有多个相容标签，但 none 或 uncertain "
            "不得与其他标签并存。",
            "只输出一个 JSON 对象，不要使用 Markdown 代码块，"
            "不要在 JSON 前后添加文字。",
            "输出结构如下；observations 可以为空，notes 只能记录简短视觉歧义，"
            "不能包含动作、坐标或参数：",
            json.dumps(contract, ensure_ascii=False, indent=2),
        )
    )


def make_unavailable_vlm_evidence(
    *,
    round_id: str,
    status: str,
    model_id: str | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> dict[str, Any]:
    """Build a schema-valid empty artifact for parser, timeout or refusal failure."""
    if status not in _UNAVAILABLE_STATUSES:
        allowed = ", ".join(sorted(_UNAVAILABLE_STATUSES))
        raise ValueError(f"status must be one of: {allowed}")
    evidence: dict[str, Any] = {
        "schema_version": "1.0",
        "round_id": round_id,
        "prompt_version": prompt_version,
        "parse_status": status,
        "observations": [],
    }
    if model_id is not None:
        evidence["model_id"] = model_id
    validate(evidence, "vlm_evidence")
    return evidence


def _validation_error_message(exc: jsonschema.ValidationError) -> str:
    path = ".".join(str(part) for part in exc.absolute_path) or "<root>"
    return f"invalid VLM JSON at {path}: {exc.message}"


def _semantic_error(
    evidence: dict[str, Any],
    allowed_targets: set[str],
) -> str | None:
    labels_by_target: dict[str, set[str]] = {}
    for observation in evidence["observations"]:
        target_id = observation["target_id"]
        if target_id not in allowed_targets:
            return f"VLM target_id {target_id!r} was not issued by the numeric layer"

        label = observation["label"]
        flags = set(observation.get("quality_flags", []))
        if "label_conflict" in flags:
            return f"VLM reported a label conflict for target {target_id!r}"
        if flags & _LOW_QUALITY_FLAGS and label != "uncertain":
            return f"low-quality target {target_id!r} must use the uncertain label"
        labels_by_target.setdefault(target_id, set()).add(label)

    for target_id, labels in labels_by_target.items():
        if labels & _NEUTRAL_LABELS and len(labels) > 1:
            return f"neutral and positive labels conflict for target {target_id!r}"
        for group in _EXCLUSIVE_LABEL_GROUPS:
            if len(labels & group) > 1:
                return f"mutually exclusive labels conflict for target {target_id!r}"
    return None


def parse_vlm_response(
    raw_response: str,
    *,
    round_id: str,
    numeric_evidence: dict[str, Any],
    model_id: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Parse strict model JSON and downgrade invalid output to PARSE_FAILED.

    The returned artifact is always valid against 'vlm_evidence'. The second
    tuple item is a diagnostic string for logs; layer 3 consumes only the first.
    """
    if numeric_evidence.get("round_id") != round_id:
        raise ValueError("numeric evidence round_id does not match requested round")
    targets = set(allowed_target_ids(numeric_evidence))

    def failed(message: str) -> tuple[dict[str, Any], str]:
        return (
            make_unavailable_vlm_evidence(
                round_id=round_id,
                status="PARSE_FAILED",
                model_id=model_id,
            ),
            message,
        )

    if not isinstance(raw_response, str) or not raw_response.strip():
        return failed("empty VLM response")

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return failed(f"VLM response is not strict JSON: {exc.msg}")
    if not isinstance(parsed, dict):
        return failed("VLM response root must be a JSON object")

    evidence = dict(parsed)
    evidence.pop("model_id", None)
    evidence["prompt_version"] = PROMPT_VERSION
    if model_id is not None:
        evidence["model_id"] = model_id

    try:
        validate(evidence, "vlm_evidence")
    except jsonschema.ValidationError as exc:
        return failed(_validation_error_message(exc))

    if evidence["round_id"] != round_id:
        return failed("VLM response round_id does not match requested round")
    if evidence["parse_status"] != "OK":
        return evidence, f"VLM response status is {evidence['parse_status']}"

    semantic_error = _semantic_error(evidence, targets)
    if semantic_error is not None:
        return failed(semantic_error)
    return evidence, None
