"""Beam search state for HBSS (Heuristic Beam Search over Structure Space).

Per-galaxy persistent state holding:
- priority queue Q (max length = beam_width, sorted by rank asc then h_score desc)
- visited topology set (target_topology_hash -> first visitor metadata)
- incumbent best (s_best) with fitness metrics
- consumed fit budget counter
- audit log

Mirrors ``best_round_registry.py`` patterns: threading.RLock + module-level
global dict + atomic file persistence (``<galaxy_dir>/.beam_search.json``),
toggleable via ``BEAM_SEARCH_PERSIST`` environment variable.

The single MCP tool ``beam_search_state`` routes all operations through an
``action`` parameter. The orchestrating LLM MUST NOT maintain queue/visited
state in its own context — every read and write goes through this tool.
"""

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Optional


# ── dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class BeamCandidate:
    """One (state, action) node in the beam search queue."""

    candidate_id: str
    parent_id: Optional[str]            # None for the seed
    source_topology_hash: str           # topology this candidate was generated FROM
    target_topology_hash: str           # topology reached by applying the action
    action_type: str                    # Add|Remove|Adjust|ToggleProfile|ToggleVary|NoOp
    action_component: str               # "Bulge"|"Disk"|... or "" for NoOp
    action_detail: str                  # human-readable, e.g. "add sersic n=4"
    init_params: dict                   # suggested initial values for Add actions
    rank: int                           # 1 = best (batch-sorted by VLM)
    h_score: float                      # VLM confidence in [0, 1] (record only)
    reason: str                         # short VLM justification
    lyric_path: str                     # this candidate's OWN lyric (filled at mark_visited)
    parent_lyric_path: str = ""         # parent state's lyric (used to materialize the action)
    status: str = "pending"             # pending|fitting|done|failed|pruned
    fit_round_label: Optional[str] = None
    fit_summary_path: Optional[str] = None
    fit_comparison_path: Optional[str] = None
    fitness_metrics: dict = field(default_factory=dict)
    depth: int = 0                      # 0 for seed, increments per level
    generated_at: str = ""


@dataclass
class BeamSearchState:
    """Per-galaxy beam search bookkeeping."""

    galaxy_key: str
    beam_width: int = 4
    budget: int = 15
    consumed_fits: int = 0
    early_stop_k: int = 3
    early_stop_eps: float = 0.05
    no_improve_streak: int = 0
    queue: list[BeamCandidate] = field(default_factory=list)
    visited_topology: dict[str, dict] = field(default_factory=dict)
    best: Optional[BeamCandidate] = None
    best_history: list[dict] = field(default_factory=list)
    last_fitted_candidate_id: Optional[str] = None  # most recent mark_visited; used as parent for enqueue
    phase: str = "init"                 # init|running|early_stopped|budget_exhausted|finalized
    created_at: str = ""
    updated_at: str = ""
    audit_log: list[str] = field(default_factory=list)


# ── constants ────────────────────────────────────────────────────────────────


_VALID_ACTIONS = {
    "init", "enqueue", "pop_next", "mark_visited", "is_visited",
    "update_best", "get_status", "finalize", "clear",
}

_VALID_CANDIDATE_STATUS = {"pending", "fitting", "done", "failed", "pruned"}

_VALID_PHASES = {
    "init", "running", "early_stopped", "budget_exhausted", "finalized",
}


# ── persistence (atomic write, lazy load — mirrors best_round_registry) ──────


SCHEMA_VERSION = 1
_STATE_FILENAME = ".beam_search.json"

_LOCK = threading.RLock()  # RLock: update_best internally touches mark_visited semantics
_STATES: dict[str, BeamSearchState] = {}


def _persistence_enabled() -> bool:
    return os.environ.get("BEAM_SEARCH_PERSIST", "1") == "1"


def _state_path(galaxy_key: str) -> str:
    return os.path.join(galaxy_key, _STATE_FILENAME)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _candidate_to_dict(c: BeamCandidate) -> dict:
    return {
        "candidate_id": c.candidate_id,
        "parent_id": c.parent_id,
        "source_topology_hash": c.source_topology_hash,
        "target_topology_hash": c.target_topology_hash,
        "action_type": c.action_type,
        "action_component": c.action_component,
        "action_detail": c.action_detail,
        "init_params": c.init_params,
        "rank": c.rank,
        "h_score": c.h_score,
        "reason": c.reason,
        "lyric_path": c.lyric_path,
        "parent_lyric_path": c.parent_lyric_path,
        "status": c.status,
        "fit_round_label": c.fit_round_label,
        "fit_summary_path": c.fit_summary_path,
        "fit_comparison_path": c.fit_comparison_path,
        "fitness_metrics": c.fitness_metrics,
        "depth": c.depth,
        "generated_at": c.generated_at,
    }


