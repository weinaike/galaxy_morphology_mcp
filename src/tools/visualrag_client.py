"""Client for the visualRAG online retrieval service.

Used by ``residual_analysis`` (VLM mode) to fetch Few-shot reference cases and
inject them into the first turn. The query galaxy's dual feature is extracted on
the *server*, so this client only:

1. derives the GALFIT archive dir from the comparison-PNG path it already has,
2. collects the raw material files (model cube + feedme + latest ``galfit.NN`` +
   mask/sigma) into a self-contained zip, resolving each by the name the runner
   *declared* (feedme paths + ``galfit_summary.md`` Output-File record) rather
   than by naming convention — so it works across pipelines whose filenames
   differ (gadotti ``galfit.fit``/``mask.fit`` vs JWST ``<obj>_galfit.fits``/
   ``mask_f277w.fits``). Feedme file refs are rewritten to basenames.
3. POSTs the zip to the service, parses the returned teaching cases,
4. downloads each case's comparison PNG to a temp file for inline image upload.

Everything is best-effort: any failure (service down, empty results, missing
files) makes the caller fall back to the plain single-image turn-1.
"""
from __future__ import annotations

import glob
import io
import logging
import os
import re
import tempfile
import zipfile

import requests

from .parse_feedme import parse_feedme

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120  # seconds (server does a DINOv2 forward + FAISS search)

# Intro prepended to turn-1 explaining the reference Few-shot. Captions travel
# interleaved with each image (see openai_analysis._build_interleaved...), so
# this only states the layout and that the LAST image is the analysis target.
REFERENCE_INTRO = (
    "下面先给出若干参考样例用于校准判别（按顺序：基线样例/困难反例/正样例），最多包含 3 个参考样例，但是可能因为检索匹配原因，部分参考样可能会缺失，"
    "每张图后附其专家图注；最后一张才是本轮待分析的目标星系。"
    "请结合参考样例中可迁移的「视觉特征→诊断→处方」规则，对最后一张目标星系"
    "完成阶段一的客观视觉特征提取。"
)

# Feedme file-reference lines rewritten to basenames so the staged archive is
# self-contained (read_archive resolves mask/sigma by basename within the dir).
_FEEDME_LINE_RE = re.compile(r"^([ABCDFG])\)\s+(\S+)(.*)$", re.MULTILINE)


def _normalize_feedme_paths(text: str) -> str:
    def repl(m: re.Match) -> str:
        return f"{m.group(1)}) {os.path.basename(m.group(2))}{m.group(3)}"
    return _FEEDME_LINE_RE.sub(repl, text)


