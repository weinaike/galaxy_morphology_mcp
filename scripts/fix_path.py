import os
import glob
from tqdm import tqdm

# 自动获取项目根目录，并确保末尾带斜杠，比如: /mnt/data/wyh/galaxy_morphology_mcp/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_PREFIX = os.path.join(BASE_DIR, "") 

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. 核心大瘦身：直接把长长的绝对路径前缀抹除
    # 比如 /mnt/data/wyh/.../data/galfit_sample... -> 变成 data/galfit_sample...
    new_content = content.replace(ABS_PREFIX, "")
    
    # 2. 顺手干掉以前遗留的 PSF 路径刺客
    new_content = new_content.replace("./f160w_psf.fits", "data/galfit_sample_0204/f160w_psf.fits")

    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return True
    return False

def main():
    print("🔍 正在全局扫描数据目录，准备清理致命的绝对路径...")
    
    # 扩大搜索范围，把你生成的扰动文件和原始模板一网打尽
    search_pattern_feedme = os.path.join(BASE_DIR, "data", "**", "*.feedme")
    feedme_files = glob.glob(search_pattern_feedme, recursive=True)
    
    search_pattern_md = os.path.join(BASE_DIR, "data", "**", "*.md")
    md_files = glob.glob(search_pattern_md, recursive=True)
    
    all_files = feedme_files + md_files
    print(f"📦 共找到 {len(all_files)} 个配置文件，开始执行手术...")
    
    fixed_count = 0
    for f in tqdm(all_files, desc="处理文件"):
        if fix_file(f):
            fixed_count += 1
            
    print(f"✅ 绝杀！成功给 {fixed_count} 个文件完成 '瘦身'！\n现在它们全都是安全的相对路径了，绝对不会再撑爆 GALFIT 的内存！")

if __name__ == "__main__":
    main()