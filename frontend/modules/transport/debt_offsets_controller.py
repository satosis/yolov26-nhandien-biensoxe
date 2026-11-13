"""Router for Bù trừ công nợ TK 331."""

from __future__ import annotations

from datetime import date
import logging
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from frontend.context.company_context import get_current_company_id
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.modules.transport.debt_offset_service import (
    count_offsets,
    create_offset,
    delete_offset,
    list_offsets,
    list_vendor_partners,
    update_offset,
)

logger = logging.getLogger(__name__)


async def debt_offsets_page(request: Request, page: int, message: str, error: str, user: dict):
    ctx = get_menu_context(request, user)
    cid = get_current_company_id()

    total = count_offsets(cid) if cid else 0
    pagination = paginate_counts(
        total,
        page=page,
        per_page=20,
        base_url="/admin/transport/debt-offsets",
    )
    offsets = list_offsets(
        cid,
        limit=pagination["per_page"],
        offset=(pagination["page"] - 1) * pagination["per_page"],
    ) if cid else []

    partners = list_vendor_partners()
    today_iso = date.today().isoformat()

    ctx.update({
        "current_menu": "vehicles",
        "transport_section": "debt_offsets",
        "transport_active": "debt_offsets",
        "offsets": offsets,
        "pagination": pagination,
        "partners": partners,
        "today": today_iso,
        "message": message,
        "error": error,
    })

    return templates.TemplateResponse("pages/transport/debt_offsets.html", {"request": request, **ctx})


async def create_debt_offset(
    request: Request,
    offset_date: str,
    debit_partner_id: str,
    credit_partner_id: str,
    amount: str,
    description: str,
    user: dict,
):
    cid = get_current_company_id()
    created_by = user.get("id")

    ok, msg = create_offset(
        company_id=cid,
        offset_date=offset_date,
        debit_partner_id=debit_partner_id,
        credit_partner_id=credit_partner_id,
        amount=amount,
        description=description,
        created_by=created_by,
    )
    param = "message" if ok else "error"
    return RedirectResponse(f"/admin/transport/debt-offsets?{param}={quote(msg)}", status_code=303)


async def edit_debt_offset(
    request: Request,
    offset_id: int,
    page: int,
    offset_date: str,
    debit_partner_id: str,
    credit_partner_id: str,
    amount: str,
    description: str,
    user: dict,
):
    cid = get_current_company_id()
    ok, msg = update_offset(
        company_id=cid,
        offset_id=offset_id,
        offset_date=offset_date,
        debit_partner_id=debit_partner_id,
        credit_partner_id=credit_partner_id,
        amount=amount,
        description=description,
    )
    param = "message" if ok else "error"
    return RedirectResponse(f"/admin/transport/debt-offsets?page={max(page, 1)}&{param}={quote(msg)}", status_code=303)


async def delete_debt_offset(request: Request, offset_id: int, user: dict):
    cid = get_current_company_id()
    ok, msg = delete_offset(company_id=cid, offset_id=offset_id)
    param = "message" if ok else "error"
    return RedirectResponse(f"/admin/transport/debt-offsets?{param}={quote(msg)}", status_code=303)
