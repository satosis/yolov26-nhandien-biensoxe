"""Cầu nối HTTP của frontend.modules.invoices.companies_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, UploadFile, File
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.invoices.companies_controller as controller


async def companies_management_page(
    request: Request,
    back: str = Query('/admin/companies'),
    page: int = Query(1, ge=1),
    q: str = Query('', max_length=200),
    debt: bool = Query(False),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('companies')),
):
    return await controller.companies_management_page(request=request, back=back, page=page, q=q, debt=debt, message=message, error=error, user=user)


async def add_company_page(
    request: Request,
    tax_code: str = Query(''),
    partner_type: str = Query('organization'),
    back: str = Query(''),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.add_company_page(request=request, tax_code=tax_code, partner_type=partner_type, back=back, message=message, error=error, user=user)


async def create_catalog_company(
    request: Request,
    name: str = Form(...),
    tax_code: str = Form(''),
    address: str = Form(''),
    email: str = Form(''),
    phone: str = Form(''),
    bank_account: str = Form(''),
    bank_name: str = Form(''),
    business_activity: str = Form(''),
    director_user_id: str = Form(''),
    partner_type: str = Form('organization'),
    signature_file: Optional[UploadFile] = File(None),
    stamp_file: Optional[UploadFile] = File(None),
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.create_catalog_company(request=request, name=name, tax_code=tax_code, address=address, email=email, phone=phone, bank_account=bank_account, bank_name=bank_name, business_activity=business_activity, director_user_id=director_user_id, partner_type=partner_type, signature_file=signature_file, stamp_file=stamp_file, back=back, _=_)


async def edit_company_page(
    company_id: int,
    request: Request,
    back: str = Query(''),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_company_page(company_id=company_id, request=request, back=back, message=message, error=error, user=user)


async def edit_catalog_company(
    company_id: int,
    request: Request,
    name: str = Form(...),
    tax_code: str = Form(''),
    address: str = Form(''),
    email: str = Form(''),
    phone: str = Form(''),
    bank_account: str = Form(''),
    bank_name: str = Form(''),
    business_activity: str = Form(''),
    director_user_id: str = Form(''),
    partner_type: str = Form('organization'),
    signature_file: Optional[UploadFile] = File(None),
    stamp_file: Optional[UploadFile] = File(None),
    remove_signature: Optional[str] = Form(None),
    remove_stamp: Optional[str] = Form(None),
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.edit_catalog_company(company_id=company_id, request=request, name=name, tax_code=tax_code, address=address, email=email, phone=phone, bank_account=bank_account, bank_name=bank_name, business_activity=business_activity, director_user_id=director_user_id, partner_type=partner_type, signature_file=signature_file, stamp_file=stamp_file, remove_signature=remove_signature, remove_stamp=remove_stamp, back=back, _=_)


async def refresh_catalog_company(
    company_id: int,
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.refresh_catalog_company(company_id=company_id, back=back, _=_)


async def remove_catalog_company(
    request: Request,
    company_id: int,
    back: str = Form(''),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.remove_catalog_company(request=request, company_id=company_id, back=back, _=_)


async def bulk_delete_catalog_companies(request: Request, _: dict = Depends(require_menu('vehicles'))):
    return await controller.bulk_delete_catalog_companies(request=request, _=_)


async def bulk_refresh_catalog_companies(request: Request, _: dict = Depends(require_menu('vehicles'))):
    return await controller.bulk_refresh_catalog_companies(request=request, _=_)


async def pay_company_debt_endpoint(
    request: Request,
    company_id: int,
    amount: str = Form(...),
    note: str = Form(''),
    recorded_date: str = Form(''),
    reference_doc: str = Form(''),
    back: str = Form(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.pay_company_debt_endpoint(request=request, company_id=company_id, amount=amount, note=note, recorded_date=recorded_date, reference_doc=reference_doc, back=back, user=user)


async def add_company_debt_endpoint(
    request: Request,
    company_id: int,
    amount: str = Form(...),
    note: str = Form(''),
    recorded_date: str = Form(''),
    reference_doc: str = Form(''),
    back: str = Form(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.add_company_debt_endpoint(request=request, company_id=company_id, amount=amount, note=note, recorded_date=recorded_date, reference_doc=reference_doc, back=back, user=user)


async def get_company_debts_endpoint(
    company_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.get_company_debts_endpoint(company_id=company_id, request=request, user=user)


async def delete_company_debt_log_endpoint(
    log_id: int,
    request: Request,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_company_debt_log_endpoint(log_id=log_id, request=request, user=user)


async def upload_company_signature(
    company_id: int,
    file: UploadFile = File(...),
    _: dict = Depends(require_menu('vehicles')),
):
    'Upload signature image for a company and save to upload/companies.'
    return await controller.upload_company_signature(company_id=company_id, file=file, _=_)


async def upload_company_stamp(
    company_id: int,
    file: UploadFile = File(...),
    _: dict = Depends(require_menu('vehicles')),
):
    'Upload stamp image for a company and save to upload/companies.'
    return await controller.upload_company_stamp(company_id=company_id, file=file, _=_)


async def remove_company_signature(company_id: int, _: dict = Depends(require_menu('vehicles'))):
    'Remove signature image of a company.'
    return await controller.remove_company_signature(company_id=company_id, _=_)


async def remove_company_stamp(company_id: int, _: dict = Depends(require_menu('vehicles'))):
    'Remove stamp image of a company.'
    return await controller.remove_company_stamp(company_id=company_id, _=_)


async def export_company_contract(
    company_id: int,
    request: Request,
    download: str | None = Query(None),
    format: str | None = Query(None),
    view: str | None = Query(None),
    contract_date: str | None = Query(None),
    contract_no: str | None = Query(None),
    back: str = Query(''),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_company_contract(company_id=company_id, request=request, download=download, format=format, view=view, contract_date=contract_date, contract_no=contract_no, back=back, user=user)
