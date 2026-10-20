"""B0 legacy-baseline action parser: component_analysis decision markdown -> canonical action.

Deterministic pure function. Parsing takes the FIRST structural step of the
decision (legacy decisions often state a multi-step ladder; the execution rule
is one structural change per round), matching the frozen set's
immediate-next-round action typing. Key conventions learned from the frozen
historical decision corpus:

- an explicit terminal statement wins over any other content (best-round
  decisions still discuss hypothetical future adjustments);
- parameter-only adjustments (add a CONSTRAINT, reset an INITIAL value) are
  REFIT, not structural ADD — the verb must act on a component;
- "保留原有 Sersic 作为核球" style relabeling is PROPOSE_REPLACE;
- the same atom restated in the goal line and the detail line collapses to one.

Unresolvable text returns ``PARSE_TO_ACTION_FAILED`` with a reason — the
failure rate itself is a reported metric of the legacy method's actionability.
"""

from __future__ import annotations

import re
from typing import Any

from .adjudication_full import _BOILERPLATE_MARKERS, _DECISION_MARKER_RE

_FAILED = {"status": "PARSE_TO_ACTION_FAILED"}

# Component lexicon (ordered: more specific words first).
_COMPONENT_PATTERNS: list[tuple[str, str]] = [
    ("fourier_m1", r"fourier|傅里叶|f1|m=1"),
    ("companion", r"companion|伴星系|伴星"),
    ("edge_on_disk", r"edge-?on|侧向盘|edgeondisk"),
    ("single_sersic", r"single\s*sersic|单成分|单一\s*sersic"),
    ("agn", r"\bagn\b|nucleus|活动核|活动星系核|psf|点源"),
    ("lens", r"lens|透镜"),
    ("disk", r"\bdisk\b|expdisk|指数盘|星系盘|盘成分"),
    ("bulge", r"bulge|核球"),
    ("bar", r"\bbar\b|棒"),
]
_COMPONENT_RE = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in _COMPONENT_PATTERNS}

_TERMINAL_RE = re.compile(
    r"无需进一步调整|无需再?调整|不再需要调整|模型(已)?固化|保持现有(模型|结构|成分组合)|已达到(收敛|最优)|no further adjustment|keep the current model|final model"
    r"|拟合结束|可接受终态|宣告.{0,8}终态|锁定(最终|拟合)?结果|拟合任务.{0,6}(完成|圆满)|结束该星系的拟合流程|acceptable final state|维持当前.{0,12}(锁定|结束|终态)",
    re.IGNORECASE,
)
_ADD_RE = re.compile(r"(新增|增加|添加|引入|加入|add)", re.IGNORECASE)
_REMOVE_RE = re.compile(r"(删除|移除|去掉|放弃|remove|delete)", re.IGNORECASE)
_REPLACE_RE = re.compile(r"(替换|改标|改为|转换成|转换为|转为|改用|replace|convert|switch)", re.IGNORECASE)
# "保留原有/现有 <X> 作为 <Y>" / "将 <X> 作为 <Y>（改标）" relabeling.
_RELABEL_RE = re.compile(r"(保留|将|让).{0,25}?(sersic|单成分|sérsic|成分).{0,30}?(作为|演化为|演变为|改造为|转变为|重构为).{0,12}?(核球|bulge|盘|disk|棒|bar|透镜|lens|agn)", re.IGNORECASE | re.DOTALL)
_PROMOTE_RE = re.compile(r"(sersic|单成分).{0,30}(expdisk|作为盘)|原位确认|promote", re.IGNORECASE | re.DOTALL)
_PARAMETER_WORD_RE = re.compile(r"约束|初值|初始值|边界|绑定|权重|fix|free|重置|reset", re.IGNORECASE)
_LADDER_FIRST_RE = re.compile(r"第一步|首先|step\s*1|先添加|先增加|优先", re.IGNORECASE)


def _decision_section(text: str, *, max_chars: int = 4000) -> str:
    markers = list(_DECISION_MARKER_RE.finditer(text))
    boilerplate_starts = [text.find(word) for word in _BOILERPLATE_MARKERS if text.find(word) != -1]
    cut = min(boilerplate_starts) if boilerplate_starts else len(text)
    for marker in reversed(markers):
        body = text[marker.start():min(marker.start() + max_chars, max(cut, marker.start()))]
        if len(body.strip()) > 80:
            return body
    end = cut if boilerplate_starts else len(text)
    return text[max(0, end - max_chars):end]


def _atom(action_type: str, component: str | None = None, replace_from: str | None = None, replace_to: str | None = None) -> dict[str, Any]:
    return {"action_type": action_type, "component": component, "replace_from": replace_from, "replace_to": replace_to}


