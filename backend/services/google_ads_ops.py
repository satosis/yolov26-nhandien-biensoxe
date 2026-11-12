from __future__ import annotations
import logging
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from backend.services.database import db
from backend.services.lead_followup import as_aware
from backend.services.site_url import admin_page_url
from backend.services.periodic import start_periodic
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger(__name__)
ADS_DIGEST_KEY = "ADS_DIGEST_LAST_DATE"
ADS_SYNC_LAST_AT = "ADS_SYNC_LAST_AT"


def next_status(status: str, start_date, end_date, today: date) -> str:
    """Tính trạng thái kế tiếp cho chiến dịch (hàm thuần, không gọi DB)."""
    if isinstance(start_date, str):
        try:
            start_date = date.fromisoformat(start_date)
        except Exception:
            pass
    if isinstance(end_date, str):
        try:
            end_date = date.fromisoformat(end_date)
        except Exception:
            pass
    if status == "completed":
        return "completed"
    if end_date is not None and end_date < today:
        return "completed"
    if status == "scheduled" and start_date is not None and start_date <= today:
        return "active"
    return status


def sync_statuses(today: date | None = None) -> dict:
    """Đồng bộ trạng thái chiến dịch theo ngày."""
    if today is None:
        today = date.today()

    activated: list[Any] = []
    completed: list[Any] = []

    rows = db._execute_query(
        "SELECT id, status, start_date, end_date FROM public.advertising_campaigns WHERE status <> 'completed'",
        fetchall=True,
        commit=False,
    )

    if not rows or not isinstance(rows, list):
        return {"activated": [], "completed": []}

    for row in rows:
        ns = next_status(row["status"], row["start_date"], row["end_date"], today)
        if ns != row["status"]:
            db._execute_query(
                "UPDATE public.advertising_campaigns SET status=%s, updated_at=NOW() WHERE id=%s",
                (ns, row["id"]),
                commit=True,
            )
            if ns == "active":
                activated.append(row["id"])
            elif ns == "completed":
                completed.append(row["id"])

    return {"activated": activated, "completed": completed}


def collect_actions(today: date) -> dict:
    """Thu thập các hành động cần làm trong ngày."""
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    cols = "id, title, target_province, daily_budget, start_date, end_date"
    queries = {
        "start_today": (
            f"SELECT {cols} FROM public.advertising_campaigns WHERE status='active' AND start_date = %s",
            (today,),
        ),
        "end_today": (
            f"SELECT {cols} FROM public.advertising_campaigns WHERE status='active' AND end_date = %s",
            (today,),
        ),
        "ended_yesterday": (
            f"SELECT {cols} FROM public.advertising_campaigns WHERE status='completed' AND end_date = %s",
            (yesterday,),
        ),
        "start_tomorrow": (
            f"SELECT {cols} FROM public.advertising_campaigns WHERE status='scheduled' AND start_date = %s",
            (tomorrow,),
        ),
    }

    result: dict[str, list] = {k: [] for k in queries}
    for key, (sql, params) in queries.items():
        rows = db._execute_query(sql, params, fetchall=True, commit=False)
        result[key] = rows if isinstance(rows, list) else []

    return result


def build_digest(actions: dict, admin_url: str = "", today: date | None = None) -> str | None:
    """Xây dựng tin nhắn tóm tắt Google Ads."""
    if all(len(actions.get(k, [])) == 0 for k in ("start_today", "end_today", "ended_yesterday", "start_tomorrow")):
        return None

    if today is None:
        today = date.today()
    header = f"📣 Google Ads {today.day:02d}/{today.month:02d}"

    group_meta = [
        ("start_today", "▶️ BẬT hôm nay trên Google Ads"),
        ("end_today", "⏰ Hôm nay là ngày cuối — tắt trước khi hết ngày"),
        ("ended_yesterday", "⏹ Đã hết hạn hôm qua — kiểm tra đã TẮT trên Google Ads"),
        ("start_tomorrow", "📅 Ngày mai bật"),
    ]

    def fmt_budget(budget) -> str:
        try:
            val = int(Decimal(str(budget)))
            return f"{val:,}".replace(",", ".")
        except Exception:
            return str(budget)

    def fmt_date(d) -> str:
        if d is None:
            return "?"
        if isinstance(d, str):
            try:
                d = date.fromisoformat(d)
            except Exception:
                return d
        if isinstance(d, (date, datetime)):
            return f"{d.day:02d}/{d.month:02d}"
        return str(d)

    parts = [header]
    for key, title in group_meta:
        rows = actions.get(key, [])
        if not rows:
            continue
        parts.append(f"\n{title}")
        display = rows[:10]
        for row in display:
            budget_fmt = fmt_budget(row.get("daily_budget", 0))
            sd = fmt_date(row.get("start_date"))
            ed = fmt_date(row.get("end_date"))
            province = row.get("target_province") or ""
            title_text = row.get("title") or ""
            parts.append(f"• {title_text} ({province}) · {budget_fmt} đ/ngày · {sd} → {ed}")
        extra = len(rows) - len(display)
        if extra > 0:
            parts.append(f"… và {extra} chiến dịch nữa")

    if admin_url:
        parts.append(f"\n{admin_url}")

    text = "\n".join(parts)
    if len(text) > 3500:
        text = text[:3498] + "…"

    return text


