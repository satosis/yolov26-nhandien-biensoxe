"""Service handling user schedule reminders stored in Docker PostgreSQL."""

from __future__ import annotations
from datetime import date, datetime
from typing import Any
import logging

from backend.services.database import db

logger = logging.getLogger("schedule_reminders")


def compute_reminder_state(due_date: date, remind_before_days: int, status: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    days_diff = (due_date - today).days

    if status == "completed":
        return {
            "state": "completed",
            "days_diff": days_diff,
            "text": "Đã hoàn thành",
            "badge_class": "badge-soft-success",
            "badge_color": "#10b981",
            "is_active": False,
            "urgency": 0,
        }
    if status == "cancelled":
        return {
            "state": "cancelled",
            "days_diff": days_diff,
            "text": "Đã hủy",
            "badge_class": "badge-soft-secondary",
            "badge_color": "#6b7280",
            "is_active": False,
            "urgency": 0,
        }

    # Pending status
    if days_diff < 0:
        return {
            "state": "overdue",
            "days_diff": days_diff,
            "text": f"Quá hạn {abs(days_diff)} ngày!",
            "badge_class": "badge-soft-danger",
            "badge_color": "#ef4444",
            "is_active": True,
            "urgency": 3,
        }
    if days_diff == 0:
        return {
            "state": "due_today",
            "days_diff": 0,
            "text": "Hôm nay đến hạn!",
            "badge_class": "badge-soft-danger",
            "badge_color": "#dc2626",
            "is_active": True,
            "urgency": 2,
        }
    if days_diff <= remind_before_days:
        return {
            "state": "reminding",
            "days_diff": days_diff,
            "text": f"Còn {days_diff} ngày",
            "badge_class": "badge-soft-warning",
            "badge_color": "#f59e0b",
            "is_active": True,
            "urgency": 1,
        }
    return {
        "state": "upcoming",
        "days_diff": days_diff,
        "text": f"Còn {days_diff} ngày (chưa đến kỳ nhắc)",
        "badge_class": "badge-soft-info",
        "badge_color": "#3b82f6",
        "is_active": False,
        "urgency": 0,
    }


def _format_row(row: dict[str, Any]) -> dict[str, Any]:
    due_date = row["due_date"]
    remind_days = int(row.get("remind_before_days") or 1)
    status = str(row.get("status") or "pending")
    state_info = compute_reminder_state(due_date, remind_days, status)

    created_at = row.get("created_at")
    completed_at = row.get("completed_at")

    old_due_date = row.get("old_due_date")

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "company_id": row.get("company_id"),
        "title": row["title"],
        "description": row.get("description") or "",
        "due_date": due_date.isoformat() if hasattr(due_date, "isoformat") else str(due_date),
        "due_date_display": due_date.strftime("%d/%m/%Y") if hasattr(due_date, "strftime") else str(due_date),
        "old_due_date": old_due_date.isoformat() if old_due_date and hasattr(old_due_date, "isoformat") else (str(old_due_date) if old_due_date else None),
        "old_due_date_display": old_due_date.strftime("%d/%m/%Y") if old_due_date and hasattr(old_due_date, "strftime") else (str(old_due_date) if old_due_date else "--"),
        "remind_before_days": remind_days,
        "priority": row.get("priority") or "normal",
        "status": status,
        "completed_at": completed_at.strftime("%d/%m/%Y %H:%M") if completed_at and hasattr(completed_at, "strftime") else None,
        "created_at": created_at.strftime("%d/%m/%Y %H:%M") if created_at and hasattr(created_at, "strftime") else None,
        **state_info,
    }


def get_active_reminders_for_user(user_id: int) -> list[dict[str, Any]]:
    """Get active reminders for user that need attention today (in reminding period or overdue)."""
    rows = db._execute_query(
        """
        SELECT * FROM public.schedule_reminders
        WHERE user_id = %s AND status = 'pending'
        ORDER BY due_date ASC, id ASC
        """,
        (user_id,),
        fetchall=True,
        commit=False,
    ) or []

    results = []
    for r in rows:
        formatted = _format_row(r)
        if formatted["is_active"]:
            results.append(formatted)

    # Sort: overdue (urgency=3) first, then due today (urgency=2), then reminding (urgency=1)
    results.sort(key=lambda x: (-x["urgency"], x["days_diff"]))
    return results


