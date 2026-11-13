"""Router for Chứng từ nghiệp vụ khác."""

from __future__ import annotations

from datetime import date
import logging
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from frontend.context.company_context import get_current_company_id
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
from frontend.modules.transport.other_vouchers_service import (
    count_vouchers,
    create_voucher,
    delete_voucher,
    list_customer_partners,
    list_vendor_partners,
    list_vouchers,
    update_voucher,
)

logger = logging.getLogger(__name__)


async def other_vouchers_page(request: Request, page: int, message: str, error: str, user: dict):
    ctx = get_menu_context(request, user)
    cid = get_current_company_id()

    total = count_vouchers(cid) if cid else 0
    pagination = paginate_counts(
        total,
        page=page,
        per_page=20,
        base_url="/admin/transport/other-vouchers",
    )
    vouchers = list_vouchers(
        cid,
        limit=pagination["per_page"],
        offset=(pagination["page"] - 1) * pagination["per_page"],
    ) if cid else []

    vendors = list_vendor_partners()
    customers = list_customer_partners()
    today_iso = date.today().isoformat()

    ctx.update({
        "current_menu": "vehicles",
        "transport_section": "other_vouchers",
        "transport_active": "other_vouchers",
        "vouchers": vouchers,
        "pagination": pagination,
        "vendors": vendors,
        "customers": customers,
        "today": today_iso,
        "message": message,
        "error": error,
    })

    return templates.TemplateResponse("pages/transport/other_vouchers.html", {"request": request, **ctx})


async def create_other_voucher_action(
    request: Request,
    voucher_date: str,
    debit_account: str,
    credit_account: str,
    debit_partner_id: str,
    credit_partner_id: str,
    amount: str,
    description: str,
    user: dict,
):
    cid = get_current_company_id()
    created_by = user.get("id")

    ok, msg = create_voucher(
        company_id=cid,
        voucher_date=voucher_date,
        debit_account=debit_account,
        credit_account=credit_account,
        debit_partner_id=debit_partner_id,
        credit_partner_id=credit_partner_id,
        amount=amount,
        description=description,
        created_by=created_by,
    )
    param = "message" if ok else "error"
    return RedirectResponse(f"/admin/transport/other-vouchers?{param}={quote(msg)}", status_code=303)


async def edit_other_voucher_action(
    request: Request,
    voucher_id: int,
    page: int,
    voucher_date: str,
    debit_account: str,
    credit_account: str,
    debit_partner_id: str,
    credit_partner_id: str,
    amount: str,
    description: str,
    user: dict,
):
    cid = get_current_company_id()
    ok, msg = update_voucher(
        company_id=cid,
        voucher_id=voucher_id,
        voucher_date=voucher_date,
        debit_account=debit_account,
        credit_account=credit_account,
        debit_partner_id=debit_partner_id,
        credit_partner_id=credit_partner_id,
        amount=amount,
        description=description,
    )
    param = "message" if ok else "error"
    return RedirectResponse(f"/admin/transport/other-vouchers?page={max(page, 1)}&{param}={quote(msg)}", status_code=303)


async def delete_other_voucher_action(request: Request, voucher_id: int, user: dict):
    cid = get_current_company_id()
    ok, msg = delete_voucher(company_id=cid, voucher_id=voucher_id)
    param = "message" if ok else "error"
    return RedirectResponse(f"/admin/transport/other-vouchers?{param}={quote(msg)}", status_code=303)
