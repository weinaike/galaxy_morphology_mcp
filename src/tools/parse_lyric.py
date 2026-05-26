import re
from typing import Annotated, Any, List
import ast
from dataclasses import dataclass
import os


@dataclass
class ImageInfo:
    image: Any
    band: Any
    sigma: Any
    psf: Any
    psf_sampling: int
    mask: Any
    unit: str
    fitting_area: Any
    conversion: Any
    magzp: float
    skymodel: str
    skyparameter: List[List[float]]
    shift: float
    shift_param: List[List[float]]
    use_sed: int
    image_label: str # used for label only


def _resolve_path_pair(value, config_dir):
    """Resolve relative path in a [path, hdu] pair to absolute path."""
    if isinstance(value, list) and len(value) >= 1 and isinstance(value[0], str):
        if not os.path.isabs(value[0]):
            value[0] = os.path.normpath(os.path.join(config_dir, value[0]))
    return value


def parse_image_infos_from_lyric(path_or_text: str) -> List[ImageInfo]:
    """Extract FITS file paths from a given text or path.

    Args:
        path_or_text: A string that may contain FITS file paths or be a path itself.
    Returns:
        A list of ImageInfo objects.
    """
    config_dir = None
    if os.path.isfile(path_or_text):
        config_dir = os.path.dirname(os.path.abspath(path_or_text))
        with open(path_or_text, 'r') as f:
            content = f.read()
    else:
        content = path_or_text
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith('#')]

    pattern = re.compile(r'^I([A-Za-z])(\d+)\)\s*(.+?)\s*$')

    config_groups = {}
    for line in lines:
        line = line.split('#')[0].strip()
        match = pattern.match(line)
        if not match:
            continue
        label, index, value = match.groups()
        try:
            if index in ("1", "3", "4", "6"):
                value = value.strip("[]").split(",")
                if len(value) == 1:
                    value.append(0)
                else:
                    value = [value[0].strip(), int(value[1].strip())]
                if config_dir:
                    value = _resolve_path_pair(value, config_dir)
            else:
                value = ast.literal_eval(value)
        except:
            pass
        if label not in config_groups:
            config_groups[label] = {}
        config_groups[label][int(index)] = value

    image_infos = []
    for label in sorted(config_groups.keys()):
        group = config_groups[label]
        values = [group.get(i, None) for i in range(1, 16)]
        values.append(label)
        info = ImageInfo(*values)
        image_infos.append(info)

    return image_infos    

def TEST_parse_image_infos_from_lyric_success():
    path = "/home/jiangbo/GALFITS_examples/40/obj40.lyric"
    image_infos = parse_image_infos_from_lyric(path)
    for image_info in image_infos:
        print(image_info)

def TEST_parse_image_infos_from_lyric_failure():
    path = "/home/jiangbo/GALFITS_examples/40/obj40.lyr"
    image_infos = parse_image_infos_from_lyric(path)
    for image_info in image_infos:
        print(image_info)
    
if __name__ == '__main__':
    TEST_parse_image_infos_from_lyric_success()
    print("=====")
    TEST_parse_image_infos_from_lyric_failure()