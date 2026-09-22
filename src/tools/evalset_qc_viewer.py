"""Read-only local web viewer for component-analysis evalset QC samples.

Serves the FULL_REVIEW_REQUIRED run's a2-qc-samples/action-*.md as a single
filterable page, joining each sample_id to its adjudication, candidate and
state-manifest record, and mapping /media/data read-only for comparison PNGs.
See docs/component-analysis/evaluation-set-qc-viewer.md.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MEDIA_ROOTS = (
    Path("/media/data/galfit_run_history").resolve(),
    Path("/media/data/galfits_run_history").resolve(),
)

DECISION_MD_PATTERN = re.compile(r"_component_analysis_[0-9a-f\-]+\.md$")
DECISION_HEADING = "本次调整决策"
DECISION_EXCERPT_LIMIT = 1500


def parse_action_md(path: Path) -> list[dict]:
    """Parse one action-*.md QC table into row dicts keyed by sample_id."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6 or cells[0] in {"sample_id", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(
            {
                "sample_id": cells[0],
                "verdict": cells[1],
                "conf": cells[2],
                "pool": cells[3],
                "action": cells[4],
                "reason_codes": [c for c in cells[5].split(",") if c],
            }
        )
    return rows


def load_jsonl_index(path: Path) -> dict[str, dict]:
    index = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            index[record["sample_id"]] = record
    return index


def extract_decision_excerpt(path: Path) -> str:
    """Slice the decision section from a single-band component-analysis markdown."""
    text = path.read_text(encoding="utf-8", errors="replace")
    anchor = text.find(DECISION_HEADING)
    if anchor < 0:
        return text[:DECISION_EXCERPT_LIMIT].strip()
    excerpt = text[anchor:].strip()
    return excerpt[:DECISION_EXCERPT_LIMIT].strip()


def post_round_comparison(candidate: dict) -> str | None:
    """Display-only helper: locate the post-action round's comparison PNG.

    Uses the post-round directory recorded in candidate evidence_refs; the
    state round always comes from the manifest's explicit role=comparison ref.
    """
    refs = candidate.get("evidence_refs") or []
    if len(refs) < 2:
        return None
    post_dir = Path(refs[-1]).parent
    if not post_dir.is_dir():
        return None
    for name in sorted(post_dir.iterdir()):
        if name.suffix.lower() == ".png" and "comparison" in name.name:
            return str(name)
    return None


def build_samples(run_dir: Path, action_files: list[Path]) -> list[dict]:
    adjudications = load_jsonl_index(run_dir / "evaluation-action-adjudications.jsonl")
    candidates = load_jsonl_index(run_dir / "evaluation-decision-candidates.jsonl")
    manifests_dir = run_dir / "evaluation-state-manifests"

    samples = []
    for action_file in action_files:
        for row in parse_action_md(action_file):
            sample_id = row["sample_id"]
            adjudication = adjudications.get(sample_id)
            candidate = candidates.get(sample_id)
            manifest_path = manifests_dir / f"{sample_id}.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None

            state_refs = []
            state_png = None
            if manifest:
                for ref in manifest.get("source_refs", []):
                    state_refs.append({"role": ref.get("role", "other"), "path": ref.get("path", "")})
                    if ref.get("role") == "comparison" and ref.get("path", "").endswith(".png"):
                        state_png = ref["path"]

            decision_path = None
            decision_excerpt = None
            if adjudication:
                for ref in adjudication.get("evidence_refs", []):
                    if DECISION_MD_PATTERN.search(ref) and Path(ref).is_file():
                        decision_path = ref
                        decision_excerpt = extract_decision_excerpt(Path(ref))
                        break

            health = (candidate or {}).get("current_fit_health", {})
            samples.append(
                {
                    "source_file": action_file.name,
                    "sample_id": sample_id,
                    "mode": (candidate or {}).get("mode"),
                    "object_id": (candidate or {}).get("object_id"),
                    "verdict": (adjudication or {}).get("action_verdict", row["verdict"]),
                    "conf": (adjudication or {}).get("confidence", row["conf"]),
                    "pool": (adjudication or {}).get("evaluation_pool", row["pool"]),
                    "action": row["action"],
                    "reason_codes": (adjudication or {}).get("verdict_reason_codes", row["reason_codes"]),
                    "adjudicator_note": (adjudication or {}).get("adjudicator_note"),
                    "canonical_action": (candidate or {}).get("canonical_action"),
                    "source_components": (candidate or {}).get("source_components"),
                    "expert_final_components": (candidate or {}).get("expert_final_components"),
                    "state_round_id": (candidate or {}).get("state_round_id"),
                    "post_action_round_id": (candidate or {}).get("post_action_round_id"),
                    "reduced_chisq": health.get("reduced_chisq"),
                    "bic": health.get("bic"),
                    "state_refs": state_refs,
                    "state_png": state_png,
                    "post_png": post_round_comparison(candidate or {}),
                    "decision_path": decision_path,
                    "decision_excerpt": decision_excerpt,
                    "joined": {"adjudication": adjudication is not None, "candidate": candidate is not None, "manifest": manifest is not None},
                }
            )
    return samples


