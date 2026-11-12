"""Báo cáo tổng hợp vận hành marketing hàng tuần qua Telegram."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable

from backend.services.database import db
from backend.services.site_url import admin_page_url
from backend.services.lead_followup import as_aware, parse_dt
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger(__name__)

WEEKLY_REPORT_LAST_DATE = "WEEKLY_REPORT_LAST_DATE"

LABELS: dict[str, str] = {
    "google_ads": "Google Ads",
    "facebook_ads": "Facebook Ads",
    "facebook_page": "Facebook (bài đăng)",
    "organic_search": "Tìm kiếm tự nhiên",
    "referral": "Trang khác giới thiệu",
    "direct": "Truy cập trực tiếp",
}


def fmt_money(val: Any) -> str:
    """Định dạng số tiền theo mẫu '150.000 đ'."""
    try:
        n = int(round(float(val or 0)))
    except (ValueError, TypeError):
        return "0 đ"
    return f"{n:,}".replace(",", ".") + " đ"


def fmt_date(d: Any) -> str:
    """Định dạng ngày theo mẫu dd/mm."""
    if not d:
        return ""
    if isinstance(d, (datetime, date)):
        return d.strftime("%d/%m")
    if isinstance(d, str):
        try:
            return date.fromisoformat(d).strftime("%d/%m")
        except Exception:
            return d
    return str(d)


def diff_symbol(cur: int, prev: int) -> str:
    """Ký hiệu so sánh tuần trước."""
    if cur > prev:
        return "▲"
    elif cur < prev:
        return "▼"
    return "＝"


def collect_inquiries_stats(now: datetime) -> dict:
    """(a) Thu thập số yêu cầu tư vấn 7 ngày qua và 7 ngày trước đó."""
    now_aware = as_aware(now)
    w1_start = now_aware - timedelta(days=7)
    w2_start = now_aware - timedelta(days=14)
    res = {
        "current_total": 0,
        "previous_total": 0,
        "current_by_source": {},
        "previous_by_source": {},
    }
    rows = db._execute_query(
        "SELECT lead_source, created_at FROM public.inquiries WHERE created_at >= %s",
        (w2_start,),
        fetchall=True,
        commit=False,
    )
    for r in rows or []:
        created = parse_dt(r.get("created_at"))
        if created is None:
            continue
        ca = as_aware(created)

        src = (r.get("lead_source") or "").strip() or "direct"
        if ca >= w1_start:
            res["current_total"] += 1
            res["current_by_source"][src] = res["current_by_source"].get(src, 0) + 1
        elif ca >= w2_start:
            res["previous_total"] += 1
            res["previous_by_source"][src] = res["previous_by_source"].get(src, 0) + 1

    return res


def collect_sla_and_unread(now: datetime) -> dict:
    """(b) Tỷ lệ xử lý trong 30 phút và số yêu cầu chưa đọc."""
    now_aware = as_aware(now)
    w1_start = now_aware - timedelta(days=7)
    cutoff_48h = now_aware - timedelta(hours=48)
    row = db._execute_query(
        "SELECT "
        "COUNT(*) FILTER (WHERE read_at IS NOT NULL) AS processed_count, "
        "COUNT(*) FILTER (WHERE read_at IS NOT NULL AND read_at - created_at <= INTERVAL '30 minutes') AS within_30m_count, "
        "COUNT(*) FILTER (WHERE is_read = FALSE) AS unread_count, "
        "COUNT(*) FILTER (WHERE is_read = FALSE AND created_at < %s) AS unread_over_48h "
        "FROM public.inquiries "
        "WHERE created_at >= %s OR is_read = FALSE",
        (cutoff_48h, w1_start),
        fetch=True,
        commit=False,
    )
    proc = row["processed_count"] or 0
    within = row["within_30m_count"] or 0
    return {
        "processed_count": proc,
        "within_30m_count": within,
        "sla_rate": (within / proc * 100) if proc > 0 else None,
        "unread_count": row["unread_count"] or 0,
        "unread_over_48h": row["unread_over_48h"] or 0,
    }


def collect_campaigns(now: datetime) -> list[dict]:
    """(c) Các chiến dịch quảng cáo có khoảng ngày giao với 7 ngày qua."""
    window_end = now.date() if isinstance(now, datetime) else now
    window_start = window_end - timedelta(days=7)
    rows = db._execute_query(
        "SELECT title, start_date, end_date, spent_amount, clicks, calls "
        "FROM public.advertising_campaigns "
        "WHERE start_date <= %s AND (end_date IS NULL OR end_date >= %s) "
        "ORDER BY id ASC",
        (window_end, window_start),
        fetchall=True,
        commit=False,
    )
    res = []
    for r in rows or []:
        spent = float(r.get("spent_amount") or 0)
        clicks = int(r.get("clicks") or 0)
        calls = int(r.get("calls") or 0)
        res.append({
            "title": r.get("title") or "Chiến dịch không tên",
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "spent_amount": spent,
            "clicks": clicks,
            "calls": calls,
            "cost_per_call": (spent / calls) if calls > 0 else None,
        })
    return res


def collect_facebook(now: datetime) -> dict:
    """(d) Số liệu bài đăng Facebook và sức khỏe Fanpage."""
    from backend.services.facebook_page import read_cached_health

    now_aware = as_aware(now)
    w1_start = now_aware - timedelta(days=7)
    row = db._execute_query(
        "SELECT "
        "COUNT(*) FILTER (WHERE status IN ('published', 'scheduled') AND publish_at >= %s AND publish_at <= %s) AS recent_posts, "
        "COUNT(*) FILTER (WHERE status = 'pending') AS pending_posts, "
        "COUNT(*) FILTER (WHERE status = 'failed') AS failed_posts "
        "FROM fb_post_drafts",
        (w1_start, now_aware),
        fetch=True,
        commit=False,
    )
    return {
        "recent_posts": row["recent_posts"] or 0,
        "pending_posts": row["pending_posts"] or 0,
        "failed_posts": row["failed_posts"] or 0,
        "health": read_cached_health(),
    }


def collect_health_issues() -> list[dict]:
    """(e) Tối đa 5 cảnh báo sẵn sàng marketing mức bad."""
    from backend.services.marketing_readiness import collect_checks

    return [c for c in collect_checks() if c.get("level") == "bad"][:5]


def collect(now: datetime | None = None) -> dict:
    """Tổng hợp dữ liệu báo cáo tuần từ tất cả các nguồn."""
    if now is None:
        now = datetime.now()
    return {
        "now": now,
        "inquiries": collect_inquiries_stats(now),
        "sla": collect_sla_and_unread(now),
        "campaigns": collect_campaigns(now),
        "facebook": collect_facebook(now),
        "health_issues": collect_health_issues(),
    }


def admin_url() -> str:
    return admin_page_url("/admin/advertising")


def build_report(data: dict, admin_url: str | None = None) -> str:
    """Dựng tin nhắn báo cáo tuần bằng tiếng Việt dưới 3500 ký tự."""
    parts: list[str] = ["📊 BÁO CÁO MARKETING TUẦN"]

    # 1. Yêu cầu tư vấn (Leads)
    inq = data.get("inquiries") or {}
    cur_total = inq.get("current_total", 0)
    prev_total = inq.get("previous_total", 0)
    sym = diff_symbol(cur_total, prev_total)
    parts.append("\n1. Yêu cầu tư vấn (7 ngày qua):")
    parts.append(f"• Tổng: {cur_total} ({sym} so với tuần trước: {prev_total})")

    by_src = inq.get("current_by_source") or {}
    if by_src:
        src_items = []
        for k, v in by_src.items():
            lbl = LABELS.get(k, k)
            src_items.append(f"{lbl}: {v}")
        parts.append(f"• Theo kênh: {', '.join(src_items)}")
    else:
        parts.append("• Theo kênh: chưa có")

    # 2. SLA & Backlog
    sla_data = data.get("sla") or {}
    rate = sla_data.get("sla_rate")
    rate_str = f"{rate:.1f}%" if rate is not None else "chưa có"
    unread = sla_data.get("unread_count", 0)
    unread_48h = sla_data.get("unread_over_48h", 0)
    parts.append("\n2. Phản hồi & Tồn đọng:")
    parts.append(f"• Xử lý trong 30 phút: {rate_str}")
    parts.append(f"• Chưa xử lý: {unread} (trong đó {unread_48h} quá 48 giờ)")

    # 3. Chiến dịch quảng cáo (số liệu cộng dồn cả chiến dịch)
    camps = data.get("campaigns") or []
    parts.append("\n3. Quảng cáo (số liệu cộng dồn cả chiến dịch):")
    if not camps:
        parts.append("• Chưa có")
    else:
        for c in camps:
            title = c.get("title") or "Chiến dịch"
            spent = fmt_money(c.get("spent_amount"))
            clicks = c.get("clicks", 0)
            calls = c.get("calls", 0)
            cpc = c.get("cost_per_call")
            cpc_str = f"{fmt_money(cpc)}/cuộc" if cpc is not None else "chưa có cuộc gọi"
            sd = fmt_date(c.get("start_date"))
            ed = fmt_date(c.get("end_date"))
            date_range = f" ({sd} → {ed})" if sd or ed else ""
            parts.append(f"• {title}{date_range}: {spent} · {clicks} click · {calls} cuộc · {cpc_str}")

    # 4. Facebook Fanpage
    fb = data.get("facebook") or {}
    recent = fb.get("recent_posts", 0)
    pending = fb.get("pending_posts", 0)
    failed = fb.get("failed_posts", 0)
    health = fb.get("health")
    if health is None:
        health_str = "chưa kiểm tra"
    elif isinstance(health, dict):
        health_str = health.get("message") or health.get("level") or "chưa kiểm tra"
    else:
        health_str = str(health)

    parts.append("\n4. Facebook Fanpage:")
    parts.append(f"• Bài đăng 7 ngày qua: {recent} (đã đăng/lên lịch)")
    parts.append(f"• Bản nháp: {pending} chờ duyệt · {failed} gửi lỗi")
    parts.append(f"• Sức khỏe Fanpage: {health_str}")

    # 5. Cảnh báo hệ thống
    issues = data.get("health_issues") or []
    parts.append("\n5. Cảnh báo hệ thống:")
    if not issues:
        parts.append("• Chưa có")
    else:
        for issue in issues:
            lbl = issue.get("label") or "Cảnh báo"
            det = issue.get("detail") or ""
            parts.append(f"• {lbl}: {det}")

    if admin_url:
        parts.append(f"\n{admin_url}")

    text = "\n".join(parts)
    if len(text) > 3500:
        text = text[:3498] + "…"

    return text


def tick(now: datetime | None = None, notify: Callable[..., bool] | None = None) -> str:
    """Chu kỳ gửi báo cáo tuần: thứ Hai lúc 08:00."""
    if now is None:
        now = datetime.now()

    # Chưa phải thứ Hai (0) hoặc trước 08:00
    if now.weekday() != 0 or now.hour < 8:
        return "early"

    today_str = now.date().isoformat()
    if get_app_setting(WEEKLY_REPORT_LAST_DATE) == today_str:
        return "done"

    text = build_report(collect(now), admin_url())
    if notify is None:
        from backend.services.telegram_service import notify_telegram as notify
    if not notify(text, important=True, prefix=""):
        return "failed"
    set_app_setting(WEEKLY_REPORT_LAST_DATE, today_str)
    return "sent"
