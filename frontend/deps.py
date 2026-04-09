"""Shared dependencies for webapp."""
import os
from functools import lru_cache
from fastapi import Request, HTTPException
from starlette.responses import RedirectResponse

_db = None


def get_db():
    """Get or create DatabaseManager singleton."""
    global _db
    if _db is None:
        from backend.core.database import DatabaseManager
        _db = DatabaseManager()
    return _db


def get_current_user(request: Request) -> dict:
    """Extract authenticated user from session. Redirect to /login if missing."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def get_optional_user(request: Request) -> dict | None:
    """Extract user from session without requiring auth."""
    return request.session.get("user")


def require_menu(menu_key: str):
    """Dependency factory: checks RBAC for a specific menu key."""
    def _check(request: Request) -> dict:
        user = get_current_user(request)
        allowed = user.get("allowed_menus", "")
        if allowed != "*" and menu_key not in allowed.split(","):
            raise HTTPException(status_code=403, detail="Không có quyền truy cập")
        return user
    return _check


def get_menu_context(request: Request, user: dict) -> dict:
    """Build sidebar menu context filtered by RBAC.
    Also refreshes session allowed_menus from DB to handle role changes."""
    from frontend.menu_registry import MENU_GROUPS

    roles = get_db().get_roles()
    role_info = next((r for r in roles if r["role_name"] == user.get("role")), None)
    allowed_str = role_info.get("allowed_menus", "") if role_info else ""

    # Refresh session if role permissions changed (fix stale session)
    session_allowed = user.get("allowed_menus", "")
    if allowed_str != session_allowed:
        user["allowed_menus"] = allowed_str
        request.session["user"] = user

    allowed = allowed_str.split(",") if allowed_str and allowed_str != "*" else ("*" if allowed_str == "*" else [])

    filtered = {}
    for group_name, items in MENU_GROUPS.items():
        group_items = {}
        for k, v in items.items():
            if allowed == "*" or k in allowed:
                group_items[k] = v
        if group_items:
            filtered[group_name] = group_items

    sites = get_db().get_sites(active_only=True)
    active_site_id = request.session.get("active_site_id", 0)
    current_menu = request.url.path.strip("/").split("/")[0] if request.url.path != "/" else ""

    return {
        "user": user,
        "menu_groups": filtered,
        "sites": sites,
        "active_site_id": active_site_id,
        "current_menu": current_menu,
    }
