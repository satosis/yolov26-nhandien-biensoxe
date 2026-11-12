import logging
from fastapi import Request

from frontend.deps import get_optional_user, templates
from frontend.routers.cms_controller import get_landing_settings
from frontend.catalog import catalog_items
from . import config

logger = logging.getLogger(__name__)
# Landing Page Quả Cân Chuẩn: /qua-can


async def landing_test_weights_service(request: Request):
    """Trang Landing Page chuyên biệt cho dịch vụ cho thuê quả cân chuẩn kiểm định cân điện tử."""
    weights_info = config.get_test_weights_settings()
    raw_settings = get_landing_settings()
    settings = {
        "phone": weights_info["phone"],
        "phone_raw": weights_info["phone_raw"],
        "zalo": weights_info["zalo"],
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }

    return templates.TemplateResponse(
        "weights/index.html",
        {
            "request": request,
            "settings": settings,
            "company": weights_info,
            "weight_items": catalog_items("qua-can"),
            "source_page": "weights_landing",
            "source_page_form": "weights_landing",
            "contact_variant": "weights",
            "active_page": "weights",
            "user": get_optional_user(request),
        },
    )
