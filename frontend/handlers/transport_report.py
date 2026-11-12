"""Cầu nối HTTP của frontend.modules.transport.report_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.transport.report_controller as controller


async def monthly_report(
    request: Request,
    month: str | None = None,
    vehicle_id: str | None = Query(None),
    report_page: int = Query(1, ge=1),
    trip_page: int = Query(1, ge=1),
    receivable_page: int = Query(1, ge=1),
    cash_page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.monthly_report(request=request, month=month, vehicle_id=vehicle_id, report_page=report_page, trip_page=trip_page, receivable_page=receivable_page, cash_page=cash_page, user=user)


async def vehicle_payroll(
    request: Request,
    month: str | None = None,
    vehicle_id: str | None = Query(None),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.vehicle_payroll(request=request, month=month, vehicle_id=vehicle_id, user=user)


async def vehicle_expenses(
    request: Request,
    month: str | None = None,
    vehicle_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.vehicle_expenses(request=request, month=month, vehicle_id=vehicle_id, page=page, user=user)


async def export_monthly_report(
    request: Request,
    month: str,
    vehicle_id: str | None = Query(None),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.export_monthly_report(request=request, month=month, vehicle_id=vehicle_id, _=_)


async def add_vehicle_expense(
    vehicle_id: int = Form(...),
    expense_date: str = Form(...),
    expense_type: str = Form(...),
    amount: str = Form(...),
    quantity: str = Form(''),
    unit_price: str = Form(''),
    vendor: str = Form(''),
    invoice_number: str = Form(''),
    note: str = Form(''),
    return_vehicle_id: str | None = Form(None),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.add_vehicle_expense(vehicle_id=vehicle_id, expense_date=expense_date, expense_type=expense_type, amount=amount, quantity=quantity, unit_price=unit_price, vendor=vendor, invoice_number=invoice_number, note=note, return_vehicle_id=return_vehicle_id, _=_)


async def delete_vehicle_expense(
    expense_id: int,
    month: str = Form(...),
    return_vehicle_id: str | None = Form(None),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.delete_vehicle_expense(expense_id=expense_id, month=month, return_vehicle_id=return_vehicle_id, _=_)


async def update_vehicle_expense_amount(
    expense_id: int,
    amount: str = Form(...),
    month: str = Form(...),
    return_vehicle_id: str | None = Form(None),
    _: dict = Depends(require_menu('vehicles')),
):
    return await controller.update_vehicle_expense_amount(expense_id=expense_id, amount=amount, month=month, return_vehicle_id=return_vehicle_id, _=_)
