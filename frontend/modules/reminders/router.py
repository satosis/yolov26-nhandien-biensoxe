"""Router for schedule reminders API and Header Bell integration."""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from frontend.deps import get_current_user
from frontend.modules.reminders import reminder_service

router = APIRouter(prefix="/api/reminders", tags=["Reminders"])


@router.get("/badge")
async def get_reminders_badge(request: Request, user: dict[str, Any] = Depends(get_current_user)):
    """Fetch active reminders count and top items for the Header Bell."""
    user_id = user.get("id")
    if not user_id:
        return JSONResponse({"ok": True, "count": 0, "items": []})

    try:
        active = reminder_service.get_active_reminders_for_user(int(user_id))
        return JSONResponse({
            "ok": True,
            "count": len(active),
            "items": active[:8],  # Show up to 8 items in the dropdown
        })
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e), "count": 0, "items": []}, status_code=500)


@router.get("/all")
async def get_all_reminders(
    request: Request,
    status: str = Query("all"),
    q: str = Query(""),
    user: dict[str, Any] = Depends(get_current_user),
):
    """Fetch all reminders for the user with status filter and search."""
    user_id = user.get("id")
    if not user_id:
        return JSONResponse({"ok": True, "items": []})

    try:
        items = reminder_service.get_all_reminders_for_user(int(user_id), status_filter=status, search=q)
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@router.post("")
async def create_reminder_api(request: Request, user: dict[str, Any] = Depends(get_current_user)):
    """Create a new reminder."""
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập.")

    try:
        data = await request.json()
        item = reminder_service.create_reminder(int(user_id), data)
        return JSONResponse({
            "ok": True,
            "message": "Đã tạo lịch nhắc thành công.",
            "item": item,
        })
    except ValueError as ve:
        return JSONResponse({"ok": False, "message": str(ve)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Lỗi hệ thống: {str(e)}"}, status_code=500)


@router.put("/{reminder_id}")
async def update_reminder_api(
    reminder_id: int, request: Request, user: dict[str, Any] = Depends(get_current_user)
):
    """Update a reminder."""
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập.")

    try:
        data = await request.json()
        item = reminder_service.update_reminder(reminder_id, int(user_id), data)
        return JSONResponse({
            "ok": True,
            "message": "Đã cập nhật nhắc lịch.",
            "item": item,
        })
    except ValueError as ve:
        return JSONResponse({"ok": False, "message": str(ve)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@router.post("/{reminder_id}/complete")
async def complete_reminder_api(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)):
    """Mark a reminder as completed."""
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập.")

    success = reminder_service.complete_reminder(reminder_id, int(user_id))
    if success:
        return JSONResponse({"ok": True, "message": "Đã đánh dấu hoàn thành."})
    return JSONResponse({"ok": False, "message": "Không tìm thấy lịch nhắc."}, status_code=404)


@router.post("/{reminder_id}/renew")
async def renew_reminder_api(
    reminder_id: int, request: Request, user: dict[str, Any] = Depends(get_current_user)
):
    """Renew/advance a reminder to next due date with old due date preserved."""
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập.")

    try:
        data = await request.json()
        next_due = data.get("next_due_date")
        if not next_due:
            return JSONResponse({"ok": False, "message": "Vui lòng cung cấp hạn tiếp theo."}, status_code=400)
        note = data.get("description") or data.get("note") or ""
        item = reminder_service.renew_reminder(reminder_id, int(user_id), next_due, note)
        return JSONResponse({"ok": True, "message": "Đã gia hạn việc thành công.", "item": item})
    except ValueError as ve:
        return JSONResponse({"ok": False, "message": str(ve)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@router.post("/{reminder_id}/reopen")
async def reopen_reminder_api(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)):
    """Reopen a completed reminder."""
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập.")

    success = reminder_service.reopen_reminder(reminder_id, int(user_id))
    if success:
        return JSONResponse({"ok": True, "message": "Đã mở lại nhắc lịch."})
    return JSONResponse({"ok": False, "message": "Không tìm thấy lịch nhắc."}, status_code=404)


@router.delete("/{reminder_id}")
async def delete_reminder_api(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)):
    """Delete a reminder."""
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập.")

    success = reminder_service.delete_reminder(reminder_id, int(user_id))
    if success:
        return JSONResponse({"ok": True, "message": "Đã xóa lịch nhắc."})
    return JSONResponse({"ok": False, "message": "Không tìm thấy lịch nhắc."}, status_code=404)
