import json
import asyncio
from datetime import datetime
import logging
import re
from typing import Literal, Optional
from urllib.parse import urlparse
import tinytuya
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from backend.iot.door_controller import DoorController
from backend.iot.door_config import canonical_relay_config, relay_config_error, with_tuya_defaults
from backend.iot.hunonic_handler import HunonicHandler
from backend.services.settings_service import get_app_setting, set_many_app_settings
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.routers.door_providers_queries import get_all_door_providers
import frontend.routers.rolling_doors_queries as queries

logger = logging.getLogger("rolling_doors_router")

_OPTIONAL_POSITIVE_INT_PATTERN = r"^\s*(?:[1-9]\d{0,9})?\s*$"
_IPV4_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}\Z")
_MAX_DB_INTEGER = 2_147_483_647


def _optional_form_number(value: str | None) -> int | None:
    return int(value.strip()) if value and value.strip() else None


def _tuya_devices(raw: object) -> list[dict]:
    if not isinstance(raw, dict):
        return []
    devices = []
    for ip, item in raw.items():
        if not isinstance(ip, str) or not isinstance(item, dict) or _IPV4_RE.fullmatch(ip) is None:
            continue
        device_id = str(item.get("gwId") or item.get("id") or "").strip()
        if not device_id:
            continue
        devices.append({
            "ip": ip,
            "device_id": device_id,
            "name": str(item.get("name") or item.get("productKey") or "Tuya Device"),
            "version": str(item.get("version") or ""),
        })
    return devices


async def scan_rolling_door_devices(user: dict):
    try:
        raw = await asyncio.to_thread(
            tinytuya.deviceScan,
            verbose=False,
            maxretry=3,
            color=False,
            poll=False,
            forcescan=False,
        )
    except OSError as exc:  # không lấy được IP/mạng nội bộ
        logger.warning("Rolling door scan network error: %s", exc)
        return JSONResponse({"ok": False, "message": f"Không quét được thiết bị trong mạng nội bộ: {exc}"})
    return {"ok": True, "status": "success", "devices": _tuya_devices(raw)}


async def hunonic_devices(phone: str, password: str, user: dict):
    if not phone.strip():
        return JSONResponse({"ok": False, "message": "Thiếu tài khoản Hunonic."}, status_code=422)
    if urlparse(HunonicHandler.API_BASE).scheme != "https":
        logger.error("Hunonic discovery is unavailable because its API endpoint is not HTTPS")
        return JSONResponse(
            {"ok": False, "message": "Chưa thể dò thiết bị Hunonic qua kết nối an toàn."},
            status_code=503,
        )
    devices = await asyncio.to_thread(HunonicHandler(phone.strip(), password).get_devices)
    return {"ok": True, "status": "success", "devices": devices}


def count_rolling_doors() -> int:
    row = queries.count_rolling_doors_select_rolling_doors()
    return int((row or {}).get("total") or 0)


def get_rolling_door(door_id: int) -> dict | None:
    return queries.get_rolling_door_select_rolling_doors(door_id)


def upsert_rolling_door_record(**data):
    door_id = data.pop('id', None)
    if door_id:
        return queries.upsert_rolling_door_record_update_rolling_doors(data, door_id)
    created = queries.upsert_rolling_door_record_insert_rolling_doors(data)
    if not created:
        logger.error("Rolling door insert affected no rows")
    return created


def delete_rolling_door_record(door_id: int):
    return queries.delete_rolling_door_record_delete_rolling_doors(door_id)


async def rolling_doors_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rolling_doors"
    
    ctx["global_tuya_device_id"] = get_app_setting("TUYA_DEVICE_ID", "")
    ctx["global_tuya_local_key"] = get_app_setting("TUYA_LOCAL_KEY", "")
    ctx["global_tuya_ip"] = get_app_setting("TUYA_IP", "")
    
    total_doors = count_rolling_doors()
    page_data = paginate_counts(total_doors, page=page, per_page=20, base_url="/admin/rolling_doors")
    doors = queries.get_all_rolling_doors(
        limit=page_data["per_page"], offset=(page_data["page"] - 1) * page_data["per_page"],
    ) or []
    suppliers = get_all_door_providers()
    sites = queries.select_sites()
    
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
    ctx.update(page=page_data["page"], total_pages=page_data["total_pages"],
               total=page_data["total"], page_base_url=page_data["page_base_url"])
    ctx["suppliers"] = suppliers
    ctx["sites"] = sites
    ctx["cameras"] = queries.select_cameras()

    return templates.TemplateResponse("pages/rolling_doors.html", {"request": request, **ctx})


async def update_tuya_global(device_id: str, local_key: str, ip: str, _: dict):
    saved = set_many_app_settings({
        "TUYA_DEVICE_ID": device_id.strip(),
        "TUYA_LOCAL_KEY": local_key.strip(),
        "TUYA_IP": ip.strip(),
    })
    if not saved:
        return HTMLResponse("Không thể lưu cấu hình Tuya.", status_code=500)
    
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


async def upsert_rolling_door(
    door_id: Optional[str],
    name: str,
    supplier_key: Optional[str],
    site_id: int,
    relay_type: Literal['gpio', 'tasmota', 'http', 'tuya_local', 'hunonic_cloud'],
    relay_ip: Optional[str],
    relay_port: Optional[str],
    relay_id: Optional[str],
    relay_key: Optional[str],
    relay_pin: Optional[str],
    hunonic_phone: Optional[str],
    hunonic_password: Optional[str],
    hunonic_device_id: Optional[str],
    is_active: Optional[str],
    camera_id: Optional[str],
    auto_close_enabled: Optional[str],
    auto_close_delay_sec: Optional[int],
    _: dict,
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
    relay_config = canonical_relay_config(relay_type, relay_config)
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
        return HTMLResponse("Không thể lưu cửa cuốn.", status_code=500)
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


async def edit_rolling_door_page(door_id: int, request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rolling_doors"
    
    door = get_rolling_door(door_id)
    if not door:
        return HTMLResponse("Không tìm thấy cửa cuốn.", status_code=404)
        
    suppliers = get_all_door_providers()
    sites = queries.select_sites()
    
    conf = door.get('relay_config')
    if isinstance(conf, str):
        door['relay_config'] = json.loads(conf)
            
    ctx["door"] = door
    ctx["suppliers"] = suppliers
    ctx["sites"] = sites
    ctx["cameras"] = queries.select_cameras()
    
    return templates.TemplateResponse("pages/rolling_doors.html", {"request": request, **ctx})


async def delete_rolling_door(door_id: int, _: dict):
    if not delete_rolling_door_record(door_id):
        return HTMLResponse("Không tìm thấy cửa cuốn.", status_code=404)
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


async def control_rolling_door(door_id: int, action: Literal['OPEN', 'CLOSE'], _: dict):
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
    relay_config = canonical_relay_config(relay_type, relay_config)

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
