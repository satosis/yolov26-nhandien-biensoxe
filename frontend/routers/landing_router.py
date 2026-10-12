import logging
import re
from datetime import datetime
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, Response

from frontend.deps import get_db, get_optional_user, templates
from frontend.notifications import manager
from .cms_router import get_projects, get_news, get_news_by_slug, get_news_by_id, get_landing_settings
from .inquiries_router import save_inquiry

logger = logging.getLogger(__name__)
router = APIRouter()


def _landing_settings(db, warehouse=False):
    raw = get_landing_settings(db)
    suffix = "_WAREHOUSE" if warehouse else ""
    return {
        "phone": raw.get(f"LANDING_PHONE{suffix}", ""),
        "zalo": raw.get(f"LANDING_ZALO{suffix}", ""),
        "gtm_id": raw.get("GTM_ID", ""),
        "fb_pixel_id": raw.get("FB_PIXEL_ID", ""),
    }


@router.get("/", response_class=HTMLResponse)
async def landing_home(request: Request):
    db = get_db()
    settings = _landing_settings(db, warehouse=True)
    
    projects = get_projects(db)
    for p in projects:
        p['load'] = p.get('load_capacity')
        if p.get('created_at'):
            p['created_at'] = p['created_at'].isoformat() if hasattr(p['created_at'], 'isoformat') else str(p['created_at'])
        
    news_items = get_news(db, limit=3)
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
    db = get_db()
    raw_settings = get_landing_settings(db)
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
    db = get_db()
    settings = _landing_settings(db)
    
    projects = get_projects(db)
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
    db = get_db()
    settings = _landing_settings(db)
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
    settings = _landing_settings(get_db(), warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_services.html",
        {"request": request, "settings": settings, "source_page": "services", "user": get_optional_user(request)},
    )


@router.get("/du-an", response_class=HTMLResponse)
async def landing_projects(request: Request):
    settings = _landing_settings(get_db(), warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_projects.html",
        {"request": request, "settings": settings, "source_page": "warehouse", "user": get_optional_user(request)},
    )


@router.get("/xecau", response_class=HTMLResponse)
async def landing_crane(request: Request):
    settings = _landing_settings(get_db(), warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_crane.html",
        {"request": request, "settings": settings, "source_page": "crane", "user": get_optional_user(request)},
    )


@router.get("/kiem-dinh-can", response_class=HTMLResponse)
async def landing_scale(request: Request):
    settings = _landing_settings(get_db(), warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_scale.html",
        {"request": request, "settings": settings, "source_page": "kiem-dinh-can", "user": get_optional_user(request)},
    )


@router.get("/lien-he", response_class=HTMLResponse)
async def landing_contact(request: Request):
    db = get_db()
    raw_settings = get_landing_settings(db)
    settings = {"phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", "")}
    return templates.TemplateResponse(
        "pages/landing_contact.html",
        {"request": request, "settings": settings, "source_page": "contact", "user": get_optional_user(request)},
    )


@router.get("/tin-tuc/{slug}", response_class=HTMLResponse)
async def landing_news_detail(request: Request, slug: str):
    from fastapi.responses import RedirectResponse
    db = get_db()
    raw_settings = get_landing_settings(db)
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
    }
    if slug.isdigit():
        item = get_news_by_id(db, int(slug))
        if item and item.get("slug"):
            return RedirectResponse(f"/tin-tuc/{item['slug']}", status_code=301)
        return RedirectResponse("/tin-tuc")
    item = get_news_by_slug(db, slug)
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
    db = get_db()
    raw_settings = get_landing_settings(db)
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
        "zalo": raw_settings.get("LANDING_ZALO_WAREHOUSE", ""),
    }
    news_items = get_news(db, limit=50)
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
    db = get_db()
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

    news_items = get_news(db, limit=200)
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



_PHONE_RE = re.compile(r"^[0-9\s\+\-\.]{7,20}$")

_SUCCESS_HTML = """<div id="contact-result"
         class="bg-green-500/10 border border-green-500/30 rounded-xl p-4 text-green-700 text-sm"
         role="status" aria-live="polite">
      <div class="flex items-center gap-2 font-bold mb-1">
        <span class="material-symbols-outlined text-green-600 text-base">check_circle</span>
        Đã gửi thành công!
      </div>
      <p class="text-green-600 text-sm">
        Chúng tôi sẽ liên hệ lại với bạn trong vòng 30 phút trong giờ làm việc. Cảm ơn bạn đã quan tâm!
      </p>
    </div>"""


def _error_html(errors: list[str]) -> str:
    items = "".join(f'<li class="list-disc ml-4">{e}</li>' for e in errors)
    return f"""<div id="contact-result"
         class="bg-rose-500/10 border border-rose-500/30 rounded-xl p-4 text-rose-700 text-sm"
         role="alert">
      <div class="flex items-center gap-2 font-semibold mb-2">
        <span class="material-symbols-outlined text-rose-500 text-base">warning</span>
        Vui lòng kiểm tra lại:
      </div>
      <ul>{items}</ul>
    </div>"""


@router.post("/lien-he", response_class=HTMLResponse)
async def contact_inquiry(
    full_name: str = Form(""),
    phone: str = Form(""),
    company_name: str = Form(""),
    demand: str = Form(""),
    source_page: str = Form("home"),
):
    full_name = full_name.strip()
    phone = phone.strip()
    company_name = company_name.strip()
    demand = demand.strip()

    errors: list[str] = []
    if not full_name or len(full_name) < 2:
        errors.append("Họ tên không hợp lệ (tối thiểu 2 ký tự).")
    if not phone or not _PHONE_RE.match(phone):
        errors.append("Số điện thoại không hợp lệ.")

    if errors:
        return HTMLResponse(_error_html(errors), status_code=400)

    inquiry_id = save_inquiry(get_db(), full_name, phone, company_name, demand, source_page)
    logger.info("New inquiry saved: %s | %s | source=%s", full_name, phone, source_page)

    import asyncio
    asyncio.create_task(manager.broadcast({
        "type": "NEW_INQUIRY",
        "data": {
            "id": inquiry_id,
            "full_name": full_name,
            "phone": phone,
            "demand": demand,
            "source_page": source_page
        }
    }))

    return HTMLResponse(_SUCCESS_HTML, status_code=200)
