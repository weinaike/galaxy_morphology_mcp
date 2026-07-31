"""Sanitize experiment artifacts before they are shown to a training model."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping


_CONTROL_PATH_PLACEHOLDERS = {
    "A": "<INPUT_IMAGE>",
    "B": "<OUTPUT_FITS>",
    "C": "<SIGMA_IMAGE>",
    "D": "<PSF_IMAGE>",
    "F": "<MASK_IMAGE>",
    "G": "<CONSTRAINT_FILE>",
}

_FIT_LOG_PATH_PLACEHOLDERS = {
    "Input image": "<INPUT_IMAGE>",
    "Init. par. file": "<CURRENT_FEEDME>",
    "Restart file": "<RESTART_FILE>",
    "Output image": "<OUTPUT_FITS>",
}

_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w.])/(?:[^/\s`]+/)*[^/\s`]+"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:\\)(?:[^\\\r\n`]+\\)*[^\\\r\n`]+"
)


def sanitize_summary_for_model(summary_content: str) -> str:
    """Remove machine-specific paths while preserving GALFIT task evidence."""

    if not isinstance(summary_content, str) or not summary_content:
        return summary_content

    sanitized_lines: list[str] = []
    for line in summary_content.splitlines():
        output_match = re.match(
            r"^(\s*\*\*Output File:\*\*\s*)`[^`\r\n]*`(\s*)$", line
        )
        if output_match:
            sanitized_lines.append(
                f"{output_match.group(1)}`<OUTPUT_FITS>`{output_match.group(2)}"
            )
            continue

        control_match = re.match(r"^(\s*)([A-G])\)\s+\S+(.*)$", line)
        if control_match and control_match.group(2) in _CONTROL_PATH_PLACEHOLDERS:
            letter = control_match.group(2)
            sanitized_lines.append(
                f"{control_match.group(1)}{letter}) "
                f"{_CONTROL_PATH_PLACEHOLDERS[letter]}"
                f"{control_match.group(3)}"
            )
            continue

        fit_log_match = re.match(
            r"^(\s*)(Input image|Init\. par\. file|Restart file|Output image)"
            r"(\s*:\s*).*$",
            line,
        )
        if fit_log_match:
            key = fit_log_match.group(2)
            sanitized_lines.append(
                f"{fit_log_match.group(1)}{key}{fit_log_match.group(3)}"
                f"{_FIT_LOG_PATH_PLACEHOLDERS[key]}"
            )
            continue

        line = _WINDOWS_ABSOLUTE_PATH_RE.sub("<PATH>", line)
        line = _POSIX_ABSOLUTE_PATH_RE.sub("<PATH>", line)
        sanitized_lines.append(line)

    return "\n".join(sanitized_lines)


def normalize_generation_artifacts(text: str) -> str:
    """Rewrite data-generation terminology into task-level state semantics."""

    if not isinstance(text, str) or not text:
        return text

    normalized = text
    replacements = (
        ("被退火算法拒绝或随后删除", "未被保留或随后被删除"),
        ("多次退火均无法改善", "多次尝试均未改善"),
        ("多次退火未见显著改善", "多次尝试均未见显著改善"),
        ("历史多轮退火未见改善", "历史中的多轮尝试均未见改善"),
        ("多轮退火未见改善", "多轮尝试均未见改善"),
        ("多次退火收敛", "多次尝试后保持稳定"),
        (
            "之前的退火算法可能做出了错误的接受决定",
            "之前曾将未改善的结果保留为后续状态",
        ),
        (
            "当前状态是退火算法接受的较差探索节点",
            "当前状态来自一个虽未改善但被保留的探索结果",
        ),
        (
            "退火算法随机游走导致的结构退化",
            "未改善结果被保留后造成的结构退化",
        ),
        ("退火算法的错误接受", "一个未改善但被保留的结果"),
        ("退火尝试", "探索尝试"),
    )
    for old, new in replacements:
        normalized = normalized.replace(old, new)

    normalized = re.sub(
        r"第\s*(\d+)\s*步的退火接受实际上",
        r"第 \1 步的结果虽被保留为后续状态，但实际上",
        normalized,
    )
    normalized = re.sub(
        r"退火算法在第\s*(\d+)\s*步接受了",
        r"第 \1 步执行并保留了",
        normalized,
    )
    normalized = normalized.replace("被退火算法拒绝", "未被保留")
    normalized = normalized.replace("退火算法拒绝", "未保留")
    normalized = normalized.replace("退火算法", "历史状态转换")
    normalized = normalized.replace("退火", "探索")
    return normalized


def sanitize_history_for_model(history_summary: str) -> str:
    """Normalize legacy history strings before inserting them into a prompt."""

    if not isinstance(history_summary, str) or not history_summary:
        return history_summary
    history_summary = re.sub(
        r"采纳\[([^\]]+)\]\(退火接受,\s*质量未改善\)",
        r"执行[\1]，该结果作为后续状态；相对上一步质量未改善",
        history_summary,
    )
    return normalize_generation_artifacts(history_summary)


def _fmt_metric(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else "NA"


def build_history_summary(
    parent_node: Mapping[str, Any],
    tree: Mapping[str, Any],
    history_max_steps: int = 0,
) -> str:
    """Render task-level history without exposing the search/acceptance method."""

    nodes = list(tree.get("nodes", []))
    by_id = {node["node_id"]: node for node in nodes}
    children_by_parent: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for node in nodes:
        children_by_parent[node.get("parent_id")].append(node)

    chain: list[Mapping[str, Any]] = []
    current: Mapping[str, Any] | None = parent_node
    seen = set()
    while current is not None and current.get("node_id") not in seen:
        seen.add(current.get("node_id"))
        chain.append(current)
        current = by_id.get(current.get("parent_id"))
    chain.reverse()

    if history_max_steps and history_max_steps > 0:
        chain = chain[-history_max_steps:]

    lines: list[str] = []
    for node in chain:
        depth = node.get("depth", 0)
        metrics = node.get("metrics", {})
        metric_text = (
            f"chi2_nu={_fmt_metric(metrics.get('chi2_nu'))}, "
            f"BIC={_fmt_metric(metrics.get('bic'))}"
        )
        action = node.get("action_from_parent")
        if not action:
            lines.append(f"- 第{depth}步(根节点): {metric_text}")
        else:
            label = action.get("coarse_label", "?")
            note = (
                action.get("target") or action.get("reasoning") or ""
            ).strip().replace("\n", " ")[:50]
            if node.get("mh_accepted"):
                prefix = (
                    f"- 第{depth}步 执行[{label}]，该结果作为后续状态；"
                    "相对上一步质量未改善"
                )
            else:
                prefix = f"- 第{depth}步 采纳[{label}]"
            lines.append(
                f"{prefix} → {metric_text}{('；' + note) if note else ''}"
            )

        siblings = children_by_parent.get(node.get("parent_id"), [])
        rejected = [
            sibling
            for sibling in siblings
            if sibling.get("node_id") != node.get("node_id")
            and not sibling.get("is_accepted")
        ]
        if rejected:
            labels = [
                (sibling.get("action_from_parent") or {}).get(
                    "coarse_label", "?"
                )
                for sibling in rejected
            ]
            lines.append(f"    (同层被拒: {labels})")

    return "\n".join(lines) if lines else ""
