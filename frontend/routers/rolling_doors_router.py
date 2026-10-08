import json
from datetime import datetime
import logging
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from backend.iot.door_controller import DoorController
from backend.services.settings_service import get_app_setting, set_app_setting
from frontend.deps import get_db, get_menu_context, require_menu
from frontend.routers.door_providers_router import get_all_door_providers

logger = logging.getLogger("rolling_doors_router")

router = APIRouter(prefix="/rolling_doors")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.add_extension('jinja2.ext.do')


def get_all_rolling_doors(db) -> list:
    """Lấy danh sách tất cả cửa cuốn cùng tên nhà cung cấp và cơ sở."""
    query = """
        SELECT rd.*, dp.name as supplier_name, s.site_name 
        FROM rolling_doors rd
        LEFT JOIN door_providers dp ON rd.supplier_key = dp.provider_key
        LEFT JOIN sites s ON rd.site_id = s.id
        ORDER BY dp.name ASC, rd.name ASC
    """
    return db.execute_query(query, fetchall=True, commit=False) or []


def get_rolling_door(db, door_id: int) -> dict | None:
    """Lấy thông tin một cửa cuốn theo ID."""
    return db.select('rolling_doors', conditions={'id': door_id}, fetchall=False)


def upsert_rolling_door_record(db, **data):
    """Thêm mới hoặc cập nhật thông tin cửa cuốn."""
    door_id = data.pop('id', None)
    if door_id:
        return db.update('rolling_doors', data, {'id': door_id})
    return db.insert('rolling_doors', data)


def delete_rolling_door_record(db, door_id: int):
    """Xóa cửa cuốn theo ID."""
    return db.delete('rolling_doors', {'id': door_id})


@router.get("")
async def rolling_doors_page(request: Request, user: dict = Depends(require_menu("rolling_doors"))):
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rolling_doors"
    
    # Global Tuya settings
    ctx["global_tuya_device_id"] = get_app_setting(db, "TUYA_DEVICE_ID", "")
    ctx["global_tuya_local_key"] = get_app_setting(db, "TUYA_LOCAL_KEY", "")
    ctx["global_tuya_ip"] = get_app_setting(db, "TUYA_IP", "")
    
    # Fetch data
    doors = get_all_rolling_doors(db)
    suppliers = get_all_door_providers(db)
    sites = db.select('sites', order_by='site_name ASC') or []
    
    # Group doors by supplier NAME for cleaner UI grouping
    grouped_doors = {}
    for d in doors:
        s_name = d.get('supplier_name') or 'CÁ NHÂN (TỰ ĐỊNH NGHĨA)'
        if s_name not in grouped_doors:
            grouped_doors[s_name] = []
        
        # Process relay_config
        if isinstance(d.get('relay_config'), str):
            try:
                d['relay_config'] = json.loads(d['relay_config'])
            except Exception:
                d['relay_config'] = {}
        
        # Sanitize for JSON (datetime issues)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        
        grouped_doors[s_name].append(d)
    
    ctx["grouped_doors"] = grouped_doors
    ctx["suppliers"] = suppliers
    ctx["sites"] = sites
    ctx["cameras"] = db.select('cameras', order_by='camera_name ASC') or []

    return templates.TemplateResponse("pages/rolling_doors.html", {"request": request, **ctx})


