"""Mechanical candidate -> .lyric transcriber for the multi-band engine (P3).

docs/design_mb_beam.md §5. Input: the parent lyric + the parent gssummary
(free AND fixed — warm-start material) + candidate primitives in the mb
unit contract (lengths in px tagged with ``panel_band``, PA in sky-PA).
Output: ``_iter{n}.lyric`` + (K>=2 central components) ``iter{n}.constrain``.

Discipline inherited from the galfit mode:

* **candidates declare INITIAL VALUES only** — the schema has no band
  field at all; every structural five-tuple carries the default bound set;
* **warm-start backfill**: every structural parameter NOT declared by the
  primitives takes the parent gssummary converged value as init (the
  "clean increment" contract — the VLM judged the parent's CONVERGED
  state, not its input guesses). Per-band fluxes (logNorm_*) have NO
  lyric row — GalfitS auto-initialises them at load time
  (``log10|imsum| - logM - log10(Ncomp)``, equal split), so fluxes are
  never warm-startable via the config (nor were they in the historical
  flow); the mb candidate schema therefore has no mag seeding channel;
* **type mandates** applied deterministically: disk n=1 fixed, bar n=0.5
  fixed, bulge n free from creation (single-band austerity decision);
* **strict invariants maintained as-we-write**: add/remove keep the Ga2
  membership in lockstep; the output is rejected unless check_lyric_file
  AND the no-dangling invariants pass.

Px -> arcsec: ``re_px * pixscale(panel_band)``; centre offsets go through
the WCS affine (exact inverse of GalfitS's coordinates_transfer) when the
band image is reachable, else the north-up fallback
(ra_off = -dx*s, dec_off = +dy*s). PA is sky-PA, written verbatim.

Serialization: the rebuilt lyric text is re-parsed through
``parse_lyric_doc`` — block registration, key/duplicate/grouping checks
and the strict invariants all run for free on the OUTPUT before it is
accepted.
"""

from __future__ import annotations

import os
import re
from typing import Any

from beam_mb.lyric_doc import LyricDoc, LyricError, parse_lyric_doc

# ---------------------------------------------------------------------------
# mandates & templates (values verified against the kilogas_mb corpus and
# GalfitS gsutils parsing)

# n-row five-tuples per structure name: fixed mandates; None = free n
N_MANDATE: dict[str, list | None] = {
    "disk": [1.0, 0.5, 2.0, 0.1, 0],
    "bar": [0.5, 0.4, 0.6, 0.1, 0],
    "bulge": None,          # free from creation
    "lens": None,
    "singlesersic": None,   # free (overall-concentration observable)
    "outerdisk": None,
}

# constant SED rows P9-P16, P26-P32 cloned into every new component
# (identical across the whole corpus)
SED_TEMPLATE: dict[int, Any] = {
    9: [[-2, -8, 0, 0.1, 0]] * 5,
    10: [0.0, 0.1, 0.5, 1.0, 2.0, 5.0],
    11: [[0.01, 0.001, 0.04, 0.001, 0]],
    12: [[0.7, 0, 5.1, 0.1, 0]],
    13: [100, 40, 200, 1, 0],
    14: [9.0, 6, 12, 0.1, 0],
    15: "bins",
    16: [-2, -4, -2, 0.1, 0],
    26: [3.0, 0, 5, 0.1, 0],
    27: 0,
    28: [8.14, 4.5, 10, 0.1, 0],
    29: [1.0, 0.1, 50, 0.1, 0],
    30: [1.0, 0.47, 7.32, 0.1, 0],
    31: [1.0, 1.0, 3.0, 0.1, 0],
    32: [0.10, 0, 1.0, 0.1, 0],
}

