"""Parameter-subspace cell filter (2026-10-17 finalized scheme).

The pre-transcription INPUT of every executed fit is mapped to per-component
JOINT bin cells (the user-specified bins, replay-validated on 138 galaxies
with zero true best-kill). Visited sets are scoped per EXACT inventory and
accumulated from every EXECUTED fit (all recorded rounds are real optimizer
starts; a same-cell retry of a frozen run just freezes again).

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
    re_eff = c.get("re_effective")
    if re_eff is not None:
        return float(re_eff)   # already an effective radius (projection path)
    re = c.get("re")
    if re is not None and (c.get("type") or "").lower() in ("expdisk", "edgedisk"):
        re = float(re) * 1.68  # raw Rs -> effective radius
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


def primitives_carry_f1(prims: list) -> bool:
    """Whether a primitive set carries an F1 row — the scope flag of the
    ledger's inventory key (inventories parsed from feedmes do not record
    F1 rows, so the pending record's primitives are the only source)."""
    for p in prims or []:
        if not isinstance(p, dict):
            continue
        if p.get("f1"):
            return True
        add = p.get("add")
        if isinstance(add, dict) and add.get("f1"):
            return True
    return False


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
    """Dual visited-cell bookkeeping (2026-10-20 x/y registration design):

    Two visited sets, both scoped per exact inventory:
      - ``input`` cells: the pre-transcribed INPUT (x) of every executed
        fit — "which starting cells have been fed to the optimiser";
      - ``attractor`` cells: the FITTED OUTPUT (y) of every executed fit —
        "which cells the optimiser has converged into". Warm-start child
        inputs cluster near the parent OUTPUT, so a tiny-delta tune whose
        declared change stays within cells lands on the parent attractor
        cell set and is recognised as a same-basin re-entry.
    Both are persisted in the graph (``cell_visited``) at record_fit time,
    NOT rebuilt per ingest — the audit trail and the shadow replay need
    them; the reserved F1 rounds are exempt from registration."""

    GRAPH_KEY = "cell_visited"

    def __init__(self, data: dict | None = None):
        # {inventory_key: {"input": {comp: [cells]}, "attractor": {comp: [cells]}}}
        self._data: dict = data or {}

    # -- raw dict access (graph persistence) --
    @staticmethod
    def _c(cell):
        """Normalise a cell to tuple form (JSON persistence turns the
        in-memory tuples into lists; `tuple not in [list]` is silently
        False, which would disarm the whole filter cross-process)."""
        return tuple(cell) if isinstance(cell, list) else cell

    def to_dict(self) -> dict:
        return self._data

    @classmethod
    def from_dict(cls, data) -> "CellLedger":
        led = cls()
        if not isinstance(data, dict):
            return led
        for k, slot in data.items():
            if not isinstance(slot, dict):
                continue
            norm = {}
            for kind in ("input", "attractor"):
                bucket = slot.get(kind)
                if isinstance(bucket, dict):
                    norm[kind] = {nm: [cls._c(c) for c in cells]
                                  for nm, cells in bucket.items()
                                  if isinstance(cells, (list, set, tuple))}
            led._data[k] = norm
        return led

    @classmethod
    def from_graph(cls, graph) -> "CellLedger":
        """Load the PERSISTED ledger (registered at record_fit). Falls back
        to a dual reconstruction from the executed states' stored
        inventories (legacy graphs predating the persisted registration)."""
        stored = graph.g.graph.get(cls.GRAPH_KEY)
        if isinstance(stored, dict) and stored:
            return cls.from_dict(stored)
        led = cls()
        pending = graph.g.graph.get("pending", {})
        for _label, attrs in sorted(graph.g.nodes(data=True),
                                      key=lambda kv: kv[1].get("global_iter_id", 0)):
            if attrs.get("global_iter_id", 0) == 0:
                continue
            aid = attrs.get("action_in") or ""
            rec = pending.get(aid) or {}
            if (rec.get("code_flags") or {}).get("f1_reserved"):
                continue
            inv_in = attrs.get("input_inventory") or []
            if not inv_in:
                continue
            has_f1 = primitives_carry_f1(rec.get("primitives") or [])
            led.register(inv_in, attrs.get("inventory") or [], has_f1)
        return led

    # -- registration --
    def register(self, x_inv: list[dict], y_inv: list[dict], has_f1: bool = False):
        """Register one executed fit: x = pre-transcribed INPUT cells,
        y = fitted OUTPUT (attractor) cells. Persisted form: lists (json)."""
        k = str(inventory_key(x_inv, has_f1))
        slot = self._data.setdefault(k, {"input": {}, "attractor": {}})
        for kind, inv in (("input", x_inv), ("attractor", y_inv)):
            for nm, cell in cells_of(inv, has_f1).items():
                cell = self._c(cell)
                bucket = slot[kind].setdefault(nm, [])
                if cell not in bucket:
                    bucket.append(cell)

    # -- queries --
    def _matches(self, vis: dict, cells: dict) -> bool:
        for nm, cell in cells.items():
            if cell not in vis.get(nm, ()):
                return False
        return True

    def is_dup(self, inv: list[dict], has_f1: bool = False) -> bool:
        """Dup iff EVERY component's cell matches a visited INPUT cell set
        OR a visited ATTRACTOR cell set within this exact inventory."""
        k = str(inventory_key(inv, has_f1))
        slot = self._data.get(k)
        if slot is None:
            return False
        cells = cells_of(inv, has_f1)
        return self._matches(slot["input"], cells) or self._matches(slot["attractor"], cells)

    def attractor_cells(self, y_inv: list[dict], has_f1: bool = False):
        """The attractor cell dict of a fitted output (queue-hygiene pass)."""
        return cells_of(y_inv, has_f1)

    def matches_cells(self, cells: dict, inv: list[dict],
                     has_f1: bool = False) -> bool:
        """Does a cell dict match (every component) — used by the queue
        purge pass against a specific fitted output's cells."""
        return self._matches(cells, cells_of(inv, has_f1)) if cells else False


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
