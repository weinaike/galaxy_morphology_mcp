"""bar_recognition 算法核 — 从 bar_recognize 仓库逐字移植的自包含实现。

移植来源（逻辑保持逐位一致，含全部魔数/阈值/环境变量守卫）：
  - /home/wnk/code/bar_recognize/neuro_symbolic/rule_engine.py  (v2.4.1)
      → calculate_deprojected_ab / evaluate_galaxy_bar
  - /home/wnk/code/bar_recognize/neuro_symbolic/photometry.py
      → extract_photometrics_from_arrays（_extract_from_npz 的数组版）
        extract_guarded_features（_extract_guarded_features 逐字移植）
  - /home/wnk/code/bar_recognize/bar_classify/parsing.py
      → extract_json_from_text / try_fix_truncated_json

与 bar_lopsidedness_core.py 相同的移植纪律：算法逻辑零改动，只允许因
接口变化做的适配（npz 文件读取 → 内存数组传参），适配处在注释中标明。

行为对拍测试：tests/test_bar_recognition_parity.py（参考库存在时逐位断言）。
"""

import json
import math
import os
import re
from typing import Any, Dict, Optional

import numpy as np


# ════════════════════════════════════════════════════════════════════════════
# 第一部分：神经-符号天体物理判决引擎 v2.4.1（rule_engine.py 逐字移植）
# ════════════════════════════════════════════════════════════════════════════

def calculate_deprojected_ab(raw_ab: float, delta_pa_deg: float, inc_deg: float) -> float:
    """计算盘面去投影（Face-on）后的本征真实轴比 (a/b)_intrinsic。"""
    raw_ab = max(raw_ab, 1.0)
    inc_deg = min(max(inc_deg, 0.0), 85.0)
    inc_rad = math.radians(inc_deg)
    cos_i = math.cos(inc_rad)
    cos_i = max(cos_i, 0.087)

    delta_deg = abs(delta_pa_deg) % 180.0
    if delta_deg > 90.0:
        delta_deg = 180.0 - delta_deg
    delta_rad = math.radians(delta_deg)

    factor_a_sq = (math.cos(delta_rad) ** 2) + ((math.sin(delta_rad) / cos_i) ** 2)
    factor_b_sq = (math.sin(delta_rad) ** 2) + ((math.cos(delta_rad) / cos_i) ** 2)

    intrinsic_ab = raw_ab * math.sqrt(factor_a_sq / factor_b_sq)
    return float(intrinsic_ab)


