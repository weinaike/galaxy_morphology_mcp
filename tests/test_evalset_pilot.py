"""Focused tests for the read-only micro-pilot extraction boundary (component-evalset-v2)."""

from pathlib import Path

from component_analysis.evalset.pilot import _infer_action, _round_blocks, component_signature


def _lyric(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_single_band_signature_ignores_sky_and_reads_fourier(tmp_path: Path) -> None:
    path = _lyric(tmp_path / "galfit.02", "0) sersic\nF1) 0.2 45 1 1\n0) sky\n")
    assert component_signature(path, "single_band") == ["fourier_m1", "single_sersic"]


def test_multiband_signature_uses_component_labels(tmp_path: Path) -> None:
    path = _lyric(tmp_path / "obj.lyric", "Pa1) disk\nPa2) sersic\nPb1) bulge\nPb2) sersic\n")
    assert component_signature(path, "multi_band") == ["bulge", "disk"]


def test_multiband_unlabelled_single_sersic_is_unclassified(tmp_path: Path) -> None:
    path = _lyric(tmp_path / "obj.lyric", "Pa1) obj0\nPa2) sersic\n")
    assert component_signature(path, "multi_band") == ["unclassified_sersic"]


def test_pure_promotion_is_single_action(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "a.lyric", "Pa1) obj0\nPa2) sersic\n"), "multi_band")
    after = _round_blocks(_lyric(tmp_path / "b.lyric", "Pa1) disk\nPa2) sersic\n"), "multi_band")
    action = _infer_action(before, after)
    assert action["action_type"] == "PROMOTE_SINGLE_SERSIC_TO_DISK"


def test_promote_plus_add_bulge_is_compound(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "a.lyric", "Pa1) obj0\nPa2) sersic\n"), "multi_band")
    after = _round_blocks(_lyric(tmp_path / "b.lyric", "Pa1) disk\nPa2) sersic\nPb1) bulge\nPb2) sersic\n"), "multi_band")
    action = _infer_action(before, after)
    assert action["action_type"] == "COMPOUND"
    assert action["compound_reason"] == "NO_INTERMEDIATE_ROUND"
    assert [item["action_type"] for item in action["atomic_actions"]] == ["PROMOTE_SINGLE_SERSIC_TO_DISK", "PROPOSE_ADD"]
    assert action["atomic_actions"][1]["component"] == "bulge"


def test_promote_plus_inplace_fourier_is_compound(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "a.lyric", "Pa1) obj0\nPa2) sersic\n"), "multi_band")
    after = _round_blocks(_lyric(tmp_path / "b.lyric", "Pa1) disk\nPa2) sersic_f\n"), "multi_band")
    action = _infer_action(before, after)
    assert action["action_type"] == "COMPOUND"
    assert [item["action_type"] for item in action["atomic_actions"]] == ["PROMOTE_SINGLE_SERSIC_TO_DISK", "PROPOSE_ADD"]
    assert action["atomic_actions"][1]["component"] == "fourier_m1"


def test_gadotti_decomposition_is_replace_plus_add(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "galfit.01", "0) sersic\n"), "single_band")
    after = _round_blocks(_lyric(tmp_path / "galfit.02", "0) sersic\n0) expdisk\n"), "single_band")
    action = _infer_action(before, after)
    assert action["action_type"] == "COMPOUND"
    replace = action["atomic_actions"][0]
    assert replace["action_type"] == "PROPOSE_REPLACE"
    assert replace["replace_from"] == "single_sersic"
    assert replace["replace_to"] == "bulge"
    assert action["atomic_actions"][1] == {"action_type": "PROPOSE_ADD", "component": "disk", "replace_from": None, "replace_to": None}


def test_obj1845_fourier_addition_is_single_action(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "galfit.01", "0) sersic\n"), "single_band")
    after = _round_blocks(_lyric(tmp_path / "galfit.02", "0) sersic\nF1) 0.27 45 1 1\n"), "single_band")
    action = _infer_action(before, after)
    assert action == {"action_type": "PROPOSE_ADD", "component": "fourier_m1", "replace_from": None, "replace_to": None}


