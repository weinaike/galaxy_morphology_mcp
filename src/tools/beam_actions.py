"""
Beam Search candidate-action generator for GalfitS multi-band fitting.

Mirrors the dispatch structure of `analyze_multiband_components` but, instead of
returning a single next-step decision, returns 2–4 candidate composite actions
(with local benefit σ, expected component inventory, behavior tag) for the
orchestrator agent to deduplicate, rank, and enqueue.
"""

import os
import uuid
from typing import Annotated, Any

import dotenv

from . import prompt
from .analyze_image import read_summary_file
from .parse_lyric import (
    extract_component_attributes,
    parse_image_infos_from_lyric,
)

dotenv.load_dotenv()


def _build_summary_content(lyric_file: str, summary_file: str) -> str:
    """Render the same per-band component summary used by analyze_multiband_components.

    Returns an empty string if no band could be parsed (caller treats this as a
    hard failure, mirroring the upstream contract).
    """
    summary_content = ""
    image_infos = parse_image_infos_from_lyric(lyric_file)
    for image_info in image_infos:
        components = extract_component_attributes(
            summary_file=summary_file,
            config_file=lyric_file,
            fits_file=image_info.image[0],
            band=image_info.band,
        )
        summary_content += f"=== Band: {image_info.band} ===\n"
        for component in components:
            summary_content += f"- Component: {component['name']}\n"
            summary_content += f"  - Type: {component['type']}\n"
            summary_content += f"  - Parameters:\n"
            for param_name, param_value in component.items():
                if param_name in ("name", "type"):
                    continue
                summary_content += f"    - {param_name}: {param_value}\n"
        summary_content += "\n"
    return summary_content