# AGN N-block template — the image-only anti-degeneracy configuration:
# Na10 fixed at the log L5100 prior, Na26 the SOLE free flux degree of
# freedom, Na11 fixed + Na18=4 (sparse wavelength coverage). Na1 (name)
# and Na4/Na5 (centre) are filled per candidate.
AGN_N_TEMPLATE: dict[int, Any] = {
    2: [0.0527, 0.01, 0.1, 0.001, 0],
    3: 0.0072,
    6: [7, 5, 10, 0.1, 0],
    7: [-1, -4, 2, 0.1, 0],
    8: [0, 0, 0.99, 0.01, 0],
    9: [0, 0, 4, 0.25, 0],
    10: [42, 41, 48, 0.1, 0],
    11: [1, 0, 4, 0.1, 0],
    12: [],
    13: [],
    14: 0, 15: 0, 16: 0, 17: 0,
    18: 4,
    19: [1, 0.5, 2, 0.05, 0],
    20: 0,
    21: [41, 39, 44, 0.1, 0],
    22: [-0.5, -2.5, -0.25, 0.05, 0],
    23: [0.5, 0.25, 1.5, 0.05, 0],
    24: [7, 5, 10, 0.5, 0],
    25: [15, 0, 90, 5, 0],
    26: [1.0, 0.2, 5.0, 0.1, 1],
}

CENTRAL_STRUCTURES = {"disk", "edgedisk", "bulge", "bar", "lens"}
_ROW_ORDER = list(range(1, 33))
_BARE_WORD = re.compile(r'^[A-Za-z_][A-Za-z0-9_.\-]*$')

# structural row index -> (param name, gssummary suffix)
_STRUCT: dict[int, tuple[str, str]] = {
    3: ("xcen", "xcen"), 4: ("ycen", "ycen"), 5: ("re", "Re"),
    6: ("n", "n"), 7: ("pa", "ang"), 8: ("q", "axrat"),
}


def _r4(v) -> float:
    return round(float(v), 4)


def _pixscale(bands_meta: dict, band: str) -> float | None:
    s = ((bands_meta or {}).get(band) or {}).get("pixscale")
    return float(s) if s else None


def _floor_arcsec(bands_meta: dict) -> float:
    """max(0.05, 0.1 x widest-band PSF FWHM in arcsec) — conservative."""
    fw = [float(e.get("fwhm_px")) * float(e.get("pixscale"))
          for e in (bands_meta or {}).values()
          if e.get("fwhm_px") and e.get("pixscale")]
    return round(max(0.05, 0.1 * max(fw)), 4) if fw else 0.05


def _half_region_arcsec(bands_meta: dict) -> float:
    for e in (bands_meta or {}).values():
        reg, s = e.get("region"), e.get("pixscale")
        if reg and len(reg) == 4 and s:
            return round(0.5 * (float(reg[1]) - float(reg[0])) * float(s), 4)
    return 45.0


def _image_path_of(doc: LyricDoc, band: str) -> str | None:
    """The band's science image path (Ia1), with stale-absolute-path
    remap by basename into the parent lyric's directory."""
    lbl = doc.label_of_band(band)
    img = doc.images.get(lbl or "")
    if img is None:
        return None
    v = img.value(1)
    path = None
    if isinstance(v, (list, tuple)) and v:
        path = str(v[0])
    elif isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            path = s[1:-1].split(",")[0].strip().strip("'\"")
        else:
            path = s.strip("'\"")
    if not path:
        return None
    if not os.path.exists(path):
        # doc.lines carry the parent's path context: use any raw line's dir
        for ln in doc.lines:
            if hasattr(ln, "raw") and ln.raw.startswith("R1)"):
                break
        base = doc._source_dir or "."          # set by _load_doc helper
        cand = os.path.join(base, os.path.basename(path))
        if os.path.exists(cand):
            return cand
    return path if os.path.exists(path) else path


