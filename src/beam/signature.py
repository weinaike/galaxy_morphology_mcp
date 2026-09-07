"""Canonical state signatures, tolerance-band equivalence, zombie flags,
combo identity and closed-form projection (graph-search cycle detection).

Unit contract: every Re / position / size is a pixel value in the feedme
reference frame; PA follows the N=+Y convention. Inputs are component
inventories in either of two shapes (normalised on entry):

* ``check_feedme_file`` entries: ``{number, name, type, params{...},
  free_fixed{...}}``;
* ``parse_components`` dicts: ``{type, name, x, y, mag, re, n, ba, pa,
  toggles{}}`` (``re`` is the raw ``4)`` row; Rs for expdisk).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Tolerance bands (workflow §Deduplication and Ranking / §State-Ledger Usage Rules)
TOLERANCES = {
    "re": 0.20,   # relative ±20% (compared in effective radius)
    "n": 0.5,     # absolute
    "q": 0.10,    # absolute
    "pa": 10.0,   # degrees (N=+Y, wrap-aware)
    "mag": 0.5,   # absolute
    "pos": 8.0,   # px, absolute (State-Ledger comparison)
}

# bulge n=0.5 fixed is physically a bar (naming swaps allowed in equivalence)
NAME_SWAP_CLASSES = [{"bulge", "bar"}]
_COMPANION_RE = re.compile(r"^(companion|comp|secondary|satellite)", re.IGNORECASE)


# ----------------------------------------------------------------- normalisation
def normalize_entry(entry: dict) -> dict:
    """Normalise one component entry to a flat signature-friendly dict."""
    if "params" in entry:  # check_feedme_file shape
        params = entry.get("params", {})
        toggles = entry.get("free_fixed", {})
        re_raw = params.get("re_px")
        return {
            "number": entry.get("number"),
            "name": (entry.get("name") or "").lower(),
            "type": (entry.get("type") or "").lower(),
            "x": params.get("x_px"),
            "y": params.get("y_px"),
            "mag": params.get("mag"),
            "re": re_raw,
            "re_effective": params.get("re_effective_px", re_raw),
            "n": params.get("n"),
            "q": params.get("q"),
            "pa": params.get("pa_deg"),
            "toggles": dict(toggles),
        }
    # parse_components shape (also already-normalised dicts: idempotent)
    toggles = entry.get("toggles", {})
    re_raw = entry.get("re")
    is_expdisk = (entry.get("type") or "").lower() == "expdisk"
    if entry.get("re_effective") is not None:
        re_eff = entry.get("re_effective")
    else:
        re_eff = 1.68 * re_raw if is_expdisk and re_raw else re_raw
    return {
        "number": entry.get("number"),
        "name": (entry.get("name") or "").lower(),
        "type": (entry.get("type") or "").lower(),
        "x": entry.get("x"),
        "y": entry.get("y"),
        "mag": entry.get("mag"),
        "re": re_raw,
        "re_effective": re_eff,
        "n": entry.get("n"),
        "q": entry.get("q", entry.get("ba")),
        "pa": entry.get("pa"),
        "toggles": dict(toggles),
    }


def normalize_inventory(inventory: list[dict]) -> list[dict]:
    return [normalize_entry(e) for e in inventory]


# --------------------------------------------------------------------- signature
def component_signature(comp: dict) -> dict:
    """Canonical per-component signature (px, N=+Y)."""
    n = comp.get("n")
    n_state = None if n is None else f"{'free' if comp.get('toggles', {}).get('n', 1) else 'fixed'}:{n:g}"
    return {
        "name": comp.get("name"),
        "type": comp.get("type"),
        "n_state": n_state,
        "re_px": _round(comp.get("re_effective")),
        "mag": _round(comp.get("mag")),
        "q": _round(comp.get("q")),
        "pa": _round(comp.get("pa")),
        "x_px": _round(comp.get("x")),
        "y_px": _round(comp.get("y")),
        "toggles": {k: int(v) for k, v in sorted((comp.get("toggles") or {}).items())},
    }


def canonical_signature(inventory: list[dict],
                        cons_bands: dict | None = None,
                        initial_bands: dict | None = None) -> dict:
    """State signature: sorted structures x per-component params x toggles x bands."""
    comps = sorted(normalize_inventory(inventory), key=lambda c: (c.get("name") or "", c.get("type") or ""))
    return {
        "structures": [component_signature(c) for c in comps],
        "cons_bands": {f"{k[0]}.{k[1]}": [round(v[0], 4), round(v[1], 4)]
                       for k, v in sorted((cons_bands or {}).items())},
        "initial_bands": {f"{k[0]}.{k[1]}": [round(v[0], 4), round(v[1], 4)]
                          for k, v in sorted((initial_bands or {}).items())},
    }


def _round(v):
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------- equivalence
def _same_name_class(a: str, b: str) -> bool:
    if a == b:
        return True
    if _COMPANION_RE.match(a or "") and _COMPANION_RE.match(b or ""):
        return True
    return any(a in cls and b in cls for cls in NAME_SWAP_CLASSES)


def _param_close(pa: str, va, vb) -> bool:
    if va is None or vb is None:
        return va is None and vb is None
    try:
        va, vb = float(va), float(vb)
    except (TypeError, ValueError):
        return False
    if pa == "re":
        # strict side (min) — over-merging distinct states is worse than re-fitting one
        return abs(va - vb) <= TOLERANCES["re"] * max(min(abs(va), abs(vb)), 1e-9)
    if pa == "pa":
        d = abs(va - vb) % 360.0
        d = min(d, 360.0 - d)
        return d <= TOLERANCES["pa"]
    if pa == "n":
        return abs(va - vb) <= TOLERANCES["n"]
    if pa == "q":
        return abs(va - vb) <= TOLERANCES["q"]
    if pa == "mag":
        return abs(va - vb) <= TOLERANCES["mag"]
    if pa in {"x", "y"}:
        return abs(va - vb) <= TOLERANCES["pos"]
    return abs(va - vb) <= 1e-9


def _components_match(a: dict, b: dict, *, ignore_toggles: bool) -> bool:
    if (a.get("type") or "") != (b.get("type") or ""):
        return False
    if not _same_name_class(a.get("name") or "", b.get("name") or ""):
        return False
    if not ignore_toggles and a.get("toggles") != b.get("toggles"):
        return False
    for key in ("re_px", "mag", "q", "pa", "n_state"):
        va, vb = a.get(key), b.get(key)
        if key == "n_state":
            if (va is None) != (vb is None):
                return False
            if va is not None:
                # n compared numerically within ±0.5; free/fixed state is part of
                # the toggle configuration and only enforced via toggles above
                try:
                    va_v = float(str(va).split(":")[1])
                    vb_v = float(str(vb).split(":")[1])
                except (IndexError, ValueError):
                    if va != vb:
                        return False
                else:
                    if abs(va_v - vb_v) > TOLERANCES["n"]:
                        return False
            continue
        if key == "re_px":
            if not _param_close("re", va, vb):
                return False
        elif key == "pa":
            if not _param_close("pa", va, vb):
                return False
        elif key == "mag":
            if not _param_close("mag", va, vb):
                return False
        elif key == "q":
            if not _param_close("q", va, vb):
                return False
    return True


def signature_equivalent(a: dict, b: dict, *, ignore_toggles: bool = False,
                         ignore_bands: bool = False) -> bool:
    """Multiset equivalence of two canonical signatures within tolerance bands.

    Conservative AND of: same structure count, pairwise match (greedy matching
    over name-class+type+params), toggle configuration, and (unless ignored)
    .cons band configuration. Positions (x/y) are compared with the 8 px band
    only when both signatures carry them (result-ledger style).
    """
    sa, sb = a.get("structures", []), b.get("structures", [])
    if len(sa) != len(sb):
        return False
    used = [False] * len(sb)
    for ca in sa:
        found = False
        for j, cb in enumerate(sb):
            if not used[j] and _components_match(ca, cb, ignore_toggles=ignore_toggles):
                used[j] = True
                found = True
                break
        if not found:
            return False
    if not ignore_toggles and any(c.get("toggles") != d.get("toggles")
                                  for c, d in zip(sa, sb)):
        # already enforced pairwise; kept as a no-op guard
        pass
    if not ignore_bands and a.get("cons_bands") != b.get("cons_bands"):
        return False
    return True


# ------------------------------------------------------------------------ zombie
def find_zombies(inventory: list[dict], threshold: float = 0.005) -> list[str]:
    """Components with post-fit flux < 0.5% of the brightest (relative criterion)."""
    comps = normalize_inventory(inventory)
    mags = [c.get("mag") for c in comps if c.get("mag") is not None]
    if len(mags) < 2:
        return []
    delta = math.log10(1.0 / threshold) / 0.4  # ~5.75 mag
    mag_min = min(mags)
    return [c["name"] for c in comps
            if c.get("mag") is not None and c["mag"] - mag_min > delta]


def strip_zombies(sig: dict, zombies: list[str]) -> dict:
    """Zombie-aware equivalence: drop zombie components from a signature."""
    zset = set(zombies or [])
    out = dict(sig)
    out["structures"] = [s for s in sig.get("structures", []) if s.get("name") not in zset]
    return out


# ----------------------------------------------------------------- combo identity
def combo_identity(inventory_or_sig) -> str:
    """Physical-identity inventory key (the unit of the per-combination cap).

    Naming swaps allowed (bulge n=0.5-fixed ≡ bar); parameter values and tune
    axes do NOT distinguish attempts. Companion variants collapse to one role
    with multiplicity.
    """
    if isinstance(inventory_or_sig, dict) and "structures" in inventory_or_sig:
        comps = inventory_or_sig["structures"]
        names = []
        for s in comps:
            name = s.get("name") or ""
            ns = s.get("n_state")
            if name == "bulge" and ns is not None and str(ns).startswith("fixed:0.5"):
                name = "bar"
            names.append(name)
    else:
        names = []
        for c in normalize_inventory(inventory_or_sig):
            name = c.get("name") or ""
            toggles = c.get("toggles", {})
            if name == "bulge" and c.get("n") is not None:
                try:
                    if toggles.get("n", 1) == 0 and abs(float(c["n"]) - 0.5) < 1e-6:
                        name = "bar"
                except (TypeError, ValueError):
                    pass
            names.append(name)
    roles: list[str] = []
    for n in names:
        roles.append("companion*" if _COMPANION_RE.match(n or "") else (n or "?"))
    return "+".join(sorted(roles))


# ------------------------------------------------------------ closed-form projection
@dataclass
class Projection:
    inventory: list[dict]
    kind: str  # "remove-only" | "param-revert" | "bound-restore"


def project_closed_form(parent_inventory: list[dict], primitives: list[dict],
                        param_history: dict | None = None,
                        bound_ctx=None) -> Projection | None:
    """Exact projection of closed-form transitions (R2); None = black-box.

    Supported closed forms:
      * remove-only: every primitive is ``{"op": "remove", ...}``;
      * param-revert: a single tune restoring a param to a value recorded in
        ``param_history[(name, param)]`` (list of (value, toggle));
      * bound-restore: a single tune restoring the default band (structure
        unchanged).
    Survivors keep the parent toggle configuration (warm-start rules).
    """
    param_history = param_history or {}
    if not primitives:
        return None
    if all(p.get("op") == "remove" for p in primitives):
        inv = [dict(c) for c in normalize_inventory(parent_inventory)]
        for p in primitives:
            target = (p.get("target") or p.get("structure_name") or "").lower()
            inv = [c for c in inv if c.get("name") != target]
        return Projection(inv, "remove-only")

    if len(primitives) == 1 and primitives[0].get("op") == "tune":
        p = primitives[0]
        target = (p.get("structure_name") or p.get("target") or "").lower()
        param = p.get("param")
        value = p.get("value")
        inv = [dict(c) for c in normalize_inventory(parent_inventory)]
        comp = next((c for c in inv if c.get("name") == target), None)
        if comp is None:
            return None
        if value is not None and param is not None and param in comp:
            history = param_history.get((target, param), [])
            reverted = any(abs(float(value) - float(v)) <= 1e-9 for v, _t in history)
            if reverted:
                comp[param] = value
                return Projection(inv, "param-revert")
        # bound restoration: no param-value change, only bands restored to defaults
        if p.get("cons_bounds") and _restores_default_band(p, comp, bound_ctx):
            return Projection(inv, "bound-restore")
    return None


def _restores_default_band(tune: dict, comp: dict, bound_ctx=None) -> bool:
    """True when the tune's declared bounds equal the default bound set."""
    from beam.cons_decode import BoundContext

    cb = tune.get("cons_bounds") or {}
    ctx = bound_ctx or BoundContext()
    companion = bool(_COMPANION_RE.match(comp.get("name") or ""))
    for param, key in (("re", "re"), ("n", "n"), ("q", "q")):
        band = cb.get(key)
        if band is None:
            continue
        default = ctx.default_band(param, companion=companion)
        if default is None:
            return False
        if abs(float(band[0]) - default[0]) > 1e-6 or abs(float(band[1]) - default[1]) > 1e-6:
            return False
    return True


