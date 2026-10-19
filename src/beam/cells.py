"""Parameter-subspace cell filter (2026-10-17 finalized scheme).

The pre-transcription INPUT of every executed fit is mapped to per-component
JOINT bin cells (the user-specified bins, replay-validated on 138 galaxies
with zero true best-kill). Visited sets are scoped per EXACT inventory and
accumulated from every EXECUTED fit (sub-converged included — they are real
optimizer starts; a same-cell retry of a frozen run just freezes again).

A candidate is a DUP when EVERY component's cell is already visited within
its inventory (same-basin re-entry hypothesis: warm-start children whose
every component lands in explored cells almost always re-converge to the
parent attractor); ONE new component cell anywhere makes it meaningful.
"""

from __future__ import annotations

import bisect

# ---- user-specified bins ------------------------------------------------
PA_B = [-90.0, -45.0, 0.0, 45.0]              # 4 bins x 45deg (sign trap at boundaries)
RE_BULGE = [0.5, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
RE_BAR = [1.0, 4.0, 8.0, 16.0, 32.0, 64.0]
RE_DISK = [1.0, 4.0, 8.0, 16.0, 32.0, 64.0]
Q_BULGE = [0.25, 0.5, 0.75]
Q_BAR = [0.1, 0.2, 0.4]
N_B = [2.0, 4.0]

BULGE_FAMILY = {"bulge", "singlesersic", "sersic", "sersic2"}
PRESENCE_ONLY = {"agn", "psf", "companion", "comp", "secondary", "satellite"}


def _bin(v, bounds):
    if v is None:
        return "x"
    return bisect.bisect_right(bounds, v)


def _pa_norm(pa):
    if pa is None:
        return None
    return ((pa + 90.0) % 180.0) - 90.0


def _re_eff(c):
    re = c.get("re_effective", c.get("re"))
    if re is not None and (c.get("type") or "").lower() in ("expdisk", "edgedisk"):
        re = float(re) * 1.68
    return re


def comp_cell(c: dict):
    """The JOINT bin-tuple of ONE component (its parameter cell)."""
    name = (c.get("name") or "?").lower()
    t = (c.get("type") or "").lower()
    if name in PRESENCE_ONLY or (t == "psf" and name not in BULGE_FAMILY):
        return (name,)                        # agn / companions: presence only
    re = _re_eff(c)
    q = c.get("q", c.get("ba"))
    pa = _pa_norm(c.get("pa"))
    if name == "disk" or t == "expdisk":
        return ("disk", _bin(re, RE_DISK), _bin(pa, PA_B))
    if name == "bar":
        return ("bar", _bin(re, RE_BAR), _bin(q, Q_BAR), _bin(pa, PA_B))
    if name in BULGE_FAMILY:
        return (name, _bin(re, RE_BULGE), _bin(q, Q_BULGE),
                _bin(pa, PA_B), _bin(c.get("n"), N_B))
    return (name or t or "?",)


def inventory_key(inv: list[dict], has_f1: bool = False):
    """Scope key: the sorted name multiset + F1 flag (F1-bearing and plain
    variants of the same inventory never collide)."""
    names = tuple(sorted((c.get("name") or "?").lower() for c in inv))
    return (names, bool(has_f1))


def cells_of(inv: list[dict], has_f1: bool = False):
    """{component name: joint cell} for one pre-transcribed input."""
    cells = {}
    for c in inv:
        nm = (c.get("name") or "?").lower()
        cells.setdefault(nm, comp_cell(c))
    return cells


class CellLedger:
    """Visited-cell bookkeeping over the executed fits, scoped per inventory."""

    def __init__(self):
        self._visited: dict = {}   # inventory_key -> {comp: set(cells)}

    def add(self, inv: list[dict], has_f1: bool = False):
        k = inventory_key(inv, has_f1)
        vis = self._visited.setdefault(k, {})
        for nm, cell in cells_of(inv, has_f1).items():
            vis.setdefault(nm, set()).add(cell)

    def is_dup(self, inv: list[dict], has_f1: bool = False) -> bool:
        """True when EVERY component's cell is already visited within this
        exact inventory — the same-basin re-entry hypothesis. A component
        set never tried before is meaningful (no entry in _visited)."""
        k = inventory_key(inv, has_f1)
        vis = self._visited.get(k)
        if vis is None:
            return False
        for nm, cell in cells_of(inv, has_f1).items():
            if cell not in vis.get(nm, set()):
                return False
        return True

    @classmethod
    def from_graph(cls, graph) -> "CellLedger":
        """Build the ledger from every EXECUTED fit's INPUT inventory
        (sub-converged included; the reserved F1 rounds are exempt)."""
        led = cls()
        pending = graph.g.graph.get("pending", {})
        for label, attrs in sorted(graph.g.nodes(data=True),
                                   key=lambda kv: kv[1].get("global_iter_id", 0)):
            if attrs.get("global_iter_id", 0) == 0:
                continue
            aid = attrs.get("action_in") or ""
            rec = pending.get(aid) or {}
            if (rec.get("code_flags") or {}).get("f1_reserved"):
                continue
            inv = attrs.get("input_inventory") or []
            if not inv:
                continue
            prims = rec.get("primitives") or []
            has_f1 = any(p.get("f1") or (p.get("add") or {}).get("f1")
                         for p in prims if isinstance(p, dict))
            led.add(inv, has_f1)
        return led


def skeleton_class(inv_or_names) -> str:
    """(a) singlesersic regime / (b) disk-bearing no bar / (c) disk + bar."""
    if isinstance(inv_or_names[0], dict):
        names = {(c.get("name") or "").lower() for c in inv_or_names}
    else:
        names = {str(n).lower() for n in inv_or_names}
    if "singlesersic" in names:
        return "a"
    if "bar" in names:
        return "c"
    return "b"
