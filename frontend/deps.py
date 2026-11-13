"""Application dependencies and template configuration."""
from pathlib import Path
from fastapi.templating import Jinja2Templates

from frontend.formatting.filters import _json_default, format_currency, setup_jinja2_filters
from frontend.auth.rbac import (
    get_current_user,
    get_optional_user,
    get_role_permissions,
    require_menu,
)
from frontend.context.menu_context import get_active_company_id, get_menu_context
from frontend.context.company_context import get_current_company_id
from frontend.tracking import tracking_ids
from frontend.modules.claude_code.service import is_allowed_request as claude_code_allowed

# Template engine setup
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["tracking_ids"] = tracking_ids
templates.env.globals["claude_code_allowed"] = claude_code_allowed
templates.env.add_extension("jinja2.ext.do")
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": _json_default}
setup_jinja2_filters(templates.env)

__all__ = [
    "templates",
    "format_currency",
    "get_current_user",
    "get_optional_user",
    "get_role_permissions",
    "require_menu",
    "get_menu_context",
    "get_active_company_id",
    "get_current_company_id",
]
