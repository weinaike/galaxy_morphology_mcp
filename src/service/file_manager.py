import re
import os
import shutil
import tempfile
from pathlib import Path
import requests
import zipfile

def extract_fits_paths_from_lyric(lyric_path):
    image_paths = []
    sigma_paths = []
    psf_paths = []
    mask_paths = []
    image_pattern = re.compile(r'^I[a-z]1\) \[(.+?\.fits)')
    sigma_pattern = re.compile(r'^I[a-z]3\) \[(.+?\.fits)')
    psf_pattern = re.compile(r'^I[a-z]4\) \[(.+?\.fits)')
    mask_pattern = re.compile(r'^I[a-z]6\) \[(.+?\.fits)')

    with open(lyric_path, 'r', encoding='utf-8') as f:
        for line in f:
            match = image_pattern.match(line.strip())
            if match:
                path = match.group(1)
                if not os.path.isabs(path): # TODO: It should be tested against OSS paths in the future
                    path = os.path.join(os.path.dirname(lyric_path), path)
                image_paths.append(path)
            match = sigma_pattern.match(line.strip())
            if match:                
                path = match.group(1)    
                if not os.path.isabs(path):
                    path = os.path.join(os.path.dirname(lyric_path), path)
                sigma_paths.append(path)
            match = psf_pattern.match(line.strip())
            if match:
                path = match.group(1)    
                if not os.path.isabs(path):
                    path = os.path.join(os.path.dirname(lyric_path), path)
                psf_paths.append(path)
            match = mask_pattern.match(line.strip())
            if match:
                path = match.group(1)    
                if not os.path.isabs(path):
                    path = os.path.join(os.path.dirname(lyric_path), path)
                mask_paths.append(path)    

    return image_paths, sigma_paths, psf_paths, mask_paths