def test_unclassified_to_non_disk_is_unmappable_compound(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "a.lyric", "Pa1) obj0\nPa2) sersic\n"), "multi_band")
    after = _round_blocks(_lyric(tmp_path / "b.lyric", "Pa1) bulge\nPa2) sersic\nPb1) disk\nPb2) sersic\n"), "multi_band")
    action = _infer_action(before, after)
    assert action["action_type"] == "COMPOUND"
    assert action["compound_reason"] == "UNMAPPABLE_ATOMIC_CHANGE"


def test_unchanged_structure_is_refit(tmp_path: Path) -> None:
    before = _round_blocks(_lyric(tmp_path / "a.lyric", "Pa1) disk\nPa2) sersic\n"), "multi_band")
    after = _round_blocks(_lyric(tmp_path / "b.lyric", "Pa1) disk\nPa2) sersic\n"), "multi_band")
    assert _infer_action(before, after)["action_type"] == "REFIT_PARAMETERS"


def test_single_band_promotable_sersic_to_disk_is_promote(tmp_path: Path) -> None:
    before = _lyric(tmp_path / "galfit.01", "# Component number: 1\n 0) sersic\n 1) 10 10 1 1\n 4) 15\n 5) 3.0\n")
    after = _lyric(tmp_path / "galfit.02", "# Component number: 1\n 0) expdisk\n 1) 10 10 1 1\n 4) 15\n# Component number: 2\n 0) sersic\n 1) 10 10 1 1\n 4) 1.5\n 5) 4.0\n")
    action = _infer_action(_round_blocks(before, "single_band"), _round_blocks(after, "single_band"), single_band=True)
    assert action["action_type"] == "COMPOUND"
    assert [item["action_type"] for item in action["atomic_actions"]] == ["PROMOTE_SINGLE_SERSIC_TO_DISK", "PROPOSE_ADD"]


def test_single_band_elliptical_sersic_to_disk_is_still_promote(tmp_path: Path) -> None:
    before = _lyric(tmp_path / "galfit.01", "# Component number: 1\n 0) sersic\n 1) 10 10 1 1\n 4) 15\n 5) 3.0\n")
    after = _lyric(tmp_path / "galfit.02", "# Component number: 1\n 0) expdisk\n 1) 10 10 1 1\n 4) 15\n# Component number: 2\n 0) sersic\n 1) 10 10 1 1\n 4) 1.5\n 5) 4.0\n")
    action = _infer_action(_round_blocks(before, "single_band"), _round_blocks(after, "single_band"), single_band=True)
    assert [item["action_type"] for item in action["atomic_actions"]] == ["PROMOTE_SINGLE_SERSIC_TO_DISK", "PROPOSE_ADD"]


def test_single_band_bar_detection_by_n05_with_renumbering(tmp_path: Path) -> None:
    before = _lyric(tmp_path / "galfit.09", "# Component number: 1\n 0) expdisk\n 1) 10 10 1 1\n 4) 20\n# Component number: 2\n 0) sersic\n 1) 10 10 1 1\n 4) 2.5\n 5) 6.0\n# Component number: 3\n 0) sersic\n 1) 10 10 1 1\n 4) 1.5\n 5) 0.5\n# Component number: 4\n 0) sersic\n 1) 60 70 1 1\n 4) 3\n 5) 0.7\n")
    after = _lyric(tmp_path / "galfit.10", "# Component number: 1\n 0) expdisk\n 1) 10 10 1 1\n 4) 20\n# Component number: 2\n 0) sersic\n 1) 10 10 1 1\n 4) 2.5\n 5) 5.0\n# Component number: 3\n 0) sersic\n 1) 60 70 1 1\n 4) 3\n 5) 1.0\n")
    action = _infer_action(_round_blocks(before, "single_band"), _round_blocks(after, "single_band"), single_band=True)
    assert action == {"action_type": "PROPOSE_REMOVE", "component": "bar", "replace_from": None, "replace_to": None}


