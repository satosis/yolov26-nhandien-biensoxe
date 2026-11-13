"""Router for Prepaid Expenses & CCDC Allocation (TK 242)."""

from __future__ import annotations

from datetime import date
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from frontend.context.company_context import get_current_company_id
from frontend.deps import get_menu_context, templates
from frontend.formatting.http_headers import content_disposition
from frontend.modules.transport.asset_vouchers import (
    KIND_LABELS,
    create_voucher,
    delete_voucher,
    get_voucher_kind,
    parse_voucher_form,
    update_voucher,
)
from frontend.modules.transport.cash_bank_service import get_company_info
from frontend.modules.transport.prepaid_expense_service import (
    add_prepaid_expense,
    build_ccdc_tab_data,
    delete_prepaid_expense,
    export_prepaid_expenses_excel,
    get_prepaid_expenses_data,
    list_pending_ccdc_invoices,
    parse_ccdc_form,
    update_prepaid_expense,
)
from frontend.modules.transport.date_utils import resolve_period_dates

logger = logging.getLogger(__name__)


async def prepaid_expenses_page(
    request: Request,
    year: int,
    period: str,
    tab: str,
    date_from: str,
    date_to: str,
    kind: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    today = date.today()
    selected_year, active_period, d_from, d_to = resolve_period_dates(year, period, date_from, date_to)

    pe_data = get_prepaid_expenses_data(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
        kind=kind,
    )

    last_alloc_year = max((it["start_date_raw"].year + (it["start_date_raw"].month - 1 + it["total_months"]) // 12 for it in pe_data["items"]), default=selected_year)
    available_years = sorted({selected_year, selected_year - 1, *range(2020, max(2026, last_alloc_year) + 1)}, reverse=True)

    valid_tabs = (
        "register",
        "increase",
        "allocation",
        "revalue",
        "transfer",
        "decrease",
        "inventory",
        "report",
        "process",
    )
    active_tab = tab.lower().strip() if (tab and tab.lower().strip() in valid_tabs) else "allocation"

    tab_data = build_ccdc_tab_data(pe_data, selected_year)

    book_tabs = [
        ("register", "Sổ theo dõi CCDC", "bx-book"),
        ("increase", "Ghi tăng", "bx-plus-circle"),
        ("allocation", "Phân bổ chi phí", "bx-calculator"),
        ("revalue", "Điều chỉnh", "bx-slider-alt"),
        ("transfer", "Điều chuyển", "bx-transfer"),
        ("decrease", "Ghi giảm", "bx-minus-circle"),
        ("inventory", "Kiểm kê", "bx-check-square"),
        ("report", "Báo cáo phân tích", "bx-bar-chart-alt-2"),
        ("process", "Quy trình", "bx-git-merge"),
    ]

    pending_invoices = list_pending_ccdc_invoices()

    pending_total = sum(int(inv.get("subtotal") or 0) for inv in pending_invoices)
    open_pending = request.query_params.get("pending") == "1"

    ctx.update({
        "current_menu": "prepaid_expenses",
        "transport_active": "prepaid_expenses",
        "pe_data": pe_data,
        "kind": pe_data.get("kind", "all"),
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "today": today,
        "active_tab": active_tab,
        "tab_data": tab_data,
        "book_tabs": book_tabs,
        "voucher_kind_labels": KIND_LABELS["ccdc"],
        "voucher_api_base": "/admin/transport/prepaid-expenses/voucher-api",
        "pending_invoices": pending_invoices,
        "pending_total": pending_total,
        "open_pending": open_pending,
    })
    return templates.TemplateResponse("pages/transport/prepaid_expenses.html", {"request": request, **ctx})


async def prepaid_expenses_export(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    kind: str,
    user: dict,
):
    selected_year, _, d_from, d_to = resolve_period_dates(year, period, date_from, date_to)

    excel_bytes = export_prepaid_expenses_excel(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
        kind=kind,
    )
    comp_info = get_company_info()
    safe_name = "".join(c for c in (comp_info.get("name") or "CongTy") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    table = "Bang_phan_bo_chi_phi_tra_truoc_242" if kind == "prepaid" else "Bang_phan_bo_CCDC_242"
    filename = f"{table}_Nam_{selected_year}_{safe_name}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def api_add_ccdc(request: Request, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    form = dict(await request.form())
    values, err = parse_ccdc_form(form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg, ins_id = add_prepaid_expense(company_id=int(cid), **values)
    return JSONResponse({"ok": ok, "message": msg, "id": ins_id})


async def api_update_ccdc(request: Request, item_id: int, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    form = dict(await request.form())
    values, err = parse_ccdc_form(form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = update_prepaid_expense(item_id=item_id, values=values, company_id=int(cid))
    return JSONResponse({"ok": ok, "message": msg})


async def api_delete_ccdc(request: Request, item_id: int, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    ok, msg = delete_prepaid_expense(item_id=item_id, company_id=int(cid))
    return JSONResponse({"ok": ok, "message": msg})


async def api_add_voucher(request: Request, kind: str, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    form = dict(await request.form())
    values, err = parse_voucher_form("ccdc", kind, form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = create_voucher(int(cid), "ccdc", kind, values)
    return JSONResponse({"ok": ok, "message": msg})


async def api_update_voucher(request: Request, voucher_id: int, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    kind = get_voucher_kind(int(cid), "ccdc", voucher_id)
    if not kind:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chứng từ."})
    form = dict(await request.form())
    values, err = parse_voucher_form("ccdc", kind, form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = update_voucher(int(cid), "ccdc", voucher_id, values)
    return JSONResponse({"ok": ok, "message": msg})


async def api_delete_voucher(request: Request, voucher_id: int, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    ok, msg = delete_voucher(int(cid), "ccdc", voucher_id)
    return JSONResponse({"ok": ok, "message": msg})
