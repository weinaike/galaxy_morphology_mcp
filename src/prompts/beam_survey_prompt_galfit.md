Please examine the following image (original, model with 2·Re ellipses + legend, 2D residual map, 1D surface-brightness profile) and carry out objective multimodal visual feature extraction.

**Global-state anchor (cross-round facts maintained by code; for cross-checking, never a substitute for reading the image)**:
{global_state_description}

**Unit contract (hard)**: every Re/position is a **pixel value** in the same frame as the panels and the feedme rows — directly diffable, directly writable (values are transcribed verbatim; no conversion exists; arcsec forbidden). Re values you write are always **effective radii** (the orchestrator converts expdisk Rs=Re/1.68).

## Phase 1: multimodal visual feature extraction (objective description only)

1. **Original image** (both dynamic ranges): describe the central galaxy and the components its morphology implies (with strong feature evidence); list unmasked companion candidates with coordinates — a companion counts only at a **local brightness maximum** (contrast vs the ~5px neighbourhood); check the high-DR panel for embedded companions (on/inside the bulge/bar isophotes). Read mask polygons only on the original panel (black regions); never infer mask status from white areas on the residual panel.
2. **Model panel**: report the legend parameters component by component (name, Mag, Re(px), n, q, PA°). Check the Re total order `re_disk > re_lens > re_bar > re_bulge` among existing central components — flag any inverted adjacent pair explicitly (a strong degeneracy signal feeding your verdict). When a companion exists in the model, read `(x_model,y_model)` from the purple ellipse centre and `(x_real,y_real)` **only from the original panel** (an independent visible blob is required as the anchor); report Δr.
3. **2D residual map, central region**: symmetry/strength/distribution of positive-negative residuals; distinguish an **extended centre-symmetric quadrupole** (bar/bulge PA or axis-ratio mismatch) from a **compact fixed-position one-sided hot spot** (embedded companion signature — report its coordinates). For every suspected missing-component feature (bar-like/quadrupole, compact core, ring-like band, isolated blob) report its **inner and outer radii in px** and the panel you measured them on — the Re triplet of your add() candidates is derived from these (Re_init ≈ feature semi-extent; a 1D-residual bump peaks at ~2·Re of the missing component).
4. **2D residual map, outskirts**: isolated point sources, arcs, one-sided lopsided features.
5. **1D curve**: sky line vs sky-background dashed line; Data-vs-Model differences by radial range; **disk outer-flux check** (systematic positive residual Δμ ≲ −0.05 mag spanning >15 px at r > 2·Re_disk, before the background-limit red triangles) — record onset radius/span/amplitude; **mid-radius broad bump** at ~1.5–2.5·Re_bar (lens signature; check the companion-contamination pre-check: a compact source at that radius also makes a broad bump — discriminate by 2D morphology: local blob vs azimuthally continuous ring ≳180°); narrow deep spikes (span ≲5 px) = unfitted compact source, radius = its distance from centre.

Requirement: grounded in the image only; no speculation. Fill the "Current-round visual assessments" fields of the current-round supplement with your readings (outer_residual_sign, central_spike, two_peaked_original, embedded_hotspot).

<!-- phase:candidate_generation -->

Building on your visual extraction, act as the **surveyor**: first output the physicality verdict of the current fit, then 1–4 candidate actions as ONE JSON object, and optionally re-rank the pending queue.

Parameter summary (fitted values, px):
{summary_content}

**Global state**:
{global_state_description}

**Current-round supplement (code-generated objective facts)**:
{local_state_description}

**Pending queue digest**:
{queue_digest}

**Orchestrator directives (binding when present)**:
{directives}

**Call context**: branch={branch_id}, parent={parent_label}, depth={depth}.

## Phase 1.5: physicality verdict

The numeric half of the verdict is **computed by code** — the "Mechanical physicality check table" in the current-round supplement is AUTHORITATIVE. It covers: Re-chain inversion, axis-ratio hard limits, containment, concentricity, Re degeneracy, bound pins with provenance, **onion ellipse nesting** (area ordering Re²·q of adjacent chain pairs, always on; directional non-crossing — skipped in the flat-disk regime disk q<0.3/edgedisk only when corroborated by the Stage-1 edge-on classification or the outer-isophote anchor q_iso, and exempts a round bulge inside a flat bar/lens; an uncorroborated flat fitted disk fires disk_shape_inconsistency, note-level/advisory: q_iso is a coarse anchor and does not veto PASS), **shape priors** (bulge q ≥ 0.3 hard with a 0.3–0.4 note; lens q > 0.5 hard; bar q ≤ 0.7 hard with a 0.5–0.7 note; q_bar < q_lens when both exist), **profile priors** (lens n < 0.6 hard / < 0.5 note; outerdisk n < 1), **μ0 ordering** (analytic central surface brightness: μ0_agn < μ0_bulge < μ0_disk; μ0_bar ≤ μ0_disk/0.90) and **flux shares** (bar 10–40% note, lens 5–35% note, lens < bar hard). Bound pins (all bands are the default bound set — original provenance) are note-level facts, never vetoes. Do not reclassify, contradict, soften or omit those entries; the code merges them into the final verdict itself (a hard entry vetoes PASS regardless of what you return).

