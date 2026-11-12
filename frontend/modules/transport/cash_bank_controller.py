"""Router for Cash Book (Mau S04a-DNN) and Bank Book (Mau S05a-DNN) under Circular 133/2016/TT-BTC."""

from __future__ import annotations

from datetime import date
import json
import logging
import re

from fastapi import Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from frontend.deps import get_menu_context, require_menu, templates, get_active_company_id
from frontend.modules.invoices.companies import get_company, list_companies
from frontend.formatting.http_headers import content_disposition
from frontend.formatting.money import parse_vnd
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
    get_open_receivable_invoices,
    get_open_payable_invoices,
    partner_payment_descriptions,
    get_opening_balance_setting,
    import_bank_transactions_from_excel,
    import_cash_transactions_from_excel,
    parse_flexible_date,
    partner_address_by_tax_code,
    record_cash_or_bank_entry,
    record_cash_transfer,
    renumber_vouchers,
    set_opening_balance_setting,
    update_cash_or_bank_entry,
)
from frontend.modules.transport.hdnt_bbbg import number_to_vietnamese_words
from frontend.modules.transport.special_payments import (
    get_company_director_name,
    payroll_preview,
    record_bank_interest,
    record_special_payment,
)
from frontend.modules.transport.date_utils import resolve_period_dates

logger = logging.getLogger(__name__)


