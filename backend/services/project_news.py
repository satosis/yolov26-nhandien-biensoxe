"""Quản lý tin dự án: thu thập tin công trình, nhà máy mới khởi công từ Google News và gửi Telegram."""
from __future__ import annotations

import email.utils
import hashlib
import html
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from backend.services import prospects
from backend.services.database import db
from backend.services.lead_followup import as_aware
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger(__name__)

QUERIES_KEY = "PROJECT_NEWS_QUERIES"
LAST_DATE_KEY = "PROJECT_NEWS_LAST_DATE"
LAST_TRY_KEY = "PROJECT_NEWS_LAST_TRY"

DEFAULT_QUERIES = [
    "khởi công nhà máy Phú Thọ",
    "khởi công nhà máy Vĩnh Phúc",
    "khởi công nhà máy Bắc Ninh",
    "khởi công nhà máy Hà Nội",
    "khởi công nhà máy Thái Nguyên",
    "động thổ cụm công nghiệp Phú Thọ",
]

# Chỉ giữ tin có tên một địa phương mà doanh nghiệp phục vụ, trong truy vấn hoặc trong tiêu đề.
REGIONS = (
    "phú thọ", "vĩnh phúc", "hà nội", "bắc ninh", "bắc giang", "thái nguyên",
    "hưng yên", "hải dương", "hải phòng", "việt trì", "yên lạc", "vĩnh yên",
)

KEEP = (
    "khởi công",
    "động thổ",
    "nhà máy",
    "dự án",
    "khu công nghiệp",
    "xây dựng",
    "lắp đặt",
    "cụm công nghiệp",
)

DROP = (
    "tai nạn",
    "khởi tố",
    "bắt giữ",
    "tử vong",
    "tin giả",
    "lừa đảo",
)

STATUSES = {"new", "saved", "skipped"}
SEND_AFTER = (7, 45)
MAX_QUERIES = 12
MAX_QUERY_LEN = 100