def generate_beam_actions(
    lyric_file: Annotated[str, "Absolute path to the parent state's .lyric config file"],
    summary_file: Annotated[str, "Absolute path to the parent state's .gssummary file"],
    comparison_file: Annotated[str, "Absolute path to all_bands_comparison.png produced by the parent state's fitting"],
    working_note_file: Annotated[str, "Absolute path to the multi-branch working_note.md so the VLM can see full beam history"] = "",
    branch_id: Annotated[str, "Current beam branch identifier (e.g. 'A', 'B'). Used in candidate action_ids."] = "A",
    parent_label: Annotated[str, "Parent round label inside the branch (e.g. 'A.3'). Used in candidate action_ids."] = "",
    depth: Annotated[int, "Depth of the parent state in the search tree. 1 = after the first fit on the input lyric. Controls candidate count via the prompt: depth=1 → 1-2 candidates (phase-one driven), depth=2 → 2-3, depth>=3 → 2-4."] = 1,
    custom_instructions: Annotated[str, "Extra context for the VLM: forbidden actions, phase-one bar/lop findings, etc."] = "",
) -> dict[str, Any]:
    """Generate depth-aware candidate composite actions for Beam Search.

    Called by the orchestrator agent after each successful fit. The output is a
    structured Markdown list of candidates; the agent then performs semantic
    deduplication and global heuristic ranking (per workflow_galfits.md §去重与排序)
    before enqueuing into the priority queue of width W=5.

    Candidate count is driven by ``depth`` via the prompt template:
    - depth=1 -> 1-2 candidates (phase-one bar/lop driven; usually deterministic)
    - depth=2 -> 2-3 candidates
    - depth>=3 -> 2-4 candidates (full beam exploration)

    Returns
    -------
    dict with keys:
        - status: "success" | "failure"
        - candidates: str (the VLM-generated Markdown, only on success)
        - candidates_file: str | None (path to the saved .md file)
        - branch_id, parent_label, depth: echoed back for traceability
        - error: str (only on failure)
    """
    # ── Validate inputs ───────────────────────────────────────────────
    for path, label in [
        (lyric_file, "Lyric file"),
        (summary_file, "Summary file"),
        (comparison_file, "Comparison file"),
    ]:
        if not os.path.exists(path):
            return {"status": "failure", "error": f"{label} not found: {path}"}

    # ── Build parameter summary (same shape as analyze_multiband_components)
    summary_content = _build_summary_content(lyric_file, summary_file)
    if not summary_content:
        return {"status": "failure",
                "error": f"Failed to parse components from summary: {summary_file}"}

    # ── Inject working_note history into custom_instructions ──────────
    if working_note_file and os.path.exists(working_note_file):
        wn_content = read_summary_file(working_note_file) or ""
        if wn_content:
            custom_instructions = (
                (custom_instructions + "\n\n" if custom_instructions else "")
                + "历史轮次与跨分支决策摘要（working_note.md）：\n"
                + wn_content
            )

    # ── Build system message (reuse residual analysis system message +
    #    GalfitS component specification so the VLM obeys the same rules)
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE
    component_spec = prompt.get_component_specification_galfits()
    if component_spec:
        system_message = system_message + "\n\n" + component_spec

    # ── Build two-turn prompts from the beam_action_generation phases ─
    turn1 = prompt.get_beam_visual_extraction()
    turn2 = prompt.get_beam_candidate_generation(
        summary_content=summary_content,
        custom_instructions=custom_instructions,
        branch_id=branch_id,
        parent_label=parent_label,
        depth=depth,
    )

    # ── Dispatch to the configured analysis backend ───────────────────
    analysis_mode = os.environ.get("ANALYSIS_MODE", "vlm").lower()
    session_id = ""
    analysis: str | None = None
    error: str | None = None

    if analysis_mode == "cc":
        if not os.environ.get("CLAUDECODE_API_KEY"):
            return {"status": "failure",
                    "error": "ANALYSIS_MODE=cc requires CLAUDECODE_API_KEY to be set"}
        from .cc_analysis import run_component_analysis_cc
        session_id = str(uuid.uuid4())
        analysis, error = run_component_analysis_cc(
            system_prompt=system_message,
            analysis_prompts=[turn1, turn2],
            session_id=session_id,
        )

    elif analysis_mode == "acp":
        from .acp_analysis import run_component_analysis_acp
        # ACP uses a single mega-prompt concatenating all phases.
        mega = (
            "你是 Beam Search 候选动作生成器。基于 GALFITS 多波段拟合结果，"
            "通过两阶段思维链输出 2–4 个候选复合动作。\n\n"
            "在这个过程中只能使用 read_file 和 write_file 工具，不能使用其他工具。\n\n"
            f"【输入图像文件】：{os.path.abspath(comparison_file)}\n"
            "（包含原图、模型图、2D残差图及1D表面亮度轮廓图）\n\n"
            "请使用 read_file 工具读取上述文件后，依次执行以下 2 个阶段。"
            "在阶段一中，你必须保持绝对的客观。\n\n"
            f"{turn1}\n\n{turn2}"
        )
        analysis, session_id, error = run_component_analysis_acp(
            system_prompt=system_message,
            analysis_prompts=[mega],
        )

    else:
        # vlm mode: multi-turn via OpenAI SDK
        from .openai_analysis import run_openai_analysis
        deferred_system = os.environ.get("VLM_DEFERRED_SYSTEM", "0") == "1"
        analysis, session_id, error = run_openai_analysis(
            system_prompt=system_message,
            analysis_prompts=[turn1, turn2],
            image_path=os.path.abspath(comparison_file),
            deferred_system=deferred_system,
        )

    if error:
        result: dict[str, Any] = {"status": "failure", "error": error}
        if session_id:
            result["session_id"] = session_id
        if analysis:
            result["partial_analysis"] = analysis
        return result

    assert analysis is not None, "analysis must not be None when error is None"

    # ── Persist the candidate list alongside other fitting artefacts ─
    base_name = os.path.splitext(os.path.basename(comparison_file))[0]
    if session_id:
        out_file = os.path.join(
            os.path.dirname(comparison_file),
            f"{base_name}_beam_actions_{branch_id}_{session_id}.md",
        )
    else:
        out_file = os.path.join(
            os.path.dirname(comparison_file),
            f"{base_name}_beam_actions_{branch_id}.md",
        )
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(analysis)
        print(f"[beam_actions] candidates saved to: {out_file}")
    except OSError as e:
        print(f"[beam_actions] warning: failed to save candidates file: {e}")
        out_file = None

    # ── Hand-off note for the orchestrator agent ──────────────────────
    handoff = (
        "\n\n---\n"
        "# 候选入队守则（主模型职责）\n"
        "- 对每个候选执行语义去重（与当前 Q 中已有 (s_j, a_j) 比对；等价则保留 g 较高者）。\n"
        "- 去重判据（同时满足视为等价）：\n"
        "  1) expected_C' 在物理身份上等价（允许 bulge/bar 命名互换等）；\n"
        "  2) 预期参数在容忍带内一致（Re ±20%、n ±0.5、q ±0.1、PA ±10°、mag ±0.5）；\n"
        "  3) expected_behavior_tag 一致。\n"
        "- 对保留者按六维（残差改善潜力、物理合理性、路径多样性、退化惩罚、历史一致性、BIC 门槛）\n"
        "  各打 0–1 分后等权平均得到 g，按 g 降序截断到 W=5 入队 Q。\n"
        "- candidate 的 σ 仅供参考，不直接用于排序。\n"
    )

    return {
        "status": "success",
        "candidates": analysis + handoff,
        "candidates_file": out_file,
        "branch_id": branch_id,
        "parent_label": parent_label,
        "depth": depth,
        "session_id": session_id,
    }
