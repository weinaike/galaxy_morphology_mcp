from __future__ import annotations

import pytest

from src.component_analysis.shadow import _components_from_lyric


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
