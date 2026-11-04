"""Candidate JSON schema + solution-space rules for the multi-band mode (P4a).

Mirrors beam/candidate_schema.py's architecture (pydantic shells -> rule
engine with E_* issue codes -> ValidationReport) but with the mb primitive
contract (docs/design_mb_beam.md §4):

* lengths arrive in **px tagged with panel_band** (the unit contract — the
  transcription layer converts via the band's pixscale / WCS affine);
* PA is **sky-PA** (``pa_sky_deg``, read against the panel compass);
* the solution space matches the single-band branch restriction:
  disk / bulge / bar / agn / singlesersic only — lens, outerdisk,
  edgedisk, companion and the Fourier variants (sersic_f/rf/bf) are
  OUTSIDE (the disk->sersic_f switch is the mb F1 form, disabled until
  its enqueue design lands);
* AGN is the N block (``structure_name: "agn"``, the psf avatar);
* no mag seeding channel exists (logNorm auto-initialises inside GalfitS).

The Candidate / Verdict / SurveyResponse shells are byte-compatible with
the galfit contract so survey.py's dispatch only swaps the parse+validate
pair.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError

# ------------------------------------------------------------------ shells

ALLOWED_SLOTS = {"disk", "bulge", "bar", "agn", "singlesersic"}
RESTRICTED_SLOTS = {"lens", "outerdisk", "edgedisk", "companion",
                    "sersic_f", "sersic_rf", "sersic_bf"}
CENTRAL_SLOTS = {"disk", "bulge", "bar"}
RE_RANK = {"bulge": 0, "bar": 1, "disk": 2}
RE_CHAIN_TOL = 0.03


class MbF1Params(BaseModel):
    """m=1 Fourier warm start on a sersic_f disk: am (0-0.5, typical seed
    0.05) and theta_m (sky-PA of the dipole's brighter side, mod 180)."""
    amplitude: float = Field(gt=0, le=0.5)
    phase_deg: float = Field(ge=-180, le=180)


class MbAddParams(BaseModel):
    structure_name: str
    component_type: Literal["sersic", "sersic_f"] = "sersic"
    re_px: float = Field(gt=0)
    q: float = Field(gt=0, le=1)
    pa_sky_deg: float = Field(ge=-180, le=180)
    dx_px: float = 0.0
    dy_px: float = 0.0
    panel_band: str
    n: Optional[float] = None
    f1: Optional[MbF1Params] = None


class MbSkeletonParams(BaseModel):
    structure_name: str
    component_type: Literal["sersic"] = "sersic"
    re_px: float = Field(gt=0)
    q: float = Field(gt=0, le=1)
    pa_sky_deg: float = Field(ge=-180, le=180)
    dx_px: float = 0.0
    dy_px: float = 0.0
    panel_band: str
    n: Optional[float] = None


class MbAddPrimitive(BaseModel):
    op: Literal["add"]
    structure_name: str
    component_type: Literal["sersic", "sersic_f"] = "sersic"
    re_px: float = Field(gt=0)
    q: float = Field(gt=0, le=1)
    pa_sky_deg: float = Field(ge=-180, le=180)
    dx_px: float = 0.0
    dy_px: float = 0.0
    panel_band: str
    n: Optional[float] = None
    f1: Optional[MbF1Params] = None


class MbRemovePrimitive(BaseModel):
    op: Literal["remove"]
    target: str


class MbTuneParams(BaseModel):
    re_px: Optional[float] = Field(default=None, gt=0)
    dx_px: Optional[float] = None
    dy_px: Optional[float] = None
    q: Optional[float] = Field(default=None, gt=0, le=1)
    pa_sky_deg: Optional[float] = Field(default=None, ge=-180, le=180)
    n: Optional[float] = None
    panel_band: str = ""


class MbTunePrimitive(BaseModel):
    op: Literal["tune"]
    target: str
    params: MbTuneParams


class MbSkeletonComponent(MbSkeletonParams):
    pass


class MbSkeletonPrimitive(BaseModel):
    op: Literal["skeleton"]
    components: list[MbSkeletonComponent] = Field(min_length=1, max_length=4)


class MbReclassifyMapEntry(BaseModel):
    model_config = {"populate_by_name": True}
    src: str = Field(alias="from")
    dst: str = Field(alias="to")


class MbReclassifyBody(BaseModel):
    map: list[MbReclassifyMapEntry] = Field(min_length=1)


class MbReclassifyPrimitive(BaseModel):
    op: Literal["reclassify"]
    reclassify: MbReclassifyBody


MbPrimitive = Union[MbAddPrimitive, MbRemovePrimitive, MbTunePrimitive,
                    MbSkeletonPrimitive, MbReclassifyPrimitive]


class MbQueueReorder(BaseModel):
    action_id: str
    rank: int = Field(ge=1, le=5)


class MbCandidate(BaseModel):
    action_id: str = ""
    primitives: list[MbPrimitive] = Field(min_length=1, max_length=2)
    physical_motivation: str
    expected_C_prime: str
    novelty_claim: str = ""
    expected_behavior_tag: str
    local_benefit_sigma: float = Field(ge=0.0, le=1.0)
    queue_reorder: Optional[list[MbQueueReorder]] = None

    def to_plain_primitives(self) -> list[dict]:
        out = []
        for p in self.primitives:
            d = p.model_dump(exclude_none=True)
            if p.op == "reclassify":
                d["reclassify"] = {
                    "map": [{"from": m.src, "to": m.dst}
                            for m in p.reclassify.map]}
            out.append(d)
        return out


class MbVerdict(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    failed_checks: list[str] = []
    swap_hint: Optional[str] = None


class MbSurveyResponse(BaseModel):
    physicality_verdict: MbVerdict
    candidates: list[MbCandidate] = Field(min_length=1, max_length=4)


def parse_survey_response_mb(payload: dict) -> tuple[MbSurveyResponse | None, str]:
    """Pydantic-level parse; returns (model, error-message)."""
    try:
        return MbSurveyResponse(**payload), ""
    except ValidationError as e:
        parts = []
        for err in e.errors()[:8]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg')}")
        return None, "; ".join(parts)


# ------------------------------------------------------------------ report

class Issue:
    def __init__(self, code: str, message: str, action_id: str = "",
                 severity: str = "error"):
        self.code, self.message = code, message
        self.action_id, self.severity = action_id, severity

    def __repr__(self):
        return f"[{self.code}] {self.message}"


class ValidationReport:
    """Interface-matched to beam.candidate_schema.ValidationReport so
    survey.py's dispatch needs no adaptation (errors / ok / error_feedback)."""

    def __init__(self):
        self.issues: list[Issue] = []

    def add(self, code: str, message: str, action_id: str = "",
            severity: str = "error") -> None:
        self.issues.append(Issue(code, message, action_id, severity))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_feedback(self) -> str:
        return "\n".join(f"- {i.code} ({i.action_id}): {i.message}"
                         for i in self.issues if i.severity == "error")


# ------------------------------------------------------------------ helpers

def _pixscale(bands_meta: dict, band: str) -> float | None:
    s = ((bands_meta or {}).get(band) or {}).get("pixscale")
    return float(s) if s else None


def _names(inv: list[dict]) -> set[str]:
    return {(c.get("name") or "").lower() for c in inv}


def project_mb_inventory(parent_inv: list[dict], primitives: list[dict],
                         bands_meta: dict) -> list[dict]:
    """Rough structural projection of the primitives onto the parent
    inventory (names/types/Re in arcsec) — the multiplicity / re-chain
    pre-check substrate. Re from px uses the declared panel_band scale."""
    inv = [dict(c) for c in parent_inv]

    def _implicit_convert():
        """The singlesersic -> multi transition: the FIRST add of a central
        component converts the singlesersic into the disk (rename; the
        transcriber applies the n=1-fixed mandate) — the projected regime
        must reflect it, or every such candidate is E_MULTIPLICITY-killed."""
        for c in inv:
            if (c.get("name") or "").lower() == "singlesersic":
                c["name"] = "disk"
                c["type"] = "sersic"
                return True
        return False

    for p in primitives:
        op = p.get("op")
        if op == "skeleton":
            inv = []
            for c in p.get("components", []):
                s = _pixscale(bands_meta, c.get("panel_band") or "")
                re = (c.get("re_px", 0) * s) if s else None
                inv.append({"name": c.get("structure_name"), "type": "sersic",
                            "re": re, "re_effective": re,
                            "q": c.get("q"), "pa": c.get("pa_sky_deg"),
                            "x": 0.0, "y": 0.0})
        elif op == "add":
            name = (p.get("structure_name") or "").lower()
            has_ss = any((c.get("name") or "").lower() == "singlesersic"
                         for c in inv)
            if name in CENTRAL_SLOTS and has_ss:
                _implicit_convert()
                if name == "disk":
                    # add(disk) on a singlesersic parent IS the conversion
                    # (the rename above) — no duplicate disk is appended
                    s = _pixscale(bands_meta, p.get("panel_band") or "")
                    if s and p.get("re_px") is not None:
                        for c in inv:
                            if c["name"] == "disk":
                                c["re"] = c["re_effective"] = \
                                    p["re_px"] * s
                                c["q"] = p.get("q", c.get("q"))
                                c["pa"] = p.get("pa_sky_deg", c.get("pa"))
                    continue
            s = _pixscale(bands_meta, p.get("panel_band") or "")
            re = (p.get("re_px", 0) * s) if s else None
            inv.append({"name": p.get("structure_name"), "type": "sersic",
                        "re": re, "re_effective": re,
                        "q": p.get("q"), "pa": p.get("pa_sky_deg"),
                        "x": 0.0, "y": 0.0})
        elif op == "remove":
            inv = [c for c in inv
                   if (c.get("name") or "").lower() != (p.get("target") or "").lower()]
        elif op == "tune":
            for c in inv:
                if (c.get("name") or "").lower() == (p.get("target") or "").lower():
                    params = p.get("params") or {}
                    if params.get("re_px") is not None:
                        s = _pixscale(bands_meta,
                                      params.get("panel_band") or "")
                        if s:
                            c["re"] = c["re_effective"] = \
                                params["re_px"] * s
                    if params.get("q") is not None:
                        c["q"] = params["q"]
                    if params.get("pa_sky_deg") is not None:
                        c["pa"] = params["pa_sky_deg"]
        elif op == "reclassify":
            for m in p.get("reclassify", {}).get("map", []):
                for c in inv:
                    if (c.get("name") or "").lower() == (m.get("from", "").lower()):
                        c["name"] = m.get("to")
    return inv


def _regime_errors(names: set[str], report: ValidationReport,
                   action_id: str) -> None:
    """The two-regime rule: {singlesersic (+agn +companions)} OR
    (disk mandatory among central components)."""
    central = names & CENTRAL_SLOTS
    if "singlesersic" in names and central:
        report.add("E_MULTIPLICITY",
                   "singlesersic cannot coexist with central components "
                   "(multi-component regime) — the first add(disk/bulge/bar) "
                   "converts it (code applies the rename + n=1-fixed mandate)",
                   action_id)
    if central and "disk" not in names:
        report.add("E_MULTIPLICITY",
                   "diskless multi-component inventory is invalid (the "
                   "decomposition regime is disk-bearing by definition)",
                   action_id)


# ------------------------------------------------------------------ rules

def validate_mb_candidate(cand: MbCandidate,
                          parent_inventory: list[dict],
                          bands_meta: dict,
                          is_root_opening: bool = False,
                          action_id: str = "") -> list[Issue]:
    issues: list[Issue] = []
    aid = action_id or cand.action_id
    prims = cand.to_plain_primitives()
    names_parent = _names(parent_inventory)

    def reject(code, msg):
        issues.append(Issue(code, msg, aid))

    names = set(names_parent)   # working set: prior primitives in the SAME
    #                            candidate take effect (remove(disk)+add(disk,
    #                            sersic_f) must not trip the slot check)
    for p in prims:
        op = p.get("op")
        if op == "skeleton":
            if not is_root_opening:
                reject("E_OPENING",
                       "skeleton primitives are legal only in the round-0 "
                       "opening survey (parent = the unfitted root)")
            skel = {(c.get("structure_name") or "").lower()
                    for c in p.get("components", [])}
            for nm in skel:
                if nm in RESTRICTED_SLOTS:
                    reject("E_RESTRICTED_SPACE",
                           f"skeleton component {nm!r} is outside the mb "
                           f"solution space {sorted(ALLOWED_SLOTS)}")
            _regime_errors(skel, _StubReport(issues, aid), aid)
        elif op == "add":
            nm = (p.get("structure_name") or "").lower()
            if nm in RESTRICTED_SLOTS:
                reject("E_RESTRICTED_SPACE",
                       f"add({nm}) is outside the mb solution space "
                       f"{sorted(ALLOWED_SLOTS)}")
            elif nm not in ALLOWED_SLOTS:
                reject("E_ALPHABET", f"unknown structure_name {nm!r}")
            if (p.get("component_type") or "sersic") == "sersic_f" \
                    and nm != "disk":
                reject("E_PARAM_TYPE",
                       "component_type sersic_f (the m=1 Fourier carrier) "
                       "is legal ONLY on the main disk")
            if nm in names:
                reject("E_MULTIPLICITY", f"add({nm}): slot already occupied")
            else:
                names.add(nm)
            if nm != "agn" and not ({"disk", "bulge", "bar"} & names_parent
                                    or nm == "disk") and \
                    "singlesersic" in names_parent:
                pass   # the singlesersic->disk conversion is implicit here
        elif op == "remove":
            nm = (p.get("target") or "").lower()
            if nm not in names:
                reject("E_TARGET", f"remove: {nm!r} not in the parent "
                                   f"inventory {sorted(names)}")
            else:
                names.discard(nm)
            # NO mid-op regime check: a pair like remove(disk)+add(disk,
            # sersic_f) transiently looks diskless — the FINAL projected
            # regime check (below) governs
        elif op == "tune":
            nm = (p.get("target") or "").lower()
            if nm not in names_parent:
                reject("E_TARGET", f"tune: {nm!r} not in the parent "
                                   f"inventory {sorted(names_parent)}")
            params = p.get("params") or {}
            has_px = params.get("re_px") is not None or \
                params.get("dx_px") is not None or \
                params.get("dy_px") is not None
            if has_px and not params.get("panel_band"):
                reject("E_PANEL_BAND",
                       "px-valued tune params require panel_band (the unit "
                       "contract: lengths are px of the measured panel)")
            if params.get("panel_band") and \
                    params["panel_band"] not in (bands_meta or {}):
                reject("E_PANEL_BAND",
                       f"panel_band {params['panel_band']!r} is not one of "
                       f"the assembled bands {sorted(bands_meta or {})}")
            if params.get("n") is not None and nm in ("disk", "bar"):
                reject("E_PARAM_MANDATE",
                       f"tune({nm}, n): {nm} n is type-mandated and cannot "
                       "be tuned")
        elif op == "reclassify":
            entries = p.get("reclassify", {}).get("map", [])
            for m in entries:
                src = (m.get("from") or "").lower()
                dst = (m.get("to") or "").lower()
                if src not in names_parent:
                    reject("E_TARGET", f"reclassify: source {src!r} not in "
                                       f"the parent inventory")
                if dst in RESTRICTED_SLOTS:
                    reject("E_RESTRICTED_SPACE",
                           f"reclassify -> {dst!r} is outside the solution "
                           f"space {sorted(ALLOWED_SLOTS)}")
                elif dst not in ALLOWED_SLOTS:
                    reject("E_ALPHABET", f"reclassify: unknown target {dst!r}")
            # bijection: no duplicate destinations
            dsts = [(m.get("to") or "").lower() for m in entries]
            if len(dsts) != len(set(dsts)):
                reject("E_RECLASSIFY", "duplicate reclassify destinations")

    # ---- add-level px contract
    for p in prims:
        if p.get("op") == "add":
            if p.get("panel_band") not in (bands_meta or {}):
                reject("E_PANEL_BAND",
                       f"add({p.get('structure_name')}): panel_band "
                       f"{p.get('panel_band')!r} missing or not assembled")

    # ---- projected regime + re-chain pre-check (arcsec)
    if all(i.severity == "reject" for i in []):  # placeholder no-op
        pass
    proj = project_mb_inventory(parent_inventory, prims, bands_meta)
    proj_names = _names(proj)
    _regime_errors(proj_names, _StubReport(issues, aid), aid)
    chain = [(c["name"], c["re"]) for c in proj
             if (c.get("name") or "").lower() in RE_RANK
             and isinstance(c.get("re"), (int, float))]
    chain.sort(key=lambda t: RE_RANK[t[0]])
    for (na, ra), (nb, rb) in zip(chain, chain[1:]):
        if ra is not None and rb is not None and \
                ra > rb * (1 + RE_CHAIN_TOL) + 1e-6:
            issues.append(Issue(
                "E_RE_CHAIN",
                f"projected re_{na}({ra:.3f}\") exceeds re_{nb}({rb:.3f}\") "
                f"by more than {RE_CHAIN_TOL:.0%} (required decrease outward "
                f"bulge < bar < disk)", aid))
    return issues


class _StubReport:
    """Adapter feeding _regime_errors into a plain issue list."""

    def __init__(self, issues: list[Issue], action_id: str):
        self.issues, self.aid = issues, action_id

    def add(self, code, message, action_id="", severity="error"):
        self.issues.append(Issue(code, message, action_id or self.aid,
                                 severity))


def validate_mb_survey(resp: MbSurveyResponse, graph,
                       state_label: str) -> ValidationReport:
    """Full mb survey validation: per-candidate solution-space checks +
    survey-level counts/tag-uniqueness (mirrors galfit's validate_survey)."""
    report = ValidationReport()
    state = graph.state(state_label)
    parent_inventory = state.get("inventory", [])
    bands_meta = (graph.g.graph.get("meta", {}) or {}).get("bands", {})
    is_root_opening = int(state.get("global_iter_id", 0)) == 0

    tags = []
    for cand in resp.candidates:
        aid = cand.action_id or f"cand-{len(tags) + 1}"
        issues = validate_mb_candidate(cand, parent_inventory, bands_meta,
                                       is_root_opening, aid)
        report.issues.extend(issues)
        tags.append(cand.expected_behavior_tag)
        if cand.queue_reorder:
            for qr in cand.queue_reorder:
                if qr.action_id == aid:
                    report.add("E_QUEUE_REORDER",
                               "a candidate cannot reorder itself", aid)
    dup_tags = {t for t in tags if tags.count(t) > 1}
    if dup_tags:
        report.add("E_TAG_DUPLICATE",
                   f"duplicate expected_behavior_tag values: {sorted(dup_tags)}")
    # depth-based candidate-count discipline (galfit contract: 1-2 opening,
    # 2-3 at depth 2, 2-4 deeper — enforced leniently via max bound only)
    depth = int(state.get("depth", 1))
    max_count = 2 if is_root_opening or depth <= 1 else 4
    if len(resp.candidates) > max_count:
        report.add("E_COUNT",
                   f"{len(resp.candidates)} candidates exceed the survey "
                   f"maximum {max_count}")
    return report
