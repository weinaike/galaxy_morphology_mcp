"""Candidate JSON schema (pydantic) + solution-space validation rule engine.

The surveyor returns ONE fenced JSON object per call:

    {"physicality_verdict": {"verdict": "PASS|FAIL", "failed_checks": [...],
                             "swap_hint": "none|disk_bulge_swap"},
     "candidates": [ {action_id?, primitives[1..2], physical_motivation,
                      expected_C_prime, novelty_claim, expected_behavior_tag,
                      local_benefit_sigma, queue_reorder?}, ... ]}

Validation runs the solution-space rules (closed alphabet, multiplicity,
Re-chain adjacency, SingleSersic conversion bundling, F1 target, psf param
exemptions, combo cap, ledger equivalence, temporary constraints) and returns
issues with stable error codes; ``severity="error"`` issues feed the bounded
retry loop, ``warn`` issues are recorded but do not trigger a retry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, ValidationError

# --------------------------------------------------------------------- schema

VALID_PARAMS = ("mag", "re_px", "n", "q", "pa_deg", "x_px", "y_px")


class ConsBounds(BaseModel):
    re: tuple[float, float] | None = None      # absolute 'to' form (effective radius)
    n: tuple[float, float] | None = None
    q: tuple[float, float] | None = None
    center_window: float | None = None         # +/- px relative window (x,y together)


class AddParams(BaseModel):
    structure_name: str
    component_type: Literal["sersic", "expdisk", "edgedisk", "psf"]
    mag: float | None = None
    re_px: float | None = None                 # ALWAYS the effective radius (unit contract)
    n: float | None = None
    q: float | None = None
    pa_deg: float | None = None
    x_px: float | None = None
    y_px: float | None = None
    toggles: dict[str, int] = {}
    cons_bounds: ConsBounds | None = None
    f1: dict | None = None                     # {"amplitude": .., "phase_deg": ..}


class TuneDelta(BaseModel):
    structure_name: str
    param: Literal["mag", "re_px", "n", "q", "pa_deg", "x_px", "y_px"]
    value: float | None = None
    toggle: int | None = None
    cons_bounds: ConsBounds | None = None


class AddPrimitive(BaseModel):
    op: Literal["add"]
    add: AddParams

    def to_plain(self) -> dict:
        d = self.add.model_dump(exclude_none=True)
        d["op"] = "add"
        return d


class RemovePrimitive(BaseModel):
    op: Literal["remove"]
    remove: str                                 # structure_name

    def to_plain(self) -> dict:
        return {"op": "remove", "target": self.remove}


class TunePrimitive(BaseModel):
    op: Literal["tune"]
    tune: TuneDelta

    def to_plain(self) -> dict:
        d = self.tune.model_dump(exclude_none=True)
        d["op"] = "tune"
        return d


class ConvertPrimitive(BaseModel):
    op: Literal["convert"]
    convert: dict                               # {"from_name","to_name","to_type"}

    def to_plain(self) -> dict:
        return {"op": "convert", "convert": dict(self.convert)}


Primitive = Annotated[
    Union[AddPrimitive, RemovePrimitive, TunePrimitive, ConvertPrimitive],
    Field(discriminator="op"),
]


class QueueReorder(BaseModel):
    action_id: str
    new_rank: int = Field(ge=1)


class Candidate(BaseModel):
    action_id: str = ""                         # code assigns when empty
    primitives: list[Primitive] = Field(min_length=1, max_length=2)
    physical_motivation: str
    expected_C_prime: str
    novelty_claim: str = ""
    expected_behavior_tag: str
    local_benefit_sigma: float = Field(ge=0.0, le=1.0)
    queue_reorder: list[QueueReorder] | None = None

    def to_plain_primitives(self) -> list[dict]:
        return [p.to_plain() for p in self.primitives]


class Verdict(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    failed_checks: list[str] = []
    swap_hint: str | None = None


class SurveyResponse(BaseModel):
    physicality_verdict: Verdict
    candidates: list[Candidate] = Field(min_length=1, max_length=4)


def parse_survey_response(payload: dict) -> tuple[SurveyResponse | None, str]:
    """Pydantic-level parse; returns (model, error-message)."""
    try:
        return SurveyResponse(**payload), ""
    except ValidationError as e:
        return None, _compact_pydantic_error(e)


def _compact_pydantic_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:8]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


# ----------------------------------------------------------------- rule engine

CLOSED_ALPHABET = {"disk", "edgedisk", "bulge", "bar", "lens", "outerdisk",
                   "agn", "companion", "singlesersic"}
NAME_TO_TYPES = {
    "disk": {"expdisk"},
    "edgedisk": {"edgedisk"},
    "bulge": {"sersic"},
    "bar": {"sersic"},
    "lens": {"sersic"},
    "outerdisk": {"expdisk", "sersic"},
    "agn": {"psf"},
    "singlesersic": {"sersic"},
}
CENTRAL_STRUCTURES = {"disk", "edgedisk", "bulge", "bar", "lens"}
SLOTS_MAX_ONE = {"disk", "edgedisk", "bulge", "bar", "lens", "outerdisk", "agn",
                 "singlesersic", "f1"}
_COMPANION_RE = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)
_F1_TARGETS = {"disk", "edgedisk", "singlesersic"}

DEPTH_COUNTS = {1: (1, 2), 2: (2, 3), 3: (2, 4)}  # depth >= 3 -> (2, 4)


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "error"          # "error" (retry) | "warn" (record only)
    action_id: str = ""
    field: str = ""


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_feedback(self) -> str:
        """Structured feedback block for the retry prompt."""
        return "\n".join(f"- {i.code} ({i.action_id or i.field}): {i.message}"
                         for i in self.issues if i.severity == "error")


def _slot(name: str) -> str:
    return "companion" if _COMPANION_RE.match(name or "") else (name or "?").lower()


def validate_candidate(cand: Candidate, parent_inventory: list[dict],
                       combo_counts: dict[str, int], per_combo_cap: int,
                       depth: int, refuted: list[dict],
                       temp_constraints: list[dict],
                       ledger_signatures: list[dict],
                       stage1_bar_pa: float | None = None) -> list[Issue]:
    """Validate one candidate against the solution space + graph state."""
    from beam.signature import (
        apply_primitives_to_inventory,
        canonical_signature,
        combo_identity,
        project_closed_form,
        signature_equivalent,
    )

    issues: list[Issue] = []
    aid = cand.action_id or "<unnamed>"
    prims = cand.to_plain_primitives()

    # ---- per-primitive checks
    n_central_adds = 0
    for p in prims:
        if p["op"] == "add":
            entry = p
            name = (entry.get("structure_name") or "").lower()
            ctype = (entry.get("component_type") or "").lower()
            if name == "sky" or name in {"mask", "sigma"}:
                issues.append(Issue("E_SKY_TOUCH",
                                    f"add target '{name}' touches a non-fittable block",
                                    action_id=aid))
                continue
            if _slot(name) not in CLOSED_ALPHABET:
                issues.append(Issue("E_ALPHABET",
                                    f"structure '{name}' outside the closed alphabet "
                                    f"{sorted(CLOSED_ALPHABET)}", action_id=aid))
                continue
            allowed = NAME_TO_TYPES.get(name, {"sersic", "psf"})
            if ctype not in allowed:
                issues.append(Issue("E_ALPHABET",
                                    f"'{name}' must use component_type in {sorted(allowed)}, "
                                    f"got '{ctype}'", action_id=aid))
            if name in CENTRAL_STRUCTURES:
                n_central_adds += 1
            # psf has no n/q/PA/Re; expdisk has no n
            if ctype == "psf" and any(entry.get(k) is not None for k in ("re_px", "n", "q", "pa_deg")):
                issues.append(Issue("E_PARAM_TYPE",
                                    "psf (AGN) components have no re/n/q/PA parameters; "
                                    "do not invent them", action_id=aid))
            if ctype == "expdisk" and entry.get("n") is not None:
                issues.append(Issue("E_PARAM_TYPE",
                                    "expdisk has no n parameter (n≡1 by type)", action_id=aid))
            # Re initial-value requirement (AGN exempt)
            if ctype != "psf" and entry.get("re_px") is None:
                issues.append(Issue("E_RE_CHAIN",
                                    f"add({name}) without re_px — an add() on a component "
                                    "with a physical Re MUST carry the effective radius "
                                    "initial value (px)",
                                    action_id=aid))
            if entry.get("f1") and _slot(name) not in _F1_TARGETS:
                issues.append(Issue("E_F1_TARGET",
                                    "F1 may only attach to disk/edgedisk/singlesersic",
                                    action_id=aid))
        elif p["op"] == "remove":
            target = (p.get("target") or "").lower()
            if target == "sky":
                issues.append(Issue("E_SKY_TOUCH", "remove(sky) is forbidden "
                                    "(the sky block is never a search dimension)",
                                    action_id=aid))
        elif p["op"] == "tune":
            t = p
            name = (t.get("structure_name") or "").lower()
            param = t.get("param")
            if name == "sky":
                issues.append(Issue("E_SKY_TOUCH",
                                    "tune on the sky block is forbidden", action_id=aid))
            parent_names = {c.get("name") for c in parent_inventory}
            if name not in parent_names and not _COMPANION_RE.match(name):
                issues.append(Issue("E_PARAM_TYPE",
                                    f"tune target '{name}' not in parent inventory "
                                    f"{sorted(n for n in parent_names if n)}",
                                    action_id=aid))
            if param == "n" and name in {"disk", "edgedisk"}:
                issues.append(Issue("E_PARAM_TYPE",
                                    "the Disk is expdisk: it has no n to release "
                                    "(n≡1 by component type)", action_id=aid))
            if t.get("value") is None and t.get("toggle") is None and not t.get("cons_bounds"):
                issues.append(Issue("E_SCHEMA",
                                    "tune carries neither value, toggle nor cons_bounds",
                                    action_id=aid))
        elif p["op"] == "convert":
            cv = p.get("convert") or {}
            frm = (cv.get("from_name") or "").lower()
            to = (cv.get("to_name") or "").lower()
            if frm != "singlesersic" or to != "disk":
                issues.append(Issue("E_SINGLE_CONVERSION",
                                    f"convert {frm}->{to} is not a legal conversion "
                                    "(only singlesersic->disk: expdisk, Rs=Re/1.68)",
                                    action_id=aid))

    # ---- hypothetical inventory (non-strict: multiplicity checks fire below)
    hypo, err = apply_primitives_to_inventory(parent_inventory, prims, strict=False)
    if hypo is None:
        issues.append(Issue("E_SCHEMA", f"primitives do not apply to the parent "
                                        f"inventory: {err}", action_id=aid))
        return issues

    names = [c.get("name") for c in hypo]

    # ---- multiplicity
    slots: dict[str, int] = {}
    for n in names:
        slots[_slot(n)] = slots.get(_slot(n), 0) + 1
    for slot, count in slots.items():
        if slot in SLOTS_MAX_ONE and count > 1:
            issues.append(Issue("E_MULTIPLICITY",
                                f"slot '{slot}' appears {count} times (max 1)",
                                action_id=aid))
    if "disk" in slots and "edgedisk" in slots:
        issues.append(Issue("E_MULTIPLICITY",
                            "disk and edgedisk are mutually exclusive", action_id=aid))

    # ---- singlesersic rules
    parent_names = [c.get("name") for c in parent_inventory]
    parent_is_single = parent_names == ["singlesersic"]
    if parent_is_single and n_central_adds > 0:
        has_convert = any(p["op"] == "convert" for p in prims)
        if not has_convert:
            issues.append(Issue("E_SINGLE_CONVERSION",
                                "adding a central component to a SingleSersic parent MUST "
                                "bundle convert(singlesersic->disk: expdisk, Rs=Re/1.68) "
                                "as the second primitive", action_id=aid))
    if "singlesersic" in slots and len(names) > 1:
        co_tenants = sorted(s for s in slots if s != "singlesersic")
        if co_tenants != ["agn"]:
            # {singlesersic, agn} is the legal elliptical+point-core terminal
            # state (KILOGAS_221 retrospective: the expert's singlesersic+AGN
            # answer was unreachable because the exclusivity barred the slot).
            issues.append(Issue("E_MULTIPLICITY",
                                "singlesersic may coexist only with an AGN (psf) point "
                                f"core — found alongside {co_tenants}; any "
                                "disk/bulge/bar/lens/outerdisk add must bundle "
                                "convert(singlesersic->disk: expdisk, Rs=Re/1.68)",
                                action_id=aid))
    if len(names) > 1 and "disk" in slots:
        disk_type = next((c.get("type") for c in hypo if c.get("name") == "disk"), "")
        if disk_type != "expdisk":
            issues.append(Issue("E_ALPHABET",
                                "a multi-component model's disk slot must be expdisk",
                                action_id=aid))

    # ---- Re chain adjacency (central components only; decrease outward
    # with a 3% relative tolerance)
    issues.extend(_check_re_chain(cand, prims, hypo, aid))

    # ---- AGN admission (bulge-Re-collapse rule)
    issues.extend(_check_agn_admission(cand, prims, parent_inventory, hypo, aid))

    # ---- embedded-companion timing (parent must have bulge or bar)
    issues.extend(_check_companion_timing(cand, prims, parent_inventory, hypo, aid))

    # ---- weak-PA anchor (new/re-tuned PA copying a near-round parent's PA)
    issues.extend(_check_pa_anchor(cand, prims, parent_inventory, aid,
                                   stage1_bar_pa=stage1_bar_pa))

    # ---- combo cap
    combo = combo_identity(hypo)
    if combo_counts.get(combo, 0) >= per_combo_cap:
        issues.append(Issue("E_COMBO_EXHAUSTED",
                            f"combination '{combo}' has exhausted its {per_combo_cap} "
                            "attempts — diversify toward a different inventory",
                            action_id=aid))

    # ---- ledger equivalence (structural R1 pre-check)
    hypo_sig = canonical_signature(hypo)
    for led_sig in ledger_signatures:
        if signature_equivalent(hypo_sig, led_sig, ignore_toggles=True, ignore_bands=True):
            if not cand.novelty_claim.strip():
                issues.append(Issue("E_R1_LEDGER",
                                    "landed state is band-equivalent to an executed input "
                                    "and novelty_claim is empty — state the untested "
                                    "parameter axis or change the inventory",
                                    action_id=aid))
            break

    # ---- R2: closed-form projection onto the result ledger
    proj = project_closed_form(parent_inventory, prims)
    if proj is not None:
        issues.append(Issue("E_R2_EXACT",
                            "closed-form transition (remove-only/revert) — its landing can "
                            "be projected exactly; the orchestrator handles it as a "
                            "zero-cost rollback, do not spend a candidate slot on it "
                            "unless the projection misses every ledger state",
                            severity="warn", action_id=aid))

    # ---- temporary constraints
    for tc in temp_constraints or []:
        if not tc.get("active", True):
            continue
        for struct in tc.get("forbid_structures", []):
            for p in prims:
                hit = None
                if p["op"] == "add":
                    hit = (p.get("structure_name") or "").lower()
                elif p["op"] == "tune":
                    hit = (p.get("structure_name") or "").lower()
                if hit and (_slot(hit) == _slot(struct) or
                            _COMPANION_RE.match(hit) and _COMPANION_RE.match(struct)):
                    issues.append(Issue("E_TEMP_CONSTRAINT",
                                        f"active temporary constraint ({tc.get('text', '')}) "
                                        f"forbids candidates touching '{struct}'",
                                        action_id=aid))
    # ---- refuted-hypothesis clause (same component + same parameter direction)
    for p in prims:
        if p["op"] != "tune":
            continue
        name = (p.get("structure_name") or "").lower()
        param = p.get("param")
        value = p.get("value")
        for ref in refuted or []:
            tag = str(ref.get("tag", ""))
            r_state = str(ref.get("state", ""))
            if not tag or not r_state:
                continue
            if _refute_matches(tag, name, param):
                if not cand.novelty_claim.strip():
                    issues.append(Issue("E_REFUTED",
                                        f"direction '{tag}' was refuted ({ref.get('evidence')}) "
                                        f"— reopen only with new evidence or a substantially "
                                        "different parameterised direction, stated in "
                                        "novelty_claim/physical_motivation",
                                        severity="warn", action_id=aid))
                break

    return issues


def _check_re_chain(cand: Candidate, prims: list[dict], hypo: list[dict],
                    aid: str) -> list[Issue]:
    """Re total order disk > lens > bar > bulge (outerdisk above disk).

    Only adjacent pairs touched by THIS candidate are checked — a candidate is
    not penalised for the parent state's pre-existing chain violations (those
    belong to the physicality verdict, not to candidate legality).
    """
    issues: list[Issue] = []
    rank = {"bulge": 0, "bar": 1, "lens": 2, "disk": 3, "edgedisk": 3, "outerdisk": 4}
    chain_set = CENTRAL_STRUCTURES | {"outerdisk"}  # OuterDisk sits above re_disk
    # relative tolerance (mirrors physicality.RE_CHAIN_TOL): the inner Re may
    # exceed the adjacent outer Re by at most 3% without firing
    re_tol = 0.03
    centrals = {c.get("name"): c for c in hypo if c.get("name") in chain_set}

    touched: set[str] = set()
    for p in prims:
        if p["op"] == "add" and (p.get("structure_name") or "").lower() in chain_set:
            touched.add((p.get("structure_name") or "").lower())
        elif p["op"] == "tune" and (p.get("structure_name") or "").lower() in chain_set:
            touched.add((p.get("structure_name") or "").lower())
        elif p["op"] == "remove" and (p.get("target") or "").lower() in chain_set:
            # removal makes its former rank-neighbours newly adjacent
            gone_rank = rank[(p.get("target") or "").lower()]
            touched.update(n for n in centrals if abs(rank[n] - gone_rank) <= 1)

    vals = []
    for n, c in centrals.items():
        re_eff = c.get("re_effective") or c.get("re")
        if re_eff is None:
            continue
        vals.append((rank[n], n, float(re_eff)))
    vals.sort(key=lambda t: t[0])
    for (_r1, n1, v1), (_r2, n2, v2) in zip(vals, vals[1:]):
        if n1 in touched or n2 in touched:
            if v1 > v2 * (1.0 + re_tol):
                issues.append(Issue("E_RE_CHAIN",
                                    f"Re total-order violation: re_{n1}={v1:g}px exceeds "
                                    f"re_{n2}={v2:g}px by more than {re_tol:.0%} "
                                    "(disk > lens > bar > bulge, 3% tolerance)",
                                    action_id=aid))

    # declared triplet adjacency for added components
    for p in prims:
        if p["op"] != "add":
            continue
        name = (p.get("structure_name") or "").lower()
        if name not in chain_set:
            continue
        cb = (p.get("cons_bounds") or {}).get("re")
        re_eff = p.get("re_px")
        if re_eff is None:
            continue
        inner = [(rank[n], n, float(centrals[n].get("re_effective") or 0))
                 for n in centrals if rank[n] < rank[name]
                 and centrals[n].get("re_effective") is not None]
        outer = [(rank[n], n, float(centrals[n].get("re_effective") or 0))
                 for n in centrals if rank[n] > rank[name]
                 and centrals[n].get("re_effective") is not None]
        r_in = max((v for _r, _n, v in inner), default=None)
        r_out = min((v for _r, _n, v in outer), default=None)
        if cb:
            lo, hi = float(cb[0]), float(cb[1])
            if r_in is not None and lo <= r_in:
                issues.append(Issue("E_RE_CHAIN",
                                    f"{name} Re_min={lo:g}px must exceed the adjacent inner "
                                    f"Re={r_in:g}px (total-order adjacency)", action_id=aid))
            if r_out is not None and hi >= r_out:
                issues.append(Issue("E_RE_CHAIN",
                                    f"{name} Re_max={hi:g}px must stay below the adjacent "
                                    f"outer Re={r_out:g}px (total-order adjacency)",
                                    action_id=aid))
        else:
            # same 3% tolerance: the adjacent inner Re may exceed re_init by
            # at most re_tol; re_init may exceed the adjacent outer Re by at
            # most re_tol
            if r_in is not None and r_in > re_eff * (1.0 + re_tol):
                issues.append(Issue("E_RE_CHAIN",
                                    f"{name} re_init={re_eff:g}px must exceed the adjacent "
                                    f"inner Re={r_in:g}px (within {re_tol:.0%})", action_id=aid))
            if r_out is not None and re_eff > r_out * (1.0 + re_tol):
                issues.append(Issue("E_RE_CHAIN",
                                    f"{name} re_init={re_eff:g}px must stay below the "
                                    f"adjacent outer Re={r_out:g}px (within {re_tol:.0%})",
                                    action_id=aid))
    return issues


def _check_agn_admission(cand: Candidate, prims: list[dict], parent_inventory: list[dict],
                         hypo: list[dict], aid: str) -> list[Issue]:
    issues: list[Issue] = []
    for p in prims:
        if p["op"] == "add" and (p.get("structure_name") or "").lower() == "agn":
            bulge = next((c for c in parent_inventory if c.get("name") == "bulge"), None)
            if bulge is None:
                issues.append(Issue("E_AGN_ADMISSION",
                                    "add(AGN) without a parent bulge: admission requires "
                                    "the Bulge-Re-collapse rule (Re<0.2px mandatory; "
                                    "0.2-0.5px competing variant) or clear central-spike "
                                    "residual evidence — justify in physical_motivation",
                                    severity="warn", action_id=aid))
            else:
                re_eff = bulge.get("re_effective") or bulge.get("re") or 0.0
                if re_eff > 0.5:
                    issues.append(Issue("E_AGN_ADMISSION",
                                        f"add(AGN) while the parent bulge Re={re_eff:g}px "
                                        "> 0.5px violates the admission rule "
                                        "(collapse <0.2px / border 0.2-0.5px)",
                                        action_id=aid))
    return issues


def _check_companion_timing(cand: Candidate, prims: list[dict], parent_inventory: list[dict],
                            hypo: list[dict], aid: str) -> list[Issue]:
    issues: list[Issue] = []
    has_central = any(c.get("name") in {"bulge", "bar"} for c in parent_inventory)
    disk = next((c for c in parent_inventory if c.get("name") in {"disk", "edgedisk"}), None)
    for p in prims:
        if p["op"] != "add":
            continue
        name = (p.get("structure_name") or "").lower()
        if not _COMPANION_RE.match(name):
            continue
        x, y = p.get("x_px"), p.get("y_px")
        if x is None or y is None or disk is None:
            if not has_central:
                issues.append(Issue("E_COMPANION_TIMING",
                                    "add(Companion) without coordinates and without a "
                                    "parent bulge/bar — embedded-companion degeneracy risk; "
                                    "build the central skeleton first",
                                    severity="warn", action_id=aid))
            continue
        re_eff = disk.get("re_effective") or 0.0
        r = ((x - (disk.get("x") or 0)) ** 2 + (y - (disk.get("y") or 0)) ** 2) ** 0.5
        if r < 2 * re_eff and not has_central:
            issues.append(Issue("E_COMPANION_TIMING",
                                f"companion at r={r:.1f}px < 2*Re_disk={2 * re_eff:.1f}px is "
                                "EMBEDDED but the parent has no bulge/bar — add(Bulge)/"
                                "add(Bar) first (hard timing rule)", action_id=aid))
    return issues


def _check_pa_anchor(cand: Candidate, prims: list[dict], parent_inventory: list[dict],
                     aid: str, stage1_bar_pa: float | None = None) -> list[Issue]:
    """A new/re-tuned elongated component's PA may not anchor on a near-round
    (q>0.9) parent component's fitted PA.

    Plate0436 retrospective: the parent disk (q=0.955) carried a meaningless
    converged PA=-38.5deg; the surveyor copied it as the added lens's PA, and
    the orthogonal-to-bar seeding drove the fit into a degenerate basin that
    mech-vetoed the whole lens direction — re-anchored on the measured bar
    direction (56deg) the SAME inventory improved BIC_eff by 58. A near-round
    component's PA is unidentifiable optimiser noise; anchors must be measured
    feature directions (bar axis / Stage-1 detected PA / isophote twist).
    PA has 180deg symmetry -> compare mod 180 within +-10deg.

    Trusted-direction exemption (limits false positives): a PA that ALSO sits
    within +-10deg of a well-determined direction — any non-round (q<=0.9)
    parent's PA, or the Stage-1 detected bar PA — passes: a legitimately
    measured direction that coincides with a trusted anchor is evidence of a
    real feature, not a copy of optimiser noise."""
    issues: list[Issue] = []
    round_parents = []
    trusted: list[float] = []
    for c in parent_inventory:
        # raw graph inventories key the axis ratio 'ba'; normalized ones 'q'
        q = c.get("q")
        if q is None:
            q = c.get("ba")
        try:
            if c.get("pa") is None or q is None:
                continue
            if float(q) > 0.9:
                round_parents.append(dict(c, q=q))
            else:
                trusted.append(float(c["pa"]))   # identifiable parent PA
        except (TypeError, ValueError):
            continue
    if not round_parents:
        return issues
    if stage1_bar_pa is not None:
        try:
            trusted.append(float(stage1_bar_pa))
        except (TypeError, ValueError):
            pass

    def _near_any(pa: float, anchors: list[float]) -> bool:
        for a in anchors:
            d = abs(pa - a) % 180.0
            if min(d, 180.0 - d) <= 10.0:
                return True
        return False

    for p in prims:
        target = pa_new = None
        if p["op"] == "add":
            target = (p.get("structure_name") or "").lower()
            pa_new = p.get("pa_deg")
        elif p["op"] == "tune" and p.get("param") == "pa_deg":
            target = (p.get("structure_name") or "").lower()
            pa_new = p.get("value")
        if pa_new is None:
            continue
        try:
            pa_val = float(pa_new)
        except (TypeError, ValueError):
            continue
        if _near_any(pa_val, trusted):
            continue  # anchored on a well-determined direction — legitimate
        for par in round_parents:
            if (par.get("name") or "").lower() == target:
                continue  # tuning the round component's own PA is not an anchor
            d = abs(pa_val - float(par["pa"])) % 180.0
            if min(d, 180.0 - d) <= 10.0:
                issues.append(Issue(
                    "E_WEAK_PA_ANCHOR",
                    f"{p['op']}({target}) pa_deg={pa_val:g} is within 10deg of "
                    f"parent {par['name']}'s PA={float(par['pa']):g} whose "
                    f"q={float(par['q']):g} > 0.9 — a near-round component's PA is "
                    "ill-determined optimiser noise, not a feature direction; anchor "
                    "the PA on a MEASURED feature direction instead (bar axis / "
                    "Stage-1 detected bar PA / isophote twist read from the panels)",
                    action_id=aid))
                break
    return issues


def _refute_matches(tag: str, name: str, param: str | None) -> bool:
    """Loose matcher between a refuted entry's tag and a tune direction."""
    t = tag.lower()
    if param and param.replace("_px", "") in t:
        return True
    if name and name in t and not param:
        return True
    return False