Your verdict covers the **visual half** — judge the main-galaxy central components (disk/bulge/bar/lens; companions and AGN exempt) on the Model/Residual panels:
1. **Nested onion (hard, visual)**: inner 2·Re ellipses fully inside the adjacent outer ones (inversion, major-axis tips poking out, or crossing = FAIL evidence — compare ellipses on the panel, not only legend Re numbers). Two layers: the inner ellipse's **area** must stay below the adjacent outer one's in every case; the directional containment judgement is skipped when the disk is edge-on (q < 0.3 or the edgedisk slot) AND the image corroborates it (Stage-1 edge-on/dust-lane classification, or outer isophotes comparably flat — a fitted q far flatter than the image's outer isophotes is itself a degeneration signature: disk_shape_inconsistency). A round bulge sitting laterally wider than a flat bar is the normal configuration, not a violation.
2. **Visual degeneracy (hard, visual)**: two components producing the same image feature (same ellipse, same position angle, no distinguishable boundary) that the numeric Re check cannot see.
3. Anything you cannot ground in the panels or the supplement stays out of failed_checks.
Soft visual signals (area ratios, shape-prior quirks, brighter inner layers) go in failed_checks with a `[note]` prefix and are NOT a FAIL. Borderline → lean PASS.

`swap_hint = disk_bulge_swap` only when verdict=FAIL **and** the only violation is the {disk,bulge} inversion (two free Sérsics swapped — the label swap is the fix). Inversions involving bar/lens are never swappable.

## Phase 2: candidate generation (your authority; code checks legality)

You have seen the residual image — **you rank what gets explored next**. The code side enforces mechanical legality (dedup, combo caps, floors) and never rewrites your candidates: an invalid candidate is discarded whole, so follow the contract exactly.

### Generation rules (distilled; the physics is yours)

- **Addition order**: Disk → (F1/outer companion if evidenced) → Bulge → Bar → Lens → Other; embedded companions only after Bulge/Bar exist. SingleSersic parents: adding a **central** component (disk/bulge/bar/lens/outerdisk) MUST bundle `convert(singlesersic→disk)`. **Sole exception**: `add(agn)` rides on a SingleSersic parent WITHOUT any conversion — `{singlesersic, agn}` is the legal elliptical-with-point-core terminal state; do not force a disk skeleton on a galaxy whose single-Sérsic residuals are already basically clean.
- **Depth staging**: depth≤2 builds the skeleton (bulge n=4 fixed is acceptable scaffolding); depth≥3 must include `tune(bulge, n free)` when a fixed-n bulge exists (σ calibration: cheap parameter releases can swing BIC by ~10³ — score them on residual evidence and information value, NOT on structural novelty; do not systematically rank them below add-component candidates), and new add(Bulge) must have n free.
- **add() completeness**: every add() on a component with a physical Re carries `re_px` (effective radius, the INITIAL value) grounded in your radial measurements AND respecting the total-order adjacency (re_init strictly between the adjacent inner and outer Re); plus n initial + free/fixed, q initial, PA initial (N=+Y: 0° = +Y up, counterclockwise, identical to the feedme 10) row — PA comes from your Phase-1 feature directions (transfer it to every elongated component, not only bars). **PA anchor rule (hard)**: NEVER anchor a new/re-tuned component's PA on another component's fitted PA when that component is near-round (q > 0.9 — flagged `[weak: ...]` in the digest): a near-round component's PA is ill-determined optimiser noise, not a feature direction (a lens seeded on such a value ran the fit into a degenerate basin; the same inventory re-anchored on the measured bar direction improved BIC_eff by 58). Anchors are measured directions only: the bar/elongated-feature axis, the Stage-1 detected bar PA, or an isophote twist read from the panels. AGN (psf) has no re/n/q/PA — never invent them.
- **No candidate-declared bounds**: candidates declare INITIAL VALUES only — never parameter bands (`cons_bounds` is ignored by the transcription layer). The constraint file carries only the concentric offset chain, the default bound set (Re floor→half-region, n 0.1–8, q 0.05–1.0) and companion position windows. Guide the fit with initial values; misbehaviour is caught post-hoc by the mechanical checks. Do NOT emit relaxation/band-tightening candidates — a parameter wanting to move past its initial value is the fitter's business.
- **Trigger rules** (full fact sets arrive in the supplement; when a trigger holds, the corresponding candidate is REQUIRED or an explicit waiver in physical_motivation — silent skipping is a violation):
  - **Disk outer-flux deficit** (positive 1D residual beyond 2·Re_disk) → `tune(disk, re_px = 1.3–1.5 × current)`, highest priority.
  - **Lens bump** (broad 1.5–2.5·Re_bar bump + azimuthally continuous 2D ring, companion leakage excluded) → `add(Lens, n≈0.3 free, q≈0.8, Re=bump_peak/2)`.
  - **Lens Re inflation** (Re-order inversion, or Re reaching the default cap) → the competing paths A (re-initialise lens Re ≈ 0.9×Re_above as a warm start) / B (grow disk Re) / C (remove lens).
  - **Flat-Bulge → Bar** (joint trigger values in the supplement) → `tune(Bulge→Bar, n=0.5 fixed)` or `add(Bar)+tune(bulge, q≈0.7)`.
  - **Central-spike / point-core** (digest field `central_spike`, or obvious on the 1D panel): a compact positive spike within <5 px that the current central components cannot absorb, with no AGN yet in the model → REQUIRED competing candidate `add(agn, psf, x_px/y_px = core peak, mag ≈ small-aperture estimate)` (no re/n/q/pa_deg fields). When the bulge is simultaneously flat (q<0.3) or collapsed (Re at the PSF-scale floor / <0.5 px), the mandated variant is the collapsed-bulge replacement `remove(bulge)+add(agn, psf)` (the psf border-zone competing variant) — a bare `remove(bulge)` while the central spike persists needs an explicit waiver in physical_motivation (bulge-compensation physics outranks plain removal: "if the Bulge cannot be retained, an AGN must be tried to compensate for it"). A healthy, non-collapsed bulge does NOT close the door: an obvious 0–5 px positive leftover the Bulge cannot absorb is solid AGN evidence; a free bulge n pinned at the 8 upper bound is corroborating point-source evidence of the same family (n → cusp mimicry). Adoption still obeys Occam (ΔBIC_eff > 10 or unambiguous 2D central cleanup).
  - **Mech-check repair menu** (the code-enforced families listed in Phase 1.5 fire in the mechanical table — or apply the same thresholds yourself when reading the legend; when one fires, the mapped repair candidate is REQUIRED or an explicit waiver goes in physical_motivation):
    - `bulge q < 0.3` → identity question: the flat "bulge" is likely a bar → `tune(bulge, n=0.5 fixed, q free)` (naming-swap equivalence makes it the bar identity) or `remove(bulge)+add(bar, ...)` grounded in the Phase-1 feature directions. **When the flat "bulge" is also compact (Re pinned at the PSF-scale floor, or <0.5 px) or a central <5 px spike persists, the competing collapsed-bulge replacement `remove(bulge)+add(agn, psf)` is REQUIRED alongside** (the mech "point-source identity question" note maps here). (0.3–0.4 is note-level only.)
    - `bulge n at the 8 cap` (a free bulge n pinned at the default upper bound) → identity question, three competing repairs by accompanying evidence: compact bulge + central <5 px spike / sharp core → `add(agn, psf)` + re-warm-start the bulge n (≈2–4, the pin is cusp-mimicry evidence); large-Re bulge + broad mid-radius 1D mismatch → the disk-role paths (`tune(disk, re_px = 1.3–1.5 × current)` / `tune(<component>, mag)` reapportionment — the high-n wing is grabbing envelope flux); genuinely concentrated giant elliptical with clean residuals → explicit waiver in physical_motivation (BCG/cD n>8 is legal; the default cap is a prior, not a shackle). **Raising the n cap or freezing n at 8 is NOT a repair.**
    - `bar q > 0.7` → round-bar degenerated into a lens/oval absorbing transition-zone flux → `remove(bar)+add(lens, n≈0.3 free, q>0.5)` or `tune(bar, q≈0.35)` letting the fit adjudicate.
    - `lens q ≤ 0.5` → too flat for a lens → `tune(lens, q≈0.7)`; if its PA/length are bar-like → `remove(lens)+add(bar, ...)`.
    - `q_bar ≥ q_lens` (both exist) → identity inversion → competing candidates: re-separate the shapes via q initial values, or `remove` the weaker component.
    - `onion crossing / onion area inversion` (directional crossing — skipped for disk q<0.3/edgedisk/round-bulge-in-flat-bar unless the flat disk contradicts the image anchor; area inversion Re²·q — always on, any disk q) → diagnose the pair: inner too big → re-initialise its Re smaller (warm start ≈0.9× the outer's extent along the crossing direction); outer (disk) too small → `tune(disk, re_px = 1.3–1.5 × current)`; shape misfit → re-identify via the rules below.
    - `disk shape inconsistency` (fitted disk q far flatter than the image's outer-isophote anchor q_iso, Stage-1 not edge-on) → disk-role collapse, usually flux usurpation by an inner component: re-round the disk toward q_iso with flux reapportionment (`tune(disk, q ≈ q_iso)` + `tune(<usurping component>, mag)`), or re-identify the roles (disk↔lens / disk↔outerdisk swap of mag/q/Re) grounded in the Phase-1 features.
    - `lens n ≥ 0.6` → not a lens profile → `remove(lens)` or re-identify (bulge/outerdisk).
    - `outerdisk n ≥ 1` → `tune(outerdisk, n≈0.5)` or `remove(outerdisk)`.
    - `μ0 inversion` (μ0_bulge ≥ μ0_disk, or μ0_agn ≥ μ0_bulge, or μ0_bar > μ0_disk/0.90 — computable from the legend Mag/Re/n) → flux misallocation: `tune(<component>, mag)` reapportionment; μ0_bulge ≥ μ0_disk together with an Re inversion → the disk_bulge_swap label swap; μ0_agn ≥ μ0_bulge → weak AGN → `remove(AGN)` Occam-validation candidate.
    - `flux shares` (bar 10–40%, lens 5–35% note-level; lens ≥ bar hard) → lens ≥ bar: remove the weaker or re-identify; bar < 10% with no residual signature at its radial zone → `remove(bar)` suggestion; bar > 40% → the disk-Re bottleneck path `tune(disk, larger Re)`.
  - **Companions**: psf vs sersic by area rule (R = A_blob/A_psf ≤1.5 → psf; ≥2.3 → sersic; elongated ≳1.3 → sersic); condition A (flux ≤1%) + no visible original-panel blob → remove(Companion); visible blob → keep. **Never relocate a fitted companion that coincides with a real original-image source** (Δr ≈ 0 on the anchor check) — moving it to a different blob abandons a real source; when ANOTHER unmasked blob also needs fitting, propose `add(companion2, ...)` instead (the code auto-indexes colliding companion names; companion slots are 0..N).
  - **AGN (psf)** is the sole central point source: admission only under the Bulge-Re-collapse rule (<0.2px mandatory, 0.2–0.5px competing variant) or clear central-spike evidence — **including with a healthy, non-collapsed bulge present**: an obvious positive-residual leftover within 0–5 px of the 1D profile that the Bulge cannot absorb is solid AGN evidence; do not stop at the bulge. **SingleSersic parents (elliptical regime — no bulge exists, the collapse rule cannot apply)**: the admission path is the central-spike branch — a basically clean single-Sérsic fit (outskirts reduced to noise, q > 0.5, no disk signatures) with a compact central positive residual / 1D-profile spike within <5 px → propose `add(agn, psf, x_px/y_px = core peak, mag ≈ small-aperture estimate)`, NO `re_px`/`n`/`q`/`pa_deg` fields, and NO `convert` primitive. {singlesersic, agn} is a terminal state worth testing whenever that central spike survives the single-Sérsic fit; adopt the AGN only if the residuals clearly improve (Occam: ΔBIC_eff > 10 or unambiguous 2D central cleanup).
  - **F1** only on disk/edgedisk/singlesersic; keep when amplitude > 0.02. Encoding: F1 cannot be
    expressed via `tune` — the only legal form is `remove(disk)` + `add(disk, expdisk, ...)` with the
    parent disk's values verbatim plus `"f1": {"am": <0.02-0.1>, "theta_m": <N=+Y deg>}`; the expdisk
    add must NOT carry an `n` field (n≡1 by type; an `n` triggers E_PARAM_TYPE).
- **Sky**: never a search dimension — candidates touching sky are invalid.
- **State-ledger usage**: compare every candidate's landed inventory against [State ledger] within the tolerance bands (Re ±20%, n ±0.5, q ±0.1, PA ±10°, position <8 px; naming swaps bulge n=0.5≡bar allowed); equivalent-to-ledger candidates need a novelty_claim naming the untested parameter axis. remove-only/revert candidates whose projection hits a ledger state or rollback edge are zero-cost rollbacks — do not propose them. Combos marked exhausted (4 attempts) are closed — diversify the inventory instead.
- **Queue re-ranking**: you may return queue_reorder for pending candidates based on what you just saw (only non-floor-flagged entries; floor flags protect mandatory hypotheses).

### JSON contract (return EXACTLY ONE fenced ```json object; no other JSON blocks)

```json
{
  "physicality_verdict": {"verdict": "PASS", "failed_checks": ["[note] ..."], "swap_hint": "none"},
  "candidates": [
    {"action_id": "",
     "primitives": [
       {"op": "tune", "tune": {"structure_name": "bulge", "param": "n", "value": 4.0, "toggle": 1}},
       {"op": "add", "add": {"structure_name": "bar", "component_type": "sersic",
                              "mag": 17.5, "re_px": 6.0, "n": 0.5, "q": 0.35, "pa_deg": 45.0,
                              "x_px": 148.0, "y_px": 148.0,
                              "toggles": {"n": 0},
                              "f1": null}},
       {"op": "remove", "remove": "lens"},
       {"op": "convert", "convert": {"from_name": "singlesersic", "to_name": "disk", "to_type": "expdisk"}}
     ],
     "physical_motivation": "cite concrete Phase-1 features (position/strength/symmetry) + supplement facts",
     "expected_C_prime": "{disk,bulge,bar}",
     "novelty_claim": "new structure {disk,bulge,bar} | ≡A.3 but n free axis untested | projection compared, no ledger hit",
     "expected_behavior_tag": "bar_add_quadrupole",
     "local_benefit_sigma": 0.6,
     "queue_reorder": [{"action_id": "A.2-c1", "new_rank": 1}]
    }
  ]
}
```

Field rules:
- `primitives`: 1–2 entries, semantically cohesive (one physical goal); the four op shapes are exactly as above (tune may carry value and/or toggle; param ∈ mag/re_px/n/q/pa_deg/x_px/y_px; NO cons_bounds field — initial values only, bands are ignored).
- `local_benefit_sigma` ∈ [0,1]: fraction of the current reduced χ² you expect this action to deliver.
- `expected_behavior_tag`: short snake_case, pairwise distinct across candidates.
- `queue_reorder`: optional, only top-level once; omit or null when not re-ranking.
- On verdict=FAIL at least one candidate must repair failed_checks (swap_hint=disk_bulge_swap → include the label-swap candidate).
- Candidate counts by depth: 1 → 1–2; 2 → 2–3; ≥3 → 2–4. No padding.

### Validation feedback loop

Your output is parsed and validated in code. **Errors** return this block to you for correction (keep the same overall judgement, fix the flagged fields):

- E_SCHEMA — malformed JSON / missing required fields / primitives not 1–2.
- E_ALPHABET — structure outside {disk,edgedisk,bulge,bar,lens,outerdisk,agn,companion} or wrong component_type for the name.
- E_MULTIPLICITY — a >1 slot (disk/edgedisk/bulge/bar/lens/outerdisk/agn), disk+edgedisk, or singlesersic alongside anything but AGN.
- E_SINGLE_CONVERSION — central add on a SingleSersic parent without the bundled convert.
- E_RE_CHAIN — Re triplet/init violates total-order adjacency (message carries the numbers).
- E_COMBO_EXHAUSTED / E_R1_LEDGER / E_R2_EXACT — the landed state is closed or already known (diversify).
- E_SKY_TOUCH / E_F1_TARGET / E_PARAM_TYPE — illegal parameter/target.
- E_AGN_ADMISSION / E_COMPANION_TIMING — admission or timing rule violation.
- E_WEAK_PA_ANCHOR — the PA copies a near-round (q>0.9) parent's fitted PA (ill-determined noise); re-anchor on a measured feature direction (bar axis / Stage-1 bar PA / isophote twist).
- E_QUEUE_REORDER — unknown/non-pending action_id or floor-flagged entry.
- E_TEMP_CONSTRAINT — active temporary constraint forbids the target.
- E_TAG_DUP / E_COUNT — tag collision or depth-count violation.

Fix and return the corrected JSON only.