class GalfitsFileManager:
    def __init__(self, prefix="galfits_fitting_"):
        self.prefix       = prefix
        self.pre_hooks = [] 
        self.post_hooks = []
        self.URL          = "https://astro-workbench-bts.lab.zverse.space:32443/api/csst"
        self.URL          = "https://astro-workbench.eva24002.lab.zverse.space:32443/api/csst"
        self.DOWNLOAD     = "/fitting/v2/oss/files/download"
        self.CONTENT      = "/fitting/v2/oss/files/content"
        self.UPLOADFILE   = "/fitting/v2/oss/files"
        self.UPLOADFOLDER = "/fitting/v2/oss/folders"

    def __enter__(self):
        self.work_dir = tempfile.mkdtemp(prefix=self.prefix)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, 'work_dir'):
            shutil.rmtree(self.work_dir)

    def download_lyric_and_fits_files(self, lyric_file):
        if not hasattr(self, "work_dir"):
            self.work_dir = tempfile.mkdtemp(prefix=self.prefix)
        local_lyric_file = self.download_file(lyric_file, self.work_dir)    
        image_files, sigma_files, psf_files, mask_files = extract_fits_paths_from_lyric(local_lyric_file)
        remote_prefix = Path(lyric_file).parent.parent.parent #//  /bywang/test001/6978/16/input/16.lyric -> /bywang/test001/6978/ 

        for fits_file in image_files + sigma_files + mask_files:
            self.download_file(os.path.join(str(remote_prefix), "raw", os.path.basename(fits_file)), os.path.join(self.work_dir, "fits_files"))
        for fits_file in psf_files:
            self.download_file(os.path.join(str(remote_prefix), "raw", "PSF", os.path.basename(fits_file)), os.path.join(self.work_dir, "fits_files"))    

        return local_lyric_file, (image_files, sigma_files, psf_files, mask_files)

    def copy_lyric_and_fits_files(self, lyric_file):
        # Used for test only as no oss download currently
        if not hasattr(self, "work_dir"):
            self.work_dir = tempfile.mkdtemp(prefix=self.prefix)
        local_lyric_file = os.path.join(self.work_dir, os.path.basename(lyric_file))
        shutil.copy(lyric_file, local_lyric_file)

        fits_files = extract_fits_paths_from_lyric(local_lyric_file)
        for fits_file in fits_files:
            dest_path = os.path.join(self.work_dir, "fits_files", os.path.basename(fits_file))
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy(fits_file, dest_path)

        return local_lyric_file, fits_files    

    def download_file(self, oss_path, dest_dir):
        url = self.URL + self.DOWNLOAD
        os.makedirs(dest_dir, exist_ok=True)

        response = requests.get(url, params={"filePath": oss_path}, stream=True)
        response.raise_for_status()  # 自动抛异常（4xx/5xx）

        is_folder = oss_path.endswith("/")

        if is_folder:
            folder_name = oss_path.strip("/").split("/")[-1] 
            zip_path = os.path.join(dest_dir, f"{folder_name}.zip")
            final_path = os.path.join(dest_dir, folder_name)

            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)

            shutil.unpack_archive(zip_path, final_path)
            os.remove(zip_path)
        else:
            file_name = os.path.basename(oss_path)
            final_path = os.path.join(dest_dir, file_name)

            with open(final_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)
        return final_path            


    def update_local_lyric_file(self, 
        lyric_file: str,
        *,
        new_img_dir: str=None,
        new_psf_dir: str=None,
        new_sigma_dir: str=None,
        new_mask_dir: str=None
    ):
        config_input_dir = os.path.dirname(lyric_file)
        fits_files_dir = os.path.join(config_input_dir, "fits_files")
        new_img_dir = new_img_dir or fits_files_dir
        new_psf_dir = new_psf_dir or fits_files_dir
        new_sigma_dir = new_sigma_dir or fits_files_dir
        new_mask_dir = new_mask_dir or fits_files_dir

        with open(lyric_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = []
        #pattern = re.compile(r'^(I[a-z][1346])\) \[(.+?)([^/]+?\.fits)(.*)\]')
        pattern = re.compile(r'^(I[a-z][1346])\) \[(.+?)([^/]+?\.fits)\s*,\s*([0-9]*)\]')


        for line in lines:
            match = pattern.match(line.strip())
            if not match:
                new_lines.append(line)
                continue

            key = match.group(1)    # 例如 Ia1, Ib3
            prefix = match.group(2) # 旧路径前半部分
            fits_name = match.group(3) # 文件名 f115w.fits
            suffix = match.group(4)   # 后面的 ,0 等

            num = int(re.search(r'\d+', key).group())
            if num not in {1, 3, 4, 6}:
                new_lines.append(line)
                continue

            if num == 1:
                new_path = Path(new_img_dir) / fits_name
            elif num == 3:
                new_path = Path(new_sigma_dir) / fits_name
            elif num == 4:
                new_path = Path(new_psf_dir) / fits_name
            else:
                new_path = Path(new_mask_dir) / fits_name

            new_line = f"{key}) [{new_path},{suffix}]\n"
            new_lines.append(new_line)

        with open(lyric_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
    
    def add_pre_hook(self, callable_func, **kwargs):
        self.pre_hooks.append({"func": callable_func, "args": kwargs})

    def add_post_hook(self, callable_func, **kwargs):
        self.post_hooks.append({"func": callable_func, "args": kwargs})

    def upload_file(self, local_file_path, oss_file_path):
        url = self.URL + self.UPLOADFILE

        if os.path.exists(local_file_path):
            with open(local_file_path, "r") as f:
                data = {"filePath": oss_file_path, "content": f.read()}
                resp = requests.post(url, json=data)

            resp.raise_for_status()    
            print(f"upload file `{local_file_path}' to `{oss_file_path}'. resp: {resp}")

    def upload_folder(self, local_folder_path, target_path):
        if not target_path.endswith("/"):
            target_path += "/"

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_zip:
            zip_filename = temp_zip.name

        with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(local_folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, local_folder_path)
                    zipf.write(file_path, rel_path)

        url = self.URL + self.UPLOADFOLDER

        with open(zip_filename, "rb") as f:
            files = {"file": (os.path.basename(local_folder_path) + ".zip", f, "application/zip") }
            data = {"targetPath": target_path}
            resp = requests.post(url, files=files, data=data)

        resp.raise_for_status()
        os.unlink(zip_filename)

    def run_pre_hooks(self):
        for hook in self.pre_hooks:
            hook["func"](**hook["args"])
    
    def run_post_hooks(self):
        for hook in self.post_hooks:
            hook["func"](**hook["args"])        
            
def TEST_extract_fits_paths():
    lyric_path = "/home/jiangbo/galaxy_morphology_mcp/GALFITS_examples/40/obj40_s2_sed_opt_free.lyric"
    fits_paths = extract_fits_paths_from_lyric(lyric_path)
    for path in fits_paths:
        print(path)

def TEST_download_files():
    with GalfitsFileManager() as fm:
        lyric_file = "/home/jiangbo/GALFITS_examples/latest/configs/obj692"
        local_lyric, fits_files = fm.download_lyric_and_fits_files(lyric_file)
        print("Local lyric file:", local_lyric)
        print("Downloaded fits files:", fits_files)        

def TEST_upload_folder():
    with GalfitsFileManager() as fm:
        local_folder = "/home/jiangbo/GALFITS_examples/latest/configs/obj692_fits_files"
        target_path = "/obj692/obj692_fits/"
        fm.upload_folder(local_folder, target_path)
        print(f"Folder {local_folder} uploaded to {target_path}")        

def TEST_download_file():
    with GalfitsFileManager() as fm:
        oss_file = "/obj692/obj692.lyric"
        local_file = fm.download_file(oss_file, fm.work_dir)
        print(f"File {oss_file} downloaded to {local_file}")        

def TEST_download_file2():
    with GalfitsFileManager() as fm:
        oss_file = "/obj692/obj692_fits/obj692_fits_files/mask692_F444W.fits"
        local_file = fm.download_file(oss_file, fm.work_dir)
        print(f"File {oss_file} downloaded to {local_file}")        

def TEST_upload_file():
    with GalfitsFileManager() as fm:
        local_file = "/home/jiangbo/GALFITS_examples/latest/configs/obj692"
        target_path = "/obj692/obj692.lyric"
        fm.upload_file(local_file, target_path)
        print(f"File {local_file} uploaded to {target_path}")        

def TEST_upload_and_download():
    with GalfitsFileManager() as fm:
        local_dir = "/home/jiangbo/GALFITS_examples/latest/configs/obj692_fits_files"
        target_path = "/obj692_2/obj692_fits/"

        fm.upload_folder(local_dir, target_path)
        print(f"Folder {local_dir} uploaded to {target_path}")        

        target_path = "/obj692_2/obj692_fits/mask692_F444W.fits"
        fm.download_file(target_path, fm.work_dir)
        print(f"Folder {target_path} downloaded to {fm.work_dir}")

if __name__ == "__main__":
    # TEST_extract_fits_paths()
    # TEST_upload_file()
    # TEST_upload_folder()
    # TEST_upload_folder2()
    # TEST_download_file2()
    TEST_upload_and_download()
        
        
