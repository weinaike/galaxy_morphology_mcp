import re
import sys
from dataclasses import dataclass
from typing import Annotated, List, Tuple, Union, Optional
from pathlib import Path

# ---------- regex ----------
HEADER_RE = re.compile(r"(?m)^#\s*(Object|Component)\s*number:\s*(\d+)\s*$")
HEADER_RE = re.compile(r"(?m)^\s*#\s*(Component|Object)\s*number:\s*(\d+)")
COMP_TYPE_RE  = re.compile(r"(?m)^\s*0\)\s*([A-Za-z_]+)\b")
POS_RE        = re.compile(r"(?m)^\s*1\)\s*([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\b")
MAG_RE        = re.compile(r"(?m)^\s*3\)\s*([+-]?\d+(?:\.\d+)?)\b")
N_RE          = re.compile(r"(?m)^\s*5\)\s*([+-]?\d+(?:\.\d+)?)\b")

@dataclass
class Block:
    text: Annotated[str, "the text of the block"]
    comp_type: Annotated[str, "the type of the component"]
    header_type: Annotated[str, "the type of the header, Object or Component"]
    """
    The block of the feedme file
    text: the text of the block
    comp_type: the type of the component
    header_type: the type of the header, Object or Component
    """

def _split_prefix_and_blocks(
    feedme_file: Annotated[str, "the path of the feedme file"]
    ) -> Tuple[Annotated[str, "the prefix of the feedme file"],
               Annotated[List[Annotated[Block, "the blocks of the feedme file"]], "the blocks of the feedme file"]]:
    """
    Split into prefix (before first object) and a list of object blocks.
    feedme_file: the path of the feedme file
    return: the prefix of the feedme file and the blocks of the feedme file
    """
    headers = list(HEADER_RE.finditer(feedme_file))
    if not headers:
        raise ValueError("No '# Object/Component number:' blocks found.")

    prefix = feedme_file[:headers[0].start()]
    blocks: List[Block] = []

    for i, h in enumerate(headers):
        start = h.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(feedme_file)
        bt = feedme_file[start:end].rstrip() + "\n"

        header_type = h.group(1)

        mt = COMP_TYPE_RE.search(bt)
        ctype = mt.group(1).lower() if mt else "unknown"
        blocks.append(Block(text=bt, comp_type=ctype, header_type=header_type))

    return prefix, blocks

def _renumber_and_join(
    prefix: Annotated[str, "the prefix of the feedme file"],
    blocks: Annotated[List[Annotated[Block, "the blocks of the feedme file"]], "the blocks of the feedme file"],
    start_num: Annotated[int, "the starting number of the components"] = 1
    ) -> Annotated[str, "the text of the new feedme file"]:
    """
    Renumber and join the blocks of the feedme file
    prefix: the prefix of the feedme file
    blocks: the blocks of the feedme file
    start_num: the starting number of the components
    return: the text of the new feedme file
    """
    out = []
    n = start_num
    for b in blocks:
        new_header = f"# {b.header_type} number: {n}"
        bt = HEADER_RE.sub(new_header, b.text, count=1)
        out.append(bt.rstrip() + "\n")
        n += 1
    return prefix + "".join(out)

def _get_first_sersic_block(
    blocks: Annotated[List[Annotated[Block, "the blocks of the feedme file"]], "the blocks of the feedme file"]
    ) -> Annotated[str, "the text of the first sersic block"]:
    """
    Get the first sersic block from the blocks of the feedme file
    blocks: the blocks of the feedme file
    return: the text of the first sersic block
    """
    for b in blocks:
        if b.comp_type == "sersic":
            return b.text
    raise ValueError("No sersic component found (needed as template).")

def _get_first_sersic_xy_mag(
    blocks: Annotated[List[Annotated[Block, "the blocks of the feedme file"]], "the blocks of the feedme file"]
    ) -> Annotated[Tuple[Annotated[float, "the x coordinate of the first sersic component"],
                        Annotated[float, "the y coordinate of the first sersic component"],
                        Annotated[float, "the magnitude of the first sersic component"]],
                    "the x, y, and magnitude of the first sersic component"]:
    """
    Get the x, y, and magnitude of the first sersic component from the blocks of the feedme file
    blocks: the blocks of the feedme file
    return: the x, y, and magnitude of the first sersic component
    """
    s = _get_first_sersic_block(blocks)
    pm = POS_RE.search(s)
    mm = MAG_RE.search(s)
    if not pm:
        raise ValueError("Cannot parse first sersic '1) x y' line.")
    if not mm:
        raise ValueError("Cannot parse first sersic '3) mag' line.")
    return float(pm.group(1)), float(pm.group(2)), float(mm.group(1))

