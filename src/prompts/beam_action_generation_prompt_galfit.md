Please examine the following image (containing the original image, the model image, the 2D residual map and the 1D surface-brightness profile) and carry out objective multimodal visual feature extraction.

**Global-state anchor (cross-round established facts, for cross-checking; must not replace image reading, and must not be copied into your description as if it were an observation)**:
{global_state_description}

**Unit contract**: every Re/position in the global-state anchor is a **pixel value** (px), in **the same reference frame** as this round's image panels and the feedme parameter file — direct diffing is possible (e.g. a basin entry px(95,128) vs your reading this round of px(111,126), Δ=16px at a glance), and the values can be used directly as candidate values (the orchestrator writes the px values **verbatim** into the feedme; no unit conversion exists anywhere). If arcsec values appear in the anchor (legacy format), **ignore their numerical value and keep only their semantics**.

How to use it: the position/parameter readings you measured in previous and earlier rounds, the verified parameter basins, and the refuted hypotheses are all recorded here. When you read a new reading from the image this round that conflicts with the global state (especially **absolute pixel coordinates**, which naturally carry ±10px noise), you must first cross-check via a relative judgement (e.g. "does the residual hot spot fall inside the model component's 2·Re ellipse?", "is this round's Δr inconsistent with the verified basin recorded globally?"); only report the new reading when the image evidence clearly outweighs the global record, and note the disagreement with the global record in your description.

**Phase 1: multimodal visual feature extraction (objective description only)**
1. Features of the original image at high/low dynamic range
    - Concretely describe the X- and Y-axis ranges of the original image, the axis units, and what the title says
    - Describe the central galaxy in the different dynamic ranges, and infer the components most likely present (support them with strong feature evidence)
    - Describe the unmasked companion regions (obviously independent point sources or extended sources, i.e. white bright areas; black = masked), giving concrete coordinates. Note that companions occur not only in the outskirts but also right against the central bright region of the main galaxy (embedded companions, on or inside the bulge/bar isophotal contours) — inspect the high-dynamic-range original image carefully to find embedded companions.
    - **Local-maximum criterion for companion candidates**: before reporting a coordinate, confirm the position is a **local brightness maximum** (describe its contrast against the surrounding ~5px neighbourhood, e.g. "the bright spot clearly stands above the neighbourhood background"). Gradients on an extended halo and asymmetric bulges of isophotes are not companions — they have no discrete peak, and any coordinate read off them is noise.
    - **Mask reading rules (against misjudging "already masked")**: read mask polygons only on the **original-image panel** (black regions). **It is forbidden** to infer "some source is masked" from white regions on the residual panel — masked areas on the residual panel are also rendered white, indistinguishable from unmasked positive-residual hot spots. If you cannot decide whether a blob falls inside a mask polygon, report "mask status uncertain"; do not use that to exclude the source or ignore the coordinate.
    - **Reliability warning for embedded-companion positions**: **before** the central components (Bulge/Bar) are established, the residuals are contaminated by unfitted central flux; positions of embedded companions read off the residual map or the high-dynamic-range original image in that regime are **unreliable** (bulge-residual artefacts are easily misread as companion positions). Only positions read after the central components are established count as strong evidence.
2. 2D original vs model features: assess whether the overall skeletons agree, and where they differ:
    - **Model-panel component contours and parameter legend (must read)**: each component's 2·Re ellipse on the Model panel is coloured by component identity (disk=blue, bulge=green, bar=orange, lens=red, companion=purple, AGN/nucleus=brown; colours match the 1D-curve legend), and the legend in the panel's upper-left lists each component's fitted `name Mag Re(px) n q PA°` on one line. **Re is the true effective radius in pixels** (expdisk already converted via Re=1.68·Rs, in the form `Re=35.0px`), directly comparable with this panel's axes, the ellipse sizes, Δr and other pixel measurements — no conversion needed; these px values are **in the same unit and reference frame** as the Re values you put into candidates, and the orchestrator writes them verbatim into the feedme. **PA follows this workflow's N=+Y contract** (see the 🔑 PA Convention): the image's +Y axis (up) is 0°, increasing counterclockwise — the same convention as the feedme `10)` parameter row; when generating PA-bearing candidates you can take the legend value directly, with no conversion. Do not guess contour attribution by the "small inside large" size heuristic any more — read it directly by colour and legend. You must report the legend parameters component by component in the feature description, and check the Re total order `Re_disk > Re_lens > Re_bar > Re_bulge` (compare only the components that actually exist): if the legend values show an inverted adjacent pair (e.g. re_bar ≤ re_bulge), you must explicitly flag "Re total-order inversion" and list the inverted pair — a strong degeneracy signal.
    - **Companion position verification (execute only when the parameter summary already contains a Companion)**: read the companion's pixel centre from both the original image and the Model panel — `(x_real, y_real)` and `(x_model, y_model)` — read `x_model/y_model` directly from the centre of the purple (companion-coloured) 2·Re ellipse, do not guess; **`x_real/y_real` may only be read from the Original panel**, and the original image must show a discernible independent bright blob as the anchor — it is **forbidden** to take the model panel's purple-ellipse centre, or the residual hot spot's position, as x_real. Report the offsets `Δx = x_model − x_real`, `Δy = y_model − y_real` and `Δr = √(Δx² + Δy²)`. If `Δr > 2 px`, treat it as a significant offset. If **the model companion's position shows no corresponding source in the original image**, you must explicitly flag "model companion has no original-image counterpart" in the report — the highest-priority signature of a fake basin / position drift; in that case generate the position correction from the coordinates of the actually visible blob (or the residual hot spot), and however small Δr is you must not judge "position consistent" (a fake basin can have tiny Δr — the component was initialised there, and a reading taken from the model ellipse is necessarily "consistent"). **But before generating a position-correction candidate, first check whether the parent state has established central components (Bulge or Bar)**:
        - Parent state **already has** Bulge/Bar → generate the `tune(companion, x_real, y_real)` position-correction candidate (write pixel coordinates directly; the orchestrator fills them verbatim into the feedme).
        - Parent state **has no** Bulge/Bar and the companion shows bound-hitting parameters (Re/xcen/ycen at bounds) → this is the degeneracy signature of "the companion is being borrowed to compensate central flux"; a **position correction treats the symptom, not the cause**. Generate `add(Bulge)` or `add(Bar)` candidates to build the central skeleton first; correct the companion position after the centre stabilises.
        - If `Δr ≤ 2 px`, the position is consistent: no position-correction candidate is needed (morphological-correction candidates may still be given based on the residuals).
    - **Companion coordinate table (structured output, hard requirement)**: whenever you report the position of any companion (existing or candidate), append one row of the coordinate table, each column **read independently then filled in** — no blanks, no copying between columns:
      ```
      | id | x_orig,y_orig | visible in original (Y/N) | inside mask (Y/N/uncertain) | x_res,y_res (hot spot) | 1D spike radius (px) | x_model,y_model |
      ```
      A companion with `visible in original=N` must never be judged "position consistent" however small Δr is; the `inside mask` column is read on the Original panel per the mask-reading rules above; the `1D spike radius` is the radius of the narrow 1D-residual spike (write "none" if absent; see Phase 1 "companion radius-spike cross-check"). When columns contradict each other (e.g. x_orig vs the x_res hot spot differ by > 5 px, or the spike radius differs from the companion radius by > 5 px), you must **point out the contradiction** in the description and give your adjudication; do not average things away or silently drop a column.
