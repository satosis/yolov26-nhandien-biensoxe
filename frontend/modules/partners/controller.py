"""Router quản lý đối tác giới thiệu: danh sách, thêm, sửa, xóa và link giới thiệu."""
from __future__ import annotations

import logging

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from backend.services import partners as partners_service
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import paginate_counts
from frontend.redirects import redirect_with_query

logger = logging.getLogger(__name__)

BASE_URL = "/admin/advertising/partners"
PER_PAGE = 20


def _redirect(msg: str = "", error: str = "") -> RedirectResponse:
    return redirect_with_query(BASE_URL, msg=msg, error=error)


def list_page(request: Request, q: str, page: int, user: dict):
    clean_q = (q or "").strip()
    page_num = max(1, int(page or 1))
    offset = (page_num - 1) * PER_PAGE

    rows = partners_service.list_partners(q=clean_q, limit=PER_PAGE, offset=offset)
    total = partners_service.count_partners(q=clean_q)
    stats_data = partners_service.stats()
    base_url = str(request.base_url)

    for p in rows:
        code = p.get("code") or ""
        p["link"] = partners_service.link_for(code, base_url)
        p["stats"] = stats_data.get(code.lower(), {"total": 0, "last30": 0})

    pagination = paginate_counts(
        total=total,
        page=page_num,
        per_page=PER_PAGE,
        base_url=BASE_URL,
        extra_params={"q": clean_q} if clean_q else None,
    )

    context = {
        "request": request,
        "partners": rows,
        "pagination": pagination,
        "kinds": partners_service.KINDS,
        "q": clean_q,
        "msg": request.query_params.get("msg", ""),
        "error": request.query_params.get("error", ""),
        "current_menu": "partners",
        **get_menu_context(request, user),
    }
    return templates.TemplateResponse("pages/advertising/partners.html", context)


def create_partner(
    name: str,
    phone: str,
    kind: str,
    code: str,
    commission_percent: str,
    active: str,
    note: str,
    user: dict,
):
    clean_name = name.strip()
    clean_code = code.strip()
    if clean_code:
        if not partners_service.valid_code(clean_code):
            return _redirect(error="Không lưu được đối tác.")
        if partners_service.get_by_code(clean_code):
            return _redirect(error="Mã đã có.")

    try:
        comm = float(commission_percent)
    except (ValueError, TypeError):
        comm = 3.0

    is_active = (active in ("1", "true", "True", "on"))

    partner_id = partners_service.create({
        "name": clean_name,
        "phone": phone.strip(),
        "kind": kind.strip(),
        "code": clean_code,
        "commission_percent": comm,
        "active": is_active,
        "note": note.strip(),
    })
    if not partner_id:
        return _redirect(error="Không lưu được đối tác.")
    return _redirect(msg="Đã lưu.")


def update_partner(
    partner_id: int,
    name: str,
    phone: str,
    kind: str,
    code: str,
    commission_percent: str,
    active: str,
    note: str,
    user: dict,
):
    clean_name = name.strip()
    clean_code = code.strip()
    if clean_code:
        if not partners_service.valid_code(clean_code):
            return _redirect(error="Không lưu được đối tác.")
        existing = partners_service.get_by_code(clean_code)
        if existing and existing.get("id") != partner_id:
            return _redirect(error="Mã đã có.")

    try:
        comm = float(commission_percent)
    except (ValueError, TypeError):
        comm = 3.0

    is_active = (active in ("1", "true", "True", "on"))

    ok = partners_service.update(partner_id, {
        "name": clean_name,
        "phone": phone.strip(),
        "kind": kind.strip(),
        "code": clean_code,
        "commission_percent": comm,
        "active": is_active,
        "note": note.strip(),
    })
    if not ok:
        return _redirect(error="Không lưu được đối tác.")
    return _redirect(msg="Đã lưu.")


def delete_partner(
    partner_id: int,
    user: dict = Depends(require_menu("partners")),
):
    partners_service.delete(partner_id)
    return _redirect(msg="Đã xóa.")
