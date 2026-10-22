import re
from typing import Optional
from fastapi import APIRouter, Request, Form, Depends, Query
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.responses import RedirectResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from backend.services.database import db
from backend.camera import camera_service as cam_svc

router = APIRouter(prefix="/providers")


def _mask(val) -> str:
    v = str(val or "")
    if not v:
        return ""
    return ("●" * 4 + v[-4:]) if len(v) > 4 else "●" * 4


@router.get("")
async def providers_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("providers")),
):
    ctx = get_menu_context(request, user)
    total_providers = cam_svc.count_providers()
    page_data = paginate_counts(total_providers, page=page, per_page=20, base_url="/admin/providers")
    providers = cam_svc.get_all_providers(
        limit=page_data["per_page"], offset=(page_data["page"] - 1) * page_data["per_page"],
    ) or []
    for p in providers:
        pk = p["provider_key"]
        keys = cam_svc.get_provider_api_keys(pk)
        p["_api_keys"] = keys
        p["_masked"] = {k: _mask(v) for k, v in keys.items() if k != "email" and str(v or "").strip()}
    ctx["providers"] = providers
    ctx["pagination"] = page_data
    ctx["total_providers"] = total_providers
    ctx["current_menu"] = "providers"
    return templates.TemplateResponse("pages/providers.html", {"request": request, **ctx})


@router.get("/{provider_key}/api-keys")
async def edit_api_keys_page(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("providers")),
):
    ctx = get_menu_context(request, user)
    provider = cam_svc.get_provider(provider_key)
    if not provider:
        return HTMLResponse("Không tìm thấy nhà cung cấp camera.", status_code=404)
    
    keys = cam_svc.get_provider_api_keys(provider_key)
    ctx["provider"] = provider
    ctx["api_keys"] = keys
    ctx["current_menu"] = "providers"
    return templates.TemplateResponse("pages/provider_api_keys.html", {"request": request, **ctx})


@router.post("/{provider_key}/api-keys")
async def save_api_keys(
    provider_key: str,
    request: Request,
    _: dict = Depends(require_menu("providers")),
):
    form = await request.form()
    names = form.getlist("api_key_names[]")
    values = form.getlist("api_key_values[]")
    if not cam_svc.get_provider(provider_key):
        return HTMLResponse("Không tìm thấy nhà cung cấp camera.", status_code=404)
    if len(names) != len(values) or len(names) > 32 or any(
        not isinstance(name, str) or re.fullmatch(r"[a-zA-Z0-9_]{1,80}", name.strip()) is None
        or not isinstance(value, str) or len(value) > 2048
        for name, value in zip(names, values)
    ):
        return HTMLResponse("Danh sách khóa API không hợp lệ.", status_code=422)
    keys = {}
    for n, v in zip(names, values):
        n_stripped = n.strip().lower()
        if n_stripped:
            keys[n_stripped] = v.strip()
            
    if not cam_svc.update_provider_api_keys(provider_key, keys):
        return HTMLResponse("Không thể lưu khóa API.", status_code=500)
    return RedirectResponse("/admin/providers", status_code=303)


@router.post("")
async def upsert_provider(
    provider_key: str = Form(...),
    name: str = Form(...),
    api_base_url: Optional[str] = Form(None),
    api_doc_url: Optional[str] = Form(None),
    rtsp_template: Optional[str] = Form(None),
    onvif_port: int = Form(80),
    default_rtsp_port: int = Form(554),
    default_http_port: int = Form(80),
    notes: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    _: dict = Depends(require_menu("providers")),
):
    provider_key = provider_key.strip().lower()
    name = name.strip()
    if not provider_key or not name:
        return HTMLResponse("Mã và tên nhà cung cấp camera không được để trống.", status_code=422)
    if not cam_svc.upsert_provider(
        provider_key=provider_key,
        name=name,
        api_base_url=api_base_url or None,
        api_doc_url=api_doc_url or None,
        rtsp_template=rtsp_template or None,
        onvif_port=onvif_port,
        default_rtsp_port=default_rtsp_port,
        default_http_port=default_http_port,
        notes=notes or None,
        logo_url=logo_url or None,
        is_active=is_active == "on" or is_active == "1" or is_active == "true",
    ):
        return HTMLResponse("Không thể lưu nhà cung cấp camera.", status_code=500)
    return RedirectResponse("/admin/providers", status_code=303)


@router.delete("/{provider_key}")
async def delete_provider(
    provider_key: str,
    _: dict = Depends(require_menu("providers")),
):
    if not cam_svc.delete_provider(provider_key):
        return JSONResponse({"ok": False, "message": "Không tìm thấy nhà cung cấp camera."}, status_code=404)
    return {"ok": True}


@router.post("/{provider_key}/toggle")
async def toggle_provider(
    provider_key: str,
    _: dict = Depends(require_menu("providers")),
):
    provider = cam_svc.get_provider(provider_key)
    if not provider:
        return HTMLResponse("Không tìm thấy nhà cung cấp camera.", status_code=404)
    if not cam_svc.upsert_provider(
        provider_key=provider["provider_key"],
        name=provider["name"],
        api_base_url=provider.get("api_base_url"),
        api_doc_url=provider.get("api_doc_url"),
        rtsp_template=provider.get("rtsp_template"),
        onvif_port=provider.get("onvif_port", 80),
        default_rtsp_port=provider.get("default_rtsp_port", 554),
        default_http_port=provider.get("default_http_port", 80),
        notes=provider.get("notes"),
        logo_url=provider.get("logo_url"),
        is_active=not provider.get("is_active", True),
    ):
        return HTMLResponse("Không thể cập nhật nhà cung cấp camera.", status_code=500)
    return RedirectResponse("/admin/providers", status_code=303)