def _get_first_sersic_attrs(
    blocks: Annotated[List[Annotated[Block, "the blocks of the feedme file"]], "the blocks of the feedme file"]
    ) -> Annotated[Tuple[Annotated[float, "the x coordinate of the first sersic component"],
                        Annotated[float, "the y coordinate of the first sersic component"],
                        Annotated[float, "the magnitude of the first sersic component"]],
                    "the x, y, and magnitude of the first sersic component"]:
    """
    Get the x, y, and magnitude of the first sersic component from the blocks of the feedme file
    blocks: the blocks of the feedme file
    return: the x, y, and magnitude of the first sersic component
    """
    s = _get_first_sersic_block(blocks)
    pm = POS_RE.search(s)
    mm = MAG_RE.search(s)
    nm = N_RE.search(s)
    if not pm:
        raise ValueError("Cannot parse first sersic '1) x y' line.")
    if not mm:
        raise ValueError("Cannot parse first sersic '3) mag' line.")
    if not nm:
        raise ValueError("Cannot parse first sersic '5) n' line.")    
    return float(pm.group(1)), float(pm.group(2)), float(mm.group(1)), float(nm.group(1))

def _ensure_header(
    block_text: Annotated[str, "the text of the block of the feedme file"],
    default_header_type: Annotated[str, "the default type of the header, Object or Component"] = "Object"
    ) -> Annotated[Tuple[Annotated[str, "the text of the block of the feedme file with the header"],
                        Annotated[str, "the type of the header, Object or Component"]],
                    "the text of the block of the feedme file with the header and the type of the header"]:
    """
    Ensure the header of the block of the feedme file
    block_text: the text of the block of the feedme file
    return: the text of the block of the feedme file with the header
    """
    if block_text is None:
        block_text = ""
    if not isinstance(block_text, str):
        block_text = str(block_text)
    block_text = block_text.strip() + "\n"
    first = block_text.splitlines()[0] if block_text.strip() else ""
    m = HEADER_RE.search(first)
    if m:
        return block_text, m.group(1)
    return f"# {default_header_type} number: 0\n" + block_text, default_header_type

def _make_psf_from_first_sersic(
    blocks: Annotated[List[Annotated[Block, "the blocks of the feedme file"]], "the blocks of the feedme file"],
    delta_mag: Annotated[float, "the delta magnitude"] = 1.5
    ) -> Annotated[str, "the text of the psf block"]:
    """
    Make the psf block from the first sersic component in the blocks of the feedme file
    blocks: the blocks of the feedme file
    delta_mag: the delta magnitude
    return: the text of the psf block
    """
    x, y, mag = _get_first_sersic_xy_mag(blocks)
    psf_block = f"""
0) psf                       #  Component type
1) {x:.5f}  {y:.5f}  1  1     #  Position x, y
3) {mag + delta_mag:.4f}  1   #  Integrated magnitude (fainter by {delta_mag})
Z) 0                          #  Skip? (yes=1, no=0)
"""
    result, _ = _ensure_header(psf_block)
    return result

# ---------- public main ----------
# insert_list supports:
#   "sersic" -> duplicate first sersic block
#   "psf"    -> build psf using first sersic x/y and mag+1.5
#   dict like {"type":"psf","delta_mag":2.0}  (optional customization)
InsertItem = Union[str, dict]