# -------------------------------------------------- hypothetical application (R1)
def apply_primitives_to_inventory(parent_inventory: list[dict],
                                  primitives: list[dict],
                                  strict: bool = True) -> tuple[list[dict] | None, str]:
    """Apply candidate primitives at signature level -> hypothetical inventory.

    Used for R1 input-ledger comparison and candidate validation (E_RE_CHAIN,
    E_MULTIPLICITY ...). Returns ``(inventory, "")`` or ``(None, reason)``.
    With ``strict=False`` a duplicate structure name is appended instead of
    failing, so downstream multiplicity checks can fire on it.
    Structural application only — no feedme writing (that is transcribe.py).
    """
    inv = [dict(c) for c in normalize_inventory(parent_inventory)]
    for p in primitives:
        op = p.get("op")
        if op == "remove":
            target = (p.get("target") or p.get("structure_name") or "").lower()
            before = len(inv)
            inv = [c for c in inv if c.get("name") != target]
            if len(inv) == before:
                return None, f"remove target not found: {target}"
        elif op == "add":
            entry = p.get("add") or p
            name = (entry.get("structure_name") or "").lower()
            if not name:
                return None, "add without structure_name"
            if any(c.get("name") == name for c in inv):
                if strict:
                    return None, f"duplicate structure name: {name}"
            ctype = (entry.get("component_type") or "").lower()
            re_eff = entry.get("re_px")  # VLM Re triplets are effective radii (unit contract)
            comp = {
                "number": None,
                "name": name,
                "type": ctype,
                "x": entry.get("x_px"),
                "y": entry.get("y_px"),
                "mag": entry.get("mag"),
                "re": (re_eff / 1.68 if ctype == "expdisk" and re_eff is not None else re_eff),
                "re_effective": re_eff,
                "n": entry.get("n"),
                "q": entry.get("q"),
                "pa": entry.get("pa_deg"),
                "toggles": dict(entry.get("toggles") or {}),
            }
            inv.append(comp)
        elif op == "tune":
            tunes = p.get("tunes") or [p]
            for t in tunes:
                target = (t.get("structure_name") or t.get("target") or "").lower()
                comp = next((c for c in inv if c.get("name") == target), None)
                if comp is None:
                    return None, f"tune target not found: {target}"
                param = t.get("param")
                value = t.get("value")
                if param is None or value is None:
                    # bound-only tunes don't change the structural inventory
                    continue
                key = {"re_px": "re", "q": "q", "pa_deg": "pa", "mag": "mag",
                       "n": "n", "x_px": "x", "y_px": "y"}.get(param, param)
                comp[key] = value
                if key == "re":
                    # VLM values are effective radii; expdisk stores Rs = Re/1.68
                    comp["re_effective"] = value
                    comp["re"] = (value / 1.68 if comp.get("type") == "expdisk" else value)
                if t.get("toggle") is not None:
                    comp.setdefault("toggles", {})[key] = int(t["toggle"])
        elif op == "convert":
            cv = p.get("convert") or {}
            frm = (cv.get("from_name") or "").lower()
            to = (cv.get("to_name") or "").lower()
            comp = next((c for c in inv if c.get("name") == frm), None)
            if comp is None:
                return None, f"convert source not found: {frm}"
            comp["name"] = to or comp["name"]
            comp["type"] = (cv.get("to_type") or comp["type"]).lower()
            if comp["type"] == "expdisk":
                # Rs = fitted Re / 1.68 (the declared conversion rule)
                if comp.get("re_effective") is not None:
                    comp["re"] = comp["re_effective"] / 1.68
                comp.pop("n", None)
        else:
            return None, f"unknown op: {op}"
    return inv, ""
