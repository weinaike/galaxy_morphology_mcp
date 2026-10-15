"""Offline end-to-end tests for the bar_recognition MCP tool (VLM mocked).

Fixture: tests/test_data/NGC1097_* (a strongly barred SBb galaxy; the archived
GALFIT output FITS + fitted param file + mask + science image are all present).
"""

import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools import bar_recognition as br  # noqa: E402
from tools.bar_recognition import (  # noqa: E402
    bar_recognition,
    _load_fit_fits,
    _find_param_file,
)

TEST_DATA = Path(__file__).parent / "test_data"

# NGC1097 型强棒特征（elbow 旋臂折角 + 双耳/实心柱残差 + 高轴比拉长核）
NGC1097_FEATURES = {
    "is_spiral": True,
    "inclination_deg": 35.0,
    "disk_pa_deg": 35.0,
    "has_outer_disk": "disk",
    "optical_core_pa_deg": 70.0,
    "optical_core_axis_ratio": 2.2,
    "spiral_arm_takeoff": "elbow",
    "residual_has_discrete_ansae": True,
    "residual_has_solid_pillar": True,
    "residual_ring_type": "none",
    "residual_core_contrast": "strong_bipolar_bar",
    "has_unmasked_companion": False,
    "morphology_notes": "mocked VLM reply for unit test",
}


def _ngc1097_source_fits() -> Path:
    """定位 NGC1097 输出 FITS。

    test_run_galfit 的 real-GALFIT 流程测试会把 tests/test_data 下的输出
    FITS 移进 archives/ 子目录（run_galfit 的归档行为），因此这里在主位置
    缺失时回退取最新归档副本，保证测试顺序无关。
    """
    primary = TEST_DATA / "NGC1097_galfit.fits"
    if primary.exists():
        return primary
    archived = sorted(TEST_DATA.glob("archives/*/NGC1097_galfit.fits"))
    if archived:
        return archived[-1]
    pytest.skip("NGC1097_galfit.fits fixture not found (neither test_data/ nor archives/)")


@pytest.fixture
def ngc1097_round(tmp_path):
    """把 NGC1097 首轮拟合产物复制进 tmp 目录，模拟 archives/<round>/ 布局。"""
    galaxy_dir = tmp_path / "galaxy"
    round_dir = galaxy_dir / "archives" / "20260101T000000.deadbeef"
    round_dir.mkdir(parents=True)
    shutil.copy(_ngc1097_source_fits(), round_dir / "NGC1097_galfit.fits")
    for name in ("galfit.01", "NGC1097.1.finmask_nonan.fits",
                 "NGC1097.phot.1_nonan.fits"):
        src = TEST_DATA / name
        if src.exists():
            shutil.copy(src, round_dir / name)
    return round_dir


@pytest.fixture
def mock_vlm(monkeypatch):
    """mock VLM 客户端与 API 调用；测试可通过 mock_vlm['features'] 覆盖特征。"""
    state = {"features": NGC1097_FEATURES}

    def fake_call_vlm_api(client, base64_image, additional_content, system_message,
                          model=None, max_tokens=9600, temperature=0.3,
                          additional_images=None):
        state["system_message"] = system_message
        state["user_text"] = additional_content[0]["text"]
        state["n_images"] = 1 + len(additional_images or [])
        state["image_order"] = [img["description"] for img in additional_images or []]
        return json.dumps(state["features"]), None

    monkeypatch.setattr(br, "create_vlm_client", lambda *a, **k: (object(), None))
    monkeypatch.setattr(br, "call_vlm_api", fake_call_vlm_api)
    return state


# ── 数据加载 ────────────────────────────────────────────────────────────────

def test_load_fit_fits_galfit_layout():
    data = _load_fit_fits(str(_ngc1097_source_fits()))
    assert data["format"] == "galfit"
    assert data["sci"].shape == (1431, 1431)
    assert data["model"] is not None and data["resid"] is not None
    assert data["mask_hdul"] is None


def test_load_fit_fits_galfits_5hdu_layout(tmp_path):
    rng = np.random.default_rng(0)
    shape = (40, 40)
    path = tmp_path / "band_result.fits"
    hdus = [fits.PrimaryHDU(rng.normal(size=shape)),          # 0 residual
            fits.ImageHDU(np.zeros(shape)),                   # 1 mask
            fits.ImageHDU(np.ones(shape)),                    # 2 sigma
            fits.ImageHDU(rng.normal(size=shape)),            # 3 model
            fits.ImageHDU(rng.normal(size=shape))]            # 4 original
    fits.HDUList(hdus).writeto(path)
    data = _load_fit_fits(str(path))
    assert data["format"] == "galfits_band"
    assert data["sci"].shape == shape and data["resid"].shape == shape
    assert data["mask_hdul"] is not None


