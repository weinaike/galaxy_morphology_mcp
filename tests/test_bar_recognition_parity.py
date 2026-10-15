"""Parity tests: bar_recognition_core vs the reference bar_recognize pipeline.

The reference repo (/home/wnk/code/bar_recognize) is imported directly and the
ported implementations are asserted to be bit-identical on:
  1. the deterministic rule engine (evaluate_galaxy_bar) over a seeded random
     feature grid covering both subtrees, all gates and the env-var guard;
  2. the dual-track photometry (npz float track + PNG guarded track);
  3. the VLM-output JSON parser.

If the reference repo is absent, these tests skip gracefully.
"""

import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

REF_ROOT = Path("/home/wnk/code/bar_recognize")
_ref_missing = not (REF_ROOT / "neuro_symbolic" / "rule_engine.py").exists()

pytestmark = pytest.mark.skipif(
    _ref_missing, reason=f"reference repo not found: {REF_ROOT}")

if not _ref_missing:
    sys.path.insert(0, str(REF_ROOT))
    from neuro_symbolic.rule_engine import evaluate_galaxy_bar as ref_evaluate_galaxy_bar  # noqa: E402
    from neuro_symbolic.photometry import extract_galaxy_photometrics as ref_extract_photometrics  # noqa: E402
    from bar_classify.parsing import extract_json_from_text as ref_extract_json  # noqa: E402

from tools.bar_recognition_core import (  # noqa: E402
    evaluate_galaxy_bar,
    extract_photometrics_from_arrays,
    extract_guarded_features,
    extract_json_from_text,
)

VERDICT_KEYS = ("contains_bar", "bar_pa_deg", "deprojected_ab", "delta_pa_deg",
                "decision_path", "reasoning", "matched_criteria",
                "evaluated_predicates")

# ── 特征取值空间（覆盖三层判决架构的所有门与分支） ─────────────────────────
INCLINATIONS = [0.0, 25.0, 40.0, 52.0, 55.0, 60.0, 70.0, 75.0, 80.0]
DELTA_PAS = [0.0, 8.0, 15.0, 30.0, 90.0]
DISK_PAS = [0.0, 30.0, 100.0]
CORE_ABSD = [1.0, 1.05, 1.12, 1.15, 1.2, 1.26, 1.3, 1.38, 1.5, 1.65, 1.8, 2.2]
IS_SPIRAL = [True, False]
HOST_DISK = ["disk", "spheroid", "uncertain"]
ARM_TAKEOFF = ["elbow", "curved_shoulder", "smooth", "none"]
RING_TYPES = ["none", "concentric_360", "outer_resonance_ring_with_inner_bar"]
CORE_CONTRASTS = ["flat_clean", "bullseye_concentric", "strong_bipolar_bar",
                  "diffuse_gradient"]
FLAGS = [True, False]
M2_AMPS = [0.0, 0.02, 0.08, 0.12, 0.18, 0.25, 0.40]
QC_VALUES = [0.5, 1.3, 1.45, 1.90, 2.20, 3.0]
CORE_AB_PHOT = [1.0, 1.05, 1.08, 1.15, 1.28, 1.50]
ALT_CONTRASTS = [0.0, 35.0, 40.0, 50.0, 60.0]
RE_RATIOS = [1.0, 3.5]


def _random_feature_set(rng: random.Random) -> dict:
    disk_pa = rng.choice(DISK_PAS)
    core_pa = (disk_pa + rng.choice(DELTA_PAS)) % 180.0
    return {
        "is_spiral": rng.choice(IS_SPIRAL),
        "inclination_deg": rng.choice(INCLINATIONS),
        "disk_pa_deg": disk_pa,
        "has_outer_disk": rng.choice(HOST_DISK),
        "optical_core_pa_deg": core_pa,
        "optical_core_axis_ratio": rng.choice(CORE_ABSD),
        "spiral_arm_takeoff": rng.choice(ARM_TAKEOFF),
        "residual_has_discrete_ansae": rng.choice(FLAGS),
        "residual_has_solid_pillar": rng.choice(FLAGS),
        "residual_ring_type": rng.choice(RING_TYPES),
        "residual_core_contrast": rng.choice(CORE_CONTRASTS),
        "has_unmasked_companion": rng.choice(FLAGS),
    }, {
        "m2_amp": rng.choice(M2_AMPS),
        "quadrupole_contrast": rng.choice(QC_VALUES),
        "core_ab_phot": rng.choice(CORE_AB_PHOT),
        "red_flux_density": rng.choice([0.5, 2.0]),
        "clean_quad_ratio": rng.choice([0.0, 0.3]),
        "quad_alt_contrast": rng.choice(ALT_CONTRASTS),
        "has_real_deficit": rng.choice(FLAGS),
        "guarded_ring_mod": rng.choice([0.0, 1.3]),
    }