PAGE_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>evalset QC viewer</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --line:#d9dee5; --muted:#6b7684; }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.5 -apple-system,"Segoe UI",Roboto,"Noto Sans SC",sans-serif; color:var(--ink); background:var(--bg); }
header { padding:10px 16px; background:#16233a; color:#fff; position:sticky; top:0; z-index:5; }
header h1 { font-size:16px; margin:0 0 8px; }
.tabs button { background:transparent; border:1px solid #4a5a75; color:#cfd8e6; padding:3px 10px; margin-right:6px; border-radius:4px; cursor:pointer; }
.tabs button.active { background:#fff; color:#16233a; border-color:#fff; }
.controls { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; align-items:center; }
.controls select, .controls input { padding:3px 6px; border-radius:4px; border:1px solid var(--line); }
#count { color:#aebad0; margin-left:auto; }
main { padding:12px 16px; }
table { border-collapse:collapse; width:100%; background:var(--card); }
th, td { border:1px solid var(--line); padding:5px 8px; text-align:left; vertical-align:top; }
th { background:#eef1f5; position:sticky; top:96px; cursor:pointer; user-select:none; white-space:nowrap; }
tr.row { cursor:pointer; }
tr.row:hover { background:#f0f4fa; }
.sid { font-family:ui-monospace,monospace; font-size:12px; }
.badge { display:inline-block; padding:0 6px; border-radius:9px; font-size:12px; margin:1px 2px; border:1px solid var(--line); background:#f2f4f7; }
.badge.CORRECT { background:#e2f4e6; border-color:#9fd4ac; }
.badge.EXPLORATORY { background:#fdf3dd; border-color:#e6c97a; }
.badge.HARMFUL { background:#fbe4e4; border-color:#e2a2a2; }
.badge.INCONCLUSIVE { background:#ece9f7; border-color:#b7add9; }
.badge.benchmark { background:#dbeafc; border-color:#9cc3f5; }
.rc { font-size:11px; font-family:ui-monospace,monospace; }
#detail { position:fixed; top:0; right:-70%; width:66%; height:100%; background:var(--card); box-shadow:-4px 0 18px rgba(0,0,0,.25); transition:right .2s; padding:16px 20px; overflow-y:auto; z-index:10; }
#detail.open { right:0; }
#detail h2 { font-size:14px; margin-top:0; word-break:break-all; }
#detail .close { float:right; cursor:pointer; font-size:18px; color:var(--muted); }
#detail img { max-width:49%; border:1px solid var(--line); margin:4px 0 8px 8px; }
#detail pre { background:#f2f4f7; padding:8px; overflow-x:auto; font-size:12px; white-space:pre-wrap; word-break:break-all; }
#detail section { margin-bottom:14px; }
#detail h3 { font-size:13px; margin:0 0 6px; color:var(--muted); }
.pathlist { margin:0; padding-left:18px; }
.pathlist a { font-family:ui-monospace,monospace; font-size:12px; word-break:break-all; }
.muted { color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>evalset QC viewer</h1>
  <div class="tabs" id="tabs"></div>
  <div class="controls">
    <select id="f-verdict"></select>
    <select id="f-pool"></select>
    <select id="f-conf"></select>
    <select id="f-mode"></select>
    <input id="f-search" placeholder="sample_id / object 搜索" size="24">
    <span id="count"></span>
  </div>
</header>
<main><table id="tbl">
  <thead><tr>
    <th data-k="sample_id">sample_id</th><th data-k="verdict">verdict</th><th data-k="conf">conf</th>
    <th data-k="pool">pool</th><th data-k="action">action</th><th>关键 reason codes</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table></main>
<div id="detail"><span class="close" onclick="closeDetail()">&times;</span><div id="detail-body"></div></div>
<script>
const DATA = __DATA__;
const runDir = "__RUN_DIR__";
let tab = DATA.tabs[0], sortKey = null, sortAsc = true;

function uniq(key) {
  const s = new Set();
  DATA.samples.forEach(x => { if (x[key] != null) s.add(x[key]); });
  return [...s].sort();
}
function fillSelect(id, values, label) {
  const el = document.getElementById(id);
  el.innerHTML = `<option value="">${label}: 全部</option>` + values.map(v => `<option>${v}</option>`).join("");
}
function initControls() {
  document.getElementById("tabs").innerHTML = DATA.tabs.map(
    t => `<button data-t="${t}" class="${t === tab ? "active" : ""}">${t}</button>`).join("");
  document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
    tab = b.dataset.t; initControls(); render();
  });
  fillSelect("f-verdict", uniq("verdict"), "verdict");
  fillSelect("f-pool", uniq("pool"), "pool");
  fillSelect("f-conf", uniq("conf"), "conf");
  fillSelect("f-mode", uniq("mode"), "mode");
  ["f-verdict", "f-pool", "f-conf", "f-mode"].forEach(id =>
    document.getElementById(id).onchange = render);
  document.getElementById("f-search").oninput = render;
  document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
    if (sortKey === th.dataset.k) sortAsc = !sortAsc; else { sortKey = th.dataset.k; sortAsc = true; }
    render();
  });
}
function filtered() {
  const v = document.getElementById("f-verdict").value;
  const p = document.getElementById("f-pool").value;
  const c = document.getElementById("f-conf").value;
  const m = document.getElementById("f-mode").value;
  const q = document.getElementById("f-search").value.trim().toLowerCase();
  let rows = DATA.samples.filter(x => x.source_file === tab
    && (!v || x.verdict === v) && (!p || x.pool === p)
    && (!c || x.conf === c) && (!m || x.mode === m)
    && (!q || x.sample_id.toLowerCase().includes(q) || (x.object_id || "").toLowerCase().includes(q)));
  if (sortKey) rows.sort((a, b) => String(a[sortKey] ?? "").localeCompare(String(b[sortKey] ?? "")) * (sortAsc ? 1 : -1));
  return rows;
}
function badge(v) { return v ? `<span class="badge ${v}">${v}</span>` : ""; }
function actionLabel(s) {
  const ca = s.canonical_action;
  if (!ca) return s.action;
  if (ca.action_type === "COMPOUND" && ca.atomic_actions)
    return ca.atomic_actions.map(a => a.action_type + (a.component ? `(${a.component})` : "")
      + (a.replace_from ? ` ${a.replace_from}->${a.replace_to}` : "")).join(" + ");
  return ca.action_type + (ca.component ? `(${ca.component})` : "")
    + (ca.replace_from ? ` ${ca.replace_from}->${ca.replace_to}` : "");
}
function render() {
  const rows = filtered();
  document.getElementById("count").textContent = `${rows.length} / ${DATA.samples.filter(x => x.source_file === tab).length} 条`;
  document.getElementById("tbody").innerHTML = rows.map((s, i) => `
    <tr class="row" data-i="${DATA.samples.indexOf(s)}">
      <td class="sid">${s.sample_id}</td>
      <td>${badge(s.verdict)}</td>
      <td>${s.conf ?? ""}</td>
      <td>${badge(s.pool)}</td>
      <td class="sid">${actionLabel(s)}</td>
      <td>${(s.reason_codes || []).map(r => `<span class="badge rc">${r}</span>`).join(" ")}</td>
    </tr>`).join("");
  document.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => showDetail(DATA.samples[+tr.dataset.i]));
}
function mediaUrl(p) {
  const prefix = "/media" + "/" + "data" + "/";
  return p ? prefix + (p.startsWith(prefix) ? p.slice(prefix.length) : p) : null;
}
function roleGroup(refs) {
  const by = {};
  (refs || []).forEach(r => { (by[r.role] = by[r.role] || []).push(r.path); });
  return Object.entries(by).map(([role, paths]) =>
    `<b>${role}</b><ul class="pathlist">` + paths.map(p =>
      `<li><a href="${mediaUrl(p)}" target="_blank">${p}</a></li>`).join("") + "</ul>").join("");
}
function showDetail(s) {
  const parts = [];
  parts.push(`<h2>${s.sample_id}</h2>
    <div>${badge(s.verdict)} ${badge(s.pool)} conf=${s.conf ?? "-"}
    <span class="muted">${s.mode ?? "-"} · ${s.object_id ?? "-"}</span></div>`);
  let imgs = "";
  if (s.state_png) imgs += `<img src="${mediaUrl(s.state_png)}" title="state 轮">`;
  if (s.post_png) imgs += `<img src="${mediaUrl(s.post_png)}" title="post 轮">`;
  if (imgs) parts.push(`<section><h3>comparison（左 state 轮 / 右 post 轮）</h3>${imgs}</section>`);
  parts.push(`<section><h3>canonical action</h3><pre>${JSON.stringify(s.canonical_action, null, 1)}</pre>
    <div>source: <code>${(s.source_components || []).join(", ") || "-"}</code> → expert: <code>${(s.expert_final_components || []).join(", ") || "-"}</code></div>
    <div>chi2/nu=${s.reduced_chisq ?? "-"} · BIC=${s.bic ?? "-"} · state=${s.state_round_id ?? "-"} · post=${s.post_action_round_id ?? "-"}</div></section>`);
  if (s.decision_path || s.adjudicator_note)
    parts.push(`<section><h3>关键决策内容</h3>
      ${s.decision_path ? `<div><a href="${mediaUrl(s.decision_path)}" target="_blank">${s.decision_path}</a></div>` : ""}
      ${s.decision_excerpt ? `<pre>${s.decision_excerpt}</pre>` : ""}
      ${s.adjudicator_note ? `<div><b>adjudicator_note:</b> ${s.adjudicator_note}</div>` : ""}</section>`);
  parts.push(`<section><h3>输入文件（state 轮 manifest）</h3>${roleGroup(s.state_refs) || '<span class="muted">manifest 缺失</span>'}</section>`);
  parts.push(`<section class="muted">run: ${runDir} · joined: ${JSON.stringify(s.joined)}</section>`);
  document.getElementById("detail-body").innerHTML = parts.join("");
  document.getElementById("detail").classList.add("open");
}
function closeDetail() { document.getElementById("detail").classList.remove("open"); }
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDetail(); });
initControls();
render();
</script>
</body>
</html>
"""


class ViewerState:
    """Holds the built page; rebuilds when watched inputs change on disk."""

    def __init__(self, run_dir: Path, action_files: list[Path]):
        self.run_dir = run_dir
        self.action_files = action_files
        self._fingerprint: tuple | None = None
        self.samples: list[dict] = []
        self.page_html = ""
        self.refresh(force=True)

    def _current_fingerprint(self) -> tuple:
        watched = [
            self.run_dir / "evaluation-action-adjudications.jsonl",
            self.run_dir / "evaluation-decision-candidates.jsonl",
            *self.action_files,
        ]
        return tuple((str(p), p.stat().st_mtime_ns) for p in watched)

    def refresh(self, force: bool = False) -> bool:
        """Rebuild the page if any watched file mtime changed; returns True on rebuild."""
        fingerprint = self._current_fingerprint()
        if not force and fingerprint == self._fingerprint:
            return False
        samples = build_samples(self.run_dir, self.action_files)
        page = PAGE_TEMPLATE.replace("__DATA__", json.dumps(
            {"tabs": [p.name for p in self.action_files], "samples": samples}, ensure_ascii=False))
        page = page.replace("__RUN_DIR__", html.escape(str(self.run_dir.resolve())))
        self.samples = samples
        self.page_html = page
        self._fingerprint = fingerprint
        print(f"reloaded page: {len(samples)} samples from {len(self.action_files)} action files", flush=True)
        return True


class ViewerHandler(BaseHTTPRequestHandler):
    state: ViewerState

    def do_HEAD(self):
        self._route(head_only=True)

    def do_GET(self):
        self._route(head_only=False)

    def _route(self, head_only: bool) -> None:
        if self.path in ("/", "/index.html"):
            self.state.refresh()
            self._send_bytes(self.state.page_html.encode("utf-8"), "text/html; charset=utf-8", head_only, no_store=True)
            return
        if self.path.startswith("/media/data/"):
            self._send_media(self.path, head_only)
            return
        self.send_error(404)

    def _send_media(self, url_path: str, head_only: bool) -> None:
        relative = url_path[len("/media/data/"):]
        target = Path("/media/data", relative).resolve()
        if not any(str(target).startswith(str(root) + "/") for root in MEDIA_ROOTS) or not target.is_file():
            self.send_error(404)
            return
        suffix = target.suffix.lower()
        content_type = {".png": "image/png", ".md": "text/markdown; charset=utf-8", ".json": "application/json"}.get(suffix, "application/octet-stream")
        self._send_bytes(target.read_bytes(), content_type, head_only)

    def _send_bytes(self, body: bytes, content_type: str, head_only: bool, no_store: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("artifacts/component-analysis-evalset/evalset-20260917T033940Z"))
    parser.add_argument("--action-md", type=Path, nargs="*", default=None, help="action-*.md files (default: all under <run-dir>/a2-qc-samples)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use 0.0.0.0 only for LAN demos")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    action_files = args.action_md or sorted((args.run_dir / "a2-qc-samples").glob("action-*.md"))
    if not action_files:
        parser.error(f"no action-*.md found under {args.run_dir / 'a2-qc-samples'}; pass --action-md explicitly")

    state = ViewerState(args.run_dir, action_files)
    for key in ("adjudication", "candidate", "manifest"):
        missing = sum(1 for s in state.samples if not s["joined"][key])
        print(f"join check {key}: {len(state.samples) - missing}/{len(state.samples)}", flush=True)
    missing_png = [s["sample_id"] for s in state.samples if not s["state_png"]]
    print(f"state comparison PNG: {len(state.samples) - len(missing_png)}/{len(state.samples)}", flush=True)
    if missing_png:
        print("  missing:", ", ".join(missing_png[:10]), "..." if len(missing_png) > 10 else "", flush=True)

    handler = type("BoundViewerHandler", (ViewerHandler,), {"state": state})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"serving http://{args.host}:{args.port}/ ({len(state.samples)} samples from {len(action_files)} action files)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
