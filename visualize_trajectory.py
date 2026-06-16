"""
Trajectory Visualization Tool
==============================
读取一个星系的 trajectory.json，提取所有从根到叶子节点的链路，
生成 HTML 页面展示每条链路的 comparison 图 + summary + BIC/chi2_nu。

用法:
    python visualize_trajectory.py <trajectory.json路径>

输出:
    在 trajectory.json 同目录下生成 trajectory_viewer.html
"""

import json
import os
import sys
import base64
from collections import defaultdict
from pathlib import Path


def load_trajectory(json_path: str) -> dict:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_children_map(nodes: list) -> dict:
    """node_id -> list of all child nodes (accepted + rejected)"""
    children = defaultdict(list)
    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None:
            children[pid].append(n)
    return children


def find_leaf_nodes(nodes: list, children_map: dict) -> list:
    """叶子 = is_accepted=True 且没有 *accepted* 子节点的节点。

    注意:trajectory.json 里保存了所有 reward 评估过的节点(包括 rejected),
    rejected 子节点不算"被采纳进入下一层",所以不影响叶子判定。
    """
    leaves = []
    for n in nodes:
        if not n.get("is_accepted"):
            continue
        nid = n["node_id"]
        kids = children_map.get(nid) or []
        accepted_kids = [c for c in kids if c.get("is_accepted")]
        if not accepted_kids:
            leaves.append(n)
    return leaves


def extract_chain(node: dict, by_id: dict) -> list:
    """从叶子节点向上回溯到根，返回 root->leaf 的节点列表"""
    chain = []
    cur = node
    seen = set()
    while cur is not None:
        if cur["node_id"] in seen:
            break
        seen.add(cur["node_id"])
        chain.append(cur)
        parent_id = cur.get("parent_id")
        cur = by_id.get(parent_id) if parent_id else None
    chain.reverse()
    return chain


def _get_metric(metrics: dict, *keys, default="N/A"):
    """从 metrics 字典里按优先级取第一个非 None 的值。"""
    for k in keys:
        v = metrics.get(k)
        if v is not None:
            return v
    return default


def _fmt_metric(v, fmt=".4f"):
    if isinstance(v, (int, float)):
        return f"{v:{fmt}}"
    return str(v)


