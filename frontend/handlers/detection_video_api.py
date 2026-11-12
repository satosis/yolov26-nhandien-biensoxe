"""Cầu nối HTTP của frontend.routers.detection.video_api: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, UploadFile, File, Form, Depends
from frontend.deps import require_menu
import frontend.routers.detection.video_api as controller


async def analyze_video(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form('fast'),
    roi: str = Form(''),
    user: dict = Depends(require_menu('detection')),
):
    return await controller.analyze_video(request=request, file=file, mode=mode, roi=roi, user=user)


async def current_video_analysis(request: Request, user: dict = Depends(require_menu('detection'))):
    return await controller.current_video_analysis(request=request, user=user)


async def video_analysis_status(
    job_id: str,
    request: Request,
    after: str = '',
    user: dict = Depends(require_menu('detection')),
):
    return await controller.video_analysis_status(job_id=job_id, request=request, after=after, user=user)


async def cancel_video_analysis(
    job_id: str,
    request: Request,
    user: dict = Depends(require_menu('detection')),
):
    return await controller.cancel_video_analysis(job_id=job_id, request=request, user=user)


async def video_analysis_crop(
    job_id: str,
    filename: str,
    request: Request,
    user: dict = Depends(require_menu('detection')),
):
    return await controller.video_analysis_crop(job_id=job_id, filename=filename, request=request, user=user)
