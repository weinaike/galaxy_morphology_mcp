import json
import re

from data_gen.convert_sft_to_llamafactory import (
    _assistant_target,
    _build_history_summary_replica,
)
from data_gen.model_input_sanitization import (
    normalize_generation_artifacts,
    sanitize_summary_for_model,
)
from data_gen.vlm_proposal import build_multiturn_prompts, build_proposal_prompt


RAW_SUMMARY = """# GALFIT Fitting Summary

**Output File:** `/media/zhongling/wyh/GalDecomp_Gen/output/E7/g/node.fits`

## Init. par. file Content
A) ../../../gadotti_data/SDSS_gband/g/image.fit    # Input image
B) node.fits    # Output image
C) ../../../gadotti_data/SDSS_gband/g/sigma.fit    # Sigma
D) ../../../gadotti_data/SDSS_gband/g/psf.fit    # PSF
F) ../../../gadotti_data/SDSS_gband/g/mask.fit    # Mask
G) node.cons    # Constraint
H) 1 256 1 256

## Fit log Content
Input image     : ../../../gadotti_data/SDSS_gband/g/image.fit[1:256,1:256]
Init. par. file : /media/zhongling/wyh/GalDecomp_Gen/output/E7/g/node.feedme
Restart file    : galfit.08
Output image    : node.fits
Chi^2/nu = 0.754
| BIC | 140.9105 |
"""


def test_summary_paths_are_normalized_but_metrics_are_preserved():
    cleaned = sanitize_summary_for_model(RAW_SUMMARY)

    assert "/media/zhongling" not in cleaned
    assert "../../../gadotti_data" not in cleaned
    assert "**Output File:** `<OUTPUT_FITS>`" in cleaned
    assert "A) <INPUT_IMAGE>" in cleaned
    assert "C) <SIGMA_IMAGE>" in cleaned
    assert "D) <PSF_IMAGE>" in cleaned
    assert "F) <MASK_IMAGE>" in cleaned
    assert "G) <CONSTRAINT_FILE>" in cleaned
    assert "Init. par. file : <CURRENT_FEEDME>" in cleaned
    assert "Chi^2/nu = 0.754" in cleaned
    assert "| BIC | 140.9105 |" in cleaned


def test_history_describes_state_transition_without_annealing_method():
    root = {
        "node_id": "root",
        "parent_id": None,
        "depth": 0,
        "metrics": {"chi2_nu": 1.0, "bic": 100.0},
    }
    degraded = {
        "node_id": "child",
        "parent_id": "root",
        "depth": 1,
        "metrics": {"chi2_nu": 1.1, "bic": 110.0},
        "is_accepted": True,
        "mh_accepted": True,
        "action_from_parent": {
            "coarse_label": "modify",
            "target": "释放 Bulge 的 n",
        },
    }
    tree = {"nodes": [root, degraded]}

    history = _build_history_summary_replica(degraded, tree)

    assert "退火" not in history
    assert (
        "第1步 执行[modify]，该结果作为后续状态；"
        "相对上一步质量未改善"
    ) in history
    assert "chi2_nu=1.1000, BIC=110.0000" in history


def test_existing_assistant_annealing_wording_is_rewritten():
    cases = {
        "被退火算法拒绝或随后删除": "未被保留或随后被删除",
        "多次退火均无法改善": "多次尝试均未改善",
        "第 10 步的退火接受实际上降低了模型质量":
            "第 10 步的结果虽被保留为后续状态，但实际上降低了模型质量",
        "之前的退火算法可能做出了错误的接受决定":
            "之前曾将未改善的结果保留为后续状态",
        "退火算法在第2步接受了移除 Disk 的操作":
            "第 2 步执行并保留了移除 Disk 的操作",
        "当前状态是退火算法接受的较差探索节点":
            "当前状态来自一个虽未改善但被保留的探索结果",
        "退火算法随机游走导致的结构退化":
            "未改善结果被保留后造成的结构退化",
        "退火算法的错误接受": "一个未改善但被保留的结果",
    }
    for original, expected in cases.items():
        cleaned = normalize_generation_artifacts(original)
        assert "退火" not in cleaned
        assert expected in cleaned


def test_assistant_json_action_is_unchanged_after_reasoning_cleanup():
    response = """分析：第3步的退火接受实际上降低了模型质量。
```json
{"components":[{"role":"bulge","model":"sersic","mag":16.2}],
 "sky":{"value":null,"fix":0},
 "reasoning":"移除 Disk 是退火算法的错误接受"}
```"""
    cleaned, fallback = _assistant_target({"full_response": response})

    assert fallback is False
    assert "退火" not in cleaned
    payload = json.loads(re.search(r"```json\s*(.*?)\s*```", cleaned, re.S).group(1))
    assert payload["components"] == [
        {"role": "bulge", "model": "sersic", "mag": 16.2}
    ]
    assert payload["sky"] == {"value": None, "fix": 0}


def test_single_and_multiturn_prompts_apply_the_same_cleanup():
    legacy_history = (
        "- 第4步 采纳[modify](退火接受,质量未改善) "
        "→ chi2_nu=0.7540, BIC=140.8549"
    )
    single = build_proposal_prompt(
        RAW_SUMMARY,
        step=5,
        max_steps=15,
        num_sersic=2,
        history_summary=legacy_history,
    )
    system, turns = build_multiturn_prompts(
        RAW_SUMMARY,
        step=5,
        max_steps=15,
        num_sersic=2,
        history_summary=legacy_history,
    )
    combined = "\n".join([single, system, *turns])

    assert "/media/zhongling" not in combined
    assert "../../../gadotti_data" not in combined
    assert "退火" not in combined
    assert "该结果作为后续状态；相对上一步质量未改善" in combined