def admin_base_url() -> str:
    return admin_page_url("/admin/advertising")


def tick(now=None, notify=None, sync=None) -> str:
    """Kiểm tra và gửi digest Google Ads nếu cần; đồng bộ API nếu đã cấu hình."""
    if now is None:
        now = datetime.now()

    # Luôn đồng bộ trạng thái trước
    sync_statuses(now.date() if isinstance(now, datetime) else now)

    digest_res: str = "early"
    # Trước 07:30 thì bỏ qua digest
    if now.hour < 7 or (now.hour == 7 and now.minute < 30):
        digest_res = "early"
    else:
        # Kiểm tra đã gửi hôm nay chưa
        last = get_app_setting(ADS_DIGEST_KEY)
        if last == now.date().isoformat():
            digest_res = "done"
        else:
            actions = collect_actions(now.date())
            text = build_digest(actions, admin_base_url(), today=now.date())

            if text is None:
                set_app_setting(ADS_DIGEST_KEY, now.date().isoformat())
                digest_res = "nothing"
            else:
                from backend.services.google_ads_api import is_live, load_config
                if not is_live(load_config()):
                    text = text + "\n(chưa bật chế độ tự động — hãy bật/tắt thủ công)"

                if notify is None:
                    from backend.services.telegram_service import notify_telegram as notify  # type: ignore

                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "Mở Quảng cáo", "url": admin_base_url()}]
                    ]
                }

                result = notify(
                    text,
                    important=True,
                    prefix="",
                    reply_markup=reply_markup,
                )

                if result:
                    set_app_setting(ADS_DIGEST_KEY, now.date().isoformat())
                    digest_res = "sent"
                else:
                    digest_res = "failed"

    # Đồng bộ API sau phần nhắc bật/tắt nếu đã cấu hình và cách lần trước >= 60 phút
    from backend.services.google_ads_api import (
        ADS_SYNC_LAST_AT as API_SYNC_KEY,
        is_configured,
        is_live,
        load_config,
    )
    from backend.services.google_ads_api import sync_all as default_sync_all

    cfg = load_config()
    if is_configured(cfg):
        last_sync_str = get_app_setting(API_SYNC_KEY, "")
        should_sync = True
        if last_sync_str:
            now_dt = now if isinstance(now, datetime) else datetime.combine(now, datetime.min.time())
            if (as_aware(now_dt) - as_aware(datetime.fromisoformat(last_sync_str))).total_seconds() < 3600:
                should_sync = False

        if should_sync:
            sync_fn = sync if sync is not None else default_sync_all
            sync_res = sync_fn(today=now.date() if isinstance(now, datetime) else now)
            set_app_setting(API_SYNC_KEY, now.isoformat())

            if is_live(cfg) and sync_res and sync_res.get("ok"):
                for ch in sync_res.get("changes", []):
                    if not ch.get("dry_run", True):
                        action_vn = "BẬT" if ch.get("to") == "ENABLED" else "TẮT"
                        camp_title = ch.get("title", "")
                        msg = f"Đã {action_vn} chiến dịch {camp_title} trên Google Ads"
                        if notify is None:
                            from backend.services.telegram_service import notify_telegram as notify  # type: ignore
                        notify(msg, prefix="")

    return digest_res


def start_scheduler(interval_secs: int = 600) -> threading.Event:
    return start_periodic(lambda: tick(), name="google-ads-ops", interval_secs=interval_secs, error_wait_secs=1800, error_message="Google Ads ops lỗi", logger=logger)
