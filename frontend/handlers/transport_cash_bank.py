"""Cầu nối HTTP của frontend.modules.transport.cash_bank_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, UploadFile, File
from frontend.deps import require_menu
import frontend.modules.transport.cash_bank_controller as controller


async def cash_book_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.cash_book_page(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def cash_book_export(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.cash_book_export(request=request, year=year, period=period, date_from=date_from, user=user)


async def cash_voucher_print(
    request: Request,
    entry_id: int,
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.cash_voucher_print(request=request, entry_id=entry_id, company_id=company_id, user=user)


async def bank_book_page(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    account_no: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.bank_book_page(request=request, year=year, period=period, date_from=date_from, date_to=date_to, account_no=account_no, user=user)


async def bank_book_export(
    request: Request,
    year: int = Query(None),
    period: str = Query('all'),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.bank_book_export(request=request, year=year, period=period, date_from=date_from, date_to=date_to, user=user)


async def api_add_cash_bank_entry(
    request: Request,
    book_type: str = Form(...),
    voucher_type: str = Form(...),
    amount: str = Form(...),
    voucher_date: str = Form(''),
    voucher_no: str = Form(''),
    description: str = Form(''),
    offset_account: str = Form('131'),
    partner_name: str = Form(''),
    partner_code: str = Form(''),
    bank_account_no: str = Form(''),
    bank_name: str = Form(''),
    note: str = Form(''),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_add_cash_bank_entry(request=request, book_type=book_type, voucher_type=voucher_type, amount=amount, voucher_date=voucher_date, voucher_no=voucher_no, description=description, offset_account=offset_account, partner_name=partner_name, partner_code=partner_code, bank_account_no=bank_account_no, bank_name=bank_name, note=note, company_id=company_id, user=user)


async def api_special_payment(
    request: Request,
    kind: str = Form(...),
    book_type: str = Form('bank'),
    voucher_date: str = Form(''),
    voucher_no: str = Form(''),
    partner_name: str = Form(''),
    note: str = Form(''),
    period: str = Form(''),
    notice_total: str = Form(''),
    items_json: str = Form(''),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_special_payment(request=request, kind=kind, book_type=book_type, voucher_date=voucher_date, voucher_no=voucher_no, partner_name=partner_name, note=note, period=period, notice_total=notice_total, items_json=items_json, company_id=company_id, user=user)


async def api_bank_interest(
    request: Request,
    amount: str = Form(''),
    voucher_date: str = Form(''),
    voucher_no: str = Form(''),
    partner_name: str = Form(''),
    note: str = Form(''),
    period: str = Form(''),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_bank_interest(request=request, amount=amount, voucher_date=voucher_date, voucher_no=voucher_no, partner_name=partner_name, note=note, period=period, company_id=company_id, user=user)


async def api_cash_transfer(
    request: Request,
    direction: str = Form(''),
    voucher_date: str = Form(''),
    voucher_no: str = Form(''),
    amount: str = Form(''),
    description: str = Form(''),
    note: str = Form(''),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_cash_transfer(request=request, direction=direction, voucher_date=voucher_date, voucher_no=voucher_no, amount=amount, description=description, note=note, company_id=company_id, user=user)


async def api_renumber_vouchers(
    request: Request,
    book_type: str = Form(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_renumber_vouchers(request=request, book_type=book_type, company_id=company_id, user=user)


async def api_payroll_preview(
    request: Request,
    month: str = Query(''),
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_payroll_preview(request=request, month=month, company_id=company_id, user=user)


async def api_open_receivable_invoices(
    request: Request,
    partner_code: str = Query(''),
    partner_name: str = Query(''),
    as_of: str = Query(''),
    exclude_entry_id: int | None = Query(None),
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_open_receivable_invoices(request=request, partner_code=partner_code, partner_name=partner_name, as_of=as_of, exclude_entry_id=exclude_entry_id, company_id=company_id, user=user)


async def api_open_payable_invoices(
    request: Request,
    partner_code: str = Query(''),
    partner_name: str = Query(''),
    as_of: str = Query(''),
    exclude_entry_id: int | None = Query(None),
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_open_payable_invoices(request=request, partner_code=partner_code, partner_name=partner_name, as_of=as_of, exclude_entry_id=exclude_entry_id, company_id=company_id, user=user)


async def api_get_cash_bank_entry(
    request: Request,
    entry_id: int,
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_get_cash_bank_entry(request=request, entry_id=entry_id, company_id=company_id, user=user)


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
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_update_cash_bank_entry(request=request, entry_id=entry_id, voucher_type=voucher_type, amount=amount, voucher_date=voucher_date, voucher_no=voucher_no, description=description, offset_account=offset_account, partner_name=partner_name, partner_code=partner_code, bank_account_no=bank_account_no, bank_name=bank_name, note=note, company_id=company_id, user=user)


async def api_delete_cash_bank_entry(
    request: Request,
    entry_id: int,
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_delete_cash_bank_entry(request=request, entry_id=entry_id, company_id=company_id, user=user)


async def bank_book_download_template(user: dict = Depends(require_menu('vehicles'))):
    return await controller.bank_book_download_template(user=user)


async def bank_book_import_excel(
    request: Request,
    file: UploadFile = File(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.bank_book_import_excel(request=request, file=file, company_id=company_id, user=user)


async def cash_book_download_template(user: dict = Depends(require_menu('vehicles'))):
    return await controller.cash_book_download_template(user=user)


async def cash_book_import_excel(
    request: Request,
    file: UploadFile = File(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.cash_book_import_excel(request=request, file=file, company_id=company_id, user=user)


async def api_set_opening_balance(
    request: Request,
    book_type: str = Form(...),
    amount: str = Form(...),
    company_id: int | None = Form(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_set_opening_balance(request=request, book_type=book_type, amount=amount, company_id=company_id, user=user)


async def api_get_opening_balance(
    request: Request,
    book_type: str = Query('bank'),
    company_id: int | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.api_get_opening_balance(request=request, book_type=book_type, company_id=company_id, user=user)
