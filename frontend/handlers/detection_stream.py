"""Cầu nối HTTP của frontend.routers.detection.stream: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends
from frontend.deps import require_menu
import frontend.routers.detection.stream as controller


async def stream_camera(camera_id: int, request: Request, user: dict = Depends(require_menu('detection'))):
    return await controller.stream_camera(camera_id=camera_id, request=request, user=user)
