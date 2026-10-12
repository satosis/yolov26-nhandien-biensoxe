import logging
from fastapi import APIRouter, Request, Depends, Form, Body
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel, Field, StringConstraints
from typing import Optional
from typing import Annotated
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db
from backend.camera import camera_service as cam_svc
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras")


class AutoAddCameraInput(BaseModel):
    id: str | None = ""
    ip: str | None = ""
    type: str | None = "onvif"
    model: str | None = ""
    manufacturer: str | None = ""
    name: str | None = ""
    onvif_port: int | None = Field(default=554, ge=1, le=65535)
    camera_id: int | None = Field(default=None, gt=0)
    rtsp_url_onvif: str | None = ""


class ProbeOnvifInput(BaseModel):
    ip: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    onvif_port: int = Field(default=80, ge=1, le=65535)
    username: str | None = ""
    password: str | None = ""


def _site_ids_from_form(values: list[str] | None) -> list[int] | None:
    parts = [part.strip() for value in (values or []) for part in value.split(",") if part.strip()]
    if any(not part.isascii() or not part.isdecimal() or len(part) > 10 for part in parts):
        return None
    ids = list(dict.fromkeys(int(part) for part in parts))
    return ids if all(0 < site_id <= 2147483647 for site_id in ids) else None


@router.get("")
async def cameras_page(request: Request, user: dict = Depends(require_menu("cameras"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "cameras"
    
    active_site_id = ctx.get("active_site_id")
    site_id_filter = int(active_site_id) if active_site_id else None
    cameras = cam_svc.get_all_cameras(site_id=site_id_filter if site_id_filter and site_id_filter > 0 else None)
    for cam in cameras:
        if not cam.get("rtsp_url"):
            cam["rtsp_url"] = cam_svc.get_camera_rtsp_url(cam["id"])

    ctx["cameras"] = cameras
    ctx["providers"] = cam_svc.get_all_providers()
    ctx["provider_templates"] = {p['provider_key']: p.get('rtsp_template', '') for p in ctx["providers"]}
    ctx["global_mk"] = get_app_setting("IMOU_MK", "")
    ctx["last_discovery"] = get_app_setting("LAST_DISCOVERY_AT", "")
    ctx["auto_register"] = (get_app_setting("AUTO_REGISTER_NEW_CAMERAS", "false") or "false").lower() == "true"
    ctx["camera_status"] = cam_svc.get_cameras_latest_status()
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
    site_ids: Optional[list[str]] = Form(None),
):
    parsed_site_ids = _site_ids_from_form(site_ids)
    wants_json = "application/json" in request.headers.get("accept", "").lower()
    if parsed_site_ids is None:
        message = "Danh sách địa điểm không hợp lệ."
        if wants_json:
            return JSONResponse({"ok": False, "message": message}, status_code=422)
        return HTMLResponse(message, status_code=422)

    provider_keys = {"mk": imou_mk} if imou_mk else None
    new_camera_id = cam_svc.add_camera(
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
    if new_camera_id is None:
        return JSONResponse({"ok": False, "message": "Không thể thêm camera."}, status_code=500)
    if wants_json:
        return {"ok": True, "camera_id": new_camera_id}
    return RedirectResponse(url="/admin/cameras", status_code=303)


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    if not cam_svc.delete_camera(camera_id):
        return JSONResponse({"ok": False, "message": "Không tìm thấy camera."}, status_code=404)
    return {"ok": True}


@router.post("/{camera_id:int}/toggle")
async def toggle_camera(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("cameras")),
):
    cam = cam_svc.get_camera(camera_id)
    if not cam:
        return HTMLResponse("Không tìm thấy camera.", status_code=404)

    updated = cam_svc.update_camera(
        camera_id=camera_id,
        name=cam["camera_name"],
        cam_type=cam["camera_type"],
        imou_device_id=cam.get("imou_device_id", ""),
        rtsp_url=cam.get("rtsp_url", ""),
        is_active=not cam["is_active"],
        imou_channel_id=cam.get("imou_channel_id", "0"),
        site_ids=cam.get("site_ids"),
        provider_keys=cam.get("provider_keys"),
        camera_ip=cam.get("camera_ip"),
        camera_port=cam.get("camera_port"),
    )
    if not updated:
        return HTMLResponse("Không tìm thấy camera.", status_code=404)
    return RedirectResponse(url="/admin/cameras", status_code=303)


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
    site_ids: Optional[list[str]] = Form(None),
):
    parsed_site_ids = _site_ids_from_form(site_ids)
    if parsed_site_ids is None:
        return HTMLResponse("Danh sách địa điểm không hợp lệ.", status_code=422)

    cam = cam_svc.get_camera(camera_id)
    if not cam:
        return HTMLResponse("Không tìm thấy camera.", status_code=404)

    final_rtsp_url = rtsp_url.strip() if rtsp_url and rtsp_url.strip() else None

    updated = cam_svc.update_camera(
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
    if not updated:
        return HTMLResponse("Không tìm thấy camera.", status_code=404)
    return RedirectResponse(url="/admin/cameras", status_code=303)


@router.post("/{camera_id}/stream/start")
async def start_stream(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("detection")),
):
    cam = cam_svc.get_camera(camera_id)
    if not cam:
        return JSONResponse({"ok": False, "error": "Camera không tồn tại"}, status_code=404)

    rtsp_url = cam_svc.get_camera_rtsp_url(camera_id)
    if not rtsp_url:
        return JSONResponse({
            "ok": False,
            "error": f"Camera '{cam.get('camera_name')}' chưa có địa chỉ IP hoặc mật khẩu để kết nối."
        }, status_code=400)

    if not cam_svc.set_camera_streaming(camera_id, True):
        return JSONResponse({"ok": False, "message": "Không tìm thấy camera."}, status_code=404)
    return {"ok": True}


@router.post("/{camera_id}/stream/stop")
async def stop_stream(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("detection")),
):
    if not cam_svc.set_camera_streaming(camera_id, False):
        return JSONResponse({"ok": False, "message": "Không tìm thấy camera."}, status_code=404)
    return {"ok": True}


