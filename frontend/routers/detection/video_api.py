import re
import json
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from frontend.deps import require_menu

from .model_registry import (
    _get_video_jobs,
    _video_identity,
    _owned_video_job,
    _video_jobs,
)

router = APIRouter()


@router.post("/api/videos", status_code=202)
async def analyze_video(request: Request, file: UploadFile = File(...),
                        mode: str = Form("fast"), roi: str = Form(""),
                        user: dict = Depends(require_menu("detection"))):
    region = None
    try:
        if roi:
            region = json.loads(roi)
            if (not isinstance(region, list) or len(region) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (float, int)) for v in region)
                    or not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1)):
                raise ValueError("Vùng nhận diện không hợp lệ.")
        manager = await run_in_threadpool(_get_video_jobs)
        job = await run_in_threadpool(manager.reserve, *_video_identity(request, user), file.filename,
                                     mode=mode, roi=region)
    except (ValueError, json.JSONDecodeError) as exc:
        await file.close()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        await file.close()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    try:
        size = 0
        with job.source.open("wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Video vượt quá 512 MB. Hãy chia thành đoạn nhỏ hơn.")
                await run_in_threadpool(target.write, chunk)
        if not size:
            raise HTTPException(status_code=400, detail="Video tải lên rỗng.")
        await run_in_threadpool(manager.enqueue, job)
        return await run_in_threadpool(manager.snapshot, job)
    except BaseException:
        await run_in_threadpool(manager.discard, job)
        raise
    finally:
        await file.close()


@router.get("/api/videos/latest")
@router.get("/api/videos/current", include_in_schema=False)
async def current_video_analysis(request: Request,
                                 user: dict = Depends(require_menu("detection"))):
    manager = await run_in_threadpool(_get_video_jobs)
    job = await run_in_threadpool(manager.latest, *_video_identity(request, user))
    snapshot = await run_in_threadpool(manager.snapshot, job) if job is not None else {"job_id": None}
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.get("/api/videos/{job_id}")
async def video_analysis_status(job_id: str, request: Request,
                                after: str = "",
                                user: dict = Depends(require_menu("detection"))):
    manager, job = await run_in_threadpool(_owned_video_job, request, user, job_id)
    snapshot = await run_in_threadpool(manager.snapshot, job)
    if after:
        if not re.fullmatch(r"[0-9a-f]{32}", after):
            raise HTTPException(status_code=400, detail="Mốc sự kiện không hợp lệ.")
        events = snapshot["events"]
        index = next((i for i, event in enumerate(events) if event.get("id") == after), None)
        snapshot["events_total"] = len(events)
        snapshot["events_complete"] = index is None
        if index is not None:
            snapshot["events"] = events[index + 1:]
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.post("/api/videos/{job_id}/cancel")
async def cancel_video_analysis(job_id: str, request: Request,
                                user: dict = Depends(require_menu("detection"))):
    manager, job = await run_in_threadpool(_owned_video_job, request, user, job_id)
    await run_in_threadpool(manager.cancel_job, job)
    return await run_in_threadpool(manager.snapshot, job)


@router.get("/api/videos/{job_id}/assets/{filename}")
async def video_analysis_crop(job_id: str, filename: str, request: Request,
                              user: dict = Depends(require_menu("detection"))):
    _, job = await run_in_threadpool(_owned_video_job, request, user, job_id)
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
        raise HTTPException(status_code=404)
    path = job.directory / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.on_event("shutdown")
def _close_video_jobs():
    global _video_jobs
    if _video_jobs is not None:
        _video_jobs.close()