def _candidate_from_dict(d: dict) -> BeamCandidate:
    return BeamCandidate(
        candidate_id=d["candidate_id"],
        parent_id=d.get("parent_id"),
        source_topology_hash=d.get("source_topology_hash", ""),
        target_topology_hash=d.get("target_topology_hash", ""),
        action_type=d.get("action_type", "NoOp"),
        action_component=d.get("action_component", ""),
        action_detail=d.get("action_detail", ""),
        init_params=d.get("init_params", {}) or {},
        rank=int(d.get("rank", 99)),
        h_score=float(d.get("h_score", 0.0)),
        reason=d.get("reason", ""),
        lyric_path=d.get("lyric_path", ""),
        parent_lyric_path=d.get("parent_lyric_path", ""),
        status=d.get("status", "pending"),
        fit_round_label=d.get("fit_round_label"),
        fit_summary_path=d.get("fit_summary_path"),
        fit_comparison_path=d.get("fit_comparison_path"),
        fitness_metrics=d.get("fitness_metrics", {}) or {},
        depth=int(d.get("depth", 0)),
        generated_at=d.get("generated_at", ""),
    )


def _state_to_dict(s: BeamSearchState) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "galaxy_key": s.galaxy_key,
        "beam_width": s.beam_width,
        "budget": s.budget,
        "consumed_fits": s.consumed_fits,
        "early_stop_k": s.early_stop_k,
        "early_stop_eps": s.early_stop_eps,
        "no_improve_streak": s.no_improve_streak,
        "phase": s.phase,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "queue": [_candidate_to_dict(c) for c in s.queue],
        "visited_topology": dict(s.visited_topology),
        "best": _candidate_to_dict(s.best) if s.best else None,
        "best_history": list(s.best_history),
        "last_fitted_candidate_id": s.last_fitted_candidate_id,
        "audit_log": list(s.audit_log),
    }


