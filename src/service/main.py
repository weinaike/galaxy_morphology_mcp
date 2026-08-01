import asyncio

from fastapi import FastAPI, Body
import uvicorn
import uuid
import requests
from src.service.tasks import do_fitting_task

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

# @app.get("/api/fitting-status/{task_id}")
# def status(task_id: str):
#     res = do_fitting_task.AsyncResult(task_id)
#     return {"status": res.status, "result": res.result}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
