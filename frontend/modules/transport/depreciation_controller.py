from __future__ import annotations

from datetime import date
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from frontend.deps import get_menu_context, templates
from frontend.modules.transport.cash_bank_service import get_company_info
from frontend.formatting.http_headers import content_disposition
from frontend.context.company_context import get_current_company_id
from frontend.modules.transport.asset_vouchers import (
    KIND_LABELS,
    create_voucher,
    delete_voucher,
    get_voucher_kind,
    parse_voucher_form,
    update_voucher,
)
from frontend.modules.transport.depreciation_service import (
    ASSET_TYPE_OPTIONS,
    add_fixed_asset,
    build_tscd_tab_data,
    delete_fixed_asset,
    export_depreciation_excel,
    get_depreciation_data,
    list_pending_tscd_invoices,
    parse_fixed_asset_form,
    restore_vehicle,
    update_fixed_asset,
)

logger = logging.getLogger(__name__)


def _depreciation_period(year, period, mode):
    """Year, month the depreciation runs to, period name and whether it is the full year."""
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year
    p = (period or mode or "ytd").lower().strip()
    quarters = {"q1": 3, "q2": 6, "q3": 9, "q4": 12}
    if p in ("full", "all"):
        return selected_year, 12, "full", True
    if p in quarters:
        return selected_year, quarters[p], p, False
    if p.startswith("m") and p[1:].isdigit():
        month = max(1, min(12, int(p[1:])))
        return selected_year, month, f"m{month}", False
    return selected_year, today.month if today.year == selected_year else 12, "ytd", False


async def depreciation_page(request: Request, year: int, mode: str, period: str, tab: str, user: dict):
    ctx = get_menu_context(request, user)
    today = date.today()
    selected_year, as_of_m, active_period, is_full_year = _depreciation_period(year, period, mode)

    depr_data = get_depreciation_data(
        year=selected_year,
        as_of_month=as_of_m,
        full_year=is_full_year,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    valid_tabs = (
        "register",
        "increase",
        "depreciation",
        "revalue",
        "transfer",
        "decrease",
        "inventory",
        "report",
        "process",
    )
    active_tab = tab.lower().strip() if (tab and tab.lower().strip() in valid_tabs) else "depreciation"

    tab_data = build_tscd_tab_data(depr_data, selected_year)

    book_tabs = [
        ("register", "Sổ tài sản", "bx-book"),
        ("increase", "Ghi tăng", "bx-plus-circle"),
        ("depreciation", "Tính khấu hao", "bx-calculator"),
        ("revalue", "Đánh giá lại", "bx-line-chart"),
        ("transfer", "Điều chuyển", "bx-transfer"),
        ("decrease", "Ghi giảm", "bx-minus-circle"),
        ("inventory", "Kiểm kê", "bx-check-square"),
        ("report", "Báo cáo phân tích", "bx-bar-chart-alt-2"),
        ("process", "Quy trình", "bx-git-merge"),
    ]

    pending_invoices = list_pending_tscd_invoices()

    pending_total = sum(int(inv.get("subtotal") or 0) for inv in pending_invoices)
    open_pending = request.query_params.get("pending") == "1"

    ctx.update({
        "current_menu": "depreciation",
        "transport_active": "depreciation",
        "depr_data": depr_data,
        "selected_year": selected_year,
        "selected_mode": "full" if is_full_year else "ytd",
        "active_period": active_period,
        "is_full_year": is_full_year,
        "available_years": available_years,
        "today": today,
        "asset_type_options": ASSET_TYPE_OPTIONS,
        "active_tab": active_tab,
        "tab_data": tab_data,
        "book_tabs": book_tabs,
        "voucher_kind_labels": KIND_LABELS["tscd"],
        "voucher_api_base": "/admin/transport/depreciation/voucher-api",
        "pending_invoices": pending_invoices,
        "pending_total": pending_total,
        "open_pending": open_pending,
    })
    return templates.TemplateResponse("pages/transport/depreciation.html", {"request": request, **ctx})


async def export_depreciation(request: Request, year: int, mode: str, period: str, user: dict):
    selected_year, as_of_m, _, is_full_year = _depreciation_period(year, period, mode)

    excel_bytes = export_depreciation_excel(
        year=selected_year,
        as_of_month=as_of_m,
        full_year=is_full_year,
    )

    comp_info = get_company_info()
    safe_name = "".join(c for c in (comp_info.get("name") or "CongTy") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    filename = f"BANG_TINH_KHAU_HAO_TAI_SAN_CO_DINH_NAM_{selected_year}_{safe_name}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def api_add_asset(request: Request, user: dict):
    form = dict(await request.form())
    values, err = parse_fixed_asset_form(form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = add_fixed_asset(values)
    return JSONResponse({"ok": ok, "message": msg})


async def api_update_asset(request: Request, source: str, source_id: int, user: dict):
    form = dict(await request.form())
    values, err = parse_fixed_asset_form(form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = update_fixed_asset(source=source, source_id=source_id, values=values)
    return JSONResponse({"ok": ok, "message": msg})


async def api_delete_asset(request: Request, source: str, source_id: int, user: dict):
    ok, msg = delete_fixed_asset(source=source, source_id=source_id)
    return JSONResponse({"ok": ok, "message": msg})


async def api_restore_vehicle(request: Request, vehicle_id: int, user: dict):
    ok, msg = restore_vehicle(vehicle_id=vehicle_id)
    return JSONResponse({"ok": ok, "message": msg})


async def api_add_voucher(request: Request, kind: str, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    form = dict(await request.form())
    values, err = parse_voucher_form("tscd", kind, form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = create_voucher(int(cid), "tscd", kind, values)
    return JSONResponse({"ok": ok, "message": msg})


async def api_update_voucher(request: Request, voucher_id: int, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    kind = get_voucher_kind(int(cid), "tscd", voucher_id)
    if not kind:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chứng từ."})
    form = dict(await request.form())
    values, err = parse_voucher_form("tscd", kind, form)
    if err:
        return JSONResponse({"ok": False, "message": err})
    ok, msg = update_voucher(int(cid), "tscd", voucher_id, values)
    return JSONResponse({"ok": ok, "message": msg})


async def api_delete_voucher(request: Request, voucher_id: int, user: dict):
    cid = get_current_company_id()
    if not cid or int(cid) <= 0:
        return JSONResponse({"ok": False, "message": "Chưa chọn công ty làm việc."})
    ok, msg = delete_voucher(int(cid), "tscd", voucher_id)
    return JSONResponse({"ok": ok, "message": msg})

