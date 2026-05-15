import re
import os

def parse_galfit_summary(file_path):
    """
    提取 summary.md 中的核心参数 (Chi2 和各成分物理量)
    """
    if not os.path.exists(file_path):
        return "Summary file not found."

    with open(file_path, 'r') as f:
        content = f.read()

    # 1. 提取全局指标 Chi2/nu
    chi2_match = re.search(r'Chi\^2/nu\s*\|\s*([\d\.]+)', content)
    chi2_nu = chi2_match.group(1) if chi2_match else "unknown"

    # 2. 提取成分参数 (利用正则匹配 obj_X 开头的行)
    comp_lines = re.findall(r'\|\s*(obj_\d+.*?)\|', content)
    
    parsed_txt = f"Global: Chi2/nu = {chi2_nu}\nComponents:\n"
    for line in comp_lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 7:
            # 假设常见排版: ID, Type, X, Y, Mag, Re, n
            parsed_txt += f"- {parts[0]} ({parts[1]}): Mag={parts[4]}, Re={parts[5]}, n={parts[6]}\n"

    return parsed_txt.strip()