def test_find_param_file_prefers_latest_galfit(ngc1097_round):
    found = _find_param_file(str(ngc1097_round), "")
    assert found == str(ngc1097_round / "galfit.01")
    explicit = _find_param_file(str(ngc1097_round), str(ngc1097_round / "galfit.01"))
    assert explicit == str(ngc1097_round / "galfit.01")
    # 显式路径不存在 → 转入自动发现（不返回 None）
    bogus = _find_param_file(str(ngc1097_round), str(ngc1097_round / "galfit.99"))
    assert bogus == str(ngc1097_round / "galfit.01")


def test_find_param_file_walks_up_to_galaxy_dir(tmp_path):
    """FITS 深处目录无参数文件时，向上发现星系主目录里的 galfit.NN。"""
    galaxy = tmp_path / "g1" / "g2" / "g3" / "galaxy"
    deep = galaxy / "archives" / "round1"
    deep.mkdir(parents=True)
    (galaxy / "galfit.02").write_text("")
    assert _find_param_file(str(deep), "") == str(galaxy / "galfit.02")


def test_missing_param_file_degrades_with_warning(tmp_path, mock_vlm):
    """参数文件彻底缺失：不失败，降级执行并在结果与 note 小节中显式警告。"""
    # 深嵌套目录，向上 4 层内均无 galfit.NN/feedme，隔离 /tmp 下的无关文件
    bare = tmp_path / "a" / "b" / "c" / "round"
    bare.mkdir(parents=True)
    shutil.copy(_ngc1097_source_fits(), bare / "NGC1097_galfit.fits")

    result = bar_recognition(fits_file=str(bare / "NGC1097_galfit.fits"))
    assert result["status"] == "success"
    assert result["param_file"] is None
    assert result["warnings"] and "output_param_file" in result["warnings"][0]
    assert "重调" in result["message"]  # 降级信息已在返回值中自含
    assert result["first_round_components"] == []
    assert "⚠" in result["note_section"]
    # 降级路径下判决仍产出（mock 特征为强棒 → Tier 1 依旧成立）
    assert result["tier"] == 1


def test_explicit_param_file_restores_full_precision(tmp_path, mock_vlm):
    """备份入参生效：FITS 单独放置 + 显式 param_file → 无降级警告、成分解析成功。"""
    bare = tmp_path / "x" / "round"
    bare.mkdir(parents=True)
    shutil.copy(_ngc1097_source_fits(), bare / "NGC1097_galfit.fits")
    param_backup = tmp_path / "x" / "galfit.01"
    shutil.copy(TEST_DATA / "galfit.01", param_backup)

    result = bar_recognition(
        fits_file=str(bare / "NGC1097_galfit.fits"),
        param_file=str(param_backup))
    assert result["status"] == "success"
    assert result["param_file"] == str(param_backup)
    assert not result.get("warnings")
    assert result["first_round_components"]  # 成分表来自显式参数文件


# ── 端到端（mock VLM）───────────────────────────────────────────────────────

