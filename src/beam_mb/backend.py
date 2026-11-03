"""galfits (multi-band .lyric) format backend for the beam engine.

P2 implementation (docs/design_mb_beam.md §2-§3). The engine-facing
run-result contract mirrors the galfit one so ``BeamGraph.record_fit``
stays format-neutral:

* ``input_param_file``  = the input ``_iter{n}.lyric`` (names / order /
  bands / five-tuple bands / vary flags);
* ``output_param_file`` = the fitted ``.gssummary`` (free AND fixed
  parameters — the fixed section is mandatory warm-start material);
* ``fit_statistics``    = ``{bic, reduced_chisq, chi2_nu, n_free, n_data,
  per_band_chisq}`` (``bic`` is the gssummary global BIC, k included —
  ranked first by ``graph._metric_key``).

Unit contract (design doc §2): the normalized inventory is **arcsec-
canonical** — ``re``/``x``/``y`` are arcsec (``x``/``y`` the offsets
relative to the R2 centre, exactly the ``Pa3/Pa4`` semantics), ``pa`` is
**sky-PA** (the ``Pa7`` convention, read against the panel compass).
Per-band magnitudes ride on ``mags{band}`` via the constrain-file formula
``mag = zp(Ia10) - 2.5*(logM + logNorm)`` (verified on KILOGAS_110
iter3: disk decam_g = 22.5 - 2.5*9.0 - 2.5*(-6.5196) = 16.299).

An N block maps to a pseudo-component ``{name: "agn", type: "psf"}``
(the galfit inventory's AGN shape — the shared physicality onion/centre
checks key on it); its centre comes from the lmfit names
``xcen_<Na1>/ycen_<Na1>`` (the gssummary spelling, e.g. ``xcen_AGN``).
"""

from __future__ import annotations

import os
from typing import Any

from beam_mb.lyric_doc import LyricDoc, LyricError, parse_lyric_doc

MODE = "galfits"

# metrics recorded on every state node (chi2_nu = reduced chisq alias so
# the galfit-era digests keep reading)
METRIC_KEYS = ("bic", "reduced_chisq", "chi2_nu", "n_free", "n_data",
               "per_band_chisq")

# structural row index -> param name (the cons_effective / toggles spine)
_STRUCT_ROWS = ((3, "xcen"), (4, "ycen"), (5, "re"), (6, "n"), (7, "pa"),
                (8, "q"))
# per-type slot -> lmfit suffix for fitted values (gsutils naming)
_SLOT_SUFFIX = {"re": "Re", "n": "n", "pa": "ang", "q": "axrat",
                "r_in": "r_in", "r_out": "r_out", "alpha_rc": "alpha_rc",
                "theta_out": "theta_out", "am": "am", "theta_m": "theta_m",
                "i_arm": "i_arm", "rs": "rs", "hs": "hs", "r_out_f": "Rout",
                "alpha": "alpha", "beta": "beta", "r0": "r0", "sig": "sig"}


def _gssummary_params(summary_file: str) -> dict[str, float]:
    """Flat {pname: value} from a .gssummary (free + fixed sections)."""
    if not summary_file or not os.path.exists(summary_file):
        return {}
    try:
        from tools.parse_lyric import parse_gssummary
    except Exception:                       # pragma: no cover - astropy env
        return _parse_gssummary_local(summary_file)
    params, _cfg = parse_gssummary(summary_file)
    return params or {}


