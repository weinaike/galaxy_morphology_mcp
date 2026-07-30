import pytest

from eval.prepare_ui_s1_grpo_data import _parse_path_maps, remap_paths


def test_remap_paths_only_rewrites_prefix_boundaries():
    mappings = [("/media/source", "/mnt/target")]
    value = {
        "feedme": "/media/source/a/A.feedme",
        "other": ["/media/source2/keep", "/media/source/b.png"],
    }
    assert remap_paths(value, mappings) == {
        "feedme": "/mnt/target/a/A.feedme",
        "other": ["/media/source2/keep", "/mnt/target/b.png"],
    }


def test_path_map_requires_old_and_new():
    with pytest.raises(ValueError):
        _parse_path_maps(["missing-separator"])
