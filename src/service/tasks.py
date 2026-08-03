# tasks.py
from src.service.file_manager import GalfitsFileManager
from src.service.watcher import watch_round_status_files
from src.service.workflow_runner import run_galfit_workflow
from src.tools.galfits_fitting import ImageFitting, PureSEDFitting, ImageSEDFitting
from src.tools.parse_feedme import parse_feedme
from src.tools.run_galfit import run_galfit
import asyncio
import datetime
import os
import time
import requests
import traceback


GALFIT_TASKS: dict[str, dict] = {}


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def get_galfit_task(task_id: str) -> dict | None:
    return GALFIT_TASKS.get(task_id)


def list_galfit_tasks() -> list[dict]:
    return sorted(GALFIT_TASKS.values(), key=lambda item: item.get("created_at", ""), reverse=True)


def _append_galfit_event(task_id: str, event: dict) -> None:
    task = GALFIT_TASKS.get(task_id)
    if not task:
        return
    event.setdefault("created_at", utc_now_iso())
    task.setdefault("events", []).append(event)
    task["updated_at"] = event["created_at"]


async def run_galfit_task(task_id: str, data: dict) -> None:
    """Run one GALFIT round or the audited autonomous workflow."""
    feedme = os.path.abspath(data["feedme"])
    config_paths = parse_feedme(feedme)
    output_file = config_paths.get("output")
    watch_root = os.path.dirname(output_file) if output_file else os.path.dirname(feedme)
    task = GALFIT_TASKS[task_id]
    task.update({"status": "running", "started_at": utc_now_iso(),
                 "updated_at": utc_now_iso(), "watch_root": watch_root})
    _append_galfit_event(task_id, {"type": "task_started", "feedme": feedme, "watch_root": watch_root})
    stop_event = asyncio.Event()

    async def on_round_status(status_file: str, payload: dict) -> None:
        _append_galfit_event(task_id, {"type": "round_finished", "round_status_file": status_file,
                                      "archive_dir": os.path.dirname(status_file), "round_status": payload})

    watcher_task = asyncio.create_task(watch_round_status_files(watch_root, on_round_status, stop_event))
    try:
        options = data.get("options") or ["-o"]
        if data.get("workflow_mode", "workflow") == "single":
            result = await run_galfit(feedme, options, data.get("callback_url"))
        else:
            async def emit_workflow_event(event: dict) -> None:
                _append_galfit_event(task_id, event)

            result = await run_galfit_workflow(
                task_id=task_id, feedme=feedme, options=options, emit_event=emit_workflow_event,
                max_rounds=int(data.get("max_rounds", 8)), agent_command=data.get("agent_command"),
                agent_timeout=int(data.get("agent_timeout", 600)),
                verifier_command=data.get("verifier_command"),
                verifier_timeout=int(data.get("verifier_timeout", 600)),
            )
        task["result"] = result
        task["status"] = result.get("status", "unknown")
        _append_galfit_event(task_id, {"type": "task_finished", "status": task["status"]})
    except Exception as exc:
        task["status"] = "failure"
        task["error"] = str(exc)
        task["traceback"] = traceback.format_exc()
        _append_galfit_event(task_id, {"type": "task_failed", "error": str(exc)})
    finally:
        stop_event.set()
        await watcher_task
        task["finished_at"] = utc_now_iso()
        task["updated_at"] = task["finished_at"]

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
