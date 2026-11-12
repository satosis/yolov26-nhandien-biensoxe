"""Dịch vụ kiểm định cân của Thép Sơn Lâm: /kiem-dinh-can."""
from fastapi import Request

from frontend.deps import get_optional_user, templates
from frontend.routers.landing_controller import _landing_settings


async def landing_scale(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "scale/index.html",
        {"request": request, "settings": settings, "source_page": "kiem-dinh-can", "user": get_optional_user(request)},
    )