def test_bar_recognition_tier1_end_to_end(ngc1097_round, mock_vlm):
    fits_file = str(ngc1097_round / "NGC1097_galfit.fits")
    result = bar_recognition(
        fits_file=fits_file,
        source_id="NGC1097",
        custom_instructions="unit test",
    )

    assert result["status"] == "success"
    assert result["vlm_status"] == "ok"
    assert result["tier"] == 1
    assert "Tier 1" in result["message"]
    assert "warnings" not in result["message"]
    assert result["contains_bar"] is True
    # Tier 1 必须给出棒 PA 与投影轴比（VLM 轨 raw_ab）
    assert result["bar"]["pa_deg"] == pytest.approx(70.0)
    assert result["bar"]["projected_axis_ratio"] == pytest.approx(2.2)
    assert result["bar"]["galfit_q"] == pytest.approx(1.0 / 2.2, abs=1e-3)
    assert "80% 概率存在 bar" in result["tier_label"]

    # 几何合理性
    geo = result["geometry"]
    assert geo["re_obs_px"] > 0
    assert geo["half_width_px"] <= min((1431, 1431)) / 4 + 1e-6
    assert result["first_round_components"]  # galfit.01 解析出成分

    # 测光已计算（含数组轨字段）
    for key in ("core_ab_phot", "m2_amp", "quadrupole_contrast"):
        assert key in result["photometrics"]

    # 三视图落盘且为 384px 见方（参考渲染约定 figsize 3.84 @ dpi100）
    from PIL import Image
    for key in ("global", "core_zoom", "residual_zoom"):
        p = result["images"][key]
        assert Path(p).exists(), p
        with Image.open(p) as im:
            assert im.size == (384, 384), (p, im.size)

    # VLM 收到 3 张图，顺序 global → core → residual（description 承载 Image2/3 角色）
    assert mock_vlm["n_images"] == 3
    assert mock_vlm["image_order"][0].startswith("Image 2")
    assert mock_vlm["image_order"][1].startswith("Image 3")

    # JSON / MD 结果落盘
    assert Path(result["analysis_file"]).exists()
    payload = json.loads(Path(result["analysis_file"]).read_text(encoding="utf-8"))
    assert payload["tier"] == 1 and payload["verdict"]["contains_bar"] is True

    # 工具不写 working_note：返回 note_section 供 Agent 追加，且不产生 note 文件
    section = result["note_section"]
    assert "Tier 1" in section and "bar_pa_deg" in section and "raw_ab" in section
    assert "拟合指引" in section
    assert not (ngc1097_round.parent.parent / "working_note.md").exists()
    assert "working_note_file" not in result


def test_bar_recognition_vlm_failure_maps_to_tier2(ngc1097_round, monkeypatch):
    """VLM 彻底失败 → 参考语义：Tier 2（Uncertain），status 仍为 success。"""
    monkeypatch.setattr(br, "create_vlm_client", lambda *a, **k: (object(), None))

    def failing_api(*a, **k):
        return None, "simulated API failure"

    monkeypatch.setattr(br, "call_vlm_api", failing_api)
    result = bar_recognition(fits_file=str(ngc1097_round / "NGC1097_galfit.fits"))
    assert result["status"] == "success"
    assert result["vlm_status"] == "failed"
    assert result["tier"] == 2
    assert result["contains_bar"] == "Uncertain"
    assert "bar" not in result  # Tier 2 不给棒参数
    assert "simulated API failure" in result["error"]


def test_bar_recognition_note_section_for_tier3(ngc1097_round, mock_vlm):
    """Tier 3 特征（无实体、低轴比、光滑旋臂）时 note_section 表达无棒结论。"""
    mock_vlm["features"] = {
        **NGC1097_FEATURES,
        "optical_core_pa_deg": 35.0,     # ΔPA=0
        "optical_core_axis_ratio": 1.05,
        "spiral_arm_takeoff": "smooth",
        "residual_has_discrete_ansae": False,
        "residual_has_solid_pillar": False,
        "residual_core_contrast": "flat_clean",
    }
    result = bar_recognition(fits_file=str(ngc1097_round / "NGC1097_galfit.fits"))
    assert result["status"] == "success"
    assert result["tier"] == 3
    assert "80% 概率无 bar" in result["tier_label"]
    assert "不得主动添加 Bar 成分" in result["note_section"]


def test_bar_recognition_missing_file():
    result = bar_recognition(fits_file="/nonexistent/galfit.fits")
    assert result["status"] == "failure"


# ── MCP 注册 ────────────────────────────────────────────────────────────────

def _list_tools_in_subprocess(extra_env: dict) -> list:
    """子进程导入 mcp_server 并列出工具名（注册发生在 import 时，须隔离子进程）。"""
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k not in ("GALFIT_BIN", "GALFITS_BIN")}
    env.update(extra_env)
    src_dir = str(Path(__file__).parent.parent / "src")
    out = subprocess.run(
        [sys.executable, "-c",
         "import asyncio, mcp_server; "
         "print(','.join(t.name for t in asyncio.run(mcp_server.app.list_tools())))"],
        cwd=src_dir, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().split(",")


def test_registration_galfit_block():
    """bar_recognition 随 GALFIT 工具组注册（GALFIT_BIN 置位时可用）。"""
    names = _list_tools_in_subprocess({"GALFIT_BIN": "/usr/bin/true"})
    assert "bar_recognition" in names
    assert "run_galfit" in names
    # view_original_image / render_original 已移至 GalfitS 组
    assert "view_original_image" not in names


def test_registration_absent_without_galfit():
    names = _list_tools_in_subprocess({})
    assert "bar_recognition" not in names
