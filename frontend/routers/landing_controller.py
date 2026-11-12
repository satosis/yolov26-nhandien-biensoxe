import logging
import os
import re
import dataclasses
from datetime import datetime, timezone
from fastapi import BackgroundTasks, Request
from fastapi.responses import JSONResponse, Response

from frontend.deps import get_optional_user, templates
from frontend.notifications import manager
from .cms_controller import count_news, get_news_by_slug, get_news_by_id, get_landing_settings
from .cms_queries import get_projects, get_news
from frontend.pagination import paginate_counts
from .inquiries_controller import InquiryInput, save_inquiry
from frontend.crane.locations import all_pages
from frontend.registry import SERVICE_PAGES
from frontend.tracking import clean_attribution, classify_lead_source, site_host, LEAD_SOURCE_LABELS

logger = logging.getLogger(__name__)


def _landing_settings(warehouse=False):
    raw = get_landing_settings()
    suffix = "_WAREHOUSE" if warehouse else ""
    return {
        "phone": raw.get(f"LANDING_PHONE{suffix}", ""),
        "zalo": raw.get(f"LANDING_ZALO{suffix}", ""),
        "gtm_id": raw.get("GTM_ID", ""),
        "fb_pixel_id": raw.get("FB_PIXEL_ID", ""),
    }


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


async def landing_services(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_services.html",
        {"request": request, "settings": settings, "source_page": "services", "user": get_optional_user(request)},
    )


async def landing_projects(request: Request):
    settings = _landing_settings(warehouse=True)
    return templates.TemplateResponse(
        "pages/landing_projects.html",
        {"request": request, "settings": settings, "source_page": "warehouse", "user": get_optional_user(request)},
    )


async def landing_contact(request: Request):
    raw_settings = get_landing_settings()
    settings = {"phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", "")}
    return templates.TemplateResponse(
        "pages/landing_contact.html",
        {"request": request, "settings": settings, "source_page": "contact", "user": get_optional_user(request)},
    )


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


async def landing_news(request: Request, page: int):
    raw_settings = get_landing_settings()
    settings = {
        "phone": raw_settings.get("LANDING_PHONE_WAREHOUSE", ""),
        "zalo": raw_settings.get("LANDING_ZALO_WAREHOUSE", ""),
    }
    page_data = paginate_counts(count_news(), page=page, per_page=12, base_url="/tin-tuc")
    news_items = get_news(
        limit=page_data["per_page"],
        offset=(page_data["page"] - 1) * page_data["per_page"],
    )
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
        "pagination": page_data,
        "user": user,
    })


async def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    now = datetime.utcnow().strftime("%Y-%m-%d")

    static_urls = [
        ("", "1.0", "weekly"),
        ("/du-an", "0.9", "weekly"),
        ("/nhaxuong", "0.9", "weekly"),
        ("/nha-xuong-co-tiet", "0.9", "weekly"),
        ("/dich-vu", "0.8", "monthly"),
        ("/kiem-dinh-can", "0.8", "monthly"),
        ("/tin-tuc", "0.8", "daily"),
        ("/lien-he", "0.7", "monthly"),
        ("/gioi-thieu", "0.7", "monthly"),
        ("/chinh-sach-quyen-rieng-tu", "0.3", "yearly"),
    ]
    for page in SERVICE_PAGES:
        static_urls.append((page["path"], "0.8", "monthly"))
    for service, area in all_pages():
        static_urls.append((f"/{service}/{area}", "0.7", "monthly"))
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


CONSENT_REQUIRED = "Vui lòng đồng ý với Chính sách quyền riêng tư để gửi yêu cầu."