def image_to_base64(img_path: str) -> str:
    """将图片转为 base64 data URI，找不到则返回空"""
    if not img_path or not os.path.exists(img_path):
        return ""
    try:
        with open(img_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        ext = os.path.splitext(img_path)[1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext.lstrip("."), "image/png")
        return f"data:{mime};base64,{data}"
    except Exception:
        return ""


def read_summary_text(summary_path: str) -> str:
    """读取 summary markdown 内容"""
    if not summary_path or not os.path.exists(summary_path):
        return "(summary 文件不存在)"
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"(读取失败: {e})"


def read_feedme_text(feedme_path: str) -> str:
    """读取 feedme 内容"""
    if not feedme_path or not os.path.exists(feedme_path):
        return "(feedme 文件不存在)"
    try:
        with open(feedme_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"(读取失败: {e})"


def load_gadotti_params(init_feedme_path: str) -> dict:
    """Gadotti_params.json 与原始 init_feedme 同目录。找不到返回 None。"""
    if not init_feedme_path:
        return None
    source_dir = os.path.dirname(os.path.abspath(init_feedme_path))
    json_path = os.path.join(source_dir, "Gadotti_params.json")
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return {"path": json_path, "data": json.load(f)}
    except Exception as e:
        return {"path": json_path, "data": None, "error": str(e)}


def guess_comparison_png(node: dict) -> str:
    """从 residual_path (cutoff) 推断全图 comparison.png 路径"""
    rp = node.get("residual_path", "")
    if not rp:
        return ""
    # residual_path 通常是 xxx_comparison_cutoff.png，全图是 xxx_comparison.png
    if "_cutoff" in rp:
        full = rp.replace("_cutoff.png", ".png").replace("_cutoff.jpg", ".jpg")
        if os.path.exists(full):
            return full
    # 或者直接用 node_id 拼
    d = os.path.dirname(rp)
    nid = node.get("node_id", "")
    candidate = os.path.join(d, f"{nid}_comparison.png")
    if os.path.exists(candidate):
        return candidate
    return ""


def get_action_label(node: dict) -> str:
    """从 action_from_parent 提取可读动作标签"""
    action = node.get("action_from_parent")
    if not action:
        return "Root (初始化)"
    cl = action.get("coarse_label", "")
    if cl:
        return cl
    s = action.get("structural", "")
    if s:
        return s
    t = action.get("type", "?")
    return f"Action {t}"


def generate_html(trajectory: dict, chains: list, json_path: str) -> str:
    galaxy_id = trajectory.get("galaxy_id", "unknown")
    total_nodes = len(trajectory.get("nodes", []))
    accepted_nodes = sum(1 for n in trajectory.get("nodes", []) if n.get("is_accepted"))
    rejected_nodes = total_nodes - accepted_nodes

    # 加载 Gadotti 专家真值(与 init_feedme 同目录)
    gadotti = load_gadotti_params(trajectory.get("init_feedme", ""))
    if gadotti is not None:
        if gadotti.get("data") is not None:
            gad_pretty = json.dumps(gadotti["data"], indent=2, ensure_ascii=False)
            gad_pretty_escaped = gad_pretty.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            gadotti_html = f'''
    <details class="gadotti-block" open>
        <summary>🎯 专家真值 (Gadotti_params.json) — <code>{gadotti["path"]}</code></summary>
        <pre class="summary-pre">{gad_pretty_escaped}</pre>
    </details>
    '''
        else:
            gadotti_html = f'<div class="gadotti-block">⚠️ Gadotti_params.json 读取失败 ({gadotti.get("error","")}): {gadotti["path"]}</div>'
    else:
        gadotti_html = '<div class="gadotti-block" style="color:#888">ℹ️ 该星系无 Gadotti_params.json (init_feedme 同目录未找到)</div>'

    chain_html_blocks = []
    available_metric_keys = set()
    for n in trajectory.get("nodes", []):
        available_metric_keys.update((n.get("metrics") or {}).keys())

    # 预构建 parent_id → 所有 children(accepted+rejected),用于查找拒绝兄弟
    all_children = defaultdict(list)
    for n in trajectory.get("nodes", []):
        pid = n.get("parent_id")
        if pid is not None:
            all_children[pid].append(n)

    def _metric_delta_html(start, end, fmt=".4f", lower_is_better=True):
        """格式化 start → end (delta%) 进度,lower_is_better 决定 ↓/↑ 哪个是绿色"""
        if not (isinstance(start, (int, float)) and isinstance(end, (int, float))):
            return f"{_fmt_metric(start, fmt)} → {_fmt_metric(end, fmt)}"
        delta = end - start
        pct = (delta / start * 100) if start != 0 else 0
        if abs(delta) < 1e-12:
            arrow, color = "→", "#aaa"
        elif (delta < 0) == lower_is_better:
            arrow, color = "↓" if delta < 0 else "↑", "#2ecc71"  # 改进 = 绿
        else:
            arrow, color = "↓" if delta < 0 else "↑", "#e74c3c"  # 变差 = 红
        return f'{start:{fmt}} → {end:{fmt}} <span style="color:{color};font-weight:bold">({arrow}{abs(pct):.1f}%)</span>'

    def _render_rejected_sibling(sib: dict) -> str:
        """渲染单个被拒绝兄弟节点,重点展示 VLM reward 打标细节"""
        nid = sib.get("node_id", "")
        sm = sib.get("metrics", {})
        s_chi2 = _get_metric(sm, "chi2_nu", "chisq_nu", "chisq1d_nu")
        s_bic = _get_metric(sm, "bic", "bic1d", "BIC")
        s_dr = sib.get("delta_R")
        s_action = get_action_label(sib)

        # 缩略残差图
        sib_cutoff = sib.get("residual_path", "")
        sib_b64 = image_to_base64(sib_cutoff)
        thumb = f'<img src="{sib_b64}" class="sib-thumb" onclick="openModal(this.src)">' if sib_b64 else '<div class="img-placeholder" style="width:140px;height:120px">无图</div>'

        # VLM reward 详情(假阳假阴的关键证据)
        rd = sib.get("reward_detail") or {}
        vd = rd.get("vlm_detail") or {}
        vlm_html = ""
        if vd:
            orig_imp = vd.get("original_improvement", "—")
            final_imp = vd.get("final_improvement", vd.get("improvement", "—"))
            imp_level = vd.get("residual_improvement_level") or vd.get("improvement_level", "—")
            conf = vd.get("confidence", "—")
            src = vd.get("improvement_source", "—")
            param_ok = vd.get("param_plausible", "—")
            metric_ok = vd.get("metric_consistent", "—")
            hard_warn = vd.get("hard_warnings", "—")
            reason = vd.get("reason", "")
            reason_esc = (reason if isinstance(reason, str) else str(reason)).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            vlm_html = f'''
            <div class="vlm-detail">
                <table class="vlm-table">
                    <tr><td>original_improvement</td><td><b>{orig_imp}</b></td>
                        <td>final_improvement</td><td><b>{final_imp}</b></td></tr>
                    <tr><td>improvement_level</td><td>{imp_level}</td>
                        <td>improvement_source</td><td>{src}</td></tr>
                    <tr><td>confidence</td><td>{conf}</td>
                        <td>param_plausible</td><td>{param_ok}</td></tr>
                    <tr><td>metric_consistent</td><td>{metric_ok}</td>
                        <td>hard_warnings</td><td>{hard_warn}</td></tr>
                </table>
                <div class="vlm-reason"><b>reason:</b> {reason_esc}</div>
            </div>
            '''

        chi2_s = _fmt_metric(s_chi2, ".4f")
        bic_s = _fmt_metric(s_bic, ".2f")
        dr_s = f"{s_dr:+.4f}" if isinstance(s_dr, (int, float)) else "—"
        dr_color = "#e74c3c" if isinstance(s_dr, (int, float)) and s_dr <= 0 else "#aaa"

        return f'''
        <div class="rejected-sib">
            <div class="rejected-sib-left">{thumb}</div>
            <div class="rejected-sib-right">
                <div class="sib-header">
                    <span class="node-id">{nid}</span>
                    <span class="action-badge" style="background:#888">{s_action}</span>
                    <span class="metric"><b>χ²/ν</b>={chi2_s}</span>
                    <span class="metric"><b>BIC</b>={bic_s}</span>
                    <span style="color:{dr_color};font-weight:bold">ΔR={dr_s}</span>
                </div>
                {vlm_html}
            </div>
        </div>
        '''

    for ci, chain in enumerate(chains):
        leaf = chain[-1]
        leaf_metrics = leaf.get("metrics", {})
        leaf_chi2 = _get_metric(leaf_metrics, "chi2_nu", "chisq_nu", "chisq1d_nu")
        leaf_bic = _get_metric(leaf_metrics, "bic", "bic1d", "BIC")

        # 链路起点(root)的 chi2 / bic,用于算 root→leaf 进度
        root_metrics = chain[0].get("metrics", {})
        root_chi2 = _get_metric(root_metrics, "chi2_nu", "chisq_nu", "chisq1d_nu")
        root_bic = _get_metric(root_metrics, "bic", "bic1d", "BIC")
        chi2_progress = _metric_delta_html(root_chi2, leaf_chi2, ".4f", lower_is_better=True)
        bic_progress = _metric_delta_html(root_bic, leaf_bic, ".2f", lower_is_better=True)

        steps_html = []
        for si, node in enumerate(chain):
            is_leaf = (si == len(chain) - 1)
            nid = node.get("node_id", "")
            metrics = node.get("metrics", {})
            chi2_nu = _get_metric(metrics, "chi2_nu", "chisq_nu", "chisq1d_nu")
            bic = _get_metric(metrics, "bic", "bic1d", "BIC")
            chisq1d_nu = _get_metric(metrics, "chisq1d_nu")
            delta_r = node.get("delta_R")
            action_label = get_action_label(node)

            # 图片
            cutoff_path = node.get("residual_path", "")
            full_path = guess_comparison_png(node)
            summary_path = node.get("summary_path", "")

            cutoff_b64 = image_to_base64(cutoff_path)
            full_b64 = image_to_base64(full_path)
            summary_text = read_summary_text(summary_path)
            feedme_text = read_feedme_text(node.get("feedme_path", ""))

            delta_html = ""
            if delta_r is not None:
                color = "#2ecc71" if delta_r > 0 else "#e74c3c"
                delta_html = f'<span style="color:{color};font-weight:bold">ΔR={delta_r:+.4f}</span>'

            chi2_display = _fmt_metric(chi2_nu, ".4f")
            bic_display = _fmt_metric(bic, ".2f")
            chisq1d_display = _fmt_metric(chisq1d_nu, ".4f")
            chisq1d_html = f' <span class="metric"><b>χ²₁D/ν</b> = {chisq1d_display}</span>' if chisq1d_nu != "N/A" else ""

            cutoff_img = f'<img src="{cutoff_b64}" class="node-img" onclick="openModal(this.src)" title="Residual cutoff">' if cutoff_b64 else '<div class="img-placeholder">Cutoff 图不存在</div>'
            full_img = f'<img src="{full_b64}" class="node-img-full" onclick="openModal(this.src)" title="Full comparison">' if full_b64 else '<div class="img-placeholder">Comparison 图不存在</div>'

            summary_escaped = summary_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            feedme_escaped = feedme_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            # 找该节点的被拒绝兄弟(同 parent_id, is_accepted=False)
            parent_id = node.get("parent_id")
            rejected_sibs = []
            if parent_id:
                rejected_sibs = [
                    s for s in all_children.get(parent_id, [])
                    if not s.get("is_accepted") and s.get("node_id") != nid
                ]
            rej_html = ""
            if rejected_sibs:
                sib_blocks = "".join(_render_rejected_sibling(s) for s in rejected_sibs)
                rej_html = f'''
                    <details class="summary-details">
                        <summary>🚫 被 VLM reward 拒绝的兄弟 ({len(rejected_sibs)} 个) — 看假阳假阴</summary>
                        <div class="rejected-list">{sib_blocks}</div>
                    </details>
                '''

            leaf_class = " leaf-step" if is_leaf else ""
            leaf_badge = ' <span class="leaf-badge">🏁 叶子</span>' if is_leaf else ''

            step_html = f'''
            <div class="step-card{leaf_class}" id="chain{ci}_step{si}">
                <div class="step-header">
                    <div class="step-title">
                        <span class="step-badge">Step {node.get("depth", si)}</span>
                        <span class="node-id">{nid}</span>
                        <span class="action-badge">{action_label}</span>{leaf_badge}
                    </div>
                    <div class="metrics-bar">
                        <span class="metric"><b>χ²/ν</b> = {chi2_display}</span>
                        <span class="metric"><b>BIC</b> = {bic_display}</span>{chisq1d_html}
                        {delta_html}
                    </div>
                </div>
                <div class="step-body">
                    <div class="images-row">
                        <div class="img-container">
                            <div class="img-label">Residual Cutoff</div>
                            {cutoff_img}
                        </div>
                        <div class="img-container">
                            <div class="img-label">Full Comparison (Original | Model | Residual | 1D SB)</div>
                            {full_img}
                        </div>
                    </div>
                    <details class="summary-details">
                        <summary>📄 Summary Markdown</summary>
                        <pre class="summary-pre">{summary_escaped}</pre>
                    </details>
                    <details class="summary-details">
                        <summary>📐 Feedme</summary>
                        <pre class="summary-pre">{feedme_escaped}</pre>
                    </details>
                    {rej_html}
                </div>
            </div>
            '''
            steps_html.append(step_html)

            # 添加箭头（非最后一步）
            if si < len(chain) - 1:
                steps_html.append('<div class="arrow-down">⬇</div>')

        leaf_chi2_display = _fmt_metric(leaf_chi2, ".4f")
        leaf_bic_display = _fmt_metric(leaf_bic, ".2f")

        chain_block = f'''
        <div class="chain-block">
            <div class="chain-header" onclick="toggleChain('chain{ci}_body')">
                <h2>
                    🔗 链路 {ci + 1}
                    <span class="chain-summary">
                        深度 {len(chain) - 1} 步 | 叶子: {leaf.get("node_id", "")}<br>
                        χ²/ν: {chi2_progress}<br>
                        BIC: {bic_progress}
                    </span>
                </h2>
                <span class="toggle-icon" id="chain{ci}_icon">▼</span>
            </div>
            <div class="chain-body" id="chain{ci}_body">
                {"".join(steps_html)}
            </div>
        </div>
        '''
        chain_html_blocks.append(chain_block)

    # 排序链路：按叶子 chi2_nu 升序
    # (已经在 main 里排好了)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trajectory Viewer — {galaxy_id}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif;
    background: #0f0f23;
    color: #e0e0e0;
    padding: 20px;
    line-height: 1.5;
}}
h1 {{ color: #ffd700; margin-bottom: 10px; }}
.global-stats {{
    background: #1a1a3e;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 16px 24px;
    margin-bottom: 24px;
    display: flex;
    gap: 32px;
    flex-wrap: wrap;
}}
.stat-item {{ font-size: 15px; }}
.stat-item b {{ color: #ffd700; }}
.gadotti-block {{
    background: #1a1a3e;
    border: 1px solid #ffd700;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 20px;
}}
.gadotti-block > summary {{
    cursor: pointer;
    color: #ffd700;
    font-weight: bold;
    font-size: 15px;
}}
.gadotti-block code {{
    color: #aaa;
    font-size: 12px;
    font-weight: normal;
}}
.chain-block {{
    background: #1a1a3e;
    border: 1px solid #444;
    border-radius: 10px;
    margin-bottom: 20px;
    overflow: hidden;
}}
.chain-header {{
    background: #252550;
    padding: 14px 20px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    user-select: none;
}}
.chain-header:hover {{ background: #2e2e60; }}
.chain-header h2 {{ font-size: 18px; color: #ffd700; }}
.chain-summary {{ font-size: 14px; color: #aaa; margin-left: 16px; font-weight: normal; }}
.toggle-icon {{ font-size: 18px; color: #888; transition: transform 0.2s; }}
.chain-body {{ padding: 16px; }}
.step-card {{
    background: #222244;
    border: 1px solid #444;
    border-radius: 8px;
    margin-bottom: 8px;
    overflow: hidden;
}}
.step-card.leaf-step {{
    border: 2px solid #ffd700;
    box-shadow: 0 0 14px rgba(255, 215, 0, 0.3);
}}
.leaf-badge {{
    background: #ffd700;
    color: #111;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: bold;
    margin-left: 6px;
}}
.rejected-list {{
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 8px;
}}
.rejected-sib {{
    display: flex;
    gap: 12px;
    padding: 10px;
    background: #1a1a3e;
    border: 1px dashed #e74c3c;
    border-radius: 6px;
}}
.rejected-sib-left {{ flex: 0 0 auto; }}
.rejected-sib-right {{ flex: 1; }}
.sib-thumb {{
    max-height: 130px;
    max-width: 180px;
    border-radius: 4px;
    border: 1px solid #555;
    cursor: zoom-in;
}}
.sib-header {{
    display: flex;
    gap: 14px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 8px;
    font-size: 13px;
}}
.vlm-detail {{ font-size: 12px; }}
.vlm-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 6px;
}}
.vlm-table td {{
    padding: 3px 8px;
    border: 1px solid #333;
    color: #ccc;
}}
.vlm-table td:nth-child(odd) {{ color: #888; width: 22%; }}
.vlm-reason {{
    background: #111;
    padding: 8px 10px;
    border-radius: 4px;
    color: #ccc;
    border-left: 3px solid #4a90d9;
    margin-top: 4px;
    line-height: 1.5;
}}
.step-header {{
    background: #2a2a55;
    padding: 10px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
}}
.step-title {{ display: flex; align-items: center; gap: 10px; }}
.step-badge {{
    background: #ffd700;
    color: #111;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 13px;
    font-weight: bold;
}}
.node-id {{ color: #aaa; font-size: 13px; font-family: monospace; }}
.action-badge {{
    background: #4a90d9;
    color: #fff;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 13px;
}}
.metrics-bar {{ display: flex; gap: 18px; align-items: center; }}
.metric {{ font-size: 14px; color: #ccc; }}
.metric b {{ color: #ffd700; }}
.step-body {{ padding: 12px 16px; }}
.images-row {{
    display: flex;
    gap: 16px;
    margin-bottom: 10px;
    flex-wrap: wrap;
}}
.img-container {{ text-align: center; }}
.img-label {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
.node-img {{
    max-height: 220px;
    border-radius: 6px;
    border: 1px solid #555;
    cursor: zoom-in;
}}
.node-img-full {{
    max-height: 220px;
    border-radius: 6px;
    border: 1px solid #555;
    cursor: zoom-in;
}}
.img-placeholder {{
    width: 200px; height: 150px;
    background: #333;
    border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    color: #666; font-size: 13px;
}}
.arrow-down {{
    text-align: center;
    font-size: 22px;
    color: #666;
    margin: 4px 0;
}}
.summary-details {{ margin-top: 6px; }}
.summary-details summary {{
    cursor: pointer;
    color: #aaa;
    font-size: 13px;
}}
.summary-details summary:hover {{ color: #ffd700; }}
.summary-pre {{
    background: #111;
    color: #ccc;
    padding: 12px;
    border-radius: 6px;
    max-height: 400px;
    overflow-y: auto;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
    margin-top: 6px;
}}
/* Modal for zoomed images */
.modal-overlay {{
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.85);
    z-index: 1000;
    cursor: zoom-out;
    justify-content: center;
    align-items: center;
}}
.modal-overlay.active {{ display: flex; }}
.modal-overlay img {{
    max-width: 95vw;
    max-height: 95vh;
    border-radius: 8px;
}}
</style>
</head>
<body>

<h1>🔭 {galaxy_id} — Trajectory Viewer</h1>

<div class="global-stats">
    <div class="stat-item"><b>总节点数:</b> {total_nodes}</div>
    <div class="stat-item"><b>Accepted:</b> {accepted_nodes}</div>
    <div class="stat-item"><b>Rejected:</b> {rejected_nodes}</div>
    <div class="stat-item"><b>链路数 (叶子):</b> {len(chains)}</div>
    <div class="stat-item"><b>来源:</b> {os.path.basename(json_path)}</div>
    <div class="stat-item" style="color:#aaa"><b>可用 metric 字段:</b> {", ".join(sorted(available_metric_keys)) or "(无)"}</div>
</div>

{gadotti_html}

{"".join(chain_html_blocks)}

<div class="modal-overlay" id="imgModal" onclick="closeModal()">
    <img id="modalImg" src="" alt="zoomed">
</div>

<script>
function toggleChain(bodyId) {{
    const body = document.getElementById(bodyId);
    const icon = document.getElementById(bodyId.replace('_body','_icon'));
    if (body.style.display === 'none') {{
        body.style.display = 'block';
        if (icon) icon.textContent = '▼';
    }} else {{
        body.style.display = 'none';
        if (icon) icon.textContent = '▶';
    }}
}}
function openModal(src) {{
    const modal = document.getElementById('imgModal');
    document.getElementById('modalImg').src = src;
    modal.classList.add('active');
}}
function closeModal() {{
    document.getElementById('imgModal').classList.remove('active');
}}
document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') closeModal();
}});
</script>

</body>
</html>'''
    return html


def main():
    if len(sys.argv) < 2:
        print("用法: python visualize_trajectory.py <trajectory.json路径>")
        print("例如: python visualize_trajectory.py output/vlm_proposal_.../SDSS_gband_Plate0282_.../SDSS_gband_Plate0282_..._trajectory.json")
        sys.exit(1)

    json_path = sys.argv[1]
    if not os.path.exists(json_path):
        print(f"错误: 文件不存在 — {json_path}")
        sys.exit(1)

    print(f"📖 读取 trajectory: {json_path}")
    trajectory = load_trajectory(json_path)

    nodes = trajectory.get("nodes", [])
    if not nodes:
        print("错误: trajectory 中没有节点")
        sys.exit(1)

    by_id = {n["node_id"]: n for n in nodes}
    children_map = build_children_map(nodes)

    # 打印第一个有 metrics 的节点的字段,帮 debug BIC 缺失等问题
    sample_metrics = next((n.get("metrics") for n in nodes if n.get("metrics")), None)
    if sample_metrics:
        print(f"🔬 样例 metrics 字段: {list(sample_metrics.keys())}")
        print(f"   样例值: {sample_metrics}")

    leaves = find_leaf_nodes(nodes, children_map)
    print(f"📊 总节点: {len(nodes)} | 叶子节点: {len(leaves)}")

    if not leaves:
        print("⚠️ 没有叶子节点（所有 accepted 节点都有子节点?），降级: 取最深层 accepted 节点")
        max_depth = max(n.get("depth", 0) for n in nodes if n.get("is_accepted"))
        leaves = [n for n in nodes if n.get("is_accepted") and n.get("depth") == max_depth]

    chains = [extract_chain(leaf, by_id) for leaf in leaves]

    # 按叶子 chi2_nu 升序排列（最好的链路在前）
    def chain_sort_key(chain):
        leaf_chi2 = chain[-1].get("metrics", {}).get("chi2_nu", 999.0)
        return leaf_chi2 if isinstance(leaf_chi2, (int, float)) else 999.0

    chains.sort(key=chain_sort_key)

    print(f"🔗 提取链路: {len(chains)} 条")
    for i, chain in enumerate(chains):
        leaf = chain[-1]
        m = leaf.get("metrics", {})
        chi2 = _get_metric(m, "chi2_nu", "chisq_nu", "chisq1d_nu")
        bic = _get_metric(m, "bic", "bic1d", "BIC")
        print(f"  链路 {i+1}: 深度={len(chain)-1}, 叶子={leaf['node_id']}, χ²/ν={_fmt_metric(chi2)}, BIC={_fmt_metric(bic, '.2f')}")

    print("🎨 生成 HTML...")
    html = generate_html(trajectory, chains, json_path)

    out_dir = os.path.dirname(os.path.abspath(json_path))
    out_html = os.path.join(out_dir, "trajectory_viewer.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ 已生成: {out_html}")
    print(f"   在浏览器中打开即可查看。")


if __name__ == "__main__":
    main()
