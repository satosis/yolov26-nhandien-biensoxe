import asyncio
import logging
from contextlib import closing
from http.client import HTTPConnection
from urllib.parse import urlencode
import requests as http_req
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, Response
from frontend.deps import require_menu
from backend.camera import camera_service as cam_svc
from backend.services.settings_service import get_app_setting

logger = logging.getLogger(__name__)

router = APIRouter()


def _request_go2rtc_webrtc(params: dict, sdp_offer: str) -> tuple[int, str]:
    target = "/api/webrtc?" + urlencode(params)
    with closing(HTTPConnection("127.0.0.1", 1984, timeout=10)) as connection:
        connection.request(
            "POST", target, body=sdp_offer.encode("utf-8"),
            headers={"Content-Type": "application/sdp"},
        )
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8", errors="replace")


@router.post("/go2rtc-proxy/{camera_id}")
async def go2rtc_webrtc_proxy(camera_id: int, request: Request, user: dict = Depends(require_menu("detection"))):
    sdp_offer = (await request.body()).decode("utf-8", errors="replace")
    stream_name = f"cam_{camera_id}"

    cam = cam_svc.get_camera(camera_id)
    if not cam:
        logger.warning("go2rtc proxy: camera_id=%s was not found", camera_id)
        return JSONResponse(
            {"ok": False, "message": "Camera không tồn tại."},
            status_code=404,
        )
    if not cam.get("rtsp_url"):
        logger.warning("go2rtc proxy: camera_id=%s has no RTSP URL", camera_id)
        return JSONResponse(
            {"ok": False, "message": "Camera chưa có luồng RTSP."},
            status_code=400,
        )
    registration = http_req.put(
        "http://localhost:1984/api/streams",
        json={stream_name: cam["rtsp_url"]},
        timeout=3,
    )
    if not registration.ok:
        logger.error("go2rtc stream registration failed for camera_id=%s (status=%s)",
                     camera_id, registration.status_code)
        return JSONResponse(
            {"ok": False, "message": "Không thể đăng ký luồng camera với go2rtc."},
            status_code=502,
        )

    params = {"src": stream_name, "video": "h264/baseline", "audio": "opus"}

    turn_url = get_app_setting("WEBRTC_TURN_URL", "")
    turn_user = get_app_setting("WEBRTC_TURN_USERNAME", "")
    turn_pass = get_app_setting("WEBRTC_TURN_PASSWORD", "")
    if turn_url:
        ice_param = turn_url
        if turn_user and turn_pass:
            ice_param = f"{turn_url}:{turn_user}:{turn_pass}"
        params["ice"] = ice_param

    status_code, response_body = await asyncio.to_thread(_request_go2rtc_webrtc, params, sdp_offer)
    if status_code == 200:
        return Response(content=response_body, media_type="application/sdp")
    logger.error("go2rtc WebRTC negotiation failed for camera_id=%s (status=%s)",
                 camera_id, status_code)
    return JSONResponse(
        {"ok": False, "message": "go2rtc không thể thiết lập WebRTC."},
        status_code=502,
    )