def _component_in(segment: str) -> str | None:
    for name, _ in _COMPONENT_PATTERNS:
        if _COMPONENT_RE[name].search(segment):
            return name
    return None


def _split_units(section: str) -> list[str]:
    units = re.split(r"\n\s*(?:[#*\-]\s+|\d+[.、]\s)|(?<=[。；;])", section)
    return [unit.strip() for unit in units if len(unit.strip()) >= 6]


def _structural_component(unit: str, verb_re: re.Pattern[str]) -> str | None:
    """Component the structural verb acts ON: a component keyword must follow the
    verb closely or the verb must say 成分/component. Parameter objects
    (添加约束 / 增加 Re 初值) are not structural."""
    for match in verb_re.finditer(unit):
        tail = unit[match.end():match.end() + 24]
        component = _component_in(tail)
        if component:
            return component
        if re.match(r"\s*[的]?\s*(一个|一个)?\s*(sérsic|sersic)?\s*(成分|component|profile)", tail, re.IGNORECASE) or re.search(r"^(一个)?\s*(成分|component)", tail.strip(), re.IGNORECASE):
            # "新增一个成分" without naming it: try the whole unit for a component.
            return _component_in(unit)
    return None


_TARGET_INTRO_RE = re.compile(r"成分组成|目标.{0,6}结构|成分类型预测|预期.{0,10}组成|final composition|target structure", re.IGNORECASE)


def _components_in_order(segment: str) -> list[str]:
    seen: list[str] = []
    low = segment.lower()
    positions: list[tuple[int, str]] = []
    for name, _ in _COMPONENT_PATTERNS:
        match = _COMPONENT_RE[name].search(low)
        if match:
            positions.append((match.start(), name))
    for _, name in sorted(positions):
        if name not in seen:
            seen.append(name)
    return seen


def _target_components(section: str) -> list[str] | None:
    """Target component set from the 预期成分组成 declaration (intro line, value on
    the same line after the colon or on the following line)."""
    lines = section.splitlines()
    for index, line in enumerate(lines):
        intro = None
        for match in _TARGET_INTRO_RE.finditer(line):
            intro = match
        if intro is None:
            continue
        for candidate, start in [(line, intro.end())] + [(nth, 0) for nth in lines[index + 1:index + 3]]:
            candidate = re.sub(r"（注[^）]*）|\(注[^)]*\)", "", candidate)  # strip hypothetical annotations
            tail = candidate[start:]
            for colon in ["：", ":"]:
                pos = tail.find(colon)
                if pos != -1:
                    tail = tail[pos + 1:]
                    break
            tail = tail.split("。")[0]
            found = _components_in_order(tail) or _components_in_order(candidate)
            if found:
                return found
    return None


_NEGATION_RE = re.compile(r"(放弃|取消|不再|废除|废弃)[^。；\n]{0,30}(替换|改标|改为|添加|加入|增加|引入|使用)", re.IGNORECASE)
_STATE_MARKERS = {"single_sersic", "unclassified_sersic", "unidentified_sersic"}


