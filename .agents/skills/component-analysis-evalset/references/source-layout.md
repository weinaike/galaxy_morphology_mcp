<!-- design_document: docs/component-analysis/evaluation-set-design.md -->
<!-- adjudication_rule_version: component-evalset-v2 -->

# Source Layout

- Single-band root: `/media/data/galfit_run_history`
- Multi-band root: `/media/data/galfits_run_history`
- Multi-band expert labels: `/media/data/galfits_run_history/expert-final-labels.json`
- Single-band JWST labels: `/media/data/galfit_run_history/jwst_32_gt.json`
- Single-band Gadotti labels: `Gadotti_params.json` or `*_Gadotti_params.json` under the source root; for the former, the parent directory is the object ID. Reading rule (2026-09-15): `MType == "elliptical"` with a non-zero `mag_bulge` means the object label is `single_sersic` (the bulge magnitude describes the single component); otherwise components come from the non-zero `mag_xxx` fields (`mag_disk`/`mag_bulge`/`mag_bar`; zero means absent).

Single-band batches contain object directories with `galfit.feedme`, `galfit.NN`, `analysis_report.md`, `working_note.md`, FITS files, and comparison images. Canonical mappings are `disk`, `bulge`, `bar`, `nucleus -> agn`, `fourier -> fourier_m1`, and `elliptical -> single_sersic`; `companion?` is uncertain. Semantic-label sources (2026-09-18 ruling): `# STRUCTURE` comments, `# Object number: N -- Label` headers, and trailing `(Label)` on the `0) model` line are equivalent; an unlabeled multi-component Sersic with n≈1.0 fixed (vary=0) is a Sersic-disk. Multi-band lyrics: N blocks map to `agn`, P blocks under a non-host G block map to `companion`, and generic `objN` labels resolve via the round's decision file, instance-wide letter anchors, or fixed-n heuristics; unresolvable blocks are `unidentified_sersic` (their transitions go INCONCLUSIVE/excluded). Multi-band decision files (`all_bands_comparison_component_analysis_*.md`) are action-intent evidence.

Multi-band batches contain numeric object directories, sometimes with `_2`, and base/`_iterN.lyric` configurations, reports, notes, summaries, and band-level FITS. The base `.lyric` is the first historical configuration. Normalize `_2` only for comparison; use content fingerprints to distinguish duplicate and independent rerun.

Only `label_status=confirmed` objects in `expert-final-labels.json` are eligible. Preserve all label files as read-only evidence.