def get_all_reminders_for_user(
    user_id: int, status_filter: str = "all", search: str = ""
) -> list[dict[str, Any]]:
    """List reminders with filter and search."""
    params: list[Any] = [user_id]
    clauses = ["user_id = %s"]

    if status_filter in ("pending", "completed", "cancelled"):
        clauses.append("status = %s")
        params.append(status_filter)

    if search.strip():
        clauses.append("(title ILIKE %s OR description ILIKE %s)")
        q = f"%{search.strip()}%"
        params.extend([q, q])

    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT * FROM public.schedule_reminders
        WHERE {where_sql}
        ORDER BY
            CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
            due_date ASC,
            id DESC
    """
    rows = db._execute_query(query, tuple(params), fetchall=True, commit=False) or []
    return [_format_row(r) for r in rows]


def create_reminder(user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Create a new reminder for the user."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Vui lòng nhập nội dung nhắc lịch.")

    raw_due = data.get("due_date")
    if not raw_due:
        raise ValueError("Vui lòng chọn hạn phải làm.")
    if isinstance(raw_due, str):
        due_date = date.fromisoformat(raw_due.strip())
    elif isinstance(raw_due, date):
        due_date = raw_due
    else:
        raise ValueError("Ngày đến hạn không hợp lệ.")

    raw_old = data.get("old_due_date")
    old_due_date = None
    if raw_old:
        if isinstance(raw_old, str) and raw_old.strip():
            old_due_date = date.fromisoformat(raw_old.strip())
        elif isinstance(raw_old, date):
            old_due_date = raw_old

    try:
        remind_before_days = int(data.get("remind_before_days") or 1)
        if remind_before_days < 0:
            remind_before_days = 0
    except (ValueError, TypeError):
        remind_before_days = 1

    description = str(data.get("description") or "").strip()
    company_id = data.get("company_id")
    if company_id:
        try:
            company_id = int(company_id)
        except (ValueError, TypeError):
            company_id = None
    else:
        company_id = None

    priority = str(data.get("priority") or "normal").strip()
    if priority not in ("urgent", "high", "normal", "low"):
        priority = "normal"

    row = db._execute_query(
        """
        INSERT INTO public.schedule_reminders
            (user_id, company_id, title, description, due_date, old_due_date, remind_before_days, priority, status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        RETURNING *
        """,
        (user_id, company_id, title, description, due_date, old_due_date, remind_before_days, priority),
        fetch=True,
        commit=True,
    )
    if not row:
        raise RuntimeError("Không thể tạo nhắc lịch.")
    return _format_row(row)


def update_reminder(reminder_id: int, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Update an existing reminder belonging to user."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Vui lòng nhập nội dung nhắc lịch.")

    raw_due = data.get("due_date")
    if isinstance(raw_due, str):
        due_date = date.fromisoformat(raw_due.strip())
    elif isinstance(raw_due, date):
        due_date = raw_due
    else:
        raise ValueError("Ngày đến hạn không hợp lệ.")

    raw_old = data.get("old_due_date")
    old_due_date = None
    if raw_old:
        if isinstance(raw_old, str) and raw_old.strip():
            old_due_date = date.fromisoformat(raw_old.strip())
        elif isinstance(raw_old, date):
            old_due_date = raw_old

    try:
        remind_before_days = int(data.get("remind_before_days") or 1)
        if remind_before_days < 0:
            remind_before_days = 0
    except (ValueError, TypeError):
        remind_before_days = 1

    description = str(data.get("description") or "").strip()
    priority = str(data.get("priority") or "normal").strip()

    row = db._execute_query(
        """
        UPDATE public.schedule_reminders
        SET title = %s, description = %s, due_date = %s, old_due_date = %s, remind_before_days = %s, priority = %s, updated_at = NOW()
        WHERE id = %s AND user_id = %s
        RETURNING *
        """,
        (title, description, due_date, old_due_date, remind_before_days, priority, reminder_id, user_id),
        fetch=True,
        commit=True,
    )
    if not row:
        raise ValueError("Không tìm thấy nhắc lịch hoặc bạn không có quyền sửa.")
    return _format_row(row)


def renew_reminder(reminder_id: int, user_id: int, next_due_date: str | date, note: str = "") -> dict[str, Any]:
    """Complete current cycle and advance to next due date, preserving old_due_date."""
    if isinstance(next_due_date, str):
        next_due = date.fromisoformat(next_due_date.strip())
    else:
        next_due = next_due_date

    clean_note = str(note or "").strip()
    row = db._execute_query(
        """
        UPDATE public.schedule_reminders
        SET old_due_date = due_date,
            due_date = %s,
            description = CASE WHEN %s <> '' THEN %s ELSE description END,
            status = 'pending',
            completed_at = NULL,
            updated_at = NOW()
        WHERE id = %s AND user_id = %s
        RETURNING *
        """,
        (next_due, clean_note, clean_note, reminder_id, user_id),
        fetch=True,
        commit=True,
    )
    if not row:
        raise ValueError("Không tìm thấy nhắc lịch hoặc bạn không có quyền thao tác.")
    return _format_row(row)


def complete_reminder(reminder_id: int, user_id: int) -> bool:
    """Mark reminder as completed by user."""
    row = db._execute_query(
        """
        UPDATE public.schedule_reminders
        SET status = 'completed', completed_at = NOW(), updated_at = NOW()
        WHERE id = %s AND user_id = %s
        RETURNING id
        """,
        (reminder_id, user_id),
        fetch=True,
        commit=True,
    )
    return bool(row)


def reopen_reminder(reminder_id: int, user_id: int) -> bool:
    """Reopen a completed reminder."""
    row = db._execute_query(
        """
        UPDATE public.schedule_reminders
        SET status = 'pending', completed_at = NULL, updated_at = NOW()
        WHERE id = %s AND user_id = %s
        RETURNING id
        """,
        (reminder_id, user_id),
        fetch=True,
        commit=True,
    )
    return bool(row)


def delete_reminder(reminder_id: int, user_id: int) -> bool:
    """Delete a reminder belonging to user."""
    row = db._execute_query(
        """
        DELETE FROM public.schedule_reminders
        WHERE id = %s AND user_id = %s
        RETURNING id
        """,
        (reminder_id, user_id),
        fetch=True,
        commit=True,
    )
    return bool(row)