def _offsets_arcsec(doc: LyricDoc, bands_meta: dict, band: str,
                    dx_px: float, dy_px: float) -> tuple[float, float] | None:
    """(ra_off, dec_off) arcsec for a centre-relative (dx, dy) in ``band`` px.

    WCS-affine path mirrors galfits coordinates_transfer (its exact
    inverse); the north-up pixscale fallback applies when the band image
    is unreachable: +x = west = -RA, +y = north = +dec.
    """
    path = _image_path_of(doc, band)
    if path and os.path.exists(path):
        try:
            from astropy.io import fits as _fits
            from astropy.wcs import WCS as _WCS

            with _fits.open(path) as hdul:
                w = _WCS(hdul[0].header)
            ra, dec = doc.center
            cx, cy = w.all_world2pix(ra, dec, 1)
            px2, py2 = w.all_world2pix(ra + 1 / 60.0, dec, 1)
            qx2, qy2 = w.all_world2pix(ra, dec + 1 / 60.0, 1)
            dxra, dyra = (px2 - cx) * 60.0, (py2 - cy) * 60.0
            dxdec, dydec = (qx2 - cx) * 60.0, (qy2 - cy) * 60.0
            det = dxra * dydec - dyra * dxdec
            if abs(det) < 1e-12:
                raise ValueError("degenerate WCS affine")
            ex, ey = float(dx_px), float(dy_px)
            dra = (ex * dydec - ey * dxdec) / det
            ddec = (ey * dxra - ex * dyra) / det
            return _r4(dra * 3600.0), _r4(ddec * 3600.0)
        except Exception:
            pass
    s = _pixscale(bands_meta, band)
    if s is None:
        return None
    return _r4(-float(dx_px) * s), _r4(float(dy_px) * s)


def _re_arcsec(bands_meta: dict, band: str, re_px: float) -> float | None:
    s = _pixscale(bands_meta, band)
    return _r4(float(re_px) * s) if s else None


def _render_row(family: str, label: str, idx: int, value: Any) -> str:
    key = f"{family}{label}{idx})"
    if isinstance(value, str):
        s = value if _BARE_WORD.match(value) else repr(value)
        return f"{key} {s}"
    return f"{key} {value!r}"


class _Comp:
    """Mutable working component (a P block under construction)."""

    def __init__(self, label: str, name: str, ptype: str,
                 rows: dict[int, Any]):
        self.label = label
        self.name = name
        self.ptype = ptype
        self.rows: dict[int, Any] = dict(rows)

    def five(self, idx: int) -> list | None:
        v = self.rows.get(idx)
        return list(v) if isinstance(v, (list, tuple)) and len(v) == 5 else None