def _state_from_dict(d: dict) -> Optional[BeamSearchState]:
    try:
        return BeamSearchState(
            galaxy_key=d["galaxy_key"],
            beam_width=int(d.get("beam_width", 4)),
            budget=int(d.get("budget", 15)),
            consumed_fits=int(d.get("consumed_fits", 0)),
            early_stop_k=int(d.get("early_stop_k", 3)),
            early_stop_eps=float(d.get("early_stop_eps", 0.05)),
            no_improve_streak=int(d.get("no_improve_streak", 0)),
            phase=d.get("phase", "init"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            queue=[_candidate_from_dict(c) for c in d.get("queue", [])],
            visited_topology=dict(d.get("visited_topology", {})),
            best=_candidate_from_dict(d["best"]) if d.get("best") else None,
            best_history=list(d.get("best_history", [])),
            last_fitted_candidate_id=d.get("last_fitted_candidate_id"),
            audit_log=list(d.get("audit_log", [])),
        )
    except (KeyError, TypeError, ValueError) as e:
        print(f"[beam_search] state deserialize failed: {e}")
        return None


def _persist_state(state: BeamSearchState) -> None:
    """Atomic write of the state to its galaxy's state file.

    Failures are logged and swallowed: the in-memory state is still updated,
    so a persist outage only costs resume safety, never the current session.
    """
    if not _persistence_enabled() or not state.galaxy_key:
        return
    path = _state_path(state.galaxy_key)
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        data = json.dumps(_state_to_dict(state), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(prefix=".beam_search.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        print(f"[beam_search] persist failed for {path}: {e}")


def _load_state(galaxy_key: str) -> Optional[BeamSearchState]:
    if not _persistence_enabled() or not galaxy_key:
        return None
    path = _state_path(galaxy_key)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[beam_search] load failed for {path}: {e}")
        return None
    if d.get("schema_version") != SCHEMA_VERSION:
        return None
    return _state_from_dict(d)


# ── registry accessors ───────────────────────────────────────────────────────


def get_state(galaxy_key: str) -> Optional[BeamSearchState]:
    with _LOCK:
        if galaxy_key in _STATES:
            return _STATES[galaxy_key]
    entry = _load_state(galaxy_key)
    if entry is None:
        return None
    with _LOCK:
        if galaxy_key not in _STATES:
            _STATES[galaxy_key] = entry
        return _STATES[galaxy_key]


def _set_state_unlocked(galaxy_key: str, state: BeamSearchState) -> None:
    state.updated_at = _now_iso()
    _STATES[galaxy_key] = state
    _persist_state(state)


def _set_state(galaxy_key: str, state: BeamSearchState) -> None:
    with _LOCK:
        _set_state_unlocked(galaxy_key, state)


def clear() -> None:
    """Drop all in-memory states. Persisted files are untouched (use ``clear_persisted``)."""
    with _LOCK:
        _STATES.clear()


def clear_persisted(galaxy_key: Optional[str] = None) -> None:
    """Remove persisted state file(s) from disk."""
    if galaxy_key:
        try:
            os.remove(_state_path(galaxy_key))
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"[beam_search] clear_persisted failed for {galaxy_key}: {e}")
        return
    with _LOCK:
        keys = list(_STATES.keys())
    for k in keys:
        try:
            os.remove(_state_path(k))
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"[beam_search] clear_persisted failed for {k}: {e}")


# ── topology normalization ──────────────────────────────────────────────────
#
# A lyric may label components by generic IDs (e.g. "obj0", "obj1") rather than
# physical roles. We combine three signals to bucket each component into a
# stable physical-role token:
#   1. Adjacent comment line (e.g. "# Sersic function - Disk (Pa)")
#   2. Component name keywords (disk/bulge/bar/agn/...)
#   3. Profile type as fallback (sersic -> DISK by default)
#
# The topology hash is sha1(sorted role tokens joined by '|')[:12], prefixed
# with "topo_". Two lyrics with the same component role multiset share a hash,
# regardless of parameter values — this is what enables topology-level dedup.


_COMPONENT_HEADER_RE = re.compile(r"^P([a-z])1\)\s*(\S+)")
_COMPONENT_TYPE_RE = re.compile(r"^P([a-z])2\)\s*(\S+)")
_COMMENT_ROLE_RE = re.compile(
    r"#.*?-\s*(Disk|Bulge|Bar|AGN|Psf|EdgeOn|Edge-on|Ring|Companion|Fourier|Spiral|Nucleus)",
    re.IGNORECASE,
)


def _infer_role(name: str, ptype: str, role_hint: str) -> str:
    """Bucket a component into a stable physical-role token."""
    text = f"{role_hint} {name}".lower()
    ptype_l = (ptype or "").lower()

    # Fourier / spiral-arm modulation on a disk
    if "sersic_f" in ptype_l or "fourier" in text or "spiral" in text:
        return "DISK+F1"

    # Edge-on disk
    if "edge" in text or ptype_l == "edgeondisk":
        return "EDGEON_DISK"

    # Bar (Ferrer profile or named bar)
    if "bar" in text or ptype_l.startswith("ferrer"):
        return "BAR"

    # Ring
    if "ring" in text or ptype_l.startswith("gauRing".lower()):
        return "RING"

    # AGN / PSF / nucleus
    if "agn" in text or "psf" in text or "nucleus" in text or ptype_l in ("gaussian", "psf"):
        return "AGN"

    # Companion galaxy
    if "comp" in text:
        return "COMPANION"

    # Bulge / spheroid
    if "bulge" in text or "spheroid" in text:
        return "BULGE"

    # Disk (explicit name)
    if "disk" in text or "disc" in text:
        return "DISK"

    # Fallback: any sersic variant defaults to DISK
    if ptype_l.startswith("sersic"):
        return "DISK"

    return "OTHER"


def _parse_components_with_hints(lyric_file: str) -> list[tuple[str, str, str, str]]:
    """Parse lyric into [(px_prefix, name, ptype, role_hint), ...].

    The role_hint is extracted from the closest preceding comment line that
    matches the ``# ... - <Role> (Px)`` convention.
    """
    components: list[tuple[str, str, str, str]] = []
    current_px: Optional[str] = None
    current_name: Optional[str] = None
    current_ptype: Optional[str] = None
    current_hint = ""
    pending_hint = ""

    try:
        with open(lyric_file, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    pending_hint = ""
                    continue

                if line.startswith("#"):
                    cm = _COMMENT_ROLE_RE.search(line)
                    if cm:
                        pending_hint = cm.group(1)
                    continue

                m = _COMPONENT_HEADER_RE.match(line)
                if m:
                    if current_px and current_name and current_ptype:
                        components.append(
                            (current_px, current_name, current_ptype, current_hint)
                        )
                    current_px = m.group(1)
                    current_name = m.group(2)
                    current_ptype = None
                    current_hint = pending_hint
                    pending_hint = ""
                    continue

                if current_px is not None:
                    m = _COMPONENT_TYPE_RE.match(line)
                    if m and m.group(1) == current_px:
                        current_ptype = m.group(2)

            if current_px and current_name and current_ptype:
                components.append(
                    (current_px, current_name, current_ptype, current_hint)
                )
    except Exception as e:  # noqa: BLE001
        print(f"[beam_search] lyric parse failed for {lyric_file}: {e}")
        return []

    return components


def compute_topology_hash(lyric_file: str) -> str:
    """Stable topology hash from a lyric file.

    Components are bucketed into physical-role tokens; the multiset is sorted
    and sha1-hashed (12 chars). Two lyrics with the same role multiset share
    a hash regardless of parameter values or component ordering.
    """
    if not lyric_file or not os.path.exists(lyric_file):
        return "topo_empty"
    components = _parse_components_with_hints(lyric_file)
    if not components:
        return "topo_empty"
    roles = sorted(
        _infer_role(name, ptype, hint) for _, name, ptype, hint in components
    )
    canon = "|".join(roles)
    return "topo_" + hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


# ── candidate factory & validation ───────────────────────────────────────────


def _make_candidate_from_payload(
    payload: dict,
    parent_id: Optional[str],
    default_source_topology: str = "",
    depth: int = 1,
) -> BeamCandidate:
    """Build a BeamCandidate from a caller-supplied JSON payload.

    The caller supplies action/rank/lyric fields; the tool fills in
    candidate_id, generated_at, status, parent_id, and depth internally.
    ``depth`` should be ``parent.depth + 1`` (caller is responsible for
    passing the correct value — typically derived from ``state.best.depth``).
    """
    lyric_path = payload.get("lyric_path", "")
    target_topology = payload.get("target_topology_hash", "")
    if not target_topology and lyric_path:
        target_topology = compute_topology_hash(lyric_path)

    return BeamCandidate(
        candidate_id=payload.get("candidate_id") or uuid.uuid4().hex,
        parent_id=payload.get("parent_id") or parent_id,
        source_topology_hash=payload.get("source_topology_hash", default_source_topology),
        target_topology_hash=target_topology,
        action_type=payload.get("action_type", "NoOp"),
        action_component=payload.get("action_component", ""),
        action_detail=payload.get("action_detail", ""),
        init_params=payload.get("init_params", {}) or {},
        rank=int(payload.get("rank", 99)),
        h_score=float(payload.get("h_score", 0.0)),
        reason=payload.get("reason", ""),
        lyric_path=lyric_path,
        status="pending",
        depth=int(payload.get("depth", depth)),
        generated_at=_now_iso(),
    )


def _sort_queue(queue: list[BeamCandidate]) -> None:
    """Sort by (rank asc, depth asc, h_score desc).

    Ordering rationale:
      1. rank — VLM's local judgement of which action is most promising
      2. depth — shallower first when ranks tie, to prevent depth-first bias
         (candidates from different parent states all carry rank=1 from
         their own sibling comparison; depth breaks the tie toward broader
         exploration rather than letting an "optimistic" branch monopolize)
      3. h_score — final tiebreaker by VLM confidence
    """
    queue.sort(key=lambda c: (c.rank, c.depth, -c.h_score))


def _trim_queue(state: BeamSearchState) -> list[BeamCandidate]:
    """Mark low-priority pending candidates as pruned, keeping only beam_width pending.

    Pruned candidates stay in ``state.queue`` (with status="pruned") so the
    full search history remains queryable via get_status. Candidates already
    in fitting/done/failed status are never pruned. Returns the list of
    newly-pruned candidates (for audit).
    """
    pending = [c for c in state.queue if c.status == "pending"]
    if len(pending) <= state.beam_width:
        return []
    # Sort pending by (rank asc, depth asc, h_score desc); keep the top beam_width
    pending.sort(key=lambda c: (c.rank, c.depth, -c.h_score))
    keep_ids = {id(c) for c in pending[: state.beam_width]}
    pruned: list[BeamCandidate] = []
    for c in state.queue:
        if c.status == "pending" and id(c) not in keep_ids:
            c.status = "pruned"
            pruned.append(c)
    return pruned


# ── fitness comparison ───────────────────────────────────────────────────────


def _score_fitness(metrics: dict) -> Optional[float]:
    """Composite score: lower is better. Returns None if uncomparable.

    Uses BIC as the primary signal (carries complexity penalty); falls back
    to reduced_chisq when BIC is missing. Both missing → None.
    """
    if not metrics:
        return None
    bic = metrics.get("bic")
    if bic is not None:
        try:
            base = float(bic)
        except (TypeError, ValueError):
            base = None
        if base is not None:
            penalty = 0.0
            if not metrics.get("converged", True):
                penalty += 1e4
            if not metrics.get("physics_ok", True):
                penalty += 1e3
            return base + penalty
    chisq = metrics.get("reduced_chisq")
    if chisq is not None:
        try:
            base = float(chisq)
        except (TypeError, ValueError):
            base = None
        if base is not None:
            penalty = 0.0
            if not metrics.get("converged", True):
                penalty += 1e2
            if not metrics.get("physics_ok", True):
                penalty += 1e1
            return base + penalty
    return None


def _max_pending_h(state: BeamSearchState) -> float:
    return max((c.h_score for c in state.queue if c.status == "pending"), default=0.0)


def _find_candidate_by_id(
    state: BeamSearchState, candidate_id: Optional[str]
) -> Optional[BeamCandidate]:
    """Look up a candidate anywhere in state (queue, best, seed)."""
    if not candidate_id:
        return None
    for c in state.queue:
        if c.candidate_id == candidate_id:
            return c
    if state.best and state.best.candidate_id == candidate_id:
        return state.best
    return None


def _get_last_fitted(state: BeamSearchState) -> Optional[BeamCandidate]:
    """Return the most recently mark_visited'd candidate (the parent for new enqueues).

    Falls back to state.best if last_fitted_candidate_id is unset or stale
    (e.g., resuming from an older persisted state).
    """
    c = _find_candidate_by_id(state, state.last_fitted_candidate_id)
    if c is not None:
        return c
    return state.best


# ── actions ──────────────────────────────────────────────────────────────────


def _action_init(
    galaxy_dir: str,
    lyric_file: str,
    budget: int,
    beam_width: int,
    early_stop_k: int,
    early_stop_eps: float,
    note: str,
) -> dict:
    galaxy_key = os.path.abspath(galaxy_dir)
    existing = get_state(galaxy_key)
    if existing is not None and existing.phase != "init":
        # Resume scenario: state already populated and not freshly initialized
        existing.audit_log.append(
            f"{_now_iso()} init (resume, phase={existing.phase}) {note}".strip()
        )
        _set_state(galaxy_key, existing)
        return {
            "status": "resumed",
            "phase": existing.phase,
            "galaxy_key": galaxy_key,
            "data": _state_to_dict(existing),
        }

    now = _now_iso()
    state = BeamSearchState(
        galaxy_key=galaxy_key,
        beam_width=beam_width,
        budget=budget,
        early_stop_k=early_stop_k,
        early_stop_eps=early_stop_eps,
        phase="running",
        created_at=now,
        updated_at=now,
        audit_log=[f"{now} init budget={budget} beam_width={beam_width} {note}".strip()],
    )

    # Create a seed BeamCandidate as the initial s_best (depth=0, status=done).
    # This gives every subsequent enqueue a concrete parent.depth to inherit,
    # and lets B1 call update_best(seed_id, fitness_metrics) to fill in the
    # seed's actual fit metrics after the initial fit completes.
    if lyric_file and os.path.exists(lyric_file):
        seed_hash = compute_topology_hash(lyric_file)
        state.best = BeamCandidate(
            candidate_id="seed",
            parent_id=None,
            source_topology_hash="",
            target_topology_hash=seed_hash,
            action_type="NoOp",
            action_component="",
            action_detail="seed (initial lyric)",
            init_params={},
            rank=0,
            h_score=1.0,
            reason="initial state",
            lyric_path=os.path.abspath(lyric_file),
            status="done",
            fit_round_label="seed",
            fitness_metrics={},
            depth=0,
            generated_at=now,
        )
        state.visited_topology[seed_hash] = {
            "first_candidate_id": "seed",
            "fit_round_label": "seed",
            "lyric_path": os.path.abspath(lyric_file),
        }
        state.last_fitted_candidate_id = "seed"
        state.audit_log.append(
            f"{now} seed topology={seed_hash} depth=0 lyric={lyric_file} "
            f"last_fitted=seed"
        )

    _set_state(galaxy_key, state)
    return {
        "status": "initialized",
        "phase": state.phase,
        "galaxy_key": galaxy_key,
        "seed_candidate_id": "seed" if state.best else None,
        "data": _state_to_dict(state),
    }


def _action_enqueue(
    state: BeamSearchState,
    candidate_payload: dict,
    parent_id: Optional[str],
    note: str,
) -> dict:
    if state.phase == "finalized":
        return {"status": "rejected", "reason": "phase=finalized"}
    if state.phase in ("early_stopped", "budget_exhausted"):
        return {"status": "rejected", "reason": f"phase={state.phase}"}

    # Derive parent info from last_fitted (NOT state.best): the parent of a
    # new candidate is the state that was just analyzed, which is the most
    # recently fitted state. state.best may be a different (better, older)
    # state — using it as parent would produce wrong target lyrics.
    parent = _get_last_fitted(state)
    parent_depth = parent.depth if parent else 0
    parent_lyric = parent.lyric_path if parent else ""
    parent_topology = parent.target_topology_hash if parent else ""
    new_depth = parent_depth + 1

    new_cand = _make_candidate_from_payload(
        candidate_payload,
        parent_id=parent_id,
        default_source_topology=parent_topology,
        depth=new_depth,
    )
    new_cand.parent_lyric_path = parent_lyric

    # Topology-level dedup
    if new_cand.target_topology_hash and new_cand.target_topology_hash in state.visited_topology:
        state.audit_log.append(
            f"{_now_iso()} enqueue dedup topology={new_cand.target_topology_hash} "
            f"action={new_cand.action_type}/{new_cand.action_component} depth={new_depth} {note}".strip()
        )
        _set_state(state.galaxy_key, state)
        return {
            "status": "deduplicated",
            "candidate_id": new_cand.candidate_id,
            "target_topology_hash": new_cand.target_topology_hash,
        }

    state.queue.append(new_cand)
    _sort_queue(state.queue)
    pruned = _trim_queue(state)
    state.audit_log.append(
        f"{_now_iso()} enqueue action={new_cand.action_type}/{new_cand.action_component} "
        f"rank={new_cand.rank} h={new_cand.h_score:.2f} depth={new_depth} "
        f"topo={new_cand.target_topology_hash} {note}".strip()
    )
    if pruned:
        state.audit_log.append(
            f"{_now_iso()} pruned {len(pruned)} low-priority candidate(s)"
        )
    _set_state(state.galaxy_key, state)
    return {
        "status": "enqueued",
        "candidate_id": new_cand.candidate_id,
        "rank": new_cand.rank,
        "depth": new_depth,
        "queue_length": len(state.queue),
    }


def _action_pop_next(state: BeamSearchState, note: str) -> dict:
    if state.phase == "finalized":
        return {"status": "rejected", "reason": "phase=finalized"}
    _sort_queue(state.queue)
    for c in state.queue:
        if c.status == "pending":
            c.status = "fitting"
            state.audit_log.append(
                f"{_now_iso()} pop candidate={c.candidate_id} "
                f"action={c.action_type}/{c.action_component} rank={c.rank} {note}".strip()
            )
            _set_state(state.galaxy_key, state)
            return {
                "status": "ok",
                "candidate": _candidate_to_dict(c),
            }
    state.audit_log.append(f"{_now_iso()} pop empty {note}".strip())
    _set_state(state.galaxy_key, state)
    return {"status": "empty"}


def _action_mark_visited(
    state: BeamSearchState,
    candidate_id: str,
    fit_round_label: Optional[str],
    fit_summary_path: Optional[str],
    fit_comparison_path: Optional[str],
    lyric_path: Optional[str],
    fit_count_delta: int,
    note: str,
) -> dict:
    target = _find_candidate_by_id(state, candidate_id)

    if target is None:
        return {"status": "not_found", "candidate_id": candidate_id}

    target.status = "done"
    target.fit_round_label = fit_round_label
    target.fit_summary_path = fit_summary_path
    target.fit_comparison_path = fit_comparison_path
    if lyric_path:
        target.lyric_path = lyric_path
    state.consumed_fits = max(state.consumed_fits + fit_count_delta, state.consumed_fits)

    # Record this candidate as the most recently fitted — subsequent enqueues
    # will use it as parent (inheriting parent_lyric_path and depth) rather
    # than state.best, so that a "worse" branch still produces children with
    # the correct parent lyric.
    state.last_fitted_candidate_id = candidate_id

    if target.target_topology_hash and target.target_topology_hash not in state.visited_topology:
        state.visited_topology[target.target_topology_hash] = {
            "first_candidate_id": target.candidate_id,
            "fit_round_label": fit_round_label or "",
        }

    state.audit_log.append(
        f"{_now_iso()} mark_visited candidate={candidate_id} round={fit_round_label} "
        f"consumed={state.consumed_fits} lyric={lyric_path or '(unchanged)'} "
        f"last_fitted={candidate_id} {note}".strip()
    )
    _set_state(state.galaxy_key, state)
    return {
        "status": "visited",
        "candidate_id": candidate_id,
        "consumed_fits": state.consumed_fits,
        "last_fitted_candidate_id": candidate_id,
    }


def _action_is_visited(state: BeamSearchState, topology_hash: str) -> dict:
    return {
        "status": "ok",
        "visited": topology_hash in state.visited_topology,
        "topology_hash": topology_hash,
    }


def _action_update_best(
    state: BeamSearchState,
    candidate_id: str,
    fitness_metrics: dict,
    lyric_path: Optional[str],
    note: str,
) -> dict:
    target = None
    for c in state.queue:
        if c.candidate_id == candidate_id:
            target = c
            break
    if target is None and state.best and state.best.candidate_id == candidate_id:
        target = state.best

    if target is None:
        return {"status": "not_found", "candidate_id": candidate_id}

    # Compute cur_score BEFORE mutating target — when target IS state.best
    # (e.g., seed initialization in B1), updating target.fitness_metrics would
    # otherwise mutate state.best.fitness_metrics in place, making
    # cur_score == new_score and never registering the improvement.
    cur_score = _score_fitness(state.best.fitness_metrics) if state.best else None
    is_self_update = state.best is not None and target.candidate_id == state.best.candidate_id

    target.fitness_metrics = fitness_metrics or {}
    if lyric_path:
        target.lyric_path = lyric_path

    new_score = _score_fitness(target.fitness_metrics)

    replaced = False
    if new_score is not None:
        if is_self_update:
            # Refreshing the seed's metrics (or any incumbent's refit):
            # don't touch no_improve_streak — this is an initialization, not a
            # competitive comparison. Mark as replaced=True so the caller sees
            # status="updated", but skip best_history logging.
            state.audit_log.append(
                f"{_now_iso()} update_best candidate={candidate_id} self-refresh "
                f"score={new_score} {note}".strip()
            )
            replaced = True
        elif cur_score is None or new_score < cur_score:
            old_best_id = state.best.candidate_id if state.best else None
            state.best = target
            state.no_improve_streak = 0
            state.best_history.append({
                "replaced_at": _now_iso(),
                "candidate_id": target.candidate_id,
                "previous_best_id": old_best_id,
                "new_score": new_score,
                "previous_score": cur_score,
                "fit_round_label": target.fit_round_label,
                "topology_hash": target.target_topology_hash,
                "depth": target.depth,
            })
            replaced = True
        else:
            state.no_improve_streak += 1
    else:
        state.audit_log.append(
            f"{_now_iso()} update_best candidate={candidate_id} metrics uncomparable {note}".strip()
        )

    # Phase transitions
    if state.consumed_fits >= state.budget:
        state.phase = "budget_exhausted"
    elif (
        state.no_improve_streak >= state.early_stop_k
        and _max_pending_h(state) < state.early_stop_eps
    ):
        state.phase = "early_stopped"

    state.audit_log.append(
        f"{_now_iso()} update_best candidate={candidate_id} "
        f"score={new_score} prev={cur_score} "
        f"replaced={replaced} streak={state.no_improve_streak} phase={state.phase} {note}".strip()
    )
    _set_state(state.galaxy_key, state)
    return {
        "status": "updated" if replaced else "retained",
        "candidate_id": candidate_id,
        "replaced_best": replaced,
        "no_improve_streak": state.no_improve_streak,
        "phase": state.phase,
        "best_id": state.best.candidate_id if state.best else None,
    }


def _action_get_status(state: BeamSearchState) -> dict:
    queue_summary = [
        {
            "candidate_id": c.candidate_id,
            "rank": c.rank,
            "depth": c.depth,
            "status": c.status,
            "action_type": c.action_type,
            "action_component": c.action_component,
            "target_topology_hash": c.target_topology_hash,
            "h_score": c.h_score,
            "lyric_path": c.lyric_path,
            "parent_lyric_path": c.parent_lyric_path,
        }
        for c in state.queue
    ]
    return {
        "status": "ok",
        "phase": state.phase,
        "galaxy_key": state.galaxy_key,
        "last_fitted_candidate_id": state.last_fitted_candidate_id,
        "consumed_fits": state.consumed_fits,
        "budget": state.budget,
        "beam_width": state.beam_width,
        "no_improve_streak": state.no_improve_streak,
        "queue_length": len([c for c in state.queue if c.status == "pending"]),
        "queue": queue_summary,
        "visited_count": len(state.visited_topology),
        "best": _candidate_to_dict(state.best) if state.best else None,
        "best_history_count": len(state.best_history),
        "audit_tail": state.audit_log[-10:],
    }


def _action_finalize(state: BeamSearchState, note: str) -> dict:
    state.phase = "finalized"
    state.audit_log.append(f"{_now_iso()} finalize {note}".strip())
    _set_state(state.galaxy_key, state)
    return {
        "status": "finalized",
        "best": _candidate_to_dict(state.best) if state.best else None,
        "consumed_fits": state.consumed_fits,
        "visited_count": len(state.visited_topology),
        "best_history": state.best_history,
    }


def _action_clear(galaxy_dir: str, purge_disk: bool, note: str) -> dict:
    galaxy_key = os.path.abspath(galaxy_dir) if galaxy_dir else ""
    with _LOCK:
        if galaxy_key:
            _STATES.pop(galaxy_key, None)
        else:
            _STATES.clear()
    removed_disk = False
    if purge_disk and galaxy_key:
        try:
            os.remove(_state_path(galaxy_key))
            removed_disk = True
        except FileNotFoundError:
            pass
    return {
        "status": "cleared",
        "galaxy_key": galaxy_key,
        "memory_cleared": True,
        "disk_cleared": removed_disk,
    }


# ── MCP tool entry point ────────────────────────────────────────────────────


def beam_search_state(
    action: Annotated[
        str,
        "One of: init, enqueue, pop_next, mark_visited, is_visited, "
        "update_best, get_status, finalize, clear",
    ],
    galaxy_dir: Annotated[
        str,
        "Absolute path to the galaxy main directory (parent of output/). "
        "Used as the state key.",
    ],
    lyric_file: Annotated[
        str,
        "Path to the .lyric file. Required for init; used for topology hashing "
        "when candidate payload omits target_topology_hash.",
    ] = "",
    candidate: Annotated[
        str,
        "JSON string encoding a candidate payload. Used by enqueue/mark_visited/"
        "update_best. See workflow doc for schema.",
    ] = "",
    budget: Annotated[int, "init: total fit budget (default 15)."] = 15,
    beam_width: Annotated[int, "init: beam width (default 4)."] = 4,
    early_stop_k: Annotated[
        int, "init: consecutive no-improve rounds before early stop (default 3)."
    ] = 3,
    early_stop_eps: Annotated[
        float, "init: min h-delta to count as improvement (default 0.05)."
    ] = 0.05,
    fit_count_delta: Annotated[
        int, "mark_visited: increment consumed_fits by this (default 1)."
    ] = 1,
    purge_disk: Annotated[
        bool, "clear: also remove the .beam_search.json file (default false)."
    ] = False,
    note: Annotated[str, "Free-text annotation appended to audit log."] = "",
) -> dict:
    """Beam-search state store for the HBSS workflow.

    All queue/visited/best state for a galaxy MUST go through this tool. The
    orchestrating LLM must NOT maintain these in its own context.

    Lifecycle:
      init → (enqueue → pop_next → fit → mark_visited → update_best)* → finalize

    Pairing rules:
      - pop_next MUST be called before fit; returns the candidate to fit.
      - mark_visited MUST be called after fit; records fit_round_label.
      - update_best compares fitness_metrics with the incumbent and updates s_best.

    All ops are idempotent on failure: re-calling mark_visited with the same
    candidate_id is safe.
    """
    if action not in _VALID_ACTIONS:
        return {
            "status": "failure",
            "error": f"Unknown action: {action!r}. Valid: {sorted(_VALID_ACTIONS)}",
        }

    if not galaxy_dir and action != "clear":
        return {"status": "failure", "error": "galaxy_dir is required"}

    # init is special: it creates the state
    if action == "init":
        return _action_init(
            galaxy_dir=galaxy_dir,
            lyric_file=lyric_file,
            budget=budget,
            beam_width=beam_width,
            early_stop_k=early_stop_k,
            early_stop_eps=early_stop_eps,
            note=note,
        )

    if action == "clear":
        return _action_clear(galaxy_dir=galaxy_dir, purge_disk=purge_disk, note=note)

    galaxy_key = os.path.abspath(galaxy_dir)
    state = get_state(galaxy_key)
    if state is None:
        return {
            "status": "failure",
            "error": f"No beam_search state for {galaxy_key}. Call action=init first.",
        }

    if action == "get_status":
        return _action_get_status(state)

    if action == "is_visited":
        return _action_is_visited(state, lyric_file or candidate)

    # candidate-payload actions
    payload: dict = {}
    if candidate:
        try:
            payload = json.loads(candidate)
            if not isinstance(payload, dict):
                return {"status": "failure", "error": "candidate must be a JSON object"}
        except json.JSONDecodeError as e:
            return {"status": "failure", "error": f"candidate JSON parse failed: {e}"}

    if action == "enqueue":
        parent_id = state.best.candidate_id if state.best else None
        return _action_enqueue(state, payload, parent_id=parent_id, note=note)

    if action == "pop_next":
        return _action_pop_next(state, note=note)

    if action == "mark_visited":
        cid = payload.get("candidate_id", "")
        if not cid:
            return {"status": "failure", "error": "candidate_id missing in payload"}
        return _action_mark_visited(
            state=state,
            candidate_id=cid,
            fit_round_label=payload.get("fit_round_label"),
            fit_summary_path=payload.get("fit_summary_path"),
            fit_comparison_path=payload.get("fit_comparison_path"),
            lyric_path=payload.get("lyric_path"),
            fit_count_delta=fit_count_delta,
            note=note,
        )

    if action == "update_best":
        cid = payload.get("candidate_id", "")
        if not cid:
            return {"status": "failure", "error": "candidate_id missing in payload"}
        return _action_update_best(
            state=state,
            candidate_id=cid,
            fitness_metrics=payload.get("fitness_metrics", {}) or {},
            lyric_path=payload.get("lyric_path"),
            note=note,
        )

    if action == "finalize":
        return _action_finalize(state, note=note)

    # Unreachable (action validated above)
    return {"status": "failure", "error": f"Unhandled action: {action}"}
