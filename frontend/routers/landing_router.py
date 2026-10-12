import logging
import re
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response

from frontend.deps import get_optional_user, templates
from frontend.notifications import manager
from .cms_router import get_projects, get_news, get_news_by_slug, get_news_by_id, get_landing_settings
from .inquiries_router import InquiryInput, save_inquiry

logger = logging.getLogger(__name__)
router = APIRouter()


def _landing_settings(warehouse=False):
    raw = get_landing_settings()
    suffix = "_WAREHOUSE" if warehouse else ""
    return {
        "phone": raw.get(f"LANDING_PHONE{suffix}", ""),
        "zalo": raw.get(f"LANDING_ZALO{suffix}", ""),
        "gtm_id": raw.get("GTM_ID", ""),
        "fb_pixel_id": raw.get("FB_PIXEL_ID", ""),
    }


@router.get("/", response_class=HTMLResponse)
async def landing_home(request: Request):
    settings = _landing_settings(warehouse=True)
    
    projects = get_projects()
    for p in projects:
        p['load'] = p.get('load_capacity')
        if p.get('created_at'):
            p['created_at'] = p['created_at'].isoformat() if hasattr(p['created_at'], 'isoformat') else str(p['created_at'])
        
    news_items = get_news(limit=3)
    for n in news_items:
        if n.get('publish_date'):
            n['publish_date_str'] = n['publish_date'].strftime('%d/%m/%Y') if hasattr(n['publish_date'], 'strftime') else str(n['publish_date'])
        if n.get('created_at'):
            n['created_at'] = n['created_at'].isoformat() if hasattr(n['created_at'], 'isoformat') else str(n['created_at'])

    return templates.TemplateResponse(
        "pages/landing_home.html",
        {
            "request": request, 
            "settings": settings, 
            "projects": projects, 
            "news_items": news_items,
            "source_page": "home", 
            "user": get_optional_user(request)
        },
    )


@router.get("/gioi-thieu", response_class=HTMLResponse)
async def landing_about(request: Request):
    raw_settings = get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE", ""),
        "zalo": raw_settings.get("LANDING_ZALO", ""),
    }
    return templates.TemplateResponse(
        "pages/landing_about.html",
        {"request": request, "settings": settings, "source_page": "about", "user": get_optional_user(request)},
    )


@router.get("/nhaxuong", response_class=HTMLResponse)
async def landing_warehouse(request: Request):
    settings = _landing_settings()
    
    projects = get_projects()
    for p in projects:
        p['load'] = p.get('load_capacity')
        if p.get('created_at'):
            p['created_at'] = p['created_at'].isoformat() if hasattr(p['created_at'], 'isoformat') else str(p['created_at'])

    return templates.TemplateResponse(
        "pages/landing_warehouse.html",
        {
            "request": request, 
            "settings": settings, 
            "projects": projects, 
            "source_page": "warehouse", 
            "user": get_optional_user(request)
        },
    )


@router.get("/nha-xuong-co-tiet", response_class=HTMLResponse)
async def landing_co_tiet(request: Request):
    settings = _landing_settings()
    return templates.TemplateResponse(
        "pages/landing_co_tiet.html",
        {
            "request": request, 
            "settings": settings, 
            "source_page": "co_tiet", 
            "user": get_optional_user(request),
        },
    )


@router.get("/dich-vu", response_class=HTMLResponse)
async def landing_services(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_services.html",
        {"request": request, "settings": settings, "source_page": "services", "user": get_optional_user(request)},
    )


@router.get("/du-an", response_class=HTMLResponse)
async def landing_projects(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_projects.html",
        {"request": request, "settings": settings, "source_page": "warehouse", "user": get_optional_user(request)},
    )


@router.get("/xecau", response_class=HTMLResponse)
async def landing_crane(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_crane.html",
        {"request": request, "settings": settings, "source_page": "crane", "user": get_optional_user(request)},
    )


@router.get("/kiem-dinh-can", response_class=HTMLResponse)
async def landing_scale(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_scale.html",
        {"request": request, "settings": settings, "source_page": "kiem-dinh-can", "user": get_optional_user(request)},
    )


@router.get("/lien-he", response_class=HTMLResponse)
async def landing_contact(request: Request):
    raw_settings = get_landing_settings()
    settings = {"phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", "")}
    return templates.TemplateResponse(
        "pages/landing_contact.html",
        {"request": request, "settings": settings, "source_page": "contact", "user": get_optional_user(request)},
    )


@router.get("/tin-tuc/{slug}", response_class=HTMLResponse)
async def landing_news_detail(request: Request, slug: str):
    from fastapi.responses import RedirectResponse
    raw_settings = get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
    }
    if slug.isdigit():
        item = get_news_by_id(int(slug))
        if item and item.get("slug"):
            return RedirectResponse(f"/tin-tuc/{item['slug']}", status_code=301)
        return RedirectResponse("/tin-tuc")
    item = get_news_by_slug(slug)
    if not item:
        return RedirectResponse("/tin-tuc")
    user = get_optional_user(request)
    return templates.TemplateResponse("pages/landing_news_detail.html", {
        "request": request,
        "settings": settings,
        "item": item,
        "user": user,
    })