def _disambiguate_companion_names(resp: SurveyResponse,
                                  parent_inventory: list[dict]) -> None:
    """Companion slots are 0..N (solution-space multiplicity), but a second
    add naturally reuses the name 'companion' — which the strict
    duplicate-name check would discard (Plate0300 incident: the brighter
    companion candidate add_companion_114_93 was dropped with
    'E_APPLY: duplicate structure name: companion'). Auto-index colliding
    companion-family names to the next free sibling (companion2, companion3,
    ...). Mechanical disambiguation only — position/type/parameters stay
    verbatim (the VLM's physical intent is unchanged)."""
    taken = {str(c.get("name") or "").lower() for c in parent_inventory}
    for cand in resp.candidates:
        for prim in cand.primitives:
            if getattr(prim, "op", "") != "add":
                continue
            name = (prim.add.structure_name or "").lower()
            if not name:
                continue
            if _COMPANION_RE.match(name) and name in taken:
                base = _slot(name)              # canonical 'companion'
                i = 2
                while f"{base}{i}" in taken:
                    i += 1
                prim.add.structure_name = f"{base}{i}"
                taken.add(prim.add.structure_name)
            else:
                taken.add(name)  # two companion adds inside one candidate


def validate_survey(resp: SurveyResponse, graph, state_label: str) -> ValidationReport:
    """Full-survey validation: per-candidate solution-space checks + survey-level
    counts/tag-uniqueness/queue_reorder sanity."""
    issues: list[Issue] = []
    state = graph.state(state_label)
    parent_inventory = state.get("inventory", [])
    _disambiguate_companion_names(resp, parent_inventory)
    depth = int(state.get("depth", 1))
    combo_counts = graph.combo_counts()
    cap = int(graph.g.graph["meta"].get("per_combo_cap", 4))
    refuted = graph.g.graph.get("refuted_hypotheses", [])
    temp = graph.g.graph.get("temporary_constraints", [])
    ledger = graph.input_ledger_signatures()
    try:
        stage1_bar_pa = ((graph.g.graph.get("stage1", {})
                          .get("detect_bar_lopsidedness") or {})
                         .get("bar") or {}).get("pa_deg")
    except AttributeError:
        stage1_bar_pa = None

    for cand in resp.candidates:
        issues.extend(validate_candidate(
            cand, parent_inventory, combo_counts, cap, depth, refuted, temp, ledger,
            stage1_bar_pa=stage1_bar_pa))

    # survey-level: candidate counts per depth, tag uniqueness, queue_reorder sanity
    lo, hi = DEPTH_COUNTS.get(depth, (2, 4))
    if len(resp.candidates) > hi:
        issues.append(Issue("E_COUNT",
                            f"{len(resp.candidates)} candidates exceed the depth-{depth} "
                            f"maximum {hi}", severity="error"))
    if len(resp.candidates) < lo:
        issues.append(Issue("E_COUNT",
                            f"only {len(resp.candidates)} candidate(s) at depth-{depth} "
                            f"(expected {lo}-{hi}) — padding is forbidden but the count "
                            "should reflect genuine residual evidence",
                            severity="warn"))
    tags = [c.expected_behavior_tag for c in resp.candidates]
    if len(set(tags)) != len(tags):
        issues.append(Issue("E_TAG_DUP",
                            "expected_behavior_tag values must be pairwise distinct",
                            severity="error"))
    pending = graph.g.graph.get("pending", {})
    floor_flagged = {aid for aid, r in pending.items()
                     if r.get("status") == "pending" and r.get("code_flags")}
    for cand in resp.candidates:
        for qr in cand.queue_reorder or []:
            if qr.action_id not in pending or pending[qr.action_id].get("status") != "pending":
                issues.append(Issue("E_QUEUE_REORDER",
                                    f"queue_reorder references unknown/non-pending "
                                    f"action_id {qr.action_id}", action_id=cand.action_id))
            elif qr.action_id in floor_flagged:
                issues.append(Issue("E_QUEUE_REORDER",
                                    f"queue_reorder may not demote floor-protected "
                                    f"candidate {qr.action_id}", action_id=cand.action_id))
    return ValidationReport(issues)
