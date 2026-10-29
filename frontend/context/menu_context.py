from fastapi import Request
from backend.services.database import db
from frontend.auth.rbac import get_role_permissions


from typing import Optional
from backend.app_types import SessionUser, InvoiceCompany
from backend.repositories.company_repo import get_assigned_companies
from frontend.context.company_context import get_current_company_id


def get_user_assigned_companies(user: SessionUser) -> list[InvoiceCompany]:
    if not user or not user.get("id"):
        return []
    return get_assigned_companies(user["id"], user.get("role", ""))


def get_menu_context(request: Request, user: SessionUser) -> dict:
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
    active_company_id = get_current_company_id()
    if not active_company_id:
        cid = request.session.get("active_company_id")
        active_company_id = int(cid) if cid and str(cid).isdigit() and int(cid) > 0 else None

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
        elif admin_sub.startswith("enterprise/debts"):
            current_menu = "debts"
        elif admin_sub.startswith("enterprise/sales"):
            current_menu = "sales"
        elif admin_sub.startswith("enterprise/suppliers"):
            current_menu = "suppliers"
        elif admin_sub.startswith("enterprise/tax-checklist"):
            current_menu = "tax_checklist"
        elif admin_sub.startswith("enterprise"):
            current_menu = "enterprise"
        elif admin_sub.startswith("transport/"):
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


def get_active_company_id(request: Request, user: dict | None = None, company_id: int | None = None) -> int:
    """Resolve active company ID directly from context / header dropdown / session."""
    if company_id is not None and str(company_id).isdigit() and int(company_id) > 0:
        return int(company_id)

    cid = get_current_company_id()
    if cid and cid > 0:
        return cid

    s_cid = request.session.get("active_company_id")
    if s_cid and str(s_cid).isdigit() and int(s_cid) > 0:
        return int(s_cid)

    return 33
