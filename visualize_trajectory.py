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
    """node_id -> list of child nodes"""
    children = defaultdict(list)
    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None:
            children[pid].append(n)
    return children


def find_leaf_nodes(nodes: list, children_map: dict) -> list:
    """叶子 = is_accepted=True 且没有子节点的节点"""
    leaves = []
    for n in nodes:
        nid = n["node_id"]
        if n.get("is_accepted") and not children_map.get(nid):
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

    chain_html_blocks = []
    for ci, chain in enumerate(chains):
        leaf = chain[-1]
        leaf_metrics = leaf.get("metrics", {})
        leaf_chi2 = leaf_metrics.get("chi2_nu", "N/A")
        leaf_bic = leaf_metrics.get("bic", "N/A")

        steps_html = []
        for si, node in enumerate(chain):
            nid = node.get("node_id", "")
            metrics = node.get("metrics", {})
            chi2_nu = metrics.get("chi2_nu", "N/A")
            bic = metrics.get("bic", "N/A")
            delta_r = node.get("delta_R")
            action_label = get_action_label(node)

            # 图片
            cutoff_path = node.get("residual_path", "")
            full_path = guess_comparison_png(node)
            summary_path = node.get("summary_path", "")

            cutoff_b64 = image_to_base64(cutoff_path)
            full_b64 = image_to_base64(full_path)
            summary_text = read_summary_text(summary_path)

            delta_html = ""
            if delta_r is not None:
                color = "#2ecc71" if delta_r > 0 else "#e74c3c"
                delta_html = f'<span style="color:{color};font-weight:bold">ΔR={delta_r:+.4f}</span>'

            chi2_display = f"{chi2_nu:.4f}" if isinstance(chi2_nu, (int, float)) else str(chi2_nu)
            bic_display = f"{bic:.2f}" if isinstance(bic, (int, float)) else str(bic)

            cutoff_img = f'<img src="{cutoff_b64}" class="node-img" onclick="openModal(this.src)" title="Residual cutoff">' if cutoff_b64 else '<div class="img-placeholder">Cutoff 图不存在</div>'
            full_img = f'<img src="{full_b64}" class="node-img-full" onclick="openModal(this.src)" title="Full comparison">' if full_b64 else '<div class="img-placeholder">Comparison 图不存在</div>'

            summary_escaped = summary_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            step_html = f'''
            <div class="step-card" id="chain{ci}_step{si}">
                <div class="step-header">
                    <div class="step-title">
                        <span class="step-badge">Step {node.get("depth", si)}</span>
                        <span class="node-id">{nid}</span>
                        <span class="action-badge">{action_label}</span>
                    </div>
                    <div class="metrics-bar">
                        <span class="metric"><b>χ²/ν</b> = {chi2_display}</span>
                        <span class="metric"><b>BIC</b> = {bic_display}</span>
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
                </div>
            </div>
            '''
            steps_html.append(step_html)

            # 添加箭头（非最后一步）
            if si < len(chain) - 1:
                steps_html.append('<div class="arrow-down">⬇</div>')

        leaf_chi2_display = f"{leaf_chi2:.4f}" if isinstance(leaf_chi2, (int, float)) else str(leaf_chi2)
        leaf_bic_display = f"{leaf_bic:.2f}" if isinstance(leaf_bic, (int, float)) else str(leaf_bic)

        chain_block = f'''
        <div class="chain-block">
            <div class="chain-header" onclick="toggleChain('chain{ci}_body')">
                <h2>
                    🔗 链路 {ci + 1}
                    <span class="chain-summary">
                        深度 {len(chain) - 1} 步 | 叶子: {leaf.get("node_id", "")} |
                        χ²/ν = {leaf_chi2_display} | BIC = {leaf_bic_display}
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
</div>

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
        chi2 = m.get("chi2_nu", "N/A")
        bic = m.get("bic", "N/A")
        chi2_str = f"{chi2:.4f}" if isinstance(chi2, (int, float)) else str(chi2)
        bic_str = f"{bic:.2f}" if isinstance(bic, (int, float)) else str(bic)
        print(f"  链路 {i+1}: 深度={len(chain)-1}, 叶子={leaf['node_id']}, χ²/ν={chi2_str}, BIC={bic_str}")

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
