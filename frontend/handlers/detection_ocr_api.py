"""Cầu nối HTTP của frontend.routers.detection.ocr_api: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, UploadFile, File, Form, Depends
from frontend.deps import require_menu
import frontend.routers.detection.ocr_api as controller


async def detect_image(
    request: Request,
    file: UploadFile | None = File(None),
    source: str = Form('webcam'),
    stream_id: str = Form(''),
    camera_id: int = Form(0),
    video_time: float = Form(-1),
    finish_tracks: bool = Form(False),
    client_info: str = Form(''),
    user: dict = Depends(require_menu('detection')),
):
    return await controller.detect_image(request=request, file=file, source=source, stream_id=stream_id, camera_id=camera_id, video_time=video_time, finish_tracks=finish_tracks, user=user, client_info=client_info)


async def reset_tracker(user: dict = Depends(require_menu('detection'))):
    return await controller.reset_tracker(user=user)
