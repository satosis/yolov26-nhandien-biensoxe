"""Cầu nối HTTP của frontend.routers.landing_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, BackgroundTasks, Form
import frontend.routers.landing_controller as controller


async def landing_home(request: Request):
    return await controller.landing_home(request=request)


async def landing_about(request: Request):
    return await controller.landing_about(request=request)


async def landing_services(request: Request):
    return await controller.landing_services(request=request)


async def landing_projects(request: Request):
    return await controller.landing_projects(request=request)


async def landing_contact(request: Request):
    return await controller.landing_contact(request=request)


async def landing_news_detail(request: Request, slug: str):
    return await controller.landing_news_detail(request=request, slug=slug)


async def landing_news(request: Request, page: int = Query(1, ge=1)):
    return await controller.landing_news(request=request, page=page)


async def sitemap(request: Request):
    return await controller.sitemap(request=request)


async def robots(request: Request):
    return await controller.robots(request=request)


async def contact_inquiry(
    request: Request,
    background_tasks: BackgroundTasks,
    full_name: str = Form(''),
    phone: str = Form(''),
    company_name: str = Form(''),
    demand: str = Form(''),
    source_page: str = Form('home'),
    gclid: str = Form(''),
    gbraid: str = Form(''),
    wbraid: str = Form(''),
    fbclid: str = Form(''),
    utm_source: str = Form(''),
    utm_medium: str = Form(''),
    utm_campaign: str = Form(''),
    utm_term: str = Form(''),
    utm_content: str = Form(''),
    landing_url: str = Form(''),
    referrer: str = Form(''),
    consent: str = Form(''),
):
    return await controller.contact_inquiry(request=request, background_tasks=background_tasks, full_name=full_name, phone=phone, company_name=company_name, demand=demand, source_page=source_page, gclid=gclid, gbraid=gbraid, wbraid=wbraid, fbclid=fbclid, utm_source=utm_source, utm_medium=utm_medium, utm_campaign=utm_campaign, utm_term=utm_term, utm_content=utm_content, landing_url=landing_url, referrer=referrer, consent=consent)
