from fastapi import Request, Depends
from frontend.deps import get_menu_context, require_menu, templates
from backend.camera import camera_service as cam_svc
from backend.iot.door_auto_close import tripwire_line_ratio
from backend.services.database import db
import threading

from .model_registry import _start_cleanup_loop, _get_models


async def detection_page(request: Request, user: dict = Depends(require_menu("detection"))):
    _start_cleanup_loop()
    # Nạp model ngay khi mở trang (model tự gỡ sau 10 phút rảnh): khung webcam đầu tiên không phải chờ nạp.
    threading.Thread(target=_get_models, daemon=True, name="detection-warmup").start()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "detection"
    cameras = cam_svc.get_active_cameras()
    statuses = cam_svc.get_cameras_latest_status()
    for c in cameras:
        c["status"] = statuses.get(str(c["id"]), {}).get("status", "unknown")
    ctx["cameras"] = cameras
    door_cameras = db.execute_query(
        "SELECT DISTINCT camera_id FROM rolling_doors WHERE auto_close_enabled = TRUE AND camera_id IS NOT NULL AND is_active = TRUE",
        fetchall=True, commit=False)
    ratio = tripwire_line_ratio()
    ctx["door_lines"] = {str(row["camera_id"]): ratio for row in door_cameras}
    return templates.TemplateResponse("pages/detection.html", {"request": request, **ctx})