def add_components(
    feedme_file: Annotated[str, "the text of the feedme file"],
    insert_list: Annotated[List[InsertItem], "the insert list"]
    ) -> Annotated[str, "the text of the new feedme file"]:
    """
    Main function: 先应用天文老师的物理大底规则，再用专家先验增量覆盖
    """
    p = Path(feedme_file)
    if p.is_file():
        feedme_file = p.read_text(encoding="utf-8", errors="ignore")

    prefix, blocks = _split_prefix_and_blocks(feedme_file)

    sky_idxs = [i for i, b in enumerate(blocks) if b.comp_type == "sky"]
    if len(sky_idxs) > 1:
        raise ValueError(f"Found {len(sky_idxs)} sky components. Requirement: exactly one sky.")

    if len(sky_idxs) == 1:
        sky_idx = sky_idxs[0]
        sky_block = blocks[sky_idx]
        non_sky = [b for i, b in enumerate(blocks) if i != sky_idx]
        blocks = non_sky + [sky_block]

    first_sersic_block = _get_first_sersic_block(blocks)

    insert_blocks: List[Block] = []
    for item in insert_list:
        # 1. 统一提取真正的形态类别 (兼容动作包装字典)
        if isinstance(item, str):
            t = item.lower().strip()
            item_dict = {}
        elif isinstance(item, dict):
            # 🚀 兼容：如果把整个 Action A 字典塞进来了，智能提取它的成分类型
            t = str(item.get("component_type", item.get("type", ""))).lower().strip()
            if t in ["a", "b", "c", "d"]: # 如果取到的是外层动作标识，降级取具体的组件类型
                t = str(item.get("component_type", "")).lower().strip()
            item_dict = item
        else:
            raise ValueError(f"Insert item must be str or dict, got: {type(item)}")

        # ========================================================
        # 🛠️ 步骤 A：运行天文老师的默认保底配置 (完全保留你的原有设计)
        # ========================================================
        if t == "sersic":
            bt = first_sersic_block
            index = item_dict.get("n", None) or item_dict.get("index", None)
            if index is not None:
                n_pattern = r"^(\s*5\))\s+[-+]?\d*\.?\d+"
                bt = re.sub(n_pattern, r"\1 " + str(index), bt, flags=re.MULTILINE)
        elif t in ["bar", "disk", "bulge"]:
            bt = first_sersic_block
            n_pattern = r"(^\s*5\))\s+[-+]?\d*\.?\d+"
            if t == "bar":
                bt = re.sub(n_pattern, r"\1 0.5", bt, flags=re.MULTILINE)
            elif t == "disk":
                bt = re.sub(r"(^\s*5\))\s+[-+]?\d*\.?\d+", r"\1 1.0", bt, flags=re.MULTILINE)
            elif t == "bulge":
                bt = re.sub(r"(^\s*5\))\s+[-+]?\d*\.?\d+", r"\1 4.0", bt, flags=re.MULTILINE)    
        elif t == "psf":
            delta = float(item_dict.get("delta_mag", 1.5)) if isinstance(item, dict) else 1.5
            bt = _make_psf_from_first_sersic(blocks, delta_mag=delta)
        else:
            raise ValueError(f"Unknown insert type string: {t!r}.")

        # ========================================================
        # 🚀 步骤 B：增量覆盖机制 (拆包裹)
        # ========================================================
        if isinstance(item, dict) and t in ["bar", "disk", "bulge", "sersic"]:
            # 📦 拆开包裹，如果动作里没有这个包裹，就会拿到一个安全的空字典 {}
            patch = item_dict.get("patch_applied", {})

            # 1. 覆盖精准 Sersic 指数 n
            preset_n = patch.get("preset_n")  # 👈 现在从 patch 里取值，而不是 item_dict
            if preset_n is not None:
                bt = re.sub(r"(^\s*5\))\s+[-+]?\d*\.?\d+", r"\g<1> " + f"{preset_n:.4f}", bt, flags=re.MULTILINE)
                
            # 2. 覆盖精准有效半径 Re
            preset_re = patch.get("preset_re")
            if preset_re is not None:
                bt = re.sub(r"(^\s*4\))\s+[-+]?\d*\.?\d+", r"\g<1> " + f"{preset_re:.4f}", bt, flags=re.MULTILINE)
                
            # 3. 覆盖亮度偏置
            mag_offset = patch.get("mag_offset")
            if mag_offset is not None:
                mag_match = re.search(r"^\s*3\)\s+([-+]?\d*\.?\d+)", bt, flags=re.MULTILINE)
                if mag_match:
                    new_mag = float(mag_match.group(1)) + float(mag_offset)
                    bt = re.sub(r"(^\s*3\))\s+[-+]?\d*\.?\d+", r"\g<1> " + f"{new_mag:.4f}", bt, flags=re.MULTILINE)

            # 4. 覆盖轴比 q
            preset_q = patch.get("preset_q")
            if preset_q is not None:
                bt = re.sub(r"(^\s*9\))\s+[-+]?\d*\.?\d+", r"\g<1> " + f"{preset_q:.4f}", bt, flags=re.MULTILINE)

            # 5. 覆盖位置角 PA
            preset_pa = patch.get("preset_pa")
            if preset_pa is not None:
                bt = re.sub(r"(^\s*10\))\s+[-+]?\d*\.?\d+", r"\g<1> " + f"{preset_pa:.4f}", bt, flags=re.MULTILINE)
                
        if bt is None:
            raise ValueError(f"Block text is None for item: {item}")
        bt, header_type = _ensure_header(bt)
        mt = COMP_TYPE_RE.search(bt)
        ctype = mt.group(1).lower() if mt else "unknown"
        insert_blocks.append(Block(text=bt, comp_type=ctype, header_type=header_type))

    if len(sky_idxs) == 1:
        blocks = blocks[:-1] + insert_blocks + [blocks[-1]]
    else:
        blocks = blocks + insert_blocks

    return _renumber_and_join(prefix, blocks, start_num=1)

