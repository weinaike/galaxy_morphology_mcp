"""BeamGraph: the networkx state graph + priority queue of the beam search.

Single source of truth for the mechanised workflow (replaces the
orchestrator-maintained working_note ledgers):

* nodes  = fitted states (id = round label "A.0" / "A.4" / "B.2");
* edges  = EXECUTED actions (parent -> child);
* pending candidates live in ``G.graph["pending"]`` (action_id -> record) and
  are promoted to real edges on execution;
* graph-level attrs carry meta / stage-1 conclusions / counters / queue /
  refuted hypotheses / verified basins / temporary constraints.

Persistence: ``<galaxy_dir>/beam_state/graph.json`` (node_link_data), written
atomically (tmp + os.replace) with a ``.bak`` fallback.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

import networkx as nx

from beam.cons_decode import decode_cons_file, effective_band, number_components
from beam.signature import (
    canonical_signature,
    combo_identity,
    find_zombies,
)

SCHEMA_VERSION = 1
GRAPH_SUBDIR = "beam_state"
GRAPH_FILE = "graph.json"

# Hard constants of the workflow (not adjusted per galaxy)
BEAM_WIDTH = 5
N_MAX = 15
STAGNATION_MAX = 5
PER_COMBO_CAP = 4
G_MIN = 0.3
REFUTE_DBIC = 10.0
# verifier-FAIL repair budget: bounded grant, cumulative cap (fits)
REPAIR_BUDGET_CAP = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _metric_key(metrics: dict) -> float | None:
    """BIC convention: BIC_eff first, fall back to bic1d, then chi2."""
    for key in ("bic_eff", "bic1d", "chi2_nu"):
        v = metrics.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


class BeamGraph:
    def __init__(self, graph: nx.DiGraph, path: str):
        self.g = graph
        self.path = path
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ init
    @classmethod
    def init(cls, galaxy_dir: str, root_feedme: str, stage1: dict,
             psf_fwhm_px: float | None = None, a_psf_px2: float | None = None,
             temporary_constraints: list[dict] | None = None,
             beam_width: int | None = None, n_max: int | None = None,
             stagnation_max: int | None = None,
             ablations: dict | None = None) -> "BeamGraph":
        g = nx.DiGraph()
        g.graph["schema_version"] = SCHEMA_VERSION
        g.graph["meta"] = {
            "galaxy_dir": os.path.abspath(galaxy_dir),
            "created_at": _now(),
            "psf_fwhm_px": psf_fwhm_px,
            "a_psf_px2": a_psf_px2,
            "W": int(beam_width) if beam_width else BEAM_WIDTH,
            "N_max": int(n_max) if n_max else N_MAX,
            "stagnation_max": int(stagnation_max) if stagnation_max else STAGNATION_MAX,
            "per_combo_cap": PER_COMBO_CAP,
            "g_min": G_MIN,
        }
        # Ablation arm (paper): persisted in the graph so every later call
        # (survey_round / record_fit / enqueue) reads the SAME arm — no drift,
        # and the artefact itself documents which variant produced it.
        if ablations:
            g.graph["meta"]["ablations"] = {k: bool(v) for k, v in dict(ablations).items()}
        g.graph["stage1"] = stage1 or {}
        g.graph["temporary_constraints"] = temporary_constraints or []
        g.graph["queue"] = []            # ordered action_ids (pending)
        g.graph["pending"] = {}          # action_id -> candidate record
        g.graph["counters"] = {
            "global_iter_id": 0, "stagnation": 0, "n_executed": 0, "n_failed": 0,
        }
        g.graph["best_state"] = None
        g.graph["refuted_hypotheses"] = []
        g.graph["verified_basins"] = []
        g.graph["proposal_counts"] = {}  # combo_key -> times proposed (any fate)
        g.graph["decision_log"] = []     # cross-branch decision log entries
        g.graph["repair_budget"] = {"granted": 0, "cap": REPAIR_BUDGET_CAP, "log": []}

        fit_region = None
        try:
            from tools.parse_feedme import parse_feedme
            header = parse_feedme(root_feedme)
            region = header.get("fit_region")
            if region and len(region) == 4:
                fit_region = tuple(float(v) for v in region)
        except Exception:
            header = {}
        if fit_region:
            g.graph["meta"]["fit_region"] = list(fit_region)

        inventory = list(number_components(root_feedme).values())
        root_label = "A.0"
        g.add_node(root_label, **{
            "label": root_label,
            "global_iter_id": 0,
            "parent": None,
            "action_in": "",
            "signature": canonical_signature(inventory),
            "inventory": inventory,
            "metrics": {},
            "verdict": None,
            "zombies": [],
            "depth": 0,
            "archive_dir": None,
            "artifacts": {"feedme": os.path.abspath(root_feedme)},
            "cons_effective": {},
            "is_best": False,
            "combo_key": combo_identity(inventory),
            "created_at": _now(),
            "executed_at": None,
        })
        graph = cls(g, cls._graph_path(galaxy_dir))
        graph.commit()
        return graph

    @staticmethod
    def _graph_path(galaxy_dir: str) -> str:
        return os.path.join(galaxy_dir, GRAPH_SUBDIR, GRAPH_FILE)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, galaxy_dir: str) -> "BeamGraph":
        path = cls._graph_path(galaxy_dir)
        if not os.path.exists(path):
            raise FileNotFoundError(f"beam graph not found at {path} (run beam_init first)")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            g = nx.node_link_graph(data, directed=True, multigraph=False)
        except (json.JSONDecodeError, ValueError):
            bak = path + ".bak"
            if os.path.exists(bak):
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                g = nx.node_link_graph(data, directed=True, multigraph=False)
            else:
                raise
        if int(g.graph.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError(
                f"beam graph schema version {g.graph.get('schema_version')} != {SCHEMA_VERSION}"
            )
        graph = cls(g, path)
        graph._ensure_repair_budget()
        return graph

    def _ensure_repair_budget(self) -> None:
        rb = self.g.graph.get("repair_budget")
        if not isinstance(rb, dict) or "granted" not in rb:
            self.g.graph["repair_budget"] = {
                "granted": 0, "cap": REPAIR_BUDGET_CAP, "log": [],
            }

    def grant_repair_budget(self, rounds: int, reason: str = "") -> dict:
        """Grant extra fits after a best-round-verifier FAIL (bounded).

        Cumulative grants are capped at ``cap``; crash/failure rounds do not
        count toward stagnation, so a repair loop keeps moving while budget
        remains.
        """
        self._ensure_repair_budget()
        rb = self.g.graph["repair_budget"]
        room = max(0, int(rb.get("cap", REPAIR_BUDGET_CAP)) - int(rb.get("granted", 0)))
        granted = max(0, min(int(rounds), room))
        if granted:
            rb["granted"] = int(rb.get("granted", 0)) + granted
        rb.setdefault("log", []).append({
            "at": _now(), "requested": int(rounds), "granted": granted,
            "reason": reason,
        })
        self.log_decision({"kind": "repair-budget-grant", "requested": int(rounds),
                           "granted": granted, "reason": reason,
                           "budget_left": self.budget_left()})
        return dict(rb, budget_left=self.budget_left())

    # --------------------------------------------------------------- commit
    def commit(self) -> None:
        with self._lock:
            data = nx.node_link_data(self.g)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)
            if os.path.exists(self.path):
                os.replace(self.path, self.path + ".bak")
            os.replace(tmp, self.path)

    # -------------------------------------------------------------- helpers
    def counters(self) -> dict:
        return self.g.graph.setdefault("counters", {})

    def n_total(self) -> int:
        c = self.counters()
        return int(c.get("n_executed", 0)) + int(c.get("n_failed", 0))

    def log_decision(self, entry: dict) -> None:
        entry = dict(entry)
        entry.setdefault("at", _now())
        self.g.graph.setdefault("decision_log", []).append(entry)

    def state(self, label: str) -> dict:
        return dict(self.g.nodes[label])

    def latest_state(self) -> str:
        """Most recently executed state label (max global_iter_id)."""
        best_label, best_iter = None, -1
        for label, attrs in self.g.nodes(data=True):
            if attrs.get("global_iter_id", 0) > best_iter:
                best_label, best_iter = label, attrs["global_iter_id"]
        return best_label

    def _next_local_round(self, branch: str) -> int:
        top = 0
        for label in self.g.nodes:
            if label.startswith(f"{branch}."):
                try:
                    top = max(top, int(label.split(".")[1]))
                except (IndexError, ValueError):
                    continue
        return top + 1

    # ----------------------------------------------------------- add pending
    def add_pending(self, candidate: dict, source_session: str,
                    parent_label: str) -> str:
        """Register a validated candidate as a pending action. Returns action_id.

        ``candidate`` keys: sigma, expected_behavior_tag, expected_C_prime,
        novelty_claim, primitives, code_flags (optional), surveyor_rank.
        """
        pending = self.g.graph.setdefault("pending", {})
        action_id = candidate.get("action_id") or f"{parent_label}-c{len(pending) + 1}"
        while action_id in pending:
            action_id += "x"
        branch = parent_label.split(".")[0]
        record = {
            "action_id": action_id,
            "branch": branch,
            "parent": parent_label,
            "primitives": candidate.get("primitives", []),
            "source_session": source_session,
            "source_round": parent_label,
            "sigma": float(candidate.get("sigma", 0.0) or 0.0),
            "surveyor_rank": candidate.get("surveyor_rank"),
            "code_flags": candidate.get("code_flags", {}),
            "score": candidate.get("score"),
            "status": "pending",
            "discard_reason": None,
            "expected_C_prime": candidate.get("expected_C_prime", ""),
            "novelty_claim": candidate.get("novelty_claim", ""),
            "expected_behavior_tag": candidate.get("expected_behavior_tag", ""),
            "target_state": None,
            "age_counter": 0,
            "enqueued_at": _now(),
        }
        pending[action_id] = record
        self.g.graph.setdefault("queue", []).append(action_id)
        # proposal accounting for the never-executed precheck
        key = candidate.get("combo_key") or combo_identity(
            self._hypothesized_inventory(parent_label, candidate.get("primitives", [])))
        counts = self.g.graph.setdefault("proposal_counts", {})
        counts[key] = counts.get(key, 0) + 1
        return action_id

    def _hypothesized_inventory(self, parent_label: str, primitives: list[dict]) -> list[dict]:
        from beam.signature import apply_primitives_to_inventory

        parent = self.state(parent_label)
        inv, _err = apply_primitives_to_inventory(parent.get("inventory", []), primitives)
        return inv if inv is not None else parent.get("inventory", [])

    # ---------------------------------------------------------- record fit
    def record_fit(self, run_result: dict, action_id: str = "",
                   verdict: dict | None = None,
                   parent_label: str | None = None) -> str:
        """Register a successful fit as a new state node; update counters/best.

        ``run_result`` is the run_galfit success dict (input_param_file,
        output_param_file, image_file, summary_file, round_status_file,
        fit_statistics). ``action_id`` empty = the deterministic first fit.
        """
        c = self.counters()
        c["n_executed"] = int(c.get("n_executed", 0)) + 1

        if action_id:
            rec = self.g.graph["pending"].get(action_id)
            if rec is None:
                raise KeyError(f"unknown action_id {action_id}")
            parent_label = rec["parent"]
            branch = rec["branch"]
            # the iter id was consumed at feedme-write time (apply_candidate)
            if rec.get("iter_id"):
                iter_id = int(rec["iter_id"])
                c["global_iter_id"] = max(int(c.get("global_iter_id", 0)), iter_id)
            else:
                c["global_iter_id"] = int(c.get("global_iter_id", 0)) + 1
                iter_id = c["global_iter_id"]
        else:
            c["global_iter_id"] = int(c.get("global_iter_id", 0)) + 1
            iter_id = c["global_iter_id"]
            parent_label = parent_label or "A.0"
            branch = parent_label.split(".")[0]

        parent = self.state(parent_label)
        label = f"{branch}.{self._next_local_round(branch)}"

        fit_stats = dict(run_result.get("fit_statistics") or {})
        metrics = {k: fit_stats.get(k) for k in
                   ("chi2_nu", "chisq1d_nu", "bic1d", "bic_eff", "sky_value",
                    "psf_fwhm", "a_psf", "n_free", "n_data")}
        metrics["convergence"] = fit_stats.get("convergence", {"flag": "ok"})

        input_param_file = run_result.get("input_param_file", "")
        output_param_file = run_result.get("output_param_file", "")
        fitted_inv = list(number_components(output_param_file,
                                            name_file=input_param_file).values()) \
            if output_param_file else []
        input_inv = list(number_components(input_param_file).values()) \
            if input_param_file else []

        cons_effective = self._decode_cons_for(input_param_file, input_inv)

        zombies = find_zombies(fitted_inv) if fitted_inv else []
        archive_dir = None
        if run_result.get("round_status_file"):
            archive_dir = os.path.dirname(os.path.abspath(run_result["round_status_file"]))

        self.g.add_node(label, **{
            "label": label,
            "global_iter_id": iter_id,
            "parent": parent_label,
            "action_in": action_id,
            "signature": canonical_signature(fitted_inv or input_inv,
                                             cons_bands=cons_effective),
            "inventory": fitted_inv or input_inv,
            "input_inventory": input_inv,
            "metrics": metrics,
            "verdict": None,  # settled by apply_verdict (idempotence guard keys on it)
            "zombies": zombies,
            "depth": int(parent.get("depth", 0)) + 1,
            "archive_dir": archive_dir,
            "artifacts": {
                "feedme": input_param_file,
                "galfit_nn": output_param_file,
                "comparison_png": run_result.get("image_file", ""),
                "summary": run_result.get("summary_file", ""),
                "round_status": run_result.get("round_status_file", ""),
            },
            "cons_effective": cons_effective,
            "is_best": False,
            "combo_key": combo_identity(fitted_inv or input_inv),
            "created_at": _now(),
            "executed_at": _now(),
        })
        self.g.add_edge(parent_label, label, action_id=action_id or f"{parent_label}-firstfit")

        # deterministic numeric checks (physicality defect A: the numeric half
        # of the verdict is mechanical; the VLM keeps the visual half)
        try:
            from beam.physicality import compute_mech_checks

            mech = compute_mech_checks(self, self.state(label))
        except Exception as e:  # never block fit recording on check errors
            mech = [{"severity": "note", "check": "internal",
                     "detail": f"mech-check computation failed: {e}"}]
        self.g.nodes[label]["mech_checks"] = mech

        if action_id and action_id in self.g.graph.get("pending", {}):
            rec = self.g.graph["pending"][action_id]
            rec["status"] = "executed"
            rec["target_state"] = label
            self._drop_from_queue(action_id)

        if verdict is not None:
            self.apply_verdict(label, verdict, parent_label=parent_label)
        elif any(c.get("severity") == "hard" for c in mech):
            # Fail-safe (KILOGAS_231 2026-09-16 incident): a driver that never
            # settles verdicts (no survey_round, no verdict_json) must not be
            # able to bypass the physicality gate — mech-hard entries alone
            # settle FAIL here, exactly what merge_verdict would produce after
            # any surveyor call. States without mech-hard stay unsettle-pending
            # until survey_round runs.
            self.apply_verdict(label, {"verdict": "FAIL", "failed_checks": [],
                                       "swap_hint": "none"},
                               parent_label=parent_label)
        self.commit()
        return label

    def apply_verdict(self, label: str, verdict: dict,
                      parent_label: str | None = None) -> None:
        """Attach the surveyor's Physicality Verdict to a fitted state and
        settle best/refutation bookkeeping (verdict-gated).

        Called by survey_round after parsing the verdict (the natural order is
        fit -> beam_record_fit -> survey_round); beam_record_fit may also pass
        the verdict directly. Stagnation settles exactly once per state.

        The mechanical check table (computed at record_fit time) is merged
        here: any hard entry vetoes PASS (``mech_veto``), mechanical entries
        are appended to ``failed_checks`` prefixed ``[mech-hard]/[mech-note]``,
        and the surveyor's original verdict is preserved in ``verdict_vlm``.
        """
        state = self.state(label)
        if (state.get("verdict") or {}).get("verdict") in {"PASS", "FAIL"}:
            return  # already settled — idempotent
        from beam.physicality import merge_verdict

        original = dict(verdict)
        verdict, vetoed = merge_verdict(state.get("mech_checks") or [], verdict)
        if vetoed:
            self.g.nodes[label]["verdict_vlm"] = original
        self.g.nodes[label]["verdict"] = verdict
        self._update_best_and_refutations(label,
                                           parent_label or state.get("parent"),
                                           verdict)
        self.commit()

    def _decode_cons_for(self, feedme: str, input_inv: list[dict]) -> dict:
        """Effective .cons bands {(number, param): (lo, hi)} for a feedme."""
        if not feedme:
            return {}
        try:
            from tools.parse_feedme import parse_feedme
            cons_rel = parse_feedme(feedme).get("constraint")
        except Exception:
            return {}
        if not cons_rel or cons_rel.lower() == "none":
            return {}
        cons_file = cons_rel if os.path.isabs(cons_rel) else \
            os.path.join(os.path.dirname(os.path.abspath(feedme)), cons_rel)
        if not os.path.exists(cons_file):
            return {}
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

    def _update_best_and_refutations(self, label: str, parent_label: str,
                                     verdict: dict | None) -> None:
        state = self.state(label)
        metrics = state.get("metrics", {})
        conv_ok = (metrics.get("convergence") or {}).get("flag", "ok") != "sub-converged"
        verdict_pass = not (verdict and verdict.get("verdict") == "FAIL")
        # ablation arm no_verdict_gate: metric-only best selection — FAIL
        # rounds may take s* (verdicts are still recorded, so post-hoc
        # analysis can identify unphysical selections)
        if (self.g.graph.get("meta", {}).get("ablations") or {}).get("no_verdict_gate"):
            verdict_pass = True

        # mechanical best update: verdict-gated, sub-convergence-gated, BIC-first
        eligible = verdict_pass and conv_ok and _metric_key(metrics) is not None
        current_best = self.g.graph.get("best_state")
        if eligible:
            best_metrics = self.state(current_best).get("metrics", {}) if current_best else {}
            new_key, old_key = _metric_key(metrics), _metric_key(best_metrics)
            if old_key is None or (new_key is not None and new_key < old_key - 1e-9):
                if current_best:
                    self.g.nodes[current_best]["is_best"] = False
                self.g.nodes[label]["is_best"] = True
                self.g.graph["best_state"] = label
                self.counters()["stagnation"] = 0
            else:
                self.counters()["stagnation"] = int(self.counters().get("stagnation", 0)) + 1
        else:
            self.counters()["stagnation"] = int(self.counters().get("stagnation", 0)) + 1

        # refuted-hypothesis gating: only valid rounds may ground refutations
        if not conv_ok:
            self.log_decision({
                "kind": "sub-converged-quarantine", "state": label,
                "frozen": (metrics.get("convergence") or {}).get("frozen_free_params", []),
                "note": "inadmissible as refutation evidence (Refutation validity rule)",
            })
            return
        if verdict and verdict.get("verdict") == "FAIL":
            self.g.graph.setdefault("refuted_hypotheses", []).append({
                "state": label,
                "action": state.get("action_in"),
                "tag": (self.g.graph["pending"].get(state.get("action_in"), {})
                        or {}).get("expected_behavior_tag", ""),
                "evidence": f"physicality FAIL: {verdict.get('failed_checks')}",
                "reason": "physicality_fail",
                "reopening": "a repair candidate restoring the onion structure",
            })
            return
        if parent_label and conv_ok:
            parent_metrics = self.state(parent_label).get("metrics", {})
            new_key, old_key = _metric_key(metrics), _metric_key(parent_metrics)
            if new_key is not None and old_key is not None and new_key >= old_key + REFUTE_DBIC:
                self.g.graph.setdefault("refuted_hypotheses", []).append({
                    "state": label,
                    "action": state.get("action_in"),
                    "tag": (self.g.graph["pending"].get(state.get("action_in"), {})
                            or {}).get("expected_behavior_tag", ""),
                    "evidence": f"dBIC={new_key - old_key:+.1f} vs parent {parent_label}",
                    "reason": "bic_worse",
                    "reopening": "new residual evidence absent at refutation time",
                })

    # ------------------------------------------------------------ failures
    def mark_failed(self, action_id: str, reason: str) -> None:
        rec = self.g.graph["pending"].get(action_id)
        if rec is None:
            return
        rec["status"] = "failed"
        rec["discard_reason"] = reason
        self._drop_from_queue(action_id)
        c = self.counters()
        c["n_failed"] = int(c.get("n_failed", 0)) + 1
        # a crashed/diverged fit is NOT evidence of a stuck search: budget
        # already bounds the failure loop, stagnation stays untouched
        self.log_decision({"kind": "fit-failure", "action_id": action_id, "reason": reason})

    def mark_discarded(self, action_id: str, reason: str,
                       bump_stagnation: bool = True) -> None:
        """Dequeue-time / enqueue-time discard (R0/R1/R2/g_min/truncation...)."""
        rec = self.g.graph["pending"].get(action_id)
        if rec is None:
            return
        rec["status"] = "discarded"
        rec["discard_reason"] = reason
        self._drop_from_queue(action_id)
        if bump_stagnation:
            self.counters()["stagnation"] = int(self.counters().get("stagnation", 0)) + 1
        self.log_decision({"kind": "discard", "action_id": action_id, "reason": reason})

    def _drop_from_queue(self, action_id: str) -> None:
        q = self.g.graph.get("queue", [])
        self.g.graph["queue"] = [a for a in q if a != action_id]

    # ---------------------------------------------------------------- queue
    def pending_queue(self) -> list[str]:
        return list(self.g.graph.get("queue", []))

    def pending_record(self, action_id: str) -> dict | None:
        rec = self.g.graph.get("pending", {}).get(action_id)
        return dict(rec) if rec else None

    def pop_next(self) -> str | None:
        q = self.g.graph.get("queue", [])
        return q[0] if q else None

    def set_queue(self, ordered_ids: list[str]) -> None:
        self.g.graph["queue"] = list(ordered_ids)

    def age_pending(self) -> None:
        for rec in self.g.graph.get("pending", {}).values():
            if rec.get("status") == "pending":
                rec["age_counter"] = int(rec.get("age_counter", 0)) + 1

    def best_path_labels(self) -> set[str]:
        """Labels on the current best branch (best state + its ancestors).

        Falls back to the latest state's ancestor chain while no PASS state
        exists. The starvation-aging bonus applies only to candidates whose
        parent lies on this path (KILOGAS_319 defect C: an aged candidate
        from a long-superseded parent outranked fresh repairs and wasted a
        fit).
        """
        cur = self.g.graph.get("best_state") or self.latest_state()
        labels: set[str] = set()
        while cur and cur in self.g.nodes and cur not in labels:
            labels.add(cur)
            cur = self.g.nodes[cur].get("parent")
        return labels

    # ------------------------------------------------------------- traversal
    def traversal(self) -> list[dict]:
        """The execution-ordered walk through the beam (search trajectory).

        One entry per consumed iter id, ascending: the root state, every
        fitted state (label, parent, action_in, combo, BIC, verdict, best
        flag) and — interleaved at their iter position — failed executions
        (GALFIT crashes have no state node; they appear as kind="failed").
        """
        entries: list[dict] = [{
            "kind": "state", "iter": 0, "label": "A.0", "parent": None,
            "action": "", "tag": "", "combo": self.state("A.0").get("combo_key"),
            "bic_eff": None, "verdict": None, "is_best": False,
        }] if "A.0" in self.g.nodes else []
        for _label, attrs in self.g.nodes(data=True):
            if attrs.get("global_iter_id", 0) == 0:
                continue
            entries.append({
                "kind": "state",
                "iter": int(attrs.get("global_iter_id", 0)),
                "label": attrs.get("label"),
                "parent": attrs.get("parent"),
                "action": attrs.get("action_in", ""),
                "tag": (self.g.graph.get("pending", {})
                        .get(attrs.get("action_in", ""), {}) or {})
                        .get("expected_behavior_tag", ""),
                "combo": attrs.get("combo_key"),
                "bic_eff": _metric_key(attrs.get("metrics", {})),
                "verdict": (attrs.get("verdict") or {}).get("verdict"),
                "is_best": bool(attrs.get("is_best")),
            })
        for aid, rec in self.g.graph.get("pending", {}).items():
            if rec.get("status") == "failed" and rec.get("iter_id"):
                entries.append({
                    "kind": "failed",
                    "iter": int(rec["iter_id"]),
                    "label": None,
                    "parent": rec.get("parent"),
                    "action": aid,
                    "tag": rec.get("expected_behavior_tag", ""),
                    "combo": None, "bic_eff": None, "verdict": "CRASH",
                    "is_best": False,
                })
        entries.sort(key=lambda e: (e["iter"], e["label"] or ""))
        return entries

    # ------------------------------------------------- temporary constraints
    def set_temporary_constraint(self, action: str, text: str,
                                 forbid_structures: list[str] | None = None,
                                 issued: str = "") -> dict:
        """Add / deactivate a session-scoped user constraint.

        Enforced at enqueue time by ``_temp_constraint_violation`` (adds/tunes
        of a forbidden structure are discarded with E_TEMP_CONSTRAINT).
        ``action='remove'`` deactivates matching constraint(s) by text.
        """
        tcs = self.g.graph.setdefault("temporary_constraints", [])
        if action == "add":
            entry = {
                "issued": issued or _now()[:10],
                "text": text,
                "forbid_structures": [s.lower() for s in (forbid_structures or [])],
                "active": True,
            }
            if not any(t.get("text") == text for t in tcs):
                tcs.append(entry)
            self.log_decision({"kind": "temporary-constraint-add", "text": text,
                               "forbid_structures": entry["forbid_structures"]})
        elif action == "remove":
            hit = 0
            for t in tcs:
                if t.get("text") == text and t.get("active", True):
                    t["active"] = False
                    t["revoked_at"] = _now()
                    hit += 1
            self.log_decision({"kind": "temporary-constraint-remove", "text": text,
                               "deactivated": hit})
        else:
            raise ValueError(f"action must be 'add' or 'remove', got {action!r}")
        return {"active": [t for t in tcs if t.get("active", True)], "all": tcs}

    # -------------------------------------------------------------- queries
    def input_ledger_signatures(self) -> list[dict]:
        """Canonical input-side signatures of every executed state (R1 set)."""
        sigs = []
        for _label, attrs in sorted(self.g.nodes(data=True),
                                    key=lambda kv: kv[1].get("global_iter_id", 0)):
            if attrs.get("global_iter_id", 0) == 0:
                continue
            sigs.append(attrs.get("signature", {}))
        return sigs

    def result_ledger_states(self) -> list[str]:
        return [label for label, attrs in self.g.nodes(data=True)
                if attrs.get("global_iter_id", 0) > 0]

    def combo_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _label, attrs in self.g.nodes(data=True):
            if attrs.get("global_iter_id", 0) == 0:
                continue
            key = attrs.get("combo_key", "?")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def never_executed_precheck(self) -> list[dict]:
        """Inventories proposed >=2 times but never executed (termination gate)."""
        executed = set(self.combo_counts())
        out = []
        for key, count in self.g.graph.get("proposal_counts", {}).items():
            if count >= 2 and key not in executed:
                out.append({"combo": key, "proposed": count})
        return out

    def rollback_edges(self) -> list[dict]:
        """Executed edges whose primitives are closed-form transitions."""
        from beam.signature import project_closed_form

        out = []
        for u, v, data in self.g.edges(data=True):
            action_id = data.get("action_id", "")
            rec = self.g.graph.get("pending", {}).get(action_id)
            if not rec:
                continue
            proj = project_closed_form(self.state(u).get("inventory", []),
                                       rec.get("primitives", []))
            if proj is not None:
                out.append({"action_id": action_id, "from": u, "to": v,
                            "kind": proj.kind})
        return out

    def budget_left(self) -> int:
        self._ensure_repair_budget()
        rb = self.g.graph["repair_budget"]
        return int(self.g.graph["meta"].get("N_max", N_MAX)) \
            + int(rb.get("granted", 0)) - self.n_total()

    def floor_blockers(self) -> list[str]:
        """Pending candidates carrying a mandatory floor flag, queue-ordered.

        ``floor_*`` code_flags (set by enqueue.ingest) mark hypotheses the
        workflow declares mandatory (n-release, bar direction, disk-Re
        bottleneck, lens relax-D). The stagnation stop is suspended while
        one remains unexecuted, so a lock never rests on an untested
        mandatory direction (KILOGAS_296 A.2-c1 edge case).
        """
        out = []
        for aid in self.pending_queue():
            rec = self.g.graph.get("pending", {}).get(aid)
            if not rec or rec.get("status") != "pending":
                continue
            flags = rec.get("code_flags") or {}
            if any(str(k).startswith("floor_") and v for k, v in flags.items()):
                out.append(aid)
        return out

    def termination_check(self) -> dict:
        """Step-2 termination + never-executed precheck (hard gate).

        A termination condition fires but is SUSPENDED while a >=2-times
        proposed inventory has never been executed and budget remains (the
        precheck's option (a); option (b), a recorded direct refutation,
        removes the blocker from proposal_counts by hand in stage 4).

        Independently, the ``stagnation`` condition is suspended while a
        mandatory floor candidate is still pending and budget remains (the
        never-executed precheck is combo-based and cannot see candidate-level
        floors). ``budget_exhausted`` / ``queue_empty`` always stop.
        """
        meta = self.g.graph.get("meta", {})
        self._ensure_repair_budget()
        rb = self.g.graph["repair_budget"]
        n_max_effective = int(meta.get("N_max", N_MAX)) + int(rb.get("granted", 0))
        conditions = []
        if not self.pending_queue():
            conditions.append("queue_empty")
        if self.n_total() >= n_max_effective:
            conditions.append("budget_exhausted")
        if int(self.counters().get("stagnation", 0)) >= int(meta.get("stagnation_max", STAGNATION_MAX)):
            conditions.append("stagnation")
        blockers = self.never_executed_precheck()
        floors = self.floor_blockers()
        left = self.budget_left()
        suspended = bool(conditions) and bool(blockers) and left > 0
        floor_suspended = ("stagnation" in conditions) and bool(floors) and left > 0
        return {
            "stop": bool(conditions) and not suspended and not floor_suspended,
            "conditions": conditions,
            "suspended_by_never_executed": suspended,
            "never_executed_blockers": blockers,
            "suspended_by_floor": floor_suspended,
            "floor_blockers": floors,
            "budget_left": left,
            "repair_budget": dict(rb),
        }

    def next_action(self) -> str | None:
        """Queue head, or the highest-ranked floor candidate during suspension.

        While the stagnation stop is suspended by unexecuted mandatory floors,
        the next action to execute is the queue's highest-scored floor entry
        (not the plain queue head), so the mandatory direction is discharged
        first and the stop takes effect immediately afterwards.
        """
        q = self.pending_queue()
        if not q:
            return None
        if self.termination_check().get("suspended_by_floor"):
            floors = set(self.floor_blockers())
            for aid in q:
                if aid in floors:
                    return aid
        return q[0]

    def snapshot(self) -> dict:
        c = self.counters()
        best = self.g.graph.get("best_state")
        on_path = self.best_path_labels()
        meta = self.g.graph.get("meta", {})
        return {
            "config": {k: meta.get(k) for k in
                       ("W", "N_max", "stagnation_max", "per_combo_cap", "g_min")},
            "ablations": meta.get("ablations", {}),
            "best_state": best,
            "best_metrics": self.state(best).get("metrics", {}) if best else None,
            "best_combo": self.state(best).get("combo_key") if best else None,
            "counters": dict(c, n_total=self.n_total(), budget_left=self.budget_left()),
            "queue": [
                {**{k: self.g.graph["pending"][aid].get(k)
                    for k in ("action_id", "parent", "sigma", "score",
                              "expected_behavior_tag", "code_flags", "age_counter")},
                 "off_best_path": self.g.graph["pending"][aid].get("parent") not in on_path}
                for aid in self.pending_queue()
                if aid in self.g.graph.get("pending", {})
            ],
            "combo_counts": self.combo_counts(),
            "never_executed": self.never_executed_precheck(),
            "refuted": self.g.graph.get("refuted_hypotheses", []),
            "traversal": self.traversal(),
            "temporary_constraints": [t for t in self.g.graph.get("temporary_constraints", [])
                                      if t.get("active", True)],
            "states": sorted(
                ({"label": s, "combo": a.get("combo_key"), "bic": _metric_key(a.get("metrics", {})),
                  "verdict": (a.get("verdict") or {}).get("verdict"),
                  "mech_veto": bool((a.get("verdict") or {}).get("mech_veto")),
                  "is_best": a.get("is_best")}
                 for s, a in self.g.nodes(data=True)),
                key=lambda d: str(d["label"]),
            ),
        }
