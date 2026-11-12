"""Cầu nối HTTP của frontend.modules.reminders.controller: nhận tham số của request rồi giao cho controller, không làm gì thêm."""
from fastapi import Request, Depends, Query
from frontend.deps import get_current_user
from typing import Any
import frontend.modules.reminders.controller as controller


async def get_reminders_badge(request: Request, user: dict[str, Any] = Depends(get_current_user)):
    'Fetch active reminders count and top items for the Header Bell.'
    return await controller.get_reminders_badge(request=request, user=user)


async def get_all_reminders(
    request: Request,
    status: str = Query('all'),
    q: str = Query(''),
    user: dict[str, Any] = Depends(get_current_user),
):
    'Fetch all reminders for the user with status filter and search.'
    return await controller.get_all_reminders(request=request, status=status, q=q, user=user)


async def create_reminder_api(request: Request, user: dict[str, Any] = Depends(get_current_user)):
    'Create a new reminder.'
    return await controller.create_reminder_api(request=request, user=user)


async def update_reminder_api(
    reminder_id: int,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
):
    'Update a reminder.'
    return await controller.update_reminder_api(reminder_id=reminder_id, request=request, user=user)


async def complete_reminder_api(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)):
    'Mark a reminder as completed.'
    return await controller.complete_reminder_api(reminder_id=reminder_id, user=user)


async def renew_reminder_api(
    reminder_id: int,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
):
    'Renew/advance a reminder to next due date with old due date preserved.'
    return await controller.renew_reminder_api(reminder_id=reminder_id, request=request, user=user)


async def reopen_reminder_api(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)):
    'Reopen a completed reminder.'
    return await controller.reopen_reminder_api(reminder_id=reminder_id, user=user)


async def delete_reminder_api(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)):
    'Delete a reminder.'
    return await controller.delete_reminder_api(reminder_id=reminder_id, user=user)
