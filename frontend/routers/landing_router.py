"""Landing page router — public routes, no auth required."""
import logging
import re
from datetime import datetime
from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, Response
from pathlib import Path

from frontend.deps import get_db, get_optional_user
from frontend.notifications import manager

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
async def landing_home(request: Request):
    """Home page - professional company landing page."""
    db = get_db()
    raw_settings = db.get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
        "zalo": raw_settings.get("LANDING_ZALO_WAREHOUSE", ""),
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }
    
    projects = db.get_projects()
    if not projects:
        logger.warning("No projects found in DB, using fallback data.")
        projects = [
            {"id": 1, "name": "Vĩnh Phúc Core Logistics Park", "location": "Vĩnh Phúc", "image": "/static/brain/wh_real1.jpg", "area": "15,000 m²", "load_capacity": "5 Tons/m²"},
            {"id": 2, "name": "Hải Phòng Industrial Center", "location": "Hải Phòng", "image": "/static/brain/wh_real2.jpg", "area": "22,000 m²", "load_capacity": "8 Tons/m²"}
        ]
    
    print(f"DEBUG: Fetched {len(projects) if projects else 0} projects from DB")
    # Normalize for template and fix JSON serialization issues (datetime)
    for p in projects:
        p['load'] = p.get('load_capacity')
        if p.get('created_at'):
            p['created_at'] = p['created_at'].isoformat() if hasattr(p['created_at'], 'isoformat') else str(p['created_at'])
        
    news_items = db.get_news(limit=3)
    if not news_items:
        news_items = [
            {"id": 1, "title": "Xu hướng nhà xưởng 2026", "category": "Thị trường", "publish_date_str": "29/04/2026", "image": "/static/brain/news1.jpg"}
        ]
    
    print(f"DEBUG: Fetched {len(news_items) if news_items else 0} news items from DB")
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
    """About us page."""
    db = get_db()
    raw_settings = db.get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE", ""),
        "zalo": raw_settings.get("LANDING_ZALO", ""),
    }
    return templates.TemplateResponse(
        "pages/landing_about.html",
        {"request": request, "settings": settings, "source_page": "about", "user": get_optional_user(request)},
    )


@router.get("/nha-xuong", response_class=HTMLResponse)
async def landing_warehouse(request: Request):
    """Spoke 1 — Nha xuong cho thue."""
    db = get_db()
    raw_settings = db.get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE", ""),
        "zalo": raw_settings.get("LANDING_ZALO", ""),
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }
    
    projects = db.get_projects()
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
    """Spoke 1b — CCN Co Tiet nha xuong cho thue."""
    db = get_db()
    raw_settings = db.get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE", ""),
        "zalo": raw_settings.get("LANDING_ZALO", ""),
        "gtm_id": raw_settings.get("GTM_ID", ""),
        "fb_pixel_id": raw_settings.get("FB_PIXEL_ID", ""),
    }
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
    """Dịch vụ & giải pháp — build-to-suit, vận hành, pháp lý FDI, hạ tầng."""
    return templates.TemplateResponse(
        "pages/landing_services.html",
        {"request": request, "source_page": "services", "user": get_optional_user(request)},
    )


@router.get("/tin-tuc/{slug}", response_class=HTMLResponse)
async def landing_news_detail(request: Request, slug: str):
    """Chi tiet bai viet tin tuc — slug hoac legacy numeric ID."""
    from fastapi.responses import RedirectResponse
    db = get_db()
    raw_settings = db.get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
    }
    # Legacy: numeric ID → redirect to slug URL
    if slug.isdigit():
        item = db.get_news_by_id(int(slug))
        if item and item.get("slug"):
            return RedirectResponse(f"/tin-tuc/{item['slug']}", status_code=301)
        return RedirectResponse("/tin-tuc")
    item = db.get_news_by_slug(slug)
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
    """Tin tức & Cập nhật — danh sách tin tức từ DB."""
    db = get_db()
    raw_settings = db.get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
        "zalo": raw_settings.get("LANDING_ZALO_WAREHOUSE", ""),
    }
    news_items = db.get_news(limit=50)
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


# ---------------------------------------------------------------------------
# SEO — sitemap.xml + robots.txt
# ---------------------------------------------------------------------------

@router.get("/sitemap.xml")
async def sitemap(request: Request):
    db = get_db()
    base = str(request.base_url).rstrip("/")
    now = datetime.utcnow().strftime("%Y-%m-%d")

    static_urls = [
        ("", "1.0", "weekly"),
        ("/nha-xuong", "0.9", "weekly"),
        ("/nha-xuong-co-tiet", "0.9", "weekly"),
        ("/dich-vu", "0.8", "monthly"),
        ("/tin-tuc", "0.8", "daily"),
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

    news_items = db.get_news(limit=200)
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


# ---------------------------------------------------------------------------
# Form handlers
# ---------------------------------------------------------------------------

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
    """HTMX contact form — validate + save to inquiries table."""
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

    try:
        db = get_db()
        inquiry_id = db.save_inquiry(full_name, phone, company_name, demand, source_page)
        logger.info("New inquiry saved: %s | %s | source=%s", full_name, phone, source_page)
        
        # Broadcast via WebSocket
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
    except Exception as exc:
        logger.warning("Could not save inquiry: %s", exc)

    return HTMLResponse(_SUCCESS_HTML, status_code=200)


# Legacy warehouse form redirect — keep old URL working
@router.post("/nha-xuong/lien-he", response_class=HTMLResponse)
async def warehouse_contact_legacy(
    name: str = Form(""),
    phone: str = Form(""),
    area: str = Form(""),
):
    """Legacy form endpoint — proxies to new save_inquiry."""
    name = name.strip()
    phone = phone.strip()

    errors: list[str] = []
    if not name or len(name) < 2:
        errors.append("Họ tên không hợp lệ (tối thiểu 2 ký tự).")
    if not phone or not _PHONE_RE.match(phone):
        errors.append("Số điện thoại không hợp lệ.")

    if errors:
        return HTMLResponse(_error_html(errors), status_code=400)

    try:
        db = get_db()
        db.save_inquiry(name, phone, "", area, "warehouse_legacy")
        logger.info("Legacy warehouse contact saved: %s | %s", name, phone)
    except Exception as exc:
        logger.warning("Could not save legacy contact: %s", exc)

    return HTMLResponse(_SUCCESS_HTML, status_code=200)
