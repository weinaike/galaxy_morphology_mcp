"""
Beam Search candidate-action generator for GALFIT single-band fitting.

Mirrors the dispatch structure of the GalfitS `beam_actions.py` but operates on
GALFIT artefacts: the input `feedme` (structure + `# STRUCTURE:` naming
comments), the fitted parameter file (`galfit.NN`, where GALFIT drops the
naming comments — names are recovered by block order from the paired feedme)
and the markdown summary written by `run_galfit`. All sizes/positions are in
pixels on both the VLM side and the feedme side — no unit conversion anywhere.

Also provides `check_feedme_file`, the lightweight structural validator used by
the beam-search orchestrator before every `run_galfit` call (the single-band
counterpart of the GalfitS `check_lyric_file` gate).
"""

import os
import re
import uuid
from typing import Annotated, Any

import dotenv

from . import prompt
from .parse_feedme import parse_components, parse_feedme
from .render_original import effective_re, EXPDISK_RE_FACTOR

dotenv.load_dotenv()


# The shared system message (residual_analysis_message.md) is written for the
# GalfitS multi-band flow and mandates a true sky-PA convention ("north = 0°,
# align on the compass"). The single-band beam flow adopts the N=+Y contract
# instead (north assumed aligned with +Y, so sky-PA ≡ the feedme `10)` frame
# and VLM readings are written verbatim). Patch the PA clause at load time —
# the shared file itself stays untouched for GalfitS.
_PA_CLAUSE_RE = re.compile(r"(?m)^3\. \*\*PA 约定\*\*：.*$")
_NPY_PA_CLAUSE = (
    "3. **PA 约定（N=+Y 契约）**：凡涉及位置角（PA）—— 包括成分的 PA、Fourier 模式的相位角、"
    "建议纠正的方向 —— 一律按本工作流的 **N=+Y 契约**：**假定图像正北方向与 Y 轴正上方一致，"
    "0° = 图像 Y 轴正上方，逆时针增大**——读角度直接对齐图像纵轴。该约定与 GALFIT feedme `10)` "
    "参数行（\"+Y 轴为 0°\"）数值等同：你读出/写出的 PA 会被主模型**原样写入** feedme，不做任何换算。"
    "`detect_bar_lopsidedness` 返回的 `bar.pa_deg` 与 `lopsidedness.phase_deg` 在本契约下可直接引用。"
)


def _galfit_system_message() -> str:
    """Residual-analysis system message with the PA clause swapped to N=+Y."""
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE
    patched, n = _PA_CLAUSE_RE.subn(_NPY_PA_CLAUSE, system_message)
    if n == 0:
        # Clause wording drifted — fall back to appending the contract so the
        # beam prompt and the system message never disagree silently.
        system_message = system_message + "\n\n" + _NPY_PA_CLAUSE
    else:
        system_message = patched
    return system_message


def _fmt_toggle(toggles: dict, key: str) -> str:
    """Render a parameter's fit toggle as free/fixed (+toggle)."""
    v = toggles.get(key)
    if v is None:
        return "unknown"
    return "free" if v else "fixed"


