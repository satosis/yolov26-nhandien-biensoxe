import json
import asyncio
from datetime import datetime
import logging
import re
from typing import Literal, Optional
from urllib.parse import urlparse
import tinytuya
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from backend.services.database import db
from backend.iot.door_controller import DoorController
from backend.iot.door_config import normalize_relay_config, relay_config_error, with_tuya_defaults
from backend.iot.hunonic_handler import HunonicHandler
from backend.services.settings_service import get_app_setting, set_many_app_settings
from frontend.deps import get_menu_context, require_menu, templates
from frontend.routers.door_providers_router import get_all_door_providers

logger = logging.getLogger("rolling_doors_router")

router = APIRouter(prefix="/rolling_doors")
_OPTIONAL_POSITIVE_INT_PATTERN = r"^\s*(?:[1-9]\d{0,9})?\s*$"
_IPV4_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}\Z")
_MAX_DB_INTEGER = 2_147_483_647


def _optional_form_number(value: str | None) -> int | None:
    return int(value.strip()) if value and value.strip() else None


def _tuya_devices(raw: object) -> list[dict]:
    if not isinstance(raw, dict):
        logger.error("Tuya discovery returned a non-object response")
        raise ValueError("Tuya discovery response must be an object")
    devices = []
    invalid = 0
    for ip, item in raw.items():
        device_id = (item.get("gwId") or item.get("id")) if isinstance(item, dict) else None
        if (
            not isinstance(ip, str) or _IPV4_RE.fullmatch(ip) is None
            or any(int(part) > 255 for part in ip.split("."))
            or not isinstance(device_id, str) or not device_id.strip()
        ):
            invalid += 1
            continue
        name = item.get("name") or item.get("productKey") or "Tuya Device"
        version = item.get("version") or ""
        devices.append({
            "ip": ip,
            "device_id": device_id.strip(),
            "name": name if isinstance(name, str) else "Tuya Device",
            "version": str(version) if isinstance(version, (str, int, float)) else "",
        })
    if invalid:
        logger.warning("Tuya discovery skipped %s malformed device records", invalid)
    return devices


@router.post("/scan")
async def scan_rolling_door_devices(user: dict = Depends(require_menu("rolling_doors"))):
    raw = await asyncio.to_thread(
        tinytuya.deviceScan,
        verbose=False,
        maxretry=3,
        color=False,
        poll=False,
        forcescan=False,
    )
    return {"ok": True, "status": "success", "devices": _tuya_devices(raw)}


@router.post("/hunonic/devices")
async def hunonic_devices(
    phone: str = Form(..., min_length=1),
    password: str = Form(..., min_length=1),
    user: dict = Depends(require_menu("rolling_doors")),
):
    if not phone.strip() or not password:
        return JSONResponse({"ok": False, "message": "Thiếu tài khoản Hunonic."}, status_code=422)
    if urlparse(HunonicHandler.API_BASE).scheme != "https":
        logger.error("Hunonic discovery is unavailable because its API endpoint is not HTTPS")
        return JSONResponse(
            {"ok": False, "message": "Chưa thể dò thiết bị Hunonic qua kết nối an toàn."},
            status_code=503,
        )
    devices = await asyncio.to_thread(HunonicHandler(phone.strip(), password).get_devices)
    return {"ok": True, "status": "success", "devices": devices}


def get_all_rolling_doors() -> list:
    query = """
        SELECT rd.*, dp.name as supplier_name, s.site_name 
        FROM rolling_doors rd
        LEFT JOIN door_providers dp ON rd.supplier_key = dp.provider_key
        LEFT JOIN sites s ON rd.site_id = s.id
        ORDER BY dp.name ASC, rd.name ASC
    """
    return db.execute_query(query, fetchall=True, commit=False)


def get_rolling_door(door_id: int) -> dict | None:
    return db.select('rolling_doors', conditions={'id': door_id}, fetchall=False)


def upsert_rolling_door_record(**data):
    door_id = data.pop('id', None)
    if door_id:
        return db.update('rolling_doors', data, {'id': door_id})
    return db.insert('rolling_doors', data)


def delete_rolling_door_record(door_id: int):
    return db.delete('rolling_doors', {'id': door_id})