def test_single_band_structure_comment_overrides_n_heuristic(tmp_path: Path) -> None:
    text = "# Component number: 1\n# STRUCTURE: DISK (expdisk)\n 0) expdisk\n 1) 10 10 1 1\n# Component number: 2\n# STRUCTURE: COMPANION (sersic)\n 0) sersic\n 1) 60 70 1 1\n 5) 1.0\n"
    assert component_signature(_lyric(tmp_path / "g.feedme", text), "single_band") == ["companion", "disk"]


def test_gadotti_object_number_feedme_labels_and_params_parse(tmp_path: Path) -> None:
    text = (
        "# Object number: 1 -- Bulge (sersic)\n0) sersic\n1) 128.02  128.36  1  1\n4) 4.44           1\n5) 2.69           1\n"
        "# Object number: 2 -- Disk (expdisk)\n0) expdisk\n1) 127.88  128.12  1  1\n4) 11.51          1\n5) 0.0000        0\n"
        "# Object number: 3 -- Bar (sersic, n=0.5 fixed)\n0) sersic\n1) 128.02  128.36  1  1\n4) 14.87          1\n5) 0.5            0\n"
        "# Object number: 4\n0) sky\n"
    )
    blocks = _round_blocks(_lyric(tmp_path / "galfit.feedme", text), "single_band")
    assert [semantic for block in blocks for semantic in block["semantics"]] == ["bulge", "disk", "bar"]
    assert blocks[2]["n"] == 0.5 and blocks[0]["pos"] == (128.02, 128.36)


def test_headerless_feedme_parses_parameters_for_bar_heuristic(tmp_path: Path) -> None:
    text = "0) sersic\n1) 10 10 1 1\n4) 5\n5) 0.5 0\n0) sersic\n1) 10 10 1 1\n4) 2\n5) 4.0 1\n"
    blocks = _round_blocks(_lyric(tmp_path / "galfit.02", text), "single_band")
    assert [block["semantics"] for block in blocks] == [["bar"], ["bulge"]]


def test_parenthetical_component_type_labels_parse(tmp_path: Path) -> None:
    text = (
        "# Object number: 1\n0) sersic  #  Component type (Bulge)\n1) 10 10 1 1\n4) 3\n5) 4.0 1\n"
        "# Object number: 2\n0) sersic  #  Component type (Bar)\n1) 10 10 1 1\n4) 2\n5) 0.5 0\n"
        "# Object number: 3\n0) sersic  #  Component type (Disk)\n1) 10 10 1 1\n4) 20\n5) 1.0 0\n"
        "# Object number: 4\n0) sky\n"
    )
    assert component_signature(_lyric(tmp_path / "galfit.feedme", text), "single_band") == ["bar", "bulge", "disk"]


def test_unlabeled_sersic_n1_fixed_is_disk(tmp_path: Path) -> None:
    text = (
        "# Component number: 1\n0) sersic\n1) 10 10 1 1\n4) 20\n5) 1.0000      0\n"
        "# Component number: 2\n0) sersic\n1) 10 10 1 1\n4) 3\n5) 0.8940      1\n"
    )
    assert component_signature(_lyric(tmp_path / "galfit.03", text), "single_band") == ["bulge", "disk"]


def test_multiband_n_block_and_companion_g_block(tmp_path: Path) -> None:
    text = (
        "Pa1) obj0\nPa2) sersic\nPa6) [1.0,0.5,2.0,0.1,0]\n"
        "Pb1) obj1\nPb2) sersic\nPb6) [3.16,0.1,8.0,0.1,1]\n"
        "Pc1) obj2\nPc2) sersic\nPc6) [2.0,0.5,5.0,0.1,1]\n"
        "Na1) agn\n"
        "Ga1) a\nGa2) ['a', 'b']\nGb1) b\nGb2) ['c']\n"
    )
    blocks = _round_blocks(_lyric(tmp_path / "obj.lyric", text), "multi_band")
    semantics = {block["key"]: block["semantics"] for block in blocks}
    assert semantics["a"] == ["disk"]          # obj0 via n=1.0 fixed (QC ruling)
    assert semantics["N"] == ["agn"]           # N block
    assert semantics["c"] == ["companion"]     # non-host G member
    assert semantics["b"] == ["unidentified_sersic"]  # n=3.16 free, no anchor


