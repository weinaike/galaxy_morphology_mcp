"""Focused tests for evalset transition fingerprints and alias merging."""

from component_analysis.evalset.fingerprint import aggregate_checksum, merge_aliases, transition_fingerprint

_ACTION = {"action_type": "PROPOSE_ADD", "component": "bulge"}


def test_fingerprint_ignores_irrelevant_context() -> None:
    base = transition_fingerprint("multi_band", "104", "cfgsha", "ressha", "aftersha", _ACTION)
    same = transition_fingerprint("multi_band", "104", "cfgsha", "ressha", "aftersha", {"component": "bulge", "action_type": "PROPOSE_ADD"})
    assert base == same
    assert base != transition_fingerprint("multi_band", "104", "other", "ressha", "aftersha", _ACTION)
    assert base != transition_fingerprint("single_band", "104", "cfgsha", "ressha", "aftersha", _ACTION)


def test_aggregate_checksum_requires_all_shas() -> None:
    assert aggregate_checksum([{"path": "/a/x.fits", "sha256": "aa"}]) is not None
    assert aggregate_checksum([{"path": "/a/x.fits", "sha256": None}]) is None


def test_merge_aliases_keeps_richest_evidence() -> None:
    samples = [
        {"sample_id": "a", "fingerprint": "f1", "evidence_score": 5},
        {"sample_id": "b", "fingerprint": "f1", "evidence_score": 9},
        {"sample_id": "c", "fingerprint": "f2", "evidence_score": 1},
    ]
    canonical, aliases = merge_aliases(samples)
    assert [item["sample_id"] for item in canonical] == ["b", "c"]
    assert aliases == [{"sample_id": "a", "alias_of": "b", "fingerprint": "f1"}]
