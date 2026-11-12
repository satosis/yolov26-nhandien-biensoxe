"""Cầu nối HTTP của frontend.routers.cameras_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, Body
from frontend.deps import require_menu
from typing import Optional
import frontend.routers.cameras_controller as controller


async def cameras_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('cameras')),
):
    return await controller.cameras_page(request=request, page=page, user=user)


async def create_camera(
    request: Request,
    user: dict = Depends(require_menu('cameras')),
    camera_name: str = Form(...),
    camera_type: str = Form(...),
    imou_device_id: str = Form(''),
    imou_channel_id: str = Form('0'),
    camera_ip: str = Form(''),
    camera_port: int = Form(554),
    rtsp_url: str = Form(''),
    imou_mk: str = Form(''),
    site_ids: Optional[list[str]] = Form(None),
):
    return await controller.create_camera(request=request, user=user, camera_name=camera_name, camera_type=camera_type, imou_device_id=imou_device_id, imou_channel_id=imou_channel_id, camera_ip=camera_ip, camera_port=camera_port, rtsp_url=rtsp_url, imou_mk=imou_mk, site_ids=site_ids)


async def delete_camera(camera_id: int, user: dict = Depends(require_menu('cameras'))):
    return await controller.delete_camera(camera_id=camera_id, user=user)


async def toggle_camera(camera_id: int, user: dict = Depends(require_menu('cameras'))):
    return await controller.toggle_camera(camera_id=camera_id, user=user)


async def edit_camera(
    camera_id: int,
    user: dict = Depends(require_menu('cameras')),
    camera_name: str = Form(...),
    camera_type: str = Form(...),
    imou_device_id: str = Form(''),
    imou_channel_id: str = Form('0'),
    camera_ip: str = Form(''),
    camera_port: Optional[int] = Form(None),
    rtsp_url: str = Form(''),
    imou_mk: str = Form(''),
    site_ids: Optional[list[str]] = Form(None),
):
    return await controller.edit_camera(camera_id=camera_id, user=user, camera_name=camera_name, camera_type=camera_type, imou_device_id=imou_device_id, imou_channel_id=imou_channel_id, camera_ip=camera_ip, camera_port=camera_port, rtsp_url=rtsp_url, imou_mk=imou_mk, site_ids=site_ids)


async def start_stream(camera_id: int, user: dict = Depends(require_menu('detection'))):
    return await controller.start_stream(camera_id=camera_id, user=user)


async def stop_stream(camera_id: int, user: dict = Depends(require_menu('detection'))):
    return await controller.stop_stream(camera_id=camera_id, user=user)


async def get_stream_status(camera_id: int, user: dict = Depends(require_menu('detection'))):
    return await controller.get_stream_status(camera_id=camera_id, user=user)


async def discover_cameras(user: dict = Depends(require_menu('cameras'))):
    return await controller.discover_cameras(user=user)


async def auto_add_camera(
    data: controller.AutoAddCameraInput = Body(...),
    user: dict = Depends(require_menu('cameras')),
):
    return await controller.auto_add_camera(data=data, user=user)


async def probe_onvif(
    data: controller.ProbeOnvifInput = Body(...),
    user: dict = Depends(require_menu('cameras')),
):
    return await controller.probe_onvif(data=data, user=user)


async def camera_health(user: dict = Depends(require_menu('cameras')), hours: int = 24):
    return await controller.camera_health(user=user, hours=hours)
