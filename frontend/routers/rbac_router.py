"""RBAC router."""
import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from pathlib import Path
from typing import List
from frontend.deps import get_db, get_menu_context, require_menu
from frontend.menu_registry import ALL_MENUS, MENU_GROUPS

logger = logging.getLogger("rbac_router")
router = APIRouter(prefix="/rbac")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def rbac_page(request: Request, user: dict = Depends(require_menu("rbac"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rbac"
    db = get_db()
    roles = db.get_roles()
    # Count users per role for display
    all_users = db.get_all_users()
    role_user_counts = {}
    for u in all_users:
        rname = u.get("role", "")
        role_user_counts[rname] = role_user_counts.get(rname, 0) + 1
    for r in roles:
        r["user_count"] = role_user_counts.get(r["role_name"], 0)

    ctx["roles"] = roles
    ctx["all_menus"] = ALL_MENUS
    ctx["menu_groups"] = MENU_GROUPS
    return templates.TemplateResponse("pages/rbac.html", {"request": request, **ctx})


@router.post("")
async def create_role(
    request: Request,
    role_name: str = Form(...),
    allowed_menus: List[str] = Form([]),
    user: dict = Depends(require_menu("rbac")),
):
    menus_str = ",".join(m.strip() for m in allowed_menus if m.strip()) if allowed_menus else "none"
    db = get_db()
    ok, msg = db.create_role(role_name)
    if ok:
        roles = db.get_roles()
        new_role = next((r for r in roles if r["role_name"] == role_name), None)
        if new_role:
            db.update_role_permissions(new_role["id"], menus_str)
        logger.info("Role '%s' created with menus: %s", role_name, menus_str)
    else:
        logger.warning("Failed to create role '%s': %s", role_name, msg)
    return RedirectResponse(url="/admin/rbac", status_code=303)


@router.post("/{role_id}/edit")
async def update_role(
    role_id: int,
    request: Request,
    allowed_menus: List[str] = Form([]),
    user: dict = Depends(require_menu("rbac")),
):
    menus_str = ",".join(m.strip() for m in allowed_menus if m.strip()) if allowed_menus else "none"
    get_db().update_role_permissions(role_id, menus_str)
    logger.info("Role %d updated: %s", role_id, menus_str)
    return RedirectResponse(url="/admin/rbac", status_code=303)


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    request: Request,
    user: dict = Depends(require_menu("rbac")),
):
    db = get_db()
    # Check if role has users
    roles = db.get_roles()
    target_role = next((r for r in roles if r["id"] == role_id), None)
    if not target_role:
        return JSONResponse({"detail": "Vai trò không tồn tại."}, status_code=404)

    if target_role["role_name"].lower() == "admin":
        return JSONResponse({"detail": "Không thể xóa vai trò Admin."}, status_code=400)

    all_users = db.get_all_users()
    users_with_role = [u for u in all_users if u.get("role") == target_role["role_name"]]
    if users_with_role:
        return JSONResponse(
            {"detail": f"Không thể xóa. Còn {len(users_with_role)} người dùng đang dùng vai trò này."},
            status_code=400,
        )

    db.delete_role(role_id)
    logger.info("Role '%s' (id=%d) deleted", target_role["role_name"], role_id)
    return RedirectResponse(url="/admin/rbac", status_code=303)
