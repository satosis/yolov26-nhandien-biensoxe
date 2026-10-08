"""Door providers router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from pathlib import Path
from typing import Optional
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/door_providers")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def get_all_door_providers(db) -> list:
    """Lấy danh sách tất cả nhà cung cấp cửa cuốn."""
    return db.select("door_providers", order_by="name ASC") or []


def get_door_provider(db, provider_key: str) -> dict | None:
    """Lấy chi tiết nhà cung cấp theo provider_key."""
    return db.select("door_providers", conditions={"provider_key": provider_key}, fetchall=False)


def upsert_door_provider(db, provider_key, name, connection_type=None, api_doc_url=None,
                         config_template=None, notes=None, logo_url=None, is_active=True) -> bool:
    """Thêm hoặc cập nhật nhà cung cấp cửa cuốn."""
    query = """
        INSERT INTO door_providers
            (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (provider_key) DO UPDATE SET
            name=EXCLUDED.name, connection_type=EXCLUDED.connection_type,
            api_doc_url=EXCLUDED.api_doc_url, config_template=EXCLUDED.config_template,
            notes=EXCLUDED.notes, logo_url=EXCLUDED.logo_url, is_active=EXCLUDED.is_active, updated_at=NOW()
    """
    return db.execute_query(
        query,
        (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active),
        commit=True
    ) is not None


def delete_door_provider(db, provider_key: str) -> bool:
    """Xóa nhà cung cấp cửa cuốn."""
    return db.delete("door_providers", {"provider_key": provider_key})


@router.get("")
async def door_providers_page(request: Request, user: dict = Depends(require_menu("door_providers"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "door_providers"
    db = get_db()
    ctx["providers"] = get_all_door_providers(db)
    return templates.TemplateResponse("pages/door_providers.html", {"request": request, **ctx})


@router.post("")
async def upsert_door_provider_route(
    request: Request,
    provider_key: str = Form(...),
    name: str = Form(...),
    connection_type: Optional[str] = Form(None),
    api_doc_url: Optional[str] = Form(None),
    config_template: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    logo_url: Optional[str] = Form(None),
    is_active: Optional[str] = Form("on"),
    user: dict = Depends(require_menu("door_providers")),
):
    active = is_active in ("on", "true", "1", "yes")
    upsert_door_provider(
        db=get_db(),
        provider_key=provider_key,
        name=name,
        connection_type=connection_type or None,
        api_doc_url=api_doc_url or None,
        config_template=config_template or None,
        notes=notes or None,
        logo_url=logo_url or None,
        is_active=active,
    )
    return RedirectResponse(url="/admin/door_providers", status_code=303)


@router.delete("/{provider_key}")
async def delete_door_provider_route(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("door_providers")),
):
    delete_door_provider(get_db(), provider_key)
    return RedirectResponse(url="/admin/door_providers", status_code=303)


@router.post("/{provider_key}/toggle")
async def toggle_door_provider(
    provider_key: str,
    request: Request,
    user: dict = Depends(require_menu("door_providers")),
):
    db = get_db()
    provider = get_door_provider(db, provider_key)
    if provider:
        upsert_door_provider(
            db=db,
            provider_key=provider["provider_key"],
            name=provider["name"],
            connection_type=provider.get("connection_type"),
            api_doc_url=provider.get("api_doc_url"),
            config_template=provider.get("config_template"),
            notes=provider.get("notes"),
            logo_url=provider.get("logo_url"),
            is_active=not provider.get("is_active", True),
        )
    return RedirectResponse(url="/admin/door_providers", status_code=303)