@router.post("/tuya_global")
async def update_tuya_global(
    request: Request,
    device_id: str = Form(""),
    local_key: str = Form(""),
    ip: str = Form(""),
    user: dict = Depends(require_menu("rolling_doors")),
):
    db = get_db()
    set_app_setting(db, "TUYA_DEVICE_ID", device_id.strip())
    set_app_setting(db, "TUYA_LOCAL_KEY", local_key.strip())
    set_app_setting(db, "TUYA_IP", ip.strip())
    
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("")
async def upsert_rolling_door(
    request: Request,
    door_id: Optional[str] = Form(None),
    name: str = Form(...),
    supplier_key: Optional[str] = Form(None),
    site_id: int = Form(...),
    relay_type: str = Form("http"),
    # Collect generic relay config
    relay_ip: Optional[str] = Form(None),
    relay_port: Optional[str] = Form(None),
    relay_id: Optional[str] = Form(None),
    relay_key: Optional[str] = Form(None),
    relay_pin: Optional[str] = Form(None),
    # Hunonic specific
    hunonic_phone: Optional[str] = Form(None),
    hunonic_password: Optional[str] = Form(None),
    hunonic_device_id: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    camera_id: Optional[str] = Form(None),
    auto_close_enabled: Optional[str] = Form(None),
    auto_close_delay_sec: Optional[int] = Form(300),
    user: dict = Depends(require_menu("rolling_doors")),
):
    db = get_db()
    
    if relay_type == "hunonic_cloud":
        relay_config = {
            "phone": hunonic_phone,
            "password": hunonic_password,
            "device_id": hunonic_device_id
        }
    else:
        # Convert numeric fields safely
        safe_port = None
        try: safe_port = int(relay_port) if relay_port and str(relay_port).strip() else None
        except Exception: pass
        
        safe_pin = None
        try: safe_pin = int(relay_pin) if relay_pin and str(relay_pin).strip() else None
        except Exception: pass

        relay_config = {
            "ip": relay_ip,
            "port": safe_port,
            "device_id": relay_id,
            "local_key": relay_key,
            "pin": safe_pin
        }
    
    # Filter out None and empty strings
    relay_config = {k: v for k, v in relay_config.items() if v is not None and str(v).strip() != ""}
    
    # Safe ID conversion
    final_id = int(door_id) if door_id and str(door_id).strip() else None
    final_camera_id = int(camera_id) if camera_id and str(camera_id).strip() else None

    upsert_rolling_door_record(
        db,
        id=final_id,
        name=name,
        supplier_key=supplier_key,
        site_id=site_id,
        relay_type=relay_type,
        relay_config=json.dumps(relay_config),
        is_active=(is_active == "on" or is_active == "1" or is_active == "true"),
        camera_id=final_camera_id,
        auto_close_enabled=(auto_close_enabled == "on" or auto_close_enabled == "1" or auto_close_enabled == "true"),
        auto_close_delay_sec=auto_close_delay_sec or 300,
    )
    
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("/scan")
async def scan_doors(request: Request, user: dict = Depends(require_menu("rolling_doors"))):
    import tinytuya
    try:
        devices = tinytuya.deviceScan(verbose=False, color=False, poll=False)
        results = []
        for ip, info in devices.items():
            results.append({
                "ip": ip,
                "device_id": info.get("gwId"),
                "product_key": info.get("productKey"),
                "version": info.get("version"),
                "name": f"Tuya Device ({ip})"
            })
            
        return {"status": "success", "devices": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/hunonic/devices")
async def get_hunonic_devices(
    request: Request,
    phone: str = Form(...),
    password: str = Form(...),
    user: dict = Depends(require_menu("rolling_doors"))
):
    from backend.iot.hunonic_handler import HunonicHandler
    try:
        handler = HunonicHandler(phone, password)
        if not handler.login():
            return {"status": "error", "message": "Login failed"}
        
        devices = handler.get_devices()
        return {"status": "success", "devices": devices}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/{door_id}/delete")
async def delete_rolling_door(
    door_id: int,
    request: Request,
    user: dict = Depends(require_menu("rolling_doors")),
):
    delete_rolling_door_record(get_db(), door_id)
    return RedirectResponse(url="/admin/rolling_doors", status_code=303)


@router.post("/{door_id}/control")
async def control_rolling_door(
    door_id: int,
    action: str = Form(...),
    user: dict = Depends(require_menu("rolling_doors")),
):
    db = get_db()
    door = get_rolling_door(db, door_id)
    if not door:
        return {"status": "error", "message": "Door not found"}
    
    # Parse relay_config
    try:
        conf = door.get('relay_config')
        if isinstance(conf, str):
            relay_config = json.loads(conf)
        else:
            relay_config = conf or {}
    except Exception as e:
        logger.error(f"Failed to parse relay_config for door {door_id}: {str(e)}")
        relay_config = {}

    controller = DoorController()
    success = controller.control_door(
        action=action.upper(), # 'OPEN', 'CLOSE', 'STOP'
        relay_type=door.get('relay_type'),
        relay_config=relay_config
    )
    
    if success:
        # Reset auto-close state when manually opening
        if action.upper() == "OPEN":
            try:
                from backend.iot.door_auto_close import DoorAutoCloseManager
                mgr = DoorAutoCloseManager()
                mgr.reset_door(door_id)
            except Exception:
                pass
        return {"status": "success", "message": f"Command {action} sent"}
    else:
        logger.error(f"Failed to send command {action} to door {door_id}")
        return {"status": "error", "message": f"Failed to send command {action}"}
