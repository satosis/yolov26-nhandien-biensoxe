"""Providers router — Camera provider CRUD."""
from typing import Optional
from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path

from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/providers")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _mask(val) -> str:
    v = str(val or "")
    if not v:
        return ""
    return ("●" * 4 + v[-4:]) if len(v) > 4 else "●" * 4


@router.get("")
async def providers_page(request: Request, user: dict = Depends(require_menu("providers"))):
    ctx = get_menu_context(request, user)
    db = get_db()
    providers = db.get_all_providers()
    for p in providers:
        pk = p["provider_key"]
        keys = db.get_provider_api_keys(pk)
        p["_api_keys"] = keys or {}
        p["_masked"] = {k: _mask(v) for k, v in (keys or {}).items() if k != "email" and str(v or "").strip()}
    ctx["providers"] = providers
    ctx["current_menu"] = "providers"
    return templates.TemplateResponse("pages/providers.html", {"request": request, **ctx})


@router.get("/{provider_key}/api-keys")
async def edit_api_keys_page(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("providers")),
):
    ctx = get_menu_context(request, user)
    db = get_db()
    provider = db.get_provider(provider_key)
    if not provider:
        return RedirectResponse("/admin/providers", status_code=303)
    
    keys = db.get_provider_api_keys(provider_key)
    ctx["provider"] = provider
    ctx["api_keys"] = keys or {}
    ctx["current_menu"] = "providers"
    return templates.TemplateResponse("pages/provider_api_keys.html", {"request": request, **ctx})


@router.post("/{provider_key}/api-keys")
async def save_api_keys(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("providers")),
):
    form = await request.form()
    # Handle dynamic lists of names and values
    names = form.getlist("api_key_names[]")
    values = form.getlist("api_key_values[]")
    
    keys = {}
    for n, v in zip(names, values):
        n_stripped = n.strip().lower()
        if n_stripped:
            keys[n_stripped] = v.strip()
            
    get_db().update_provider_api_keys(provider_key, keys)
    return RedirectResponse("/admin/providers", status_code=303)


@router.post("")
async def upsert_provider(
    request: Request,
    user: dict = Depends(require_menu("providers")),
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
):
    get_db().upsert_provider(
        provider_key=provider_key.strip().lower(),
        name=name.strip(),
        api_base_url=api_base_url or None,
        api_doc_url=api_doc_url or None,
        rtsp_template=rtsp_template or None,
        onvif_port=onvif_port,
        default_rtsp_port=default_rtsp_port,
        default_http_port=default_http_port,
        notes=notes or None,
        logo_url=logo_url or None,
        is_active=is_active == "on" or is_active == "1" or is_active == "true",
    )
    return RedirectResponse("/admin/providers", status_code=303)


@router.delete("/{provider_key}")
async def delete_provider(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("providers")),
):
    get_db().delete_provider(provider_key)
    return RedirectResponse("/admin/providers", status_code=303)


@router.post("/{provider_key}/toggle")
async def toggle_provider(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("providers")),
):
    db = get_db()
    provider = db.get_provider(provider_key)
    if provider:
        db.upsert_provider(
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
        )
    return RedirectResponse("/admin/providers", status_code=303)
