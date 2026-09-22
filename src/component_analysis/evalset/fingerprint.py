"""Stable transition fingerprints and alias merging (design doc §14).

The normalized fingerprint covers mode, canonical object id, before/after
configuration checksums, the aggregate before-result checksum, and the
canonical action. Timestamps, absolute batch paths, and run ids are excluded,
so an object re-run in a mirrored batch directory collapses onto the same
fingerprint and is recorded as an alias instead of a duplicate sample.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def aggregate_checksum(items: list[dict[str, Any]]) -> str | None:
    """Digest over an ordered set of ``(relative name, sha256)`` file records."""
    pairs = sorted((str(item.get("path", "")).rsplit("/", 1)[-1], item.get("sha256") or "") for item in items)
    if not pairs or not all(sha for _, sha in pairs):
        return None
    digest = hashlib.sha256()
    for name, sha in pairs:
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def transition_fingerprint(mode: str, object_id: str, before_config_sha: str, before_result_sha: str | None, after_config_sha: str | None, action: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    for part in (mode, object_id, before_config_sha, before_result_sha or "-", after_config_sha or "-", json.dumps(action, sort_keys=True, ensure_ascii=False)):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def merge_aliases(samples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the most evidence-complete sample per fingerprint; others become aliases.

    ``samples`` are tuples already carrying ``fingerprint`` and ``evidence_score``.
    Returns ``(canonical_samples, alias_records)``; input order is preserved for
    canonical picks so output stays deterministic.
    """
    best: dict[str, dict[str, Any]] = {}
    aliases: list[dict[str, Any]] = []
    for sample in samples:
        key = sample["fingerprint"]
        current = best.get(key)
        if current is None or sample["evidence_score"] > current["evidence_score"]:
            if current is not None:
                aliases.append({"sample_id": current["sample_id"], "alias_of": sample["sample_id"], "fingerprint": key})
            best[key] = sample
        else:
            aliases.append({"sample_id": sample["sample_id"], "alias_of": current["sample_id"], "fingerprint": key})
    return list(best.values()), aliases
