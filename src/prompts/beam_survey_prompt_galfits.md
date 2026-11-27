Please examine the following multi-band comparison image (per band stacked vertically: Original, Model with 2·Re ellipses + legend, 2D residual map, 1D surface-brightness profile) and carry out objective multimodal visual feature extraction.

**Global-state anchor (cross-round facts maintained by code; for cross-checking, never a substitute for reading the image)**:
{global_state_description}

**Unit contract (hard, multi-band)**: every length you read or write is a **pixel value of ONE named band's panel**, and you MUST name that band (`panel_band`) whenever you write a length. The transcription layer converts px to arcsec; you never see or use a pixel scale and never write arcsec. **PA is sky-PA**: read every PA against the compass (N arrow) of the panel you measured — 0° = celestial North, increasing counterclockwise toward East; write it verbatim. Never mix lengths read from different bands' panels; if a feature is only visible in one band, measure it there and tag THAT band.

## Phase 1: multimodal visual feature extraction (objective description only)

1. **Original panels (per band)**: describe the central galaxy's morphology and the components it implies (with strong feature evidence); note band-to-band differences in colour/contrast (a young disk bluer in g, a bulge redder in z) — they support component identification but never replace geometry. Report suspected unmasked companions ONLY at a local brightness maximum, with the band and coordinates.
   **Bar vs Bulge discrimination**: judge the centre on the panel where the centre is NOT saturated (usually the redder band's panel at high dynamic range). **Bulge** = innermost round/spheroidal concentration. **Bar** = obviously elongated, rectangular-like bright structure with a clear major axis across the centre — clearly non-circular. A FAT oval (q ≳ 0.5) is still a bar (an OVAL bar): seed q at the measured value; never force the 0.2–0.4 default. Whenever the centre is elongated, report its measured q and sky-PA.
2. **Model panels (any one band's legend carries the shared structural parameters)**: report the legend component by component (name, per-band Mag, Re in that panel's px, n, q, sky-PA). Structural parameters (Re/n/q/PA) are SHARED across bands — a disagreement between bands' legends is a rendering note, not two models. Check the Re total order `re_disk > re_bar > re_bulge` among existing central components; flag any inverted adjacent pair explicitly.
3. **2D residual maps (per band)**: symmetry/strength/distribution of positive-negative residuals; an extended centre-symmetric quadrupole = PA/q mismatch of an existing component; a compact fixed-position one-sided hot spot = possible point source (AGN question). For every suspected missing-component feature report its **inner/outer radii in px AND the band** — your add() candidates' `re_px` derives from these (Re ≈ feature semi-extent; a 1D bump peaks at ~2·Re of the missing component).
4. **2D residual outskirts**: isolated point sources, arcs, one-sided lopsidedness (report which band shows it best).
5. **1D curves (per band)**: sky line vs background; Data-vs-Model by radial range; a systematic positive residual spanning a wide radial range at r > 2·Re_disk = outer-flux deficit (record onset/span/amplitude and the band); a broad mid-radius bump at ~1.5–2.5·Re_bar (record band and radius); narrow deep spikes (≲5 px) = compact unfitted source.

Requirement: grounded in the image only; no speculation; every length carries its band.

<!-- phase:candidate_generation -->

## Supplement (code-maintained facts — AUTHORITATIVE numbers)

{summary_content}

**Cross-round digest**:
{global_state_description}

**Current-round state** (branch {branch_id}, parent {parent_label}, depth {depth}):
{local_state_description}

**Queue digest**:
{queue_digest}

**Orchestrator directives (binding when present)**:
{directives}

## Mech check table (authoritative, code-settled)

The "Mechanical physicality check table" in the current-round supplement is AUTHORITATIVE and settles the round's verdict BY ITSELF at record time — any `[hard]` entry makes the round mech-FAIL and bars it from s*. It covers: Re-chain inversion (arcsec, 3% tolerance), axis-ratio limits (bar q > 0.7 / < 0.15 hard; q < 0.05 thin-line; q > 1), Re degeneracy vs the PSF, concentricity (arcsec), μ0 ordering (reference band), flux existence floors (bulge < 3% / bar < 5% hard; bar outside 10–40% note) and bar_identity (compact-bar note). Its checks run on the arcsec canonical inventory; your numbers stay px+band. You do not return a verdict — but the mech table's repair menu (below) is binding for your candidate choices whenever a `[hard]` entry fired. Identity-inversion `swap_hint`s are derived by code; the required repair is a RECLASSIFY candidate, never a bare label swap.

## Phase 2: candidate generation (the JSON contract)

Output ONE fenced ```json object:

```json
{{
  "candidates": [
    {{
      "action_id": "",
      "primitives": [ <one or two mb primitives, see the menu> ],
      "physical_motivation": "<evidence-grounded; cite panel + band for every claimed feature>",
      "expected_C_prime": "{disk,bulge,bar}",
      "novelty_claim": "<what is new vs the state ledger>",
      "expected_behavior_tag": "add_bar|tune_bar_re|remove_bulge|...",
      "pred_bic": 912.4
    }}
  ]
}}
```

**Field notes**:
- `pred_bic` (float, unbounded): your estimate of the **realistically attainable BIC_eff after fitting this candidate's direction**, grounded in the digest's BIC facts (parent BIC, current best, dBIC history). The queue is ordered **ascending by pred_bic** (most promising = smallest) and only the ORDER matters — keep the relative order across your candidates honest; a near-certain worsening direction gets a clearly larger value.
- **Round-0 opening only**: write pred_bic as `-(K-1), ..., -1, 0` following your array order (most promising first) — the code ignores your values and assigns them mechanically from the array position; your ARRAY ORDER is the ranking.

**Primitive menu (multi-band)**:
- add: `{{"op": "add", "structure_name": "bulge|bar|disk|agn", "component_type": "sersic", "re_px": <float>, "q": <float>, "pa_sky_deg": <float>, "dx_px": <float>, "dy_px": <float>, "panel_band": "<band>"}}` — n is code-mandated (disk 1 fixed, bar 0.5 fixed, bulge FREE from creation with prior 4; you may add `"n": <float>` for bulge/lens-free slots). Positions are centre-relative (dx right, dy up) in the panel_band's pixels. AGN adds the N-block point source (no re/q/PA needed — dx/dy/panel_band suffice).
- remove: `{{"op": "remove", "target": "<name>"}}`
- tune: `{{"op": "tune", "target": "<name>", "params": {{"re_px": ..., "q": ..., "pa_sky_deg": ..., "n": ..., "dx_px": ..., "dy_px": ..., "panel_band": "<band>"}}}}` — panel_band is REQUIRED whenever any px-valued param is present; n is untunable for disk/bar (mandated).
- skeleton (round-0 opening ONLY): `{{"op": "skeleton", "components": [<add-shaped declarations for the WHOLE luminous model>]}}`.
- reclassify: `{{"op": "reclassify", "reclassify": {{"map": [{{"from": "<name>", "to": "<slot>"}}]}}}}` — same-slot bijection over {disk, bulge, bar, singlesersic}; light parameters are carried verbatim and the code applies type mandates.
  **Reclassify is the FIRST-choice repair for Re-chain inversions** (KILOGAS_296): when mech failures are limited to Re-chain ORDERING (clean visual onion, no crossing) and the round improved BIC vs its parent (or has no parent), propose reclassify — components keep their fitted light parameters, only the TYPE LABELS change (a compact "disk" is actually the bar; an extended "bar" is actually the disk). **A tune of individual Re values is the WRONG direction for an inversion**: the identities are swapped, not the parameters — tune slides along the degeneracy valley and the optimiser pulls the Re values back to the inverted state, wasting a budget round. Reclassify swaps the names in one atomic action and the Re chain is immediately correct (bar gets n=0.5 fixed, disk gets n=1 fixed — the type mandates). Bare label swaps remain forbidden — reclassify is the only legal identity-reassignment channel.

**Solution space (hard)**: disk / bulge / bar / agn / singlesersic, plus the m=1 Fourier carrier **sersic_f — ONLY on the main disk**. lens, outerdisk, edgedisk, companion and the other Fourier variants (sersic_rf/bf) are OUTSIDE — describe them as unfitted residual CONTEXT only, never as candidates. singlesersic cannot coexist with central components; a multi-component model REQUIRES a disk. The singlesersic→multi transition is implicit: add(disk/bulge/bar) on a singlesersic parent converts it (code renames + fixes n=1).
**F1 (m=1 lopsidedness) — proposable INSIDE the main loop**: when the residuals show a one-sided dipole / lopsided stretch (the galaxy heavier, brighter or more extended on one side; a one-sided large-scale bright patch), propose the disk type-switch as a TWO-primitive candidate: `[{{"op": "remove", "target": "disk"}}, {{"op": "add", "structure_name": "disk", "component_type": "sersic_f", ...same fitted disk parameters from the legend..., "f1": {{"amplitude": 0.05, "phase_deg": <sky-PA of the dipole's brighter side>}}}}]`. am seeds at ~0.05 (retained when fitted am > 0.02); theta_m is sky-PA mod 180. Do NOT attach sersic_f to bulge/bar, and never propose it without dipole evidence.

**Hard limits**: Re total order disk > bar > bulge (3% tolerance, pre-checked on your declared values); bar q prior < 0.4 (hard cap 0.7); bulge q ≥ 0.3; PA free (sky-PA); every px value must carry the band you measured it on.

**Candidate-count discipline**: 1–2 in the round-0 opening; 2–3 at depth 2; 2–4 deeper. When the mech check table carries `[hard]` failures, at least one candidate must repair them (the mech-repair menu: **flux floors / component collapse → REPAIR FIRST, remove only as last resort** (see the Collapse-Repair Priority Rule below); Re-chain inversion → RECLASSIFY the pair; bar_identity → reclassify(bar→bulge) alone, add(bar) larger-Re ONLY on residual evidence; μ0 inversion → mag reapportionment or identity reclassification).

**Collapse-Repair Priority Rule (hard; KILOGAS_110 retrospective)**: when a mech-hard entry fires on a component (bulge q < 0.3, bar q < 0.15, bulge/bar flux floor violations), the ROUND'S TOP repair candidate must be a **tune that REPAIRS the collapsed component** — never a bare remove. The collapse is usually a SEEDING problem (initial Re too small or too large relative to neighbours), not evidence the component does not exist; deleting it on the first failure forecloses the correct decomposition (KILOGAS_110: bulge seed Re=0.92" vs disk Re=6.0" collapsed to a needle; the repair chose remove(bulge)+add(bar); {disk,bulge,bar} was never fitted — the expert's answer).

  Repair directions by collapse signature (all values in px of your measured panel, panel_band required):
  - **Bulge collapsed to needle (q < 0.3) with no bar present**: tune the bulge **LARGER** — `tune(bulge, re_px = ≈ disk_Re/4 or larger (cross a factor-2 bin), q = 0.6–0.75, n = 1–4, panel_band = ...)`. A too-small seed makes the optimiser push it to zero flux and stretch it into a ridge; a physically-sized round core stabilises.
  - **Bulge collapsed to needle (q < 0.3) alongside a bar**: tune the bulge **SMALLER** — `tune(bulge, re_px = ≈ PSF FWHM (a compact round core), q = 0.65, n = 0.5–1 (pseudobulge), panel_band = ...)`. A large-Re bulge overlapping the bar region competes for the same light and the optimiser flattens it along the bar axis — the collapse mechanism; a tiny round bulge inside the bar does not compete.
  - **Bar collapsed to needle (q < 0.15) or flux floor**: tune the bar with a **rounder q and/or larger Re** — `tune(bar, q = 0.3–0.5, re_px = between bulge Re and disk Re (≤ 0.8 × disk_Re), panel_band = ...)`. If the disk also looks flat against the isophotes, the identity question (reclassify to bulge) is the competing candidate, not removal.
  - **Re and n are degenerate on a compact Sérsic** — a single-parameter tune (`tune(bulge, re_px)` alone) slides along the degeneracy valley; the repair candidate tunes **(Re, n) JOINTLY** as its two primitives (exactly the 2-primitive cap, one physical goal).
  - **REMOVE is the last resort** — propose `remove(component)` only after a repair tune has been tried and ALSO failed, or when the original image shows no evidence for the component's existence (not merely that the fit collapsed it).
  - **AGN replacement channel remains the competing candidate** when the collapse is to Re ≈ PSF scale with a central spike — `remove(bulge)+add(agn, ...)` competes with the repair tune; both may be proposed (2-primitive budget).

**Review checklist (hard requirements)**: (a) no candidate duplicates a `[Tried actions]`/`[State ledger]` line without a novelty_claim; (b) no candidate shares component+direction with a `[Refuted hypotheses]` entry without new evidence; (c) every physical_motivation cites panel+band evidence; (d) silent skipping of a FAIL-repair or a triggered rule is a violation — waive explicitly with a reason; (e) **when a mech-hard collapse entry fired, the top repair candidate MUST be a repair tune (Collapse-Repair Priority Rule) — a bare remove as the top repair without a prior failed repair tune or a no-evidence waiver is a violation**; (f) **when a Re-chain inversion (re_bar > re_disk, re_bulge > re_disk, etc.) is among the failed_checks, a reclassify candidate for the inverted pair MUST be included — proposing only tune(Re) candidates for an identity inversion without a reclassify is a violation** (KILOGAS_110 re-run: A.5's disk/bar inversion was met with tune only; the reclassify that swaps the pair was never generated).
