import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from frontend.deps import get_optional_user, templates
from frontend.routers.cms_router import get_landing_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Dịch vụ Kiểm định Cân"])


@router.get("/kiem-dinh-can", response_class=HTMLResponse)
async def landing_scale_service(request: Request):
    raw_settings = get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE") or raw_settings.get("LANDING_PHONE", ""),
        "zalo": raw_settings.get("LANDING_ZALO_WAREHOUSE") or raw_settings.get("LANDING_ZALO", ""),
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }

    return templates.TemplateResponse(
        "pages/landing_scale.html",
        {
            "request": request,
            "settings": settings,
            "source_page": "kiem-dinh-can",
            "user": get_optional_user(request),
        },
    )