def _parse_queries(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        cleaned = line[:MAX_QUERY_LEN].strip()
        key = cleaned.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(cleaned)
        if len(out) >= MAX_QUERIES:
            break
    return out


def get_queries() -> list[str]:
    """Danh sách truy vấn tìm kiếm (mặc định hoặc từ cài đặt)."""
    raw = get_app_setting(QUERIES_KEY)
    if raw:
        parsed = _parse_queries(raw)
        if parsed:
            return parsed
    return list(DEFAULT_QUERIES)


def save_queries(text: str) -> None:
    """Lưu danh sách truy vấn tìm kiếm."""
    lines = _parse_queries(text)
    set_app_setting(QUERIES_KEY, "\n".join(lines))


def parse_feed(xml_text: str, query: str = "") -> list[dict]:
    """Parse RSS feed từ Google News XML."""
    if not xml_text or not isinstance(xml_text, str):
        return []
    upper_xml = xml_text.upper()
    if "<!DOCTYPE" in upper_xml or "<!ENTITY" in upper_xml:
        logger.warning("parse_feed: XML chứa DOCTYPE hoặc ENTITY bị từ chối")
        return []
    root = ET.fromstring(xml_text)

    items: list[dict] = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        source_el = item.find("source")
        pub_el = item.find("pubDate")

        raw_title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        source = (source_el.text or "").strip() if source_el is not None else ""

        title = raw_title
        if source:
            suffix = f" - {source}"
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
            elif title.lower().endswith(suffix.lower()):
                title = title[:-len(suffix)].strip()

        published_at: datetime | None = None
        if pub_el is not None and pub_el.text:
            try:
                dt = email.utils.parsedate_to_datetime(pub_el.text.strip())
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    published_at = dt
            except Exception:
                published_at = None

        if title and link:
            items.append({
                "title": title,
                "link": link,
                "source": source,
                "published_at": published_at,
                "query": query,
            })
    return items


def is_relevant(title: str) -> bool:
    """Kiểm tra tiêu đề có liên quan công trình, nhà máy và không chứa từ tiêu cực."""
    if not title:
        return False
    t = title.lower()
    if any(drop in t for drop in DROP):
        return False
    return any(keep in t for keep in KEEP)


def in_region(query: str, title: str) -> bool:
    """Truy vấn hoặc tiêu đề nhắc tới một địa phương trong vùng phục vụ."""
    text = f"{query} {title}".lower()
    return any(region in text for region in REGIONS)


def fetch(query: str, session: Any = None) -> str:
    """Tải RSS feed từ Google News theo truy vấn."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=vi&gl=VN&ceid=VN:vi"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DaiLoXanhBot/1.0)"}
    if session is not None:
        resp = session.get(url, timeout=15, headers=headers)
    else:
        resp = requests.get(url, timeout=15, headers=headers)
    resp.raise_for_status()
    return resp.text


def refresh(now: datetime | None = None, max_age_days: int = 3, fetcher: Callable[[str], str] | None = None) -> dict:
    """Lấy tin mới cho mọi truy vấn, lọc, khử trùng và lưu vào DB."""
    now_dt = datetime.now(timezone.utc) if now is None else as_aware(now).astimezone(timezone.utc)

    cutoff = now_dt - timedelta(days=max_age_days)
    queries = get_queries()
    seen_hashes: set[str] = set()
    seen_titles: set[str] = set()
    new_rows: list[dict] = []
    errors = 0


    for idx, q in enumerate(queries):
        if idx > 0 and fetcher is None:
            time.sleep(1)

        if fetcher is not None:
            xml_text = fetcher(q)
        else:
            xml_text = fetch(q)
        items = parse_feed(xml_text, query=q)

        for item in items:
            if not is_relevant(item["title"]) or not in_region(q, item["title"]):
                continue
            pub = item.get("published_at")
            if pub is None or pub < cutoff:
                continue

            link = item["link"]
            link_hash = hashlib.sha1(link.encode("utf-8")).hexdigest()
            norm_title = " ".join(item["title"].lower().split())

            if link_hash in seen_hashes or norm_title in seen_titles:
                continue

            seen_hashes.add(link_hash)
            seen_titles.add(norm_title)

            row = db.query("""
                INSERT INTO public.project_news (
                    link_hash, title, link, source, published_at, query, status
                ) VALUES (%s, %s, %s, %s, %s, %s, 'new')
                ON CONFLICT (link_hash) DO NOTHING
                RETURNING id, link_hash, title, link, source, published_at, query, status, created_at;
            """, (link_hash, item["title"], link, item.get("source", ""), pub, q), fetch=True)

            if row and row.get("id"):
                item_record = {
                    "id": row["id"],
                    "link_hash": link_hash,
                    "title": item["title"],
                    "link": link,
                    "source": item.get("source", ""),
                    "published_at": pub,
                    "query": q,
                    "status": "new",
                }
                if isinstance(row, dict):
                    item_record.update(row)
                new_rows.append(item_record)

    return {"new": new_rows, "errors": errors}


def list_news(status: str = "", limit: int = 20, offset: int = 0) -> list[dict]:
    """Danh sách tin dự án theo trạng thái, mới nhất trước."""
    params: list[Any] = []
    where_sql = ""
    if status and status in STATUSES:
        where_sql = "WHERE status = %s"
        params.append(status)
    sql = f"""
        SELECT id, link_hash, title, link, source, published_at, query, status, created_at
        FROM public.project_news
        {where_sql}
        ORDER BY COALESCE(published_at, created_at) DESC, id DESC
        LIMIT %s OFFSET %s
    """
    params.extend([max(1, int(limit)), max(0, int(offset))])
    rows = db.query(sql, tuple(params), fetchall=True)
    return rows or []


def count_news(status: str = "") -> int:
    """Tổng số tin dự án theo trạng thái."""
    params: list[Any] = []
    where_sql = ""
    if status and status in STATUSES:
        where_sql = "WHERE status = %s"
        params.append(status)
    sql = f"SELECT COUNT(*) AS cnt FROM public.project_news {where_sql}"
    row = db.query(sql, tuple(params) if params else None, fetch=True)
    return int(row["cnt"]) if row and "cnt" in row else 0


def status_counts() -> dict[str, int]:
    """Số lượng tin theo từng trạng thái."""
    counts = {k: 0 for k in STATUSES}
    rows = db.query("SELECT status, COUNT(*) AS cnt FROM public.project_news GROUP BY status", fetchall=True) or []
    for r in rows:
        st = r.get("status")
        if st in counts:
            counts[st] = int(r.get("cnt", 0))
    return counts


def set_status(id: int, status: str) -> bool:
    """Cập nhật trạng thái một tin dự án."""
    if status not in STATUSES:
        return False
    db.query("UPDATE public.project_news SET status = %s WHERE id = %s", (status, id))
    return True


def save_as_prospect(id: int) -> int | None:
    """Chuyển tin dự án thành khách tiềm năng."""
    row = db.query("SELECT id, title, link, source FROM public.project_news WHERE id = %s", (id,), fetch=True)
    if not row:
        return None

    title = str(row.get("title") or "").strip()
    name = title[:100].strip()
    link = str(row.get("link") or "").strip()
    source = str(row.get("source") or "").strip()
    note = f"{link} | {source}".strip(" |")

    prospect_id = prospects.create({
        "name": name,
        "note": note,
        "segment": "contractor",
        "source": "project_news",
    })
    if prospect_id:
        set_status(id, "saved")
        return prospect_id
    return None


def build_digest(rows: list[dict], admin_link: str) -> str:
    """Tạo thông báo HTML gửi Telegram điểm tin dự án mới."""
    lines = [f"📰 <b>Tin dự án mới ({len(rows)})</b>"]
    for r in rows[:10]:
        link_escaped = html.escape(str(r.get("link") or ""), quote=True)
        title_escaped = html.escape(str(r.get("title") or ""))
        source_escaped = html.escape(str(r.get("source") or ""))
        pub = r.get("published_at")
        date_str = ""
        if isinstance(pub, datetime):
            date_str = pub.strftime("%d/%m")
        elif pub:
            try:
                date_str = str(pub)[:10]
            except Exception:
                date_str = ""

        meta_parts: list[str] = []
        if source_escaped:
            meta_parts.append(source_escaped)
        if date_str:
            meta_parts.append(date_str)
        meta = ", ".join(meta_parts)

        line = f'<a href="{link_escaped}">{title_escaped}</a>'
        if meta:
            line += f" — {meta}"
        lines.append(line)

    admin_escaped = html.escape(str(admin_link or ""), quote=True)
    lines.append(f'<a href="{admin_escaped}">Xem tất cả</a>')
    return "\n".join(lines)


def _discard(ids: list[int]) -> None:
    """Xóa các tin vừa lưu mà chưa báo được, để lần thử sau gặp lại chúng như tin mới."""
    if not ids:
        return
    db.query("DELETE FROM public.project_news WHERE id = ANY(%s) AND status = 'new'", (list(ids),))


def _admin_link() -> str:
    try:
        from backend.services import facebook_autopost
        base = (facebook_autopost._site_url() or "http://127.0.0.1:8001").replace("://localhost", "://127.0.0.1")
    except Exception:
        base = "http://127.0.0.1:8001"
    return f"{base}/admin/advertising/project-news"


def tick(now: datetime | None = None, notify: Callable[..., bool] | None = None, fetcher: Callable[[str], str] | None = None) -> str:
    """Thu thập tin dự án một lần mỗi ngày sau 07:45. Trả early / done / empty / sent / failed."""
    now = now or datetime.now()
    if (now.hour, now.minute) < SEND_AFTER:
        return "early"

    today = now.date().isoformat()
    if get_app_setting(LAST_DATE_KEY) == today:
        return "done"

    last_try_iso = get_app_setting(LAST_TRY_KEY)
    if last_try_iso:
        diff = (as_aware(now) - as_aware(datetime.fromisoformat(last_try_iso))).total_seconds()
        if 0 <= diff < 3600:
            return "failed"

    queries = get_queries()
    res = refresh(now=now, fetcher=fetcher)
    if queries and res.get("errors", 0) >= len(queries):
        set_app_setting(LAST_TRY_KEY, now.isoformat())
        return "failed"

    new_rows = res.get("new", [])
    if not new_rows:
        set_app_setting(LAST_DATE_KEY, today)
        return "empty"

    ok = False
    try:
        if notify is None:
            from backend.services.telegram_service import notify_telegram as notify
        ok = notify(build_digest(new_rows, _admin_link()), important=False, prefix="", parse_mode="HTML")
    finally:
        if not ok:
            # Tin đã nằm trong DB nên lần sau sẽ không còn là tin mới: xóa để lần thử sau tìm và gửi lại.
            _discard([r["id"] for r in new_rows if r.get("id")])
            set_app_setting(LAST_TRY_KEY, now.isoformat())
    if not ok:
        return "failed"

    set_app_setting(LAST_DATE_KEY, today)
    return "sent"
