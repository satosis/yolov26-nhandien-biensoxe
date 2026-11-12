import logging
from fastapi import Request

from frontend.deps import get_optional_user, templates
from frontend.routers.cms_controller import get_landing_settings
from . import config

logger = logging.getLogger(__name__)
# Trang chủ dịch vụ xe cẩu (/xe-cau).


async def landing_crane_service(request: Request):
    """Trang Landing Page chuyên biệt cho dịch vụ xe cẩu (chạy quảng cáo).
    
    100% nội dung, menu, hotline, footer và form đều xoay quanh dịch vụ xe cẩu.
    """
    crane_info = config.get_crane_settings()
    raw_settings = get_landing_settings()
    settings = {
        "phone": crane_info["phone"],
        "phone_raw": crane_info["phone_raw"],
        "zalo": crane_info["zalo"],
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }

    resp = templates.TemplateResponse(
        "crane/index.html",
        {
            "request": request,
            "settings": settings,
            "company": crane_info,
            "fleet_items": crane_info["fleet_items"],
            "source_page": "crane_landing",
            "active_page": "crane",
            "user": get_optional_user(request),
        },
    )
    return resp