3. 2D residual map — central region (within the central galaxy's extended area):
    - Describe the symmetry, strength and spatial distribution of positive/negative residuals in the central region (and infer whether features of not-yet-added components are present)
    - **Embedded-companion check (important)**: does the central region contain a fixed-position, compact (about PSF scale to a few pixels), one-sided-offset local red positive-residual hot spot? It differs from the "extended, centre-symmetric quadrupole" pattern produced by bar/bulge PA misalignment. If such a hot spot exists and the corresponding position in the original image shows a secondary bright peak (a two-peaked structure), treat it as an embedded-companion residual signature and report its pixel coordinates accurately.
    - Describe the spatial distribution of the residuals in the extended region (e.g. concentric rings, concentric arcs, bands, random distribution)
    - Describe whether independent companion residual features exist in the extended region (isolated, non-diffuse local bright blobs); for companions there, describe their central position coordinates accurately
    - Describe whether lopsidedness residual features exist in the extended region (typically positive residuals on one side and negative on the other; companions also easily induce asymmetric lopsided residuals — distinguish carefully)
    - **Radial measurement of new-component features (mandatory; feeds the Phase-2 Re triplet)**: for every suspected new-component feature identified in the central or extended region (bar-like/quadrupole, compact core, ring-like bright band, isolated bright blob), besides the qualitative description you **must** report its **inner and outer radii** (px, noting which panel the measurement came from) — the `[Re_min, Re_init, Re_max]` of Phase-2 `add(...)` candidates is derived directly from these measurements (see the 🔑 Re Convention's residual-geometry conversion and the Bar special clause); without this measurement the candidate cannot carry a valid Re triplet and is invalid.
4. 2D residual map — outskirts (beyond 20 px from the central galaxy's extended area):
    - Describe whether the outskirts contain companions or isolated point sources (outskirts companions affect the central fit little and may be left unfitted; but if an embedded companion has been identified, the outskirts may be ignored for now to concentrate on the central structure)
5. 1D brightness curve and residuals
    - Describe the chart's axes, annotations, titles and other content
    - If the sky component exists, describe the relation between the sky-component magnitude line and the sky-background dashed line (flush, high or low)
    - Describe the regions of obvious Data-vs-Model difference (e.g. centre too bright or too dim, systematic over- or under-brightness in some radial range)
    - Describe the magnitude differences between components and the correspondence between the residual curve and each component's Re (e.g. whether the residual peak position matches some component's Re)
    - **Disk outer-flux-deficit check (takes priority over central-component residual analysis)**: first confirm the disk skeleton is correct — check whether the 1D residual curve (Δμ = Data − Model) is **systematically positive** (Data brighter than Model, Δμ < 0) in the outskirts at r > 2×Re_disk. If a broad systematic positive residual exists there (not noise fluctuation; span > 15 px, amplitude Δμ ≲ −0.05 mag), the disk Re is too small and the outer flux is not covered — a signature of a wrong disk skeleton. **Residual analysis of the central components (lens bump, bulge parameters, etc.) may then rest on a wrong disk baseline** — if the disk Re is too small, the disk "gives away" outer flux that belongs to itself (or forces lens/bar to inflate in compensation), so the central components seem to need adjustment when in fact that is just a knock-on effect of the wrong disk skeleton. Therefore correct the disk Re **first**, then look at the central-component residuals. When this outer-flux-deficit feature is found, state clearly in the feature description the onset radius, span and amplitude of the positive residual, to feed the Phase-2 `tune(disk, larger Re)` candidate (see the Disk Outer-Flux-Deficit Trigger Rule). Note: if the outer data points have turned into red triangles (reaching the background-noise limit, low SNR), be cautious — the positive residual counts only if it appears systematically **before** entering the background limit.
    - **Companion radius-spike cross-check (objective evidence independent of 2D reading accuracy)**: a **narrow, deep positive spike** on the 1D residual curve (Δμ = Data − Model; span ≲ 5 px, amplitude Δμ ≲ −0.3 mag) is the azimuthally averaged signature of an unfitted compact source, and its radius should equal that source's distance from the galaxy centre — objective numerical evidence that does not rely on 2D reading precision. Whenever the model already contains a companion (or a companion candidate has been reported), you must cross-check: does the 1D spike radius (if any) match the companion's central radius (converted to pixels from the summary's xcen/ycen) (a mismatch > 5 px fails)? A mismatch = the companion is not anchored on a real source; report in the feature description "companion radius mismatch: spike at r≈XX px, companion at r≈YY px", and fill the `1D spike radius` column of the coordinate table accordingly.
    - **Pre-check for companion contamination of the lens bump (run before the lens-bump diagnosis)**: the 1D curve is an azimuthal average — a compact source at radius r (companion, bright knot) also produces a broad positive bump at that radius after azimuthal averaging, indistinguishable from a Lens signature in 1D. When a mid-radius bump is found you must check: does the bump's radial interval cover any companion's central radius (converted from the summary's xcen/ycen) or the radius of a companion candidate visible in the original image? If it does, report "bump co-located with companion radius" in the feature description, and distinguish the two morphologies on the 2D residual map: a **local compact bright blob** (azimuthal coverage ≲90°, companion-leakage signature) vs an **azimuthally continuous ring-like positive residual** (coverage ≳180°, Lens signature). Also report the companion's numerical state (Re at the lower bound / axrat or xcen bound-hit / Mag far fainter than the disk — all signatures of a source not anchored on a real one).
    - **Lens-bump diagnosis (execute when the parent state already has a Bar or Bulge)**: check whether the 1D residual curve (Δμ = Data − Model) shows a **broad positive bump** about 1.5–2.5·Re_bar from the centre (about 2–4·Re_bulge when there is no Bar). This bump is the radial flux signature of a Lens (a low-n extended component), unlike spiral-arm residuals — spiral arms appear as spiral bands on the 2D residual map and are suppressed in 1D amplitude by azimuthal averaging, whereas a lens bump is near-circularly symmetric in 2D and significant in 1D. When such a broad bump is found, state clearly in the feature description its position, width and amplitude, to feed the Phase-2 `add(Lens)` candidate (see the Lens 1D Profile Bump Trigger Rule). **Precondition**: if the Disk outer-flux-deficit check hit, the lens-bump reading may be contaminated by a too-small disk Re — state the relative contributions of both in physical_motivation; if the pre-check reported "bump co-located with companion radius", the lens-bump conclusion must carry the 2D morphology discrimination (local blob vs ring).
    - **Quantitative measurement of 1D residual bumps (mandatory; feeds the Phase-2 Re triplet)**: for every significant bump or systematic offset on the 1D residual curve, besides the qualitative description you **must** report its **peak radius, span and amplitude** (px / mag) — Re_init ≈ bump peak radius / 2 for a missing component (see the 🔑 Re Convention's residual-geometry conversion); the Re triplets of Lens / Bar / Companion candidates are derived directly from it.

Requirement: every description must be grounded in the image content; no subjective speculation.

<!-- phase:candidate_generation -->

Building on your visual feature analysis just now, together with the fitted-parameter summary below, act as the Beam Search candidate generator: first output the **physicality verdict** of the current fit (Phase 1.5), then output **2–4** mutually differentiated candidate composite actions.

Parameter summary content:
{summary_content}

**Global state (cross-round stable facts, hard constraint — usage rules in §Global-State Usage Rules)**:
{global_state_description}

**Current-round supplement (the orchestrator's objective description of the current fit: bound-hit parameters, numerical anomalies, Stage-1 conclusions, numeric-rule delegations, etc.)**:
{local_state_description}

**Phase 2: candidate action generation (Beam Search mode)**

## Role and goal
You are now the **candidate generator** in a Beam Search. The orchestrator agent calls you after every fit; based on the current residuals and the history, you propose several "next-step candidate composite actions", and the orchestrator deduplicates, scores and enqueues them.

Unlike the "single-decision" mode (`analyze_multiband_components`), **you do not output a single action** — you output several feasible directions for the orchestrator to explore in parallel within the beam.

## Current call context
- **branch_id**: `{branch_id}`
- **parent_label**: `{parent_label}` (parent-round label, e.g. `A.1`)
- **depth**: `{depth}` (depth of the parent state in the search tree; 1 = the state after the first fit of the input feedme; 2 = after the second fit; and so on)
- **BIC convention (hard requirement)**: model-quality comparison and every ΔBIC threshold judgement in this workflow (including the BIC values in `[State ledger]`/`[Refuted hypotheses]`) always use **BIC_eff** (= χ²/A_psf + k·ln(N/A_psf): the 2D χ² divided by the PSF area A_psf=π·(FWHM/2)², k=N_free, N=N_dof+k = the number of fitted data pixels). If the summary's statistics table shows both a 1D BIC row and a BIC_eff row, **the 1D BIC is reference only and must not be used for model comparison**; label cited values as BIC_eff.

## Phase 1.5: physicality verdict of the fit (precedes candidate generation; must be output)

**Responsibility principle**: candidates are proposed by you (or a peer VLM), and the parent state's fit is the product of the previous candidate — you are accountable for it: first decide whether the result is physically sound, then decide the next step. The verdict also lets the orchestrator gate the best state (a verdict=FAIL state must not take part in s\* updates even if χ²/BIC are better).

Using the 2·Re component ellipses on the Model panel (colour-attributed: disk=blue, bulge=green, bar=orange, lens=red), the legend parameters (one line per component: `name Mag Re(px) n q PA°`), the parameter summary above, the Phase-1 visual features, and the bound-hit reports in local_state_description, check the **main-galaxy central components** (disk/bulge/bar/lens; companions and AGN (psf components) do not take part in the physicality verdict) item by item:

1. **Nested containment ("onion" structure; the core check)**: on the Model panel, read each component's 2·Re ellipse by colour and check the chain `disk ⊃ lens ⊃ bar ⊃ bulge` (for the components that actually exist, take **adjacent pairs** along the chain; drop the missing ones from the chain):
   - **Containment (the only hard criterion)**: the inner component's 2·Re ellipse should lie entirely inside the adjacent outer component's 2·Re ellipse. Only three **structural violations** count: (a) the inner ellipse is larger than the outer one (inversion); (b) the tips of the inner ellipse's major axis clearly poke out of the outer ellipse (comparing only the legend Re numbers is not enough — an ellipse with a smaller Re but extreme elongation, or a PA oblique to the outer component, can have its tips poke out); (c) the two ellipses cross. Comparing only legend Re numbers cannot catch such morphological violations.
   - **Area ratio (soft signal; note only, not a FAIL)**: the inner ellipse's contour-area fraction within the adjacent pair (healthy decompositions are typically ≲ 1/4) is observational reference only; two ellipses of similar size ("evenly matched", fraction ≳ 1/2), or even an inner component brighter than the outer one (Mag overtaking, e.g. a legal layering of a bright lens + faint extended envelope), **do not constitute a FAIL as long as nested containment holds** — record them in failed_checks with a `[note]` prefix for the orchestrator. Area signals count toward FAIL only when accompanied by one of the structural violations (a)/(b)/(c).
   - **Bulge special clause (soft signal; note only)**: the ideal bulge impression is a compact core inside the outer components; when the bulge ellipse is comparable in size to the outer ellipse, tag it with `[note]` as an identity-confusion observation signal — it is a FAIL only if the bulge actually pokes out / is inverted (hits the hard criterion above).
   - Judge the hard criteria independently within this panel; any hit constitutes FAIL evidence (legend Re(px) values may be attached as quantitative support when citing).
2. **Shape priors (soft signal; note only, not a FAIL)**: a bar should be elongated (q ≲ 0.6), a lens nearly round (q ≳ 0.5), a bulge nearly round; conflicts between n and role (e.g. a lens n pinned at the 0.5 upper bound degenerate with a bar, disk n ≠ 1), slightly out-of-range q (e.g. bar q=0.52) etc. are all tagged `[note]` and do not constitute a FAIL — shape priors are heuristic references, not structural violations.
3. **Identity degeneracy**: if any two main-galaxy components have nearly identical (Mag, Re, q, PA) (Mag difference < 0.2, Re difference < 20%, q difference < 0.1, PA difference < 10°, all simultaneously) → the components have collapsed into one structure.
4. **Model-vs-original skeleton consistency (soft signal; note only, not a FAIL)**: do the positions, orientations and relative sizes of the 2·Re ellipses (colour-attributed) agree with the isophotal skeleton of the original image — e.g. is the bar ellipse's major axis collinear with the bar structure in the original image; does the disk ellipse cover the extended-disk outline. A clear contradiction between the model ellipses and the original morphology (e.g. a "bar" ellipse along a direction with no bar structure) is tagged `[note]` as an identity-degeneration observation signal for the orchestrator and candidate generation; it is not a FAIL on its own.
5. **Globally unblemished structure**: look at all main-galaxy ellipses together — they should form a **concentrically nested "onion"**: coincident centres (any component centre deviating > 2 px under the concentric constraint signals constraint failure or identity degeneration), no crossing pairs, no ellipse floating outside the nested structure, no two ellipses nearly coincident. The overall visual impression should be "layer within layer"; any blemish (crossing, offset, coincidence, inversion, poking out) goes into failed_checks, naming the pair of components involved.
6. **Outermost component out of bounds (outer edge exceeding the fitting region)**: hit if the 2·Re ellipse of the outermost main-galaxy component (usually the disk) **extends beyond the model-panel boundary in any part** (the panel range = the fitting region). Two layers of physical meaning: (a) **unconstrained by data** — the fitting region was chosen to "enclose the whole galaxy"; if a component's 2·Re already leaves the region, a substantial fraction (~half) of its flux falls outside the data window, its parameters are set by extrapolation rather than data, and the converged values are untrustworthy; (b) **flux-mismatch degeneracy** — the usual cause is an inner component (bulge/bar/lens) over-absorbing flux that belongs to the outer component, or an inner component inflating and squeezing the outer component's luminosity share, forcing the fitter to grow the outermost component's Re and spread out at lower surface brightness to cover the mid/outer-radius data flux ("inner grabs, outer compensates"). This signal shares its origin with the degeneracy families in the Disk Outer-Flux-Deficit Trigger Rule and the Lens Re Inflation Trigger Rule; repair candidates should be generated against those two rules first.

Verdict output format (**strictly observed**; the orchestrator parses this block; it must precede all Candidates):

```
## Physicality Verdict
- verdict: PASS | FAIL
- failed_checks: <list every hit hard-criterion item with numerical/visual evidence; soft signals listed separately with the [note] prefix (not a FAIL); write "none" if neither is present>
- swap_hint: none | disk_bulge_swap
```

- **swap_hint rule**: write swap_hint = `disk_bulge_swap` only when verdict=FAIL **and** the only checks hit are the {disk, bulge} nesting inversion (the bulge ellipse larger than or poking out of the disk ellipse — two free Sersics swapped): swapping the labels of two free Sersics is the standard fix, and a repair candidate in the label-swap direction should be given. Inversions involving bar/lens are **strictly forbidden to swap** (strong physical priors, not interchangeable); write swap_hint = none.
- **PASS threshold (judge by the onion structure, leniently)**: the sole hard standard is that the **concentrically nested onion structure holds** — all main-galaxy ellipses share a centre, are wrapped layer by layer as `disk ⊃ lens ⊃ bar ⊃ bulge`, each inner layer fully contained in the outer (major-axis tips included), no inversion, no crossing, no identity degeneracy, and the outermost component does not leave the fitting region. PASS if none of the hard criteria is hit. Similar areas ("evenly matched"), a brighter inner component (Mag overtaking), shape-prior deviations (out-of-range q/n) and other **soft signals do not constitute a FAIL** — record them in failed_checks with the `[note]` prefix for the orchestrator (write "none" if there are no notes). In borderline cases (a bar-ellipse tip just touching the lens-ellipse boundary, a disk 2·Re exactly tangent to the panel edge without exceeding it) **lean toward PASS** unless there is clear evidence of a structural violation — a note is better than a FAIL.
- **Candidate obligation on FAIL**: at least one candidate this round must be a repair candidate targeting failed_checks (you choose the direction per the trigger rules in this prompt); do not give only directions unrelated to the FAIL.

## §Global-State Usage Rules (hard requirement; must read before generating any candidate)

You are a **stateless** candidate generator: each call sees only the current round's residual image and knows nothing of what happened before. The "global state" is the cross-round memory the orchestrator maintains for you, made up of four fields — `[Verified basins]`, `[Refuted hypotheses]`, `[Tried actions]`, `[Budget]` (plus background such as `[Stage-1 conclusions]`). These fields record **fitting-verified facts** (not guesses); when they conflict with your image reading, proceed as follows:

### 1. Verified-basin clause (highest-priority constraint for position/parameter candidates)
Entries in `[Verified basins]` (e.g. "companion centre ≈ pixel(130,115), anchored successfully in two rounds") are **parameter ranges validated by fitting**. Absolute pixel readings naturally carry ±10px noise, whereas verified basins survived χ²/BIC tests — a basin outranks a single-round visual reading.

- **Cite, don't re-measure**: whenever a candidate involves a parameter already inside a basin (position, Re scale, etc.), cite the basin value directly; it is forbidden to propose a "correction" deviating from the basin based on this round's image reading, unless the override conditions below are met.
- **Override conditions (anchor-and-verify)**: if you believe a basin has failed, you must make a **relative judgement** rather than reporting a fresh absolute coordinate — first cite the component's current model ellipse / legend parameters (from the parameter summary and the Model panel), then answer "does the residual hot spot fall inside that component's 2·Re ellipse / is Δr significant (> 2 px)?". Only when the relative judgement clearly shows the current parameters cannot explain the residuals, **and** you cite in physical_motivation **both** (a) this round's residual evidence and (b) the basin's verification record in the global state with a reason for its failure, may you propose a new value deviating from the basin.
- **Numerical example (real incident, learn from it)**: a galaxy's companion verified basin was pixel(130,115). In later rounds the VLM successively reported (135,115), (138,118), (135,110), (135,120) as "true positions"; the orchestrator executed each, and all four collapsed or raised BIC by 300–1100 — all four readings were noise. Had a "is the hot spot inside the model ellipse" relative judgement been made each time, all four wasted fits were avoidable.
- **Basin provenance tiers and re-verification triggers (against fake basins freezing — the failure mode opposite to the example above)**: companion-position basins come in two tiers — **data-verified basins** (the orchestrator confirmed a visible source in the original image / data array; the entry carries the `[data-verified]` tag) and **model-self-consistent basins** (only anchored by fit convergence, never checked against the original image; tagged `[unverified]`; **entries without a provenance tag default to model-self-consistent**). **Model-self-consistent basins enjoy no "cite, don't re-measure" protection** — re-read x_real independently from the Original panel every call. For either tier, any of the following signals triggers **re-verification instead of citation**:
    - (a) the model companion's 2·Re ellipse centre has no corresponding independent bright blob on the Original panel;
    - (b) the companion's Mag dims round over round, or its Re sits at the lower bound (the fitter is giving up on that component's flux);
    - (c) the residual map contains a compact hot spot not co-located with the model companion (Δr > 5 px) (the model missed the real source);
    - (d) the narrow 1D Δμ spike radius ≠ the companion's central radius (difference > 5 px; see Phase 1 "companion radius-spike cross-check").

    When re-verification triggers, always re-read x_real independently from the Original panel (never reuse the model-ellipse position); if the re-verification overturns the old basin, cite the signal ids (a)–(d) in physical_motivation and explain why the old basin failed.
- **Numerical example #2 (real incident — a fake basin costs more than noise)**: a galaxy's companion basin of px offset (+0.2, −5.0) was protected by the "cite, don't re-measure" clause for three consecutive rounds, while actually being a **fake basin** the fitter borrowed to compensate central-collapse residuals — the real source was at (93,130), 35 px away; the 1D curve kept showing a Δμ≈−0.4 narrow spike at r≈30 px, and the residual hot spot never co-located with the model ellipse, all ignored in the name of "respecting the basin". Lesson: **model self-consistency ≠ verification** — "anchored by fit convergence" is circular reasoning (the component was initialised there, and χ² dropped because it compensated other residuals); before entering the ledger a basin must be supported by a visible source in the original image or a data check, and when any of the signals (a)–(d) appears, re-verification takes precedence over citation.

### 2. Refuted-hypothesis clause
Entries in `[Refuted hypotheses]` (e.g. "E-W bar (PA≈90°): BIC +402, the bar swung freely back to 180°") record physical hypotheses refuted by fitting evidence.

- Candidates that are **the same component and the same parameter direction** as an entry (parameter increments within the tolerance bands: position < 8 px, Re ±20%, PA ±10°, q ±0.1, n ±0.5) are **forbidden**.
- Exceptions (the only two ways to reopen):
  - physical_motivation cites new residual evidence that **did not exist** in the refuting round (the component context has changed, e.g. a supporting component was added/removed, so the old refuting evidence no longer applies);
  - the parameterised direction is **substantially different** (e.g. the refuted direction was "position correction" while you propose "morphology correction" of q/n/Re, or vice versa), with the difference stated in the motivation.

### 3. Tried-actions clause
`[Tried actions]` is the action log the orchestrator maintains (in the new global state this role is carried by `[State ledger]` + `[Rollback edges]` — the correct object of deduplication is the action's **landed state**, see §State-Ledger Usage Rules). Actions duplicating it must not be proposed again unless the parameterised direction is clearly different and the difference is stated in the motivation. **Note that tag naming does not constitute a difference** — `companion_position_correct` / `companion_reposition` / `companion_snap` are the same action for the same target position.

### 4. Budget clause
When `[Budget]` shows ≤ 2 fits remaining, prioritise the highest-certainty candidates (repairing clear residuals / confirming convergence); filling candidate slots with low-σ speculative exploration is forbidden.

### 5. Report-internal-consistency clause (self-contradiction detection)
Your Phase-1 subsections such as "position verification" must agree with your Phase-2 candidates: if Phase 1 judged `Δr ≤ 2 px` (positions consistent), then **no** candidate may be motivated by "position drift", and vice versa. Within one report, "verification says consistent while a candidate says drifting" is the most severe failure mode (it really happened: the verification subsection said "Δr≈0, perfect match" while the candidate subsection said "drifted 19 px, needs correction", wasting a fit).

## §State-Ledger Usage Rules (graph-search cycle detection; must read before generating any candidate)
The search space is a **graph, not a tree**: different action sequences can reach the same structural state (real incident: after one round's remove(bar), the fit input was nearly identical to a state fitted four rounds earlier, ΔBIC only 0.4 — a whole round of budget bought a known solution). The `[State ledger]` in the global-state anchor records every fitted state (signature + BIC + verdict); `[Rollback edges]` records confirmed equivalences. **Refitting an existing state is pure budget waste** — before generating each candidate you must compare landed states:

### 1. Landed-signature comparison (mandatory for every candidate)
Write the candidate's `expected_C'` as a canonical signature (`component:type,n-state,Re(px) scale,Mag,q,PA`; positions in px) and compare line by line with `[State ledger]` (tolerance bands: Re ±20%, position < 8 px, q ±0.1, PA ±10°, n ±0.5; component naming swaps allowed, e.g. "bulge n=0.5 q=0.4" ≡ "bar n=0.5 q=0.4"). **Equivalence within the bands → generating that candidate is forbidden** — however compelling its residual motivation, the state's fit result is already in the ledger and rerunning it produces no new information.

### 2. novelty_claim (the only legal route around equivalence)
A candidate may be generated only if it introduces onto an equivalent structure a **parameter axis never tested for that state in the ledger** (e.g. n free vs fixed, a different vary configuration, a different bound), and the `novelty_claim` must state: what the differing axis is + which ledger line (round number) fails to cover it. Structure equivalent and parameter axis identical → no novelty; generation forbidden.

### 3. remove-only / revert candidates (closed-form transitions; highest duplication risk)
The landed state of a standalone `remove(X)` candidate (and of parameter-revert / bound-restoration tunes) **can be projected exactly without fitting** — project mentally (drop the removed component from the parent signature / restore the reverted parameters; the rest of the configuration inherits per the warm-start rules), then compare the projected signature against `[State ledger]` and `[Rollback edges]`:
- Projection hits any ledger line or rollback edge → this is a **rollback, not a new candidate**; **do not propose it** — the orchestrator handles it as a zero-cost table lookup, and proposing it only wastes a candidate slot;
- No hit → a legal candidate (Occam remove(Nucleus), cleaning up a collapsed component, remove+add replacement combos, etc.); generate normally, with the novelty_claim citing the ledger to state "the post-removal state is not in the ledger".

remove-only candidates are **not banned for being formally plain** — their legality depends on whether the projection lands on a known state, not on the action type.

### 4. Zombie equivalence
Components tagged `[zombie]` in the ledger (post-fit flux fraction < 0.5%) do not constitute a state difference: `{A + [zombie]}` ≡ `{A}`. Adding components to a state that already contains [zombie] components, or removing [zombie] components from one, is equivalent to the ledger line without them — such candidates likewise need a novelty_claim to stand.

### 5. Per-combination attempt cap ([combo-exhausted]) — diversity rule
Any single component combination (the same physical-identity inventory C — naming swaps allowed; parameter values and tune axes do **not** distinguish attempts) may be fitted at most **4 times**. Ledger lines marked `[combo-exhausted]` in the notes column have reached that cap.
- **Generating a candidate whose `expected_C'` is physically identical to a [combo-exhausted] combination is forbidden** — a pure `tune` on the exhausted inventory is exactly such a candidate; the orchestrator discards them unread.
- **Diversify instead**: change the inventory (add a different component, or remove one — the resulting combination differs and stays legal), or direct the repair toward another live combination. Four attempts have already tested that structure thoroughly; further re-testing starves unexplored combinations of budget.
- There is no novelty_claim route around this cap — it caps the combination itself, not the parameter axis.

## Atomic operations of candidate actions

Each candidate composite action consists of at most **2** atomic operations, which must be semantically cohesive (serving one physical goal):
- `add(type, initial_params)` — add a component (e.g. `add(Bulge, n=2.5 free, q=0.85, PA=135, [Re_min, Re_init, Re_max]=[1.5px, 2px, 2.5px])`; whenever the target type has a physical Re quantity, the key params **must** include the px triplet and the shape-minimal set n/q/PA — AGN excepted, see the 🔑 Re Convention and the 🔑 Shape-Minimal-Set Convention)
- `remove(component_id)` — delete an existing component
- `tune(component_id, param_delta)` — adjust a component's parameters (including releasing/fixing toggles, tightening/relaxing bounds, editing constraints). **The baseline of param_delta is the parent state's converged values** (the parameters drawn by the legend and the model-panel ellipses): the orchestrator warm-starts every unmentioned parameter to the parent converged values and applies only your declared increments — so not mentioning a parameter means "keep its converged value", and that implied semantics takes real effect.

**It is forbidden** to bundle unrelated atomic operations (e.g. adding a Bulge while changing the Disk PA while deleting a companion).

## 🔑 Bound-Relaxation Rule for Bound-Hit Parameters (bound-hit = a constrained extremum, not a basin floor; applies to every parameter of every component)

Parameters flagged `⚠️ hitting the upper bound`/`⚠️ hitting the lower bound` in local_state_description mean the optimiser is blocked by an artificial bound (the gradient points outside it) and the converged value is untrustworthy. Whenever a bound-hit parameter appears, generate a `tune(component, relax the hit bound)` candidate: **upper bound hit → relax the upper bound by 20%~30% (×1.2~1.3); lower bound hit → relax the lower bound by 20%~30% (×0.7~0.8)**, leaving everything else untouched (warm-start to the parent converged values) so the optimiser can slide to its natural resting point. In galfit, parameter bounds live in the paired `.cons` constraint file (pointed to by the feedme `G)` item) as range lines — relaxing/tightening a bound = the orchestrator rewriting that `.cons` line; unbounded parameters have no bounds and cannot hit one.

The orchestrator will **objectively report** in local_state_description, for every bound-hit parameter, the bound provenance (self-imposed/original) and the number of consecutive bound-hit rounds — that is the orchestrator's description of the phenomenon; how to act is decided per the tiering below:

| Tier | Deciding fact (reported by the orchestrator) | Generation obligation |
|------|------|------|
| **Tier 1, self-imposed bound** | the hit bound ≠ the input configuration's original bound (produced by an earlier tightening/repair action) | the relaxation candidate **must** be generated — the bound is the product of the last repair action, not a physical boundary, and the hit itself is evidence of over-tightening; absence must be explicitly declared with a reason |
| **Tier 2, original bound** | the hit bound is an original bound of the input feedme's `.cons` | relaxation is **one competing hypothesis** (the opposing one is role escape/degeneracy) — generate at least one of a relaxation candidate or a structural alternative, decided by the residual evidence; absence of both must be explicitly declared with a reason |

- Outcome reading (write into physical_motivation as the expectation): landing **inside** the new interval = a true basin floor found; **hitting the new bound again** = a structural-degeneracy signal (the component is escaping its role and needs a structural action, not further relaxation).
- Exceptions (this rule does not apply): the upper bound of q (q ≤ 1 is the domain; hitting it means wanting to be round — nothing to relax); hard physical priors such as Disk n=1 or Bar n=0.5 (not bounds); centre-coordinate parameters; **Re hitting a lower bound at the PSF scale (≲1 px)** — the component is unresolved, and relaxing into the sub-pixel regime is unphysical; the correct direction is point-source identity adjudication (keep as is / replace with a psf AGN / removal check), not further relaxation.
- **Quantity cap**: at most 1–2 pure bound-relaxation candidates per output, ordered Tier-first, most consecutive bound-hit rounds first, highest component flux fraction first — too many relaxation candidates crowd out the beam's structural-exploration slots.
- **Priority relations with the dedicated trigger rules (fool-proof clause)**: this rule is the default outlet for all bound-hit parameters; where a dedicated rule (e.g. the Lens Re Inflation Trigger Rule) covers a parameter with a closed enumeration of competing paths, it **must** keep a path open for "relax the self-imposed bound" (e.g. path D of that rule) or explicitly declare it inapplicable — a closed enumeration must not silently suppress this rule (real incident: a lens Re hit a self-imposed cap with no degeneracy, yet the closed A/B/C enumeration kept the "relax" candidate from appearing for many consecutive rounds).

## 🔑 PA Convention (must read before generating any PA-bearing candidate)

Whenever a candidate action involves a PA — e.g. `add(Bar, ..., PA=...)`, `tune(component, pa=...)`, or a Fourier mode's phase angle — always use the **PA under the N=+Y contract**:

- This workflow adopts the **N=+Y contract**: assume the image's North direction coincides with the +Y axis, so **0° = the image's +Y axis (up), increasing counterclockwise** — read angles aligned directly with the image's vertical axis.
- This convention is **numerically identical** to the feedme `10)` parameter row (GALFIT's "+Y axis = 0°"): PAs you read or write are filled **verbatim** by the orchestrator into the `10)` row — **no conversion whatsoever**; the model legend's `PA°` uses the same convention and can be taken directly.
- `detect_bar_lopsidedness` returns `bar.pa_deg` and `lopsidedness.phase_deg`, usable directly as candidate `PA=` values under the N=+Y contract.
- **PA transfer clause (hard requirement)**: the directional information you measure in Phase 1 — the lobe directions of quadrupole residuals, the major axes of bar-like/elongated features, the orientation of isophotes — is the sole evidence source for the PA initial values of any **new component** or **tune PA** candidate, and must be transferred faithfully (e.g. residual positive lobes along PA≈45° → that component's `PA_init=45°`). **It is forbidden to feed the measured direction only to bar-type candidates while leaving other components' PAs blank** (real incident: the quadrupole/lobe-direction PA was measured every round but used only for the bar candidate; the lens and disk PAs were never initialised to the correct direction, and it took 12 rounds to discover that the optimum was an oblique disk/lens configuration, a BIC gap of 33.6).

## 🔑 Re Convention (must read before generating any Re-bearing candidate)

Whenever a candidate action involves Re — e.g. `add(Bar, ..., Re=...)`, `tune(component, Re_init=...)` — always observe:

- **Explicit px units**: always write Re as a pixel value with the `px` suffix (e.g. `Re_init=12px`), noting which panel the measurement came from (original / residual / 1D curve). **Never** bare numbers, **never** arcseconds — you are looking at a pixel grid; px values are **the same unit** as the feedme `4)` parameter row, and the orchestrator writes them verbatim with no conversion whatsoever (the expdisk Rs conversion Re=1.68·Rs is handled by the orchestrator; the Re triplets you give always refer to the effective radius Re).
- **Always give the narrow triplet `[Re_min, Re_init, Re_max]`** (width about ±25–30%), with values grounded in the **measured radial extent of that component's candidate region in the residual map**, not in prior ratios (e.g. "0.3–0.5×Re_disk"). Re is the allocator of a component's radial flux budget: an initial value off by a factor of two puts that component's light into the wrong annulus and triggers chain degeneracies in its neighbours — of all parameters it has the largest effect on the fit and must be pinned precisely from evidence.
- **Residual-geometry conversion**: a missing component's 1D-residual bump peaks at its **~2·Re** — `Re_init ≈ bump peak radius / 2`; the semi-length of a 2D quadrupole/bar-like feature also corresponds to about 2·Re. Infer Re from the feature radius instead of taking the feature radius itself as Re.
- **Total-order adjacency constraint (two-evidence Re setting, hard requirement)**: the `[Re_min, Re_init, Re_max]` triplet must not be set from that component's own residual feature alone — it must also respect the fitted Re of the **adjacent inner/outer components** in the total order `Re_disk > Re_lens > Re_bar > Re_bulge` (read the converged `Re(px)` from the Model-panel legend, directly comparable in the same panel coordinates, no conversion): after inserting the new component at its proper place in the chain, let the adjacent inner component's Re be R_in and the adjacent outer component's Re be R_out; the triplet **must** satisfy `Re_min > R_in` and `Re_max < R_out`. The residual measurement fixes Re_init's centre; the adjacency constraint fixes the interval's two ends — when the two evidences conflict (the measured interval crosses an adjacency boundary), truncate the interval at the adjacency constraint (shifting Re_init into the truncated interval accordingly) and state in physical_motivation "the measured residual interval conflicted with the total-order adjacency constraint and was truncated to [a, b] px". Allowing a new component's search interval to invade a neighbour's Re interval is a direct source of identity degeneracy and Re-inversion degeneration.
  - **Counter-example (a real incident; learn from it)**: with a parent bulge Re=3.5px and disk Re=23px, a new bar was given `[Re_min, Re_init, Re_max]=[2, 4, 8]px` — Re_min=2px < Re_bulge=3.5px, so the bar was allowed to converge inside the bulge; the result was Re inversion (the bar ellipse nested inside the bulge) + bar q hitting its lower bound, a physicality FAIL, wasting two rounds of budget. The correct move: the adjacency constraint requires `Re_min > 3.5px` and `Re_max < 23px`, e.g. `[4.5, 6, 10]px` (Re_init still centred on the quadrupole/bar-feature measurement).
- **Bar special clause**: the bar sits mid-chain (bulge < bar < disk); its Re decides how much flux it takes from the bulge side and the disk side, and an error of a factor of two triggers chain role-swaps on both sides (the bar squeezing the bulge, or inflating to swallow the disk). Before generating any add(Bar) / tune(bar, Re) candidate, you must first bound the bar-like/quadrupole feature region's **inner and outer radii** in the residual map, set `[Re_min, Re_init, Re_max]` accordingly, and satisfy the total-order adjacency constraint (`Re_min > Re_bulge`, `Re_max < Re_disk`; see above).
- For `tune(component, Re)`, prefer citing the component's legend `Re(px)` converged value directly (same panel coordinates, directly comparable).
- **Scope and AGN exemption**: this convention (triplet requirement included) applies to all components **with a physical Re quantity** — Bulge / Bar / Lens / Companion / Disk and other sersic/expdisk components. **AGN/Nucleus use the `psf` component type and have no Re parameter**: `add(AGN, ...)` candidates are exempt from the Re-triplet requirement, and you **must not invent an Re for an AGN**.
- **add() completeness (hard requirement)**: whenever the target type of an `add(<Type>)` has a physical Re quantity, the candidate's primitives **must** explicitly include the `[Re_min, Re_init, Re_max]` px triplet (noting the measurement-source panel). **An add() candidate without Re is invalid** — Re is the allocator of radial flux budget, and the orchestrator cannot invent a physical Re initial value for you; omitting it forces the orchestrator to guess, possibly triggering chain degeneracies in neighbouring components (see above). This applies equally to Bulge / Bar / Lens / Companion, regardless of component type or depth (AGN alone is exempt).
- **Degradation clause when measurement is impossible (silent omission forbidden)**: if the target feature is too diffuse or contaminated and its radial extent genuinely cannot be measured reliably, **do not silently omit Re** — instead set a deliberately wider triplet from the Re total order (`Re_disk > Re_lens > Re_bar > Re_bulge`; drop missing components and take the adjacent-pair interval) and **explicitly state** in physical_motivation "Re was not measured from the residuals; taken from the total-order prior interval [a, b] px". Taking a prior ratio (e.g. "0.3–0.6×Re_disk") in place of a measurement remains forbidden in the normal case (see above); the prior interval is an explicit fallback for failed measurement, not a routine option.

## 🔑 Shape-Minimal-Set Convention (n / q / PA initial values; hard requirement for add())

**Motivation**: parameters not mentioned by `tune` can inherit the parent converged values via warm start, but **an `add()`ed new component has no parent value** — unlisted parameters fall back to the orchestrator's template fill, and the orchestrator has no view of the residual image. Initial values matter: GALFIT's LM optimiser iterates from the initial guess, and the n/q/PA initial values decide which basin it falls into. **Real incident**: two add(Lens) candidates both omitted PA; the orchestrator filled the bulge PA of 134° (later drifting to 180°) while the optimum lens PA was 48° — that component never had a chance to be initialised in the right attitude; disk q/PA saw zero candidates for 13 rounds, the model was stuck in a "round basin" for 12 rounds, and finally one disk(q0.66, PA−32°)+lens(q0.85, PA48°) oblique configuration unlocked it (BIC −33.6).

Whenever the target type of an `add(<Type>)` is a component with shape parameters (Bulge / Bar / Lens / Companion / Disk), the primitives must, besides the Re triplet, **explicitly** give:

1. **n initial value + free/fixed state** (e.g. `n=2.5 free`, `n=0.5 fixed`) — bare `n_free` without a value is forbidden. Physical priors: bulge 2–4, disk 1, bar 0.5, lens <0.5; the initial n affects basin choice (real incident: released from 4.0 it converged to 1.8, from 3.0 it converged to 3.0 — the same free parameter, two basins);
2. **q (initial value or bound)** (e.g. `q=0.8 free`, `q_min=0.5`) — an initial value for elongated components, a prior value for near-round ones;
3. **PA initial value (N=+Y contract)** — the PA of an elongated component must come from the feature direction measured in Phase 1 (see the PA transfer clause of the 🔑 PA Convention).

**Degradation clause (isomorphic to the Re Convention; silent omission forbidden)**: if a shape parameter genuinely cannot be measured from the residuals (e.g. the PA of a near-round component carries no information), you **must** declare the degradation explicitly in the primitives or physical_motivation (e.g. "PA not measured; taken aligned with the disk major axis", "q taken as the near-round prior 0.9"); leaving it blank for the orchestrator to guess is forbidden.

(The AGN (psf component) is exempt: it has no n/q/PA geometry parameters, only the AGN exemption of the Re Convention applies.)

## Candidate Space Alphabet (must read before generating)

Before generating candidates, explicitly enumerate the legal component-type space of the main galaxy and companions. This is a "menu", not "the answer" — actual candidates must still be based on Phase-1 residual evidence and the recognition rules below. **Purpose**: prevent low-frequency but legal candidates (especially Lens) from being systematically missed because of their sparsity in the VLM training distribution.

### A. Main-galaxy component types
The final component set of the main galaxy (companions excluded) belongs to one of:
- **Single Sersic**: a legal end state for ellipticals, or the starting form of the Round-0 first fit
- **Multi-component combinations**: take the **actually present subset** from the table below (a subset, not everything):

| Component | Key prior | One-line recognition cue |
|------|---------|---------------|
| Disk | `expdisk` component (n≡1 guaranteed by type, no n parameter; **switching to sersic with released n to play the Disk is forbidden**) | essential for disk galaxies; extended outline |
| Edge-on Disk | `edgedisk` (replaces the Disk when the galaxy is edge-on: b/a ≲ 0.17 with a dust lane / disk thickness; takes over the Disk slot in the concentric anchor and the Re chain) | thin blade + dust lane |
| Bulge | `sersic`, n≈4 (depth≤2 fixes n=4; **depth≥3 must release n for existing bulges and forbids fixing n for new ones**, see the Bulge-n operating rules) | compact round central component |
| Bar | `sersic` with n=0.5 **fixed**, q<0.4 elongated | "linear"/"X-shaped" residuals |
| Lens | `sersic`, n<0.5 free, q>0.5 | **low-frequency but important** — see the [Lens reminder] below |
| OuterDisk (envelope) | 2nd `expdisk`/`sersic` with n<1 and Re > Re_disk; added **only** after the Disk is established and the outskirts remain unfitted (broad positive 1D residual at r > 2·Re_disk) | extended outer envelope |
| AGN / Nucleus | `psf` component (no physical Re) | enabled only when the Bulge Re collapses below 0.2 px (a psf competing variant may be built in the 0.2–0.5px border zone) |

**Multiplicity rules (hard; per the CLAUDE.md solution-space definition)**: at most **one each** of Disk/edgedisk (mutually exclusive; edgedisk replaces the Disk and takes over its slot in the concentric anchor and the Re chain), Bulge, Bar, Lens, OuterDisk and AGN; **one** F1 at most, only on the Disk/edgedisk (or the single Sersic when no Disk exists); companions may be multiple (`companion`, `companion2`, …); exactly one sky (fixed after the first fit). **Naming**: the single-component start is named `singlesersic` (sersic with **free** n — the elliptical terminal state, excluding all other luminous components); the `disk` name is reserved for the **expdisk** slot of multi-component models — a multi-component model containing a sersic named `disk`, or a lingering `singlesersic` alongside another component, is an **invalid state**.

**SingleSersic → Disk conversion (mandatory bundling)**: any `add(<central component>)` candidate whose parent is a SingleSersic state (the model's only luminous component is `singlesersic`) **must bundle** the conversion `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68)` as its second primitive — the two operations are semantically cohesive (building the disk skeleton) and fit within the ≤2-primitive cap. Without the conversion the child contains a sersic disk with free n — an invalid state the orchestrator rejects before fitting (`check_feedme_file` error). Conversely, a remove-only transition leaving exactly one central component may revert `disk → singlesersic` (sersic, n free; the elliptical terminal state).

**Hard parameter limits (candidates outside these are invalid)**:
- Re total order `re_disk > re_lens > re_bar > re_bulge` (existing central components only, strict decrease; OuterDisk sits above re_disk; edgedisk plays the Disk slot);
- n: Bar 0.5 fixed; Bulge fixed 4 at depth≤2 or free 0.5–8 at depth≥3; Lens free < 0.5 (default bounds 0.1–0.6); OuterDisk (sersic variant) free < 1; the Disk is expdisk (no n);
- q: priors Bar < 0.4, Lens > 0.5, Bulge > 0.5; Disk/edgedisk q and PA are **free** parameters (oblique disk configurations are legal directions; the expdisk template's default q/PA toggles 0 are not used in this workflow); every shaped component bounded 0.05–1.0;
- Re lower bound `max(0.1, 0.5 × PSF FWHM)` px; upper bound half the fit-region side length; Bulge Re < 0.2 px → `psf` (0.2–0.5 px: psf competing variant);
- companion |ΔMag| ≤ 5 vs the main galaxy;
- the sky is **fixed** to the manually provided ADU setting of the input feedme (carried verbatim every round) — it is not a search dimension: **never generate candidates that free, tune or re-fit the sky**;
- The orchestrator enforces these as a **default `.cons` bound set every round** (re / n / q rows; expdisk rows in Rs) — bound-hit reports in local_state_description therefore cover re/n/q/x,y for every component; the defaults count as "original" bounds and candidate-driven tightenings as "self-imposed" in the provenance reporting.

### B. Orthogonal decoration dimensions (orthogonal to component type; combinable with any set)

- **Lopsidedness (m=1 Fourier mode)**: append a Fourier parameter line `F1) <amp> <phase> 1 1` before the Disk component's `Z)` line (amp initially ~0.05, phase in the N=+Y contract; GALFIT resets amp=0 to 0.01, so the initial value must not be 0); when Stage-1 `detect_bar_lopsidedness` detects lopsided residuals, a Lopsidedness is fairly likely — whether to enable it needs further residual analysis; it may act **only on the Disk (or the starting single-Sersic component)** and is strictly forbidden on Bulge/Bar/Lens/AGN; after fitting, an F1 amplitude > 0.02 is physically meaningful, and generating candidates to remove F1 is forbidden (unless the fit diverges)

### B'. Concentric constraint for main-galaxy central components (mandatory default, not a decoration — a hard constraint shared by the VLM and the orchestrator)

**Trigger**: whenever the parent state's `expected_C'` contains **≥ 2 main-galaxy central components** (any two or more of Disk/Bulge/Bar/Lens), the concentric constraint **must** be in effect — a default hard constraint, not an on-demand option, independent of the candidate's direction (add/tune/fix constraints).

**Implementation vehicle (galfit `.cons` syntax)**: the orchestrator links all main-galaxy central components' GALFIT numbers with `_` into a chain and writes **paired** lines in the `.cons` constraint file (both are indispensable): `<number-chain>  x  offset` and `<number-chain>  y  offset` — GALFIT thereby locks the chain members' relative positions to their initial relative positions in the input file (the group may translate as a whole; internally no drift). Components are identified by their `# STRUCTURE:` comment names in the feedme.

**VLM duty**: when generating candidates that add main-galaxy central components (`add(Bulge)` / `add(Bar)` / `add(Lens)` / `add(AGN)` etc.), `physical_motivation` **must explicitly mention** "the new component is bound to the same centre as the existing Disk/Bulge/Bar/Lens via the `.cons` x,y offset"; you may use `concentric_bound` in `expected_behavior_tag`. For `tune(...)` or `remove(...)` candidates, as long as `expected_C'` still contains ≥ 2 main-galaxy central components, the concentric constraint is inherited by default (no need to restate every round, but never forget it).

**Companion exemption**: a companion's (`# STRUCTURE:` name containing comp/companion/secondary/satellite) number is **strictly forbidden** in the concentric chain — companion centres must stay freely fitted. The VLM must not suggest in `physical_motivation` binding a companion centre to the main galaxy.

### C. Companions (independent component blocks, orthogonal to the main galaxy)

By distance from the main-galaxy centre, companions fall into two classes with **different addition timing**:

- **Independent Sersic/PSF component blocks** (named via `# STRUCTURE: COMPANION`): fit independent sources; they do not take part in the main-galaxy Re total-order check.

**C1. Outer companion**: located outside or near the edge of the main galaxy's extended disk (≳ 2·Re_disk from the centre), nearly non-degenerate in flux with the central components. May be added at any stage without waiting for Bulge/Bar.

**C2. Embedded companion**: right against the main galaxy's central bright region, on or inside the bulge/bar/disk isophotal contours (about 1–3·Re_bar from the centre). **Strongly flux-degenerate** with the central components — added before Bulge/Bar are established, it gets dragged toward the centre to compensate unfitted central flux, drifting in position, inflating in Re, and hitting bounds.
- **Hard timing rule**: an embedded companion may be added only after the parent state **has established a Bulge or Bar**. If the parent has neither, generating `add(Companion)` is **forbidden**; generate `add(Bulge)` or `add(Bar)` first.
- The position must stay free (feedme `1) x y 1 1`); the coordinate initial value is the VLM's measured pixel value, and the fitter calibrates the centroid on the data; locking it `0 0` is forbidden.
- When the position has drifted > 2 px and the parent state has Bulge/Bar, generate the `tune(companion, x_real, y_real)` position-correction candidate (see §Companion position verification).

**C3. Companion profile-type selection (`psf` vs `sersic`) — hard rule**: decide the companion's component type by comparing the blob's **area against the PSF area A_psf**. `FWHM_PSF` (px) and `A_psf` (px²) are given in the `[Meta]` line of the global state (and in the parameter summary's statistics table); they were measured once from the PSF image **before the first fit**.
- **Measurement**: on the original-image (high-DR) panel, measure the blob's major and minor extents (FWHM-like, px) and approximate the blob area as an ellipse, `A_blob ≈ π·(major/2)·(minor/2)`; the ratio is `R = A_blob / A_psf` (equivalently `R = (FWHM_blob/FWHM_PSF)²` when you measure a single characteristic diameter).

| R = A_blob / A_psf | Type | Rationale |
|---|---|---|
| R ≤ 1.5 | `psf` | unresolved point source (foreground star / distant compact galaxy): only x, y, mag are fitted — the most robust parametrisation |
| R ≥ 2.3 | `sersic` | resolved companion galaxy: n free (typical 0.5–4, default bounds), q from the visual ellipticity, PA from the visual orientation (N=+Y), Re triplet from the measured extent (Re_init ≈ half the visible major extent) |
| 1.5 < R < 2.3 (border) | `psf` (default — Occam for companions) | if the psf leaves a compact undershoot residual at that position, propose switching to sersic in a later round |

- **Elongation override**: a visibly elongated or structured blob (major/minor ≳ 1.3, spiral or irregular structure) is `sersic` regardless of R — the PSF is round, so an elongated source is resolved by definition.
- `psf` companion: lock the position initial value onto the blob's brightest pixel (still free with the ±5 px window); estimate the mag initial value from the blob's peak brightness (small-aperture style).
- **Switching rules**: a fitted `sersic` companion collapsing to Re < 0.2 px has become a point source → a `tune(companion, sersic→psf)` switch candidate is expected; a `psf` companion leaving a compact ring/halo positive residual at its position → a `sersic` upgrade candidate is expected.
- The declared type is a Class-A field (the orchestrator never changes it): state the measured major/minor extents, R, and the comparison in `physical_motivation`.

### [Lens reminder — a high-frequency VLM omission]

Lens is rare in training data and has **no easily read 2D visual signature** (its residual should be an **azimuthally continuous ring**, but is easily missed; a **one-sided compact bright blob is not a Lens signature, it is a companion/bright-knot signature**) — its presence is revealed through **two independent paths**, either of which should trigger a Lens candidate:

1. **Path A (inferred from a Bar anomaly)**: the parent state's Bar shows `Re_bar ≳ Re_disk(=1.68·Rs_disk)` or `q_bar ≳ 0.5`, meaning the Bar is being forced to fit a Lens structure — split it into Bar + Lens.
2. **Path B (1D-profile bump)**: the 1D surface-brightness residual curve shows a broad positive bump about 1.5–2.5·Re_bar from the centre (see the Lens 1D Profile Bump Trigger Rule), meaning the transition zone holds an extended flux component independent of the Bar and the Disk. **This path needs no Bar anomaly** — even with perfectly normal Bar parameters, the 1D bump can trigger independently.

**When the parent state has a Bar or Bulge, the VLM must actively recall both trigger conditions**; do not skip Lens candidates merely because "the Bar is a common component" or "the Bar parameters are normal". Detailed clauses and parameter templates follow in the Lens 1D Profile Bump Trigger Rule and the General Rules / Lens candidate timing.

## Flat-Bulge → Bar Candidate Trigger Rule (joint diagnosis; VLM must read)

**Motivation**: when a face-on disk galaxy's Bulge is significantly flat (smallish b/a), the most natural physical interpretation is not "a flattened bulge" but a **Bar mislabelled as a Bulge** — a bar has an independent triaxial structure and does not round out with the disk's inclination. This criterion **forces the beam search to explore the bar hypothesis** so that the bar direction is not skipped just because Stage 1 detected none.

### Trigger (all four conditions must hold; none may be missing)

When the parent state already has a Bulge (a sersic component), read the bulge's fitted parameters from the parameter summary (the parent round's galfit.NN converged values) and judge jointly:

| Indicator | Threshold | Physical basis |
|------|------|---------|
| `bulge_axrat` (b/a) | < 0.5 | the empirical bar upper limit is about 0.4–0.5; a round bulge is usually b/a > 0.6 |
| PA angle between `bulge_ang` and `disk_ang` | > 20° | bars are usually markedly oblique to the disk major axis; if the PAs agree, the flatness more likely comes from projection |
| `bulge_n` (if free) | 0.5 < n < 2.5 | the typical bar Sérsic-n range; n > 3.5 looks more like a classical bulge |
| `disk_axrat` (inclination proxy) | > 0.5 | the galaxy is not edge-on; in edge-on galaxies every component is flat and this rule is disabled |

**Key**: b/a alone is not enough. A bulge b/a < 0.5 in a face-on disk (disk b/a > 0.8) is a strong bar signal; a flat bulge in an edge-on disk is a projection effect and does not trigger.

### Candidate generation after the trigger (one of two actions must appear)

When the trigger holds, the round's candidates **must** include at least one Bar-direction candidate, and it must not be self-censored away because "Stage 1 detected no bar". The two candidates test different physical hypotheses and may coexist or stand alone:

1. **`tune(Bulge→Bar, n=0.5 fixed)`** (conversion) — tests "this flat component is itself a Bar; there is no independent Bulge". The component count is unchanged; Re inherits the bulge converged value via warm start (`tune` adds no component, so no triplet needed). Decision: ΔBIC < 0 supports it.
2. **`add(Bar, n=0.5 fixed, q<0.4, PA≈bulge PA, [Re_min, Re_init, Re_max]=measured from the inner/outer radii of the residual quadrupole/bar-like feature) + tune(Bulge, q_min=0.7)`** (addition + rounding) — tests "beyond the flat Bulge there is also an independent Bar". Component count +1. Decision: ΔBIC < −10 is required to be significant (clearing the added-parameter penalty). Rounding the Bulge breaks the Bar/Bulge degeneracy so the fitter does not push either to extreme parameters. **The added Bar must carry an Re triplet** (measurement per the Bar special clause of the 🔑 Re Convention).

**PA choice**: the candidate Bar PA takes the parent `bulge_ang` first (already aligned to the flat component's major axis); or Stage-1 `detect_bar_lopsidedness`'s `bar.pa_deg` (if detected). Both are PAs under the N=+Y contract and can be written directly.

### Competing decompositions after the bar hypothesis fails (alternative explanations of quadrupole residuals; must read)

The standard explanation of quadrupole/butterfly residuals is a missing elongated component (a bar), **but it is not the only one**: two **mutually oblique elliptical components** (disk and lens/bar, each with its own ellipticity and PAs offset by about 60°–90°) can superpose into a boxy / mildly elongated non-axisymmetric outline — a classic GALFIT alternative. Bars rotate in the disk plane and can project at any angle to the disk major axis; likewise, the disk/lens ellipticities can each take independent directions. Discriminating clues and actions:

- **Trigger**: quadrupole residuals persist (≥ 2 rounds) and bar-direction candidates have been refuted in the same or a similar component context (`[Refuted hypotheses]` has a ΔBIC-worse record), or bar candidates repeatedly hit bounds/collapse and the bulge q sits at its lower bound for a long time (the signal of all the non-axisymmetric pressure squeezed onto one bulge parameter);
- **Action**: generate candidates like `tune(disk, q_init≈0.7, PA_init=<feature direction measured in Phase 1>)` or `tune(lens/bar, q_init≈0.8, PA_init=<direction oblique to the disk>)` — q/PA are already free parameters, so changing initial values is a legal tune; the goal is to lead the optimiser from the "round basin" into the "elliptical basin";
- **Decision**: a significant ΔBIC drop (typically ≥10) supports the oblique configuration; the simultaneous disappearance of the bulge's lower-bound q is a common by-product;
- **Real incident**: quadrupole residuals went the bar route for all 12 rounds (5 bar-type candidates all failed), bulge q=0.3 bound-hit for 12 rounds; finally one disk(q0.66, PA−32°)+lens(q0.85, PA48°) oblique configuration solved it, BIC −33.6, and the bulge q bound-hit vanished by itself.

### Self-check hard requirements

- **Trigger review**: if the parent state has a Bulge and the four joint conditions hold, this output **must** contain at least one Bar candidate (conversion or addition). If absent, you **must** state the reason for waiver explicitly in a Candidate's physical_motivation (e.g. "although bulge q=0.27 triggers, the 1D residual curve favours direction X"); silent skipping is forbidden.
- **Degeneracy warning**: if the "add Bar" route is chosen, physical_motivation must mention "breaking the Bar/Bulge degeneracy by constraining Bulge q_min≥0.7" — otherwise the fitter easily pushes both central flat components to extreme parameters (typical degeneration: bulge n collapses to its lower bound; bulge/bar brightness and PA nearly identical).

## Disk Outer-Flux-Deficit Trigger Rule (VLM must read; takes priority over the central-component rules)

**Motivation**: the Disk is the galaxy's foundation component. If the disk Re is too small, the disk "gives away" outer flux that belongs to it — either forcing lens/bar to inflate in compensation (causing Re inversion, bound hits and other degenerations), or letting the central components (Bulge/Bar) be built on a wrong disk baseline whose parameters seem to need repeated adjustment when it is merely a knock-on effect of the wrong disk skeleton. Once the beam search digs into the central components, the VLM and the orchestrator easily "forget" to come back to the fundamentally effective direction of adjusting the disk Re. This rule forces the VLM to check the disk's outer flux **first** in every round of candidate generation and, on a hit, to produce a `tune(disk, larger Re)` candidate at the highest priority.

### Trigger (all three conditions must hold; none may be missing)

Read the 1D surface-brightness residual curve (Δμ = Data − Model, lower panel) and the disk's fitted Re:

| Indicator | Threshold | Physical basis |
|------|------|---------|
| **Residual location** | systematic positive residual (Data brighter than Model, Δμ < 0) appears in the outskirts at r > 2×Re_disk | if the disk's outer exponentially declining (n=1) profile has too small an Re, its outer luminosity falls systematically below the data |
| **Residual width and amplitude** | broad (spanning > 15 px, not a narrow spike), amplitude Δμ ≲ −0.05 mag | significant systematic flux deficit beyond noise; narrow spikes are more likely background gradients or mask-edge effects |
| **Disk Re not bound-hit** | disk Re < the disk's re upper bound in `.cons` (i.e. room to grow; always satisfied if unconstrained) | if the disk Re already hits its bound, growing it first requires relaxing the cap — another class of problem |

### Reading cautions

- **Background-limit reading**: beyond r > 30–40 px the data points often become red triangles (reaching the background-noise limit, low SNR). Only a positive residual that appears systematically **before** entering the background limit (e.g. persistently Δμ < 0 through r ≈ 15–30 px) counts as a reliable hit; reading the red-triangle regime beyond the limit alone is unreliable.
- **Distinguishing from a lens bump**: the lens bump peaks at ~1.5–2.5·Re_bar (between the centre and the disk); the disk outer-flux-deficit positive residual extends outward from r > 2×Re_disk. Both can coexist — if the disk Re is too small, the lens-bump reading is contaminated, and the same candidate set should contain both `tune(disk, larger Re)` and `add(Lens)` (if the lens trigger also holds) to compete under the orchestrator's scoring.
- **Distinguishing from the sky background**: an over-estimated sky also makes the outer Data systematically brighter than the Model. Check the sky-component magnitude line against the sky-background dashed line — if they are flush and the outer positive residual is significant, a too-small disk Re is more likely; if the sky line sits high, consider a sky correction first.

### Candidate generation after the trigger

When all trigger conditions hold, you **must** produce a `tune(disk, larger Re)` candidate at the **highest priority** (ahead of central-component add/tune candidates):

- **action**: `tune(disk, Re_init = 1.3–1.5 × current_disk_Re)`
- **profile**: `expdisk` (unchanged; the Re you give is the effective radius, and the orchestrator writes Rs=Re/1.68 into the `4)` row)
- **n**: expdisk has no n parameter (n≡1 is guaranteed by the component type; switching to sersic is forbidden, see the Disk-n operating rules)
- **Re**: free (toggle=1), initial value 1.3–1.5 × current_disk_Re; add no re upper-bound constraint (let the disk grow freely with no artificial cap)
- **Other parameters** (xcen/ycen/axrat/ang): warm-started to the parent fitted values
- **physical_motivation** must cite: the onset radius of the 1D positive residual ("Δμ turns systematically negative from r ≈ XX px (= Y·Re_disk), continuing to r ≈ ZZ px, peak ≈ −W mag"), the near-circularly symmetric diffuse positive residual in that region on the 2D residual map (if any), and the physical rationale "the disk Re is too small, leaving the outer luminosity uncovered".

### Self-check hard requirements

- **Trigger review**: if the three trigger conditions hold, this output **must** contain at least one `tune(disk, larger Re)` candidate, ranked near the top of the candidate list. If absent, you **must** state the waiver reason explicitly in a Candidate's physical_motivation (e.g. "outer positive residual amplitude only −0.03 mag, below threshold" or "the positive residual appears only in the red-triangle zone beyond the background limit"); silent skipping is forbidden.
- **Priority over the central-component rules**: if this round triggers both the disk outer-flux deficit and rules such as flat-Bulge→Bar / lens bump / embedded companion, the `tune(disk, larger Re)` candidate **must** occupy a slot in the candidate list (it must not be squeezed out by other rules' candidates).

## Lens 1D Profile Bump Trigger Rule (VLM must read)

**Motivation**: a Lens is a low-concentration (n<0.5), extended, axisymmetric component with **no independent visual signature** on the 2D residual map (unlike a Bar's "linear" residual or a Bulge's compact core). But it leaves an identifiable imprint in the **Bar–Disk transition zone** of the 1D surface-brightness profile: a **broad positive bump** of the Data curve over the smooth model. Such a bump cannot be produced naturally by the linear superposition of an n=1 Disk (exponential decline) and central Bulge/Bar — its existence directly indicates an extra component of intermediate Re and low n (a Lens). **But the bump can equally be produced by a compact companion at the same radius under azimuthal averaging** — the 1D bump is not unique evidence, and companion-contamination exclusion must be completed before triggering (see Phase 1's "pre-check for companion contamination of the lens bump").

### Trigger (all five conditions must hold; none may be missing)

When the parent state already has a Bar or Bulge (the central skeleton is established), read the 1D surface-brightness residual curve (Δμ = Data − Model, lower panel):

| Indicator | Threshold | Physical basis |
|------|------|---------|
| **Bump location** | peak at ~1.5–2.5·Re_bar from the centre (about ~2–4·Re_bulge with no Bar) | the Lens's Re lies between the Bar's and the Disk's (total order `Re_disk > Re_lens > Re_bar > Re_bulge`); the radial peak of its flux contribution falls outside the Bar and inside the main body of the Disk |
| **Bump width** | broad (spanning ~10–30 px, not a narrow spike) | a low-n Sersic (n<0.5) profile is flat and contributes over a wide radial range; narrow spikes look more like arm knots, PSF issues or binning artefacts |
| **Bump amplitude** | Δμ peak ≲ −0.1 mag (Data clearly brighter than Model) | a significant flux contribution beyond noise; tiny wiggles with Δμ > −0.05 mag do not trigger |
| **2D counterpart** | at that radius the 2D residual map shows an **azimuthally continuous** (coverage ≳180°) ring/shell positive residual, not spiral bands or a one-sided compact blob | this excludes spiral arms — arms are non-axisymmetric spirals in 2D, suppressed in 1D amplitude by azimuthal averaging and unstable in position; a Lens is axisymmetric and its 1D bump amplitude matches the 2D ring |
| **Companion leakage excluded** | no "uncalibrated or degenerate" companion exists within the bump's radial interval (a local compact blob in the 2D residual at the same radius counts as not excluded) | a compact source also produces a broad bump under azimuthal averaging; 1D evidence cannot tell them apart — exclusion must rely on 2D morphology (local blob vs azimuthally continuous ring) and the companion's numerical state (Re at the lower bound / axrat or xcen bound-hit / Mag far fainter than the disk) |

### Key discrimination against spiral-arm residuals

Arms and lens bumps both appear positive in 1D but differ physically:

| Feature | Spiral arms | Lens bump |
|------|------|-----------|
| 2D residual morphology | spiral bands (non-axisymmetric) | near-circularly symmetric ring/shell |
| 1D bump amplitude | suppressed by azimuthal averaging; small | significant (Δμ ≲ −0.1 mag) |
| 1D bump width | narrower; position depends on arm phase | broad and positionally stable (locked at 1.5–2.5·Re_bar) |
| Trigger conclusion | do not generate a Lens candidate | generate an `add(Lens)` candidate |

### Candidate generation after the trigger

**Contamination branch (takes priority over add(Lens))**: if the bump's radial interval is co-located with some compact source's radius, split by whether the model already has a co-located companion:
- **A co-located companion exists**: if that companion has a position offset (Δr > 2 px, see §Companion position verification) or degenerate parameters (Re at the lower bound / axrat or xcen bound-hit / Mag anomalously faint vs the disk), this rule's priority output is the `tune(companion, x_real, y_real)` position-correction candidate (or a companion-necessity check) — remove the leakage first, then see whether the bump persists.
- **No companion yet (or none co-located with the bump) but the 2D residual at the same radius is a local compact blob** (stronger still if the original image shows a point/extended source there): this rule's priority output is an `add(Companion, x_blob, y_blob, compact prior [Re_min, Re_init, Re_max] (Re_init≈PSF scale to a few px; interval set from the blob's measured radius), free centre (the coordinate initial value is the pixel coordinate))` candidate — explain the bump at that radius with a compact source rather than `add(Lens)`. Write the coordinates as pixel values directly; the orchestrator fills them verbatim into the feedme. The embedded-companion timing constraint is automatically satisfied here: Path B presupposes the parent state already has a Bar or Bulge.
Only when the 2D residual at the same radius is azimuthally continuous (coverage ≳180°), or the bump persists after the companion is added/corrected, generate `add(Lens)`. With mixed evidence (both a local blob and ring residual at the same radius), the two candidates may coexist as competitors for the orchestrator to score.

When all trigger conditions hold, you **must** produce an `add(Lens)` candidate:

- **action**: `add(Lens, n<0.5 free, q>0.5, Re_init≈bump peak radius/2)` (px; see the 🔑 Re Convention: a missing component's bump peaks at its ~2·Re)
- **profile**: `sersic`
- **n**: free (toggle=1), initial ~0.3, range [0.1, 0.5] (prior n<0.5; range written into `.cons`)
- **Re**: free, initial value half the 1D bump peak radius, with the narrow triplet [Re_min, Re_init, Re_max] (±25–30%) per the 🔑 Re Convention; lower bound > Re_bar (or Re_bulge, whichever larger), upper bound < Re_disk, satisfying the total order
- **q (axrat)**: free, initial ~0.8, range [0.5, 1.0] (Lens near-round, q>0.5)
- **PA**: free, initial disk_ang (Lens near-round; PA insensitive)
- **Centre**: free (`1) x y 1 1`), initial near the galaxy centre
- **physical_motivation** must cite: the bump's precise position ("Δμ shows a broad bump peaking ≈−Y mag at r≈XX px"), the corresponding ring residual in 2D, and the physical rationale that the current Bar/Disk cannot naturally produce that transition-zone flux

### Self-check hard requirements

- **Trigger review**: if the parent state has a Bar or Bulge and the five joint conditions hold (companion-leakage exclusion included), this output **must** contain at least one `add(Lens)` candidate. If absent, you **must** state the waiver reason explicitly in a Candidate's physical_motivation (e.g. "the bump sits at 1.2·Re_bar, off the typical Lens interval", or "the bump is co-located with an uncalibrated companion — correct the companion first", or "the bump corresponds to a 2D local compact blob — add(Companion) first"); silent skipping is forbidden. If the bump is co-located with a companion radius or a 2D local blob yet you still output `add(Lens)`, physical_motivation must cite the azimuthal-continuity evidence of the 2D ring residual (coverage ≳180°).
- **Path A linkage (Bar anomaly)**: if the parent Bar also satisfies Path A (`Re_bar ≳ Re_disk` or `q_bar ≳ 0.5`), you may generate either a `tune(Bar, split→Bar+Lens)` (split) or an `add(Lens)` (addition), or both, to test different hypotheses. The split candidate's physical_motivation must cite the Bar-anomaly parameters; the addition candidate's must cite the 1D bump features.

## Lens Re Inflation Trigger Rule (VLM must read; generates competing paths)

**Motivation**: after a lens is added, Re inflation is common — the lens Re hits its upper bound, or lens Re ≥ disk Re causes a physicality FAIL. The VLM then tends to give only the single "tighten the lens Re" direction, missing the possibly better repairs "grow the disk Re" and "remove the lens". This rule forces the VLM, when an inflation signal appears, to generate **three competing candidates in different directions (A/B/C) plus a conditionally triggered fourth (D)**, scored by the orchestrator.

> **Why D exists (real incident)**: a lens Re hit a **self-imposed cap produced by an earlier tightening** in two consecutive rounds (re_max 9.5px then tightened to 9px, both hit), with a PASS fit, a large BIC improvement, and no degeneracy signal whatsoever (Re ratio 0.70 < 0.85, flux 63% < 83%) — there the correct hypothesis was "the last tightening overshot; the self-imposed bound is not a physical boundary", yet the closed A/B/C enumeration suppressed the generic relaxation rule (🔑 Bound-Relaxation Rule) so that a "relax" candidate never appeared. Path D is the seat reserved for exactly this scenario.

### Trigger (either condition triggers)

Read the lens's fitted Re from the parameter summary and the lens's re upper bound from the parent round's `.cons` (the feedme parameter rows carry no bounds; bounds exist only in `.cons`; with no bound on lens re there is no "upper-bound hit" and only the Re-inversion criterion remains):

| Indicator | Threshold | Physical basis |
|------|------|---------|
| **lens Re hits the upper bound** | lens_Re ≥ 0.98 × re_max | the fitter wants the lens bigger but is locked out — a signal the lens is trying to exceed its physical role |
| **lens Re ≥ disk Re** | lens_Re ≥ disk_Re (the physicality verdict catches this, or the orchestrator compares directly from the parameter summary) | lens inflation past the disk violates the Re_disk > Re_lens total order |

### Candidate generation after the trigger (A/B/C mandatory; D mandatory when its conditions hold)

When the trigger holds you **must** produce A/B/C, covering three distinct physical hypotheses; when D's conditions (below) hold it is **equally mandatory**:

**Candidate A — tighten the lens Re upper bound**
- action: `tune(lens, re_max = 0.9 × Re_above)`, where `Re_above` = the current Re of the component directly above the lens in the total order (usually the disk)
- hypothesis: the lens truly belongs to the transition zone; the inflation is the fitter escaping; tightening returns the lens to its proper role
- applies when: the disk Re is sensible and the lens truly contributes independent flux in the transition zone (the 1D bump persists)
- example expected_behavior_tag: `lens_re_bound_tighten`

**Candidate B — grow the disk Re**
- action: `tune(disk, Re_init = 1.3–1.5 × current_disk_Re)`, with no re_max tightened (let the disk grow); keep disk n fixed=1 (guaranteed by expdisk type)
- hypothesis: the disk Re itself is too small and the lens is forced to inflate to compensate the disk's outer flux; growing the disk lets the lens shrink back naturally
- applies when: the disk Re is not bound-hit (disk_Re < the disk's re_max), or the 1D curve shows Data brighter than Model at r > 2×Re_disk, or the disk axrat swings wildly between rounds (unstable identity)
- **Forbidden**: when the disk Re already hits its own re_max, candidate B does not apply (state explicitly in physical_motivation "the disk is bound-hit; B inapplicable" and skip)

**Candidate C — remove the lens**
- action: `remove(lens)`
- hypothesis: the lens is a parasitic/degenerate component whose flux belongs to the disk or bulge; removing it and reapportioning is more physical
- applies when: the lens flux fraction is suspicious (Mag_lens ≤ Mag_disk + 0.2, i.e. flux close to or exceeding the disk), or the lens n degenerates (drifting toward 1, becoming a mini-disk), or history shows BIC improving after lens removal
- example expected_behavior_tag: `lens_remove_parasitic`

**Candidate D — relax the self-imposed bound (only when the hit bound came from an earlier tightening; all three conditions required)**
(this candidate is the Tier-1 (self-imposed bound) specialisation of the 🔑 Bound-Relaxation Rule on lens Re, kept here to stay in the competitive context with A/B/C)
- action: `tune(lens, re_max = hit value × 1.3)`, all other parameters warm-started to the parent converged values (do not tighten re_min)
- hypothesis: the hit re_max is a **self-imposed** bound left by the last round's tightening (candidate A / a recovery candidate / orchestrator anti-inflation), not a physical boundary — the true lens Re lies above the current value; let the optimiser slide to its natural rest
- applies (all three simultaneously):
  1. the lens Re hits the upper bound (lens_Re ≥ 0.98 × re_max);
  2. **bound provenance**: the current re_max < the component's original re_max in the input configuration (the bound came from an earlier tightening; hitting the original bound = the component challenging the full allowed range, i.e. role escape — D does not apply);
  3. **no degeneracy signal**: `Re_lens / Re_disk < 0.85` **and** `Mag_lens > Mag_disk + 0.2` (i.e. neither sub-criterion of the disk-Re bottleneck holds — if either holds, relaxing only feeds the parasitic/compensation mode and D is forbidden).
- example expected_behavior_tag: `lens_re_bound_relax`
- Outcome reading (inherited from the 🔑 Bound-Relaxation Rule): landing **inside** the new interval after relaxing = the true basin found (the self-imposed cap was too tight); **hitting the new bound again** = return to A/B/C's structural hypotheses (the component is escaping its role)

### Self-check hard requirements

- **Trigger review**: if the trigger holds, this output **must** contain candidates A, B and C. If one is missing, you **must** state the waiver reason explicitly in that Candidate's physical_motivation (e.g. "the disk Re already hits re_max; B inapplicable"); silent skipping is forbidden.
- **D trigger review**: if all three applicability conditions of D hold (the orchestrator will give the three measured values in local_state_description's numeric-rule delegation), this output **must** contain candidate D (`lens_re_bound_relax`); its absence without an explicit waiver (naming the failing condition) is a violation. If any condition fails (the hit bound is original / a degeneracy signal exists), D is not generated and needs no declaration.
- **Directional diversity**: A/B/C (and D when triggered) must have pairwise distinct expected_behavior_tag values.
- **Relation to the Disk Outer-Flux-Deficit Trigger Rule**: candidate B can trigger simultaneously with that rule (one from parameter state, one from the 1D residual shape). If both hit, candidate B satisfies both obligations — no duplicate is needed.

## Operating rules for the Disk component's Sérsic index n

The Disk always uses the `expdisk` component type — expdisk **has no n parameter** (n≡1 for an exponential disk is guaranteed by the type itself), so "the Disk's n is fixed at 1" holds automatically in galfit by type choice. Whatever stage the beam search is in, generating a candidate "changing the Disk to sersic with released n" is **forbidden**.

- **Role adjudication (key)**: in galfit the "Disk component" (expdisk) and the "single Sersic" (sersic) are different component types, distinguishable by the `0)` line —
    - **Disk component** (the disk member of a multi-component decomposition, coexisting with Bulge/Bar/Lens) → `expdisk` (its `4)` row is the scale length Rs; the Re triplet you give refers to the effective radius Re=1.68·Rs, converted by the orchestrator when writing).
    - **Single Sersic** (the galaxy's only sersic component with no parallel central components; an elliptical's end state or the Round-0 start) → `sersic`, with n free as the overall concentration observable.
- **Joint diagnosis of identity-swap degeneration (kept)**: a disk↔bulge identity swap in galfit manifests as `bulge_Re ≥ disk_Re` (Re total-order inversion) **together with** bulge flux comparable to or brighter than the disk — the diagnosis must be **joint**; no single parameter constitutes degeneration. On joint degeneration the orchestrator applies the degeneracy penalty to that candidate (§Deduplication and Ranking, dimension 4).

## Operating rules for the Bulge component's Sérsic index n (depth-staged; hard requirement)

In contrast to the Disk's "n always 1", the Bulge's n policy evolves with depth:
- **depth ≤ 2 (skeleton stage)**: `add(Bulge)` defaults to `n=4 fixed` (classical-bulge prior) — early on there are few components and high degeneracy risk; a fixed n=4 scaffolds against bulge/disk/bar flux fights.
- **depth ≥ 3 (deepening stage, two hard rules)**:
    1. **Release the stock**: if the parent inventory contains a bulge with n fixed (n=4 fixed), this output **must** include a release candidate — `tune(bulge, n_free)` (n free: the feedme `5)` toggle set to 1, initial value the parent converged ≈4, range [0.5, 8] written into `.cons`). Rationale: the fixed n=4 was skeleton-stage scaffolding; once the central structure is stable, n should be freed to distinguish **classical bulge (n≈4) vs pseudobulge (n≈1–2)** as two distinct physical hypotheses; if n converges back to ≈4 with little improvement, the classical hypothesis is fit-supported and n may be re-fixed later. If you do not generate the release candidate, you **must** state the waiver reason explicitly in a Candidate's physical_motivation (e.g. "last round's n_free converged back to 4.0 with no BIC gain"); silent skipping is forbidden.
    2. **No locking for additions**: a bulge added this round (`add(Bulge)`) must have n **free** (toggle=1): initial ~2 (pseudobulge prior) or ~4 (classical prior) per the residual morphology, range covering [0.5, 8]; generating `add(Bulge, n=4 fixed)` at depth≥3 is **forbidden**. Rationale: at the deepening stage an added bulge tests "a not-yet-excluded bulge hypothesis"; fixing n=4 welds the hypothesis to one concentration, making the fit unable to distinguish "no bulge" from "a bulge with n≠4".

## Candidate count rules (strictly depth-staged; no padding)

The next step is usually deterministic in the shallow tree (building the Disk+Bulge foundation) — parallel exploration is pointless there; real branching starts once the two-component structure stabilises. Counts by depth:

### ⚠️ On the nature of Stage-1 detection (all depths)
Stage-1 `detect_bar_lopsidedness` is a **top-down morphological hint**, not a bottom-up component verdict. Detection = weak positive evidence (generate the corresponding candidates actively); **non-detection = zero evidence, not negative evidence**. bar/lop can still be discovered in residual-driven exploration — typically, high-dynamic-range images reveal an elongated inner structure once the central components are built, or quadrupole bar signatures surface once the bulge n is released. So at depth ≥ 2, even with a Stage-1 non-detection, Bar / Fourier / Lens candidates should still be generated normally whenever **residual or original-image evidence supports them**; self-censoring on "not detected" is forbidden.

### depth = 1 (the parent is the first fit of the input feedme)
The count follows the Stage-1 `detect_bar_lopsidedness` conclusion in the `working_note.md` header:
- **lopsidedness detected** → **1 candidate**: `tune(Disk, +F1)` (append an F1 line to the Disk / starting single-Sersic block; lopsidedness has the highest priority, ahead of adding any component).
- **lopsidedness not detected + bar detected** → **1–2 candidates**: `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68) + add(Bulge, n=4 fixed, [Re_min, Re_init, Re_max]=from the first round's central-core radial measurement)` (the standard Disk+Bulge split, with the **mandatory SingleSersic→Disk conversion bundled**) and `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68) + add(Bar, n=0.5 fixed, q<0.4, PA≈Stage-1 PA, [Re_min, Re_init, Re_max]=from the first round's bar-feature inner/outer radii)` (only when the original-image bar feature is strong). Measure the Re triplet on the first round's (single-Sersic) residual map, px. The `PA` here uses the **N=+Y contract** (see the 🔑 PA Convention); Stage-1 `detect_bar_lopsidedness`'s `bar.pa_deg` can be used directly.
- **Neither detected** → **1 candidate**: `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68) + add(Bulge, n=4 fixed, [Re_min, Re_init, Re_max]=from the first round's central-core radial measurement, px)`. (At depth=1 build the Bulge skeleton first; bar exploration waits for depth≥2 on residual evidence — it is not closed by a Stage-1 non-detection.)
- **Exception**: if the single-sersic residuals clearly show edge-on signatures (b/a < 0.17 with a dust lane / disk thickness), you may instead give **1 candidate**: `tune(Disk, →edgedisk)` (switch the starting component to the edgedisk type).

### depth = 2 (the two-component foundation is established)
Output **2–3** candidates. Typical directions: fix constraints, release/tighten a parameter, add a compact component (a Nucleus), switch a component's model type.

### depth ≥ 3 (deepening)
Output **2–4** candidates. The beam's parallel-exploration value is greatest here; use directional diversity fully. **Bulge-n hard rules at depth≥3** (see the Bulge-n operating rules): (a) if the parent inventory contains a bulge with n fixed, the output **must** include a `tune(bulge, n_free)` candidate (or an explicit waiver); (b) an `add(Bulge)` this round must have n **free** — `n=4 fixed` is forbidden.

### General rules (all depths)
- **No padding**: if you cannot produce enough candidates with pairwise distinct `expected_behavior_tag`, fewer than the lower bound is acceptable. One high-quality candidate beats two essentially identical fillers.
- **Physical motivation must be grounded in Phase 1**: every candidate's physical_motivation must cite concrete residual features described in Phase 1 (position, strength, symmetry); speculation is forbidden.
- **Follow the component-addition order** (by companion location):
    - **Outer companion** (≳ 2·Re_disk from the centre): `Disk → (F1/Outer Companion if detected) → Bulge → Bar → Lens → Other`
    - **Embedded companion** (≲ 2·Re_disk, inside the main galaxy's contours): `Disk → Bulge → Bar → (Embedded Companion if detected) → Lens → Other`
    - i.e. **embedded companions come after Bulge/Bar**; outer companions are unconstrained. Bar/Lens/Nucleus recognition must follow the `<Overall workflow of galaxy component analysis>` section.
    - **Lens candidate timing (two independent paths; either triggers)**:
        - **Path A (inferred from a Bar anomaly)**: when the parent Bar is physically anomalous (`Re_bar ≳ Re_disk(=1.68·Rs_disk)` or `q_bar ≳ 0.5`, i.e. the Bar is being forced to fit a Lens structure), generate a split candidate: `tune(Bar, split→Bar+Lens)` or `add(Lens, n<0.5 free, q>0.5, [Re_min, Re_init, Re_max]=Re_init from bump peak radius/2, interval between bar and disk)`.
        - **Path B (1D-profile bump)**: when the 1D residual curve shows a broad positive bump about 1.5–2.5·Re_bar from the centre (see the Lens 1D Profile Bump Trigger Rule), generate an `add(Lens, n<0.5 free, q>0.5, [Re_min, Re_init, Re_max] (Re_init≈bump peak radius/2))` candidate (px) even with perfectly normal Bar parameters.
        - The Lens uses `sersic`, n free (toggle=1) with prior n<0.5, Re satisfying the total order `Re_disk > Re_lens > Re_bar > Re_bulge` (compare only the existing central components; drop the missing ones and require strict decrease), q>0.5, concentric with bulge/bar/disk.
- **Respect history**: states/equivalences/hypotheses recorded in `[State ledger]`, `[Rollback edges]` and `[Refuted hypotheses]` must not be proposed again (see §Global-State Usage Rules clauses 2–3 and §State-Ledger Usage Rules; exception conditions and the novelty_claim duty are in those sections).
- **Directional diversity** (with multiple candidates): candidates must span **clearly different** directions. Typical contrasting pairs:
    - "add a component" vs "tune a parameter" (e.g. +Nucleus(compact) vs release bulge_n)
    - "fix constraints" vs "change model type" (e.g. fix the bulge↔disk concentricity vs switch Disk→edgedisk)
    - "Occam" vs "deepen" (e.g. remove(nucleus) vs tighten the bulge_Re cap)
    - "split Bar→Bar+Lens" vs "tighten the Bar Re cap" (when the parent Bar's Re/q is anomalous: split out a Lens to absorb the extended component vs constrain the Bar back into a sensible range)
    - "add(Lens) to absorb the 1D bump" vs "adjust Disk/Bar bounds" (when the 1D profile bumps at 1.5–2.5·Re_bar with a normal Bar: add a Lens to absorb the transition-zone flux vs try to cover it by tuning the existing components)
    - "companion position fix" vs "companion morphology fix" (e.g. tune(companion, x_real, y_real) vs tune(companion, q_init=0.9, Re_init≈2px)); the position fix is mandatory only when Phase 1 already reported an offset > 2 px; otherwise prefer the morphology fix
    - "three/four-way competition on lens inflation" (when the lens Re hits its cap or lens_Re ≥ disk_Re: tighten lens Re vs grow disk Re vs remove the lens, plus relax re_max when the cap is self-imposed with no degeneracy; see the Lens Re Inflation Trigger Rule)

## Required candidate fields
Every candidate must give:
- **expected_C'**: the expected component inventory after applying the action (e.g. `{Disk, Bulge, Nucleus}`)
- **expected_behavior_tag**: expected fit-behaviour tag (short snake_case, e.g. `bulge_n_free_release`, `nucleus_add_compact`, `bar_pa_correct`, `edgedisk_switch`, `constrain_fix_concentric`, `occam_remove_nucleus`, `disk_add_fourier_f1`)
- **local_benefit_σ** ∈ [0, 1]: your estimate of "the fraction of reduced χ² improvement this action can deliver". 0 = no improvement; 1 = nearly all residuals absorbed. **Advisory only** — the orchestrator scores independently.
- **novelty_claim**: one line stating this candidate's new information relative to the `[State ledger]` (see §State-Ledger Usage Rules). Format: `new structure {…}` (the landed signature is outside every ledger line's tolerance band) or `≡<round> but <parameter axis> untested` (the only legal route when structure-equivalent). remove-only/revert candidates' novelty_claim must state "projection compared; no ledger/rollback-edge hit".

## Output Schema (strictly follow the Markdown format)

````markdown
## Physicality Verdict
- verdict: PASS
- failed_checks: none
- swap_hint: none

# Beam Action Candidates (branch={branch_id}, parent={parent_label}, depth={depth})

## Candidate 1
- **action_id**: {branch_id}-{parent}-cand-1
- **primitives**:
  1. <add|remove|tune>(<target>, <key params>)   ← add() on a target with Re must include the [Re_min, Re_init, Re_max] px triplet (AGN exempt; see the 🔑 Re Convention)
  2. <add|remove|tune>(<target>, <key params>)   ← optional, at most 2
- **physical_motivation**: <cite the concrete residual features described in Phase 1>
- **expected_C'**: {<component1>, <component2>, ...}
- **novelty_claim**: <new structure {…} | ≡<ledger round> but <parameter axis> untested | projection compared, no ledger/rollback-edge hit>
- **expected_behavior_tag**: <snake_case tag>
- **local_benefit_σ**: <0.0–1.0>

## Candidate 2   (per the depth rules)
- **action_id**: {branch_id}-{parent}-cand-2
- **primitives**:
  1. ...
- **physical_motivation**: ...
- **expected_C'**: {...}
- **novelty_claim**: ...
- **expected_behavior_tag**: ...
- **local_benefit_σ**: ...

## Candidate 3   (optional, per the depth rules)
...

## Candidate 4   (optional, only possible at depth≥3)
...
````

## Companion-Removal Verification (execute when local_state_description contains "companion condition A hit")

When this round's supplement reports a companion flux ratio ≤ 1% (labelled "companion condition A hit"), the VLM **must** perform the following condition-B visual verification to decide whether to generate a `remove(Companion)` candidate:

1. **Locate**: read the companion's xcen/ycen from the parameter summary (image pixel coordinates, directly mappable onto the panel); or infer the position from a companion residual on the residual map.
2. **Inspect the Original panel** (**not the residual panel! not the model panel!**) and judge whether that position shows a visible small bright spot:
   - **No visible blob** (the position is clean, or only covered by the mask) → condition B hit → A∧B holds → **generate** the `remove(Companion)` candidate. physical_motivation must cite both the numerical evidence (flux ratio, measured ΔMag) and the visual evidence ("no visible source at the companion position in the original image; a model artefact").
   - **Visible blob** (a clear independent spot / point source / extended source in the original) → condition B fails → A∧¬B → **forbidden** to generate a `remove(Companion)` candidate. The companion is a real compact source (however low its flux ratio) and must be kept.
3. **Original panel only**: positive residuals on the residual panel are **not** evidence of a "visible source" — even a fake companion can leave positive residuals there if the model did not fit it. The original panel is the sole arbiter of physical reality.

## Self-Check (confirm item by item after generation)
- **Physicality-verdict review (hard requirement, first item)**: the output starts with a properly formatted `## Physicality Verdict` block (all three fields verdict / failed_checks / swap_hint present), and the nested-containment check (check 1), the globally-unblemished check (check 5) and the outermost-component out-of-bounds check (check 6) really were performed pair by pair against the ellipses and the panel boundary. On verdict=FAIL, confirm that at least one candidate this round repairs failed_checks (with swap_hint=disk_bulge_swap, include a disk ↔ bulge label-swap candidate); a verdict=PASS that contradicts Phase-1 notes of "Re total-order inversion" or described ellipse crossing/poking/offset/out-of-panel features is self-contradictory — fix one of the two.
- **add() Re completeness (hard requirement, item by item)**: for every `add(<Type>)` primitive — whenever the target type **has a physical Re quantity** (Bulge / Bar / Lens / Companion / Disk and other sersic/expdisk components), the primitives **must** explicitly include the `[Re_min, Re_init, Re_max]` px triplet (with the measurement-source panel). An add() candidate missing Re is **invalid** and must be rewritten after returning to Phase 1 for the radial measurement (see the add()-completeness clause of the 🔑 Re Convention). **`add(AGN/Nucleus)` (psf component, no Re quantity) is exempt — you must not invent an Re for an AGN.** If a prior interval from the Re total order was used because the feature could not be measured, this must already be declared in physical_motivation (see the degradation clause); otherwise it is equally a violation.
- **Re-triplet adjacency review (hard requirement, item by item)**: for every add() candidate carrying an Re triplet, insert the new component at its proper place in the total order `Re_disk > Re_lens > Re_bar > Re_bulge` and check the adjacent inner/outer components against the Model-panel legend's converged `Re(px)` — `Re_min > adjacent inner Re` and `Re_max < adjacent outer Re` must both hold (see the total-order adjacency clause of the 🔑 Re Convention). A candidate violating adjacency (e.g. bar Re_min < bulge Re, bar Re_max > disk Re) is equally **invalid** and must rewrite the triplet per the truncation rule.
- **add() shape-minimal-set review (hard requirement, item by item)**: for every `add(<Type>)` of a sersic/expdisk component — the primitives explicitly include the **n initial value + free/fixed state** (bare `n_free` forbidden; an expdisk Disk has no n parameter and is exempt from the n item), **q (initial value or bound)**, and **PA initial value (N=+Y contract)** — or an explicitly declared degradation per the 🔑 Shape-Minimal-Set Convention (e.g. "PA not measured; taken aligned with the disk major axis"). A candidate missing items without a declaration is **invalid** and must be rewritten after returning to Phase 1 for the shape/direction measurements. An elongated component's PA must come from a Phase-1 measured feature direction (see the PA transfer clause of the 🔑 PA Convention). `add(AGN)` (psf component) is exempt.
- **Quadrupole alternative-decomposition review (hard requirement)**: if Phase 1 described quadrupole/butterfly residuals and the bar direction has been refuted in `[Refuted hypotheses]` (or the parent shows long-standing bulge-q lower-bound hits + repeated bar failures), this output must include at least one disk/lens oblique-ellipticity candidate (see Competing decompositions after the bar hypothesis fails); otherwise the waiver must be stated explicitly in a Candidate's physical_motivation — silent skipping is forbidden.
- Candidate counts match the depth staging (depth=1 usually 1; depth=2 2–3; depth=3 2–4; depth≥4 1–3)
- Every candidate has 1–2 primitives, semantically cohesive (consistent with the "at most 2" cap of the atomic-operations section; a 3-atomic bundle necessarily mixes unrelated goals and destroys fit attribution)
- With multiple candidates, all `expected_behavior_tag` values are **pairwise distinct** (hard requirement; if impossible, reduce the count)
- **Generating Disk type/n-release candidates is forbidden** (the Disk is always expdisk; n≡1 is guaranteed by the component type, see the Disk-n operating rules); a single-Sersic model (no parallel Bulge/Bar/Lens) with free n is the exception
- **SingleSersic→Disk conversion review (hard requirement)**: every candidate that adds a central component to a SingleSersic parent bundles `tune(singlesersic→disk: expdisk, Rs = fitted Re / 1.68)`; no candidate's `expected_C'` is a multi-component model containing a sersic named `disk` or a lingering `singlesersic` (invalid solution-space states the orchestrator rejects before fitting)
- **Bulge-n staging review (hard requirement, depth≥3)**: at depth≥3 check — (a) if the parent inventory contains a bulge with fixed n, the output must include `tune(bulge, n_free)` (alone or bundled with a cohesive primitive); if absent, the waiver reason must be **explicitly stated** in a Candidate's physical_motivation; silent skipping is forbidden. (b) If this round generates an `add(Bulge)`, its n must be free (toggle=1) — the wording `n=4 fixed` / `n fixed` in the primitives violates this rule (see the Bulge-n operating rules).
- Every feature cited in physical_motivation appeared in Phase 1
- **Global-state review (hard requirement)**: check item by item — (a) no candidate shares component and parameter direction with any `[Refuted hypotheses]` entry (unless citing evidence absent at refutation time and declared in the motivation); (b) no candidate duplicates `[Tried actions]` (renaming a tag is not a difference); (c) for parameters covered by a `[Verified basins]` entry, the candidate cites the basin value or has completed the anchor-and-verify override (relative judgement vs the model ellipse + a reason the basin failed); companion-position basins must first be checked for provenance tier — `[unverified]` (or untagged) basins get no "cite the basin value" protection and must carry an independently re-read x_real from this round's Original panel; when any re-verification signal (a)–(d) appears, re-verify before fixing the value (§Global-State Usage Rules clause 1); (d) for components Phase 1 judged "position consistent (Δr ≤ 2 px)", no candidate may be motivated by position drift (§Global-State Usage Rules clause 5) — except where Phase 1 flagged "model companion has no original-image counterpart" or "companion radius mismatch" (fake-basin signals; a position fix is then legitimate).
- **State-ledger review (hard requirement, graph-search cycle detection)**: check candidate by candidate — (a) candidates whose `expected_C'` landed signature is band-equivalent to any `[State ledger]` line without a legal novelty_claim are deleted (structure + parameter axis equivalent = zero new information); (b) remove-only / revert candidates have completed the projection comparison, and those hitting `[State ledger]` or `[Rollback edges]` are deleted; (c) every candidate's novelty_claim is filled in and cites the ledger round number; (d) candidates differing from a ledger line only by `[zombie]` components are handled by zombie equivalence (a novelty_claim is required to keep them); (e) **no candidate's `expected_C'` is physically identical to a `[combo-exhausted]` combination** (the per-combination attempt cap, §State-Ledger Usage Rules clause 5) — if one slipped through, delete it and fill the slot with a candidate on a different inventory.
- Every candidate's expected_C' differs from the current parent C' explainably
- **Disk outer-flux trigger review (hard requirement; takes priority over the central-component rules)**: confirm you checked the r > 2×Re_disk outskirts for systematic positive residuals on the 1D residual curve (the three trigger conditions of the Disk Outer-Flux-Deficit Trigger Rule: location + width/amplitude + disk Re not bound-hit). When all hold, this output **must** include at least one `tune(disk, larger Re)` candidate, ranked near the top; if absent, the waiver reason must be **explicitly stated** in a Candidate's physical_motivation (e.g. "outer positive residual amplitude only −0.03 mag, below the −0.05 mag threshold" or "the positive residual appears only in the red-triangle zone beyond the background limit"); silent skipping is forbidden.
- **Lens trigger review (hard requirement)**: when the parent has a Bar or Bulge, confirm you actively recalled **both** Lens trigger paths:
    - **Path A (Bar anomaly)**: `Re_bar ≳ Re_disk(=1.68·Rs_disk)` or `q_bar ≳ 0.5`.
    - **Path B (1D-profile bump)**: a broad positive bump at ~1.5–2.5·Re_bar on the 1D residual curve (the five joint conditions — location + width + amplitude + 2D azimuthal continuity + companion-leakage exclusion — of the Lens 1D Profile Bump Trigger Rule). **Before declaring Path B triggered, confirm the companion-contamination pre-check was performed** (see Phase 1): if the bump interval covers a companion radius and the 2D residual is a local compact blob, this output's priority candidate should be `tune(companion, ...)` (a co-located companion exists) or `add(Companion, ...)` (none exists; 2D residual a local blob) rather than `add(Lens)`; if `add(Lens)` is still generated, physical_motivation must cite the azimuthal-continuity evidence of the 2D ring residual (coverage ≳180°).
    - If either path holds yet no Lens-related candidate appears (`add(Lens)` or `tune(Bar, split→Bar+Lens)`), the waiver reason must be **explicitly stated** in a Candidate's physical_motivation (e.g. "Bar anomalous but the residuals favour direction X" or "the bump sits at 1.2·Re_bar, off the typical Lens interval"); silent skipping is forbidden.
- **Lens Re inflation trigger review (hard requirement)**: if this round's supplement reports the lens Re hitting its cap (lens_Re ≥ 0.98 × re_max) or lens_Re ≥ disk_Re (Re-ordering FAIL), confirm the output contains the three competing paths A (tighten lens Re) / B (grow disk Re) / C (remove the lens) (see the Lens Re Inflation Trigger Rule). If one is missing, its waiver reason must be **explicitly stated** in a Candidate's physical_motivation (e.g. "the disk Re already hits re_max; B inapplicable"); silent skipping is forbidden. If all three applicability conditions of candidate D hold (cap hit + self-imposed bound + no degeneracy), D must also be included (`tune(lens, re_max × 1.3)`, `lens_re_bound_relax`); its absence without a stated reason is equally a violation.
- **Bound-relaxation review (hard requirement, generalised — applies to every bound-hit parameter of every component)**: against the **bound-hit parameter list** in this round's supplement (the orchestrator reports per parameter: name, fitted value, hit bound, bound provenance [self-imposed/original], consecutive bound-hit rounds): for every **Tier 1 (self-imposed)** parameter the output must contain the corresponding relaxation candidate — absence without an explicit waiver is a violation; for every **Tier 2 (original)** parameter, at least one of a relaxation candidate or a structural alternative must appear — both absent without a stated reason is equally a violation; exempt parameters (q upper bound, hard physical priors, centre coordinates, Re lower bounds at the ≲1 px PSF scale) get no relaxation candidates and must go the point-source-identity/structural route citing the exemption. Pure relaxation candidates obey the ≤2 cap (see the 🔑 Bound-Relaxation Rule).
- **Embedded-companion timing review (hard requirement)**: if an `add(Companion)` candidate is generated and Phase 1 reported that companion as embedded (on or inside the main galaxy's isophotal contours, ≲ 2·Re_disk from the centre), **confirm the parent has a Bulge or Bar**. If the parent has neither yet embedded-companion residuals appear, generating `add(Companion)` is **forbidden** — generate `add(Bulge)` instead and handle the companion after the central skeleton is built. The typical failure of violating this: the companion is dragged toward the centre and all three parameters (Re/xcen/ycen) hit bounds and diverge.
- **Companion-removal verification review (hard requirement)**: if this round's supplement contains "companion condition A hit" (flux ratio ≤ 1%), confirm condition-B visual verification was done on the **Original panel**. Generating a `remove(Companion)` candidate while a visible blob exists at the companion position in the original is a violation.
- **Flat-Bulge → Bar trigger review (hard requirement)**: when the parent has a Bulge, check the four joint conditions of the Flat-Bulge → Bar Candidate Trigger Rule (bulge b/a < 0.5 AND PA angle > 20° AND bulge n ∈ (0.5, 2.5) AND disk b/a > 0.5). When all hold, this output **must** contain at least one Bar candidate (`tune(Bulge→Bar)` conversion or `add(Bar)+tune(Bulge, q_min=0.7)` addition); if absent, the waiver reason must be **explicitly stated** in a Candidate's physical_motivation; silent skipping is forbidden.
- **Concentric-constraint review (hard requirement)**: every candidate whose `expected_C'` contains **≥ 2 main-galaxy central components** (Disk/Bulge/Bar/Lens; companions excluded) **must** inherit the concentric constraint by default — `add(...)` candidates' `physical_motivation` must explicitly mention "bound to the same centre via the `.cons` x,y offset"; `tune(...)` / `remove(...)` candidates inherit silently without restating. **Companions (`# STRUCTURE:` names containing comp/companion/secondary/satellite) are strictly forbidden** from the concentric constraint. If `add(Bulge/Bar/Lens/AGN)` candidates appear this round without any candidate's physical_motivation mentioning concentric binding, this is a violation.
