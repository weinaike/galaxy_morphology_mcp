"""B0 legacy-baseline runner: replay `component_analysis` on frozen benchmark states.

Per sample: copy the state round's comparison PNG + summary (and lyric for
multi-band) into the baseline run directory — the tool writes its analysis md
next to the image, so inputs are copied to keep the read-only archives
untouched — then call the same functions the MCP tools expose, parse the
decision markdown with :mod:`b0_parser`, and checkpoint every result for
resume. Inputs are sha256-verified against the frozen state manifest before
the call; no label-side field ever reaches the tool.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .b0_parser import parse_decision_to_action
from .config import json_dump, sha256_file

_MAX_RETRIES = 2


def _manifest(run_dir: Path, sample: dict[str, Any]) -> dict[str, Any]:
    return json.loads((run_dir / sample["state_manifest"]).read_text(encoding="utf-8"))


def _find_ref(manifest: dict[str, Any], *, role: str | None = None, suffix: str | None = None, name_contains: str | None = None, parent_of: str | None = None) -> dict[str, Any] | None:
    for ref in manifest["source_refs"]:
        path = Path(ref["path"])
        if role is not None and ref.get("role") != role:
            continue
        if suffix is not None and path.suffix.lower() != suffix:
            continue
        if name_contains is not None and name_contains not in path.name:
            continue
        return ref
    if parent_of is not None:
        base = Path(parent_of).parent
        for ref in manifest["source_refs"]:
            path = Path(ref["path"])
            if path.parent == base and (suffix is None or path.suffix.lower() == suffix):
                return ref
    return None


def _inputs_for(sample: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Path]:
    """Locate the state-round tool inputs.

    The gssummary is not a whitelisted manifest role, so for multi-band it is
    found as a sibling of the state round's config and sha-verified against the
    frozen chain's source inventory instead of the manifest refs.
    """
    state_dir = Path(sample["evidence_refs"][0]).parent
    if sample["mode"] == "single_band":
        image = _find_ref(manifest, role="comparison", suffix=".png")
        summary = _find_ref(manifest, suffix=".md", name_contains="summary")
        if image is None:
            image = _find_ref(manifest, suffix=".png", parent_of=sample["evidence_refs"][0])
        return {"image_file": Path(image["path"]) if image else state_dir / "missing.png", "summary_file": Path(summary["path"]) if summary else state_dir / "missing_summary.md"}
    comparison = _find_ref(manifest, name_contains="comparison", suffix=".png")
    lyric = _find_ref(manifest, role="configuration")
    if comparison is None:
        comparison = _find_ref(manifest, suffix=".png", parent_of=sample["evidence_refs"][0])
    gssummaries = sorted(state_dir.glob("*.gssummary"))
    return {
        "comparison_file": Path(comparison["path"]) if comparison else state_dir / "missing.png",
        "summary_file": gssummaries[0] if gssummaries else state_dir / "missing.gssummary",
        "lyric_file": Path(lyric["path"]) if lyric else state_dir / "missing.lyric",
    }


def _inventory_sha_map(inventory_path: Path) -> dict[str, str]:
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for dataset in inventory["datasets"]:
        for entry in dataset["objects"]:
            for record in entry["files"]:
                if record.get("sha256"):
                    mapping[record["path"]] = record["sha256"]
    return mapping


def _verify_sha(path: Path, refs: list[dict[str, Any]]) -> bool:
    for ref in refs:
        if ref["path"] == str(path):
            digest, status = sha256_file(path)
            return status == "hashed" and digest == ref.get("sha256")
    return False


def run_b0(frozen_run: Path, output_dir: Path, *, limit: int | None = None, only: set[str] | None = None, inventory_path: Path | None = None) -> dict[str, Any]:
    frozen_run = Path(frozen_run)
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory_shas = _inventory_sha_map(inventory_path) if inventory_path else {}
    benchmark = [json.loads(line) for line in (frozen_run / "evaluation-set-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    checkpoint = output_dir / "b0-checkpoint.jsonl"
    done: set[str] = set()
    if checkpoint.exists():
        done = {json.loads(line)["sample_id"] for line in checkpoint.read_text(encoding="utf-8").splitlines() if line.strip()}

    from tools.residual_analysis import analyze_multiband_components, component_analysis

    pending = [s for s in benchmark if s["sample_id"] not in done and (only is None or s["sample_id"] in only)][:limit]
    with checkpoint.open("a", encoding="utf-8") as sink:
        for sample in pending:
            record: dict[str, Any] = {"sample_id": sample["sample_id"], "mode": sample["mode"]}
            started = time.time()
            try:
                manifest = _manifest(frozen_run, sample)
                inputs = _inputs_for(sample, manifest)
                missing = [str(p) for p in inputs.values() if not p.exists()]
                if missing:
                    record.update({"status": "INPUT_MISSING", "detail": missing})
                else:
                    refs = manifest["source_refs"]

                    def _sha_ok(p: Path) -> bool:
                        if _verify_sha(p, refs):
                            return True
                        expected = inventory_shas.get(str(p))
                        if expected is None:
                            return False
                        digest, status = sha256_file(p)
                        return status == "hashed" and digest == expected

                    bad_sha = [str(p) for p in inputs.values() if not _sha_ok(p)]
                    if bad_sha:
                        record.update({"status": "INPUT_SHA_MISMATCH", "detail": bad_sha})
                    else:
                        staging = output_dir / "inputs" / sample["sample_id"]
                        staging.mkdir(parents=True, exist_ok=True)
                        staged = {name: staging / path.name for name, path in inputs.items()}
                        for (name, path), dest in zip(inputs.items(), staged.values()):
                            shutil.copy2(path, dest)
                            staged[name] = dest
                        record["status"] = "NO_RESPONSE"
                        for attempt in range(_MAX_RETRIES + 1):
                            try:
                                if sample["mode"] == "single_band":
                                    result = component_analysis(image_file=str(staged["image_file"]), summary_file=str(staged["summary_file"]))
                                else:
                                    result = analyze_multiband_components(lyric_file=str(staged["lyric_file"]), summary_file=str(staged["summary_file"]), comparison_file=str(staged["comparison_file"]))
                            except Exception as error:  # provider/transport failures
                                record["status"] = "TOOL_ERROR"
                                record["detail"] = f"{type(error).__name__}: {error}"[:400]
                                continue
                            if result.get("status") == "success":
                                analysis = result.get("analysis", "")
                                raw_path = output_dir / "raw" / f"{sample['sample_id']}.md"
                                raw_path.parent.mkdir(parents=True, exist_ok=True)
                                raw_path.write_text(analysis, encoding="utf-8")
                                parsed = parse_decision_to_action(analysis, current_components=sample["source_components"])
                                record.update({
                                    "status": "OK",
                                    "attempts": attempt + 1,
                                    "raw": str(raw_path),
                                    "parsed": parsed.get("action"),
                                    "parsed_first_step": parsed.get("action_first_step"),
                                    "parse_status": parsed["status"],
                                    "parse_reason": parsed.get("reason"),
                                })
                                break
                            record["status"] = "TOOL_FAILURE"
                            record["detail"] = str(result.get("error", ""))[:400]
            except Exception as error:
                record.update({"status": "RUNNER_ERROR", "detail": f"{type(error).__name__}: {error}"[:400]})
            record["elapsed_sec"] = round(time.time() - started, 1)
            sink.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            sink.flush()
    return {"processed": len(pending), "checkpoint": str(checkpoint)}


def _atom_key(action: dict[str, Any] | None) -> str:
    if action is None:
        return "NONE"
    if action.get("action_type") == "COMPOUND":
        atoms = sorted(f"{a['action_type']}:{a.get('component') or a.get('replace_to')}" for a in action.get("atomic_actions", []))
        return "COMPOUND[" + "+".join(atoms) + "]"
    return f"{action.get('action_type')}:{action.get('component') or action.get('replace_to')}"


def score_b0(frozen_run: Path, output_dir: Path) -> dict[str, Any]:
    frozen_run = Path(frozen_run)
    benchmark = {json.loads(line)["sample_id"]: json.loads(line) for line in (frozen_run / "evaluation-set-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    audit = [json.loads(line) for line in (frozen_run / "evaluation-audit-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    veto: dict[tuple, set[str]] = defaultdict(set)
    for item in audit:
        if item["action_verdict"] == "EXPLORATORY":
            veto[(item["mode"], item["object_id"], tuple(sorted(item["source_components"])))].add(_atom_key(item["canonical_action"]))
    checkpoint = [json.loads(line) for line in (output_dir / "b0-checkpoint.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    stats: dict[str, Counter] = {mode: Counter() for mode in ("single_band", "multi_band", "ALL")}
    per_sample: list[dict[str, Any]] = []
    for record in checkpoint:
        sample = benchmark[record["sample_id"]]
        mode = sample["mode"]
        canonical = sample["canonical_action"]
        for bucket in (mode, "ALL"):
            c = stats[bucket]
            c["total"] += 1
            if record["status"] != "OK":
                c[f"no_response"] += 1
                continue
            if record.get("parse_status") != "OK":
                c["parse_failed"] += 1
                continue
            c["parse_ok"] += 1
            predicted = record["parsed"]
            predicted_fs = record.get("parsed_first_step") or predicted
            if predicted["action_type"] == canonical["action_type"]:
                c["type_match"] += 1
            if predicted_fs["action_type"] == canonical["action_type"]:
                c["type_match_first_step"] += 1
            if predicted == canonical:
                c["full_match"] += 1
            if canonical["action_type"] == "CONVERGED":
                c["converged_recall_denom"] += 1
                if predicted["action_type"] == "CONVERGED":
                    c["converged_recall"] += 1
            if predicted["action_type"] == "CONVERGED":
                c["converged_precision_denom"] += 1
                if canonical["action_type"] == "CONVERGED":
                    c["converged_precision"] += 1
            key = (mode, sample["object_id"], tuple(sorted(sample["source_components"])))
            if _atom_key(predicted) in veto.get(key, set()):
                c["unsafe"] += 1
            if canonical["action_type"] in {"PROPOSE_ADD", "PROPOSE_REMOVE", "PROPOSE_REPLACE", "PROMOTE_SINGLE_SERSIC_TO_DISK"}:
                c["component_denom"] += 1
                want = canonical.get("component") or canonical.get("replace_to") or "disk"
                got = predicted.get("component") or predicted.get("replace_to")
                if want == got:
                    c["component_match"] += 1
        per_sample.append({"sample_id": record["sample_id"], "mode": mode, "status": record["status"], "predicted": record.get("parsed"), "canonical": canonical, "type_match": bool(record.get("parsed") and record["parsed"].get("action_type") == canonical["action_type"])})
    json_dump(output_dir / "baseline-report.json", {"per_sample": per_sample, "stats": {k: dict(v) for k, v in stats.items()}})
    return {"stats": {k: dict(v) for k, v in stats.items()}, "report": str(output_dir / "baseline-report.json")}


def summarize_b0(output_dir: Path, config: dict[str, Any]) -> None:
    report = json.loads((output_dir / "baseline-report.json").read_text(encoding="utf-8"))
    lines = ["# B0 旧流程基线评测报告", "", f"冻结集：`{config['frozen_run']}`；被测：legacy `component_analysis` 工具（直接函数调用，与 MCP 同一实现）；custom_instructions 未传；输入经 sha256 校验后本地暂存调用。", ""]
    for mode in ("single_band", "multi_band", "ALL"):
        c = report["stats"][mode]
        parse_ok = c.get("parse_ok", 0)
        denom = c.get("total", 0)
        lines.append(f"## {mode}（{denom} 条）")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| OK 响应 | {denom - c.get('no_response', 0)}／{denom} |")
        lines.append(f"| parse_to_action_rate | {parse_ok}／{denom - c.get('no_response', 0)} |")
        if parse_ok:
            lines.append(f"| action_type_accuracy（主口径） | {c.get('type_match', 0)}／{parse_ok}（{c.get('type_match', 0) / parse_ok:.1%}） |")
            lines.append(f"| action_type_accuracy（第一步口径） | {c.get('type_match_first_step', 0)}／{parse_ok}（{c.get('type_match_first_step', 0) / parse_ok:.1%}） |")
            lines.append(f"| full_match（含成分） | {c.get('full_match', 0)}／{parse_ok}（{c.get('full_match', 0) / parse_ok:.1%}） |")
        if c.get("component_denom"):
            lines.append(f"| component_accuracy | {c.get('component_match', 0)}／{c['component_denom']}（{c['component_match'] / c['component_denom']:.1%}） |")
        if c.get("converged_recall_denom"):
            lines.append(f"| converged_recall | {c.get('converged_recall', 0)}／{c['converged_recall_denom']} |")
        if c.get("converged_precision_denom"):
            lines.append(f"| converged_precision | {c.get('converged_precision', 0)}／{c['converged_precision_denom']} |")
        if parse_ok:
            lines.append(f"| unsafe_action_rate | {c.get('unsafe', 0)}／{parse_ok}（{c.get('unsafe', 0) / parse_ok:.1%}） |")
        lines.append("")
    (output_dir / "baseline-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
