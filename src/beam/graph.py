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

# Hard constants of the workflow (not adjusted per galaxy).
# 2026-10-09 austerity profile (KILOGAS_73 retrospective): N_max 15 -> 7
# (+1 reserved F1 arbitration fit on the best state, counted outside n via
# the f1_reserved flag), stagnation 5 -> 2 (early stop dominates at low
# budget), per-combo cap 4 -> 2 (the 3rd/4th attempt on one inventory never
# repaid its fit), W 5 -> 4 (queue slots are scarce at n=7).
BEAM_WIDTH = 4
N_MAX = 7
STAGNATION_MAX = 2
PER_COMBO_CAP = 2
G_MIN = 0.3
REFUTE_DBIC = 10.0
# verifier-FAIL repair budget: bounded grant, cumulative cap (fits)
REPAIR_BUDGET_CAP = 4
# sub-converged rounds are budget-free (non-information); stop after this
# many — the seeding/optimisation is then systematically broken
SUB_CONVERGED_LIMIT = 3


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
             q_iso_outer: float | None = None,
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
            "q_iso_outer": q_iso_outer,
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

    # ------------------------------------------------- reserved F1 arbitration
    def add_f1_reserved(self, amplitude: float, phase_deg: float,
                        action_id: str = "f1-arbitration") -> tuple[str | None, str]:
        """Create the Stage-3 reserved F1 arbitration candidate on the BEST
        state (the "7+1" slot). No VLM call: the primitive pair is built from
        the best state's fitted disk (remove+add rebuild with the F1 row —
        the shape the surveyor used and the transcription layer already
        accepts), warm-started by fourier_mode_analysis's am/theta_m.

        Guards: runs at most once per run (meta["f1"]); the best state must
        exist and carry no F1 yet. The record is exempt from the apply-time
        combo-cap re-check (tools.apply_candidate) and is counted outside
        N_max (record_fit's f1_reserved branch).

        Returns (action_id, "") on success or (None, reason) on refusal.
        """
        if (self.g.graph.get("meta", {}).get("f1") or {}).get("used"):
            return None, "the reserved F1 arbitration slot was already consumed"
        best = self.g.graph.get("best_state")
        if not best or best not in self.g.nodes:
            return None, "no best_state yet — the F1 arbitration needs a fitted best"
        best_state = self.state(best)
        feedme = (best_state.get("artifacts") or {}).get("feedme") or ""
        if feedme and os.path.exists(feedme):
            with open(feedme, encoding="utf-8") as f:
                if any(line.strip().upper().startswith("F1)") for line in f):
                    return None, f"state {best} already carries an F1 row"
        target = next((c for c in best_state.get("inventory", [])
                       if (c.get("name") or "").lower() in {"disk", "edgedisk"}), None) \
            or next((c for c in best_state.get("inventory", [])
                     if (c.get("name") or "").lower() == "singlesersic"), None)
        if target is None:
            return None, f"state {best} has no disk/edgedisk/singlesersic to carry F1"
        name = (target.get("name") or "disk").lower()
        ctype = (target.get("type") or "expdisk").lower()
        re_eff = target.get("re_effective", target.get("re"))
        add_params: dict = {
            "structure_name": name, "component_type": ctype,
            "x_px": target.get("x"), "y_px": target.get("y"),
            "mag": target.get("mag"), "re_px": re_eff,
            "q": target.get("q", target.get("ba")), "pa_deg": target.get("pa",
                                                                         target.get("pa_deg", 0.0)),
            "f1": {"amplitude": float(amplitude), "phase_deg": float(phase_deg)},
        }
        if ctype == "sersic" and target.get("n") is not None:
            add_params["n"] = target.get("n")
        add_params = {k: v for k, v in add_params.items() if v is not None}
        primitives = [{"op": "remove", "target": name},
                      {"op": "add", **add_params}]
        aid = self.add_pending({
            "action_id": action_id, "sigma": 0.5,
            "expected_behavior_tag": "f1_arbitration",
            "expected_C_prime": f"{best}+F1", "novelty_claim":
                "Stage-3 reserved F1 arbitration on the current best state",
            "primitives": primitives,
            "code_flags": {"f1_reserved": True}, "score": 0.95,
        }, source_session="f1-arbitration", parent_label=best)
        self.g.graph["pending"][aid]["surveyor_rank_pin"] = 1
        self.log_decision({"kind": "f1-reserved-enqueue", "action_id": aid,
                           "parent": best, "amplitude": float(amplitude),
                           "phase_deg": float(phase_deg)})
        self.commit()
        return aid, ""

    # ------------------------------------------------- opening alternates
    def opening_skeletons(self) -> list[dict]:
        """The round-0 opening skeleton candidates (VLM-authored), in queue
        order: pending records whose primitives carry a ``skeleton`` op
        (parented the unfitted root A.0). Returns
        [{action_id, inventory, sigma, rationale, status}]."""
        out: list[dict] = []
        for aid in self.pending_queue():
            rec = self.g.graph.get("pending", {}).get(aid)
            if not rec:
                continue
            skel = next((p for p in rec.get("primitives", [])
                         if p.get("op") == "skeleton"), None)
            if skel is None:
                continue
            comps = (skel.get("skeleton") or {}).get("components") \
                or skel.get("components") or []
            out.append({
                "action_id": aid,
                "inventory": sorted((c.get("structure_name") or "?").lower()
                                    for c in comps),
                "sigma": rec.get("sigma"),
                "rationale": rec.get("expected_behavior_tag", ""),
                "status": rec.get("status"),
            })
        # include executed/discarded ones too (audit trail), appended
        for aid, rec in self.g.graph.get("pending", {}).items():
            if any(p.get("op") == "skeleton" for p in rec.get("primitives", [])) \
                    and all(o.get("action_id") != aid for o in out):
                comps = next(((p.get("skeleton") or {}).get("components")
                              or p.get("components") or [])
                             for p in rec.get("primitives", []) if p.get("op") == "skeleton")
                out.append({
                    "action_id": aid,
                    "inventory": sorted((c.get("structure_name") or "?").lower()
                                        for c in comps),
                    "sigma": rec.get("sigma"),
                    "rationale": rec.get("expected_behavior_tag", ""),
                    "status": rec.get("status"),
                })
        return out

    def enqueue_opening_alternates(self, first_state: str) -> list[dict]:
        """Enqueue the non-top opening skeletons (stage1.opening_candidates)
        as pending candidates parented on the FIRST FITTED state.

        The opening response ranks skeleton proposals; rank 1 was written by
        the orchestrator as the root feedme and fitted as ``first_state``;
        every further entry must carry ``primitives`` RELATIVE to the rank-1
        skeleton (usually a single remove) — validated by re-applying them to
        the first state's inventory and comparing against the declared
        ``inventory`` name set. Alternates that fail validation are logged
        and skipped, never fatal.
        """
        stage1 = self.g.graph.get("stage1", {})
        openings = stage1.get("opening_candidates") or {}
        if self.g.graph["meta"].get("opening_alternates_enqueued"):
            return []
        result: list[dict] = []
        base = self.state(first_state).get("inventory", [])
        base_names = {(c.get("name") or "").lower() for c in base}
        cands = openings.get("candidates")
        if not isinstance(cands, list):
            cands = []
        for i, cand in list(enumerate(cands))[1:]:
            prims = cand.get("primitives") or []
            declared = {(n or "").lower() for n in (cand.get("inventory") or [])}
            if not prims or not declared:
                self.log_decision({"kind": "opening-alternate-skip", "rank": i,
                                   "reason": "missing primitives/inventory"})
                continue
            from beam.signature import apply_primitives_to_inventory

            hypo, err = apply_primitives_to_inventory(base, prims)
            if hypo is None:
                self.log_decision({"kind": "opening-alternate-skip", "rank": i,
                                   "reason": f"E_APPLY: {err}"})
                continue
            hypo_names = {(c.get("name") or "").lower() for c in hypo}
            if hypo_names != declared:
                self.log_decision({"kind": "opening-alternate-skip", "rank": i,
                                   "reason": f"declared {sorted(declared)} != projected "
                                             f"{sorted(hypo_names)}"})
                continue
            if hypo_names == base_names:
                continue
            aid = self.add_pending({
                "action_id": cand.get("action_id") or f"open-alt-{i}",
                "sigma": float(cand.get("sigma", 0.4) or 0.4),
                "expected_behavior_tag": cand.get("rationale",
                                                  f"opening alternate #{i}")[:80],
                "expected_C_prime": "+".join(sorted(hypo_names)),
                "novelty_claim": "opening skeleton alternate "
                                 "(untried, proposed at Stage 1)",
                "primitives": prims,
                "code_flags": {"opening_alternate": True},
                "score": float(cand.get("sigma", 0.4) or 0.4),
            }, source_session="opening", parent_label=first_state)
            result.append({"action_id": aid, "rank": i,
                           "inventory": sorted(hypo_names)})
        self.g.graph["meta"]["opening_alternates_enqueued"] = True
        if result:
            self.log_decision({"kind": "opening-alternates-enqueued",
                               "parent": first_state, "alternates": result})
        self.commit()
        return result

    # ---------------------------------------------------------- record fit
    def record_fit(self, run_result: dict, action_id: str = "",
                   verdict: dict | None = None,
                   parent_label: str | None = None) -> str:
        """Register a successful fit as a new state node; update counters/best.

        ``run_result`` is the run_galfit success dict (input_param_file,
        output_param_file, image_file, summary_file, round_status_file,
        fit_statistics). ``action_id`` empty = the deterministic first fit.

        A pending record carrying ``code_flags.f1_reserved`` (the Stage-3
        F1 arbitration round on the best state) is counted in the separate
        ``n_f1_reserved`` counter, NOT in ``n_executed`` — the reserved fit
        rides on top of N_max ("7+1"), and it runs exactly once (the guard
        lives in beam_enqueue_f1 / meta["f1"]).

        A [sub-converged] round is likewise counted in ``n_sub_converged``,
        not in ``n_executed`` (KILOGAS_73 austerity rerun: fits 5-6 burned
        2/7 of the budget on frozen-optimiser rounds that carried no
        information — the Refutation validity rule already declares them
        non-evidence, the combo cap already exempts them; the budget now
        does too, bounded by the ``sub_converged_limit`` termination
        condition so a systematically broken seeding cannot loop forever).
        """
        fit_stats_early = dict(run_result.get("fit_statistics") or {})
        conv_early = ((fit_stats_early.get("convergence") or {}).get("flag"))
        c = self.counters()
        if action_id:
            rec0 = self.g.graph["pending"].get(action_id) or {}
            f1_reserved = bool((rec0.get("code_flags") or {}).get("f1_reserved"))
        else:
            f1_reserved = False
        if f1_reserved:
            c["n_f1_reserved"] = int(c.get("n_f1_reserved", 0)) + 1
        elif conv_early == "sub-converged":
            c["n_sub_converged"] = int(c.get("n_sub_converged", 0)) + 1
        else:
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

        if f1_reserved:
            # mark the reserved F1 slot consumed — beam_enqueue_f1 refuses a
            # second arbitration round regardless of the outcome (the BIC
            # comparison against the pre-F1 best settles retention)
            self.g.graph["meta"]["f1"] = {"used": True, "state": label,
                                          "parent": parent_label, "at": _now()}

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
        """Age every pending entry by one round, then EVICT stale off-path ones.

        Stale eviction (KILOGAS_73 austerity rerun: A.1-c4 sat age=8 at the
        queue head while add_agn_core from a superseded parent survived 3
        rounds and burned fit 7): a pending candidate that is >3 rounds old,
        carries NO code_flags/pin (floors, opening skeletons and the reserved
        F1 are exempt) and whose parent lies OFF the current best path is
        discarded — at a 7-fit budget queue slots are too scarce for zombie
        directions. (The aging BONUS stays on-path only; off-path entries no
        longer accumulate value with age.)
        """
        for rec in self.g.graph.get("pending", {}).values():
            if rec.get("status") == "pending":
                rec["age_counter"] = int(rec.get("age_counter", 0)) + 1
        on_path = self.best_path_labels()
        for aid in list(self.g.graph.get("queue", [])):
            rec = self.g.graph.get("pending", {}).get(aid)
            if not rec or rec.get("status") != "pending":
                continue
            if rec.get("code_flags") or rec.get("surveyor_rank_pin"):
                continue
            if rec.get("parent") in on_path:
                continue
            if int(rec.get("age_counter", 0)) > 3:
                rec["status"] = "discarded"
                rec["discard_reason"] = "STALE_OFFPATH"
                self._drop_from_queue(aid)
                self.log_decision({"kind": "stale-offpath-eviction",
                                   "action_id": aid,
                                   "age": rec.get("age_counter"),
                                   "parent": rec.get("parent")})

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

    def combo_counts(self, valid_only: bool = True) -> dict[str, int]:
        """Executed attempts per combination (cap accounting).

        valid_only=True (default, per-combination attempt cap): [sub-converged]
        rounds are excluded — the Refutation validity rule declares the
        optimiser never explored, so the attempt never happened and a
        corrected re-run must remain legal (KILOGAS_432/353, Plate0295
        A.10→A.11 chain). The reserved F1 arbitration round is ALSO excluded:
        it is exempt from the combo gates at apply time by construction, and
        counting it exhausted the best inventory right after the F1 retention
        (KILOGAS_73 austerity rerun: the post-F1 survey's candidates were
        E_COMBO_EXHAUSTED-rejected in a retry storm). valid_only=False counts
        every fit that ran (used by never_executed_precheck: a sub-converged
        round still proves the inventory was executed).
        """
        pending = self.g.graph.get("pending", {})
        counts: dict[str, int] = {}
        for _label, attrs in self.g.nodes(data=True):
            if attrs.get("global_iter_id", 0) == 0:
                continue
            if valid_only:
                conv = ((attrs.get("metrics") or {}).get("convergence") or {}).get("flag")
                if conv == "sub-converged":
                    continue
                rec = pending.get(attrs.get("action_in") or "") or {}
                if (rec.get("code_flags") or {}).get("f1_reserved"):
                    continue
            key = attrs.get("combo_key", "?")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def never_executed_precheck(self) -> list[dict]:
        """Inventories proposed >=2 times but never executed (termination gate)."""
        executed = set(self.combo_counts(valid_only=False))
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

        Discharge rule (Plate0295 incident 2026-09-17): a floor is
        SATISFIED — and its pending candidate auto-discarded — once the
        same inventory already has a valid (convergence ok) executed round
        testing the same floor hypothesis. Re-running a mandatory
        experiment on a combo that has already validly run it burns budget
        without new information (A.12: n-release re-executed after A.11
        had already provided the valid release on the same combo).
        """
        self._discharge_satisfied_floors()
        out = []
        for aid in self.pending_queue():
            rec = self.g.graph.get("pending", {}).get(aid)
            if not rec or rec.get("status") != "pending":
                continue
            flags = rec.get("code_flags") or {}
            if any(str(k).startswith("floor_") and v for k, v in flags.items()):
                out.append(aid)
        return out

    def _discharge_satisfied_floors(self) -> None:
        """Auto-discard floor-flagged pending candidates whose mandatory
        hypothesis already has a valid executed test on the same inventory.

        Satisfaction = an executed pending record whose target state has
        convergence flag 'ok', the same hypothesized combo, and primitives
        matching the same floor predicate (the same experiment, not merely
        the same inventory). Idempotent; commits only when it discharged
        something. Floor-flagged records keep their queue eviction
        protection until discharged here.
        """
        from beam.enqueue import FLOOR_PREDICATES
        from beam.signature import (
            apply_primitives_to_inventory,
            combo_identity,
        )

        pending = self.g.graph.get("pending", {})
        executed: list[tuple[str | None, list[dict], float | None]] = []
        for rec in pending.values():
            if rec.get("status") != "executed":
                continue
            target = rec.get("target_state") or ""
            if target not in self.g.nodes:
                continue
            node = self.state(target)
            conv = ((node.get("metrics") or {}).get("convergence") or {}).get("flag")
            if conv != "ok":
                continue
            # the executed round's OWN parent disk Re: floor_disk_re must
            # only be satisfied by a growth tune, never a shrink
            executed.append((node.get("combo_key"), rec.get("primitives", []),
                             self._inventory_disk_re(rec.get("parent") or "")))

        discharged = False
        for aid in self.pending_queue():
            rec = pending.get(aid)
            if not rec or rec.get("status") != "pending":
                continue
            floor_flags = [str(k) for k, v in (rec.get("code_flags") or {}).items()
                           if str(k).startswith("floor_") and v]
            if not floor_flags:
                continue
            parent = self.state(rec.get("parent") or "")
            hypo, _err = apply_primitives_to_inventory(
                parent.get("inventory", []) if parent else [],
                rec.get("primitives", []))
            combo = combo_identity(hypo) if hypo is not None else None
            for flag in floor_flags:
                pred = FLOOR_PREDICATES.get(flag)
                if pred is None:
                    continue
                if any(c == combo and pred(prims, parent_re=pre)
                       for c, prims, pre in executed):
                    self.mark_discarded(
                        aid, f"FLOOR_SATISFIED: {flag} already validly tested "
                             f"on combo {combo}", bump_stagnation=False)
                    self.log_decision({"kind": "floor-discharge", "action_id": aid,
                                       "floor": flag, "combo": combo})
                    discharged = True
                    break
        if discharged:
            self.commit()

    def _inventory_disk_re(self, label: str) -> float | None:
        """Effective Re (px) of the disk/edgedisk slot in a state's fitted
        inventory, for floor_disk_re growth-vs-shrink direction checks.

        Raw parse_components inventories store the 4) row value (Rs for
        expdisk/edgedisk) with re_effective absent — convert, so a Re-units
        tune value is never compared against Rs (a 1.68x-over-lenient
        growth test would misclassify true shrinks as growth)."""
        if label not in self.g.nodes:
            return None
        disk = next((c for c in self.state(label).get("inventory", [])
                     if c.get("name") in {"disk", "edgedisk"}), None)
        if disk is None:
            return None
        re = disk.get("re_effective")
        if re is None:
            re = disk.get("re")
            if (disk.get("type") or "").lower() in ("expdisk", "edgedisk") \
                    and re is not None:
                re = float(re) * 1.68
        try:
            return float(re)
        except (TypeError, ValueError):
            return None

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
        # sub-converged rounds are budget-free (non-information); this is the
        # bound that keeps a systematically broken seeding from looping
        if int(self.counters().get("n_sub_converged", 0)) >= SUB_CONVERGED_LIMIT:
            conditions.append("sub_converged_limit")
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
