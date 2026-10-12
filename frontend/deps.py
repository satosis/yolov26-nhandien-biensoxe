from decimal import Decimal
from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.templating import Jinja2Templates


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.add_extension("jinja2.ext.do")
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": _json_default}

_db = None


def get_db():
    global _db
    if _db is None:
        from backend.services.database import DatabaseManager
        _db = DatabaseManager()
    return _db


def get_current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def get_optional_user(request: Request) -> dict | None:
    return request.session.get("user")


def get_role_permissions(role: str) -> str:
    row = get_db().select("roles", columns="allowed_menus", conditions={"role_name": role}, fetchall=False)
    return row["allowed_menus"] if row else ""


def require_menu(menu_key: str):
    def _check(request: Request) -> dict:
        user = get_current_user(request)
        allowed = user.get("allowed_menus", "")
        if allowed != "*" and menu_key not in [t.strip() for t in allowed.split(",")]:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập")
        return user
    return _check


def get_menu_context(request: Request, user: dict) -> dict:
    from frontend.menu_registry import MENU_GROUPS

    allowed_str = get_role_permissions(user.get("role"))

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

    camera_page_keys = {
        "detection", "cameras", "vehicles", "strangers", 
        "pending_plates", "parking_dashboard", "parking_logs", 
        "parking_analytics", "recordings"
    }

    sites = get_db().select("sites", conditions={"is_active": True}, order_by="site_name")
    active_site_id = request.session.get("active_site_id", 0)
    
    path = request.url.path.strip("/")
    if path.startswith("camera/"):
        current_menu = path[7:]
    elif path.startswith("admin/"):
        current_menu = path[6:]
    else:
        current_menu = path
    current_menu = current_menu.split("/")[0]

    return {
        "user": user,
        "menu_groups": filtered,
        "sites": sites,
        "active_site_id": active_site_id,
        "current_menu": current_menu,
        "camera_page_keys": camera_page_keys,
    }