def test_multiband_unresolvable_generic_block_is_unidentified(tmp_path: Path) -> None:
    text = "Pa1) obj0\nPa2) sersic\nPa6) [1.0,0.5,2.0,0.1,1]\nPb1) obj1\nPb2) sersic\nPb6) [3.16,0.1,8.0,0.1,1]\n"
    blocks = _round_blocks(_lyric(tmp_path / "obj.lyric", text), "multi_band")
    semantics = {block["key"]: block["semantics"] for block in blocks}
    assert semantics["a"] == ["unidentified_sersic"]
    assert semantics["b"] == ["unidentified_sersic"]


def test_multiband_anchor_resolution_via_labels(tmp_path: Path) -> None:
    text = "Pa1) disk\nPa2) sersic\nPa6) [1.0,0.5,2.0,0.1,0]\nPb1) obj1\nPb2) sersic\nPb6) [3.16,0.1,8.0,0.1,1]\n"
    blocks = _round_blocks(_lyric(tmp_path / "obj.lyric", text), "multi_band", anchors={"obj1": "bulge"})
    semantics = {block["key"]: block["semantics"] for block in blocks}
    assert semantics["a"] == ["disk"]
    assert semantics["b"] == ["bulge"]


def test_lyric_comment_labels_resolve_generic_blocks(tmp_path: Path) -> None:
    text = (
        "# Sersic function — Bulge (obj0): n=4 fixed, compact, dimmer than disk\nPa1) obj0\nPa2) sersic\nPa6) [4.0,0.1,8.0,0.1,0]\n"
        "# Sersic function — Disk (obj1): n=1 fixed, extended\nPb1) obj1\nPb2) sersic\nPb6) [1.0,1.0,1.0,0.1,0]\n"
        "# Sersic function — Bar (obj2): n=0.5 fixed, elongated\nPc1) obj2\nPc2) sersic\nPc6) [0.5,0.5,0.5,0.1,0]\n"
    )
    blocks = _round_blocks(_lyric(tmp_path / "obj.lyric", text), "multi_band")
    semantics = {block["key"]: block["semantics"] for block in blocks}
    assert semantics["a"] == ["bulge"]
    assert semantics["b"] == ["disk"]
    assert semantics["c"] == ["bar"]


def test_lyric_component_header_comments(tmp_path: Path) -> None:
    text = (
        "# ---------------- Component A: Bulge (Sersic, n free) ----------------\nPa1) obj0\nPa2) sersic\n"
        "# ---------------- Component C: AGN / central point source (tiny Sersic) ----------------\nPc1) obj2\nPc2) sersic\n"
        "# Profile C - Nucleus (obj2): high-n compact Sersic\nPd1) obj3\nPd2) sersic\n"
    )
    blocks = _round_blocks(_lyric(tmp_path / "obj.lyric", text), "multi_band")
    semantics = {block["key"]: block["semantics"] for block in blocks}
    assert semantics["a"] == ["bulge"]
    assert semantics["c"] == ["agn"]      # explicit AGN comment
    assert semantics["d"] == ["bulge"]    # Nucleus on P-block Sersic -> bulge (2026-09-20 ruling)


def test_round_input_ok_gates(tmp_path: Path) -> None:
    from component_analysis.evalset.pilot import round_input_ok
    (tmp_path / "m1").mkdir()
    (tmp_path / "m2").mkdir()
    (tmp_path / "s1").mkdir()
    # multi: no fit product
    lyric = _lyric(tmp_path / "m1" / "obj.lyric", "Ia1) [none,0]\nPa1) disk\nPa2) sersic\n")
    ok, reason = round_input_ok(lyric, "multi_band")
    assert not ok and reason == "NO_FIT_PRODUCT"
    # multi: fit product present but science missing
    lyric2 = _lyric(tmp_path / "m2" / "obj.lyric", "Ia1) [/nonexistent/sci.fits,0]\nPa1) disk\nPa2) sersic\n")
    (tmp_path / "m2" / "result.fits").write_text("x", encoding="utf-8")
    ok, reason = round_input_ok(lyric2, "multi_band")
    assert not ok and reason == "SCIENCE_INPUT_MISSING"
    # single: image missing / present
    feedme = _lyric(tmp_path / "s1" / "galfit.01", "A) image.fits\n0) sersic\n")
    ok, reason = round_input_ok(feedme, "single_band")
    assert not ok and reason == "INPUT_IMAGE_MISSING"
    _lyric(tmp_path / "s1" / "image.fits", "x")
    ok, reason = round_input_ok(feedme, "single_band")
    assert ok and reason is None


