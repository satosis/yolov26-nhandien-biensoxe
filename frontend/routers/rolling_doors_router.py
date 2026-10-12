import json
from datetime import datetime
import logging
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from backend.services.database import db
from backend.iot.door_controller import DoorController
from backend.services.settings_service import get_app_setting, set_app_setting
from frontend.deps import get_menu_context, require_menu, templates
from frontend.routers.door_providers_router import get_all_door_providers

logger = logging.getLogger("rolling_doors_router")

router = APIRouter(prefix="/rolling_doors")


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
    set_app_setting("TUYA_DEVICE_ID", device_id.strip())
    set_app_setting("TUYA_LOCAL_KEY", local_key.strip())
    set_app_setting("TUYA_IP", ip.strip())
    
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("")
async def upsert_rolling_door(
    request: Request,
    door_id: Optional[str] = Form(None),
    name: str = Form(...),
    supplier_key: Optional[str] = Form(None),
    site_id: int = Form(...),
    relay_type: str = Form("http"),
    relay_ip: Optional[str] = Form(None),
    relay_port: Optional[str] = Form(None),
    relay_id: Optional[str] = Form(None),
    relay_key: Optional[str] = Form(None),
    relay_pin: Optional[str] = Form(None),
    hunonic_phone: Optional[str] = Form(None),
    hunonic_password: Optional[str] = Form(None),
    hunonic_device_id: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    camera_id: Optional[str] = Form(None),
    auto_close_enabled: Optional[str] = Form(None),
    auto_close_delay_sec: Optional[int] = Form(300),
    user: dict = Depends(require_menu("rolling_doors")),
):
    if relay_type == "hunonic_cloud":
        relay_config = {
            "phone": hunonic_phone,
            "password": hunonic_password,
            "device_id": hunonic_device_id
        }
    else:
        safe_port = int(relay_port) if relay_port and relay_port.strip() else None
        safe_pin = int(relay_pin) if relay_pin and relay_pin.strip() else None

        relay_config = {
            "ip": relay_ip,
            "port": safe_port,
            "device_id": relay_id,
            "local_key": relay_key,
            "pin": safe_pin
        }
    
    relay_config = {k: v for k, v in relay_config.items() if v is not None and str(v).strip() != ""}
    
    final_id = int(door_id) if door_id and str(door_id).strip() else None
    final_camera_id = int(camera_id) if camera_id and str(camera_id).strip() else None

    upsert_rolling_door_record(
        id=final_id,
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
        return RedirectResponse(url="/admin/rolling_doors", status_code=303)
        
    suppliers = get_all_door_providers()
    sites = db.select('sites', order_by='site_name ASC')
    
    conf = door.get('relay_config')
    if isinstance(conf, str):
        try:
            door['relay_config'] = json.loads(conf)
        except Exception:
            door['relay_config'] = {}
            
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
    delete_rolling_door_record(door_id)
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("/{door_id}/control")
async def control_rolling_door(
    door_id: int,
    action: str = Form(...),
    user: dict = Depends(require_menu("rolling_doors")),
):
    door = get_rolling_door(door_id)
    if not door:
        return {"status": "error", "message": "Door not found"}
    
    conf = door.get('relay_config')
    relay_config = json.loads(conf) if isinstance(conf, str) else conf or {}

    controller = DoorController()
    success = controller.control_door(
        action=action.upper(),
        relay_type=door.get('relay_type'),
        relay_config=relay_config
    )
    
    if success:
        return {"status": "success", "message": f"Command {action} sent"}
    else:
        return {"status": "error", "message": f"Failed to send command {action}"}
