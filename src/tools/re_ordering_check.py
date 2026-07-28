"""Programmatic Re ordering check for multi-band GalfitS fits.

Verifies that galaxy center components satisfy the canonical total-order
chain ``Re_disk > Re_lens > Re_bar > Re_bulge`` using the *subsequence rule*:
only present center components are compared, in their canonical order;
AGN (N-block, no Re) and companion galaxies are excluded.

Intended to be called by the main agent after every beam-search fit. The
returned ``custom_instructions_hint`` is a ready-to-inject string that
feeds into ``generate_beam_actions``'s ``custom_instructions``.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from .parse_lyric import parse_gssummary, parse_component_types, _SIZE_PARAM_MAP


# Canonical total-order chain, largest to smallest. The check compares only
# the roles that are actually present, in this relative order.
_CHAIN: list[str] = ["disk", "lens", "bar", "bulge"]

# Epsilon to dodge IEEE-754 noise; NOT a physical tolerance. Two Re values
# within this arcsec-scale epsilon are considered equal and thus in violation
# of the strict ">" rule.
_EPS_ARCSEC: float = 1e-9

# Label prefixes (lower-cased, startswith) that mark a P-block component as a
# companion galaxy rather than a main-galaxy center component.
_COMPANION_PREFIXES: tuple[str, ...] = ("comp", "companion", "secondary", "satellite")


# --------------------------------------------------------------------------- #
# Lyric helpers
# --------------------------------------------------------------------------- #
def _parse_n_block_labels(lyric_file: str) -> set[str]:
    """Return the set of N-block component labels (``Na1``, ``Nb1``, ...).

    AGN / nucleus components live in the N-block and have no physical Re
    quantity; identifying them by block (rather than by name heuristic)
    avoids mis-classifying a user-named AGN like ``nucleus`` or ``core``
    as a Bulge.
    """
    labels: set[str] = set()
    pat = re.compile(r"^N([a-z])1\)\s*(\S+)")
    with open(lyric_file, "r", encoding="utf-8") as f:
        for line in f:
            m = pat.match(line.strip())
            if m:
                labels.add(m.group(2))
    return labels


def _classify_label(label: str, n_labels: set[str]) -> str:
    """Map a P- or N-block component label to a semantic role.

    Returns one of ``disk`` / ``bulge`` / ``bar`` / ``lens`` / ``agn`` /
    ``companion`` / ``other``.
    """
    if label in n_labels:
        return "agn"
    low = label.lower()
    if low.startswith("disk"):
        return "disk"
    if low.startswith("bulge") or low.startswith("pseudobulge"):
        return "bulge"
    if low.startswith("bar"):
        return "bar"
    if low.startswith("lens"):
        return "lens"
    if any(low.startswith(p) for p in _COMPANION_PREFIXES):
        return "companion"
    return "other"


def _size_param_for(profile_type: str) -> str:
    """Return the parameter name that represents a component's size.

    Sersic family → ``Re``; edge-on disk → ``rs``; Ferrer bar → ``Rout``;
    Gaussian ring → ``r0``. Falls back to ``Re`` for unknown profile types
    (most galaxy components are sersic-family anyway).
    """
    return _SIZE_PARAM_MAP.get(profile_type, "Re")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def check_re_ordering(
    summary_file: Annotated[
        str,
        "Absolute path to the .gssummary file produced by a GalfitS fit round.",
    ],
    lyric_file: Annotated[
        str,
        "Absolute path to the .lyric config file used for this round. Used to "
        "classify components (P-block profile type + N-block AGN detection).",
    ],
) -> Annotated[
    dict[str, Any],
    "Structured check result. Key fields: status ('pass'|'fail'|'error'), "
    "expected_chain, components, excluded, violations, swappable_overall, "
    "custom_instructions_hint (ready to inject into generate_beam_actions).",
]:
    """Programmatically verify component Re ordering against the canonical chain.

    The canonical total-order baseline is ``Re_disk > Re_lens > Re_bar > Re_bulge``.
    The *subsequence rule* extracts the subchain of actually-present center
    components and requires each adjacent pair to be strictly decreasing.

    Excluded from comparison:
      * AGN / Nucleus (N-block) — has no physical Re quantity;
      * Companion galaxies (P-block label prefixed with ``comp``/``companion``/
        ``secondary``/``satellite``) — independent source, not part of the
        main galaxy's component decomposition.

    Strict comparison (no physical tolerance); ``epsilon=1e-9`` arcsec only
    guards against IEEE-754 float noise.

    The function never raises — any parse / IO / logic error is caught and
    returned as ``status='error'`` so the workflow can degrade gracefully
    (verifier subagent remains the final fallback at lock time).
    """
    try:
        return _check_re_ordering_impl(summary_file, lyric_file)
    except Exception as e:  # noqa: BLE001 — intentional broad guard
        return {
            "status": "error",
            "error_message": f"{type(e).__name__}: {e}",
            "summary_file": summary_file,
            "lyric_file": lyric_file,
            "expected_chain": "",
            "components": [],
            "excluded": [],
            "violations": [],
            "swappable_overall": False,
            "custom_instructions_hint": "",
            "warnings": [],
        }


# --------------------------------------------------------------------------- #
# Implementation
# --------------------------------------------------------------------------- #
def _check_re_ordering_impl(summary_file: str, lyric_file: str) -> dict[str, Any]:
    # 1. Parse .gssummary (returns flat dict of ALL params, free + fixed merged)
    params, _ = parse_gssummary(summary_file)
    if not params:
        return _error_result(
            summary_file, lyric_file,
            f"parse_gssummary returned empty params from {summary_file!r}",
        )

    # 2. Parse P-block {label: profile_type}
    p_types = parse_component_types(lyric_file)
    if not p_types:
        return _error_result(
            summary_file, lyric_file,
            f"parse_component_types returned no P-block components from {lyric_file!r}",
        )

    # 3. Parse N-block labels (AGN/nucleus)
    n_labels = _parse_n_block_labels(lyric_file)

    # 4. Classify each P-block label, extract Re (or size-equivalent)
    components: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    warnings: list[str] = []
    role_to_pairs: dict[str, list[tuple[str, float]]] = {}

    # 4a. N-block labels are AGN/nucleus by definition — excluded from Re
    # comparison (no physical Re quantity). They never appear in p_types
    # (which is P-block only), so we record them explicitly here.
    for n_label in n_labels:
        excluded.append({
            "label": n_label,
            "role": "agn",
            "reason": "N-block (no physical Re quantity)",
        })

    for label, ptype in p_types.items():
        role = _classify_label(label, n_labels)
        if role in ("agn", "companion", "other"):
            reason = {
                "agn": "N-block (no physical Re quantity)",
                "companion": "companion galaxy (separate source)",
                "other": "unrecognized P-block label (not disk/bulge/bar/lens)",
            }[role]
            excluded.append({"label": label, "role": role, "reason": reason})
            if role == "other":
                warnings.append(
                    f"P-block label {label!r} did not match any known role "
                    f"(disk/bulge/bar/lens/comp*); excluded from Re comparison. "
                    "Consider renaming to a semantic role if this is a center component."
                )
            continue

        size_param = _size_param_for(ptype)
        param_name = f"{label}_{size_param}"
        if param_name not in params:
            warnings.append(
                f"size param {param_name!r} not found in summary for component "
                f"{label!r} (profile={ptype}); skipping this component"
            )
            continue

        try:
            re_arcsec = float(params[param_name])
        except (TypeError, ValueError) as e:
            warnings.append(
                f"size param {param_name}={params[param_name]!r} not numeric "
                f"for component {label!r}: {e}; skipping"
            )
            continue

        components.append({
            "label": label,
            "role": role,
            "profile_type": ptype,
            "size_param": size_param,
            "re_arcsec": re_arcsec,
        })
        role_to_pairs.setdefault(role, []).append((label, re_arcsec))

    # 5. Collapse multiple components per role → take max (with warning)
    role_to_re: dict[str, float] = {}
    for role, pairs in role_to_pairs.items():
        if len(pairs) > 1:
            labels_str = ", ".join(p[0] for p in pairs)
            warnings.append(
                f"multiple {role} components present ({labels_str}); "
                "taking max Re as representative for ordering check"
            )
        role_to_re[role] = max(p[1] for p in pairs)

    # 6. Build the expected subchain from the canonical chain
    present_roles = [r for r in _CHAIN if r in role_to_re]
    expected_chain = " > ".join(f"re_{r}" for r in present_roles)

    # 7. Trivial pass: 0 or 1 present center components
    if len(present_roles) < 2:
        return {
            "status": "pass",
            "expected_chain": expected_chain or "(no center components)",
            "components": components,
            "excluded": excluded,
            "violations": [],
            "swappable_overall": False,
            "custom_instructions_hint": "",
            "warnings": warnings,
        }

    # 8. Walk adjacent pairs in the subchain; strict ">" required
    violations: list[dict[str, Any]] = []
    for i in range(len(present_roles) - 1):
        left_role = present_roles[i]
        right_role = present_roles[i + 1]
        left_re = role_to_re[left_role]
        right_re = role_to_re[right_role]
        # Strict ">": left must be strictly greater than right (within epsilon).
        if left_re <= right_re + _EPS_ARCSEC:
            involves_bar_or_lens = (
                left_role in ("bar", "lens") or right_role in ("bar", "lens")
            )
            violations.append({
                "pair": [left_role, right_role],
                "left_role": left_role,
                "right_role": right_role,
                "left_re_arcsec": left_re,
                "right_re_arcsec": right_re,
                "direction": (
                    f"re_{left_role} ({left_re:.4g}\") <= re_{right_role} ({right_re:.4g}\")"
                ),
                "involves_bar_or_lens": involves_bar_or_lens,
                # A single violation is swappable only if it's exactly the
                # {disk, bulge} pair — the sole case where label swap is
                # physically valid.
                "swappable": (
                    not involves_bar_or_lens
                    and {left_role, right_role} == {"disk", "bulge"}
                ),
            })

    # 9. Aggregate status and overall-swap flag
    status = "fail" if violations else "pass"
    # swappable_overall: True iff the ONLY thing wrong is a {disk, bulge}
    # reversal — i.e. no Bar/Lens involved anywhere AND present roles are
    # exactly {disk, bulge}.
    present_set = set(present_roles)
    swappable_overall = (
        status == "fail"
        and present_set == {"disk", "bulge"}
        and all(v["swappable"] for v in violations)
    )

    # 10. Render custom_instructions_hint
    hint = _render_hint(violations, swappable_overall, expected_chain)

    return {
        "status": status,
        "expected_chain": expected_chain,
        "components": components,
        "excluded": excluded,
        "violations": violations,
        "swappable_overall": swappable_overall,
        "custom_instructions_hint": hint,
        "warnings": warnings,
    }


def _render_hint(
    violations: list[dict[str, Any]],
    swappable_overall: bool,
    expected_chain: str,
) -> str:
    """Render the custom_instructions_hint string for VLM injection."""
    if not violations:
        return ""
    lines = [
        f"[程序化 Re 全序校验] 期望链：{expected_chain}",
        "检测到反置：",
    ]
    for v in violations:
        lines.append(f"  - {v['direction']}")
    if swappable_overall:
        lines.append(
            "反置仅涉及 {Disk, Bulge}——可直接交换 disk ↔ bulge 标签后重拟"
            "（两者均为自由 Sersic，fitter 经常把两者搞混，交换标签是标准修法）。"
        )
    else:
        lines.append(
            "涉及 Bar 或 Lens 的反置严禁交换标签——两者带强物理先验"
            "（Bar: n=0.5 固定且 q<0.4；Lens: n<0.5 且 q>0.5），与其他成分不可互换。"
        )
        lines.append(
            "视为拟合失败，请基于当前残差与参数状态生成修复候选"
            "（如收紧过大成分的 Re 上限、重新分配通量、回退上一轮稳定结果重拟等）。"
        )
    return "\n".join(lines)


def _error_result(summary_file: str, lyric_file: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error_message": message,
        "summary_file": summary_file,
        "lyric_file": lyric_file,
        "expected_chain": "",
        "components": [],
        "excluded": [],
        "violations": [],
        "swappable_overall": False,
        "custom_instructions_hint": "",
        "warnings": [],
    }