@router.get("/tin-tuc", response_class=HTMLResponse)
async def landing_news(request: Request):
    raw_settings = get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
        "zalo": raw_settings.get("LANDING_ZALO_WAREHOUSE", ""),
    }
    news_items = get_news(limit=50)
    for item in news_items:
        if item.get("publish_date"):
            d = item["publish_date"]
            if hasattr(d, "strftime"):
                item["publish_date_str"] = d.strftime("%d/%m/%Y")
            else:
                item["publish_date_str"] = str(d)
    user = get_optional_user(request)
    return templates.TemplateResponse("pages/landing_news.html", {
        "request": request,
        "settings": settings,
        "news_items": news_items,
        "user": user,
    })



@router.get("/sitemap.xml")
async def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    now = datetime.utcnow().strftime("%Y-%m-%d")

    static_urls = [
        ("", "1.0", "weekly"),
        ("/du-an", "0.9", "weekly"),
        ("/nhaxuong", "0.9", "weekly"),
        ("/nha-xuong-co-tiet", "0.9", "weekly"),
        ("/dich-vu", "0.8", "monthly"),
        ("/xecau", "0.8", "monthly"),
        ("/kiem-dinh-can", "0.8", "monthly"),
        ("/tin-tuc", "0.8", "daily"),
        ("/lien-he", "0.7", "monthly"),
        ("/gioi-thieu", "0.7", "monthly"),
    ]
    urls_xml = ""
    for path, priority, freq in static_urls:
        urls_xml += f"""  <url>
    <loc>{base}{path}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>{freq}</changefreq>
    <priority>{priority}</priority>
  </url>\n"""

    news_items = get_news(limit=200)
    for item in news_items:
        if not item.get("is_published", True):
            continue
        d = item.get("publish_date")
        date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else now
        urls_xml += f"""  <url>
    <loc>{base}/tin-tuc/{item['id']}</loc>
    <lastmod>{date_str}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>\n"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>"""
    return Response(content=xml, media_type="application/xml")


@router.get("/robots.txt")
async def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /camera/
Disallow: /login
Disallow: /logout

Sitemap: {base}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")



_PHONE_RE = re.compile(r"^(?:0|84)(?:3|5|7|8|9)\d{8}$")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_inquiry(
    full_name: str,
    phone: str,
    company_name: str,
    demand: str,
    source_page: str,
) -> tuple[InquiryInput | None, dict[str, str]]:
    """Convert raw form values into the sole internal contact-data format."""
    name = _WHITESPACE_RE.sub(" ", full_name.strip())
    phone_number = re.sub(r"[\s.\-]", "", phone.strip())
    if phone_number.startswith("+"):
        phone_number = phone_number[1:]
    if phone_number.startswith("84"):
        phone_number = f"0{phone_number[2:]}"

    errors: dict[str, str] = {}
    if len(name) < 2:
        errors["full_name"] = "Vui lòng nhập họ tên (ít nhất 2 ký tự)."
    if not _PHONE_RE.fullmatch(phone_number):
        errors["phone"] = "Số điện thoại không hợp lệ."
    if errors:
        return None, errors

    return InquiryInput(
        full_name=name,
        phone=phone_number,
        company_name=_WHITESPACE_RE.sub(" ", company_name.strip()),
        demand=demand.strip(),
        source_page=source_page.strip()[:80] or "home",
    ), {}


@router.post("/lien-he", response_class=JSONResponse)
async def contact_inquiry(
    background_tasks: BackgroundTasks,
    full_name: str = Form(""),
    phone: str = Form(""),
    company_name: str = Form(""),
    demand: str = Form(""),
    source_page: str = Form("home"),
):
    inquiry, errors = _normalise_inquiry(full_name, phone, company_name, demand, source_page)
    if errors:
        return JSONResponse({"ok": False, "message": next(iter(errors.values())), "fields": errors}, status_code=422)

    inquiry_id = save_inquiry(inquiry)
    if inquiry_id is None:
        logger.error("Contact inquiry was not stored")
        return JSONResponse({"ok": False, "message": "Không thể lưu yêu cầu tư vấn. Vui lòng thử lại."}, status_code=500)
    logger.info("New inquiry saved: id=%s source=%s", inquiry_id, inquiry.source_page)

    background_tasks.add_task(manager.broadcast, {
        "type": "NEW_INQUIRY",
        "data": {
            "id": inquiry_id,
            "full_name": inquiry.full_name,
            "phone": inquiry.phone,
            "demand": inquiry.demand,
            "source_page": inquiry.source_page,
        }
    })

    return JSONResponse({
        "ok": True,
        "message": "Gửi thành công! Chúng tôi sẽ liên hệ lại trong vòng 30 phút.",
        "inquiry_id": inquiry_id,
    })
