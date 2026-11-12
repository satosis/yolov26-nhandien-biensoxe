from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from typing import Optional
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
import frontend.routers.door_providers_queries as queries


def count_door_providers() -> int:
    row = queries.count_door_providers_select_door_providers()
    return int((row or {}).get("total") or 0)


def get_door_provider(provider_key: str) -> dict | None:
    return queries.get_door_provider_select_door_providers(provider_key)


def delete_door_provider(provider_key: str) -> bool:
    return queries.delete_door_provider_delete_door_providers(provider_key)


async def door_providers_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "door_providers"
    total_providers = count_door_providers()
    page_data = paginate_counts(total_providers, page=page, per_page=20, base_url="/admin/door_providers")
    ctx["providers"] = queries.get_all_door_providers(
        limit=page_data["per_page"], offset=(page_data["page"] - 1) * page_data["per_page"],
    ) or []
    ctx["pagination"] = page_data
    ctx["total_providers"] = total_providers
    return templates.TemplateResponse("pages/door_providers.html", {"request": request, **ctx})


async def upsert_door_provider_route(
    provider_key: str,
    name: str,
    connection_type: Optional[str],
    api_doc_url: Optional[str],
    config_template: Optional[str],
    notes: Optional[str],
    logo_url: Optional[str],
    is_active: Optional[str],
    _: dict,
):
    provider_key = provider_key.strip().lower()
    name = name.strip()
    if not provider_key or not name:
        return HTMLResponse("Mã và tên nhà cung cấp cửa không được để trống.", status_code=422)
    active = is_active in ("on", "true", "1", "yes")
    if not queries.upsert_door_provider(
        provider_key=provider_key,
        name=name,
        connection_type=connection_type or None,
        api_doc_url=api_doc_url or None,
        config_template=config_template or None,
        notes=notes or None,
        logo_url=logo_url or None,
        is_active=active,
    ):
        return HTMLResponse("Không thể lưu nhà cung cấp cửa.", status_code=500)
    return RedirectResponse(url="/admin/door_providers", status_code=303)


async def delete_door_provider_route(provider_key: str, _: dict):
    if not delete_door_provider(provider_key):
        return JSONResponse({"ok": False, "message": "Không tìm thấy nhà cung cấp cửa."}, status_code=404)
    return {"ok": True}


async def toggle_door_provider(provider_key: str, _: dict):
    provider = get_door_provider(provider_key)
    if not provider:
        return HTMLResponse("Không tìm thấy nhà cung cấp cửa.", status_code=404)
    if not queries.upsert_door_provider(
        provider_key=provider["provider_key"],
        name=provider["name"],
        connection_type=provider.get("connection_type"),
        api_doc_url=provider.get("api_doc_url"),
        config_template=provider.get("config_template"),
        notes=provider.get("notes"),
        logo_url=provider.get("logo_url"),
        is_active=not provider.get("is_active", True),
    ):
        return HTMLResponse("Không thể cập nhật nhà cung cấp cửa.", status_code=500)
    return RedirectResponse(url="/admin/door_providers", status_code=303)
