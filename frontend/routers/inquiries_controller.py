from dataclasses import dataclass
import logging
import math

from fastapi import Request
from fastapi.responses import HTMLResponse

from frontend.deps import get_menu_context, templates
from frontend.tracking import LEAD_SOURCE_LABELS
import frontend.routers.inquiries_queries as queries

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class InquiryInput:
    """Contact data after it has been normalized and validated at the boundary."""

    full_name: str
    phone: str
    company_name: str
    demand: str
    source_page: str
    gclid: str = ""
    gbraid: str = ""
    wbraid: str = ""
    fbclid: str = ""
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    utm_term: str = ""
    utm_content: str = ""
    landing_url: str = ""
    referrer: str = ""
    lead_source: str = ""
    consent_at: str = ""  # thời điểm khách tích chọn đồng ý xử lý dữ liệu (ISO 8601, UTC); rỗng nếu không qua biểu mẫu


INQUIRY_SERVICES: dict[str, str] = {
    "crane": "Xe cẩu 8T–10T",
    "weights": "Quả cân",
    "container": "Container",
    "solar": "Điện mặt trời",
    "photo": "Máy ảnh & Photobooth",
    "other": "Trang khác",
}


def get_lead_source_summary(days: int = 30) -> list[dict]:
    """Aggregate inquiries by lead_source over the last N days."""
    rows = queries.get_lead_source_summary_select_inquiries(days)
    return rows or []


def get_inquiries(limit: int = 20, offset: int = 0, unread_only: bool = False, service: str = "") -> list:
    return queries.get_inquiries_select_inquiries(limit, offset, unread_only=unread_only, service=service)


def count_all_inquiries(unread_only: bool = False, service: str = "") -> int:
    row = queries.count_all_inquiries_select_inquiries(unread_only=unread_only, service=service)
    return row["cnt"] if row else 0


def count_unread_inquiries(service: str = "") -> int:
    row = queries.count_unread_inquiries_select_inquiries(service=service)
    return row["cnt"] if row else 0


def get_inquiry_service_counts() -> dict[str, int]:
    rows = queries.get_inquiry_service_counts() or []
    return {row["service"]: row["n"] for row in rows}


def update_inquiry_note(inquiry_id: int, note: str) -> bool:
    return bool(queries.update_inquiry_note_update_inquiries(note, inquiry_id))


def delete_inquiry_record(inquiry_id: int) -> bool:
    return bool(queries.delete_inquiry_record_delete_inquiries(inquiry_id))


def save_inquiry(inquiry: InquiryInput) -> int | None:
    row = queries.save_inquiry_insert_inquiries(inquiry)

    if row is None:
        logger.error("Inquiry insert returned no ID")
        return None
    return row["id"]


def source_page_label(code: str) -> str:
    """Tên dịch vụ (và khu vực) của trang khách đã gửi yêu cầu; trang khác giữ nguyên mã."""
    from frontend.crane.locations import AREAS, SERVICES
    from frontend.registry import SERVICE_PAGES

    if code.startswith("area_"):
        service, _, area = code[len("area_"):].partition("_")
        if service in SERVICES and area in AREAS:
            return f"{SERVICES[service]['label']} · {AREAS[area]['name']}"
    page = next((p for p in SERVICE_PAGES if f"{p['key']}_landing" == code), None)
    return page["label"] if page else code


async def inquiries_page(request: Request, page: int, user: dict, service: str = ""):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "inquiries"
    svc = service if service in INQUIRY_SERVICES else ""
    total = count_all_inquiries(service=svc)
    total_pages = max(1, math.ceil(total / 20))
    page_num = max(1, min(page, total_pages))
    offset = (page_num - 1) * 20

    ctx["inquiries"] = get_inquiries(limit=20, offset=offset, service=svc)
    for inquiry in ctx["inquiries"]:
        inquiry["source_label"] = source_page_label(inquiry.get("source_page") or "")
    counts = get_inquiry_service_counts()
    ctx["service"] = svc
    ctx["inquiry_services"] = INQUIRY_SERVICES
    ctx["service_counts"] = counts
    ctx["total_all"] = sum(counts.values())
    ctx["page"] = page_num
    ctx["total_pages"] = total_pages
    ctx["total"] = total
    ctx["page_base_url"] = f"/admin/inquiries?service={svc}&" if svc else "/admin/inquiries?"
    ctx["unread_count"] = count_unread_inquiries(service=svc)
    ctx["source_summary"] = get_lead_source_summary(30)
    ctx["source_labels"] = LEAD_SOURCE_LABELS
    return templates.TemplateResponse("pages/inquiries.html", {"request": request, **ctx})


async def mark_all_read(__user: dict):
    queries.mark_all_inquiries_read()
    return HTMLResponse("ok")


async def mark_read(inquiry_id: int, _user: dict):
    if not queries.mark_inquiry_read(inquiry_id):
        return HTMLResponse("Không tìm thấy yêu cầu tư vấn.", status_code=404)
    return HTMLResponse('<svg class="w-5 h-5 text-green-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>')


async def save_note(inquiry_id: int, note: str, _user: dict):
    if not update_inquiry_note(inquiry_id, note.strip()):
        return HTMLResponse("Không tìm thấy yêu cầu tư vấn.", status_code=404)
    return HTMLResponse("")


async def delete_inquiry_endpoint(inquiry_id: int, _user: dict):
    if not delete_inquiry_record(inquiry_id):
        return HTMLResponse("Không tìm thấy yêu cầu tư vấn.", status_code=404)
    return HTMLResponse("")
