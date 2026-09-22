"""Deterministic, read-only pilot extraction from an approved inventory."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import json_dump
from .evidence import best_turn_from_report, fit_health_from_galfit_output, fit_health_from_gssummary, match_round_id
from .fingerprint import aggregate_checksum, merge_aliases, transition_fingerprint
from .leakage import check_manifests

_EXCLUDED_STATE_NAMES = ("working_note.md", "analysis_report", "final_report", "component_analysis", "best_round")
_EMPTY_HEALTH = {"fit_converged": None, "bic": None, "reduced_chisq": None, "residual_flags": ["FIT_HEALTH_NOT_FOUND"], "parameter_flags": [], "constraint_flags": [], "data_quality_flags": []}


def _safe_relative(path: str) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"selection path must be relative and confined to the object: {path}")
    return relative


def _records_by_relative(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    root = Path(entry["path"])
    records: dict[str, dict[str, Any]] = {}
    for record in entry["files"]:
        try:
            relative = str(Path(record["path"]).relative_to(root))
        except ValueError:
            continue
        records[relative] = record
    return records


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""



def _model_blocks(path: Path, mode: str) -> list[dict[str, Any]]:
    """Parse fitted model blocks with lineage keys: component number (single band) or P-letter (multi band)."""
    blocks: list[dict[str, Any]] = []
    if mode == "single_band":
        for component in _parse_single_band_components(_read_text(path)):
            blocks.append({"key": str(len(blocks) + 1), "label": None, **component})
        return blocks
    text = _read_text(path)
    # G blocks list each galaxy's member P-blocks; blocks under a non-host
    # galaxy (Gb2) ['c']) are companion-galaxy components.
    companion_letters: set[str] = set()
    galaxy_members = {letter: re.findall(r"'([a-zA-Z])'", members) for letter, members in re.findall(r"^G([a-z])2\)\s*(\[[^\]]*\])", text, re.MULTILINE)}
    host = set(galaxy_members.get("a", []))
    for letter, members in galaxy_members.items():
        if letter != "a":
            companion_letters.update(name for name in members if name not in host)
    for match in re.finditer(r"^P([A-Za-z])1\)\s+(\S+)", text, re.MULTILINE):
        letter = match.group(1)
        label = match.group(2).lower()
        type_match = re.search(r"^P" + letter + r"2\)\s+(\S+)", text, re.MULTILINE)
        n_match = re.search(r"^P" + letter + r"6\)\s+\[([0-9.eE+-]+),[^\]]*,([01])\]", text, re.MULTILINE)
        line_index = text[:match.start()].count("\n")
        blocks.append({
            "key": letter,
            "label": label,
            "model": (type_match.group(1).lower() if type_match else "unknown"),
            "n": float(n_match.group(1)) if n_match else None,
            "n_vary": int(n_match.group(2)) if n_match else None,
            "companion": letter in companion_letters,
            "comment_semantic": _comment_semantic(text.splitlines(), line_index),
        })
    if re.search(r"^Na1\)", text, re.MULTILINE):
        blocks.append({"key": "N", "label": "agn", "model": "agn_block", "n": None, "n_vary": None, "companion": False})
    return blocks


_MULTI_LABEL_MAP = {"disk": "disk", "bulge": "bulge", "bar": "bar", "nucleus": "agn", "agn": "agn", "companion": "companion", "edge_on_disk": "edge_on_disk"}
_SINGLE_MODEL_MAP = {"expdisk": "disk", "disk": "disk", "sersic": "bulge", "psf": "agn"}
_STRUCTURE_MAP = {"disk": "disk", "bulge": "bulge", "bar": "bar", "companion": "companion", "nucleus": "agn", "agn": "agn", "psf": "agn", "fourier": "fourier_m1"}

# Lyric block-header comments carry the semantic name of the block, e.g.
# "# Sersic function — Bulge (obj0): ...", "# Component A: Bulge (Sersic, n free)",
# "# Profile C - Nucleus (obj2): ...". Ordered patterns so that trailing prose
# ("dimmer than disk") cannot override the block's own label.
_COMMENT_LABEL_PATTERNS = [
    re.compile(r"\b(Bulge|Disk|Bar|AGN|Nucleus|Companion|Lens|Fourier)\s*\(\s*obj\d+\s*\)", re.IGNORECASE),
    re.compile(r"Component\s+[A-Za-z]\s*[:\-]\s*\**\s*(Bulge|Disk|Bar|AGN|Nucleus|Companion|Lens|Fourier)", re.IGNORECASE),
    re.compile(r"Profile\s+[A-Za-z]\s*-\s*(Bulge|Disk|Bar|AGN|Nucleus|Companion|Lens|Fourier)", re.IGNORECASE),
    re.compile(r"—\s*-?\s*\**\s*(Bulge|Disk|Bar|AGN|Nucleus|Companion|Lens|Fourier)\b", re.IGNORECASE),
    re.compile(r"^\s*#\s*\**\s*(?:Compact\s+|Central\s+)?(Bulge|Disk|Bar|AGN|Nucleus|Companion|Lens|Fourier)\b", re.IGNORECASE | re.MULTILINE),
]
# 2026-09-20 ruling: a P-block Sersic nucleus is NOT an AGN; only comments that
# explicitly name AGN map to `agn`. Nucleus comments on P-block Sersic -> bulge.
_COMMENT_SEMANTIC_MAP = {"bulge": "bulge", "disk": "disk", "bar": "bar", "agn": "agn", "companion": "companion", "lens": "lens", "fourier": "fourier_m1"}


def _comment_semantic(lines: list[str], block_line_index: int) -> str | None:
    """Semantic label from the comment line(s) directly above a P<letter>1) line."""
    comments: list[str] = []
    for offset in range(1, 5):
        index = block_line_index - offset
        if index < 0:
            break
        line = lines[index].strip()
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line)
        else:
            break
    for comment in comments:
        for pattern in _COMMENT_LABEL_PATTERNS:
            match = pattern.search(comment)
            if match:
                name = match.group(1).lower()
                if name == "nucleus":
                    return "bulge"
                return _COMMENT_SEMANTIC_MAP.get(name)
    return None


_SINGLE_HEADER_RE = re.compile(r"^\s*#\s*(?:Component|Object)\s+number:\s*\d+\s*(?:--\s*([A-Za-z_]+))?", re.MULTILINE)
# Inline labels: "# Object number: 1 -- Bulge (sersic)" header or a trailing
# "(Bulge)" on the `0) sersic  #  Component type (Bulge)` line.
_PAREN_LABEL_RE = re.compile(r"^\s*0\)\s+\S+[^\n]*?\(\s*(Bulge|Disk|Bar|PSF|Nucleus|Companion|Lens|Fourier)\s*\)", re.IGNORECASE | re.MULTILINE)


def _single_band_n_and_vary(body: str) -> tuple[float | None, int | None]:
    match = re.search(r"^\s*5\)\s+([0-9.eE+-]+)(?:\s+([01]))?", body, re.MULTILINE)
    if match is None:
        return None, None
    return (float(match.group(1)), int(match.group(2))) if match.group(2) is not None else (float(match.group(1)), None)


def _parse_single_band_components(text: str) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    headers = list(_SINGLE_HEADER_RE.finditer(text))
    if headers:
        # "# Component number: N" (fitted outputs, STRUCTURE comments inside) and
        # "# Object number: N -- Label" (gadotti-style feedmes, inline label).
        for index, header in enumerate(headers):
            body = text[header.end(): headers[index + 1].start() if index + 1 < len(headers) else len(text)]
            type_match = re.search(r"^\s*0\)\s+(\S+)", body, re.MULTILINE)
            if type_match is None or type_match.group(1).lower() == "sky":
                continue
            component: dict[str, Any] = {"model": type_match.group(1).lower()}
            structure = re.search(r"#\s*STRUCTURE:\s*([A-Za-z_]+)", body, re.MULTILINE)
            paren = _PAREN_LABEL_RE.search(body)
            if structure:
                component["structure"] = structure.group(1).lower()
            elif header.group(1):
                component["structure"] = header.group(1).lower()
            elif paren:
                component["structure"] = paren.group(1).lower()
            else:
                component["structure"] = None
            position = re.search(r"^\s*1\)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", body, re.MULTILINE)
            component["pos"] = (float(position.group(1)), float(position.group(2))) if position else None
            size = re.search(r"^\s*4\)\s+([0-9.eE+-]+)", body, re.MULTILINE)
            component["size"] = float(size.group(1)) if size else None
            component["n"], component["n_vary"] = _single_band_n_and_vary(body)
            if re.search(r"^\s*F1\)", body, re.MULTILINE):
                component["fourier"] = True
            components.append(component)
        return components
    markers = list(re.finditer(r"^\s*0\)\s+(\S+)", text, re.MULTILINE))
    for index, match in enumerate(markers):
        model = match.group(1).lower()
        if model == "sky":
            continue
        body = text[match.end(): markers[index + 1].start() if index + 1 < len(markers) else len(text)]
        position = re.search(r"^\s*1\)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", body, re.MULTILINE)
        size = re.search(r"^\s*4\)\s+([0-9.eE+-]+)", body, re.MULTILINE)
        n_value, n_vary = _single_band_n_and_vary(body)
        component = {
            "model": model,
            "structure": _PAREN_LABEL_RE.search(body).group(1).lower() if _PAREN_LABEL_RE.search(body) else None,
            "pos": (float(position.group(1)), float(position.group(2))) if position else None,
            "size": float(size.group(1)) if size else None,
            "n": n_value,
            "n_vary": n_vary,
        }
        if re.search(r"^\s*F1\)", body, re.MULTILINE):
            component["fourier"] = True
        components.append(component)
    return components


def _single_band_semantics(component: dict[str, Any], single_block: bool) -> list[str]:
    if single_block and component["model"] == "sersic":
        # A lone plain Sersic is the single_sersic state by definition; the
        # STRUCTURE comment may carry a provisional label, but disk confirmation
        # only happens when the model switches to expdisk (the PROMOTE action).
        semantics = ["single_sersic"]
        if component.get("fourier"):
            semantics.append("fourier_m1")
        return semantics
    structure = component.get("structure")
    if structure is not None and structure in _STRUCTURE_MAP:
        base = _STRUCTURE_MAP[structure]
    elif component["model"] == "sersic":
        if single_block:
            base = "single_sersic"
        elif component.get("size_disk_candidate"):
            # QC ruling 2026-09-21 (option A): with several unlabeled Sersics in
            # one round, the largest-Re block with n≈1.0 (fixed or free) is the
            # Sersic-disk; size discriminates because n≈1.0 alone does not.
            base = "disk"
        elif component.get("n") is not None and abs(component["n"] - 0.5) < 0.01:
            base = "bar"
        elif component.get("n") is not None and abs(component["n"] - 1.0) < 0.01 and component.get("n_vary") == 0 and not component.get("size_mode"):
            # QC ruling 2026-09-18: a lone unlabeled Sersic with n fixed at 1.0
            # is a Sersic-disk; inside size mode only the largest block qualifies.
            base = "disk"
        else:
            base = "bulge"
    else:
        base = _SINGLE_MODEL_MAP.get(component["model"], component["model"])
    return [base] + (["fourier_m1"] if component.get("fourier") else [])


def _rekey_after_by_lineage(before_blocks: list[dict[str, Any]], after_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reassign after-block keys by greedy position/type/size matching.

    Component numbers are not stable across single-band rounds (removals renumber
    later components), so lineage follows physical continuity instead of index.
    """
    import math

    scored: list[tuple[float, int, int]] = []
    for bi, before in enumerate(before_blocks):
        for ai, after in enumerate(after_blocks):
            score = 0.0
            if before.get("pos") and after.get("pos"):
                score += math.hypot(before["pos"][0] - after["pos"][0], before["pos"][1] - after["pos"][1])
            else:
                score += 50.0
            if before["model"] != after["model"]:
                score += 1.0
            if before.get("size") and after.get("size"):
                score += 3.0 * abs(math.log(after["size"] / before["size"]))
            if before.get("n") is not None and after.get("n") is not None and before["model"] == after["model"] == "sersic":
                score += abs(before["n"] - after["n"])
            scored.append((score, bi, ai))
    scored.sort()
    before_taken: set[int] = set()
    after_key: dict[int, str] = {}
    for score, bi, ai in scored:
        if bi in before_taken or ai in after_key:
            continue
        before_taken.add(bi)
        after_key[ai] = before_blocks[bi]["key"]
    rekeyed = []
    for ai, after in enumerate(after_blocks):
        block = dict(after)
        block["key"] = after_key.get(ai, f"new{ai}")
        rekeyed.append(block)
    return rekeyed


