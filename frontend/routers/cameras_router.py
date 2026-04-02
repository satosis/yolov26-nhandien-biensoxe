"""Cameras router."""
import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
from frontend.deps import get_db, get_menu_context, require_menu

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def cameras_page(request: Request, user: dict = Depends(require_menu("cameras"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "cameras"
    db = get_db()
    
    # Robust site_id handling
    active_site_id = ctx.get("active_site_id")
    site_id_filter = None
    if active_site_id is not None:
        if isinstance(active_site_id, str) and active_site_id.isdigit():
            site_id_filter = int(active_site_id)
        elif isinstance(active_site_id, int):
            site_id_filter = active_site_id

    cameras = db.get_all_cameras(site_id=site_id_filter if site_id_filter and site_id_filter > 0 else None)
    # Pre-fill RTSP URL for Imou if empty in DB, so user sees it in the table and forms
    for cam in cameras:
        if not cam.get("rtsp_url") and cam.get("camera_type") == "imou":
            # For performance, we only do this if needed. get_camera_rtsp_url is efficient.
            cam["rtsp_url"] = db.get_camera_rtsp_url(cam["id"])
            
    ctx["cameras"] = cameras
    ctx["providers"] = db.get_all_providers()
    return templates.TemplateResponse("pages/cameras.html", {"request": request, **ctx})


@router.post("")
async def create_camera(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
    camera_name: str = Form(...),
    camera_type: str = Form(...),
    imou_device_id: str = Form(""),
    imou_channel_id: str = Form("0"),
    camera_ip: str = Form(""),
    camera_port: int = Form(554),
    rtsp_url: str = Form(""),
    site_ids: Optional[str] = Form(None),
):
    parsed_site_ids = []
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    db = get_db()
    ok = db.add_camera(
        name=camera_name,
        cam_type=camera_type,
        imou_device_id=imou_device_id,
        rtsp_url=rtsp_url,
        imou_channel_id=imou_channel_id,
        site_ids=parsed_site_ids or None,
        camera_ip=camera_ip if camera_ip else None,
        camera_port=camera_port
    )
    if not ok:
        logger.error("create_camera: add_camera returned False for name=%s", camera_name)
    return RedirectResponse(url="/cameras", status_code=303)


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    db = get_db()
    ok = db.delete_camera(camera_id)
    if not ok:
        logger.error("delete_camera: delete_camera returned False for id=%d", camera_id)
    return RedirectResponse(url="/cameras", status_code=303)


@router.post("/{camera_id}/toggle")
async def toggle_camera(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    db = get_db()
    cam = db.get_camera(camera_id)
    if not cam:
        logger.warning("toggle_camera: camera id=%d not found", camera_id)
        return RedirectResponse(url="/cameras", status_code=303)

    ok = db.update_camera(
        camera_id=camera_id,
        name=cam["camera_name"],
        cam_type=cam["camera_type"],
        imou_device_id=cam.get("imou_device_id", ""),
        rtsp_url=cam.get("rtsp_url", ""),
        is_active=not cam["is_active"],
        imou_channel_id=cam.get("imou_channel_id", "0"),
        site_ids=cam.get("site_ids"),
        camera_ip=cam.get("camera_ip"),
        camera_port=cam.get("camera_port"),
    )
    if not ok:
        logger.error("toggle_camera: update_camera returned False for id=%d", camera_id)
    return RedirectResponse(url="/cameras", status_code=303)


@router.post("/{camera_id}/edit")
async def edit_camera(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("cameras")),
    camera_name: str = Form(...),
    camera_type: str = Form(...),
    imou_device_id: str = Form(""),
    imou_channel_id: str = Form("0"),
    camera_ip: str = Form(""),
    camera_port: Optional[int] = Form(None),
    rtsp_url: str = Form(""),
    imou_mk: str = Form(""),
    site_ids: Optional[str] = Form(None),
):
    parsed_site_ids = []
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    db = get_db()
    cam = db.get_camera(camera_id)
    if not cam:
        logger.warning("edit_camera: camera id=%d not found", camera_id)
        return RedirectResponse(url="/cameras", status_code=303)

    # If rtsp_url is provided, use it. If empty, set to None so the database dynamic generator triggers
    final_rtsp_url = rtsp_url.strip() if rtsp_url and rtsp_url.strip() else None

    ok = db.update_camera(
        camera_id=camera_id,
        name=camera_name,
        cam_type=camera_type,
        imou_device_id=imou_device_id,
        rtsp_url=final_rtsp_url,
        is_active=cam["is_active"],
        imou_channel_id=imou_channel_id,
        site_ids=parsed_site_ids or None,
        provider_keys={"mk": imou_mk} if imou_mk else cam.get("provider_keys"),
        camera_ip=camera_ip if camera_ip else None,
        camera_port=camera_port,
    )
    if not ok:
        logger.error("edit_camera: update_camera returned False for id=%d", camera_id)
    return RedirectResponse(url="/cameras", status_code=303)


@router.post("/{camera_id}/stream/start")
async def start_stream(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("detection")),
):
    db = get_db()
    ok = db.set_camera_streaming(camera_id, True)
    return {"ok": ok}


@router.post("/{camera_id}/stream/stop")
async def stop_stream(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("detection")),
):
    db = get_db()
    ok = db.set_camera_streaming(camera_id, False)
    return {"ok": ok}


@router.get("/{camera_id}/stream/status")
async def get_stream_status(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("detection")),
):
    db = get_db()
    cam = db.get_camera(camera_id)
@router.post("/discover")
async def discover_cameras(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    from backend.util.network_utils import discover_and_update_all_cameras
    results = discover_and_update_all_cameras(scan_subnet=True)
    return results


@router.post("/discover/subnet")
async def discover_subnet(
    request: Request,
    subnet: str = Form("192.168.1"),
    user: dict = Depends(require_menu("cameras")),
):
    from backend.util.network_utils import scan_subnet_for_cameras
    found_ips = scan_subnet_for_cameras(subnet)
    return {"found_ips": found_ips}