def delete_components(
    feedme_file: Annotated[str, "Path to feedme file or raw feedme text"],
    component_ids: Annotated[List[int], "List of component numbers to DELETE (1-based)"]
) -> Annotated[str, "New feedme text after deletion"]:
    """
    Delete one or more components by their 1-based component ID.
    - Will NOT delete the sky component (enforced).
    - Automatically renumbers remaining components 1,2,3...
    - Sky remains last.
    """
    p = Path(feedme_file)
    if p.is_file():
        feedme_file = p.read_text(encoding="utf-8", errors="ignore")

    prefix, blocks = _split_prefix_and_blocks(feedme_file)

    sky_idxs = [i for i, b in enumerate(blocks) if b.comp_type == "sky"]
    # if len(sky_idxs) == 0:
    #     raise ValueError("No sky component found. Requirement: exactly one sky.")
    if len(sky_idxs) > 1:
        raise ValueError(f"Found {len(sky_idxs)} sky components. Requirement: exactly one sky.")

    sky_blocks = [b for i, b in enumerate(blocks) if i in sky_idxs]
    non_sky_blocks = [b for i, b in enumerate(blocks) if i not in sky_idxs]

    total = len(non_sky_blocks)
    for cid in component_ids:
        if not isinstance(cid, int) or cid < 1 or cid > total:
            raise ValueError(f"Invalid component ID: {cid}. Must be 1..{total}")

    keep_idxs = [i for i in range(total) if (i + 1) not in component_ids]
    kept_non_sky = [non_sky_blocks[i] for i in keep_idxs]

    new_blocks = kept_non_sky + sky_blocks

    return _renumber_and_join(prefix, new_blocks, start_num=1)

def TEST_add_components():
    feedme_file = "galfit_multicomponent/goodsn_9076/round_5/goodsn_9076_f160w.feedme"
    new_feedme_file = "/tmp/new.feedme"

    # new_components = ["sersic"] #insert_list
    new_components = [
        {
            "type": "sersic",
            "n": "1.333"
        }
    ]
    # new_components = ["bar", "disk", "bulge", {"type": "psf", "delta_mag": 2.0}]
    new_text = add_components(feedme_file, new_components)
    with open(new_feedme_file, "w") as f:
        f.write(new_text)
    print(f"Feedme file {new_feedme_file} created successfully.")

def TEST_delete_components():
    feedme_file = "/home/jiangbo/galaxy_morphology_mcp/galfit_multicomponent/galfits_40_f227w/archives/20260402T160904.b480df99/galfits_40_iter1.feedme"
    new_feedme_file = "/tmp/new.feedme"
    new_text = delete_components(feedme_file, component_ids=[1])
    with open(new_feedme_file, "w") as f:
        f.write(new_text)
    print(f"Feedme file {new_feedme_file} created successfully.")

# ---------- public main ----------
# insert_list supports:
#   "sersic" -> duplicate first sersic block
#   "psf"    -> build psf using first sersic x/y and mag+1.5
#   dict like {"type":"psf","delta_mag":2.0}  (optional customization)
if __name__ == "__main__":
    # TEST_delete_components()
    TEST_add_components()