_PHONE_RE = re.compile(r"^(?:0|84)(?:3|5|7|8|9)\d{8}$")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_inquiry(
    full_name: str,
    phone: str,
    company_name: str,
    demand: str,
    source_page: str,
    attribution: dict | None = None,
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

    attr = attribution or {}
    lead_source = classify_lead_source(attr, site_host())

    return InquiryInput(
        full_name=name,
        phone=phone_number,
        company_name=_WHITESPACE_RE.sub(" ", company_name.strip()),
        demand=demand.strip(),
        source_page=source_page.strip()[:80] or "home",
        gclid=attr.get("gclid", ""),
        gbraid=attr.get("gbraid", ""),
        wbraid=attr.get("wbraid", ""),
        fbclid=attr.get("fbclid", ""),
        utm_source=attr.get("utm_source", ""),
        utm_medium=attr.get("utm_medium", ""),
        utm_campaign=attr.get("utm_campaign", ""),
        utm_term=attr.get("utm_term", ""),
        utm_content=attr.get("utm_content", ""),
        landing_url=attr.get("landing_url", ""),
        referrer=attr.get("referrer", ""),
        lead_source=lead_source,
    ), {}


def send_inquiry_telegram_alert(inquiry: InquiryInput, inquiry_id: int, base_url: str = ""):
    from backend.services.telegram_service import notify_telegram

    company = f" ({inquiry.company_name})" if inquiry.company_name else ""
    raw_base = base_url.rstrip("/") if base_url else "http://127.0.0.1:8001"
        
    # Telegram Bot API rejects 'localhost' in inline keyboard URLs; replace with 127.0.0.1 if on local
    safe_base = raw_base.replace("://localhost", "://127.0.0.1")
    admin_link = f"{safe_base}/admin/inquiries"

    source_label = LEAD_SOURCE_LABELS.get(inquiry.lead_source, inquiry.lead_source or "Trực tiếp")
    source_line = f"Nguồn: {source_label}"
    if inquiry.utm_campaign:
        source_line += f" · chiến dịch {inquiry.utm_campaign}"
    if inquiry.utm_term:
        source_line += f" · từ khóa {inquiry.utm_term}"
    source_line = source_line[:300]

    partner_line = ""
    if inquiry.lead_source == "partner":
        p_label = inquiry.utm_campaign or ""
        from backend.services import partners
        p = partners.get_by_code(inquiry.utm_campaign) if inquiry.utm_campaign else None
        if p and p.get("name"):
            p_label = p["name"]
        partner_line = f"\nĐối tác: {p_label}"

    telegram_msg = (
        f"🔔 {inquiry.full_name}{company} · {inquiry.phone}\n"
            f"Nhu cầu: {inquiry.demand or 'Tư vấn xe cẩu'}\n"
            f"{source_line}"
            f"{partner_line}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "🌐 Mở trang quản trị",
                    "url": admin_link,
                }
            ]
        ]
    }

    notify_telegram(telegram_msg, important=True, prefix="", reply_markup=reply_markup)
    logger.info("Dispatched Telegram alert for inquiry #%s with inline button", inquiry_id)


async def contact_inquiry(
    request: Request,
    background_tasks: BackgroundTasks,
    full_name: str,
    phone: str,
    company_name: str,
    demand: str,
    source_page: str,
    gclid: str,
    gbraid: str,
    wbraid: str,
    fbclid: str,
    utm_source: str,
    utm_medium: str,
    utm_campaign: str,
    utm_term: str,
    utm_content: str,
    landing_url: str,
    referrer: str,
    consent: str,
):
    raw_attribution = {
        "gclid": gclid,
        "gbraid": gbraid,
        "wbraid": wbraid,
        "fbclid": fbclid,
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
        "utm_term": utm_term,
        "utm_content": utm_content,
        "landing_url": landing_url,
        "referrer": referrer,
    }
    attribution = clean_attribution(raw_attribution)
    inquiry, errors = _normalise_inquiry(full_name, phone, company_name, demand, source_page, attribution=attribution)
    if consent != "1":
        errors = {**errors, "consent": CONSENT_REQUIRED}
    if errors:
        return JSONResponse({"ok": False, "message": next(iter(errors.values())), "fields": errors}, status_code=422)
    inquiry = dataclasses.replace(inquiry, consent_at=datetime.now(timezone.utc).isoformat())

    inquiry_id = save_inquiry(inquiry)
    if inquiry_id is None:
        return JSONResponse({"ok": False, "message": "Không thể lưu yêu cầu tư vấn. Vui lòng thử lại."}, status_code=500)
    logger.info("New inquiry saved: id=%s source=%s", inquiry_id, inquiry.source_page)

    # 1. Báo thời gian thực lên giao diện Web Admin qua WebSocket
    background_tasks.add_task(manager.broadcast, {
        "type": "NEW_INQUIRY",
        "data": {
            "id": inquiry_id,
            "full_name": inquiry.full_name,
            "phone": inquiry.phone,
            "demand": inquiry.demand,
            "source_page": inquiry.source_page,
            "lead_source": inquiry.lead_source,
        }
    })

    # Lấy base URL từ APP_URL trong .env; nếu chưa cấu hình mới suy ra từ request headers
    configured_url = os.environ.get("APP_URL", "").strip()
    if configured_url:
        base_url = configured_url.rstrip("/")
    else:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "127.0.0.1:8001"
        base_url = f"{proto}://{host}"

    # 2. Gửi cảnh báo tức thì qua Telegram bot kèm link trực tiếp
    background_tasks.add_task(send_inquiry_telegram_alert, inquiry, inquiry_id, base_url)

    return JSONResponse({
        "ok": True,
        "message": "Gửi thành công! Chúng tôi sẽ liên hệ lại trong vòng 30 phút.",
        "inquiry_id": inquiry_id,
    })
