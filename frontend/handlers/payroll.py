"""Cầu nối HTTP của frontend.modules.payroll.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
import frontend.modules.payroll.controller as controller


async def staff_payroll_page(
    request: Request,
    month: str | None = None,
    tab: str = Query('payroll'),
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.staff_payroll_page(request=request, month=month, tab=tab, page=page, user=user)


async def save_payroll_timesheet(request: Request, user: dict = Depends(require_menu('vehicles'))):
    return await controller.save_payroll_timesheet(request=request, user=user)


async def mark_payroll_shift(request: Request, user: dict = Depends(require_menu('vehicles'))):
    'Chấm ca cho nhân viên theo các mốc giờ đang đi làm, ví dụ giờ xe qua trạm thu phí.\n\n    Body: {"user_id": 7, "passes": [{"date": "19/10/2026", "time": "03:29:28"}, ...]};\n    có thể thêm "passed_at": "2026-10-28T23:15" cho một mốc lẻ.\n    '
    return await controller.mark_payroll_shift(request=request, user=user)


async def quick_fill_payroll_timesheet(
    request: Request,
    month: str = Form(...),
    mode: str = Form('weekdays'),
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.quick_fill_payroll_timesheet(request=request, month=month, mode=mode, user=user)


async def export_staff_payroll(request: Request, month: str, user: dict = Depends(require_menu('vehicles'))):
    return await controller.export_staff_payroll(request=request, month=month, user=user)


async def staff_payroll_detail_page(
    request: Request,
    staff_id: int,
    month: str | None = None,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.staff_payroll_detail_page(request=request, staff_id=staff_id, month=month, user=user)


async def advance_staff_salary(
    request: Request,
    staff_id: int,
    user: dict = Depends(require_menu('vehicles')),
):
    return await controller.advance_staff_salary(request=request, staff_id=staff_id, user=user)


async def save_staff_payroll(request: Request, staff_id: int, _: dict = Depends(require_menu('vehicles'))):
    return await controller.save_staff_payroll(request=request, staff_id=staff_id, _=_)
