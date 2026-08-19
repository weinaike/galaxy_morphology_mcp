"""Experimental ``run_galfit`` entry point augmented with effective 2D BIC.

The production implementation in ``src.tools.run_galfit`` remains untouched.
This wrapper deliberately reuses that implementation so GALFIT execution,
plotting, summary generation and archiving cannot drift between the control and
experimental paths.  It only appends auditable effective-BIC artifacts after a
successful run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, List

from eval.eff_bic.metrics import PsfAreaMethod, compute_effective_bic_from_files
from src.tools.parse_feedme import parse_feedme
from src.tools.run_galfit import run_galfit as _production_run_galfit


def _append_effective_bic_summary(summary_file: str | None, metrics: dict[str, Any]) -> None:
    if not summary_file:
        return
    path = Path(summary_file)
    if not path.is_file():
        return
    section = (
        "\n---\n\n"
        "## Experimental Effective 2D BIC\n\n"
        "> This section is produced by `eval.eff_bic.run_galfit`; it does not "
        "replace the production 1D BIC.\n\n"
        "| Statistic | Value |\n"
        "|---|---:|\n"
        f"| PSF area method | {metrics['psf_area_method']} |\n"
        f"| A_PSF (pixel²) | {metrics['psf_area']:.8f} |\n"
        f"| N pixels | {metrics['n_pixels']} |\n"
        f"| N effective | {metrics['n_effective']:.8f} |\n"
        f"| N free parameters | {metrics['nfree']} |\n"
        f"| 2D χ² | {metrics['chi2']:.8f} |\n"
        f"| Raw 2D BIC | {metrics['bic_2d']:.8f} |\n"
        f"| Effective 2D BIC | {metrics['bic_effective']:.8f} |\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(section)


async def run_galfit(
    config_file: Annotated[str, "absolute path to the GALFIT configuration file"],
    options: Annotated[List[str], "options that control how galfit runs"] | None = None,
    *,
    psf_area_method: PsfAreaMethod = "noise_equivalent",
) -> dict[str, Any]:
    """Run the unchanged production pipeline, then attach effective-BIC output."""

    controls = parse_feedme(config_file)
    result = await _production_run_galfit(config_file, options or [])
    if result.get("status") != "success":
        return result
    psf_file = controls.get("psf")
    if not psf_file:
        return {
            **result,
            "status": "failure",
            "error": "effective BIC requires a PSF file in feedme D)",
            "failure_stage": "effective_bic",
        }
    try:
        metrics = compute_effective_bic_from_files(
            result["optimized_fits_file"], psf_file, method=psf_area_method
        )
        summary_file = result.get("summary_file")
        _append_effective_bic_summary(summary_file, metrics)
        sidecar = Path(summary_file).with_name("effective_bic.json")
        sidecar.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            **result,
            "effective_bic": metrics,
            "effective_bic_file": str(sidecar),
        }
    except Exception as exc:
        return {
            **result,
            "status": "failure",
            "error": f"effective BIC failed: {type(exc).__name__}: {exc}",
            "failure_stage": "effective_bic",
        }