def evaluate_galaxy_bar(features: Dict[str, Any], metadata: Dict[str, Any] = None, photometrics: Dict[str, Any] = None) -> Dict[str, Any]:
    """基于操作性几何判据架构执行全流程物理决策，并记录完整决策链。"""
    metadata = metadata or {}
    photometrics = photometrics or {}

    inc = float(features.get("inclination_deg") or 0.0)
    disk_pa = float(features.get("disk_pa_deg") or 0.0)
    # v2.4 无盘不成棒守卫特征：宿主盘性 ("disk"|"spheroid"|"uncertain")。
    # 缺失/异常值默认 "disk" (守卫不触发)，保证旧缓存回放与 v2.3 逐样本一致。
    has_outer_disk = str(features.get("has_outer_disk") or "disk").strip().lower()
    if has_outer_disk not in ("disk", "spheroid", "uncertain"):
        has_outer_disk = "disk"
    core_pa = float(features.get("optical_core_pa_deg") if features.get("optical_core_pa_deg") is not None else disk_pa)
    raw_ab_vlm = float(features.get("optical_core_axis_ratio") or 1.0)
    is_spiral = bool(features.get("is_spiral", False))
    arm_takeoff = str(features.get("spiral_arm_takeoff", "none")).lower()
    has_ansae = bool(features.get("residual_has_discrete_ansae", False))
    has_solid_bar = bool(features.get("residual_has_solid_pillar", False))
    ring_type = str(features.get("residual_ring_type", "none")).lower()
    has_companion = bool(features.get("has_unmasked_companion", False))
    core_contrast = str(features.get("residual_core_contrast", "none")).lower()

    re_obs = float(metadata.get("re_obs_px", 10.0))
    re_fit = float(metadata.get("re_fit_px", 10.0))

    has_bar_entity = (has_ansae or has_solid_bar)
    trace = []

    # ── 数据源路由：有伴星时 photometry 的 m2_amp 被像素污染，回退到 VLM 语义 ──
    m2_amp_raw = float(photometrics.get("m2_amp", 0.0))
    qc_raw = float(photometrics.get("quadrupole_contrast", 0.0))
    core_ab_phot = float(photometrics.get("core_ab_phot", 0.0))
    red_flux_raw = float(photometrics.get("red_flux_density", 0.0))

    # 解析 3 项图像预处理守卫与特异性测光特征
    clean_quad_ratio = float(photometrics.get("clean_quad_ratio", 0.0)) if photometrics.get("clean_quad_ratio") is not None else 0.0
    quad_alt_contrast = float(photometrics.get("quad_alt_contrast", 0.0)) if photometrics.get("quad_alt_contrast") is not None else 0.0
    has_real_deficit = bool(photometrics.get("has_real_deficit", False))
    guarded_ring_mod = float(photometrics.get("guarded_ring_mod", 0.0)) if photometrics.get("guarded_ring_mod") is not None else 0.0

    if has_companion:
        m2_amp = 0.0
        has_large_residual = (core_contrast == "strong_bipolar_bar" or (has_solid_bar and quad_alt_contrast >= 50.0))
        trace.append(f"Data Routing: Companion detected -> m2_amp set to 0.0, rely on VLM contrast ({core_contrast}) / alt_contrast={quad_alt_contrast:.1f}")
    else:
        m2_amp = m2_amp_raw
        has_large_residual = (m2_amp >= 0.15 or core_contrast == "strong_bipolar_bar" or quad_alt_contrast >= 50.0)
        trace.append(f"Data Routing: Clean field -> m2_amp={m2_amp:.3f}, large_residual={has_large_residual}")

    # ── 解法一：测光二阶矩客观锚定校准 (含外共振环与强四极实体保护) ──
    if core_ab_phot > 1.0:
        if not has_bar_entity and core_ab_phot < 1.15 and m2_amp_raw < 0.08 and raw_ab_vlm <= 1.65 and quad_alt_contrast < 40.0:
            raw_ab = min(raw_ab_vlm, 1.15)
            trace.append(f"Photometry Anchoring: Isotropic unbarred core (phot_ab={core_ab_phot:.2f}, m2={m2_amp_raw:.3f}) anchored DOWN from {raw_ab_vlm:.2f} to {raw_ab:.2f}")
        elif 1.18 <= raw_ab_vlm <= 1.38:
            if core_ab_phot >= 1.28 and m2_amp_raw >= 0.12:
                raw_ab = max(raw_ab_vlm, 1.30)
                trace.append(f"Photometry Anchoring: Borderline a/b={raw_ab_vlm:.2f} anchored UP to {raw_ab:.2f} (phot_ab={core_ab_phot:.2f}, m2={m2_amp_raw:.3f})")
            elif has_solid_bar and (ring_type == "outer_resonance_ring_with_inner_bar" or quad_alt_contrast >= 50.0):
                raw_ab = raw_ab_vlm
                trace.append(f"Photometry Anchoring: Resonant/Quadrupole solid bar entity preserves VLM a/b={raw_ab:.2f}")
            elif core_ab_phot < 1.15 and m2_amp_raw < 0.08 and quad_alt_contrast < 40.0:
                raw_ab = min(raw_ab_vlm, 1.15)
                trace.append(f"Photometry Anchoring: Borderline a/b={raw_ab_vlm:.2f} anchored DOWN to {raw_ab:.2f} (phot_ab={core_ab_phot:.2f}, m2={m2_amp_raw:.3f})")
            elif m2_amp_raw >= 0.20 and (has_bar_entity or arm_takeoff in ["curved_shoulder", "elbow"]):
                raw_ab = raw_ab_vlm
                trace.append(f"Photometry Anchoring: Strong m2={m2_amp_raw:.3f} with bar entity preserves VLM a/b={raw_ab:.2f}")
            else:
                raw_ab = round(0.65 * raw_ab_vlm + 0.35 * core_ab_phot, 2)
                trace.append(f"Photometry Anchoring: Borderline a/b={raw_ab_vlm:.2f} blended with phot_ab={core_ab_phot:.2f} -> {raw_ab:.2f}")
        else:
            raw_ab = raw_ab_vlm
    else:
        raw_ab = raw_ab_vlm

    delta_pa = abs(core_pa - disk_pa) % 180.0
    if delta_pa > 90.0:
        delta_pa = 180.0 - delta_pa

    intrinsic_ab = calculate_deprojected_ab(raw_ab, delta_pa, inc)

    trace.append(
        f"Precomputed: inc={inc:.1f}°, disk_pa={disk_pa:.1f}°, core_pa={core_pa:.1f}°, "
        f"ΔPA={delta_pa:.1f}°, raw_ab={raw_ab:.2f} (vlm={raw_ab_vlm:.2f}, phot={core_ab_phot:.2f}), "
        f"intrinsic_ab={intrinsic_ab:.2f}, ansae={has_ansae}, pillar={has_solid_bar}, "
        f"arm={arm_takeoff}, ring={ring_type}, host_disk={has_outer_disk}"
    )

    evaluated_predicates = {
        "inc": round(inc, 1),
        "delta_pa": round(delta_pa, 1),
        "raw_ab": round(raw_ab, 2),
        "intrinsic_ab": round(intrinsic_ab, 2),
        "m2_amp": round(m2_amp, 3),
        "has_ansae": has_ansae,
        "has_solid_bar": has_solid_bar,
        "has_bar_entity": has_bar_entity,
        "has_large_residual": has_large_residual,
        "quadrupole_contrast": round(qc_raw, 2),
        "is_spiral": is_spiral,
        "arm_takeoff": arm_takeoff,
        "ring_type": ring_type,
        "has_companion": has_companion,
        "has_outer_disk": has_outer_disk,
    }

    # ══════════════════════════════════════════════════════════════════════
    # 【第一层：星系大类体制判定 (Layer 1: Galaxy Regime Classifier)】
    # ══════════════════════════════════════════════════════════════════════

    # ── 超强物理证据豁免条件 (Super-Strong Physics Exemption) ──
    is_super_strong_bar = (m2_amp >= 0.25 and qc_raw >= 2.20 and (has_ansae or arm_takeoff == "elbow") and raw_ab >= 1.40)
    if is_super_strong_bar:
        trace.append(f"Physics Exemption: Super-strong quadrupole resonance detected (m2={m2_amp:.3f}, QC={qc_raw:.2f}) -> exempt from Layer 1 inclination trap")

    # ── Regime A1: 极度侧向刀锋盘 (Edge-on Knife Disks: i > 70 deg) ──
    if inc > 70.0 and not is_super_strong_bar and not (has_ansae and delta_pa >= 20.0) and not (has_solid_bar and delta_pa >= 20.0) and ring_type != "outer_resonance_ring_with_inner_bar":
        if (has_solid_bar or has_ansae) and core_contrast == "strong_bipolar_bar":
            trace.append("L1: Edge-on knife disk with strong bipolar entity -> Tier 2 Buffer")
            return {
                "contains_bar": "Uncertain",
                "bar_pa_deg": core_pa,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L1: Edge-on Knife Disk with Strong Entity (Tier 2)",
                "reasoning": f"Edge-on inclination (i={inc:.1f} deg > 70 deg) causes ambiguous geometric projection despite strong residual entity, buffered in Tier 2.",
                "decision_trace": trace,
                "matched_criteria": ["L1-A1: 刀锋盘强残差几何存疑缓冲 (i > 70°)"],
                "evaluated_predicates": evaluated_predicates
            }
        trace.append("L1: Edge-on knife disk filter matched (inc > 70° without decoupled entity) -> Tier 3")
        return {
            "contains_bar": False,
            "bar_pa_deg": None,
            "deprojected_ab": round(intrinsic_ab, 2),
            "delta_pa_deg": round(delta_pa, 1),
            "decision_path": "L1: Edge-on Knife Disk (i > 70 deg, Tier 3)",
            "reasoning": f"Edge-on inclination (i={inc:.1f} deg > 70 deg) obscures planar bar structure.",
            "decision_trace": trace,
            "matched_criteria": ["L1-A1: 极度侧向刀锋盘 (i > 70°)"],
            "evaluated_predicates": evaluated_predicates
        }

    # ── Regime A2: 高倾角侧向盘共线几何假拉长 (i >= 55 deg, ΔPA < 15 deg) ──
    # (v2.2: 删除门内不可达的 ΔPA>=20° 实体豁免条款 —— 门条件 ΔPA<15° 使其永假)
    if inc >= 55.0 and delta_pa < 15.0 and not is_super_strong_bar and ring_type != "outer_resonance_ring_with_inner_bar":
        if (has_solid_bar or has_ansae) and core_contrast == "strong_bipolar_bar":
            trace.append("L1: Highly inclined disk with strong bipolar entity -> Tier 2 Buffer")
            return {
                "contains_bar": "Uncertain",
                "bar_pa_deg": core_pa,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L1: Highly Inclined Disk with Bipolar Entity (Tier 2)",
                "reasoning": f"Highly inclined disk (i={inc:.1f} deg): strong residual bipolar entity ambiguous with collinear projection, buffered in Tier 2.",
                "decision_trace": trace,
                "matched_criteria": ["L1-A2: 高倾角共线双极实体存疑缓冲 (i >= 55°)"],
                "evaluated_predicates": evaluated_predicates
            }
        trace.append("L1: High-inclination collinear projection filter matched (inc >= 55°, ΔPA < 15°) -> Tier 3")
        return {
            "contains_bar": False,
            "bar_pa_deg": None,
            "deprojected_ab": round(intrinsic_ab, 2),
            "delta_pa_deg": round(delta_pa, 1),
            "decision_path": "L1: Highly Inclined Disk Projection (i >= 55 deg, Tier 3)",
            "reasoning": f"Highly inclined disk (i={inc:.1f} deg): collinear major axis projection obscures planar bar.",
            "decision_trace": trace,
            "matched_criteria": ["L1-A2: 高倾角共线假拉长 (i >= 55°, ΔPA < 15°)"],
            "evaluated_predicates": evaluated_predicates
        }

    # ══════════════════════════════════════════════════════════════════════
    # 【Regime B: 晚型旋涡体制 (Spiral Galaxy Subtree)】
    # ══════════════════════════════════════════════════════════════════════
    if is_spiral:
        trace.append("L1: Routed to Spiral Galaxy Subtree (is_spiral=True)")

        # ── 第二层：旋涡安全门控 ──
        # (v2.2: 删除旋涡高倾角共线门 —— 被 L1-A2 上游门+豁免逻辑联合封闭，数学不可达)
        if re_obs > 0 and (re_fit / re_obs) > 3.0 and not (ring_type == "outer_resonance_ring_with_inner_bar" or (has_ansae and delta_pa >= 15.0)):
            trace.append(f"L2-Spiral: Scale divergence matched (Re_fit/Re_obs={re_fit/re_obs:.1f} > 3.0) -> Tier 2")
            return {
                "contains_bar": "Uncertain",
                "bar_pa_deg": core_pa,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L2-Spiral: Fit Scale Divergence (Tier 2)",
                "reasoning": f"Scale ratio Re_fit/Re_obs={re_fit/re_obs:.1f} > 3.0 indicates model divergence.",
                "decision_trace": trace,
                "matched_criteria": ["L2-Sp: 1D 拟合尺度发散 (Re_fit/Re_obs > 3.0)"],
                "evaluated_predicates": evaluated_predicates
            }

        # ── 第三层：旋涡定量指标联合判定 ──
        sb_matches = []
        if is_super_strong_bar:
            sb_matches.append("Sp-SB_Super: 超强傅里叶四极矩确证棒 (m2>=0.25 ∧ QC>=2.20 ∧ elbow/ansae)")
        if arm_takeoff == "elbow" and (has_ansae or has_solid_bar or ring_type == "outer_resonance_ring_with_inner_bar" or intrinsic_ab >= 1.25 or m2_amp >= 0.18) and raw_ab >= 1.25 and (inc <= 52.0 or delta_pa >= 10.0 or is_super_strong_bar):
            sb_matches.append("Sp-SB1: 刚性折角强旋涡 (arm=elbow ∧ 实体/m2/本征轴比 ∧ raw_ab >= 1.25 ∧ (i<=52° ∨ ΔPA>=10°))")
        if has_ansae and (delta_pa >= 15.0 or (ring_type == "outer_resonance_ring_with_inner_bar" and inc < 50.0) or (raw_ab >= 1.25 and inc <= 52.0 and has_large_residual) or is_super_strong_bar):
            sb_matches.append("Sp-SB2: 解耦双耳旋涡棒 (has_ansae ∧ (ΔPA>=15° ∨ 外环 ∨ (raw_ab>=1.25 ∧ i<=52° ∧ 强残差)))")
        if has_solid_bar and delta_pa >= 15.0 and raw_ab >= 1.25 and m2_amp >= 0.08:
            sb_matches.append("Sp-SB3: 解耦实心柱旋涡棒 (has_solid_bar ∧ ΔPA >= 15° ∧ m2 >= 0.08)")
        if has_solid_bar and (raw_ab >= 1.30 or intrinsic_ab >= 1.22) and inc < 48.0 and delta_pa >= 5.0:
            sb_matches.append("Sp-SB4: 去投影确证实心柱 (solid_bar ∧ raw_ab>=1.30/int_ab>=1.22 ∧ i<48°)")
        if has_solid_bar and ring_type == "outer_resonance_ring_with_inner_bar" and inc < 50.0 and (raw_ab >= 1.25 or raw_ab_vlm >= 1.25):
            sb_matches.append("Sp-SB5: 外共振环实心柱旋涡棒 (solid_bar ∧ ring=outer_resonance ∧ raw_ab>=1.25 ∧ i<50°)")
        if has_solid_bar and (has_large_residual or m2_amp >= 0.25) and (raw_ab >= 1.25 or m2_amp >= 0.25) and inc < 48.0:
            sb_matches.append("Sp-SB6: 强残差实心柱旋涡棒 (solid_bar ∧ (large_residual∨m2>=0.25) ∧ i<48°)")
        if arm_takeoff == "curved_shoulder" and (has_solid_bar or has_large_residual) and raw_ab >= 1.30 and inc < 45.0:
            sb_matches.append("Sp-SB7: 强证据肩部旋涡棒 (arm=curved_shoulder ∧ (solid_bar ∨ large_residual) ∧ raw_ab>=1.30 ∧ i<45°)")
        if has_ansae and inc <= 32.0 and m2_amp >= 0.22 and qc_raw >= 1.90:
            sb_matches.append("Sp-SB8: 低倾角强四极矩双耳旋涡棒 (has_ansae ∧ m2>=0.22 ∧ QC>=1.90 ∧ i<=32°)")
        if has_solid_bar and core_contrast == "strong_bipolar_bar" and qc_raw >= 1.45 and m2_amp >= 0.12 and inc < 48.0:
            sb_matches.append("Sp-SB9: 强双极实心柱旋涡棒 (solid_bar ∧ strong_bipolar ∧ QC>=1.45 ∧ m2>=0.12 ∧ i<48°)")
        sa_matches = []
        is_diffuse_round_spiral = (not has_bar_entity and core_contrast in ["diffuse_gradient", "none"] and raw_ab <= 1.20 and core_ab_phot <= 1.08 and m2_amp < 0.10 and quad_alt_contrast < 35.0)
        is_suspicious_weak_bar = (raw_ab >= 1.26 and core_ab_phot >= 1.08 and m2_amp >= 0.22 and qc_raw >= 1.90)
        if not has_bar_entity and (arm_takeoff in ["smooth", "none"] or ring_type == "concentric_360" or is_diffuse_round_spiral) and not is_suspicious_weak_bar and (intrinsic_ab < 1.20 or m2_amp < 0.10 or raw_ab < 1.28):
            sa_matches.append("Sp-SA1: 经典光滑风车/各向同性弥散 SA (无实体 ∧ (smooth/concentric/diffuse) ∧ 低m2/低轴比)")
        if not has_bar_entity and arm_takeoff in ["curved_shoulder", "smooth", "none"] and raw_ab <= 1.15 and m2_amp < 0.10 and delta_pa <= 10.0 and inc < 40.0:
            sa_matches.append("Sp-SA2: 近正面各向同性低残差 SA (raw_ab<=1.15 ∧ m2<0.10 ∧ ΔPA<=10° ∧ 无实体)")
        if raw_ab_vlm <= 1.12 and m2_amp < 0.08 and arm_takeoff in ["smooth", "none"] and not has_ansae and inc < 45.0:
            sa_matches.append("Sp-SA3: 光滑弥散无残差 SA (raw_ab<=1.12 ∧ m2<0.08 ∧ smooth)")

        # 🟢 Tier 1 (Strong Barred Spiral SB):
        if sb_matches:
            trace.append(f"L3-Spiral: Matched SB rules: {sb_matches} -> Tier 1")
            return {
                "contains_bar": True,
                "bar_pa_deg": core_pa,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": f"L3-Spiral: Strong Barred Spiral (SB - Tier 1) [{sb_matches[0].split(':')[0]}]",
                "reasoning": f"Verified SB bar ({'; '.join(sb_matches)}): arm={arm_takeoff}, ansae={has_ansae}, pillar={has_solid_bar}, raw a/b={raw_ab:.2f}, intrinsic a/b={intrinsic_ab:.2f}.",
                "decision_trace": trace,
                "matched_criteria": sb_matches,
                "evaluated_predicates": evaluated_predicates
            }
        # 🔴 Tier 3 (Classical Unbarred SA Spiral):
        elif sa_matches:
            trace.append(f"L3-Spiral: Matched SA rules: {sa_matches} -> Tier 3")
            return {
                "contains_bar": False,
                "bar_pa_deg": None,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": f"L3-Spiral: Classical Unbarred SA Spiral (Tier 3) [{sa_matches[0].split(':')[0]}]",
                "reasoning": f"Smooth continuous inward spiral winding (m2={m2_amp:.2f}) without rigid bar perturbation.",
                "decision_trace": trace,
                "matched_criteria": sa_matches,
                "evaluated_predicates": evaluated_predicates
            }
        # 🟡 Tier 2 (Intermediate SAB / Transition / Cross-Band Conflict):
        else:
            trace.append("L3-Spiral: Neither SB nor SA decisively matched -> Tier 2 Buffer")
            return {
                "contains_bar": "Uncertain",
                "bar_pa_deg": core_pa if (has_solid_bar or raw_ab >= 1.25) else None,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L3-Spiral: Intermediate SAB / Cross-Band Conflict (Tier 2)",
                "reasoning": f"Intermediate nuclear elongation/shoulder (arm={arm_takeoff}, raw a/b={raw_ab:.2f}, intrinsic a/b={intrinsic_ab:.2f}, m2={m2_amp:.2f}) buffered in Tier 2.",
                "decision_trace": trace,
                "matched_criteria": ["L3-Sp: SAB 弱棒/过渡态缓冲"],
                "evaluated_predicates": evaluated_predicates
            }

    # ══════════════════════════════════════════════════════════════════════
    # 【Regime C: 早型透镜与椭圆体制 (Early-Type / S0 Subtree)】
    # ══════════════════════════════════════════════════════════════════════
    else:
        trace.append("L1: Routed to Early-Type / S0 Subtree (is_spiral=False)")

        # ── 第二层：S0 数据质量门控 ──
        if re_obs > 0 and (re_fit / re_obs) > 3.0 and not (ring_type == "outer_resonance_ring_with_inner_bar" and (has_solid_bar or has_ansae)) and not (has_ansae and has_solid_bar):
            trace.append(f"L2-S0: Scale divergence matched (Re_fit/Re_obs={re_fit/re_obs:.1f} > 3.0) -> Tier 2")
            return {
                "contains_bar": "Uncertain",
                "bar_pa_deg": core_pa,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L2-S0: Fit Scale Divergence (Tier 2)",
                "reasoning": f"Scale ratio Re_fit/Re_obs={re_fit/re_obs:.1f} > 3.0 indicates model divergence.",
                "decision_trace": trace,
                "matched_criteria": ["L2-S0: 1D 拟合尺度发散 (Re_fit/Re_obs > 3.0)"],
                "evaluated_predicates": evaluated_predicates
            }

        # ── 第三层：S0 多特征联合判决 ──
        sb0_matches = []
        if is_super_strong_bar:
            sb0_matches.append("A0: 超强傅里叶四极矩确证棒 (m2>=0.25 ∧ QC>=2.20 ∧ ansae)")
        if has_ansae and delta_pa >= 15.0:
            sb0_matches.append("A1: 角度解耦双耳 (has_ansae ∧ ΔPA >= 15.0°)")
        if has_bar_entity and (ring_type == "outer_resonance_ring_with_inner_bar" or (guarded_ring_mod >= 1.20 and ring_type != "concentric_360")):
            if has_ansae or (raw_ab >= 1.25 and (delta_pa >= 8.0 or (has_large_residual and qc_raw >= 1.80 and red_flux_raw >= 1.5))) or (intrinsic_ab >= 1.20 and delta_pa >= 10.0):
                sb0_matches.append("A3: 外共振环+棒耦合系统 (outer_ring ∧ (ansae ∨ (raw_ab>=1.25 ∧ ΔPA>=8°) ∨ int_ab>=1.20))")
        if has_ansae and has_solid_bar and inc < 50.0:
            sb0_matches.append("A4: 双实体共存 (双耳+实心柱 ∧ i < 50°)")
        if has_ansae and raw_ab >= 1.25 and ((has_large_residual and inc < 55.0) or (raw_ab >= 1.40 and inc < 45.0) or (inc < 38.0 and delta_pa >= 10.0)):
            sb0_matches.append("A6: 双耳棒倾角-轴比单调面 (ansae ∧ ab>=1.25 ∧ ((强残差∧i<55°) ∨ (ab>=1.40∧i<45°) ∨ (i<38°∧ΔPA>=10°)))")
        # A10: S0 去投影确证实心柱 (取消 delta_pa>=2.0 无物理依据硬限制，要求物理四极矩 qc>=1.30 或去投影反差深度 quad_alt_contrast>=40.0)
        if has_solid_bar and (raw_ab >= 1.30 or raw_ab_vlm >= 1.30 or intrinsic_ab >= 1.20) and inc < 45.0 and (qc_raw >= 1.30 or quad_alt_contrast >= 40.0) and not (ring_type == "outer_resonance_ring_with_inner_bar" and delta_pa < 5.0 and m2_amp < 0.10):
            sb0_matches.append("A10: 去投影确证实心柱 (raw_ab >= 1.30/int_ab >= 1.20 ∧ i < 45°)")

        # A11: 纯净宿主内嵌四极矩棒 (要求具备真实负亏损槽 has_real_deficit 彻底排除各向同性同心环)
        if ring_type == "none" and has_bar_entity and core_contrast == "strong_bipolar_bar" and inc < 38.0:
            if raw_ab >= 1.20:
                sb0_matches.append("A11: 纯净宿主内嵌四极矩棒 (clean_S0 ∧ strong_bipolar ∧ raw_ab>=1.20 ∧ i<38°)")
            elif raw_ab >= 1.10 and (qc_raw >= 1.50 or (quad_alt_contrast >= 50.0 and has_real_deficit)):
                sb0_matches.append("A11: 球状核球包裹四极矩内嵌棒 (clean_S0 ∧ solid_pillar/ansae ∧ (QC>=1.50∨(alt_contrast>=50∧deficit)) ∧ i<35°)")
        if has_ansae and m2_amp >= 0.40 and qc_raw >= 3.0 and inc < 45.0:
            sb0_matches.append("A12: 超强双耳四极矩透镜棒 (has_ansae ∧ m2>=0.40 ∧ QC>=3.0 ∧ i<45°)")

        is_sb0 = (ring_type != "concentric_360") and (len(sb0_matches) > 0)

        sa0_matches = []
        # 伪柱防御: 若已证实有真实负亏损槽 (has_real_deficit) 且交替反差深度 >= 50.0，绝不视为伪柱
        is_spurious_pillar = (not has_ansae and raw_ab <= 1.15 and core_ab_phot <= 1.08 and (qc_raw < 1.30 or m2_amp < 0.08) and (quad_alt_contrast < 50.0 or not has_real_deficit))
        if not is_sb0 and (not has_bar_entity or is_spurious_pillar):
            if raw_ab <= 1.15:
                sa0_matches.append("B1: 纯球状对称核球 (raw_ab <= 1.15)")
            if ring_type == "concentric_360" and raw_ab < 1.40:
                sa0_matches.append("B2: 1D Sérsic 同心圆牛眼失配 (ring=concentric_360 ∧ raw_ab < 1.40)")
            if intrinsic_ab < 1.25 or raw_ab < 1.40 or (delta_pa < 15.0 and intrinsic_ab < 1.16):
                sa0_matches.append("B5: 弥散弱椭化无棒剖面 (int_ab < 1.25 / raw_ab < 1.40)")

        is_sa0 = len(sa0_matches) > 0

        # ── 联合裁决 ──
        # v2.4 无盘不成棒守卫 (No-Disk-No-Bar Guard)：
        # 棒是盘动力学结构。VLM 从 global view 外区判定宿主为纯球状体 (spheroid,
        # 无盘面/透镜包络) 时，SB0 命中不成立——boxy/triaxial 等照度畸变在单
        # Sérsic 残差上的四极正超量是假双耳/假柱 mimic，改判 Tier 3。
        # 仅拦截正向棒判定 (Tier 1)；SA0/Tier-2 路径不受影响。
        #
        # 【宿主自洽守卫 (可选, 默认关闭)】倾角本身就是盘 b/a 的函数:
        # 模型自报 i >= 25° (b/a <= 0.91, 明显扁率) 却同时申报 spheroid 宿主,
        # 属于物理自相矛盾 (纯球状体不存在盘倾角)。开启
        # BAR_HOST_CONSISTENCY_GUARD=1 时此类 spheroid 申报不触发 G0 拦截。
        # 动机: openai gpt-5.6-sol 对中等倾角光滑盘的字面严格解读致 78% 的
        # spheroid 申报带自报 i>=25° 矛盾, G0 因此误杀 20 个真棒 (20260831)。
        # ⚠ 勿默认开启 (2026-09-01 全量证据, 含 836×v3 特征终审):
        #   - openai 旧提示词 121: F1 0.4932→0.7917 (+0.30, 场景已被 v3 提示词治本)
        #   - openai v3 提示词 121: 0.7727→0.8043 (+0.03)
        #   - gemini v3 提示词 121: 0.8545→0.8468 (−0.01)
        #   - 836 旧提示词特征: 0.7907→0.7740, Spec 92.06%→89.41% (+3TP/+14FP)
        #   - 836 v3 提示词特征 (20260901_val836_v3prompt_gemini): 0.8018→0.7939,
        #     Spec 90.07%→88.95% (翻转 7 = 救回 1 TP + 误放行 6 FP) —— 终审否决
        #   机理: 无棒星系上 "spheroid 矛盾申报 + 假实体" 常成对出现 (模型同时
        #   读错宿主与残差), G0 恰好在成对纠错; 一律抑制矛盾反而放行假棒。
        #   矛盾的正确治法在提示词 (v3 成分判据, 20260901), 本守卫仅作厂商
        #   漂移应急 (新厂商再现大规模矛盾申报时临时开启)。
        _consistency_inc = float(os.environ.get("BAR_HOST_CONSISTENCY_INC", "25"))
        _host_claim_contradicted = (
            os.environ.get("BAR_HOST_CONSISTENCY_GUARD", "") == "1"
            and has_outer_disk == "spheroid" and inc >= _consistency_inc
        )
        if _host_claim_contradicted:
            trace.append(
                f"Host-Consistency Guard: spheroid claim contradicted by self-reported "
                f"inclination i={inc:.1f} deg (b/a<={math.cos(math.radians(inc)):.2f}) — "
                f"a flattened disk exists by the model's own measurement; G0 not applied"
            )
        if is_sb0 and has_outer_disk == "spheroid" and not _host_claim_contradicted:
            trace.append(
                f"L3-S0 Disk-Guard: spheroid host (has_outer_disk={has_outer_disk}) — "
                f"SB0 hits {sb0_matches[0].split(':')[0]} reclassified as boxy isophotal "
                f"distortion mimic: a bar cannot exist without a disk -> Tier 3"
            )
            return {
                "contains_bar": False,
                "bar_pa_deg": None,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L3-S0: No-Disk Spheroid Host / Boxy Distortion Mimic (Tier 3) [G0]",
                "reasoning": (
                    f"Host is a featureless spheroid (has_outer_disk=spheroid); quadrupole residual "
                    f"(m2={m2_amp:.2f}, QC={qc_raw:.2f}) with ansae/pillar claims ({sb0_matches[0].split(':')[0]}) "
                    f"is a boxy/triaxial isophotal distortion mimic — no disk exists to host a stellar bar."
                ),
                "decision_trace": trace,
                "matched_criteria": [f"G0: 无盘不成棒守卫 (spheroid 宿主拦截 {sb0_matches[0].split(':')[0]})"],
                "evaluated_predicates": evaluated_predicates
            }
        if is_sb0:
            trace.append(f"L3-S0: Matched SB0 rules: {sb0_matches} -> Tier 1")
            return {
                "contains_bar": True,
                "bar_pa_deg": core_pa,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": f"L3-S0: SB0 Stellar Bar with Ansae/Pillar (Tier 1) [{sb0_matches[0].split(':')[0]}]",
                "reasoning": f"Dual-confirmed SB0 bar ({'; '.join(sb0_matches)}): raw a/b={raw_ab:.2f}, delta_pa={delta_pa:.1f} deg with prominent ansae/pillar.",
                "decision_trace": trace,
                "matched_criteria": sb0_matches,
                "evaluated_predicates": evaluated_predicates
            }
        elif is_sa0:
            trace.append(f"L3-S0: Matched SA0 rules: {sa0_matches} -> Tier 3")
            return {
                "contains_bar": False,
                "bar_pa_deg": None,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": f"L3-S0: Smooth Unbarred S0 (Tier 3) [{sa0_matches[0].split(':')[0]}]",
                "reasoning": f"Smooth unbarred S0 profile ({'; '.join(sa0_matches)}): m2={m2_amp:.2f}, intrinsic a/b={intrinsic_ab:.2f} lacking discrete ansae or bar bridge.",
                "decision_trace": trace,
                "matched_criteria": sa0_matches,
                "evaluated_predicates": evaluated_predicates
            }
        else:
            trace.append("L3-S0: Neither SB0 nor SA0 decisively matched -> Tier 2 Buffer")
            return {
                "contains_bar": "Uncertain",
                "bar_pa_deg": core_pa if (has_ansae or raw_ab >= 1.25) else None,
                "deprojected_ab": round(intrinsic_ab, 2),
                "delta_pa_deg": round(delta_pa, 1),
                "decision_path": "L3-S0: Borderline S0 / Evidence Conflict (Tier 2)",
                "reasoning": f"Borderline lens structure (intrinsic a/b={intrinsic_ab:.2f}, m2={m2_amp:.2f}) with ambiguous residual features.",
                "decision_trace": trace,
                "matched_criteria": ["L3-S0: 存疑边界透镜/证据冲突缓冲"],
                "evaluated_predicates": evaluated_predicates
            }


