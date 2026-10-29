"""Router for Cash Book (Mau S04a-DNN) and Bank Book (Mau S05a-DNN) under Circular 133/2016/TT-BTC."""

from __future__ import annotations

from datetime import date, datetime
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from frontend.deps import get_menu_context, require_menu, templates, get_active_company_id
from frontend.modules.invoices.companies import list_companies
from frontend.modules.transport.cash_bank_service import (
    delete_cash_or_bank_entry,
    export_bank_book_excel,
    export_cash_book_excel,
    generate_bank_import_template,
    generate_cash_import_template,
    get_bank_book_data,
    get_cash_book_data,
    get_cash_or_bank_entry,
    get_company_info,
    get_opening_balance_setting,
    import_bank_transactions_from_excel,
    import_cash_transactions_from_excel,
    record_cash_or_bank_entry,
    set_opening_balance_setting,
    update_cash_or_bank_entry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport")


def _resolve_dates(year: int | None, period: str | None, date_from_str: str | None, date_to_str: str | None) -> tuple[int, str, date, date]:
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year

    d_from: date | None = None
    d_to: date | None = None

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass

    p = (period or "").lower().strip()

    if p and p != "custom":
        if p == "today":
            d_from = date(selected_year, 1, 1)
            d_to = min(today, date(selected_year, 12, 31))
            return selected_year, "today", d_from, d_to
        elif p == "q1":
            d_from = date(selected_year, 1, 1)
            d_to = date(selected_year, 3, 31)
            return selected_year, "q1", d_from, d_to
        elif p == "q2":
            d_from = date(selected_year, 4, 1)
            d_to = date(selected_year, 6, 30)
            return selected_year, "q2", d_from, d_to
        elif p == "q3":
            d_from = date(selected_year, 7, 1)
            d_to = date(selected_year, 9, 30)
            return selected_year, "q3", d_from, d_to
        elif p == "q4":
            d_from = date(selected_year, 10, 1)
            d_to = date(selected_year, 12, 31)
            return selected_year, "q4", d_from, d_to
        elif p.startswith("m") and p[1:].isdigit():
            m = int(p[1:])
            if 1 <= m <= 12:
                last_day = 31 if m in (1, 3, 5, 7, 8, 10, 12) else (30 if m in (4, 6, 9, 11) else 28)
                d_from = date(selected_year, m, 1)
                d_to = date(selected_year, m, last_day)
                return selected_year, p, d_from, d_to
        elif p in ("all", "year"):
            d_from = date(selected_year, 1, 1)
            d_to = date(selected_year, 12, 31)
            return selected_year, "all", d_from, d_to

    if d_from and d_to:
        return selected_year, "custom", d_from, d_to

    # default
    d_from = date(selected_year, 1, 1)
    d_to = date(selected_year, 12, 31)
    return selected_year, "all", d_from, d_to


# =====================================================================
# SỔ QUỸ TIỀN MẶT (MẪU S04a-DNN - THÔNG TƯ 133)
# =====================================================================
@router.get("/cash-book", include_in_schema=False)
@router.get("/cash-book/")
async def cash_book_page(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    cid = get_active_company_id(request, user)
    selected_year, active_period, d_from, d_to = _resolve_dates(year, period, date_from, date_to)

    cash_data = get_cash_book_data(
        company_id=cid,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "cash_book",
        "transport_active": "cash_book",
        "cash_data": cash_data,
        "companies": list_companies() or [],
        "selected_company_id": cid,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/cash_book.html", {"request": request, **ctx})


@router.get("/cash-book/export")
async def cash_book_export(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = get_active_company_id(request, user)
    selected_year, _, d_from, d_to = _resolve_dates(year, period, date_from, None)
    excel_bytes = export_cash_book_excel(
        company_id=cid,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )
    comp_info = get_company_info(cid)
    ascii_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", comp_info.get("name") or "company").strip("_") or f"company_{cid}"
    filename = f"S04a_DNN_So_quy_tien_mat_{ascii_name}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# =====================================================================
# SỔ TIỀN GỬI NGÂN HÀNG (MẪU S05a-DNN - THÔNG TƯ 133)
# =====================================================================
@router.get("/bank-book", include_in_schema=False)
@router.get("/bank-book/")
async def bank_book_page(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    account_no: str = Query(""),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    cid = get_active_company_id(request, user)
    selected_year, active_period, d_from, d_to = _resolve_dates(year, period, date_from, date_to)

    bank_data = get_bank_book_data(
        company_id=cid,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
        bank_account_no=account_no,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "bank_book",
        "transport_active": "bank_book",
        "bank_data": bank_data,
        "companies": list_companies() or [],
        "selected_company_id": cid,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "selected_account_no": account_no,
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/bank_book.html", {"request": request, **ctx})


@router.get("/bank-book/export")
async def bank_book_export(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = get_active_company_id(request, user)
    selected_year, _, d_from, d_to = _resolve_dates(year, period, date_from, date_to)
    excel_bytes = export_bank_book_excel(
        company_id=cid,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )
    comp_info = get_company_info(cid)
    ascii_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", comp_info.get("name") or "company").strip("_") or f"company_{cid}"
    filename = f"S05a_DNN_So_tien_gui_NH_{ascii_name}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# =====================================================================
# API THÊM / SỬA / XÓA CHỨNG TỪ THU/CHI TIỀN MẶT & TIỀN GỬI NGÂN HÀNG
# =====================================================================
@router.post("/api/cash-bank/entry")
async def api_add_cash_bank_entry(
    request: Request,
    book_type: str = Form(...),          # 'cash' hoặc 'bank'
    voucher_type: str = Form(...),       # 'receipt' (Thu) hoặc 'payment' (Chi)
    amount: str = Form(...),
    voucher_date: str = Form(""),
    voucher_no: str = Form(""),
    description: str = Form(""),
    offset_account: str = Form("131"),
    partner_name: str = Form(""),
    partner_code: str = Form(""),
    bank_account_no: str = Form(""),
    bank_name: str = Form(""),
    note: str = Form(""),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    cleaned = amount.replace(".", "").replace(",", "").replace(" ", "").strip()
    ok, msg, ins_id = record_cash_or_bank_entry(
        company_id=cid,
        book_type=book_type,
        voucher_type=voucher_type,
        amount=cleaned,
        voucher_date=voucher_date or None,
        voucher_no=voucher_no,
        description=description,
        offset_account=offset_account,
        partner_name=partner_name,
        partner_code=partner_code,
        bank_account_no=bank_account_no,
        bank_name=bank_name,
        note=note,
    )
    return JSONResponse({"ok": ok, "message": msg, "id": ins_id})


@router.get("/api/cash-bank/entry/{entry_id}")
async def api_get_cash_bank_entry(
    request: Request,
    entry_id: int,
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    entry = get_cash_or_bank_entry(entry_id=entry_id, company_id=cid)
    if not entry:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chứng từ."}, status_code=404)
    return JSONResponse({"ok": True, "entry": entry})


@router.post("/api/cash-bank/entry/{entry_id}/update")
async def api_update_cash_bank_entry(
    request: Request,
    entry_id: int,
    voucher_type: str = Form(None),
    amount: str = Form(None),
    voucher_date: str = Form(None),
    voucher_no: str = Form(None),
    description: str = Form(None),
    offset_account: str = Form(None),
    partner_name: str = Form(None),
    partner_code: str = Form(None),
    bank_account_no: str = Form(None),
    bank_name: str = Form(None),
    note: str = Form(None),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    cleaned_amount = None
    if amount is not None:
        cleaned_amount = amount.replace(".", "").replace(",", "").replace(" ", "").strip()
    ok, msg = update_cash_or_bank_entry(
        entry_id=entry_id,
        company_id=cid,
        voucher_type=voucher_type,
        amount=cleaned_amount,
        voucher_date=voucher_date,
        voucher_no=voucher_no,
        description=description,
        offset_account=offset_account,
        partner_name=partner_name,
        partner_code=partner_code,
        bank_account_no=bank_account_no,
        bank_name=bank_name,
        note=note,
    )
    return JSONResponse({"ok": ok, "message": msg})


@router.post("/api/cash-bank/entry/{entry_id}/delete")
async def api_delete_cash_bank_entry(
    request: Request,
    entry_id: int,
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    ok, msg = delete_cash_or_bank_entry(entry_id=entry_id, company_id=cid)
    return JSONResponse({"ok": ok, "message": msg})


# =====================================================================
# TẢI FILE MẪU & IMPORT EXCEL GIAO DỊCH NGÂN HÀNG & TIỀN MẶT
# =====================================================================
@router.get("/bank-book/template")
async def bank_book_download_template(
    user: dict = Depends(require_menu("vehicles")),
):
    excel_bytes = generate_bank_import_template()
    filename = "Mau_Nhap_Giao_Dich_Ngan_Hang_VietinBank.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/bank-book/import-excel")
async def bank_book_import_excel(
    request: Request,
    file: UploadFile = File(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    content = await file.read()
    ok, msg, count = import_bank_transactions_from_excel(file_content=content, company_id=cid)
    return JSONResponse({"ok": ok, "message": msg, "count": count})


@router.get("/cash-book/template")
async def cash_book_download_template(
    user: dict = Depends(require_menu("vehicles")),
):
    excel_bytes = generate_cash_import_template()
    filename = "Mau_Nhap_So_Quy_Tien_Mat.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/cash-book/import-excel")
async def cash_book_import_excel(
    request: Request,
    file: UploadFile = File(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    content = await file.read()
    ok, msg, count = import_cash_transactions_from_excel(file_content=content, company_id=cid)
    return JSONResponse({"ok": ok, "message": msg, "count": count})


# =====================================================================
# API THIẾT LẬP SỐ DƯ ĐẦU KỲ (TK 111 & TK 112)
# =====================================================================
@router.post("/api/cash-bank/opening-balance")
async def api_set_opening_balance(
    request: Request,
    book_type: str = Form(...),      # 'bank' hoặc 'cash'
    amount: str = Form(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    ok, msg = set_opening_balance_setting(book_type=book_type, amount=amount, company_id=cid)
    return JSONResponse({"ok": ok, "message": msg})


@router.get("/api/cash-bank/opening-balance")
async def api_get_opening_balance(
    request: Request,
    book_type: str = Query("bank"),
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    val = get_opening_balance_setting(book_type=book_type, company_id=cid)
    return JSONResponse({"ok": True, "book_type": book_type, "opening_balance": int(val)})


