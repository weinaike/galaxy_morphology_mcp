"""galfit (single-band GALFIT feedme) format backend for the beam engine.

P0 of the multi-band port (docs/design_mb_beam.md §0): the format-coupled
pieces of ``BeamGraph.init`` / ``record_fit`` are extracted verbatim into
this module so a sibling backend (``beam_mb.backend``, the GalfitS .lyric
mode) can plug into the same seams via ``meta["mode"]`` dispatch. Every
function here is a move, not a rewrite — galfit behaviour is unchanged.
"""

from __future__ import annotations

import os

MODE = "galfit"

# component NAMES that are just type strings = the galfit name-recovery
# fallback fired (input feedme carried no ``# STRUCTURE:`` names) — a
# record-time lesion (moved from graph._TYPE_NAMES_AS_NAMES)
TYPE_NAMES_AS_NAMES = {"sersic", "expdisk", "edgedisk", "psf", "ferrer",
                       "moffat", "gaussian", "king"}

# fit_statistics keys the graph records as the state's metrics
METRIC_KEYS = ("chi2_nu", "chisq1d_nu", "bic1d", "bic_eff", "sky_value",
               "psf_fwhm", "a_psf", "n_free", "n_data")


def parse_root(root_config: str) -> dict:
    """Root-config facts for ``BeamGraph.init``: ``fit_region`` (4-tuple or
    None) and the root ``inventory`` (normalized component dicts)."""
    from beam.cons_decode import number_components

    fit_region = None
    try:
        from tools.parse_feedme import parse_feedme
        header = parse_feedme(root_config)
        region = header.get("fit_region")
        if region and len(region) == 4:
            fit_region = tuple(float(v) for v in region)
    except Exception:
        header = {}
    inventory = list(number_components(root_config).values())
    return {"fit_region": fit_region, "inventory": inventory}


def parse_fit(run_result: dict) -> dict:
    """Fitted/input inventories + metrics from a run-tool success dict.

    galfit contract: ``run_result`` carries input_param_file /
    output_param_file (galfit.NN) / fit_statistics.
    """
    from beam.cons_decode import number_components

    fit_stats = dict(run_result.get("fit_statistics") or {})
    metrics = {k: fit_stats.get(k) for k in METRIC_KEYS}
    input_param_file = run_result.get("input_param_file", "")
    output_param_file = run_result.get("output_param_file", "")
    fitted_inv = list(number_components(output_param_file,
                                        name_file=input_param_file).values()) \
        if output_param_file else []
    input_inv = list(number_components(input_param_file).values()) \
        if input_param_file else []
    return {"fitted_inv": fitted_inv, "input_inv": input_inv,
            "metrics": metrics}


def record_guard(fitted_inv: list[dict], input_inv: list[dict],
                 context: str = "record") -> None:
    """Format-specific record-time lesion check; raises ValueError.

    galfit: names are recovered from the INPUT feedme's ``# STRUCTURE:``
    comments; a name-less input silently degrades every component to its
    type name (expdisk/sersic) — later exploding as E_PARAM_TYPE retry
    storms in survey (VLM retries can never fix a graph-side naming
    problem). Reject at record time instead. (A .lyric carries names
    in-block, so the mb backend has no equivalent guard.)
    """
    _bare = sorted({(c.get("name") or "?").lower()
                    for c in (fitted_inv or input_inv)
                    if (c.get("name") or "").lower() in TYPE_NAMES_AS_NAMES})
    if _bare:
        if context == "repair":
            tail = "fix the feedme's '# STRUCTURE:' names first"
        else:
            tail = ("Nothing recorded. Fix the input feedme's names and "
                    "re-record by reference (round_status_file=...)")
        raise ValueError(
            f"E_RECORD_TYPE_NAMES: fitted/input inventory carries bare type names "
            f"{_bare} — the input feedme lacks '# STRUCTURE:' semantic names (or "
            f"input_param_file was wrong). {tail}")


def decode_cons_effective(input_param_file: str, input_inv: list[dict]) -> dict:
    """Effective .cons bands ``{"<number>.<param>": (lo, hi)}`` for a feedme
    (moved verbatim from BeamGraph._decode_cons_for)."""
    if not input_param_file:
        return {}
    try:
        from tools.parse_feedme import parse_feedme
        cons_rel = parse_feedme(input_param_file).get("constraint")
    except Exception:
        return {}
    if not cons_rel or cons_rel.lower() == "none":
        return {}
    cons_file = cons_rel if os.path.isabs(cons_rel) else \
        os.path.join(os.path.dirname(os.path.abspath(input_param_file)), cons_rel)
    if not os.path.exists(cons_file):
        return {}
    from beam.cons_decode import decode_cons_file, effective_band

    decoded = decode_cons_file(cons_file)
    bands: dict[str, tuple[float, float]] = {}
    for row in decoded.numeric_rows():
        number = int(row.comp_spec)
        comp = next((c for c in input_inv if c.get("number") == number), None)
        if comp is None:
            continue
        input_value = comp.get({"re": "re", "n": "n", "q": "ba"}.get(row.param, row.param))
        if input_value is None:
            continue
        band = effective_band(row, float(input_value))
        if band:
            bands[f"{number}.{row.param}"] = band
    return bands


def artifacts(run_result: dict) -> dict:
    """State-node artifacts dict from a run-tool success dict (galfit keys)."""
    return {
        "feedme": run_result.get("input_param_file", ""),
        "galfit_nn": run_result.get("output_param_file", ""),
        "comparison_png": run_result.get("image_file", ""),
        "summary": run_result.get("summary_file", ""),
        "round_status": run_result.get("round_status_file", ""),
    }


def root_artifacts(root_config: str) -> dict:
    """Root-node artifacts dict for the unfitted A.0 state (galfit keys)."""
    return {"feedme": os.path.abspath(root_config)}