# ════════════════════════════════════════════════════════════════════════════
# 第二部分：测光特征提取（photometry.py 移植）
# ════════════════════════════════════════════════════════════════════════════

def extract_photometrics_from_arrays(sci: np.ndarray, resid: np.ndarray, mask: np.ndarray,
                                     bg_std: float, half_width_px: float,
                                     re_obs_px: float = 10.0,
                                     has_resid: bool = True) -> Optional[Dict[str, Any]]:
    """基于 float32 浮点矩阵计算高精度物理测光与几何特征。

    与参考实现 ``neuro_symbolic/photometry.py::_extract_from_npz`` 数学逐字一致；
    唯一接口适配：参考实现从 .npz 缓存读数组，本函数直接接收内存数组
    （即参考 save_galaxy_cutout_npz 保存的 zoom 裁剪内容）。

    ⚠ 入参必须是**以目标星系为中心的 zoom 裁剪**（本函数以数组几何中心为
    心做二阶矩与傅里叶分解，全幅偏心图会静默污染 core_ab_phot/m2_amp）。
    ``half_width_px`` = zoom 框半宽（= 裁剪数组半宽）。

    输出标准化字典（与参考一致）：
    - core_ab_phot / core_pa_phot: 核区 asinh 二阶矩轴比与主轴 PA
    - m2_amp / m2_pa: 残差正超量方位角 m=2 模式强度与相位
    - quadrupole_contrast / red_flux_density: 四极对比度与残差信噪比通量密度
    """
    try:
        sci = np.asarray(sci, dtype=float)
        mask = np.asarray(mask, dtype=bool)
        resid = np.asarray(resid, dtype=float)

        h, w = sci.shape
        cy = (h - 1) / 2.0
        cx = (w - 1) / 2.0
        y, x = np.mgrid[:h, :w]
        dy = y - cy
        dx = x - cx
        r = np.sqrt(dx**2 + dy**2)
        theta = np.arctan2(dy, dx)

        # 1. 核区浮点 asinh 软化二阶矩 (Lupton et al. 2004 标准物理拉伸，避免核峰淹没)
        r_core = max(4.0, min(half_width_px * 0.75, 1.2 * float(re_obs_px)))
        core_mask = (r <= r_core) & (~mask)
        outer_mask = (r > r_core * 1.2) & (~mask)
        bg = np.percentile(sci[outer_mask], 20) if np.any(outer_mask) else np.percentile(sci[~mask], 10)

        # 软化参数 a=2.0 * bg_std
        val = np.maximum(sci - bg, 0.0) / (2.0 * max(bg_std, 1e-4))
        I_core = np.arcsinh(val) * core_mask
        tot_I = np.sum(I_core) + 1e-6

        mu_x = np.sum(I_core * dx) / tot_I
        mu_y = np.sum(I_core * dy) / tot_I
        mu_xx = np.sum(I_core * (dx - mu_x)**2) / tot_I
        mu_yy = np.sum(I_core * (dy - mu_y)**2) / tot_I
        mu_xy = np.sum(I_core * (dx - mu_x) * (dy - mu_y)) / tot_I

        diff = mu_xx - mu_yy
        term = np.sqrt(diff**2 + 4.0 * mu_xy**2)
        lambda1 = 0.5 * (mu_xx + mu_yy + term)
        lambda2 = max(0.5 * (mu_xx + mu_yy - term), 1e-4)
        core_ab_phot = float(np.sqrt(lambda1 / lambda2))
        core_pa_phot = float((np.degrees(0.5 * np.arctan2(2.0 * mu_xy, diff)) + 90.0) % 180.0)

        # 2. 真实残差浮点正超量与 m=2 傅里叶分解
        red_excess = np.maximum(resid, 0.0) / max(bg_std, 1e-4) * (~mask) if has_resid else np.zeros_like(sci)
        inner_mask = (r >= 1.5) & (r <= min(half_width_px * 0.85, 2.0 * float(re_obs_px))) & (~mask)
        weights = red_excess * inner_mask
        tot_w = np.sum(weights) + 1e-6

        cos2 = np.sum(weights * np.cos(2.0 * theta)) / tot_w
        sin2 = np.sum(weights * np.sin(2.0 * theta)) / tot_w
        m2_amp = float(np.sqrt(cos2**2 + sin2**2)) if has_resid and np.sum(weights) > 0.1 else 0.0
        m2_pa = float((np.degrees(0.5 * np.arctan2(sin2, cos2)) + 90.0) % 180.0) if m2_amp > 0.01 else 0.0

        # 3. 四极矩正残差对比度与残差信噪比通量密度
        angle_diff = np.abs(np.degrees(theta) - (m2_pa - 90.0)) % 180.0
        angle_diff = np.minimum(angle_diff, 180.0 - angle_diff)
        major_q = inner_mask & (angle_diff <= 35.0)
        minor_q = inner_mask & (angle_diff >= 55.0)

        f_major = np.sum(red_excess * major_q) / (np.sum(major_q) + 1e-6)
        f_minor = np.sum(red_excess * minor_q) / (np.sum(minor_q) + 1e-6)
        quadrupole_contrast = float(f_major / (f_minor + 1e-4)) if has_resid else 1.0
        red_flux_density = float(np.sum(red_excess * inner_mask) / (np.sum(inner_mask) + 1e-6)) if has_resid else 0.0

        return {
            "core_ab_phot": round(core_ab_phot, 2),
            "core_pa_phot": round(core_pa_phot, 1),
            "m2_amp": round(m2_amp, 3),
            "m2_pa": round(m2_pa, 1),
            "quadrupole_contrast": round(quadrupole_contrast, 2),
            "red_flux_density": round(red_flux_density, 2),
            "data_source": "fits_float32"
        }
    except Exception:
        return None


