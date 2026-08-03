import asyncio
import os

from fastapi import FastAPI, Body, HTTPException
import uvicorn
import uuid
import requests
from src.service.tasks import (
    do_fitting_task, get_galfit_task, list_galfit_tasks, run_galfit_task,
    GALFIT_TASKS, utc_now_iso,
)

app = FastAPI(title="galfits fitting service", version="1.0")

def validate_arguments(body: dict):
    fitting_mode = body.get("fitting_mode", "").lower()
    if fitting_mode not in ["image fitting", "sed fitting", "image-sed fitting"]:
        return False, "fitting_mode is required and must be one of 'image fitting', 'sed fitting', or 'image-sed fitting'"

    lyric_file = body.get("lyric_file", None)
    if lyric_file is None:
        return False, "lyric_file path is required"

    output_path = body.get("output_path", None)
    if output_path is None or not isinstance(output_path, str) or output_path.strip() == "":
        return False, "output_path is invalid, it should be a non-empty string"

    workplace = body.get("workplace", "")
    if fitting_mode == "sed fitting" and not isinstance(workplace, str):
        return False, "workplace path is invalid, it should be a string"

    args = body.get("args", None)
    args = args if args is not None else []
    # args is optional, but if provided, it must be a list of strings
    if not isinstance(args, (list, str)):
        return False, "args should be a list or a string"
    if isinstance(args, str):
        args = [args]  # convert single string to list    
        body["args"] = args  # update body with the converted list
    for arg in args:
        if not isinstance(arg, str):
            return False, "each argument in args should be a string"    
    # Remove --workplace and its value from args if it exists, since we handle workplace separately.
    idx = args.index("--workplace") if "--workplace" in args else -1
    if idx != -1:        
        args.pop(idx)  # remove --workplace
        if idx < len(args):
            args.pop(idx)  # remove the value after --workplace
    
    callback_url = body.get("callback_url", None)
    if callback_url is None or not callback_url.startswith("http"):
        return False, "callback_url must be a valid URL starting with http or https"    

    return True, ""    

@app.post("/api/fitting", summary="fitting interface")
async def fitting_process(body: dict = Body(...)):
    if body is None or not isinstance(body, dict):
        return {"status": "failure", "message": "invalid body!"}
    valid, message = validate_arguments(body)
    if not valid:
        return {"status": "failure", "message": message}
        
    task_id = uuid.uuid4().hex
    asyncio.create_task(asyncio.to_thread(do_fitting_task, task_id=task_id, data=body))
    return {"status": "success", "task_id": task_id, "message": "Fitting task has been submitted successfully."}


def validate_galfit_task_arguments(body: dict) -> tuple[bool, str]:
    feedme = body.get("feedme") or body.get("config_file")
    if not isinstance(feedme, str) or not feedme:
        return False, "feedme is required and must be a string"
    if not os.path.isabs(feedme):
        return False, "feedme must be an absolute path"
    if not os.path.isfile(feedme):
        return False, f"feedme does not exist: {feedme}"
    body["feedme"] = feedme

    options = body.get("options", ["-o"])
    if isinstance(options, str):
        options = [options]
    if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
        return False, "options must be a string or a list of strings"
    body["options"] = options

    if body.get("workflow_mode", "workflow") not in ("workflow", "single"):
        return False, "workflow_mode must be either 'workflow' or 'single'"
    for key, default in (("max_rounds", 8), ("agent_timeout", 600), ("verifier_timeout", 600)):
        value = body.get(key, default)
        if not isinstance(value, int) or value < 1:
            return False, f"{key} must be a positive integer"
    for key in ("agent_command", "verifier_command"):
        value = body.get(key)
        if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
            return False, f"{key} must be a list of strings"
    return True, ""


@app.post("/api/galfit/tasks", summary="submit a single-band GALFIT task")
async def submit_galfit_task(body: dict = Body(...)):
    if not isinstance(body, dict):
        return {"status": "failure", "message": "invalid body"}
    valid, message = validate_galfit_task_arguments(body)
    if not valid:
        return {"status": "failure", "message": message}
    task_id = uuid.uuid4().hex
    now = utc_now_iso()
    GALFIT_TASKS[task_id] = {
        "task_id": task_id, "kind": "galfit", "status": "queued",
        "created_at": now, "updated_at": now,
        "request": {key: body.get(key) for key in (
            "feedme", "options", "workflow_mode", "max_rounds", "agent_command",
            "agent_timeout", "verifier_command", "verifier_timeout", "callback_url",
        )},
        "events": [],
    }
    asyncio.create_task(run_galfit_task(task_id, body))
    return {"status": "success", "task_id": task_id, "message": "GALFIT task submitted"}


@app.get("/api/galfit/tasks", summary="list GALFIT tasks")
async def list_galfit_task_statuses():
    return {"status": "success", "tasks": list_galfit_tasks()}


@app.get("/api/galfit/tasks/{task_id}", summary="get GALFIT task status")
async def get_galfit_task_status(task_id: str):
    task = get_galfit_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "success", "task": task}


@app.get("/api/galfit/tasks/{task_id}/events", summary="get GALFIT task events")
async def get_galfit_task_events(task_id: str, after: int = 0):
    task = get_galfit_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    events = task.get("events", [])
    after = max(0, after)
    return {"status": "success", "task_id": task_id, "offset": after,
            "next_offset": len(events), "events": events[after:]}

# @app.get("/api/fitting-status/{task_id}")
# def status(task_id: str):
#     res = do_fitting_task.AsyncResult(task_id)
#     return {"status": res.status, "result": res.result}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
