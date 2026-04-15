"""Cameras router."""
import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse
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
    # Pre-fill RTSP URL for ANY camera type if empty in DB
    for cam in cameras:
        if not cam.get("rtsp_url"):
            cam["rtsp_url"] = db.get_camera_rtsp_url(cam["id"])

    ctx["cameras"] = cameras
    ctx["providers"] = db.get_all_providers()
    ctx["provider_templates"] = {p['provider_key']: p.get('rtsp_template', '') for p in ctx["providers"]}
    ctx["global_mk"] = db.get_app_setting("IMOU_MK", "")
    ctx["last_discovery"] = db.get_app_setting("LAST_DISCOVERY_AT", "")
    ctx["auto_register"] = (db.get_app_setting("AUTO_REGISTER_NEW_CAMERAS", "false") or "false").lower() == "true"
    ctx["camera_status"] = db.get_cameras_latest_status()
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
    imou_mk: str = Form(""),
    site_ids: Optional[str] = Form(None),
):
    parsed_site_ids = []
    if site_ids:
        parsed_site_ids = [int(s.strip()) for s in site_ids.split(",") if s.strip()]

    db = get_db()
    provider_keys = {"mk": imou_mk} if imou_mk else None
    new_cam_id = db.add_camera(
        name=camera_name,
        cam_type=camera_type,
        imou_device_id=imou_device_id,
        rtsp_url=rtsp_url if rtsp_url and rtsp_url.strip() else None,
        imou_channel_id=imou_channel_id,
        site_ids=parsed_site_ids or None,
        camera_ip=camera_ip if camera_ip else None,
        camera_port=camera_port,
        provider_keys=provider_keys,
    )
    if new_cam_id is None:
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
    if not cam:
        return {"ok": False, "streaming": False}
    return {"ok": True, "streaming": cam.get("is_streaming", False)}


