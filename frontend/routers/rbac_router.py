"""RBAC router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from pathlib import Path
from typing import Optional
from frontend.deps import get_db, get_menu_context, require_menu
from frontend.menu_registry import ALL_MENUS

router = APIRouter(prefix="/rbac")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def rbac_page(request: Request, user: dict = Depends(require_menu("rbac"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rbac"
    ctx["roles"] = get_db().get_roles()
    ctx["all_menus"] = ALL_MENUS
    return templates.TemplateResponse("pages/rbac.html", {"request": request, **ctx})


@router.post("")
async def create_role(
    request: Request,
    role_name: str = Form(...),
    allowed_menus: Optional[str] = Form(""),
    user: dict = Depends(require_menu("rbac")),
):
    menus_str = ",".join(m.strip() for m in allowed_menus.split(",") if m.strip()) if allowed_menus else "none"
    ok, _ = get_db().create_role(role_name)
    if ok:
        roles = get_db().get_roles()
        new_role = next((r for r in roles if r["role_name"] == role_name), None)
        if new_role:
            get_db().update_role_permissions(new_role["id"], menus_str)
    return RedirectResponse(url="/rbac", status_code=303)


@router.post("/{role_id}/edit")
async def update_role(
    role_id: int,
    request: Request,
    allowed_menus: Optional[str] = Form(""),
    user: dict = Depends(require_menu("rbac")),
):
    menus_str = ",".join(m.strip() for m in allowed_menus.split(",") if m.strip()) if allowed_menus else "none"
    get_db().update_role_permissions(role_id, menus_str)
    return RedirectResponse(url="/rbac", status_code=303)


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    request: Request,
    user: dict = Depends(require_menu("rbac")),
):
    get_db().delete_role(role_id)
    return RedirectResponse(url="/rbac", status_code=303)
