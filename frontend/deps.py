"""Application dependencies and template configuration."""
from pathlib import Path
from fastapi.templating import Jinja2Templates

from backend.services.database import db
from frontend.formatting.filters import (
    _json_default,
    format_number,
    format_currency,
    setup_jinja2_filters,
)
from frontend.auth.rbac import (
    get_current_user,
    get_optional_user,
    get_role_permissions,
    require_menu,
    MENU_PERMISSION_OVERRIDES,
)
from frontend.context.menu_context import (
    get_user_assigned_companies,
    get_menu_context,
    get_active_company_id,
)
from frontend.context.company_context import (
    get_current_company_id,
    set_current_company_id,
    reset_current_company_id,
    company_context,
)
from backend.app_types import SessionUser, InvoiceCompany, CameraConfig, PlateLog

# Template engine setup
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.add_extension("jinja2.ext.do")
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": _json_default}
setup_jinja2_filters(templates.env)


def get_db():
    return db


__all__ = [
    "templates",
    "get_db",
    "db",
    "_json_default",
    "format_number",
    "format_currency",
    "setup_jinja2_filters",
    "get_current_user",
    "get_optional_user",
    "get_role_permissions",
    "require_menu",
    "MENU_PERMISSION_OVERRIDES",
    "get_user_assigned_companies",
    "get_menu_context",
    "get_active_company_id",
    "get_current_company_id",
    "set_current_company_id",
    "reset_current_company_id",
    "company_context",
    "SessionUser",
    "InvoiceCompany",
    "CameraConfig",
    "PlateLog",
]
