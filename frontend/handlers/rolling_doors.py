"""Cầu nối HTTP của frontend.routers.rolling_doors_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Depends, Form, Request, Query
from frontend.deps import require_menu
from typing import Optional, Literal
import frontend.routers.rolling_doors_controller as controller


async def scan_rolling_door_devices(user: dict = Depends(require_menu('rolling_doors'))):
    return await controller.scan_rolling_door_devices(user=user)


async def hunonic_devices(
    phone: str = Form(..., min_length=1),
    password: str = Form(..., min_length=1),
    user: dict = Depends(require_menu('rolling_doors')),
):
    return await controller.hunonic_devices(phone=phone, password=password, user=user)


async def rolling_doors_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('rolling_doors')),
):
    return await controller.rolling_doors_page(request=request, page=page, user=user)


async def update_tuya_global(
    device_id: str = Form(''),
    local_key: str = Form(''),
    ip: str = Form(''),
    _: dict = Depends(require_menu('rolling_doors')),
):
    return await controller.update_tuya_global(device_id=device_id, local_key=local_key, ip=ip, _=_)


async def upsert_rolling_door(
    door_id: Optional[str] = Form(None, pattern=controller._OPTIONAL_POSITIVE_INT_PATTERN),
    name: str = Form(...),
    supplier_key: Optional[str] = Form(None),
    site_id: int = Form(..., gt=0),
    relay_type: Literal['gpio', 'tasmota', 'http', 'tuya_local', 'hunonic_cloud'] = Form('http'),
    relay_ip: Optional[str] = Form(None),
    relay_port: Optional[str] = Form(None, pattern=controller._OPTIONAL_POSITIVE_INT_PATTERN),
    relay_id: Optional[str] = Form(None),
    relay_key: Optional[str] = Form(None),
    relay_pin: Optional[str] = Form(None, pattern=controller._OPTIONAL_POSITIVE_INT_PATTERN),
    hunonic_phone: Optional[str] = Form(None),
    hunonic_password: Optional[str] = Form(None),
    hunonic_device_id: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    camera_id: Optional[str] = Form(None, pattern=controller._OPTIONAL_POSITIVE_INT_PATTERN),
    auto_close_enabled: Optional[str] = Form(None),
    auto_close_delay_sec: Optional[int] = Form(300, gt=0),
    _: dict = Depends(require_menu('rolling_doors')),
):
    return await controller.upsert_rolling_door(door_id=door_id, name=name, supplier_key=supplier_key, site_id=site_id, relay_type=relay_type, relay_ip=relay_ip, relay_port=relay_port, relay_id=relay_id, relay_key=relay_key, relay_pin=relay_pin, hunonic_phone=hunonic_phone, hunonic_password=hunonic_password, hunonic_device_id=hunonic_device_id, is_active=is_active, camera_id=camera_id, auto_close_enabled=auto_close_enabled, auto_close_delay_sec=auto_close_delay_sec, _=_)


async def edit_rolling_door_page(
    door_id: int,
    request: Request,
    user: dict = Depends(require_menu('rolling_doors')),
):
    return await controller.edit_rolling_door_page(door_id=door_id, request=request, user=user)


async def delete_rolling_door(door_id: int, _: dict = Depends(require_menu('rolling_doors'))):
    return await controller.delete_rolling_door(door_id=door_id, _=_)


async def control_rolling_door(
    door_id: int,
    action: Literal['OPEN', 'CLOSE'] = Form(...),
    _: dict = Depends(require_menu('rolling_doors')),
):
    return await controller.control_rolling_door(door_id=door_id, action=action, _=_)
