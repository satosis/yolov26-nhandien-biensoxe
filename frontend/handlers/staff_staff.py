"""Cầu nối HTTP của frontend.modules.staff.staff_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.staff.staff_controller as controller


async def staff_index(
    request: Request,
    page: int = Query(1, ge=1),
    department: str = Query(''),
    q: str = Query('', max_length=200),
    message: str = Query(''),
    error: str = Query(''),
    user: dict = Depends(require_menu('staff')),
):
    return await controller.staff_index(request=request, page=page, department=department, q=q, message=message, error=error, user=user)


async def get_staff_json(user_id: int, _: dict = Depends(require_menu('staff'))):
    return await controller.get_staff_json(user_id=user_id, _=_)


async def create_staff(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(''),
    password: str = Form(''),
    gender: str = Form(''),
    date_of_birth: str = Form(''),
    id_number: str = Form(''),
    phone: str = Form(''),
    email: str = Form(''),
    nickname: str = Form(''),
    note: str = Form(''),
    bank_account: str = Form(''),
    bank_name: str = Form(''),
    job_title: str = Form(''),
    department: str = Form(''),
    base_salary: str = Form('0'),
    insurance_salary: str = Form('0'),
    responsibility_allowance: str = Form('0'),
    meal_allowance: str = Form('0'),
    phone_allowance: str = Form('0'),
    back: str = Form('/admin/staff'),
    _: dict = Depends(require_menu('staff')),
):
    return await controller.create_staff(request=request, full_name=full_name, username=username, password=password, gender=gender, date_of_birth=date_of_birth, id_number=id_number, phone=phone, email=email, nickname=nickname, note=note, bank_account=bank_account, bank_name=bank_name, job_title=job_title, department=department, base_salary=base_salary, insurance_salary=insurance_salary, responsibility_allowance=responsibility_allowance, meal_allowance=meal_allowance, phone_allowance=phone_allowance, back=back, _=_)


async def update_staff(
    user_id: int,
    request: Request,
    full_name: str = Form(...),
    gender: str = Form(''),
    date_of_birth: str = Form(''),
    id_number: str = Form(''),
    phone: str = Form(''),
    email: str = Form(''),
    nickname: str = Form(''),
    note: str = Form(''),
    bank_account: str = Form(''),
    bank_name: str = Form(''),
    new_password: str = Form(''),
    job_title: str = Form(''),
    department: str = Form(''),
    base_salary: str = Form('0'),
    insurance_salary: str = Form('0'),
    responsibility_allowance: str = Form('0'),
    meal_allowance: str = Form('0'),
    phone_allowance: str = Form('0'),
    back: str = Form('/admin/staff'),
    _: dict = Depends(require_menu('staff')),
):
    return await controller.update_staff(user_id=user_id, request=request, full_name=full_name, gender=gender, date_of_birth=date_of_birth, id_number=id_number, phone=phone, email=email, nickname=nickname, note=note, bank_account=bank_account, bank_name=bank_name, new_password=new_password, job_title=job_title, department=department, base_salary=base_salary, insurance_salary=insurance_salary, responsibility_allowance=responsibility_allowance, meal_allowance=meal_allowance, phone_allowance=phone_allowance, back=back, _=_)


async def delete_staff(
    user_id: int,
    request: Request,
    back: str = Form('/admin/staff'),
    _: dict = Depends(require_menu('staff')),
):
    return await controller.delete_staff(user_id=user_id, request=request, back=back, _=_)


async def get_staff_records(user_id: int, _: dict = Depends(require_menu('staff'))):
    return await controller.get_staff_records(user_id=user_id, _=_)


async def create_staff_contract(
    user_id: int,
    contract_number: str = Form(''),
    contract_type: str = Form('xác định thời hạn'),
    sign_date: str = Form(''),
    start_date: str = Form(''),
    end_date: str = Form(''),
    salary_rate: str = Form('0'),
    insurance_rate: str = Form('0'),
    note: str = Form(''),
    _: dict = Depends(require_menu('staff')),
):
    return await controller.create_staff_contract(user_id=user_id, contract_number=contract_number, contract_type=contract_type, sign_date=sign_date, start_date=start_date, end_date=end_date, salary_rate=salary_rate, insurance_rate=insurance_rate, note=note, _=_)


async def delete_staff_contract(contract_id: int, _: dict = Depends(require_menu('staff'))):
    return await controller.delete_staff_contract(contract_id=contract_id, _=_)


async def create_staff_license(
    user_id: int,
    license_type: str = Form('GPLX B2'),
    license_number: str = Form(''),
    issued_date: str = Form(''),
    expiry_date: str = Form(''),
    issued_by: str = Form(''),
    note: str = Form(''),
    _: dict = Depends(require_menu('staff')),
):
    return await controller.create_staff_license(user_id=user_id, license_type=license_type, license_number=license_number, issued_date=issued_date, expiry_date=expiry_date, issued_by=issued_by, note=note, _=_)


async def delete_staff_license(license_id: int, _: dict = Depends(require_menu('staff'))):
    return await controller.delete_staff_license(license_id=license_id, _=_)


async def create_staff_work_history(
    user_id: int,
    event_type: str = Form('bổ nhiệm'),
    event_date: str = Form(...),
    old_value: str = Form(''),
    new_value: str = Form(''),
    decision_number: str = Form(''),
    note: str = Form(''),
    user: dict = Depends(require_menu('staff')),
):
    return await controller.create_staff_work_history(user_id=user_id, event_type=event_type, event_date=event_date, old_value=old_value, new_value=new_value, decision_number=decision_number, note=note, user=user)


async def delete_staff_work_history(hist_id: int, _: dict = Depends(require_menu('staff'))):
    return await controller.delete_staff_work_history(hist_id=hist_id, _=_)