def _block_semantics(block: dict[str, Any], mode: str, single_block: bool, anchors: dict[str, str] | None = None) -> list[str]:
    if mode == "multi_band":
        label, model = block["label"], block["model"]
        if model == "agn_block":
            return ["agn"]
        if block.get("companion"):
            return ["companion"]
        if single_block and label in {"obj0", "unknown"} and model == "sersic":
            return ["unclassified_sersic"]
        if model in {"sersic_f", "expdisk_f"}:
            return ["disk", "fourier_m1"]
        if label in _MULTI_LABEL_MAP:
            return [_MULTI_LABEL_MAP[label]]
        if re.fullmatch(r"obj\d+|host|unknown", label):
            # Generic labels: the block's own header comment is the primary
            # source, then the round's decision-file objN mapping, instance-wide
            # label anchors, or fixed-n heuristics (QC rulings 2026-09-18/20);
            # otherwise the block is semantically unidentifiable.
            if block.get("comment_semantic"):
                return [block["comment_semantic"]]
            anchors = anchors or {}
            if label in anchors:
                return [anchors[label]]
            if model == "sersic" and block.get("n") is not None:
                if abs(block["n"] - 0.5) < 0.01:
                    return ["bar"]
                if abs(block["n"] - 1.0) < 0.01 and block.get("n_vary") == 0:
                    return ["disk"]
            return ["unidentified_sersic"]
        if model == "sersic":
            # 2026-09-20 ruling: no silent bulge fallback for odd non-semantic,
            # non-generic labels; such blocks are semantically unidentifiable.
            return ["unidentified_sersic"]
        if model in {"disk", "expdisk"}:
            return ["disk"]
        return [model]
    return _single_band_semantics(block, single_block)


