# tasks.py
from src.service.file_manager import GalfitsFileManager
from src.tools.galfits_fitting import ImageFitting, PureSEDFitting, ImageSEDFitting
import os
import time
import requests

def do_fitting_task(task_id: str, data: dict):
    st = time.time()
    print(f"Task {task_id} started at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st))}. Data: {data}", flush=True)
    with GalfitsFileManager() as fm:
        try: 
            fitting_mode = data.get("fitting_mode", "").lower()
            fitting_mode = fitting_mode.lower()
            lyric_file = data.get("lyric_file")
            workplace = data.get("workplace") # for pure sed fitting only
            output_path = data.get("output_path")
            callback_url = data.get("callback_url")
            local_lyric_file, _ = fm.download_lyric_and_fits_files(lyric_file)
            fm.update_local_lyric_file(lyric_file=local_lyric_file)
            args = data.get("args", [])

            # Handle arguments related to files, as they need to be downloaded or uploaded
            if "--readpar" in args:
                readpar_index = args.index('--readpar')
                if readpar_index + 1 < len(args):
                    readpar_path = args[readpar_index + 1]
                    local_readpar_path = fm.download_file(readpar_path, os.path.join(fm.work_dir, "readpar"))
                    args[readpar_index + 1] = local_readpar_path
            if "--readsummary" in args:
                readsummary_index = args.index('--readsummary')
                if readsummary_index + 1 < len(args):
                    readsummary_path = args[readsummary_index + 1]
                    local_readsummary_path = fm.download_file(readsummary_path, os.path.join(fm.work_dir, "readsummary"))
                    args[readsummary_index + 1] = local_readsummary_path        
            if "--priorpath" in args:
                priorpath_index = args.index('--priorpath')
                if priorpath_index + 1 < len(args):
                    priorpath_path = args[priorpath_index + 1]
                    local_priorpath_path = fm.download_file(priorpath_path, os.path.join(fm.work_dir, "priorpath"))
                    args[priorpath_index + 1] = local_priorpath_path
            if "--parconstrain" in args:
                parconstrain_index = args.index('--parconstrain')
                if parconstrain_index + 1 < len(args):
                    parconstrain_path = args[parconstrain_index + 1]
                    local_parconstrain_path = fm.download_file(parconstrain_path, os.path.join(fm.work_dir, "constrain"))
                    args[parconstrain_index + 1] = local_parconstrain_path                

            fm.run_pre_hooks() # run pre hooks to download files if needed
            
            if fitting_mode == "image fitting":
                result = ImageFitting(lyric_file=local_lyric_file, workplace=os.path.join(fm.work_dir, "result"), args=args)
                print(f"Task {task_id} Image fitting result: {result}", flush=True)
                if result["status"] == "success":
                    fm.upload_folder(os.path.join(fm.work_dir, "result"), output_path)
                else:
                    result["status"] = "failure" # force to be failure if not success, to avoid confusion  
                res = requests.post(callback_url, json={"task_id": task_id, "status": result["status"], "message": result.get("message", "")})
                print(f"Callback response status code: {res.status_code}, response body: {res.text}")

            elif fitting_mode == "sed fitting":
                local_workplace = fm.download_file(workplace, os.path.join(fm.work_dir, "result"))
                
                result = PureSEDFitting(lyric_file=local_lyric_file, new_lyric_file=local_lyric_file, workplace=local_workplace, args=args)
                print(f"Task {task_id} SED fitting result: {result}", flush=True)
                if result["status"] == "success":
                    fm.upload_file(local_lyric_file, os.path.join(output_path, "image_sed_default.lyric"))
                else:
                    result["status"] = "failure" # force to be failure if not success, to avoid confusion 
                res = requests.post(callback_url, json={"task_id": task_id, "status": result["status"], "message": result.get("message", "")})
                print(f"Callback response status code: {res.status_code}, response body: {res.text}")

            elif fitting_mode == "image-sed fitting":
                result = ImageSEDFitting(lyric_file=local_lyric_file, workplace=os.path.join(fm.work_dir, "result"), args=args)
                print(f"Task {task_id} Image-SED fitting result: {result}", flush=True)
                if result["status"] == "success":
                    fm.upload_folder(os.path.join(fm.work_dir, "result"), output_path)
                else:
                    result["status"] = "failure" # force to be failure if not success, to avoid confusion
                res = requests.post(callback_url, json={"task_id": task_id, "status": result["status"], "message": result.get("message", "")})
                print(f"Callback response status code: {res.status_code}, response body: {res.text}")

            fm.run_post_hooks() # run post hooks to upload files if needed
        except Exception as e:
            res = requests.post(callback_url, json={"task_id": task_id, "status": "failure", "message": str(e)})        
            print(f"Callback response status code: {res.status_code}, response body: {res.text}")
    et = time.time()
    print(f"Task {task_id} completed in {et - st:.2f} seconds.", flush=True)


def TEST_do_fitting_task():
    task_id = "test_task_001"
    data = {'fitting_mode': 'SED Fitting', 'lyric_file': '/zhongling/test_zl_twoexamples/6978/97/input/97.lyric', 'workplace': '/zhongling/test_zl_twoexamples/6978/94/output/', 'output_path': '/zhongling/test_zl_twoexamples/6978/97/output/', 'args': ['--readsummary', '/zhongling/test_zl_twoexamples/6978/97/input/obj6978_s2r_nosed_2.gssummary', '--priorpath', '/zhongling/test_zl_twoexamples/6978/97/input/97.prior'], 'callback_url': 'http://10.15.49.115:9005/fitting/v2/nodes/callback'}

    do_fitting_task(task_id, data)
    
if __name__ == "__main__":    
    TEST_do_fitting_task()    