def _diff_action(current: list[str], target: list[str]) -> dict[str, Any] | None:
    """Structural diff between the state's component set and the stated target set."""
    current_set = {c for c in current if c not in _STATE_MARKERS}
    target_set = set(target)
    added = sorted(target_set - current_set)
    removed = sorted(current_set - target_set)
    state_markers = [c for c in current if c in _STATE_MARKERS]
    atoms: list[dict[str, Any]] = []
    if state_markers and "disk" in target_set and not current_set:
        atoms.append(_atom("PROMOTE_SINGLE_SERSIC_TO_DISK", component="disk"))
        added = [component for component in added if component != "disk"]  # promotion supplies the disk
    for component in removed:
        atoms.append(_atom("PROPOSE_REMOVE", component=component))
    for component in added:
        atoms.append(_atom("PROPOSE_ADD", component=component))
    if not atoms:
        return _atom("REFIT_PARAMETERS")
    if len(atoms) == 1:
        return atoms[0]
    if len(removed) == 1 and len(added) == 1:
        return _atom("PROPOSE_REPLACE", replace_from=removed[0], replace_to=added[0])
    return {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": atoms, "compound_reason": "NO_INTERMEDIATE_ROUND"}


def _parenthetical_target(section: str) -> list[str] | None:
    """Fallback target: a parenthetical component list ("双成分（Bulge + Disk）拟合")."""
    for match in re.finditer(r"（([^（）]{6,80})）|\(([^()]{6,80})\)", section):
        segment = match.group(1) or match.group(2) or ""
        if "+" in segment or "＋" in segment or "、" in segment:
            found = _components_in_order(segment)
            if len(found) >= 2:
                return found
    return None


def _explicit_atoms(section: str, units: list[str]) -> list[dict[str, Any]]:
    """Structural atoms from explicit action verbs (dedup, negation-stripped)."""
    atoms: list[dict[str, Any]] = []
    for unit in units[:8]:
        relabel = _RELABEL_RE.search(unit)
        if relabel:
            atoms.append(_atom("PROPOSE_REPLACE", replace_from="single_sersic", replace_to=_component_in(relabel.group(0)) or "bulge"))
            continue
        if _REMOVE_RE.search(unit):
            component = _structural_component(unit, _REMOVE_RE) or _component_in(unit)
            if component:
                atoms.append(_atom("PROPOSE_REMOVE", component=component))
                continue
        if _ADD_RE.search(unit):
            component = _structural_component(unit, _ADD_RE)
            if component:
                atoms.append(_atom("PROPOSE_ADD", component=component))
                continue
            if _PROMOTE_RE.search(unit):
                atoms.append(_atom("PROMOTE_SINGLE_SERSIC_TO_DISK", component="disk"))
                continue
        replace_match = _REPLACE_RE.search(unit)
        if replace_match:
            to_component = _structural_component(unit, _REPLACE_RE) or _component_in(unit)
            if to_component:
                # The converted-away component usually sits before the verb
                # ("将原 Bar 转换为 Bulge"); the state set disambiguates it later.
                from_component = _component_in(unit[:replace_match.start()])
                atoms.append(_atom("PROPOSE_REPLACE", replace_from=from_component, replace_to=to_component))
                continue
    unique: list[dict[str, Any]] = []
    for atom in atoms:
        if atom not in unique:
            unique.append(atom)
    return unique


def _first_step_variant(action: dict[str, Any], explicit_atoms: list[dict[str, Any]]) -> dict[str, Any]:
    """Ladder-first-step readout for sensitivity analysis (primary is the target diff)."""
    if action["action_type"] != "COMPOUND":
        return action
    atoms = action.get("atomic_actions", [])
    if len(atoms) == 1:
        return atoms[0]
    if len(explicit_atoms) == 1:
        return explicit_atoms[0]
    # Convention: promotion first, then structural removals/replacements, then additions.
    order = {"PROMOTE_SINGLE_SERSIC_TO_DISK": 0, "PROPOSE_REMOVE": 1, "PROPOSE_REPLACE": 1, "PROPOSE_ADD": 2}
    return sorted(atoms, key=lambda atom: order.get(atom["action_type"], 3))[0]


def parse_decision_to_action(text: str, current_components: list[str] | None = None) -> dict[str, Any]:
    """Parse a component_analysis markdown into one canonical action.

    Priority: terminal statement > target-structure diff (needs the state's
    current components) > explicit structural verbs > parameter-only > failed.
    """
    if not text or not text.strip():
        return {**_FAILED, "reason": "EMPTY_TEXT"}
    section = _decision_section(text)
    units = _split_units(section)

    # Terminal statements are decision-level and win outright.
    if _TERMINAL_RE.search(section):
        terminal = _atom("CONVERGED")
        return {"status": "OK", "action": terminal, "action_first_step": terminal}

    # Explicit conversion wording (将原 X 转换为 Y) beats the target-line diff,
    # which states a multi-round goal that may keep both components.
    explicit = _explicit_atoms(section, units)
    if any(atom["action_type"] in {"PROPOSE_REPLACE", "PROMOTE_SINGLE_SERSIC_TO_DISK"} for atom in explicit) and len(explicit) == 1:
        return {"status": "OK", "action": explicit[0], "action_first_step": explicit[0]}

    # Explicit current-round scoping ("本轮暂不拆分，先加 m=1") overrides the
    # multi-round target diff: the md's immediate recommendation is the action.
    _SCOPE_RE = re.compile(r"(本轮|这一轮|此轮)(暂不|不进行|只|仅|而是|先)", re.IGNORECASE)
    if _SCOPE_RE.search(section) and len(explicit) == 1:
        return {"status": "OK", "action": explicit[0], "action_first_step": explicit[0]}

    # An explicit relabel ("原 sersic 改造为 Bar") pins the role assignment; the
    # target diff would swap roles (marker->disk by default). Trust the explicit set.
    if any(_RELABEL_RE.search(unit) for unit in units) and 1 <= len(explicit) <= 2:
        if len(explicit) == 1:
            return {"status": "OK", "action": explicit[0], "action_first_step": explicit[0]}
        compound = {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": explicit, "compound_reason": "NO_INTERMEDIATE_ROUND"}
        return {"status": "OK", "action": compound, "action_first_step": _first_step_variant(compound, explicit)}

    target = _target_components(section) or _parenthetical_target(section)
    if target is not None and current_components is not None:
        action = _diff_action(list(current_components), target)
        first_step = _first_step_variant(action, explicit)
        return {"status": "OK", "action": action, "action_first_step": first_step}

    # Strip negated verb phrases ("放弃将…替换为…") before explicit-verb parsing.
    section = _NEGATION_RE.sub("", section)
    units = _split_units(section)

    ordered = units
    ladder = [unit for unit in units if _LADDER_FIRST_RE.search(unit)]
    if ladder:
        ordered = ladder + [unit for unit in units if unit not in ladder]

    atoms: list[dict[str, Any]] = []
    parameter_seen = False
    reasons: list[str] = []
    for unit in ordered[:8]:
        relabel = _RELABEL_RE.search(unit)
        if relabel:
            atoms.append(_atom("PROPOSE_REPLACE", replace_from="single_sersic", replace_to=_component_in(relabel.group(0)) or "bulge"))
            continue
        if _REMOVE_RE.search(unit):
            component = _structural_component(unit, _REMOVE_RE) or _component_in(unit)
            if component:
                atoms.append(_atom("PROPOSE_REMOVE", component=component))
            else:
                reasons.append("REMOVE_WITHOUT_COMPONENT")
            continue
        if _ADD_RE.search(unit):
            component = _structural_component(unit, _ADD_RE)
            if component:
                atoms.append(_atom("PROPOSE_ADD", component=component))
                continue
            if _PROMOTE_RE.search(unit):
                atoms.append(_atom("PROMOTE_SINGLE_SERSIC_TO_DISK", component="disk"))
                continue
            if _PARAMETER_WORD_RE.search(unit):
                parameter_seen = True
                continue
            reasons.append("ADD_WITHOUT_COMPONENT")
            continue
        if _REPLACE_RE.search(unit):
            component = _structural_component(unit, _REPLACE_RE) or _component_in(unit)
            if component:
                atoms.append(_atom("PROPOSE_REPLACE", replace_to=component))
            else:
                reasons.append("REPLACE_WITHOUT_COMPONENT")
            continue
        if _PARAMETER_WORD_RE.search(unit) and not atoms:
            parameter_seen = True

    # Collapse the same atom restated across goal/detail lines.
    unique: list[dict[str, Any]] = []
    for atom in atoms:
        if not any(atom == prior for prior in unique):
            unique.append(atom)

    if unique:
        if len(unique) == 1:
            return {"status": "OK", "action": unique[0], "action_first_step": unique[0]}
        compound = {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": unique, "compound_reason": "NO_INTERMEDIATE_ROUND"}
        return {"status": "OK", "action": compound, "action_first_step": _first_step_variant(compound, unique)}
    if parameter_seen:
        refit = _atom("REFIT_PARAMETERS")
        return {"status": "OK", "action": refit, "action_first_step": refit}
    return {**_FAILED, "reason": reasons[0] if reasons else "NO_RECOGNIZED_ACTION"}


def historical_agreement(frozen_run_dir) -> dict[str, Any]:
    """QA metric: parse the HISTORICAL decision markdown of every benchmark state
    round and compare with the frozen canonical action. Circular as a model score
    (canonical adjudication saw the same text) — reported ONLY as parser quality."""
    import json
    from pathlib import Path

    run_dir = Path(frozen_run_dir)
    benchmark = [json.loads(line) for line in (run_dir / "evaluation-set-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    stats: dict[str, int] = {"total": 0, "md_found": 0, "parse_ok": 0, "type_match": 0, "full_match": 0, "converged_match": 0}
    failures: list[tuple[str, str]] = []
    for sample in benchmark:
        stats["total"] += 1
        state_ref = sample["evidence_refs"][0]
        md_dir = Path(state_ref).parent
        pattern = "*component_analysis*.md" if sample["mode"] == "single_band" else "all_bands_comparison_component_analysis_*.md"
        mds = sorted(md_dir.glob(pattern))
        if not mds:
            continue
        stats["md_found"] += 1
        text = "\n".join(md.read_text(encoding="utf-8", errors="replace") for md in mds)
        parsed = parse_decision_to_action(text, current_components=sample["source_components"])
        if parsed["status"] != "OK":
            failures.append((sample["sample_id"], parsed.get("reason", "?")))
            continue
        stats["parse_ok"] += 1
        action = parsed["action"]
        canonical = sample["canonical_action"]
        if action["action_type"] == canonical["action_type"]:
            stats["type_match"] += 1
            if action == canonical:
                stats["full_match"] += 1
            if action["action_type"] == "CONVERGED":
                stats["converged_match"] += 1
    return {"stats": stats, "failures": failures[:20]}
