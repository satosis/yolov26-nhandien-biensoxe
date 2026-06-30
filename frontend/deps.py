"""Shared dependencies for webapp."""
from fastapi import Request, HTTPException

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
        if allowed != "*" and menu_key not in [t.strip() for t in allowed.split(",")]:
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

    allowed = "*" if allowed_str == "*" else [t.strip() for t in allowed_str.split(",") if t.strip()]

    filtered = {}
    for group_name, items in MENU_GROUPS.items():
        group_items = {}
        for k, v in items.items():
            if allowed == "*" or k in allowed:
                group_items[k] = v
        if group_items:
            filtered[group_name] = group_items

    # Camera pages set for prefixing logic
    camera_page_keys = {
        "detection", "cameras", "vehicles", "strangers", 
        "pending_plates", "parking_dashboard", "parking_logs", 
        "parking_analytics", "recordings"
    }

    sites = get_db().get_sites(active_only=True)
    active_site_id = request.session.get("active_site_id", 0)
    
    # Resolve current_menu from the request path to match a menu key.
    # Routers are mounted under /admin (e.g. /admin/cameras, /admin/parking_dashboard,
    # /admin/cms). Strip the leading "admin/" so the remaining value equals the menu key
    # ("cameras", "parking_dashboard", "cms").
    path = request.url.path.strip("/")
    if path.startswith("camera/"):
        current_menu = path[7:]  # legacy /camera prefix
    elif path.startswith("admin/"):
        current_menu = path[6:]  # len("admin/") == 6
    else:
        current_menu = path
    # Only the first path segment maps to a menu key
    # (e.g. /admin/cameras/123/edit -> "cameras") so nested pages keep nav state.
    current_menu = current_menu.split("/")[0]

    return {
        "user": user,
        "menu_groups": filtered,
        "sites": sites,
        "active_site_id": active_site_id,
        "current_menu": current_menu,
        "camera_page_keys": camera_page_keys,
    }
