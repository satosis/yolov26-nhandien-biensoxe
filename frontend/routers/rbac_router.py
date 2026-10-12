import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from typing import List
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db
from frontend.menu_registry import ALL_MENUS, MENU_GROUPS
from frontend.routers.users_router import get_all_users

logger = logging.getLogger("rbac_router")
router = APIRouter(prefix="/rbac")


@router.get("")
async def rbac_page(request: Request, user: dict = Depends(require_menu("rbac"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rbac"
    roles = db.select("roles", order_by="id ASC")
    all_users = get_all_users()
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
    role_name = role_name.strip()
    menus = [m.strip() for m in allowed_menus if m.strip()]
    if not role_name or any(m not in ALL_MENUS for m in menus):
        return HTMLResponse("Tên vai trò hoặc quyền không hợp lệ.", status_code=422)
    menus_str = ",".join(menus) if menus else "none"
    existing = db.select("roles", conditions={"role_name": role_name}, fetchall=False)
    if existing:
        return HTMLResponse("Vai trò đã tồn tại.", status_code=409)
    if not db.insert("roles", {"role_name": role_name, "allowed_menus": menus_str}, returning="id"):
        return HTMLResponse("Không thể lưu vai trò.", status_code=500)
    logger.info("Role '%s' created with menus: %s", role_name, menus_str)
    return RedirectResponse(url="/admin/rbac", status_code=303)


@router.post("/{role_id}/edit")
async def update_role(
    role_id: int,
    request: Request,
    allowed_menus: List[str] = Form([]),
    user: dict = Depends(require_menu("rbac")),
):
    menus = [m.strip() for m in allowed_menus if m.strip()]
    if any(m not in ALL_MENUS for m in menus):
        return HTMLResponse("Quyền không hợp lệ.", status_code=422)
    menus_str = ",".join(menus) if menus else "none"
    if not db.update("roles", {"allowed_menus": menus_str}, {"id": role_id}):
        return HTMLResponse("Vai trò không tồn tại.", status_code=404)
    logger.info("Role %d updated: %s", role_id, menus_str)
    return RedirectResponse(url="/admin/rbac", status_code=303)


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    request: Request,
    user: dict = Depends(require_menu("rbac")),
):
    roles = db.select("roles", order_by="id ASC")
    target_role = next((r for r in roles if r["id"] == role_id), None)
    if not target_role:
        return JSONResponse({"ok": False, "message": "Vai trò không tồn tại."}, status_code=404)

    if target_role["role_name"].lower() == "admin":
        return JSONResponse({"ok": False, "message": "Không thể xóa vai trò Admin."}, status_code=400)

    all_users = get_all_users()
    users_with_role = [u for u in all_users if u.get("role") == target_role["role_name"]]
    if users_with_role:
        return JSONResponse(
            {"ok": False, "message": f"Không thể xóa. Còn {len(users_with_role)} người dùng đang dùng vai trò này."},
            status_code=400,
        )

    if not db.delete("roles", {"id": role_id}):
        return JSONResponse({"ok": False, "message": "Vai trò không tồn tại."}, status_code=404)
    logger.info("Role '%s' (id=%d) deleted", target_role["role_name"], role_id)
    return JSONResponse({"ok": True})