def extract_guarded_features(orig: np.ndarray, res: np.ndarray, re_obs_px: float = 10.0,
                             inc_deg: float = 0.0, disk_pa_deg: float = 0.0,
                             is_spiral: bool = True) -> Dict[str, Any]:
    """提取 3 项图像预处理守卫下的特异性物理特征。

    与参考实现 ``neuro_symbolic/photometry.py::_extract_guarded_features`` 逐字
    一致。orig 为灰度渲染 PNG 数组、res 为 RGB 渲染 PNG 数组（seismic 残差图，
    用 R−B 通道差分），两者须为同框同几何的中心 zoom 视图（384×384）。

    守卫 1: 高斯质心动态重对齐（消除亚像素偏心假四极）
    守卫 2: 尺度空间隔离与去投影拉伸（36 扇区傅里叶 m=2、棒/垂直扇区反差）
    守卫 3: 外环闭合度与双峰调制
    """
    try:
        h, w = orig.shape
        cy_init, cx_init = h // 2, w // 2

        # 守卫 1: 高斯质心动态重对齐 (消除 1D 拟合亚像素偏心假四极)
        w_box = 10
        core_crop = orig[max(0, cy_init - w_box): min(h, cy_init + w_box + 1), max(0, cx_init - w_box): min(w, cx_init + w_box + 1)].astype(float)
        bg = np.percentile(core_crop, 10) if core_crop.size > 0 else 0
        core_crop = np.maximum(core_crop - bg, 0.0)
        if np.sum(core_crop) > 0:
            yy, xx = np.mgrid[-w_box: w_box + 1, -w_box: w_box + 1]
            cy = cy_init + float(np.sum(core_crop * yy) / np.sum(core_crop))
            cx = cx_init + float(np.sum(core_crop * xx) / np.sum(core_crop))
        else:
            cy, cx = float(cy_init), float(cx_init)

        # 守卫 2: 尺度空间隔离与去投影拉伸
        cos_i = max(np.cos(np.radians(inc_deg)), 0.20)
        pa_rad = np.radians(disk_pa_deg)

        r_sig = res[:, :, 0].astype(float)
        b_sig = res[:, :, 2].astype(float)
        sig = r_sig - b_sig

        y, x = np.ogrid[:h, :w]
        dx = x - cx
        dy = y - cy

        x_rot = dx * np.cos(pa_rad) + dy * np.sin(pa_rad)
        y_rot = -dx * np.sin(pa_rad) + dy * np.cos(pa_rad)
        y_deproj = y_rot / cos_i
        dist_deproj = np.sqrt(x_rot**2 + y_deproj**2)
        theta_deproj = np.arctan2(y_deproj, x_rot)

        r_core_in = 3.0
        r_core_out = min(max(1.2 * float(re_obs_px), 8.0), 28.0)
        core_mask = (dist_deproj >= r_core_in) & (dist_deproj <= r_core_out)

        n_sec = 36
        core_fluxes = []
        for i in range(n_sec):
            th_min = -np.pi + i * (2.0 * np.pi / n_sec)
            th_max = -np.pi + (i + 1) * (2.0 * np.pi / n_sec)
            sec_m = core_mask & (theta_deproj >= th_min) & (theta_deproj < th_max)
            core_fluxes.append(float(np.mean(sig[sec_m])) if np.sum(sec_m) > 0 else 0.0)

        core_fluxes = np.array(core_fluxes)
        fft_c = np.fft.rfft(core_fluxes)
        m0_core = np.abs(fft_c[0]) / n_sec
        m2_core = np.abs(fft_c[2]) * 2.0 / n_sec if len(fft_c) > 2 else 0.0
        clean_quad_ratio = float(m2_core / (m0_core + 5.0))

        pa_m2_rad = 0.5 * np.angle(fft_c[2]) if len(fft_c) > 2 else 0.0
        diff_bar = np.abs(np.arctan2(np.sin(theta_deproj - pa_m2_rad), np.cos(theta_deproj - pa_m2_rad)))
        bar_sector = core_mask & ((diff_bar < np.radians(30)) | (diff_bar > np.radians(150)))
        perp_sector = core_mask & ((diff_bar > np.radians(60)) & (diff_bar < np.radians(120)))

        bar_flux = float(np.mean(sig[bar_sector])) if np.sum(bar_sector) > 0 else 0.0
        perp_flux = float(np.mean(sig[perp_sector])) if np.sum(perp_sector) > 0 else 0.0
        quad_alt_contrast = float(bar_flux - perp_flux)
        has_real_deficit = bool(perp_flux < -10.0)

        # 守卫 3: 外环闭合度与双峰调制
        r_ring_in = max(1.2 * float(re_obs_px), 12.0)
        r_ring_out = min(max(2.8 * float(re_obs_px), 25.0), min(h, w) // 2 - 5)
        ring_mask = (dist_deproj >= r_ring_in) & (dist_deproj <= r_ring_out)

        ring_fluxes = []
        for i in range(n_sec):
            th_min = -np.pi + i * (2.0 * np.pi / n_sec)
            th_max = -np.pi + (i + 1) * (2.0 * np.pi / n_sec)
            sec_m = ring_mask & (theta_deproj >= th_min) & (theta_deproj < th_max)
            ring_fluxes.append(float(np.mean(sig[sec_m])) if np.sum(sec_m) > 0 else 0.0)
        ring_fluxes = np.array(ring_fluxes)

        pos_sec_count = np.sum(ring_fluxes > 5.0)
        is_closed_ring = bool(pos_sec_count >= 18)

        fft_r = np.fft.rfft(ring_fluxes)
        m0_ring = np.abs(fft_r[0]) / n_sec
        m2_ring = np.abs(fft_r[2]) * 2.0 / n_sec if len(fft_r) > 2 else 0.0
        guarded_ring_mod = float(m2_ring / (m0_ring + 5.0)) if (is_closed_ring and not is_spiral) else 0.0

        return {
            "clean_quad_ratio": round(clean_quad_ratio, 2),
            "quad_alt_contrast": round(quad_alt_contrast, 1),
            "has_real_deficit": has_real_deficit,
            "is_closed_ring": is_closed_ring,
            "guarded_ring_mod": round(guarded_ring_mod, 2)
        }
    except Exception:
        return {
            "clean_quad_ratio": 0.0,
            "quad_alt_contrast": 0.0,
            "has_real_deficit": False,
            "is_closed_ring": False,
            "guarded_ring_mod": 0.0
        }


# ════════════════════════════════════════════════════════════════════════════
# 第三部分：VLM 输出 JSON 解析（bar_classify/parsing.py 逐字移植）
# ════════════════════════════════════════════════════════════════════════════

def try_fix_truncated_json(text: str) -> Optional[dict]:
    start = text.rfind('{')
    if start == -1:
        return None
    fragment = text[start:]
    open_braces = fragment.count('{') - fragment.count('}')
    open_brackets = fragment.count('[') - fragment.count(']')
    fragment = fragment.rstrip().rstrip(',').rstrip(':')
    quote_count = fragment.count('"') - fragment.count('\\"')
    if quote_count % 2 == 1:
        fragment += '"'
    fragment += ']' * max(0, open_brackets)
    fragment += '}' * max(0, open_braces)
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        return None


def extract_json_from_text(text: str) -> Optional[dict]:
    # 1. ```json ... ``` 块
    md_matches = re.findall(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
    for block in reversed(md_matches):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    # 2. 最后一个完整 { ... }
    brace_count = 0
    end_idx = -1
    for i in range(len(text) - 1, -1, -1):
        if text[i] == '}':
            if end_idx == -1:
                end_idx = i
            brace_count += 1
        elif text[i] == '{':
            brace_count -= 1
            if brace_count == 0 and end_idx != -1:
                candidate = text[i:end_idx + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    end_idx = -1
                    brace_count = 0
    # 3. 修复截断
    return try_fix_truncated_json(text)
