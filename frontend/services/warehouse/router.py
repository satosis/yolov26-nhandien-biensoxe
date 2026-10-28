import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from frontend.deps import get_optional_user, templates
from frontend.routers.cms_router import get_projects, get_landing_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Dịch vụ Nhà xưởng"])


def _warehouse_settings():
    raw = get_landing_settings()
    return {
        "phone": raw.get("LANDING_PHONE_WAREHOUSE") or raw.get("LANDING_PHONE", ""),
        "zalo": raw.get("LANDING_ZALO_WAREHOUSE") or raw.get("LANDING_ZALO", ""),
        "gtm_id": raw.get("GTM_ID", ""),
        "fb_pixel_id": raw.get("FB_PIXEL_ID", ""),
    }


@router.get("/nhaxuong", response_class=HTMLResponse)
async def landing_warehouse_trung_ha(request: Request):
    settings = _warehouse_settings()
    projects = get_projects()
    for p in projects:
        p["load"] = p.get("load_capacity")
        if p.get("created_at"):
            p["created_at"] = p["created_at"].isoformat() if hasattr(p["created_at"], "isoformat") else str(p["created_at"])

    return templates.TemplateResponse(
        "pages/landing_warehouse.html",
        {
            "request": request,
            "settings": settings,
            "projects": projects,
            "source_page": "warehouse",
            "user": get_optional_user(request),
        },
    )


@router.get("/nha-xuong-co-tiet", response_class=HTMLResponse)
async def landing_warehouse_co_tiet(request: Request):
    settings = _warehouse_settings()
    return templates.TemplateResponse(
        "pages/landing_co_tiet.html",
        {
            "request": request,
            "settings": settings,
            "source_page": "co_tiet",
            "user": get_optional_user(request),
        },
    )