def _parse_gssummary_local(summary_file: str) -> dict[str, float]:
    """Minimal astropy-free fallback (mirrors parse_gssummary)."""
    params: dict[str, float] = {}
    capture = False
    with open(summary_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# free parameters") or \
                    line.startswith("# fixed parameters"):
                capture = True
                continue
            if line.startswith("#"):
                continue
            parts = line.split()
            if capture and len(parts) == 2:
                try:
                    params[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return params


def _bands_of(doc: LyricDoc, params: dict | None) -> list[str]:
    """Assembled bands in Aa2 order (fall back to the logNorm suffixes)."""
    bands = doc.band_names
    if not bands and params:
        for k in params:
            if k.startswith("logNorm_"):
                bands.append(k.rsplit("_", 1)[1])
    return bands


def _zp_map(doc: LyricDoc) -> dict[str, float]:
    """{band: zeropoint} from the I blocks' Ia10 rows."""
    zp: dict[str, float] = {}
    for lbl, img in doc.images.items():
        band = img.value(2)
        v = img.value(10)
        if band is not None and isinstance(v, (int, float)):
            zp[str(band)] = float(v)
    return zp


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _inventory_from_doc(doc: LyricDoc, params: dict | None) -> list[dict]:
    """Normalized inventory (arcsec/sky-PA). ``params`` None = initial
    values from the lyric five-tuples (the A.0 / input view); a gssummary
    dict = the fitted view."""
    fitted = params is not None
    bands = _bands_of(doc, params)
    zp = _zp_map(doc)
    ref_band = bands[0] if bands else None
    inv: list[dict] = []

    for c in doc.components:
        name, ptype = c.name, c.ptype
        entry: dict[str, Any] = {
            "name": name, "type": ptype,
            "label": c.label, "number": c.label,
        }
        toggles: dict[str, int] = {}
        if fitted:
            entry["x"] = _f(params.get(f"{name}_xcen"))
            entry["y"] = _f(params.get(f"{name}_ycen"))
            for slot in ("re", "n", "pa", "q"):
                entry[slot] = _f(params.get(f"{name}_{_SLOT_SUFFIX[slot]}"))
        else:
            five = {p: c.slot_five(p) for p in ("re", "n", "pa", "q")}
            entry["x"] = _f((c.value(3) or [None])[0])
            entry["y"] = _f((c.value(4) or [None])[0])
            entry["re"] = _f(five["re"][0]) if five["re"] else None
            entry["n"] = _f(five["n"][0]) if five["n"] else None
            entry["pa"] = _f(five["pa"][0]) if five["pa"] else None
            entry["q"] = _f(five["q"][0]) if five["q"] else None
        # re_effective: sersic Re IS the effective radius (mb has no expdisk
        # Rs=Re/1.68 conversion — that factor belongs to the galfit mode)
        entry["re_effective"] = entry.get("re")
        # vary flags from the INPUT lyric five-tuples (both views)
        for rowidx, pname in _STRUCT_ROWS:
            v = c.value(rowidx)
            if isinstance(v, (list, tuple)) and len(v) == 5:
                try:
                    toggles[pname] = int(bool(v[4]))
                except (TypeError, ValueError):
                    pass
        # per-band mags from the constrain-file formula (fitted view only)
        mags: dict[str, float] = {}
        if fitted:
            logm = params.get(f"logM_{name}")
            logm = _f(logm)
            if logm is None:
                logm = _f((c.value(14) or [None])[0])
            for band in bands:
                lognorm = _f(params.get(f"logNorm_{name}_{band}"))
                if lognorm is not None and logm is not None and band in zp:
                    mags[band] = zp[band] - 2.5 * (logm + lognorm)
        entry["mags"] = mags
        entry["mag"] = mags.get(ref_band)
        entry["toggles"] = toggles
        inv.append(entry)

    # ---- N blocks -> 'agn' pseudo-components (the galfit psf shape)
    for lbl, nb in doc.nuclei.items():
        nname = str(nb.value(1) or "agn")
        entry: dict[str, Any] = {
            "name": nname.lower(), "type": "psf",
            "label": lbl, "number": lbl,
            "re": None, "re_effective": None, "n": None, "q": None,
            "pa": None, "mags": {}, "mag": None,
        }
        if fitted:
            entry["x"] = _f(params.get(f"xcen_{nname}"))
            entry["y"] = _f(params.get(f"ycen_{nname}"))
        else:
            entry["x"] = _f((nb.value(4) or [None])[0])
            entry["y"] = _f((nb.value(5) or [None])[0])
        toggles = {}
        for rowidx in (4, 5):
            v = nb.value(rowidx)
            if isinstance(v, (list, tuple)) and len(v) == 5:
                try:
                    toggles["xcen" if rowidx == 4 else "ycen"] = int(bool(v[4]))
                except (TypeError, ValueError):
                    pass
        entry["toggles"] = toggles
        inv.append(entry)
    return inv


def parse_root(root_config: str) -> dict:
    """Root-lyric facts for ``BeamGraph.init`` (arcsec inventory from the
    initial five-tuples; strict invariants enforced — a pathological root
    like the KILOGAS_110 _iter7 band-dropping lyric fails loudly)."""
    doc = parse_lyric_doc(root_config)
    errs = doc.strict_invariant_errors()
    if errs:
        raise LyricError(
            f"root lyric violates the strict invariants: {'; '.join(errs)}")
    return {"fit_region": None,
            "inventory": _inventory_from_doc(doc, None),
            "bands": doc.band_names,
            "center": doc.center}


def _fit_stats(run_result: dict) -> dict:
    stats = dict(run_result.get("fit_statistics") or {})
    if not stats:                       # run_galfits' flat success dict
        for k in ("bic", "reduced_chisq", "per_band_chisq"):
            if run_result.get(k) is not None:
                stats[k] = run_result[k]
    if stats.get("reduced_chisq") is not None and \
            stats.get("chi2_nu") is None:
        stats["chi2_nu"] = stats["reduced_chisq"]
    return stats


def parse_fit(run_result: dict) -> dict:
    """Fitted/input inventories + metrics from an mb run-result dict."""
    lyric = run_result.get("input_param_file", "")
    gssummary = run_result.get("output_param_file", "")
    doc = parse_lyric_doc(lyric) if lyric and os.path.exists(lyric) else None
    if doc is None:
        raise LyricError(
            f"input lyric missing or unreadable: {lyric!r} — the mb record "
            "contract needs input_param_file=<lyric>")
    params = _gssummary_params(gssummary)
    stats = _fit_stats(run_result)
    metrics = {k: stats.get(k) for k in METRIC_KEYS}
    return {
        "fitted_inv": _inventory_from_doc(doc, params or None),
        "input_inv": _inventory_from_doc(doc, None),
        "metrics": metrics,
    }


def record_guard(fitted_inv: list[dict], input_inv: list[dict],
                 context: str = "record") -> None:
    # .lyric names live in-block (P*1 rows) — the galfit bare-name lesion
    # cannot occur; structural problems were rejected at parse time.
    return None


def decode_cons_effective(input_param_file: str, input_inv: list[dict]) -> dict:
    """Five-tuple bands ``{"<label>.<param>": (lo, hi)}`` straight from the
    input lyric (the mb replacement for the .cons decoder — bounds live
    in-block by construction)."""
    if not input_param_file or not os.path.exists(input_param_file):
        return {}
    doc = parse_lyric_doc(input_param_file)
    bands: dict[str, tuple] = {}

    def _absorb(block, rows):
        for rowidx, pname in rows:
            v = block.value(rowidx)
            if isinstance(v, (list, tuple)) and len(v) == 5 and v[1] != v[2]:
                bands[f"{block.label}.{pname}"] = (v[1], v[2])

    for c in doc.components:
        _absorb(c, _STRUCT_ROWS)
        for slot, rowidx in ((s, r) for s, r in (
                ("r_in", 17), ("r_out", 18), ("alpha_rc", 19),
                ("theta_out", 20), ("am", 22), ("theta_m", 23),
                ("i_arm", 24), ("beta", 25))):
            if c.row(rowidx) is not None:
                _absorb(c, ((rowidx, slot),))
    for nb in doc.nuclei.values():
        _absorb(nb, ((4, "xcen"), (5, "ycen")))
    return bands


def artifacts(run_result: dict) -> dict:
    """State-node artifacts dict (mb keys; component_attributes discovered
    opportunistically next to the gssummary — it is not a guaranteed
    run_galfits product, 41/49 in the historical corpus)."""
    gssummary = run_result.get("output_param_file", "")
    comp_attr = ""
    if gssummary:
        cand = os.path.join(os.path.dirname(os.path.abspath(gssummary)),
                            "component_attributes.txt")
        if os.path.exists(cand):
            comp_attr = cand
    return {
        "lyric": run_result.get("input_param_file", ""),
        "gssummary": gssummary,
        "component_attributes": comp_attr,
        "comparison_png": run_result.get("image_file", ""),
        "round_status": run_result.get("round_status_file", ""),
    }


def root_artifacts(root_config: str) -> dict:
    return {"lyric": os.path.abspath(root_config)}
