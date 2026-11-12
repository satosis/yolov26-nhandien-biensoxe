"""Cầu nối HTTP của frontend.modules.enterprise.tax_checklist_controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Query, Depends, Form
from frontend.deps import require_menu
from typing import Optional
import frontend.modules.enterprise.tax_checklist_controller as controller


async def tax_checklist_page(
    request: Request,
    period: str = Query('2026-Q3'),
    view: str = Query('overview'),
    q: Optional[str] = Query(None),
    user: dict = Depends(require_menu('tax_checklist')),
):
    return await controller.tax_checklist_page(request=request, period=period, view=view, q=q, user=user)


async def cycle_status_api(
    request: Request,
    client_id: int = Form(...),
    period: str = Form(...),
    item_key: str = Form(...),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Toggle item status to next in cycle.'
    return await controller.cycle_status_api(request=request, client_id=client_id, period=period, item_key=item_key, user=user)


async def cycle_group_status_api(
    request: Request,
    client_id: int = Form(...),
    period: str = Form(...),
    group_id: str = Form(...),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Toggle all items in a group or single group-level status.'
    return await controller.cycle_group_status_api(request=request, client_id=client_id, period=period, group_id=group_id, user=user)


async def set_status_api(
    request: Request,
    client_id: int = Form(...),
    period: str = Form(...),
    item_key: str = Form(...),
    status: str = Form(...),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Set explicit item status.'
    return await controller.set_status_api(request=request, client_id=client_id, period=period, item_key=item_key, status=status, user=user)


async def update_note_api(
    request: Request,
    client_id: int = Form(...),
    period: str = Form(...),
    note: str = Form(''),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Update general note for client.'
    return await controller.update_note_api(request=request, client_id=client_id, period=period, note=note, user=user)


async def add_client_api(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    tax_code: str = Form(''),
    contact_info: str = Form(''),
    note: str = Form(''),
    period: str = Form('2026-Q3'),
    view: str = Form('overview'),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Add a new client and redirect back.'
    return await controller.add_client_api(request=request, code=code, name=name, tax_code=tax_code, contact_info=contact_info, note=note, period=period, view=view, user=user)


async def toggle_task_api(
    request: Request,
    task_id: int = Form(...),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Toggle task completion.'
    return await controller.toggle_task_api(request=request, task_id=task_id, user=user)


async def add_task_api(
    request: Request,
    period: str = Form(...),
    title: str = Form(...),
    client_name: str = Form(...),
    deadline: str = Form(...),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Add urgent task.'
    return await controller.add_task_api(request=request, period=period, title=title, client_name=client_name, deadline=deadline, user=user)


async def export_excel_api(
    period: str = Query('2026-Q3'),
    user: dict = Depends(require_menu('tax_checklist')),
):
    'Export checklist matrix as beautiful formatted Excel spreadsheet.'
    return await controller.export_excel_api(period=period, user=user)