def _mark_size_disk_candidate(blocks: list[dict[str, Any]]) -> None:
    """Option-A size criterion: among a round's unlabeled central Sersics, the
    largest-Re block with n≈1.0 (fixed or free) is the disk (2026-09-21 ruling).
    Blocks far from the galaxy's component centroid are excluded — an off-center
    source winning on Re is a companion, not the disk."""
    unlabeled = [block for block in blocks if block.get("structure") is None and block["model"] == "sersic" and block.get("size") is not None]
    if len(unlabeled) < 2:
        return
    positions = [block["pos"] for block in blocks if block.get("pos")]
    if positions:
        cx = sorted(x for x, _ in positions)[len(positions) // 2]
        cy = sorted(y for _, y in positions)[len(positions) // 2]
        central = [block for block in unlabeled if abs(block["pos"][0] - cx) <= 10 and abs(block["pos"][1] - cy) <= 10]
    else:
        central = unlabeled
    if not central:
        return
    for block in unlabeled:
        block["size_mode"] = True
    largest = max(central, key=lambda block: block["size"])
    if largest.get("n") is not None and abs(largest["n"] - 1.0) < 0.01:
        largest["size_disk_candidate"] = True


def _round_blocks(path: Path, mode: str, anchors: dict[str, str] | None = None) -> list[dict[str, Any]]:
    blocks = _model_blocks(path, mode)
    if mode == "single_band":
        _mark_size_disk_candidate(blocks)
    for block in blocks:
        block["semantics"] = _block_semantics(block, mode, single_block=len(blocks) == 1, anchors=anchors)
    return blocks


def component_signature(path: Path, mode: str, anchors: dict[str, str] | None = None) -> list[str]:
    blocks = _round_blocks(path, mode, anchors=anchors)
    components = [semantic for block in blocks for semantic in block["semantics"]]
    return sorted(set(components))


_MULTI_OBJ_SEMANTIC_RE = re.compile(r"obj(\d+)\s*[（(]\s*\**\s*(Disk|Bulge|Bar|Companion|AGN|Nucleus|PSF|致密核|核球|伴星系)", re.IGNORECASE)
_MULTI_OBJ_SEMANTIC_MAP = {"disk": "disk", "bulge": "bulge", "bar": "bar", "companion": "companion", "agn": "agn", "nucleus": "agn", "psf": "agn", "致密核": "agn", "核球": "bulge", "伴星系": "companion"}


def _own_decision_anchors(round_dir: Path) -> dict[str, str]:
    """objN -> semantic mapping from the round's own multi-band decision file."""
    anchors: dict[str, str] = {}
    for md in sorted(round_dir.glob("all_bands_comparison_component_analysis_*.md")):
        for number, name in _MULTI_OBJ_SEMANTIC_RE.findall(md.read_text(encoding="utf-8", errors="replace")):
            anchors[f"obj{number}"] = _MULTI_OBJ_SEMANTIC_MAP[name.lower()]
    return anchors


def _instance_letter_anchors(round_paths: list[Path], mode: str) -> dict[str, str]:
    """Letter -> semantic anchors from semantic-labeled rounds (P letters are stable within an instance)."""
    if mode != "multi_band":
        return {}
    letter_semantics: dict[str, str] = {}
    for path in round_paths:
        for block in _model_blocks(path, mode):
            label = block["label"]
            if block.get("companion"):
                letter_semantics.setdefault(block["key"], "companion")
            elif label in _MULTI_LABEL_MAP and not re.fullmatch(r"obj\d+|host|unknown", label):
                letter_semantics.setdefault(block["key"], _MULTI_LABEL_MAP[label])
    return letter_semantics


def _add(component: str) -> dict[str, Any]:
    return {"action_type": "PROPOSE_ADD", "component": component, "replace_from": None, "replace_to": None}


def _atomic_actions(before_blocks: list[dict[str, Any]], after_blocks: list[dict[str, Any]], single_band: bool = False) -> tuple[list[dict[str, Any]], bool]:
    """Decompose a transition into atomic actions by parameter-block lineage (design doc §10 Phase C, component-evalset-v2).

    ``single_band`` enables the single-band promote rule: any in-place
    ``single_sersic -> disk(expdisk)`` conversion counts as promotion
    (2026-09-15 ruling, decoupled from expert labels on 2026-09-16 — action
    typing never depends on the label; correctness is adjudicated separately).
    """
    actions: list[dict[str, Any]] = []
    unresolvable = False
    after_by_key = {block["key"]: block for block in after_blocks}
    for block in before_blocks:
        after = after_by_key.get(block["key"])
        if after is None:
            actions.extend(_add_removed(semantic) for semantic in block["semantics"])
            continue
        before_set, after_set = set(block["semantics"]), set(after["semantics"])
        added, removed = sorted(after_set - before_set), sorted(before_set - after_set)
        if not added and not removed:
            continue
        promotable_source = "unclassified_sersic" in before_set or (
            single_band and before_set == {"single_sersic"} and "disk" in after_set
        )
        if promotable_source and "disk" in after_set:
            actions.append({"action_type": "PROMOTE_SINGLE_SERSIC_TO_DISK", "component": "disk", "replace_from": None, "replace_to": None})
            actions.extend(_add(semantic) for semantic in added if semantic != "disk")
        elif "unclassified_sersic" in before_set or "unidentified_sersic" in before_set:
            unresolvable = True
        elif added and not removed:
            actions.extend(_add(semantic) for semantic in added)
        elif len(removed) == 1 and len(added) == 1:
            actions.append({"action_type": "PROPOSE_REPLACE", "component": None, "replace_from": removed[0], "replace_to": added[0]})
        else:
            unresolvable = True
    before_keys = {block["key"] for block in before_blocks}
    for block in after_blocks:
        if block["key"] not in before_keys:
            actions.extend(_add(semantic) for semantic in block["semantics"])
    return actions, unresolvable


def _add_removed(component: str) -> dict[str, Any]:
    return {"action_type": "PROPOSE_REMOVE", "component": component, "replace_from": None, "replace_to": None}


def _infer_action(before_blocks: list[dict[str, Any]], after_blocks: list[dict[str, Any]], single_band: bool = False) -> dict[str, Any]:
    if single_band:
        after_blocks = _rekey_after_by_lineage(before_blocks, after_blocks)
    actions, unresolvable = _atomic_actions(before_blocks, after_blocks, single_band)
    if any(action.get("component") == "unidentified_sersic" or action.get("replace_from") == "unidentified_sersic" or action.get("replace_to") == "unidentified_sersic" for action in actions):
        unresolvable = True
    if unresolvable:
        return {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": actions, "compound_reason": "UNMAPPABLE_ATOMIC_CHANGE"}
    if not actions:
        return {"action_type": "REFIT_PARAMETERS", "component": None, "replace_from": None, "replace_to": None}
    if len(actions) == 1:
        return actions[0]
    return {"action_type": "COMPOUND", "component": None, "replace_from": None, "replace_to": None, "atomic_actions": actions, "compound_reason": "NO_INTERMEDIATE_ROUND"}


def _state_refs(records: dict[str, dict[str, Any]], primary: dict[str, Any]) -> list[dict[str, Any]]:
    parent = Path(primary["path"]).parent
    refs = []
    for record in records.values():
        path = Path(record["path"])
        name = path.name.lower()
        if path.parent != parent or record.get("scope") != "image":
            continue
        if any(marker in name for marker in _EXCLUDED_STATE_NAMES) or name.endswith("_for_image_sed_fitting.lyric"):
            continue
        if record["role"] in {"configuration", "fit_parameter_round", "binary_fit", "comparison"} or name.endswith((".cons", ".log", ".md")):
            refs.append({key: record[key] for key in ("path", "role", "size_bytes", "sha256")})
    return sorted(refs, key=lambda item: item["path"])


def _round_info(entry: dict[str, Any], relative_path: str, records: dict[str, dict[str, Any]], letter_anchors: dict[str, str] | None = None) -> dict[str, Any]:
    relative = _safe_relative(relative_path)
    record = records.get(str(relative))
    if record is None:
        raise ValueError(f"selected round is absent from inventory: {entry['object_id']}:{relative_path}")
    if record.get("scope") != "image" or record.get("role") not in {"configuration", "fit_parameter_round"}:
        raise ValueError(f"selected round is not an Image configuration: {entry['object_id']}:{relative_path}")
    path = Path(record["path"])
    mode = "multi_band" if entry["label"]["dataset"] == "jwst_multiband" else "single_band"
    round_dir = path.parent
    structure_path = path
    anchors: dict[str, str] | None = None
    if mode == "single_band":
        feedmes = sorted(
            item["path"]
            for item in records.values()
            if item.get("scope") == "image" and Path(item["path"]).parent == round_dir and Path(item["path"]).name.endswith(".feedme")
        )
        if feedmes:
            structure_path = Path(feedmes[0])
    else:
        anchors = _own_decision_anchors(round_dir)
        if letter_anchors:
            for block in _model_blocks(structure_path, mode):
                semantic = letter_anchors.get(block["key"])
                if semantic and re.fullmatch(r"obj\d+|host|unknown", block["label"]):
                    anchors.setdefault(block["label"], semantic)
    if mode == "multi_band":
        summaries = sorted(round_dir.glob("*.gssummary"))
        health = fit_health_from_gssummary(summaries[0]) if summaries else None
    else:
        health = fit_health_from_galfit_output(round_dir)
    result_records = [item for item in records.values() if item.get("role") == "binary_fit" and item.get("scope") == "image" and Path(item["path"]).parent == round_dir]
    return {
        "round_id": str(relative),
        "primary": record,
        "components": component_signature(structure_path, mode, anchors=anchors),
        "blocks": _round_blocks(structure_path, mode, anchors=anchors),
        "health": health if health is not None else dict(_EMPTY_HEALTH),
        "config_sha": record.get("sha256"),
        "result_sha": aggregate_checksum(result_records),
        "state_refs": _state_refs(records, record),
    }


def _candidate(entry: dict[str, Any], before: dict[str, Any], after: dict[str, Any], index: int, best_turn: str | None, sample_prefix: str = "pilot") -> dict[str, Any]:
    sample_id = f"{sample_prefix}-{entry['mode']}-{entry['object_id']}-{entry['batch']}-{index:02d}"
    single_band = entry["mode"] == "single_band"
    return {
        "schema_version": "evaluation-decision-candidate@v1",
        "sample_id": sample_id,
        "dataset_id": f"{entry['mode']}:{entry['batch']}",
        "object_id": entry["object_id"],
        "mode": entry["mode"],
        "state_round_id": before["round_id"],
        "post_action_round_id": after["round_id"],
        "source_components": before["components"],
        "expert_final_components": entry["label"]["expert_final_components"],
        "current_fit_health": before["health"],
        "historical_action_raw": "Adjacent historical configurations selected; manual action interpretation required.",
        "canonical_action": _infer_action(before["blocks"], after["blocks"], single_band=single_band),
        "evidence_refs": [before["primary"]["path"], after["primary"]["path"]],
        "historical_best_round_id": best_turn,
        "terminal_consistency": "unresolved",
        "review_status": "pending",
        "evaluation_pool": "audit",
    }


def _converged_candidate(entry: dict[str, Any], best_round: dict[str, Any], best_turn: str, sample_prefix: str = "pilot") -> dict[str, Any]:
    expert = set(entry["label"]["expert_final_components"])
    consistency = "match" if set(best_round["components"]) == expert else "mismatch"
    return {
        "schema_version": "evaluation-decision-candidate@v1",
        "sample_id": f"{sample_prefix}-{entry['mode']}-{entry['object_id']}-{entry['batch']}-cv",
        "dataset_id": f"{entry['mode']}:{entry['batch']}",
        "object_id": entry["object_id"],
        "mode": entry["mode"],
        "state_round_id": best_round["round_id"],
        "post_action_round_id": None,
        "source_components": best_round["components"],
        "expert_final_components": entry["label"]["expert_final_components"],
        "current_fit_health": best_round["health"],
        "historical_action_raw": f"Analysis report designates best turn {best_turn}.",
        "canonical_action": {"action_type": "CONVERGED", "component": None, "replace_from": None, "replace_to": None},
        "evidence_refs": [best_round["primary"]["path"]],
        "historical_best_round_id": best_turn,
        "terminal_consistency": consistency,
        "review_status": "pending",
        "evaluation_pool": "audit",
    }


def _prelabel(candidate: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    codes: list[str] = []
    facts: dict[str, Any] = {}
    for metric, improved_code, worsened_code in (("reduced_chisq", "CHISQ_IMPROVED", "CHISQ_WORSENED"), ("bic", "BIC_IMPROVED", "BIC_WORSENED")):
        pre_value, post_value = before["health"].get(metric), after["health"].get(metric)
        if pre_value is not None and post_value is not None:
            delta = post_value - pre_value
            facts[metric] = {"before": pre_value, "after": post_value, "delta": delta}
            codes.append(improved_code if delta < 0 else worsened_code)
        else:
            codes.append(f"{metric.upper()}_NOT_AVAILABLE")
    if candidate["canonical_action"]["action_type"] == "COMPOUND":
        codes.append("COMPOUND_" + candidate["canonical_action"].get("compound_reason", ""))
    facts["components"] = {"source": before["components"], "post": after["components"]}
    return {
        "schema_version": "evaluation-action-prelabel@v1",
        "sample_id": candidate["sample_id"],
        "canonical_action": candidate["canonical_action"],
        "pre_action_necessity": "unknown",
        "reason_codes": codes,
        "evidence_refs": candidate["evidence_refs"],
        "prelabel_status": "PRELABEL_ONLY",
        "evidence_facts": facts,
    }


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def is_benchmark_eligible_compound(action: dict[str, Any]) -> bool:
    """Two-atom compounds that count as reasonable single-round combinations (design doc §pools, 2026-09-16 ruling)."""
    if action.get("action_type") != "COMPOUND":
        return False
    atoms = action.get("atomic_actions", [])
    if len(atoms) != 2:
        return False
    first, second = atoms
    if second.get("action_type") != "PROPOSE_ADD" or not second.get("component"):
        return False
    if first.get("action_type") == "PROMOTE_SINGLE_SERSIC_TO_DISK":
        return True
    return first.get("action_type") == "PROPOSE_REPLACE" and first.get("replace_from") == "single_sersic" and bool(first.get("replace_to"))


def _action_display(action: dict[str, Any]) -> str:
    if action["action_type"] != "COMPOUND":
        return action["action_type"]
    parts = []
    for atomic in action.get("atomic_actions", []):
        if atomic["action_type"] == "PROPOSE_ADD":
            parts.append(f"PROPOSE_ADD {atomic['component']}")
        elif atomic["action_type"] == "PROPOSE_REMOVE":
            parts.append(f"PROPOSE_REMOVE {atomic['component']}")
        elif atomic["action_type"] == "PROPOSE_REPLACE":
            parts.append(f"PROPOSE_REPLACE {atomic['replace_from']}->{atomic['replace_to']}")
        else:
            parts.append(atomic["action_type"])
    return "COMPOUND[" + " + ".join(parts) + "]"


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def round_input_ok(structure_path: Path, mode: str) -> tuple[bool, str | None]:
    """Input-completeness gate (2026-09-20 QC ruling).

    Multi-band: the round directory must carry at least one fit product
    (result FITS or gssummary) and every science image referenced by the lyric
    must exist. Single-band: the input image referenced by the fitted output /
    feedme ``A)`` line must exist (resolved against the round dir or the object
    root). Rounds failing this gate never happened as usable fits and do not
    participate in transitions or CONVERGED candidates.
    """
    if mode == "multi_band":
        files = [item.name for item in structure_path.parent.iterdir() if item.is_file()]
        if not any(name.endswith(".fits") or name.endswith(".gssummary") for name in files):
            return False, "NO_FIT_PRODUCT"
        text = _read_text(structure_path)
        refs = [value.strip().strip("'\"") for value in re.findall(r"^I[a-g]1\)\s*\[([^\],]+)", text, re.MULTILINE)]
        refs = [value for value in refs if value.lower() != "none"]
        for ref in refs:
            if not _path_exists(Path(ref)):
                return False, "SCIENCE_INPUT_MISSING"
        return True, None
    text = _read_text(structure_path)
    match = re.search(r"^\s*A\)\s+(\S+)", text, re.MULTILINE)
    if match is None:
        return False, "INPUT_IMAGE_REF_NOT_FOUND"
    image = Path(match.group(1))
    if image.is_absolute():
        candidates = [image]
    else:
        # Relative refs resolve against the round dir, the archives dir, or the
        # object root (archives/<ts>/galfit.NN sits two levels below the root).
        parent = structure_path.parent
        candidates = [parent / image, parent.parent / image, parent.parent.parent / image]
    if any(_path_exists(candidate) for candidate in candidates):
        return True, None
    return False, "INPUT_IMAGE_MISSING"


def build_pilot(
    selection: dict[str, Any],
    inventory: dict[str, Any],
    output_dir: Path,
    config: dict[str, Any],
    *,
    run_kind: str = "pilot",
    min_rounds: int = 2,
    require_inputs: bool = False,
) -> dict[str, Any]:
    if selection["inventory_run_id"] != inventory["run_id"]:
        raise ValueError("pilot selection does not reference the supplied inventory run")
    if selection["design_sha256"] != inventory["design_sha256"] or selection["adjudication_rule_version"] != inventory["adjudication_rule_version"]:
        raise ValueError("pilot selection provenance does not match inventory")

    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for dataset in inventory["datasets"]:
        for entry in dataset["objects"]:
            if not entry["eligible"]:
                continue
            key = (dataset["mode"], entry["object_id"], entry["batch"])
            if key in by_key:
                # Same-batch `_2` variant: register under an instance-disambiguated
                # batch key so both directory trees can be extracted; identical
                # copies still collapse via transition fingerprint (design §2.2).
                by_key[(dataset["mode"], entry["object_id"], f"{entry['batch']}:{entry['original_object_name']}")] = entry
            else:
                by_key[key] = entry
    candidates: list[dict[str, Any]] = []
    prelabels: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    packets: list[dict[str, Any]] = []
    fingerprint_samples: list[dict[str, Any]] = []
    selection_rows = []
    best_turn_notes: list[dict[str, Any]] = []
    for selected in selection["objects"]:
        key = (selected["mode"], selected["object_id"], selected["batch"])
        entry = by_key.get(key)
        if entry is None:
            raise ValueError(f"pilot object is not eligible in inventory: {key}")
        entry = {**entry, "mode": selected["mode"], "batch": selected["batch"]}
        records = _records_by_relative(entry)
        letter_anchors = _instance_letter_anchors([Path(records[str(_safe_relative(path))]["path"]) for path in selected["rounds"]], selected["mode"]) if selected["mode"] == "multi_band" else {}
        rounds = [_round_info(entry, path, records, letter_anchors=letter_anchors) for path in selected["rounds"]]
        if len(rounds) < min_rounds:
            raise ValueError(f"selection lists too few rounds for {key}: {len(rounds)} < {min_rounds}")
        if require_inputs:
            for round_ in rounds:
                ok, reason = round_input_ok(Path(round_["primary"]["path"]), entry["mode"])
                round_["inputs_ok"], round_["inputs_skip_reason"] = ok, reason
        round_dirs = [Path(round_["round_id"]).parent for round_ in rounds]
        if len(set(round_dirs)) != len(round_dirs):
            raise ValueError(f"pilot selection lists two configurations from the same round directory (feedme and fitted output must not both count as rounds): {key}")
        best_payload = best_turn_from_report(Path(entry["path"]))
        best_turn = str(best_payload["best_turn"]) if best_payload else None
        best_round_id = match_round_id([round_["round_id"] for round_ in rounds], best_turn) if best_turn else None
        best_round = next((round_ for round_ in rounds if round_["round_id"] == best_round_id), None)
        selection_rows.append({**selected, "expert_final_components": entry["label"]["expert_final_components"]})
        best_turn_notes.append({"object_id": entry["object_id"], "batch": entry["batch"], "best_turn": best_turn, "matched_round": best_round_id})
        for index, (before, after) in enumerate(zip(rounds, rounds[1:]), start=1):
            if require_inputs and not (before.get("inputs_ok", True) and after.get("inputs_ok", True)):
                continue
            candidate = _candidate(entry, before, after, index, best_turn, sample_prefix=run_kind)
            candidates.append(candidate)
            prelabels.append(_prelabel(candidate, before, after))
            manifest = {"schema_version": "evaluation-state-manifest@v1", "sample_id": candidate["sample_id"], "mode": entry["mode"], "object_id": entry["object_id"], "state_round_id": before["round_id"], "input_cutoff": before["round_id"], "source_refs": before["state_refs"], "history_context": [{"round_id": prior["round_id"], "action": "historical_context_only"} for prior in rounds[:index]]}
            manifests.append(manifest)
            packets.append({"schema_version": "evaluation-adjudication-packet@v1", "sample_id": candidate["sample_id"], "candidate": candidate, "state_manifest": f"evaluation-state-manifests/{candidate['sample_id']}.json", "expert_label": entry["label"], "manual_review_questions": ["动作前状态是否只包含当前轮及之前的输入？", "前后配置差异是否能唯一映射为一个 canonical action？", "该动作是否有动作前必要性和动作后有效性的直接证据？", "该记录应进入 benchmark、audit 还是 excluded？"]})
            fingerprint_samples.append({
                "sample_id": candidate["sample_id"],
                "fingerprint": transition_fingerprint(entry["mode"], entry["object_id"], before["config_sha"] or "", before["result_sha"], after["config_sha"], candidate["canonical_action"]),
                "evidence_score": len(before["state_refs"]) + (2 if before["health"]["reduced_chisq"] is not None else 0) + (2 if after["health"]["reduced_chisq"] is not None else 0),
            })
        if best_round is not None and (not require_inputs or best_round.get("inputs_ok", True)):
            converged = _converged_candidate(entry, best_round, best_turn, sample_prefix=run_kind)
            candidates.append(converged)
            manifests.append({"schema_version": "evaluation-state-manifest@v1", "sample_id": converged["sample_id"], "mode": entry["mode"], "object_id": entry["object_id"], "state_round_id": best_round["round_id"], "input_cutoff": best_round["round_id"], "source_refs": best_round["state_refs"], "history_context": [{"round_id": prior["round_id"], "action": "historical_context_only"} for prior in rounds[:rounds.index(best_round)]]})
            packets.append({"schema_version": "evaluation-adjudication-packet@v1", "sample_id": converged["sample_id"], "candidate": converged, "state_manifest": f"evaluation-state-manifests/{converged['sample_id']}.json", "expert_label": entry["label"], "manual_review_questions": ["动作前状态是否只包含当前轮及之前的输入？", "前后配置差异是否能唯一映射为一个 canonical action？", "该动作是否有动作前必要性和动作后有效性的直接证据？", "该记录应进入 benchmark、audit 还是 excluded？"]})

    canonical_samples, aliases = merge_aliases(fingerprint_samples)
    alias_ids = {item["sample_id"] for item in aliases}
    leakage = check_manifests(manifests)
    for manifest in manifests:
        json_dump(output_dir / "evaluation-state-manifests" / f"{manifest['sample_id']}.json", manifest)
    for packet in packets:
        json_dump(output_dir / "adjudication-packets" / f"{packet['sample_id']}.json", packet)
    _write_jsonl(output_dir / "evaluation-decision-candidates.jsonl", candidates)
    _write_jsonl(output_dir / "evaluation-action-prelabels.jsonl", prelabels)
    json_dump(output_dir / "evaluation-aliases.json", {"schema_version": "evaluation-aliases@v1", "aliases": aliases, "canonical_candidate_count": len(canonical_samples)})
    json_dump(output_dir / "leakage-report.json", leakage)
    json_dump(output_dir / f"{run_kind}-selection.json", {**selection, "objects": selection_rows})
    kind_label = "Pilot" if run_kind == "pilot" else "Full"
    gate_status = "PILOT_REVIEW_REQUIRED" if run_kind == "pilot" else "FULL_REVIEW_REQUIRED"
    review_lines = [f"# {kind_label} 人工校验审阅包", "", f"状态：{gate_status}。以下记录是待人工核验的候选，不是最终评测集，也没有自动正确标签。", "", f"对象×批次实例：{len(selection_rows)}；候选转换数：{sum(1 for c in candidates if c['canonical_action']['action_type'] != 'CONVERGED')}；CONVERGED 候选：{sum(1 for c in candidates if c['canonical_action']['action_type'] == 'CONVERGED')}；去重别名：{len(aliases)}。", "", "## 选择对象与最佳轮", "", "| mode | object_id | batch | expert_final_components | rounds | best_turn | matched | consistency | coverage_tags |", "|---|---|---|---|---|---|---|---|---|"]
    for row, note in zip(selection_rows, best_turn_notes):
        converged = next((c for c in candidates if c["sample_id"].endswith("-cv") and c["object_id"] == row["object_id"] and c["dataset_id"].endswith(row["batch"])), None)
        consistency = converged["terminal_consistency"] if converged else "-"
        review_lines.append(f"| {row['mode']} | {row['object_id']} | {row['batch']} | {', '.join(row['expert_final_components'])} | {len(row['rounds'])} | {note['best_turn'] or '-'} | {note['matched_round'] or '-'} | {consistency} | {', '.join(row['coverage_tags'])} |")
    review_lines.extend(["", "## 待核验转换", "", "| sample_id | action | chi2/nu before→after | alias |", "|---|---|---|---|"])
    for candidate, prelabel in zip((c for c in candidates if c["canonical_action"]["action_type"] != "CONVERGED"), prelabels):
        facts = prelabel.get("evidence_facts", {}).get("reduced_chisq")
        chi = f"{facts['before']:.3f}→{facts['after']:.3f}" if facts else "-"
        review_lines.append(f"| {candidate['sample_id']} | `{_action_display(candidate['canonical_action'])}` | {chi} | {'yes' if candidate['sample_id'] in alias_ids else ''} |")
    review_lines.extend(["", "## 边界", "", "- state manifest 只引用当前轮及其同轮产物，Working Note、analysis_report、未来轮次和 SED/Image-SED 文件不进入被测输入；泄漏六项检查见 leakage-report.json。", "- `fit_converged` 在两类数据源中均无显式标志，保持 null，不得以文件存在代替收敛。", "- prelabel 只含机器可核验事实（chi-square／BIC 变化），`pre_action_necessity=unknown`，不得据此签发 `CORRECT + high`。", "- 复合动作（`action_type=COMPOUND`）按参数块血缘分解为 `atomic_actions`；无中间轮的复合候选排除核心集（`component-evalset-v2`）。", "- 专家终态为 `edge_on_disk` 的对象不进入 benchmark 核心集（2026-09-15 裁定）。", "- 人工审阅完成前，所有候选保持 `review_status=pending`、`evaluation_pool=audit`。"])
    (output_dir / f"{run_kind}-review.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    return {"selection_rows": selection_rows, "candidates": candidates, "manifests": manifests, "leakage": leakage, "aliases": aliases}
