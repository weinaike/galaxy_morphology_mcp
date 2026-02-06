import re
import sys
from dataclasses import dataclass
from typing import Annotated, List, Tuple, Union, Optional
from pathlib import Path

# ---------- regex ----------
HEADER_RE = re.compile(r"(?m)^#\s*(Object|Component)\s*number:\s*(\d+)\s*$")
COMP_TYPE_RE  = re.compile(r"(?m)^\s*0\)\s*([A-Za-z_]+)\b")
POS_RE        = re.compile(r"(?m)^\s*1\)\s*([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\b")
MAG_RE        = re.compile(r"(?m)^\s*3\)\s*([+-]?\d+(?:\.\d+)?)\b")

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
    Main function:
      - Input: feedme_text + insert_list
      - Inserts components (in order) right before the SINGLE sky block
      - Ensures sky is last
      - Renumbers all objects sequentially from 1

    Rules enforced:
      - Exactly one sky block must exist, otherwise error.
      - sersic insert always duplicates first sersic.
      - psf insert uses first sersic x/y and mag + 1.5 (default; configurable via dict).
    """
    # Convenience: allow passing a file path instead of raw text
    p = Path(feedme_file)
    if p.is_file():
        feedme_file = p.read_text(encoding="utf-8", errors="ignore")

    prefix, blocks = _split_prefix_and_blocks(feedme_file)

    # Enforce exactly one sky
    sky_idxs = [i for i, b in enumerate(blocks) if b.comp_type == "sky"]
    if len(sky_idxs) == 0:
        raise ValueError("No sky component found. Requirement: exactly one sky.")
    if len(sky_idxs) > 1:
        raise ValueError(f"Found {len(sky_idxs)} sky components. Requirement: exactly one sky.")

    # Move sky to last (if not already)
    sky_idx = sky_idxs[0]
    sky_block = blocks[sky_idx]
    non_sky = [b for i, b in enumerate(blocks) if i != sky_idx]
    blocks = non_sky + [sky_block]

    # Prepare templates (first sersic exists?)
    first_sersic_block = _get_first_sersic_block(blocks)

    # Build insert blocks in order
    insert_blocks: List[Block] = []
    for item in insert_list:
        if isinstance(item, str):
            t = item.lower().strip()
            if t == "sersic":
                bt = first_sersic_block
            elif t == "psf":
                bt = _make_psf_from_first_sersic(blocks, delta_mag=1.5)
            else:
                raise ValueError(f"Unknown insert type string: {item!r}. Use 'sersic' or 'psf' or dict.")
        elif isinstance(item, dict):
            t = str(item.get("type", "")).lower().strip()
            if t == "sersic":
                bt = first_sersic_block
            elif t == "psf":
                delta = float(item.get("delta_mag", 1.5))
                bt = _make_psf_from_first_sersic(blocks, delta_mag=delta)
            else:
                raise ValueError(f"Unknown insert dict type: {item!r}.")
        else:
            raise ValueError(f"Insert item must be str or dict, got: {type(item)}")

        # Ensure bt is a string and has a header
        if bt is None:
            raise ValueError(f"Block text is None for item: {item}")
        bt, header_type = _ensure_header(bt)
        mt = COMP_TYPE_RE.search(bt)
        ctype = mt.group(1).lower() if mt else "unknown"
        insert_blocks.append(Block(text=bt, comp_type=ctype, header_type=header_type))

    # Insert right before sky (which is last)
    # blocks[-1] is sky
    blocks = blocks[:-1] + insert_blocks + [blocks[-1]]

    # Renumber and output
    return _renumber_and_join(prefix, blocks, start_num=1)

# ---------- public main ----------
# insert_list supports:
#   "sersic" -> duplicate first sersic block
#   "psf"    -> build psf using first sersic x/y and mag+1.5
#   dict like {"type":"psf","delta_mag":2.0}  (optional customization)
if __name__ == "__main__":

    feedme_file = "test_data/goodsn_8758_f160w_standard.feedme"
    new_feedme_file = "test_data/goodsn_8758_f160w.feedme"
    feedme_file = sys.argv[1]
    new_feedme_file = sys.argv[2]

    new_components = ["sersic"] #insert_list
    new_text = add_components(feedme_file, new_components)
    with open(new_feedme_file, "w") as f:
        f.write(new_text)
    print(f"Feedme file {new_feedme_file} created successfully.")
