import logging
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from typing import List
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate
from frontend.menu_registry import ALL_MENUS, MENU_GROUPS
from frontend.routers.users_controller import get_all_users
import frontend.routers.rbac_queries as queries

logger = logging.getLogger("rbac_router")


async def rbac_page(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "rbac"
    all_roles = queries.rbac_page_select_roles() or []
    all_users = get_all_users()
    role_user_counts = {}
    for u in all_users:
        rname = u.get("role", "")
        role_user_counts[rname] = role_user_counts.get(rname, 0) + 1
    for r in all_roles:
        r["user_count"] = role_user_counts.get(r["role_name"], 0)

    page_data = paginate(
        all_roles,
        page=page,
        per_page=20,
        base_url="/admin/rbac",
    )
    ctx["roles"] = page_data["items"]
    ctx["pagination"] = page_data
    ctx["total_roles"] = len(all_roles)
    ctx["all_menus"] = ALL_MENUS
    ctx["menu_groups"] = MENU_GROUPS
    return templates.TemplateResponse("pages/rbac.html", {"request": request, **ctx})


async def create_role(role_name: str, allowed_menus: List[str], _: dict):
    role_name = role_name.strip()
    menus = [m.strip() for m in allowed_menus if m.strip()]
    if not role_name or any(m not in ALL_MENUS for m in menus):
        return HTMLResponse("Tên vai trò hoặc quyền không hợp lệ.", status_code=422)
    menus_str = ",".join(menus) if menus else "none"
    existing = queries.create_role_select_roles(role_name)
    if existing:
        return HTMLResponse("Vai trò đã tồn tại.", status_code=409)
    if not queries.create_role_insert_roles(role_name, menus_str):
        return HTMLResponse("Không thể lưu vai trò.", status_code=500)
    logger.info("Role '%s' created with menus: %s", role_name, menus_str)
    return RedirectResponse(url="/admin/rbac", status_code=303)


async def update_role(role_id: int, allowed_menus: List[str], _: dict):
    menus = [m.strip() for m in allowed_menus if m.strip()]
    if any(m not in ALL_MENUS for m in menus):
        return HTMLResponse("Quyền không hợp lệ.", status_code=422)
    menus_str = ",".join(menus) if menus else "none"
    if not queries.update_role_update_roles(menus_str, role_id):
        return HTMLResponse("Vai trò không tồn tại.", status_code=404)
    logger.info("Role %d updated: %s", role_id, menus_str)
    return RedirectResponse(url="/admin/rbac", status_code=303)


async def delete_role(role_id: int, _: dict):
    roles = queries.delete_role_select_roles()
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

    queries.delete_role_delete_roles(role_id)
    logger.info("Role '%s' (id=%d) deleted", target_role["role_name"], role_id)
    return JSONResponse({"ok": True})
