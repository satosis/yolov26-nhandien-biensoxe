"""Router quản lý khách tiềm năng: danh sách, thêm, sửa, xóa, đổi trạng thái và nhập từ văn bản."""
from __future__ import annotations

import logging
from datetime import date

from fastapi import Request
from fastapi.responses import RedirectResponse

from backend.services import prospects as prospects_service
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.redirects import redirect_with_query

logger = logging.getLogger(__name__)

BASE_URL = "/admin/advertising/prospects"
PER_PAGE = 20


def _back(status: str = "", q: str = "", msg: str = "", error: str = "") -> RedirectResponse:
    return redirect_with_query(BASE_URL, status=status, q=q, msg=msg, error=error)


def list_page(request: Request, status: str, q: str, page: int, user: dict):
    """Trang danh sách khách tiềm năng (chỉ đọc DB)."""
    clean_status = status.strip()
    clean_q = q.strip()
    offset = (page - 1) * PER_PAGE

    total = prospects_service.count_prospects(status=clean_status, q=clean_q)
    rows = prospects_service.list_prospects(status=clean_status, q=clean_q, limit=PER_PAGE, offset=offset)
    st_counts = prospects_service.status_counts()

    pagination = paginate_counts(
        total,
        page=page,
        per_page=PER_PAGE,
        base_url=BASE_URL,
        extra_params={"status": clean_status, "q": clean_q},
    )

    ctx = get_menu_context(request, user)
    ctx.update({
        "current_menu": "prospects",
        "page_title": "Khách tiềm năng",
        "prospects": rows,
        "total": total,
        "status_counts": st_counts,
        "pagination": pagination,
        "status": clean_status,
        "q": clean_q,
        "SEGMENTS": prospects_service.SEGMENTS,
        "STATUSES": prospects_service.STATUSES,
        "SOURCES": prospects_service.SOURCES,
        "today": date.today(),
        "msg": request.query_params.get("msg", ""),
        "error": request.query_params.get("error", ""),
    })
    return templates.TemplateResponse("pages/advertising/prospects.html", {"request": request, **ctx})


def create_prospect(
    name: str,
    company: str,
    phone: str,
    zalo: str,
    segment: str,
    province: str,
    source: str,
    status: str,
    next_contact: str,
    note: str,
    filter_status: str,
    filter_q: str,
    user: dict,
):
    """Thêm mới một khách tiềm năng."""
    if prospects_service.phone_taken(phone):
        return _back(filter_status, filter_q, error="Số điện thoại đã có.")

    new_id = prospects_service.create({
        "name": name,
        "company": company,
        "phone": phone,
        "zalo": zalo,
        "segment": segment,
        "province": province,
        "source": source,
        "status": status,
        "next_contact": next_contact,
        "note": note,
    })

    if not new_id:
        return _back(filter_status, filter_q, error="Không lưu được khách.")
    return _back(filter_status, filter_q, msg="Đã lưu.")


def import_prospects(text: str, segment: str, province: str, filter_status: str, filter_q: str, user: dict):
    """Nhập danh sách khách từ văn bản phân tách cột."""
    res = prospects_service.import_text(text, segment=segment, province=province, source="import")
    added = res.get("added", 0)
    duplicate = res.get("duplicate", 0)

    msg = f"Đã thêm {added} khách."
    if duplicate > 0:
        msg += f" (trùng {duplicate})"
    return _back(filter_status, filter_q, msg=msg)


def update_prospect(
    prospect_id: int,
    name: str,
    company: str,
    phone: str,
    zalo: str,
    segment: str,
    province: str,
    source: str,
    status: str,
    next_contact: str,
    note: str,
    filter_status: str,
    filter_q: str,
    user: dict,
):
    """Cập nhật thông tin khách tiềm năng."""
    if prospects_service.phone_taken(phone, prospect_id):
        return _back(filter_status, filter_q, error="Số điện thoại đã có.")

    ok =prospects_service.update(prospect_id, {
        "name": name,
        "company": company,
        "phone": phone,
        "zalo": zalo,
        "segment": segment,
        "province": province,
        "source": source,
        "status": status,
        "next_contact": next_contact,
        "note": note,
    })

    if not ok:
        return _back(filter_status, filter_q, error="Không lưu được khách.")
    return _back(filter_status, filter_q, msg="Đã lưu.")


def delete_prospect(prospect_id: int, filter_status: str, filter_q: str, user: dict):
    """Xóa một khách tiềm năng."""
    prospects_service.delete(prospect_id)
    return _back(filter_status, filter_q, msg="Đã xóa.")


def set_prospect_status(
    prospect_id: int,
    status: str,
    next_contact: str,
    filter_status: str,
    filter_q: str,
    user: dict,
):
    """Cập nhật nhanh trạng thái khách tiềm năng."""
    ok = prospects_service.set_status(prospect_id, status, next_contact=next_contact or None)
    if not ok:
        return _back(filter_status, filter_q, error="Không lưu được khách.")
    return _back(filter_status, filter_q, msg="Đã lưu.")