@router.get("/{camera_id}/stream/status")
async def get_stream_status(
    camera_id: int,
    request: Request,
    user: dict = Depends(require_menu("detection")),
):
    cam = cam_svc.get_camera(camera_id)
    if not cam:
        return JSONResponse({"ok": False, "message": "Không tìm thấy camera.", "streaming": False}, status_code=404)
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
    data: AutoAddCameraInput = Body(...),
    user: dict = Depends(require_menu("cameras")),
):
    device_id = (data.id or "").strip()
    ip = (data.ip or "").strip()
    cam_type = data.type or "onvif"
    model = data.model or ""
    manufacturer = data.manufacturer or ""
    name = data.name or f"{manufacturer} {model}".strip() or f"Camera @ {ip}"
    onvif_port = data.onvif_port or 554
    camera_id = data.camera_id
    rtsp_url_onvif = data.rtsp_url_onvif or ""

    if device_id.startswith("unidentified_"):
        device_id = ""

    mk = get_app_setting("IMOU_MK", "")

    if camera_id:
        cam = cam_svc.get_camera(camera_id)
        if not cam:
            return JSONResponse({"ok": False, "name": name, "message": "Không tìm thấy camera."}, status_code=404)
        ok = cam_svc.update_camera(
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
        if not ok:
            return JSONResponse({"ok": False, "name": name, "message": "Không tìm thấy camera."}, status_code=404)
        if rtsp_url_onvif:
            rtsp_saved = cam_svc.store_onvif_detected_rtsp(camera_id, rtsp_url_onvif)
        else:
            url = cam_svc.get_camera_rtsp_url(camera_id)
            rtsp_saved = not url or db._execute_query(
                "UPDATE cameras SET rtsp_url = %s WHERE id = %s", (url, camera_id), commit=True
            )
        if not rtsp_saved:
            logger.error("Camera auto-add RTSP persistence failed for existing camera_id=%s", camera_id)
            return JSONResponse({
                "ok": False, "name": name, "updated": True, "partial": True,
                "message": "Đã cập nhật camera nhưng không thể lưu địa chỉ RTSP.",
            }, status_code=500)
        return {"ok": True, "name": name, "updated": True}
    else:
        if not ip and cam_type == 'imou' and device_id and mk:
            from backend.util.network_utils import discover_from_arp, get_onvif_full_info
            import asyncio
            arp_candidates = await asyncio.to_thread(discover_from_arp)
            for candidate in arp_candidates:
                full = await asyncio.to_thread(
                    get_onvif_full_info, candidate['ip'], 80, 'admin', mk, 4
                )
                if full and full.get('serial', '').upper() == device_id.upper():
                    ip = candidate['ip']
                    if full.get('rtsp_main') and not rtsp_url_onvif:
                        rtsp_url_onvif = full['rtsp_main']
                    logger.info(f"auto-add: Resolved {device_id} → {ip} via ONVIF probe")
                    break

        if ip and mk and not rtsp_url_onvif and cam_type == 'imou':
            from urllib.parse import quote
            mk_encoded = quote(mk, safe='')
            rtsp_url_onvif = f"rtsp://admin:{mk_encoded}@{ip}:554/cam/realmonitor?channel=1&subtype=1"

        new_cam_id = cam_svc.add_camera(
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
        if not new_cam_id:
            return JSONResponse({"ok": False, "name": name, "message": "Không thể thêm camera."}, status_code=500)
        if ip:
            if rtsp_url_onvif:
                rtsp_saved = cam_svc.store_onvif_detected_rtsp(new_cam_id, rtsp_url_onvif)
            else:
                url = cam_svc.get_camera_rtsp_url(new_cam_id)
                rtsp_saved = not url or db._execute_query(
                    "UPDATE cameras SET rtsp_url = %s WHERE id = %s", (url, new_cam_id), commit=True
                )
            if not rtsp_saved:
                logger.error("Camera auto-add RTSP persistence failed for new camera_id=%s", new_cam_id)
                return JSONResponse({
                    "ok": False, "name": name, "camera_id": new_cam_id, "partial": True,
                    "message": "Đã thêm camera nhưng không thể lưu địa chỉ RTSP. Kiểm tra camera đã thêm trước khi thử lại.",
                }, status_code=500)
        return {"ok": True, "name": name}


@router.post("/probe-onvif")
async def probe_onvif(
    data: ProbeOnvifInput = Body(...),
    user: dict = Depends(require_menu("cameras")),
):
    from backend.util.network_utils import get_onvif_full_info

    ip = data.ip
    port = data.onvif_port
    username = data.username or get_app_setting("ONVIF_USERNAME", "") or "admin"
    password = data.password or get_app_setting("ONVIF_PASSWORD", "") or get_app_setting("IMOU_MK", "")

    result = get_onvif_full_info(ip, port, username, password, timeout=6)
    if not result:
        return JSONResponse({"ok": False, "message": "Không thể kết nối ONVIF với camera."}, status_code=404)

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
    import asyncio
    from backend.util.network_utils import discover_from_arp, auto_register_discovered_cameras

    def _run():
        candidates = discover_from_arp()
        reg_result = auto_register_discovered_cameras(candidates)
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
    current = get_app_setting("AUTO_REGISTER_NEW_CAMERAS", "false") or "false"
    new_val = "false" if current.lower() == "true" else "true"
    if not set_app_setting("AUTO_REGISTER_NEW_CAMERAS", new_val):
        return JSONResponse({"ok": False, "message": "Không thể lưu cấu hình tự thêm camera."}, status_code=500)
    return {"auto_register": new_val == "true"}


@router.get("/health")
async def camera_health(
    request: Request,
    user: dict = Depends(require_menu("cameras")),
    hours: int = 24,
):
    events = cam_svc.get_camera_health(hours=hours)
    return {"events": events, "count": len(events)}