class MbTranscriber:
    def __init__(self, parent_lyric: str, parent_gssummary: str | None,
                 bands_meta: dict | None):
        self.source_dir = os.path.dirname(os.path.abspath(parent_lyric))
        self.doc = parse_lyric_doc(parent_lyric)
        self.doc._source_dir = self.source_dir      # stale-path remap ctx
        from beam_mb.backend import _gssummary_params
        self.params = _gssummary_params(parent_gssummary) \
            if parent_gssummary and os.path.exists(parent_gssummary) else {}
        self.bands_meta = bands_meta or {}
        self.notes: list[str] = []
        self.comps: list[_Comp] = [
            _Comp(c.label, c.name, c.ptype, {i: c.value(i) for i in c.rows})
            for c in self.doc.components]
        self.agn_blocks: list[tuple[str, dict[int, Any]]] = [
            (lbl, {i: nb.value(i) for i in nb.rows})
            for lbl, nb in self.doc.nuclei.items()]
        self.main_galaxy = next(iter(self.doc.galaxies), None)
        self.declared: set[tuple[str, str]] = set()
        # warm-start source override for RENAMED components (the implicit
        # singlesersic->disk conversion: the renamed disk backfills the
        # parent's singlesersic_* fitted values, not disk_* which don't
        # exist in the parent gssummary)
        self._warm_source: dict[str, str] = {}

    # ------------------------------------------------------------ ops
    def _gss(self, name: str, suffix: str):
        return self.params.get(f"{name}_{suffix}")

    def _next_label(self) -> str:
        used = {c.label for c in self.comps} | \
               {lbl for lbl, _ in self.agn_blocks}
        for ch in "abcdefghijklmnopqrstuvwxyz":
            if ch not in used:
                return ch
        raise LyricError("out of component labels (a-z)")

    def _declared_offsets(self, p: dict) -> tuple[float, float] | None:
        band = p.get("panel_band")
        if not band:
            return None
        return _offsets_arcsec(self.doc, self.bands_meta, band,
                               float(p.get("dx_px") or 0.0),
                               float(p.get("dy_px") or 0.0))

    def _new_structural_rows(self, name: str, p: dict,
                             chained_subordinate: bool) -> dict[int, Any]:
        """Default-bound structural five-tuples for a NEW component."""
        rows: dict[int, Any] = {}
        floor = _floor_arcsec(self.bands_meta)
        half = _half_region_arcsec(self.bands_meta)
        off = self._declared_offsets(p)
        x0, y0 = off if off else (0.0, 0.0)
        rows[3] = [x0, x0 - 10, x0 + 10, 0.13, 0 if chained_subordinate else 1]
        rows[4] = [y0, y0 - 10, y0 + 10, 0.13, 0 if chained_subordinate else 1]
        re0 = None
        if p.get("re_px") is not None and p.get("panel_band"):
            re0 = _re_arcsec(self.bands_meta, p["panel_band"], p["re_px"])
        rows[5] = [re0 if re0 is not None else max(floor, 1.0), floor, half,
                   0.08, 1]
        nman = N_MANDATE.get(name)
        if nman is not None:
            rows[6] = list(nman)
        elif p.get("n") is not None:
            v = float(p["n"])
            rows[6] = [v, 0.1, 8.0, 0.1, 1]
        else:
            rows[6] = [4.0 if name == "bulge" else 1.0, 0.1, 8.0, 0.1, 1]
        rows[7] = [float(p.get("pa_sky_deg") or 0.0), -180, 180, 1, 1]
        rows[8] = [float(p.get("q") or 0.7), 0.05, 1.0, 0.02, 1]
        return rows

    def _fourier_rows(self, p: dict) -> dict[int, Any]:
        return {17: [3.0, 0.3, 4.0, 0.1, 1],
                18: [8.0, 3.0, 20.0, 0.1, 1],
                19: [0.7, 0.5, 3.0, 0.1, 1],
                20: [90.0, 30, 130, 1, 1],
                21: 1,
                22: [0.05, 0.0, 0.5, 0.01, 1],
                23: [float(p.get("pa_sky_deg") or 0.0), 0, 180, 1, 1],
                24: [55.0, 0, 90, 1, 1]}

    def _mark_declared(self, name: str, p: dict) -> None:
        for prm in ("re", "n", "pa", "q", "xcen", "ycen"):
            src = {"re": "re_px", "xcen": "dx_px", "ycen": "dy_px"}.get(prm)
            if (src and p.get(src) is not None) or \
                    (not src and p.get(prm) is not None):
                self.declared.add((name, prm))

    def op_skeleton(self, components: list[dict]) -> None:
        """Opening skeletons: REPLACE the component set (root obj0 -> the
        declared structures). Labels reallocated a.. in declared order."""
        if self.agn_blocks:
            raise LyricError("skeleton op with existing N blocks is invalid")
        central = [c for c in components
                   if (c.get("structure_name") or "") in CENTRAL_STRUCTURES]
        comps: list[_Comp] = []
        for i, c in enumerate(components):
            name = c.get("structure_name") or ""
            if not name:
                raise LyricError("skeleton component missing structure_name")
            ptype = c.get("component_type") or "sersic"
            chained = (len(central) >= 2
                       and name in CENTRAL_STRUCTURES and name != "disk")
            rows = self._new_structural_rows(name, c, chained)
            rows.update(dict(SED_TEMPLATE))
            if ptype == "sersic_f":
                rows.update(self._fourier_rows(c))
            comps.append(_Comp(chr(ord("a") + i), name, ptype, rows))
            self._mark_declared(name, c)
        self.comps = comps

    def op_add(self, p: dict) -> None:
        name = p.get("structure_name") or ""
        if not name:
            raise LyricError("add op needs structure_name")
        if name == "agn":
            self._add_agn(p)
            return
        # ---- implicit singlesersic -> multi transition: the FIRST add of
        # a central component converts the parent's singlesersic into the
        # disk (rename + n=1-fixed mandate; its fitted/initial light
        # parameters are carried). add(disk) IS the conversion itself.
        if name in CENTRAL_STRUCTURES:
            ss = next((c for c in self.comps if c.name == "singlesersic"),
                      None)
            if ss is not None:
                ss.name = "disk"
                ss.rows[6] = list(N_MANDATE["disk"])
                self._warm_source["disk"] = "singlesersic"
                self.notes.append(
                    "singlesersic -> disk conversion (first central add): "
                    "renamed, n=1 fixed by mandate")
                if name == "disk":
                    # the add's declared params re-seed the converted disk
                    new_rows = self._new_structural_rows("disk", p, False)
                    for idx in (3, 4, 5, 7, 8):
                        ss.rows[idx] = new_rows[idx]
                    self._mark_declared("disk", p)
                    return
        if any(c.name == name for c in self.comps):
            raise LyricError(f"component {name!r} already exists")
        central_n = sum(1 for c in self.comps
                        if c.name in CENTRAL_STRUCTURES)
        if name in CENTRAL_STRUCTURES:
            central_n += 1
        chained = central_n >= 2 and name != "disk"
        rows = self._new_structural_rows(name, p, chained)
        rows.update(dict(SED_TEMPLATE))
        if (p.get("component_type") or "sersic") == "sersic_f":
            rows.update(self._fourier_rows(p))
        self.comps.append(_Comp(self._next_label(), name,
                                p.get("component_type") or "sersic", rows))
        self._mark_declared(name, p)

    def _add_agn(self, p: dict) -> None:
        nname = p.get("agn_name") or "AGN"
        rows = dict(AGN_N_TEMPLATE)
        rows[1] = nname
        off = self._declared_offsets(p)
        x0, y0 = off if off else (0.0, 0.0)
        rows[4] = [x0, x0 - 1, x0 + 1, 0.01, 1]
        rows[5] = [y0, y0 - 1, y0 + 1, 0.01, 1]
        lbl = self.main_galaxy or self._next_label()
        if any(l == lbl for l, _ in self.agn_blocks):
            raise LyricError("an AGN block already exists on this galaxy")
        self.agn_blocks.append((lbl, rows))

    def op_remove(self, target: str) -> None:
        if target == "agn":
            if not self.agn_blocks:
                raise LyricError("no AGN block to remove")
            self.agn_blocks.pop()
            return
        keep = [c for c in self.comps if c.name != target]
        if len(keep) == len(self.comps):
            raise LyricError(f"remove: unknown target {target!r}")
        self.comps = keep

    def op_tune(self, p: dict) -> None:
        target = p.get("target") or ""
        comp = next((c for c in self.comps if c.name == target), None)
        if comp is None:
            raise LyricError(f"tune: unknown target {target!r}")
        params = p.get("params") or {}
        band = params.get("panel_band") or p.get("panel_band") or ""
        if params.get("re_px") is not None and band:
            v = _re_arcsec(self.bands_meta, band, params["re_px"])
            f = comp.five(5)
            comp.rows[5] = [v if v is not None else (f or [1.0])[0],
                            f[1] if f else _floor_arcsec(self.bands_meta),
                            f[2] if f else _half_region_arcsec(self.bands_meta),
                            0.08, f[4] if f else 1]
            self.declared.add((target, "re"))
        if params.get("n") is not None:
            if N_MANDATE.get(target) is not None:
                raise LyricError(
                    f"tune: {target} n is type-mandated "
                    f"({N_MANDATE[target][0]} fixed) and cannot be tuned")
            comp.rows[6] = [float(params["n"]), 0.1, 8.0, 0.1, 1]
            self.declared.add((target, "n"))
        if params.get("q") is not None:
            f = comp.five(8) or [0.7, 0.05, 1.0, 0.02, 1]
            comp.rows[8] = [float(params["q"]), f[1], f[2], f[3], f[4]]
            self.declared.add((target, "q"))
        if params.get("pa_sky_deg") is not None:
            f = comp.five(7) or [0.0, -180, 180, 1, 1]
            comp.rows[7] = [float(params["pa_sky_deg"]), f[1], f[2], f[3], f[4]]
            self.declared.add((target, "pa"))
        if (params.get("dx_px") is not None
                or params.get("dy_px") is not None) and band:
            off = _offsets_arcsec(self.doc, self.bands_meta, band,
                                  float(params.get("dx_px") or 0.0),
                                  float(params.get("dy_px") or 0.0))
            if off:
                fx = comp.five(3) or [0.0, -10, 10, 0.13, 1]
                fy = comp.five(4) or [0.0, -10, 10, 0.13, 1]
                comp.rows[3] = [off[0], fx[1], fx[2], fx[3], fx[4]]
                comp.rows[4] = [off[1], fy[1], fy[2], fy[3], fy[4]]
                self.declared.add((target, "xcen"))
                self.declared.add((target, "ycen"))

    def op_reclassify(self, mapping: list[dict]) -> None:
        for m in mapping:
            src, dst = m.get("from") or "", m.get("to") or ""
            comp = next((c for c in self.comps if c.name == src), None)
            if comp is None:
                raise LyricError(f"reclassify: unknown source {src!r}")
            if any(c.name == dst for c in self.comps if c is not comp):
                raise LyricError(f"reclassify: target name {dst!r} occupied")
            comp.name = dst
            man = N_MANDATE.get(dst)
            if man is not None:
                comp.rows[6] = list(man)
            else:      # released n keeps the parent's FITTED value as init
                fitted = self._gss(src, "n")
                f = comp.five(6) or [4.0, 0.1, 8.0, 0.1, 1]
                comp.rows[6] = [_r4(fitted) if fitted is not None else f[0],
                                0.1, 8.0, 0.1, 1]
            self.declared.add((dst, "n"))

    # --------------------------------------------------- default bands
    def apply_default_bands(self) -> None:
        """Every round, EVERY kept structural five-tuple carries the default
        bound set (the galfit discipline: candidates declare initial values
        only; guiding happens through inits, misbehaviour is caught by the
        mech checks — a transcription inheriting a parent's tight band
        would pin the new init at the parent's boundary)."""

        floor = _floor_arcsec(self.bands_meta)
        half = _half_region_arcsec(self.bands_meta)
        central = [c.name for c in self.comps
                   if c.name in CENTRAL_STRUCTURES]
        anchor = next((c.name for c in self.comps if c.name == "disk"), None) \
            or (central[0] if central else None)
        for comp in self.comps:
            subordinate = comp.name != anchor
            for idx, (prm, _suf) in _STRUCT.items():
                f = comp.five(idx)
                if f is None:
                    continue
                init, step, vary = f[0], f[3], f[4]
                if idx in (3, 4):                     # centres
                    lo, hi, st = init - 10, init + 10, 0.13
                    vary = 0 if (subordinate and comp.name in CENTRAL_STRUCTURES) \
                        else 1
                elif idx == 5:
                    lo, hi, st = floor, half, 0.08
                elif idx == 6:
                    man = N_MANDATE.get(comp.name)
                    if man is not None:
                        comp.rows[6] = list(man)
                        continue
                    lo, hi, st = 0.1, 8.0, 0.1
                elif idx == 7:
                    lo, hi, st = -180, 180, 1
                else:                                  # q
                    lo, hi, st = 0.05, 1.0, 0.02
                comp.rows[idx] = [_r4(init), _r4(lo), _r4(hi), st, vary]

    # ------------------------------------------------------- warm start
    def warm_start(self) -> None:
        """Undeclared structural inits <- parent gssummary converged values
        (fitted n keeps its mandate vary-flag: a mandated-fixed row never
        frees, and a mandated-free row takes [fitted, 0.1, 8, 0.1, 1])."""
        for comp in self.comps:
            lookup = self._warm_source.get(comp.name, comp.name)
            for idx, (prm, suf) in _STRUCT.items():
                if (comp.name, prm) in self.declared:
                    continue
                fitted = self._gss(lookup, suf)
                if fitted is None:
                    continue
                f = comp.five(idx)
                if f is None:
                    continue
                man = N_MANDATE.get(comp.name)
                if idx == 6 and man is None and f[4] == 0:
                    # n released by mandate (e.g. historical fixed-4 bulge):
                    # free it at the fitted value
                    comp.rows[6] = [_r4(fitted), 0.1, 8.0, 0.1, 1]
                    self.notes.append(
                        f"{comp.name}: n released at fitted {fitted:.3f} "
                        f"(mandate: free from creation)")
                    continue
                comp.rows[idx] = [_r4(fitted), f[1], f[2], f[3], f[4]]

    # ------------------------------------------------------------ render
    def render(self) -> str:
        """Rebuilt lyric text: R/I/S/A/F lines verbatim from the parent,
        fresh P/N blocks, G blocks with updated membership."""
        out: list[str] = []
        # parent lines for families we do not touch, in original order
        for ln in self.doc.lines:
            if isinstance(ln, str):
                out.append(ln)
            elif ln.family in ("R", "I", "S", "A", "F"):
                out.append(ln.render())
        # P blocks
        for comp in self.comps:
            out.append(_render_row("P", comp.label, 1, comp.name))
            out.append(_render_row("P", comp.label, 2, comp.ptype))
            for i in _ROW_ORDER:
                if i in (1, 2) or i not in comp.rows:
                    continue
                out.append(_render_row("P", comp.label, i, comp.rows[i]))
        # N blocks
        for lbl, rows in self.agn_blocks:
            for i in range(1, 28):
                if i in rows:
                    out.append(_render_row("N", lbl, i, rows[i]))
        # G blocks: main galaxy gets the new membership; others verbatim
        main = self.main_galaxy or "a"
        for lbl, gb in self.doc.galaxies.items():
            members = ([c.label for c in self.comps] if lbl == main
                       else list(gb.value(2) or []))
            for i in range(1, 8):
                if i == 2:
                    val = members
                elif gb.row(i) is not None:
                    val = gb.value(i)
                elif i == 1:
                    val = lbl
                else:
                    continue
                out.append(_render_row("G", lbl, i, val))
        if not self.doc.galaxies:
            for i, val in ((1, "a"), (2, [c.label for c in self.comps]),
                           (3, [0.05267, 0.01, 12, 0.01, 0]), (4, 0.032),
                           (5, [1.0, 0.8, 1.2, 0.05, 0]), (6, []), (7, 1)):
                out.append(_render_row("G", "a", i, val))
        return "\n".join(out) + "\n"

    def constrain_text(self) -> str | None:
        """Update_Constraints source binding main-galaxy central components
        (+ AGN) to the disk anchor; None when K <= 1 and no AGN."""
        central = [c.name for c in self.comps if c.name in CENTRAL_STRUCTURES]
        anchor = next((c.name for c in self.comps if c.name == "disk"), None) \
            or (central[0] if central else None)
        if anchor is None and not self.agn_blocks:
            return None
        lines: list[str] = []
        for n in central:
            if n == anchor:
                continue
            lines.append(f"    pardictlc['{n}_xcen'] = 1 * "
                         f"pardictlc['{anchor}_xcen']")
            lines.append(f"    pardictlc['{n}_ycen'] = 1 * "
                         f"pardictlc['{anchor}_ycen']")
        for lbl, rows in self.agn_blocks:
            nm = rows.get(1, "AGN")
            lines.append(f"    pardictlc['xcen_{nm}'] = 1 * "
                         f"pardictlc['{anchor}_xcen']")
            lines.append(f"    pardictlc['ycen_{nm}'] = 1 * "
                         f"pardictlc['{anchor}_ycen']")
        if not lines:
            return None
        return ("def Update_Constraints(pardictlc):\n"
                + "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# entry point

def transcribe_mb(parent_lyric: str, parent_gssummary: str | None,
                  primitives: list[dict], out_lyric: str,
                  bands_meta: dict | None = None,
                  constrain_path: str | None = None) -> dict:
    """Apply ``primitives`` onto the parent state, warm-start, write the
    child lyric (+ .constrain when concentric), validate, return a report.

    ``out_lyric``      target path (``_iter{n}.lyric``);
    ``constrain_path`` target path for ``iter{n}.constrain`` (written only
                       when a concentric binding is required; defaults to
                       the same dir/stem with .constrain);
    ``bands_meta``     {band: {pixscale, fwhm_px, region}} (graph meta).
    """
    try:
        t = MbTranscriber(parent_lyric, parent_gssummary, bands_meta)
        for p in primitives or []:
            op = (p or {}).get("op") or ""
            if op == "skeleton":
                t.op_skeleton(p.get("components") or [])
            elif op == "add":
                t.op_add(p)
            elif op == "remove":
                t.op_remove(p.get("target") or "")
            elif op == "tune":
                t.op_tune(p)
            elif op == "reclassify":
                t.op_reclassify((p.get("reclassify") or {}).get("map") or [])
            else:
                raise LyricError(f"unknown op {op!r}")
        t.warm_start()
        t.apply_default_bands()

        text = t.render()
        doc = parse_lyric_doc(text)          # re-parse: free registration
        errs = doc.strict_invariant_errors()
        if errs:
            return {"status": "failure",
                    "error": "transcribed lyric violates strict invariants",
                    "errors": errs, "notes": t.notes}

        os.makedirs(os.path.dirname(os.path.abspath(out_lyric)), exist_ok=True)
        with open(out_lyric, "w", encoding="utf-8") as f:
            f.write(text)

        # static gate (check_lyric_file validates five-tuples, identifiers,
        # section ranges — everything the strict invariants do not)
        from tools.modify_lyric import check_lyric_file
        check = check_lyric_file(out_lyric)
        if check.get("status") == "failure":
            os.remove(out_lyric)
            return {"status": "failure",
                    "error": f"check_lyric_file rejected the output: "
                             f"{check.get('message', '')[:400]}",
                    "notes": t.notes}

        constrain_file = None
        ctext = t.constrain_text()
        if ctext:
            if constrain_path is None:
                stem = os.path.splitext(os.path.basename(out_lyric))[0]
                constrain_path = os.path.join(
                    os.path.dirname(os.path.abspath(out_lyric)),
                    re.sub(r"^_iter", "iter", stem) + ".constrain")
            with open(constrain_path, "w", encoding="utf-8") as f:
                f.write(ctext)
            constrain_file = constrain_path

        return {"status": "success", "lyric": out_lyric,
                "constrain": constrain_file, "notes": t.notes,
                "inventory": [(c.label, c.name, c.ptype) for c in t.comps],
                "agn_blocks": [lbl for lbl, _ in t.agn_blocks]}
    except (LyricError, ValueError) as e:
        return {"status": "failure", "error": str(e)}
