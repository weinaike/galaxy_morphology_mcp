from __future__ import annotations

import pytest

from src.component_analysis.shadow import _components_from_lyric
from src.tools.run_component_shadow_devset import _coverage_details, _write_coverage_report


def test_components_from_lyric_normalizes_names_and_fourier(tmp_path):
    lyric = tmp_path / "sample.lyric"
    lyric.write_text(
        """# Disk component
Pa1) disk
Pa2) sersic_f
Pa21) 1
# Nucleus component
Pb1) nucleus
Pb2) gaussian
# Companion component
Pc1) companion
Pc2) sersic
# Bar component
Pd1) bar
Pd2) sersic
""",
        encoding="utf-8",
    )

    assert _components_from_lyric(str(lyric)) == {
        "disk",
        "fourier_m1",
        "agn",
        "companion",
        "bar",
    }


def test_components_from_lyric_uses_comment_and_profile_type_semantics(tmp_path):
    lyric = tmp_path / "generic.lyric"
    lyric.write_text(
        """# Bulge component (obj0), edgeondisk is mentioned in an implementation note
Pa1) obj0
Pa2) sersic
# Disk component (obj1)
Pb1) obj1
Pb2) edgeondisk
""",
        encoding="utf-8",
    )

    assert _components_from_lyric(str(lyric)) == {"bulge", "edge_on_disk"}


def test_components_from_lyric_rejects_unresolved_generic_profile(tmp_path):
    lyric = tmp_path / "ambiguous.lyric"
    lyric.write_text(
        """Pa1) obj0
Pa2) sersic
Pb1) obj1
Pb2) sersic
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unable to normalize"):
        _components_from_lyric(str(lyric))


def test_coverage_report_keeps_action_details_and_policy_state(tmp_path):
    row = {
        "object_id": "42",
        "round_id": "round-1",
        "action_type": "REFIT_PARAMETERS",
        "raw_action_type": "INCONCLUSIVE",
        "action": {
            "action_type": "REFIT_PARAMETERS",
            "parameter_changes": [
                {"target_model_label": "obj0", "parameter": "n"},
            ],
        },
        "rule_trace": [{"rule_id": "FIT_HEALTH_V1", "outcome": "INCONCLUSIVE"}],
        "automation": {"resolution": "numeric_only_retry"},
        "termination_checks": [],
        "policy_state": {"last_round_id": "round-1"},
    }
    summary = {
        "samples": [row],
        "completion_status": "complete",
        "validation_note": "proposal-only",
        "successful_entries": 1,
        "total_entries": 1,
        "failed_entries": 0,
        "numeric_only": False,
        "action_counts": {"REFIT_PARAMETERS": 1},
        "raw_action_counts": {"INCONCLUSIVE": 1},
        "candidate_action_counts": {},
        "workflow_status_counts": {"CONTINUE": 1},
    }

    details = _coverage_details(summary)
    assert details["action_target_counts"]["REFIT_PARAMETERS | obj0:n"] == 1
    assert details["inconclusive_reason_counts"]["FIT_HEALTH_V1"] == 1
    assert details["inconclusive_resolution_counts"]["numeric_only_retry"] == 1
    assert details["state_last_round_matches"]["rows"] == 1

    report_path = tmp_path / "coverage.md"
    _write_coverage_report(report_path, summary)
    report = report_path.read_text(encoding="utf-8")
    assert "Action Component / Target Breakdown" in report
    assert "INCONCLUSIVE Reasons and Resolution" in report
    assert "Object PolicyState Continuity" in report