# =====================================================================
# SỔ QUỸ TIỀN MẶT (MẪU S04a-DNN - THÔNG TƯ 133)
# =====================================================================
async def cash_book_page(request: Request, year: int, period: str, date_from: str, date_to: str, user: dict):
    ctx = get_menu_context(request, user)
    cid = get_active_company_id(request, user)
    selected_year, active_period, d_from, d_to = resolve_period_dates(year, period, date_from, date_to)

    cash_data = get_cash_book_data(
        company_id=cid,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    companies = list_companies() or []
    ctx.update({
        "current_menu": "cash_book",
        "transport_active": "cash_book",
        "cash_data": cash_data,
        "companies": companies,
        "partner_pay_descs": partner_payment_descriptions(cid, companies),
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


async def cash_book_export(request: Request, year: int, period: str, date_from: str, user: dict):
    cid = get_active_company_id(request, user)
    selected_year, _, d_from, d_to = resolve_period_dates(year, period, date_from, None)
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
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def cash_voucher_print(request: Request, entry_id: int, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    entry = get_cash_or_bank_entry(entry_id=entry_id, company_id=cid)
    if not entry or entry.get("book_type") != "cash":
        return HTMLResponse("Không tìm thấy phiếu.", status_code=404)

    company = get_company(cid) or {}
    partner_code = (entry.get("partner_code") or "").strip()
    partner_address = partner_address_by_tax_code(partner_code) if partner_code else ""

    is_receipt = entry.get("voucher_type") == "receipt"
    raw_date = entry.get("voucher_date") or entry.get("entry_date") or ""
    if raw_date:
        d_obj = parse_flexible_date(raw_date)
        day = f"{d_obj.day:02d}"
        month = f"{d_obj.month:02d}"
        year = str(d_obj.year)
    else:
        day = ""
        month = ""
        year = ""

    offset_account = entry.get("offset_account") or ""
    if is_receipt:
        debit_acc = "1111"
        credit_acc = offset_account
    else:
        debit_acc = offset_account
        credit_acc = "1111"

    amt = entry.get("amount") or 0
    amt_int = int(round(float(amt)))
    amount_str = f"{amt_int:,}".replace(",", ".")
    amount_words = number_to_vietnamese_words(amt_int)

    return templates.TemplateResponse(
        "pages/transport/cash_voucher_print.html",
        {
            "request": request,
            "company": company,
            "is_receipt": is_receipt,
            "voucher_no": entry.get("voucher_no") or "",
            "day": day,
            "month": month,
            "year": year,
            "debit_acc": debit_acc,
            "credit_acc": credit_acc,
            "partner_name": entry.get("partner_name") or "",
            "partner_address": partner_address,
            "description": entry.get("description") or "",
            "amount": amount_str,
            "amount_words": amount_words,
        },
    )


# =====================================================================
# SỔ TIỀN GỬI NGÂN HÀNG (MẪU S05a-DNN - THÔNG TƯ 133)
# =====================================================================
async def bank_book_page(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    account_no: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    cid = get_active_company_id(request, user)
    selected_year, active_period, d_from, d_to = resolve_period_dates(year, period, date_from, date_to)

    bank_data = get_bank_book_data(
        company_id=cid,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
        bank_account_no=account_no,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    companies = list_companies() or []
    ctx.update({
        "current_menu": "bank_book",
        "transport_active": "bank_book",
        "bank_data": bank_data,
        "companies": companies,
        "partner_pay_descs": partner_payment_descriptions(cid, companies),
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
        "rent_landlord_default": get_company_director_name(cid),
    })
    return templates.TemplateResponse("pages/transport/bank_book.html", {"request": request, **ctx})


async def bank_book_export(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    cid = get_active_company_id(request, user)
    selected_year, _, d_from, d_to = resolve_period_dates(year, period, date_from, date_to)
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
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


# =====================================================================
# API THÊM / SỬA / XÓA CHỨNG TỪ THU/CHI TIỀN MẶT & TIỀN GỬI NGÂN HÀNG
# =====================================================================
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

    ok, msg, ins_id = record_cash_or_bank_entry(
        company_id=cid,
        book_type=book_type,
        voucher_type=voucher_type,
        amount=amount,
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
    status_code = 200 if ok else 400
    return JSONResponse({"ok": ok, "message": msg, "id": ins_id}, status_code=status_code)


async def api_special_payment(
    request: Request,
    kind: str,
    book_type: str,
    voucher_date: str,
    voucher_no: str,
    partner_name: str,
    note: str,
    period: str,
    notice_total: str,
    items_json: str,
    company_id: int | None,
    user: dict,
):
    try:
        items = json.loads(items_json) if items_json else None
    except Exception:
        return JSONResponse({"ok": False, "message": "Dữ liệu không hợp lệ."}, status_code=400)

    clean_kind = str(kind or "").strip().lower()
    if clean_kind in ("tax", "insurance", "rent") and not isinstance(items, dict):
        return JSONResponse({"ok": False, "message": "Dữ liệu không hợp lệ."}, status_code=400)
    if clean_kind == "salary" and not isinstance(items, list):
        return JSONResponse({"ok": False, "message": "Dữ liệu không hợp lệ."}, status_code=400)

    parsed_notice: int | None = None
    if notice_total and str(notice_total).strip():
        try:
            parsed_notice = int(round(parse_vnd(notice_total)))
        except (ValueError, TypeError):
            return JSONResponse({"ok": False, "message": "Số tiền thông báo không hợp lệ."}, status_code=400)

    cid = company_id or get_active_company_id(request, user)
    result = record_special_payment(
        company_id=cid,
        kind=clean_kind,
        book_type=book_type,
        voucher_date=voucher_date,
        voucher_no=voucher_no,
        items=items,
        partner_name=partner_name,
        note=note,
        period=period,
        notice_total=parsed_notice,
    )
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code)


async def api_bank_interest(
    request: Request,
    amount: str,
    voucher_date: str,
    voucher_no: str,
    partner_name: str,
    note: str,
    period: str,
    company_id: int | None,
    user: dict,
):
    cid = company_id or get_active_company_id(request, user)
    result = record_bank_interest(
        company_id=cid,
        voucher_date=voucher_date,
        voucher_no=voucher_no,
        amount=amount,
        partner_name=partner_name,
        note=note,
        period=period,
    )
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code)


async def api_cash_transfer(
    request: Request,
    direction: str,
    voucher_date: str,
    voucher_no: str,
    amount: str,
    description: str,
    note: str,
    company_id: int | None,
    user: dict,
):
    cid = company_id or get_active_company_id(request, user)
    result = record_cash_transfer(
        company_id=cid,
        direction=direction,
        voucher_date=voucher_date,
        amount=amount,
        voucher_no=voucher_no,
        description=description,
        note=note,
    )
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code)


async def api_renumber_vouchers(request: Request, book_type: str, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    result = renumber_vouchers(book_type=book_type, company_id=cid)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


async def api_payroll_preview(request: Request, month: str, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    m = month.strip() if month and month.strip() else date.today().strftime("%Y-%m")
    result = payroll_preview(cid, m)
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code)


async def api_open_receivable_invoices(
    request: Request,
    partner_code: str,
    partner_name: str,
    as_of: str,
    exclude_entry_id: int | None,
    company_id: int | None,
    user: dict,
):
    cid = company_id or get_active_company_id(request, user)
    return JSONResponse({"ok": True, **get_open_receivable_invoices(cid, partner_code, partner_name, as_of, exclude_entry_id)})


async def api_open_payable_invoices(
    request: Request,
    partner_code: str,
    partner_name: str,
    as_of: str,
    exclude_entry_id: int | None,
    company_id: int | None,
    user: dict,
):
    cid = company_id or get_active_company_id(request, user)
    return JSONResponse({"ok": True, **get_open_payable_invoices(cid, partner_code, partner_name, as_of, exclude_entry_id)})


async def api_get_cash_bank_entry(request: Request, entry_id: int, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    entry = get_cash_or_bank_entry(entry_id=entry_id, company_id=cid)
    if not entry:
        return JSONResponse({"ok": False, "message": "Không tìm thấy chứng từ."}, status_code=404)
    return JSONResponse({"ok": True, "entry": entry})


async def api_update_cash_bank_entry(
    request: Request,
    entry_id: int,
    voucher_type: str,
    amount: str,
    voucher_date: str,
    voucher_no: str,
    description: str,
    offset_account: str,
    partner_name: str,
    partner_code: str,
    bank_account_no: str,
    bank_name: str,
    note: str,
    company_id: int | None,
    user: dict,
):
    cid = company_id or get_active_company_id(request, user)


    ok, msg = update_cash_or_bank_entry(
        entry_id=entry_id,
        company_id=cid,
        voucher_type=voucher_type,
        amount=amount,
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
    status_code = 200 if ok else 400
    return JSONResponse({"ok": ok, "message": msg}, status_code=status_code)


async def api_delete_cash_bank_entry(request: Request, entry_id: int, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    ok, msg = delete_cash_or_bank_entry(entry_id=entry_id, company_id=cid)
    status_code = 200 if ok else 400
    return JSONResponse({"ok": ok, "message": msg}, status_code=status_code)


# =====================================================================
# TẢI FILE MẪU & IMPORT EXCEL GIAO DỊCH NGÂN HÀNG & TIỀN MẶT
# =====================================================================
async def bank_book_download_template(user: dict):
    excel_bytes = generate_bank_import_template()
    filename = "Mau_Nhap_Giao_Dich_Ngan_Hang_VietinBank.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def bank_book_import_excel(request: Request, file: UploadFile, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    content = await file.read()
    ok, msg, count = import_bank_transactions_from_excel(file_content=content, company_id=cid)
    return JSONResponse({"ok": ok, "message": msg, "count": count})


async def cash_book_download_template(user: dict):
    excel_bytes = generate_cash_import_template()
    filename = "Mau_Nhap_So_Quy_Tien_Mat.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def cash_book_import_excel(request: Request, file: UploadFile, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    content = await file.read()
    ok, msg, count = import_cash_transactions_from_excel(file_content=content, company_id=cid)
    return JSONResponse({"ok": ok, "message": msg, "count": count})


# =====================================================================
# API THIẾT LẬP SỐ DƯ ĐẦU KỲ (TK 111 & TK 112)
# =====================================================================
async def api_set_opening_balance(
    request: Request,
    book_type: str = Form(...),      # 'bank' hoặc 'cash'
    amount: str = Form(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu("vehicles")),
):
    cid = company_id or get_active_company_id(request, user)
    ok, msg = set_opening_balance_setting(book_type=book_type, amount=amount, company_id=cid)
    status_code = 200 if ok else 400
    return JSONResponse({"ok": ok, "message": msg}, status_code=status_code)


async def api_get_opening_balance(request: Request, book_type: str, company_id: int | None, user: dict):
    cid = company_id or get_active_company_id(request, user)
    val = get_opening_balance_setting(book_type=book_type, company_id=cid)
    return JSONResponse({"ok": True, "book_type": book_type, "opening_balance": int(val)})