@pytest.mark.parametrize("seed", [42])
def test_rule_engine_parity_grid(seed, monkeypatch):
    """种子随机网格上判决 dict 逐字段与参考实现一致，且三态结论都有覆盖。"""
    monkeypatch.delenv("BAR_HOST_CONSISTENCY_GUARD", raising=False)
    rng = random.Random(seed)
    outcomes = {True: 0, False: 0, "Uncertain": 0}
    for _ in range(3000):
        features, phot = _random_feature_set(rng)
        re_obs = rng.choice([10.0, 30.0])
        metadata = {"re_obs_px": re_obs, "re_fit_px": re_obs * rng.choice(RE_RATIOS)}
        ours = evaluate_galaxy_bar(features, metadata, phot)
        ref = ref_evaluate_galaxy_bar(features, metadata, phot)
        for key in VERDICT_KEYS:
            assert ours[key] == ref[key], (
                f"divergence on {key} for features={features}\n"
                f"phot={phot}\nmeta={metadata}\nours={ours.get(key)}\nref={ref.get(key)}")
        outcomes[ours["contains_bar"]] += 1
    # 网格有效性：三态结论都必须出现，否则对拍没有意义
    assert outcomes[True] > 100 and outcomes[False] > 100 and outcomes["Uncertain"] > 100, outcomes


def test_rule_engine_env_guard_parity(monkeypatch):
    """BAR_HOST_CONSISTENCY_GUARD=1 的 G0 分支（含 INC 阈值）两侧翻转一致。"""
    # spheroid 宿主 + SB0 命中特征（A1: ansae ∧ ΔPA>=15°）
    features = {
        "is_spiral": False, "inclination_deg": 30.0, "disk_pa_deg": 10.0,
        "has_outer_disk": "spheroid", "optical_core_pa_deg": 40.0,
        "optical_core_axis_ratio": 1.45, "spiral_arm_takeoff": "none",
        "residual_has_discrete_ansae": True, "residual_has_solid_pillar": False,
        "residual_ring_type": "none", "residual_core_contrast": "strong_bipolar_bar",
        "has_unmasked_companion": False,
    }
    phot = {"m2_amp": 0.10, "quadrupole_contrast": 1.0, "core_ab_phot": 1.0,
            "red_flux_density": 0.0, "quad_alt_contrast": 0.0}
    metadata = {"re_obs_px": 10.0, "re_fit_px": 10.0}

    for guard, inc in [("0", 30.0), ("1", 30.0), ("1", 20.0), ("1", 26.0)]:
        monkeypatch.setenv("BAR_HOST_CONSISTENCY_GUARD", guard)
        monkeypatch.setenv("BAR_HOST_CONSISTENCY_INC", "25")
        features["inclination_deg"] = inc
        ours = evaluate_galaxy_bar(features, metadata, phot)
        ref = ref_evaluate_galaxy_bar(features, metadata, phot)
        for key in VERDICT_KEYS:
            assert ours[key] == ref[key], f"guard={guard} inc={inc} key={key}"
        # 守卫开启且 i>=25° 时 spheroid 矛盾申报不触发 G0（Tier 1 保留）；
        # 其余情形 G0 拦截改判 Tier 3 —— 两实现必须同步。
        if guard == "1" and inc >= 25.0:
            assert ref["contains_bar"] is True
        elif guard != "1":
            assert ref["contains_bar"] is False


PHOTOMETRY_GIDS = ["KILOGAS_101", "KILOGAS_102", "KILOGAS_114", "KILOGAS_120", "KILOGAS_13"]


@pytest.mark.parametrize("gid", PHOTOMETRY_GIDS)
def test_photometry_parity_npz_track(gid):
    """npz 浮点轨 + PNG 守卫轨的合成结果与参考 extract_galaxy_photometrics 一致。"""
    from PIL import Image
    images_dir = REF_ROOT / "images_data"
    npz_path = images_dir / f"{gid}_cutout.npz"
    if not npz_path.exists():
        pytest.skip(f"{npz_path} not found")
    orig_png = images_dir / f"{gid}_original_zoom.png"
    res_png = images_dir / f"{gid}_residual_zoom.png"

    data = np.load(npz_path)
    re_obs = float(data["re_obs"]) if "re_obs" in data else 10.0
    inc, disk_pa, is_sp = 35.0, 45.0, True

    ref = ref_extract_photometrics(gid, images_dir, re_obs, inc, disk_pa, is_sp)

    ours = extract_photometrics_from_arrays(
        np.array(data["sci"], dtype=float), np.array(data["resid"], dtype=float),
        np.array(data["mask"], dtype=bool), float(data["bg_std"]),
        float(data["half_width_px"]), re_obs_px=re_obs,
        has_resid=bool(data["has_resid"]) if "has_resid" in data else True)
    assert ours is not None
    if orig_png.exists() and res_png.exists():
        orig_gray = np.array(Image.open(orig_png).convert("L"), dtype=float)
        res_rgb = np.array(Image.open(res_png).convert("RGB"), dtype=float)
        ours.update(extract_guarded_features(orig_gray, res_rgb, re_obs, inc, disk_pa, is_sp))

    assert ours == ref, f"photometry divergence for {gid}: ours={ours} ref={ref}"


def test_extract_json_from_text_parity():
    fenced = 'blah\n```json\n{"is_spiral": true, "inclination_deg": 30}\n```\ntail'
    trailing = 'analysis text {"disk_pa_deg": 45.0, "n": [1, 2]}'
    truncated = 'partial {"a": {"b": "unclosed'
    for text in (fenced, trailing, truncated):
        assert extract_json_from_text(text) == ref_extract_json(text)
    assert extract_json_from_text(fenced) == {"is_spiral": True, "inclination_deg": 30}
    assert extract_json_from_text(trailing) == {"disk_pa_deg": 45.0, "n": [1, 2]}
    # 截断修复从最后一个 { 起修，外层 {"a": ...} 包装被丢弃 —— 与参考一致
    assert extract_json_from_text(truncated) == {"b": "unclosed"}
    assert extract_json_from_text("no json at all") is None