def test_benchmark_eligible_compound_patterns() -> None:
    from component_analysis.evalset.pilot import is_benchmark_eligible_compound
    promote_add = {"action_type": "COMPOUND", "atomic_actions": [
        {"action_type": "PROMOTE_SINGLE_SERSIC_TO_DISK", "component": "disk", "replace_from": None, "replace_to": None},
        {"action_type": "PROPOSE_ADD", "component": "bulge", "replace_from": None, "replace_to": None}]}
    replace_add = {"action_type": "COMPOUND", "atomic_actions": [
        {"action_type": "PROPOSE_REPLACE", "component": None, "replace_from": "single_sersic", "replace_to": "bulge"},
        {"action_type": "PROPOSE_ADD", "component": "disk", "replace_from": None, "replace_to": None}]}
    three_atoms = {"action_type": "COMPOUND", "atomic_actions": promote_add["atomic_actions"] + [
        {"action_type": "PROPOSE_ADD", "component": "companion", "replace_from": None, "replace_to": None}]}
    with_remove = {"action_type": "COMPOUND", "atomic_actions": [
        {"action_type": "PROPOSE_REPLACE", "component": None, "replace_from": "disk", "replace_to": "single_sersic"},
        {"action_type": "PROPOSE_REMOVE", "component": "bar", "replace_from": None, "replace_to": None}]}
    assert is_benchmark_eligible_compound(promote_add)
    assert is_benchmark_eligible_compound(replace_add)
    assert not is_benchmark_eligible_compound(three_atoms)
    assert not is_benchmark_eligible_compound(with_remove)
    assert not is_benchmark_eligible_compound({"action_type": "PROPOSE_ADD", "component": "bulge"})


def test_option_a_largest_re_n1_free_is_disk(tmp_path: Path) -> None:
    # 2026-09-21 ruling: among several unlabeled Sersics the largest-Re n≈1.0
    # block (fixed OR free) is the disk; non-largest fixed n=1.0 is a bulge.
    text = (
        "# Component number: 1\n0) sersic\n1) 128 128 1 1\n4) 11.6\n5) 1.0 1\n"
        "# Component number: 2\n0) sersic\n1) 128 128 1 1\n4) 3.5\n5) 0.5 0\n"
        "# Component number: 3\n0) sersic\n1) 128 128 1 1\n4) 1.0\n5) 2.0 1\n"
        "# Component number: 4\n0) sersic\n1) 128 128 1 1\n4) 2.0\n5) 1.0 0\n"
    )
    blocks = _round_blocks(_lyric(tmp_path / "galfit.03", text), "single_band")
    semantics = {block["size"]: block["semantics"] for block in blocks}
    assert semantics[11.6] == ["disk"]   # largest, n=1.0 free
    assert semantics[3.5] == ["bar"]
    assert semantics[1.0] == ["bulge"]   # n=2.0
    assert semantics[2.0] == ["bulge"]   # fixed n=1.0 but NOT the largest -> bulge in size mode


def test_option_a_single_unlabeled_keeps_fixed_rule(tmp_path: Path) -> None:
    text = (
        "# Object number: 1 -- Bulge (sersic)\n0) sersic\n1) 10 10 1 1\n4) 3\n5) 4.0 1\n"
        "# Object number: 2\n0) sersic\n1) 10 10 1 1\n4) 20\n5) 1.0 0\n"
    )
    blocks = _round_blocks(_lyric(tmp_path / "galfit.feedme", text), "single_band")
    assert [block["semantics"] for block in blocks] == [["bulge"], ["disk"]]
