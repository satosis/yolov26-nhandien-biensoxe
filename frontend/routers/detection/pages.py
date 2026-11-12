from fastapi import Request, Depends
from frontend.deps import get_menu_context, require_menu, templates
from backend.camera import camera_service as cam_svc
from .model_registry import _start_cleanup_loop


async def detection_page(request: Request, user: dict = Depends(require_menu("detection"))):
    _start_cleanup_loop()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "detection"
    cameras = cam_svc.get_active_cameras()
    statuses = cam_svc.get_cameras_latest_status()
    for c in cameras:
        c["status"] = statuses.get(str(c["id"]), {}).get("status", "unknown")
    ctx["cameras"] = cameras
    return templates.TemplateResponse("pages/detection.html", {"request": request, **ctx})
