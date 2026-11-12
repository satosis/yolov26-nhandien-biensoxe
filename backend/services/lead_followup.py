"""Quản lý nhắc nhở yêu cầu tư vấn chưa được xử lý qua Telegram."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from backend.services.database import db
from backend.services.site_url import admin_page_url

logger = logging.getLogger(__name__)

# Nhãn tiếng Việt cho nguồn khách (chép từ frontend/tracking.py)
LABELS: dict[str, str] = {
    "google_ads": "Google Ads",
    "facebook_ads": "Facebook Ads",
    "facebook_page": "Facebook (bài đăng)",
    "facebook_group": "Facebook (nhóm)",
    "partner": "Đối tác giới thiệu",
    "organic_search": "Tìm kiếm tự nhiên",
    "referral": "Trang khác giới thiệu",
    "direct": "Truy cập trực tiếp",
}

DEFAULT_MINUTES: tuple[int, ...] = (15, 60)
MAX_AGE_HOURS: int = 48
MAX_ROWS: int = 20

def as_aware(value: datetime) -> datetime:
    """Trả datetime có múi giờ; giá trị không kèm múi giờ được hiểu là giờ địa phương của máy chủ.

    psycopg2 trả cột timestamptz dạng aware (UTC) còn datetime.now() là giờ địa phương không kèm múi giờ:
    chỉ bỏ múi giờ (replace(tzinfo=None)) sẽ lệch đúng độ lệch múi giờ (7 giờ ở Việt Nam).
    """
    return value if value.tzinfo is not None else value.astimezone()


def parse_dt(value: Any) -> datetime | None:
    """Đọc datetime hoặc chuỗi ISO; không đọc được thì trả None."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def thresholds() -> tuple[int, ...]:
    """Đọc cấu hình số phút nhắc nhở (nguyên dương, tăng dần, tối đa 4 mức)."""
    raw = os.environ.get("LEAD_REMIND_MINUTES", "15,60")
    if not raw or not isinstance(raw, str):
        return DEFAULT_MINUTES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts or len(parts) > 4:
        return DEFAULT_MINUTES
    res: list[int] = []
    last = 0
    for p in parts:
        try:
            val = int(p)
        except (ValueError, TypeError):
            return DEFAULT_MINUTES
        if val <= last:
            return DEFAULT_MINUTES
        res.append(val)
        last = val
    return tuple(res)


def enabled() -> bool:
    """Kiểm tra tính năng nhắc nhở có được bật không."""
    return os.environ.get("LEAD_REMIND_ENABLED", "1").strip() != "0"


def due_reminders(now: datetime | None = None, minutes: tuple[int, ...] | None = None) -> list[dict]:
    """Tìm các yêu cầu tư vấn cần nhắc nhở."""
    if now is None:
        now = datetime.now()
    if minutes is None:
        minutes = thresholds()
    if not minutes:
        return []

    now_aware = as_aware(now)
    cutoff_48h = now_aware - timedelta(hours=MAX_AGE_HOURS)
    rows = db._execute_query(
        "SELECT id, full_name, phone, company_name, demand, lead_source, "
            "utm_campaign, utm_term, created_at, remind_count "
            "FROM public.inquiries "
            "WHERE is_read = FALSE AND remind_count < %s AND created_at >= %s "
            "ORDER BY created_at ASC",
        (len(minutes), cutoff_48h),
        fetchall=True,
        commit=False,
    )

    if not rows or not isinstance(rows, list):
        return []

    due: list[dict] = []
    for r in rows:
        rc = r.get("remind_count")
        if rc is None:
            rc = 0
        if rc < 0 or rc >= len(minutes):
            continue
        req_min = minutes[rc]
        created = parse_dt(r.get("created_at"))
        if created is None:
            continue

        age_seconds = (now_aware - as_aware(created)).total_seconds()
        if age_seconds >= req_min * 60:
            due.append(r)
        if len(due) >= MAX_ROWS:
            break

    return due


def _sanitize(text: Any, max_len: int = 60) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[\r\n\t\x00-\x1f\x7f-\x9f]+", " ", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def build_reminder(
    rows: list[dict],
    now: datetime | None = None,
    admin_url: str | None = None,
    minutes: tuple[int, ...] | None = None,
) -> str:
    """Dựng nội dung tin nhắn Telegram nhắc nhở khách hàng chưa xử lý."""
    if now is None:
        now = datetime.now()
    if minutes is None:
        minutes = thresholds()
    now_aware = as_aware(now)

    n = len(rows)
    has_level_2 = any((r.get("remind_count") or 0) >= 1 for r in rows)
    if has_level_2:
        max_rc = max((r.get("remind_count") or 0) for r in rows)
        threshold_min = minutes[min(max_rc, len(minutes) - 1)] if minutes else 60
        header = f"⚠️ {n} yêu cầu tư vấn chưa xử lý, có yêu cầu quá {threshold_min} phút"
    else:
        header = f"⏰ {n} yêu cầu tư vấn chưa được xử lý"

    parts = [header]
    display = rows[:10]
    for r in display:
        name = _sanitize(r.get("full_name")) or "Khách hàng"
        phone = _sanitize(r.get("phone")) or "Chưa có SĐT"
        lead_source = r.get("lead_source") or "direct"
        channel_label = _sanitize(LABELS.get(lead_source, lead_source)) or LABELS["direct"]

        created = parse_dt(r.get("created_at"))
        age_min = max(0, int((now_aware - as_aware(created)).total_seconds() // 60)) if created else 0

        camp = _sanitize(r.get("utm_campaign")) or _sanitize(r.get("utm_term"))
        extra = f" · {camp}" if camp else ""

        parts.append(f"• {name} · {phone} · {channel_label} · {age_min} phút trước{extra}")

    remaining = n - len(display)
    if remaining > 0:
        parts.append(f"… và {remaining} yêu cầu nữa")

    if admin_url:
        parts.append(f"\n{admin_url}")

    return "\n".join(parts)


def admin_url() -> str:
    return admin_page_url("/admin/inquiries")


def tick(now: datetime | None = None, notify: Callable[..., bool] | None = None) -> dict:
    """Chu kỳ kiểm tra và gửi nhắc nhở Telegram."""
    if not enabled():
        return {"sent": 0, "reason": "off"}

    if now is None:
        now = datetime.now()

    rows = due_reminders(now)
    if not rows:
        return {"sent": 0, "reason": "none"}

    url = admin_url()
    text = build_reminder(rows, now, url)

    if notify is None:
        from backend.services.telegram_service import notify_telegram as notify

    reply_markup = {
        "inline_keyboard": [
            [{"text": "Mở Yêu cầu tư vấn", "url": url}]
        ]
    }

    ok = notify(
        text,
        important=True,
        prefix="",
        reply_markup=reply_markup,
    )

    if not ok:
        return {"sent": 0, "reason": "notify-failed"}

    ids = [r["id"] for r in rows]
    db._execute_query(
        "UPDATE public.inquiries SET remind_count = remind_count + 1, reminded_at = NOW() WHERE id = ANY(%s)",
        (ids,),
        commit=True,
    )
    return {"sent": len(ids), "reason": "sent"}