@router.post("/discover")
async def discover_cameras(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    import asyncio
    from backend.util.network_utils import discover_and_update_all_cameras
    results = await asyncio.to_thread(discover_and_update_all_cameras)
    return results


@router.post("/discover/subnet")
async def discover_subnet(
    request: Request,
    subnet: str = Form("192.168.1"),
    user: dict = Depends(require_menu("cameras")),
):
    return JSONResponse({"error": "Subnet scan removed. Use ONVIF + Imou Cloud instead."}, status_code=410)


@router.post("/auto-add")
async def auto_add_camera(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    """Auto-insert or update a discovered device in cameras table using global credentials."""
    data = await request.json()
    db = get_db()

    device_id = (data.get("id") or "").strip()
    ip = data.get("ip") or ""
    cam_type = data.get("type") or "onvif"
    model = data.get("model") or ""
    manufacturer = data.get("manufacturer") or ""
    name = data.get("name") or f"{manufacturer} {model}".strip() or f"Camera @ {ip}"
    onvif_port = data.get("onvif_port") or 554
    camera_id = data.get("camera_id")  # If set, update existing camera
    rtsp_url_onvif = data.get("rtsp_url_onvif") or ""  # ONVIF-detected RTSP URL

    # Strip unidentified_ prefix
    if device_id.startswith("unidentified_"):
        device_id = ""

    # Use global password from app_settings
    mk = db.get_app_setting("IMOU_MK", "")

    if camera_id:
        # Update existing camera — use ONVIF RTSP if available, else clear to regenerate
        cam = db.get_camera(camera_id)
        if not cam:
            return {"ok": False, "name": name, "error": "Camera not found"}
        ok = db.update_camera(
            camera_id=camera_id,
            name=name,
            cam_type=cam_type,
            imou_device_id=device_id.upper() if device_id else cam.get("imou_device_id"),
            rtsp_url=rtsp_url_onvif if rtsp_url_onvif else None,
            is_active=cam["is_active"],
            imou_channel_id=cam.get("imou_channel_id", "0"),
            site_ids=cam.get("site_ids"),
            provider_keys={"mk": mk} if mk else cam.get("provider_keys"),
            camera_ip=ip if ip else cam.get("camera_ip"),
            camera_port=onvif_port or cam.get("camera_port", 554),
        )
        if ok and rtsp_url_onvif:
            db.store_onvif_detected_rtsp(camera_id, rtsp_url_onvif)
        elif ok:
            url = db.get_camera_rtsp_url(camera_id)
            if url:
                db._execute_query("UPDATE cameras SET rtsp_url = %s WHERE id = %s",
                                 (url, camera_id), commit=True)
        return {"ok": ok, "name": name, "updated": True}
    else:
        # For Imou cloud devices without IP: try ARP + ONVIF probe to resolve IP
        if not ip and cam_type == 'imou' and device_id and mk:
            try:
                from backend.util.network_utils import discover_from_arp, get_onvif_full_info
                import asyncio
                arp_candidates = await asyncio.to_thread(discover_from_arp)
                for candidate in arp_candidates:
                    try:
                        full = await asyncio.to_thread(
                            get_onvif_full_info, candidate['ip'], 80, 'admin', mk, 4
                        )
                        if full and full.get('serial', '').upper() == device_id.upper():
                            ip = candidate['ip']
                            if full.get('rtsp_main') and not rtsp_url_onvif:
                                rtsp_url_onvif = full['rtsp_main']
                            logger.info(f"auto-add: Resolved {device_id} → {ip} via ONVIF probe")
                            break
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"auto-add: IP resolution failed for {device_id}: {e}")

        # Fallback: build RTSP URL from IP + mk for Imou/Dahua cameras
        if ip and mk and not rtsp_url_onvif and cam_type == 'imou':
            from urllib.parse import quote
            mk_encoded = quote(mk, safe='')
            rtsp_url_onvif = f"rtsp://admin:{mk_encoded}@{ip}:554/cam/realmonitor?channel=1&subtype=1"

        new_cam_id = db.add_camera(
            name=name,
            cam_type=cam_type,
            imou_device_id=device_id.upper() if device_id else None,
            rtsp_url=rtsp_url_onvif if rtsp_url_onvif else None,
            imou_channel_id="0",
            site_ids=None,
            camera_ip=ip if ip else None,
            camera_port=onvif_port or 554,
            provider_keys={"mk": mk} if mk else None,
        )
        # Store ONVIF RTSP and/or auto-generate URL for newly added camera
        if new_cam_id and ip:
            if rtsp_url_onvif:
                db.store_onvif_detected_rtsp(new_cam_id, rtsp_url_onvif)
            else:
                url = db.get_camera_rtsp_url(new_cam_id)
                if url:
                    db._execute_query("UPDATE cameras SET rtsp_url = %s WHERE id = %s",
                                     (url, new_cam_id), commit=True)
        return {"ok": bool(new_cam_id), "name": name}


@router.post("/probe-onvif")
async def probe_onvif(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    """On-demand ONVIF probe for a specific camera IP.
    Returns RTSP URLs, media profiles, and PTZ capability."""
    from backend.util.network_utils import get_onvif_full_info

    data = await request.json()
    ip = data.get("ip", "").strip()
    port = int(data.get("onvif_port", 80))

    if not ip:
        return JSONResponse({"error": "IP is required"}, status_code=400)

    db = get_db()
    username = data.get("username") or db.get_app_setting("ONVIF_USERNAME", "") or "admin"
    password = data.get("password") or db.get_app_setting("ONVIF_PASSWORD", "") or db.get_app_setting("IMOU_MK", "")

    result = get_onvif_full_info(ip, port, username, password, timeout=6)
    if not result:
        return JSONResponse({"error": f"ONVIF probe failed for {ip}:{port}"}, status_code=404)

    return {
        "ok": True,
        "ip": ip,
        "onvif_port": port,
        "manufacturer": result.get("manufacturer", ""),
        "model": result.get("model", ""),
        "serial": result.get("serial", ""),
        "firmware": result.get("firmware", ""),
        "rtsp_main": result.get("rtsp_main", ""),
        "rtsp_sub": result.get("rtsp_sub", ""),
        "onvif_profiles": result.get("onvif_profiles", []),
        "has_ptz": result.get("has_ptz", False),
    }


@router.post("/discover-arp")
async def discover_arp(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    """Instant zero-cost ARP table scan. Returns camera candidates from ARP cache."""
    import asyncio
    from backend.util.network_utils import discover_from_arp, auto_register_discovered_cameras

    def _run():
        candidates = discover_from_arp()
        db = get_db()
        reg_result = auto_register_discovered_cameras(candidates, db)
        return candidates, reg_result

    candidates, reg_result = await asyncio.to_thread(_run)
    return {
        "candidates": candidates,
        "registered": reg_result.get("registered", 0),
        "updated": reg_result.get("updated", 0),
    }


@router.post("/auto-register/toggle")
async def toggle_auto_register(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    """Toggle automatic registration of newly discovered cameras."""
    db = get_db()
    current = db.get_app_setting("AUTO_REGISTER_NEW_CAMERAS", "false") or "false"
    new_val = "false" if current.lower() == "true" else "true"
    db.set_app_setting("AUTO_REGISTER_NEW_CAMERAS", new_val)
    return {"auto_register": new_val == "true"}


@router.get("/health")
async def camera_health(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
    hours: int = 24,
):
    """Return health event log for all cameras."""
    db = get_db()
    events = db.get_camera_health(hours=hours)
    return {"events": events, "count": len(events)}
