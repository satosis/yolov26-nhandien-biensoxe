import datetime
from decimal import Decimal
from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.templating import Jinja2Templates


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)) or hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.add_extension("jinja2.ext.do")
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": _json_default}

from backend.services.database import db


def get_db():
    return db


def get_current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def get_optional_user(request: Request) -> dict | None:
    return request.session.get("user")


def get_role_permissions(role: str) -> str:
    if not role:
        return ""
    row = db._execute_query(
        "SELECT allowed_menus FROM roles WHERE LOWER(role_name) = LOWER(%s) LIMIT 1",
        (role,), fetch=True, commit=False
    )
    return row["allowed_menus"] if row else ""


def require_menu(menu_key: str):
    def _check(request: Request) -> dict:
        user = get_current_user(request)
        # Always fetch fresh permissions from DB to avoid stale session cache
        allowed_str = get_role_permissions(user.get("role"))
        # Sync back to session if changed
        if allowed_str != user.get("allowed_menus", ""):
            user["allowed_menus"] = allowed_str
            request.session["user"] = user
        allowed = allowed_str
        if allowed != "*":
            allowed_list = [t.strip() for t in allowed.split(",") if t.strip()]
            if menu_key not in allowed_list:
                if menu_key in ("service_accounts", "api_keys") and (
                    "vehicles" in allowed_list or "users" in allowed_list or "rbac" in allowed_list
                ):
                    return user
                if menu_key in ("companies", "staff", "enterprise", "payroll", "departments", "attendance", "advances", "assets", "debts", "sales") and (
                    "vehicles" in allowed_list or "users" in allowed_list
                ):
                    return user
                raise HTTPException(status_code=403, detail="Không có quyền truy cập")
        return user
    return _check


def get_user_assigned_companies(user: dict) -> list[dict]:
    if not user or not user.get("id"):
        return []
    row = db.select("users", columns="company_ids", conditions={"id": user["id"]}, fetchall=False)
    cids = (row.get("company_ids") if row else None) or user.get("company_ids") or []
    if cids:
        cids_tuple = tuple(int(x) for x in cids if str(x).isdigit())
        if cids_tuple:
            if len(cids_tuple) == 1:
                return db._execute_query(
                    "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE id = %s ORDER BY name",
                    (cids_tuple[0],), fetchall=True, commit=False
                ) or []
            return db._execute_query(
                "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE id IN %s ORDER BY name",
                (cids_tuple,), fetchall=True, commit=False
            ) or []
    if user.get("role") == "admin":
        return db._execute_query(
            "SELECT id, name, tax_code, address FROM public.invoice_companies ORDER BY name",
            fetchall=True, commit=False
        ) or []
    return []


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

    sites = db.select("sites", conditions={"is_active": True}, order_by="site_name")
    active_site_id = request.session.get("active_site_id", 0)

    user_companies = get_user_assigned_companies(user)
    active_company_id = request.session.get("active_company_id")
    valid_cids = [c["id"] for c in user_companies]
    if active_company_id not in valid_cids:
        active_company_id = valid_cids[0] if valid_cids else None
        request.session["active_company_id"] = active_company_id

    active_company = next((c for c in user_companies if c["id"] == active_company_id), None)
    
    path = request.url.path.strip("/")
    if path.startswith("camera/"):
        current_menu = path[7:].split("/")[0]
    elif path.startswith("admin/"):
        admin_sub = path[6:]
        if admin_sub.startswith("transport/payroll") or admin_sub.startswith("payroll"):
            current_menu = "payroll"
        elif admin_sub.startswith("enterprise/departments"):
            current_menu = "departments"
        elif admin_sub.startswith("enterprise/attendance"):
            current_menu = "attendance"
        elif admin_sub.startswith("enterprise/advances"):
            current_menu = "advances"
        elif admin_sub.startswith("enterprise/assets"):
            current_menu = "assets"
        elif admin_sub.startswith("enterprise/sales"):
            current_menu = "sales"
        elif admin_sub.startswith("enterprise"):
            current_menu = "enterprise"
        elif admin_sub.startswith("transport/catalog") or admin_sub.startswith("transport/contracts") or admin_sub.startswith("transport/input-invoices"):
            current_menu = "vehicles"
        else:
            current_menu = admin_sub.split("/")[0]
    else:
        current_menu = path.split("/")[0]

    return {
        "user": user,
        "menu_groups": filtered,
        "sites": sites,
        "active_site_id": active_site_id,
        "user_companies": user_companies,
        "active_company_id": active_company_id,
        "active_company": active_company,
        "current_menu": current_menu,
        "camera_page_keys": camera_page_keys,
    }