def derive_archive_dir(image_file: str) -> str | None:
    """Locate the GALFIT archive dir that produced `image_file`.

    The comparison PNG sits in the archive (round) dir alongside the model
    output and the ``galfit.NN`` run log. ``galfit.[0-9]*`` is galfit's own
    auto-generated naming (stable across pipelines), so we use *it* — not the
    model cube, whose name varies (gadotti ``galfit.fit`` vs JWST
    ``<obj>_galfit.fits``) — as the archive marker, walking up from the PNG's
    dirname.
    """
    cur = os.path.dirname(os.path.abspath(image_file))
    for _ in range(4):
        if cur and glob.glob(os.path.join(cur, "galfit.[0-9]*")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _resolve_aux_path(declared: str, search_dirs: list[str]) -> str | None:
    """Find an aux file (model/mask/sigma) the feedme *declared*.

    Mirrors visualRAG.data.metadata.resolve_aux_path: try the declared name's
    basename in each search dir, then fall back to the declared absolute path.
    Naming conventions differ across pipelines (gadotti ``mask.fit`` vs JWST
    ``mask_f277w.fits``), so we never glob by prefix — only by the basename the
    runner itself wrote into the feedme.
    """
    if not declared:
        return None
    base = os.path.basename(declared)
    for d in search_dirs:
        cand = os.path.join(d, base)
        if os.path.exists(cand):
            return cand
    if os.path.isabs(declared) and os.path.exists(declared):
        return declared
    return None


def _find_feedme(adir: str) -> str | None:
    """Locate the feedme in the archive dir.

    Prefers ``*.feedme`` (the runner's convention in both gadotti and JWST
    pipelines). Falls back to scanning small text files for a GALFIT feedme
    header (an ``A)…F)`` file-ref block plus the ``H)`` fit-region line) for
    pipelines that don't use the ``.feedme`` extension.
    """
    matches = glob.glob(os.path.join(adir, "*.feedme"))
    if matches:
        return matches[0]
    for f in sorted(glob.glob(os.path.join(adir, "*"))):
        if not os.path.isfile(f) or os.path.getsize(f) > 65536:
            continue
        try:
            with open(f, errors="replace") as fh:
                head = fh.read(2048)
        except OSError:
            continue
        if (re.search(r"^[ABCDFG]\)\s+\S+\s*#", head, re.MULTILINE)
                and re.search(r"^H\)\s+\d+", head, re.MULTILINE)):
            return f
    return None


def _find_model_fits(adir: str, parsed: dict) -> str | None:
    """Resolve the GALFIT model-output cube WITHOUT guessing its filename.

    Naming differs across pipelines (JWST ``<obj>_galfit.fits`` vs gadotti
    ``galfit.fit``), so a ``*_galfit.fits`` glob misses half the archives.
    Instead read the name the runner declared, in priority order (mirrors
    visualRAG.data.pseudo_rgb._find_galfit_fits):

      1. ``galfit_summary.md`` ``**Output File:**`` line (post-hoc record),
      2. the feedme ``B)`` output line,
      3. ``*galfit*.fit[s]`` glob as a last-resort backstop.

    Searches only ``adir`` (the specific round) — never the object dir, which
    holds the latest round's cube and would silently mismatch archived rounds.
    """
    # 1. summary "**Output File:**" basename
    for pat in ("*galfit_summary.md", "galfit_summary.md"):
        for summ in glob.glob(os.path.join(adir, pat)):
            try:
                with open(summ, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(4096)
            except OSError:
                continue
            m = re.search(r"\*\*Output File:\*\*\s*`?([^\s`]+)", head)
            if m:
                cand = os.path.join(adir, os.path.basename(m.group(1)))
                if os.path.isfile(cand):
                    return cand
    # 2. feedme B) output line
    out = parsed.get("output")
    if out:
        cand = os.path.join(adir, os.path.basename(out))
        if os.path.isfile(cand):
            return cand
        if os.path.isabs(out) and os.path.isfile(out):
            return out
    # 3. glob backstop (exclude comparison/subcomp)
    cands = []
    for f in glob.glob(os.path.join(adir, "*")):
        b = os.path.basename(f).lower()
        if not (b.endswith(".fit") or b.endswith(".fits")):
            continue
        if "galfit" not in b or "comparison" in b or "subcomp" in b:
            continue
        cands.append(f)
    return sorted(cands)[0] if cands else None


def _find_latest_param(adir: str) -> str | None:
    """Most recent ``galfit.[0-9]*`` fitted-parameter file (highest suffix)."""
    matches = glob.glob(os.path.join(adir, "galfit.[0-9]*"))
    if not matches:
        return None
    return max(matches, key=lambda f: int(os.path.basename(f).rsplit(".", 1)[-1]))


def collect_material(image_file: str) -> bytes:
    """Gather the archive's raw material files into a flat self-contained zip.

    Resolves each file by the name the runner declared (feedme paths +
    ``galfit_summary.md`` Output-File record), not by naming convention, so the
    archive is correct across pipelines whose filenames differ (gadotti
    ``galfit.fit``/``mask.fit`` vs JWST ``<obj>_galfit.fits``/``mask_f277w.fits``).

    Ships: feedme (path refs rewritten to basenames), the model-output cube,
    mask + sigma (resolved from ``[adir, parent, grandparent]``), and the latest
    ``galfit.NN`` fitted-param log. Raises FileNotFoundError if the model cube
    can't be resolved — without it the server can't extract a feature, so a
    silent empty zip would only waste a round-trip.
    """
    adir = derive_archive_dir(image_file)
    if not adir:
        raise FileNotFoundError(f"could not derive archive dir from {image_file}")

    feedme = _find_feedme(adir)
    parsed = parse_feedme(feedme) if feedme else {}

    model_fits = _find_model_fits(adir, parsed)
    if not model_fits:
        raise FileNotFoundError(
            f"could not resolve GALFIT model cube in {adir} "
            "(checked galfit_summary.md Output File, feedme B) line, "
            "*galfit*.fit[s] glob)"
        )

    parent = os.path.dirname(adir)
    grandparent = os.path.dirname(parent)
    search_dirs = [adir, parent, grandparent]
    mask_path = _resolve_aux_path(parsed.get("mask"), search_dirs)
    sigma_path = _resolve_aux_path(parsed.get("sigma"), search_dirs)
    param_path = _find_latest_param(adir)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if feedme:
            with open(feedme) as f:
                zf.writestr(os.path.basename(feedme),
                            _normalize_feedme_paths(f.read()))
        zf.write(model_fits, os.path.basename(model_fits))
        if mask_path:
            zf.write(mask_path, os.path.basename(mask_path))
        if sigma_path:
            zf.write(sigma_path, os.path.basename(sigma_path))
        if param_path:
            zf.write(param_path, os.path.basename(param_path))
    return buf.getvalue()


def _service_url() -> str:
    return os.environ.get("VISUALRAG_SERVICE_URL", "").rstrip("/")


def _enabled() -> bool:
    return _service_url() != "" and os.environ.get("VISUALRAG_ENABLED", "1") != "0"


def query_service(image_file: str, top_k: int | None = None,
                  strategy: str | None = None) -> dict | None:
    """Query the retrieval service for the galaxy behind `image_file`.

    Returns the parsed JSON response (dict) or None when disabled / unavailable
    / empty (caller degrades to no-reference turn-1).
    """
    if not _enabled():
        return None
    top_k = top_k or int(os.environ.get("VISUALRAG_TOP_K", "1"))
    strategy = strategy or os.environ.get("VISUALRAG_STRATEGY", "both")
    try:
        zip_bytes = collect_material(image_file)
        r = requests.post(
            f"{_service_url()}/query",
            files={"archive": ("archive.zip", zip_bytes, "application/zip")},
            data={"top_k": str(top_k), "strategy": strategy},
            timeout=DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("visualRAG query_service failed, degrading: %s", e)
        return None


def fetch_reference_images(resp: dict) -> list[dict]:
    """Download each case's comparison PNG to a temp file, in Few-shot order.

    Order follows the design doc: [baseline, *hard_negative, positive]. Cases
    without an ``image_url`` are skipped (text-only fallback for that case).
    """
    blocks: list[dict] = []
    for role in ("baseline", "hard_negative", "positive"):
        items = resp.get(role + "s" if role == "hard_negative" else role) or []
        if isinstance(items, dict):  # baseline / positive are single objects
            items = [items]
        for c in items:
            url = c.get("image_url")
            if not url:
                continue
            try:
                data = requests.get(url, timeout=60).content
            except Exception as e:  # noqa: BLE001
                log.warning("visualRAG image download failed (%s): %s", url, e)
                continue
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(data)
            tmp.close()
            blocks.append({"image": tmp.name, "caption": c.get("caption", ""),
                           "role": role, "_tmp": True})
    return blocks


def cleanup_reference_images(blocks: list[dict] | None) -> None:
    """Remove temp PNGs created by fetch_reference_images."""
    if not blocks:
        return
    for b in blocks:
        try:
            if b.get("_tmp") and b.get("image") and os.path.exists(b["image"]):
                os.unlink(b["image"])
        except OSError:
            pass
