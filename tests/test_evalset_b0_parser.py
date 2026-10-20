"""Unit tests for the B0 legacy-baseline action parser (real-text fixtures)."""

from __future__ import annotations

from component_analysis.evalset.b0_parser import parse_decision_to_action


def _md(body: str) -> str:
    return "## 分析\n若干前置分析文字。\n\n## 本次调整决策如下：\n" + body + "\n\n# 决策落实守则\n- 必须严格落实【调整决策】中的要求…严禁私自决定。\n"


def test_terminal_statement_wins_over_later_discussion() -> None:
    text = _md("当前模型已固化，无需进一步调整。未来如果出现新证据，可以考虑添加伴星系成分。")
    parsed = parse_decision_to_action(text, current_components=["disk", "bulge"])
    assert parsed["action"] == {"action_type": "CONVERGED", "component": None, "replace_from": None, "replace_to": None}


def test_target_line_diff_single_add() -> None:
    text = _md("预期最终成分组成为：**Bulge + Disk + Bar**。具体：增加一个 n=0.5 Sersic 成分作为 Bar。")
    parsed = parse_decision_to_action(text, current_components=["bulge", "disk"])
    assert parsed["action"] == {"action_type": "PROPOSE_ADD", "component": "bar", "replace_from": None, "replace_to": None}


def test_target_line_diff_replace() -> None:
    text = _md("预期最终成分组成为：**Disk + Bulge**。（注：当前 Bar 假设被证伪。）")
    parsed = parse_decision_to_action(text, current_components=["bar", "disk"])
    assert parsed["action"]["action_type"] == "PROPOSE_REPLACE"
    assert parsed["action"]["replace_from"] == "bar" and parsed["action"]["replace_to"] == "bulge"


def test_target_line_diff_promote_from_single_sersic() -> None:
    text = _md("预期最终成分组成为：**Disk + Bar**。执行顺序：sersic→expdisk 先行，再添加 Bar。")
    parsed = parse_decision_to_action(text, current_components=["single_sersic"])
    assert parsed["action"]["action_type"] == "COMPOUND"
    kinds = [a["action_type"] for a in parsed["action"]["atomic_actions"]]
    assert kinds == ["PROMOTE_SINGLE_SERSIC_TO_DISK", "PROPOSE_ADD"]


def test_target_equal_current_is_refit() -> None:
    text = _md("预期最终成分组成为：**Disk + Bar**。本轮仅重置 Bar 的初值并收紧约束。")
    parsed = parse_decision_to_action(text, current_components=["disk", "bar"])
    assert parsed["action"]["action_type"] == "REFIT_PARAMETERS"


def test_explicit_conversion_beats_target_diff() -> None:
    text = _md("预期该中心星系的最终的成分组成：Disk + Bulge + Bar。物理目标：将原 Bar 成分重置并转换为 Bulge（释放 n 值）。")
    parsed = parse_decision_to_action(text, current_components=["bar", "disk"])
    assert parsed["action"]["action_type"] == "PROPOSE_REPLACE"
    assert parsed["action"]["replace_from"] == "bar" and parsed["action"]["replace_to"] == "bulge"


def test_parenthetical_target_fallback() -> None:
    text = _md("本次调整物理目标：由单 Sérsic 扩展为双成分（Bulge + Disk）拟合，消除外围欠拟合。")
    parsed = parse_decision_to_action(text, current_components=["single_sersic"])
    assert parsed["action"]["action_type"] == "COMPOUND"
    kinds = sorted(a["action_type"] for a in parsed["action"]["atomic_actions"])
    assert kinds == ["PROMOTE_SINGLE_SERSIC_TO_DISK", "PROPOSE_ADD"]


def test_negated_replacement_is_not_an_action() -> None:
    text = _md("回退（REVERT）并重构：放弃将中心成分替换为 PSF 的方案，重建 Bulge。预期成分组成：Bulge + Disk。")
    parsed = parse_decision_to_action(text, current_components=["agn", "disk"])
    assert parsed["action"]["action_type"] == "PROPOSE_REPLACE"
    assert parsed["action"]["replace_from"] == "agn" and parsed["action"]["replace_to"] == "bulge"


def test_parameter_add_constraint_is_refit_not_add() -> None:
    text = _md("调整内容：为成分 2 添加窄幅约束（PA 限制在 -86.8 到 -66.8），并重置其初值。")
    parsed = parse_decision_to_action(text, current_components=["disk", "bar"])
    assert parsed["action"]["action_type"] == "REFIT_PARAMETERS"


def test_unresolvable_returns_failed_with_reason() -> None:
    text = _md("综合来看本轮维持观察，暂不定论。")
    parsed = parse_decision_to_action(text, current_components=["disk"])
    assert parsed["status"] == "PARSE_TO_ACTION_FAILED"
    assert parsed["reason"]
