"""Dịch vụ cho thuê nhà xưởng xây sẵn của Thép Sơn Lâm: /nhaxuong (KCN Trung Hà) và /nha-xuong-co-tiet (KCN Cổ Tiết)."""
from fastapi import Request

from frontend.deps import get_optional_user, templates
from frontend.routers.cms_queries import get_projects
from frontend.routers.landing_controller import _landing_settings


async def landing_warehouse(request: Request):
    settings = _landing_settings()
    
    projects = get_projects()
    for p in projects:
        p['load'] = p.get('load_capacity')
        if p.get('created_at'):
            p['created_at'] = p['created_at'].isoformat() if hasattr(p['created_at'], 'isoformat') else str(p['created_at'])

    return templates.TemplateResponse(
        "warehouse/trung_ha.html",
        {
            "request": request, 
            "settings": settings, 
            "projects": projects, 
            "source_page": "warehouse", 
            "user": get_optional_user(request)
        },
    )


async def landing_co_tiet(request: Request):
    settings = _landing_settings()
    return templates.TemplateResponse(
        "warehouse/co_tiet.html",
        {
            "request": request, 
            "settings": settings, 
            "source_page": "co_tiet", 
            "user": get_optional_user(request),
        },
    )
