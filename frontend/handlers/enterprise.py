"""Cầu nối HTTP của frontend.modules.enterprise.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form, UploadFile, File
from frontend.deps import require_menu, get_current_user
from typing import Optional
import frontend.modules.enterprise.controller as controller


async def enterprise_page(
    request: Request,
    company_id: Optional[int] = Query(None),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('enterprise')),
):
    return await controller.enterprise_page(request=request, company_id=company_id, message=message, error=error, user=user)


async def update_enterprise(
    request: Request,
    company_id: int = Form(...),
    tax_code: str = Form(...),
    name: str = Form(...),
    address: str = Form(...),
    email: str = Form(''),
    phone: str = Form(''),
    business_activity: str = Form(''),
    director_user_id: str = Form(''),
    signature_file: Optional[UploadFile] = File(None),
    stamp_file: Optional[UploadFile] = File(None),
    remove_signature: Optional[str] = Form(None),
    remove_stamp: Optional[str] = Form(None),
    _: dict = Depends(require_menu('enterprise')),
):
    return await controller.update_enterprise(request=request, company_id=company_id, tax_code=tax_code, name=name, address=address, email=email, phone=phone, business_activity=business_activity, director_user_id=director_user_id, signature_file=signature_file, stamp_file=stamp_file, remove_signature=remove_signature, remove_stamp=remove_stamp, _=_)


async def refresh_enterprise(company_id: int = Form(...), _: dict = Depends(require_menu('enterprise'))):
    return await controller.refresh_enterprise(company_id=company_id, _=_)


async def departments_page(
    request: Request,
    page: int = Query(1, ge=1),
    company_id: Optional[int] = Query(None),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('departments')),
):
    return await controller.departments_page(request=request, page=page, company_id=company_id, message=message, error=error, user=user)


async def department_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(''),
    manager_user_id: str = Form(''),
    description: str = Form(''),
    user: dict = Depends(require_menu('departments')),
):
    return await controller.department_create(request=request, name=name, code=code, manager_user_id=manager_user_id, description=description, user=user)


async def department_edit(
    dept_id: int,
    name: str = Form(...),
    code: str = Form(''),
    manager_user_id: str = Form(''),
    description: str = Form(''),
    _: dict = Depends(require_menu('departments')),
):
    return await controller.department_edit(dept_id=dept_id, name=name, code=code, manager_user_id=manager_user_id, description=description, _=_)


async def department_delete(dept_id: int, _: dict = Depends(require_menu('departments'))):
    return await controller.department_delete(dept_id=dept_id, _=_)


async def attendance_page(
    request: Request,
    month: str = Query(''),
    tab: str = Query('sheet'),
    attendance_page: int = Query(1, ge=1),
    leave_page: int = Query(1, ge=1),
    company_id: Optional[int] = Query(None),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('attendance')),
):
    return await controller.attendance_page(request=request, month=month, tab=tab, attendance_page=attendance_page, leave_page=leave_page, company_id=company_id, message=message, error=error, user=user)


async def attendance_save_row(
    user_id: int = Form(...),
    month: str = Form(...),
    standard_days: float = Form(26.0),
    actual_days: float = Form(0.0),
    paid_leave_days: float = Form(0.0),
    unpaid_leave_days: float = Form(0.0),
    overtime_hours: float = Form(0.0),
    note: str = Form(''),
    user: dict = Depends(require_menu('attendance')),
    request: Request = None,
):
    return await controller.attendance_save_row(user_id=user_id, month=month, standard_days=standard_days, actual_days=actual_days, paid_leave_days=paid_leave_days, unpaid_leave_days=unpaid_leave_days, overtime_hours=overtime_hours, note=note, user=user, request=request)


async def attendance_quick_fill(
    month: str = Form(...),
    standard_days: float = Form(26.0),
    user: dict = Depends(require_menu('attendance')),
    request: Request = None,
):
    return await controller.attendance_quick_fill(month=month, standard_days=standard_days, user=user, request=request)


async def leave_request_create(
    request: Request,
    user_id: int = Form(...),
    leave_type: str = Form('phép năm'),
    start_date: str = Form(...),
    end_date: str = Form(...),
    total_days: float = Form(1.0),
    reason: str = Form(''),
    user: dict = Depends(require_menu('attendance')),
):
    return await controller.leave_request_create(request=request, user_id=user_id, leave_type=leave_type, start_date=start_date, end_date=end_date, total_days=total_days, reason=reason, user=user)


async def leave_request_update_status(
    req_id: int,
    status: str = Form(...),
    user: dict = Depends(require_menu('attendance')),
):
    return await controller.leave_request_update_status(req_id=req_id, status=status, user=user)


async def leave_request_delete(req_id: int, _: dict = Depends(require_menu('attendance'))):
    return await controller.leave_request_delete(req_id=req_id, _=_)


async def advances_page(
    request: Request,
    page: int = Query(1, ge=1),
    status: str = Query('all'),
    month: str = Query(''),
    company_id: Optional[int] = Query(None),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('advances')),
):
    return await controller.advances_page(request=request, page=page, status=status, month=month, company_id=company_id, message=message, error=error, user=user)


async def advance_create(
    request: Request,
    user_id: int = Form(...),
    advance_type: str = Form('chi phí'),
    amount: str = Form(...),
    advance_date: str = Form(...),
    reason: str = Form(''),
    bank_account: str = Form(''),
    bank_name: str = Form(''),
    user: dict = Depends(require_menu('advances')),
):
    return await controller.advance_create(request=request, user_id=user_id, advance_type=advance_type, amount=amount, advance_date=advance_date, reason=reason, bank_account=bank_account, bank_name=bank_name, user=user)


async def advance_settle(
    adv_id: int,
    settled_amount: str = Form(...),
    settled_date: str = Form(...),
    settle_note: str = Form(''),
    _: dict = Depends(require_menu('advances')),
):
    return await controller.advance_settle(adv_id=adv_id, settled_amount=settled_amount, settled_date=settled_date, settle_note=settle_note, _=_)


async def advance_update_status(
    adv_id: int,
    status: str = Form(...),
    _: dict = Depends(require_menu('advances')),
):
    return await controller.advance_update_status(adv_id=adv_id, status=status, _=_)


async def advance_delete(adv_id: int, _: dict = Depends(require_menu('advances'))):
    return await controller.advance_delete(adv_id=adv_id, _=_)


async def assets_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('assets')),
):
    return await controller.assets_page(request=request, page=page, user=user)


async def asset_assignment_create(
    request: Request,
    user_id: int = Form(...),
    asset_type: str = Form('xe'),
    vehicle_id: str = Form(''),
    asset_name: str = Form(...),
    asset_code: str = Form(''),
    handover_date: str = Form(...),
    initial_odo: str = Form(''),
    condition_note: str = Form(''),
    user: dict = Depends(require_menu('assets')),
):
    return await controller.asset_assignment_create(request=request, user_id=user_id, asset_type=asset_type, vehicle_id=vehicle_id, asset_name=asset_name, asset_code=asset_code, handover_date=handover_date, initial_odo=initial_odo, condition_note=condition_note, user=user)


async def asset_assignment_return(
    assign_id: int,
    return_date: str = Form(...),
    return_odo: str = Form(''),
    condition_note: str = Form(''),
    _: dict = Depends(require_menu('assets')),
):
    return await controller.asset_assignment_return(assign_id=assign_id, return_date=return_date, return_odo=return_odo, condition_note=condition_note, _=_)


async def asset_assignment_delete(assign_id: int, _: dict = Depends(require_menu('assets'))):
    return await controller.asset_assignment_delete(assign_id=assign_id, _=_)


async def enterprise_assets_api(
    request: Request,
    q: str = Query(''),
    category: str = Query(''),
    status: str = Query(''),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('assets')),
):
    return await controller.enterprise_assets_api(request=request, q=q, category=category, status=status, page=page, user=user)


async def enterprise_asset_create_api(request: Request, user: dict = Depends(require_menu('assets'))):
    return await controller.enterprise_asset_create_api(request=request, user=user)


async def enterprise_asset_update_api(
    asset_id: int,
    request: Request,
    user: dict = Depends(require_menu('assets')),
):
    return await controller.enterprise_asset_update_api(asset_id=asset_id, request=request, user=user)


async def enterprise_asset_delete_api(
    asset_id: int,
    request: Request,
    user: dict = Depends(require_menu('assets')),
):
    return await controller.enterprise_asset_delete_api(asset_id=asset_id, request=request, user=user)


async def enterprise_debts_page(
    request: Request,
    page: int = Query(1, ge=1),
    log_page: int = Query(1, ge=1),
    q: str = Query(''),
    debt_only: str = Query('0'),
    month: str = Query(''),
    personal_month: str = Query(''),
    personal_q: str = Query(''),
    personal_debt_only: int = Query(0),
    tab: str = Query(''),
    due_only: int = Query(0),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.enterprise_debts_page(request=request, page=page, log_page=log_page, q=q, debt_only=debt_only, month=month, personal_month=personal_month, personal_q=personal_q, personal_debt_only=personal_debt_only, tab=tab, due_only=due_only, message=message, error=error, user=user)


async def enterprise_debt_add(
    request: Request,
    company_id: int = Form(...),
    amount: str = Form(...),
    recorded_date: str = Form(''),
    reference_doc: str = Form(''),
    note: str = Form(''),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.enterprise_debt_add(request=request, company_id=company_id, amount=amount, recorded_date=recorded_date, reference_doc=reference_doc, note=note, user=user)


async def enterprise_debt_pay(
    request: Request,
    company_id: int = Form(...),
    amount: str = Form(...),
    recorded_date: str = Form(''),
    reference_doc: str = Form(''),
    note: str = Form(''),
    payment_method: str = Form('bank'),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.enterprise_debt_pay(request=request, company_id=company_id, amount=amount, recorded_date=recorded_date, reference_doc=reference_doc, note=note, payment_method=payment_method, user=user)


async def enterprise_debt_delete(log_id: int, request: Request, user: dict = Depends(require_menu('debts'))):
    return await controller.enterprise_debt_delete(log_id=log_id, request=request, user=user)


async def enterprise_debt_update(log_id: int, request: Request, user: dict = Depends(require_menu('debts'))):
    return await controller.enterprise_debt_update(log_id=log_id, request=request, user=user)


async def enterprise_debt_company_logs(
    company_id: int,
    request: Request,
    user: dict = Depends(require_menu('debts')),
):
    return await controller.enterprise_debt_company_logs(company_id=company_id, request=request, user=user)


async def personal_debt_import_preview_api(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_import_preview_api(request=request, file=file, user=user)


async def personal_debt_import_api(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_import_api(request=request, file=file, user=user)


async def personal_debt_customers_api(
    request: Request,
    month: str = Query(''),
    q: str = Query(''),
    debt_only: int = Query(0),
    due_only: int = Query(0),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_customers_api(request=request, month=month, q=q, debt_only=debt_only, due_only=due_only, user=user)


async def personal_debt_customers_merge_duplicates(
    request: Request,
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_customers_merge_duplicates(request=request, user=user)


async def personal_debt_customer_create(
    request: Request,
    customer_code: str = Form(''),
    full_name: str = Form(...),
    phone: str = Form(''),
    address: str = Form(''),
    opening_balance: str = Form('0'),
    opening_balance_date: str = Form(''),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_customer_create(request=request, customer_code=customer_code, full_name=full_name, phone=phone, address=address, opening_balance=opening_balance, opening_balance_date=opening_balance_date, user=user)


async def personal_debt_customer_edit(
    customer_id: int,
    request: Request,
    customer_code: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(''),
    address: str = Form(''),
    opening_balance: str = Form('0'),
    opening_balance_date: str = Form(''),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_customer_edit(customer_id=customer_id, request=request, customer_code=customer_code, full_name=full_name, phone=phone, address=address, opening_balance=opening_balance, opening_balance_date=opening_balance_date, user=user)


async def personal_debt_customer_delete(
    customer_id: int,
    request: Request,
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_customer_delete(customer_id=customer_id, request=request, user=user)


async def personal_debt_transaction_create(
    request: Request,
    customer_id: int = Form(...),
    invoice_number: str = Form(''),
    transaction_date: str = Form(''),
    amount_due: str = Form('0'),
    amount_paid: str = Form('0'),
    received_from: str = Form(''),
    note: str = Form(''),
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_transaction_create(request=request, customer_id=customer_id, invoice_number=invoice_number, transaction_date=transaction_date, amount_due=amount_due, amount_paid=amount_paid, received_from=received_from, note=note, user=user)


async def personal_debt_customer_logs(
    customer_id: int,
    request: Request,
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_customer_logs(customer_id=customer_id, request=request, user=user)


async def personal_debt_transaction_edit(
    log_id: int,
    request: Request,
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_transaction_edit(log_id=log_id, request=request, user=user)


async def personal_debt_transaction_delete(
    log_id: int,
    request: Request,
    user: dict = Depends(require_menu('debts')),
):
    return await controller.personal_debt_transaction_delete(log_id=log_id, request=request, user=user)


async def enterprise_sales_page(request: Request, user: dict = Depends(require_menu('sales'))):
    return await controller.enterprise_sales_page(request=request, user=user)


async def enterprise_sales_next_stt_api(request: Request, user: dict = Depends(require_menu('sales'))):
    return await controller.enterprise_sales_next_stt_api(request=request, user=user)


async def enterprise_sales_list_api(
    request: Request,
    q: str = Query(''),
    date_from: str = Query(''),
    date_to: str = Query(''),
    status: str = Query(''),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=200),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_list_api(request=request, q=q, date_from=date_from, date_to=date_to, status=status, page=page, per_page=per_page, user=user)


async def enterprise_sales_items_api(
    request: Request,
    q: str = Query(''),
    product_name: str = Query(''),
    customer_id: str = Query(''),
    date_from: str = Query(''),
    date_to: str = Query(''),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=200),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_items_api(request=request, q=q, product_name=product_name, customer_id=customer_id, date_from=date_from, date_to=date_to, page=page, per_page=per_page, user=user)


async def enterprise_sales_items_export_api(
    request: Request,
    q: str = Query(''),
    product_name: str = Query(''),
    customer_id: str = Query(''),
    date_from: str = Query(''),
    date_to: str = Query(''),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_items_export_api(request=request, q=q, product_name=product_name, customer_id=customer_id, date_from=date_from, date_to=date_to, user=user)


async def enterprise_sales_update_status_api(
    sale_id: int,
    request: Request,
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_update_status_api(sale_id=sale_id, request=request, user=user)


async def enterprise_sales_get_api(
    sale_id: int,
    request: Request,
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_get_api(sale_id=sale_id, request=request, user=user)


async def enterprise_sales_create_api(request: Request, user: dict = Depends(require_menu('sales'))):
    return await controller.enterprise_sales_create_api(request=request, user=user)


async def enterprise_sales_update_api(
    sale_id: int,
    request: Request,
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_update_api(sale_id=sale_id, request=request, user=user)


async def enterprise_sales_delete_api(
    sale_id: int,
    request: Request,
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_delete_api(sale_id=sale_id, request=request, user=user)


async def enterprise_sales_import_preview_api(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_import_preview_api(request=request, file=file, user=user)


async def enterprise_sales_import_api(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_import_api(request=request, file=file, user=user)


async def enterprise_sales_payment_api(
    sale_id: int,
    request: Request,
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_payment_api(sale_id=sale_id, request=request, user=user)


async def enterprise_sales_invoice_pdf(
    sale_id: int,
    request: Request,
    user: dict = Depends(require_menu('sales')),
):
    return await controller.enterprise_sales_invoice_pdf(sale_id=sale_id, request=request, user=user)


async def enterprise_sales_presence_heartbeat_api(
    request: Request,
    user: dict = Depends(get_current_user),
):
    return await controller.enterprise_presence_heartbeat_api(request=request, user=user)


async def enterprise_sales_presence_release_api(
    request: Request,
    user: dict = Depends(get_current_user),
):
    return await controller.enterprise_presence_release_api(request=request, user=user)


async def enterprise_sales_presence_active_api(
    request: Request,
    user: dict = Depends(get_current_user),
):
    return await controller.enterprise_presence_active_api(request=request, user=user)