@router.get("")
async def rolling_doors_page(request: Request, user: dict = Depends(require_menu("rolling_doors"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rolling_doors"
    
    ctx["global_tuya_device_id"] = get_app_setting("TUYA_DEVICE_ID", "")
    ctx["global_tuya_local_key"] = get_app_setting("TUYA_LOCAL_KEY", "")
    ctx["global_tuya_ip"] = get_app_setting("TUYA_IP", "")
    
    doors = get_all_rolling_doors()
    suppliers = get_all_door_providers()
    sites = db.select('sites', order_by='site_name ASC')
    
    grouped_doors = {}
    for d in doors:
        s_name = d.get('supplier_name') or 'CÁ NHÂN (TỰ ĐỊNH NGHĨA)'
        if s_name not in grouped_doors:
            grouped_doors[s_name] = []
        
        if isinstance(d.get('relay_config'), str):
            d['relay_config'] = json.loads(d['relay_config'])
        
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        
        grouped_doors[s_name].append(d)
    
    ctx["grouped_doors"] = grouped_doors
    ctx["suppliers"] = suppliers
    ctx["sites"] = sites
    ctx["cameras"] = db.select('cameras', order_by='camera_name ASC')

    return templates.TemplateResponse("pages/rolling_doors.html", {"request": request, **ctx})


@router.post("/tuya_global")
async def update_tuya_global(
    request: Request,
    device_id: str = Form(""),
    local_key: str = Form(""),
    ip: str = Form(""),
    user: dict = Depends(require_menu("rolling_doors")),
):
    saved = set_many_app_settings({
        "TUYA_DEVICE_ID": device_id.strip(),
        "TUYA_LOCAL_KEY": local_key.strip(),
        "TUYA_IP": ip.strip(),
    })
    if not saved:
        logger.error("Could not save global Tuya settings")
        return HTMLResponse("Không thể lưu cấu hình Tuya.", status_code=500)
    
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("")
async def upsert_rolling_door(
    request: Request,
    door_id: Optional[str] = Form(None, pattern=_OPTIONAL_POSITIVE_INT_PATTERN),
    name: str = Form(...),
    supplier_key: Optional[str] = Form(None),
    site_id: int = Form(..., gt=0),
    relay_type: Literal["gpio", "tasmota", "http", "tuya_local", "hunonic_cloud"] = Form("http"),
    relay_ip: Optional[str] = Form(None),
    relay_port: Optional[str] = Form(None, pattern=_OPTIONAL_POSITIVE_INT_PATTERN),
    relay_id: Optional[str] = Form(None),
    relay_key: Optional[str] = Form(None),
    relay_pin: Optional[str] = Form(None, pattern=_OPTIONAL_POSITIVE_INT_PATTERN),
    hunonic_phone: Optional[str] = Form(None),
    hunonic_password: Optional[str] = Form(None),
    hunonic_device_id: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    camera_id: Optional[str] = Form(None, pattern=_OPTIONAL_POSITIVE_INT_PATTERN),
    auto_close_enabled: Optional[str] = Form(None),
    auto_close_delay_sec: Optional[int] = Form(300, gt=0),
    user: dict = Depends(require_menu("rolling_doors")),
):
    final_door_id = _optional_form_number(door_id)
    final_port = _optional_form_number(relay_port)
    final_pin = _optional_form_number(relay_pin)
    final_camera_id = _optional_form_number(camera_id)
    if any(
        value is not None and value > _MAX_DB_INTEGER
        for value in (final_door_id, final_pin, final_camera_id)
    ):
        return HTMLResponse("ID hoặc chân relay không hợp lệ.", status_code=422)
    if final_port is not None and final_port > 65535:
        return HTMLResponse("Cổng relay không hợp lệ.", status_code=422)

    if relay_type == "hunonic_cloud":
        relay_config = {
            "phone": hunonic_phone,
            "password": hunonic_password,
            "device_id": hunonic_device_id
        }
    else:
        relay_config = {
            "ip": relay_ip,
            "port": final_port,
            "device_id": relay_id,
            "local_key": relay_key,
            "pin": final_pin
        }
    relay_config = {k: v for k, v in relay_config.items() if v is not None and str(v).strip()}
    explicit_config = relay_config
    if relay_type == "tuya_local":
        relay_config = with_tuya_defaults(relay_type, relay_config, {
            "device_id": get_app_setting("TUYA_DEVICE_ID", ""),
            "local_key": get_app_setting("TUYA_LOCAL_KEY", ""),
            "ip": get_app_setting("TUYA_IP", ""),
        })
    config_error = relay_config_error(relay_type, relay_config)
    if config_error:
        return HTMLResponse(f"Cấu hình relay không hợp lệ: {config_error}.", status_code=422)
    relay_config = normalize_relay_config(relay_type, relay_config)
    if relay_type == "tuya_local":
        relay_config = {key: relay_config[key] for key in explicit_config if key in relay_config}
    
    saved = upsert_rolling_door_record(
        id=final_door_id,
        name=name,
        supplier_key=supplier_key,
        site_id=site_id,
        relay_type=relay_type,
        relay_config=json.dumps(relay_config),
        is_active=bool(is_active),
        camera_id=final_camera_id,
        auto_close_enabled=bool(auto_close_enabled),
        auto_close_delay_sec=auto_close_delay_sec or 300,
        updated_at=datetime.now()
    )

    if not saved:
        if final_door_id is not None:
            return HTMLResponse("Không tìm thấy cửa cuốn.", status_code=404)
        logger.error("Could not create rolling door record")
        return HTMLResponse("Không thể lưu cửa cuốn.", status_code=500)
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.get("/{door_id}/edit")
async def edit_rolling_door_page(
    door_id: int,
    request: Request,
    user: dict = Depends(require_menu("rolling_doors")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rolling_doors"
    
    door = get_rolling_door(door_id)
    if not door:
        return HTMLResponse("Không tìm thấy cửa cuốn.", status_code=404)
        
    suppliers = get_all_door_providers()
    sites = db.select('sites', order_by='site_name ASC')
    
    conf = door.get('relay_config')
    if isinstance(conf, str):
        door['relay_config'] = json.loads(conf)
            
    ctx["door"] = door
    ctx["suppliers"] = suppliers
    ctx["sites"] = sites
    ctx["cameras"] = db.select('cameras', order_by='camera_name ASC')
    
    return templates.TemplateResponse("pages/rolling_doors.html", {"request": request, **ctx})


@router.post("/{door_id}/delete")
async def delete_rolling_door(
    door_id: int,
    request: Request,
    user: dict = Depends(require_menu("rolling_doors")),
):
    if not delete_rolling_door_record(door_id):
        return HTMLResponse("Không tìm thấy cửa cuốn.", status_code=404)
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("/{door_id}/control")
async def control_rolling_door(
    door_id: int,
    action: Literal["OPEN", "CLOSE"] = Form(...),
    user: dict = Depends(require_menu("rolling_doors")),
):
    door = get_rolling_door(door_id)
    if not door:
        return JSONResponse(
            {"ok": False, "status": "error", "message": "Door not found"},
            status_code=404,
        )
    
    conf = door.get('relay_config')
    relay_config = json.loads(conf) if isinstance(conf, str) else conf or {}
    relay_type = door.get('relay_type')
    if relay_type == "tuya_local":
        relay_config = with_tuya_defaults(relay_type, relay_config, {
            "device_id": get_app_setting("TUYA_DEVICE_ID", ""),
            "local_key": get_app_setting("TUYA_LOCAL_KEY", ""),
            "ip": get_app_setting("TUYA_IP", ""),
        })
    config_error = relay_config_error(relay_type, relay_config)
    if config_error:
        return JSONResponse(
            {"ok": False, "status": "error", "message": f"Cấu hình relay không hợp lệ: {config_error}."},
            status_code=422,
        )
    relay_config = normalize_relay_config(relay_type, relay_config)

    controller = DoorController()
    success = controller.control_door(
        action=action,
        relay_type=relay_type,
        relay_config=relay_config
    )
    
    if success:
        return {"ok": True, "status": "success", "message": f"Command {action} sent"}
    return JSONResponse(
        {"ok": False, "status": "error", "message": f"Failed to send command {action}"},
        status_code=502,
    )