def _parse_stats_from_summary(summary_file: str) -> str:
    """Extract the fitting-statistics lines from a run_galfit summary md.

    Matches the current markdown statistics table (``| χ²/ν ... |``) as well as
    legacy plain-text ``Chi^2/nu = ...`` lines embedded from fit.log.
    """
    try:
        with open(summary_file, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ""
    lines = []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("|") and any(
            token in s for token in ("χ²", "BIC", "Sky Background", "N_dof", "N_free")
        ):
            lines.append(s)
        elif re.search(r"Chi\^?2", s) and "=" in s:
            lines.append(s)
    return "\n".join(lines)


def _build_summary_content_galfit(
    feedme_file: str,
    fitted_param_file: str,
    summary_file: str | None = None,
) -> str:
    """Render the per-component fitted-parameter summary for the stateless VLM.

    Component names come from the `# STRUCTURE:` comments of the input feedme
    (GALFIT output files drop them); fitted values come from `galfit.NN`.
    Everything is in pixels, matching the comparison-PNG panels one-to-one.
    """
    components = parse_components(fitted_param_file, name_file=feedme_file)
    if not components:
        return ""

    out: list[str] = []
    out.append("=== GALFIT 单波段拟合参数摘要（全部数值为像素单位，与对比图面板同参考系） ===\n")
    for i, comp in enumerate(components, start=1):
        out.append(f"- Component {i}: {comp['name']}")
        out.append(f"  - Type: {comp['type']}")
        toggles = comp.get("toggles", {})
        out.append(
            f"  - xcen: {comp['x']:.3f} px ({_fmt_toggle(toggles, 'x')}); "
            f"ycen: {comp['y']:.3f} px ({_fmt_toggle(toggles, 'y')})"
        )
        out.append(f"  - Mag: {comp['mag']:.3f} ({_fmt_toggle(toggles, 'mag')})")
        if comp["type"] == "expdisk":
            rs = comp["re"]
            out.append(
                f"  - Rs: {rs:.3f} px ({_fmt_toggle(toggles, 're')}) "
                f"[有效半径 Re = {EXPDISK_RE_FACTOR}·Rs = {effective_re(comp):.3f} px]"
            )
        else:
            out.append(f"  - Re: {comp['re']:.3f} px ({_fmt_toggle(toggles, 're')})")
        if comp.get("n") is not None:
            out.append(f"  - n: {comp['n']:.3f} ({_fmt_toggle(toggles, 'n')})")
        if comp["type"] != "psf":
            out.append(f"  - q (b/a): {comp['ba']:.3f} ({_fmt_toggle(toggles, 'ba')})")
            out.append(
                f"  - PA: {comp['pa']:.2f}° ({_fmt_toggle(toggles, 'pa')}) "
                "[N=+Y 契约：Y 轴正上方为 0°，逆时针增大；与 feedme 10) 行同帧]"
            )
        else:
            out.append("  - (psf 组件：无 n/q/PA/Re 形状参数)")
        out.append("")

    if summary_file:
        stats = _parse_stats_from_summary(summary_file)
        if stats:
            out.append("=== Fitting Statistics ===")
            out.append(stats)

    return "\n".join(out)


def generate_galfit_beam_actions(
    feedme_file: Annotated[str, "Absolute path to the parent state's input feedme file (carries the '# STRUCTURE:' component names)"],
    fitted_param_file: Annotated[str, "Absolute path to the parent state's fitted parameter file (galfit.NN, e.g. the archived output_param_file returned by run_galfit)"],
    comparison_file: Annotated[str, "Absolute path to the 2x3 comparison PNG produced by the parent state's run_galfit call"],
    summary_file: Annotated[str, "Absolute path to the markdown summary produced by the parent state's run_galfit call (only its statistics table is parsed; may be empty)"] = "",
    global_state_description: Annotated[str, "Cross-round stable facts for the stateless VLM, distilled by the orchestrator from working_note.md (NOT the raw note). Fixed schema per workflow_galfit.md §global_state_description 生成规范: [元信息（px 契约）]/[阶段一结论]/[状态账本(px)]/[回滚边]/[已验证盆]/[被否定假设]/[预算]. Keep ≤ ~50 lines."] = "",
    local_state_description: Annotated[str, "Current-round objective description: parent component inventory C + key params, bound-hit parameters (⚠️ + values vs .cons bounds), residual features, identity anomalies, and orchestrator numeric-rule delegations (companion flux check / disk-Re bottleneck / lens inflation / flat-bulge trigger values). Must NOT suggest candidate directions."] = "",
    branch_id: Annotated[str, "Current beam branch identifier (e.g. 'A', 'B'). Used in candidate action_ids."] = "A",
    parent_label: Annotated[str, "Parent round label inside the branch (e.g. 'A.3'). Used in candidate action_ids."] = "",
    depth: Annotated[int, "Depth of the parent state in the search tree. 1 = after the first fit on the input feedme. Controls candidate count via the prompt: depth=1 → 1-2 candidates (phase-one driven), depth=2 → 2-3, depth>=3 → 2-4."] = 1,
) -> dict[str, Any]:
    """Generate depth-aware candidate composite actions for GALFIT Beam Search.

    Called by the orchestrator agent after each successful ``run_galfit`` fit.
    The output is a structured Markdown list of candidates (with a leading
    ``## Physicality Verdict`` block); the agent performs semantic deduplication
    and global heuristic ranking (per workflow_galfit.md §去重与排序) before
    enqueuing into the priority queue of width W=5.

    Unlike the GalfitS variant there is **no unit conversion**: the VLM reads
    pixels off the comparison PNG and those same pixel values are written
    verbatim into the child feedme.

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
        (feedme_file, "Feedme file"),
        (fitted_param_file, "Fitted parameter file"),
        (comparison_file, "Comparison file"),
    ]:
        if not os.path.exists(path):
            return {"status": "failure", "error": f"{label} not found: {path}"}
    if summary_file and not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    # ── Build parameter summary (names from feedme, values from galfit.NN)
    summary_content = _build_summary_content_galfit(feedme_file, fitted_param_file,
                                                    summary_file)
    if not summary_content:
        return {"status": "failure",
                "error": f"Failed to parse components from fitted param file: {fitted_param_file}"}

    # ── Build system message (residual analysis system message with the PA
    #    clause patched to the N=+Y contract + GALFIT component specification)
    system_message = _galfit_system_message()
    component_spec = prompt.get_component_specification_galfit()
    if component_spec:
        system_message = system_message + "\n\n" + component_spec

    # ── Build two-turn prompts from the galfit beam prompt phases ────
    turn1 = prompt.get_galfit_beam_visual_extraction(
        global_state_description=global_state_description,
    )
    turn2 = prompt.get_galfit_beam_candidate_generation(
        summary_content=summary_content,
        global_state_description=global_state_description,
        local_state_description=local_state_description,
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
            "你是 GALFIT 单波段 Beam Search 候选动作生成器。基于 GALFIT 拟合结果，"
            "通过两阶段思维链输出物理性判定与 2–4 个候选复合动作。\n\n"
            "在这个过程中只能使用 read_file 和 write_file 工具，不能使用其他工具。\n\n"
            f"【输入图像文件】：{os.path.abspath(comparison_file)}\n"
            "（2×3 布局：DATA LOW/HIGH DR | MODEL // RESIDUAL | RESIDUAL ZOOM | 1D SB profile）\n\n"
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
        analysis, session_id, error, _timing = run_openai_analysis(
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
        print(f"[beam_actions_galfit] candidates saved to: {out_file}")
    except OSError as e:
        print(f"[beam_actions_galfit] warning: failed to save candidates file: {e}")
        out_file = None

    # ── Hand-off note for the orchestrator agent ──────────────────────
    handoff = (
        "\n\n---\n"
        "# 候选入队守则（主模型职责）\n"
        "- 先解析返回 Markdown 顶部的 `## Physicality Verdict` 块（verdict / failed_checks / "
        "swap_hint）：verdict=FAIL 的父状态不得参与 s* 更新（workflow_galfit.md §步骤 e 物理性守门），"
        "且需按 §非物理结果恢复协议 生成受保护恢复候选；swap_hint=disk_bulge_swap 时确认候选中含交换"
        " disk ↔ bulge 标签方向的修复候选。verdict 块原样记录到 working_note，不得改写。\n"
        "- 对每个候选执行语义去重（与当前 Q 中已有 (s_j, a_j) 比对；等价则保留 g 较高者）。\n"
        "- 去重判据（同时满足视为等价）：\n"
        "  1) expected_C' 在物理身份上等价（允许 bulge/bar 命名互换等）；\n"
        "  2) 预期参数在容忍带内一致（Re ±20%、n ±0.5、q ±0.1、PA ±10°（N=+Y 契约）、mag ±0.5）；\n"
        "  3) expected_behavior_tag 一致。\n"
        "- 图搜索环检测（先于语义去重，与 Q 内去重共用同一套签名判据）：除比对 Q 外，还须与"
        " **执行历史**比对——(R1) 候选转写为假想 feedme 的规范形式（结构 × free/fixed 配置 × `.cons`"
        " 边界带 × 初始值带，一律 px）与**输入账本**比对，带内等价 → 丢弃（同一输入重跑无新信息）；"
        "(R2) remove-only / 参数 revert / 边界还原类闭式转移候选的产出状态可精确投影，投影签名与"
        "**结果账本**比对（僵尸感知：仅相差 [zombie] 成分的状态等价），严格命中 → 不入队（零成本回滚），"
        "仅结构一致 → 标'[疑似近重复]'（退化惩罚维度记 0）。候选的 novelty_claim 须与账本核对：结构等价"
        "且无新参数轴的候选整条丢弃。\n"
        "- 对保留者按六维（残差改善潜力、物理合理性、路径多样性、退化惩罚、历史一致性、BIC 门槛）\n"
        "  各打 0–1 分后加权平均得到 g：路径多样性权重 ×2，其余 ×1（多样性以全局已执行候选为主、Q 内\n"
        "  元素为辅比对方向差异，同成分结构/同参数轴的重复方向该维低分），按 g 降序截断到 W=5 入队 Q。\n"
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


def check_feedme_file(
    feedme_file: Annotated[str, "Absolute path to the GALFIT feedme configuration file"],
) -> dict[str, Any]:
    """Validate a GALFIT feedme file and return its structured component inventory.

    The single-band counterpart of the GalfitS ``check_lyric_file`` gate: the
    beam-search orchestrator calls this on every ``_iter{n}.feedme`` before
    handing it to ``run_galfit``. It verifies the file parses, that every
    non-sky component carries a semantic ``# STRUCTURE:`` name (GALFIT drops
    them in its output files, so they must be maintained on the input side),
    and returns the canonical component inventory used for beam-state
    signatures (name / type / params / free-fixed toggles, all in px).
    """
    if not os.path.exists(feedme_file):
        return {"status": "failure", "error": f"Feedme file not found: {feedme_file}"}

    errors: list[str] = []
    warnings: list[str] = []

    try:
        paths = parse_feedme(feedme_file)
    except Exception as e:
        return {"status": "failure", "error": f"Failed to parse feedme header: {e}"}

    if not paths.get("input"):
        errors.append("A) input image path is missing")
    if not paths.get("output"):
        errors.append("B) output image path is missing")

    try:
        with open(feedme_file, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {"status": "failure", "error": f"Failed to read feedme: {e}"}

    # Structural sanity: at least one component block and a sky block.
    n_component_headers = len(re.findall(r"(?m)^\s*#\s*(Component|Object)\s*number:", content))
    n_type_lines = len(re.findall(r"(?m)^\s*0\)\s*\w+", content))
    if n_type_lines == 0:
        errors.append("No component blocks found (no '0) <type>' lines)")
    if "sky" not in content.lower():
        warnings.append("No sky component found — GALFIT will not fit the background")

    # Semantic names: recover which blocks carry a '# STRUCTURE:' comment.
    structure_names = re.findall(r"(?im)^\s*#\s*STRUCTURE:\s*(\S+)", content)
    components = parse_components(feedme_file)
    if components and len(structure_names) < len(components):
        warnings.append(
            f"Only {len(structure_names)} of {len(components)} non-sky components carry a "
            "'# STRUCTURE:' naming comment — unnamed components fall back to type-based "
            "names (legend/ledger consistency degrades)"
        )

    inventory = []
    for i, comp in enumerate(components, start=1):
        toggles = comp.get("toggles", {})
        entry = {
            "number": i,
            "name": comp["name"],
            "type": comp["type"],
            "params": {
                "x_px": comp["x"], "y_px": comp["y"], "mag": comp["mag"],
                "re_px": comp["re"],
                "re_effective_px": effective_re(comp) if comp["type"] == "expdisk" else comp["re"],
                "n": comp["n"], "q": comp["ba"], "pa_deg": comp["pa"],
            },
            "free_fixed": dict(toggles) if toggles else {},
            "sky": False,
        }
        inventory.append(entry)

    if errors:
        return {
            "status": "failure",
            "errors": errors,
            "warnings": warnings,
            "fit_region": paths.get("fit_region"),
            "constraint": paths.get("constraint") or None,
        }

    return {
        "status": "success",
        "components": inventory,
        "n_components": len(inventory),
        "fit_region": paths.get("fit_region"),
        "constraint": paths.get("constraint") or None,
        "warnings": warnings,
        "message": (
            "Feedme 结构校验通过。components 为规范化成分清单（px 单位，N=+Y 契约 PA），"
            "可直接用作 beam 状态签名与热启动回填的读取源。"
        ),
    }
