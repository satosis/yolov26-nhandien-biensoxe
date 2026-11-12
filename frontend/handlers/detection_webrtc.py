"""Cầu nối HTTP của frontend.routers.detection.webrtc: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends
from frontend.deps import require_menu
import frontend.routers.detection.webrtc as controller


async def go2rtc_webrtc_proxy(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu('detection')),
):
    return await controller.go2rtc_webrtc_proxy(camera_id=camera_id, request=request, user=user)
