# tasks.py
from celery import Celery
from .file_manager import GalfitsFileManager
from src.tools.galfits_fitting import ImageFitting, PureSEDFitting, ImageSEDFitting
import os
import requests

celery = Celery(
    "tasks",
    broker="sqlalchemy+sqlite:////workspace/celery.db",
    result_backend="db+sqlite:////workspace/celery.db",
)

@celery.task
def do_fitting_task(task_id: str, data: str):
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

            if fitting_mode == "image fitting":
                result = ImageFitting(lyric_file=local_lyric_file, workplace=os.path.join(fm.work_dir, "result"), args=args)
                if result["status"] == "success":
                    fm.upload_directory(os.path.join(fm.work_dir, "result"), remote_dir=output_path)
                requests.post(callback_url, json={"task_id": task_id, "status": result["status"], "message": result.get("message", "")})

            elif fitting_mode == "pure sed fitting":
                fm.download_file(workplace, os.path.join(fm.work_dir, "result"))
                
                result = PureSEDFitting(lyric_file=local_lyric_file, new_lyric_file=local_lyric_file, workplace=os.path.join(fm.work_dir, "result"), args=args)
                if result["status"] == "success":
                    fm.upload_file(local_lyric_file, output_path)
                requests.post(callback_url, json={"task_id": task_id, "status": result["status"], "message": result.get("message", "")})

            elif fitting_mode == "image sed fitting":
                result = ImageSEDFitting(lyric_file=local_lyric_file, workplace=os.path.join(fm.work_dir, "result"), args=args)
                if result["status"] == "success":
                    fm.upload_directory(os.path.join(fm.work_dir, "result"), remote_dir=output_path)
                requests.post(callback_url, json={"task_id": task_id, "status": result["status"], "message": result.get("message", "")})
        except Exception as e:
            requests.post(callback_url, json={"task_id": task_id, "status": "failure", "message": str(e)})        
