"""Quản lý khách tiềm năng: lưu thông tin từng khách, theo dõi trạng thái và nhắc hẹn gọi lại."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable

from backend.services.database import db
from backend.services.facebook_inbox import find_phone
from backend.services.settings_service import get_app_setting, set_app_setting

logger = logging.getLogger(__name__)

LAST_DATE_KEY = "PROSPECTS_LAST_DATE"
SEND_AFTER_HOUR = 8

SEGMENTS = {
    "contractor": "Nhà thầu xây dựng",
    "factory": "Nhà máy, xưởng",
    "scale": "Cân điện tử, kiểm định",
    "electrical": "Điện, PCCC, điều hòa",
    "other": "Khác",
}

STATUSES = {
    "new": "Mới",
    "contacted": "Đã liên hệ",
    "replied": "Đã phản hồi",
    "quoted": "Đã báo giá",
    "won": "Thành đơn",
    "lost": "Mất",
}

SOURCES = {"manual", "import", "radar", "project_news"}


def normalize_phone(text: str) -> str:
    """Chuẩn hóa số điện thoại Việt Nam 10 chữ số, trả '' nếu không hợp lệ."""
    return find_phone(text or "")


def clean(data: dict) -> dict:
    """Làm sạch và hợp lệ hóa dữ liệu khách tiềm năng."""
    name = str(data.get("name") or "").strip()[:200]
    company = str(data.get("company") or "").strip()[:200]
    phone = normalize_phone(str(data.get("phone") or ""))
    zalo = str(data.get("zalo") or "").strip()[:50]

    segment = str(data.get("segment") or "").strip()
    if segment not in SEGMENTS:
        segment = "other"

    province = str(data.get("province") or "").strip()[:100]

    source = str(data.get("source") or "").strip()
    if source not in SOURCES:
        source = "manual"

    status = str(data.get("status") or "").strip()
    if status not in STATUSES:
        status = "new"

    raw_next = data.get("next_contact")
    next_contact: date | None = None
    if isinstance(raw_next, datetime):
        next_contact = raw_next.date()
    elif isinstance(raw_next, date):
        next_contact = raw_next
    elif raw_next and str(raw_next).strip():
        try:
            next_contact = datetime.strptime(str(raw_next).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            next_contact = None

    note = str(data.get("note") or "").strip()[:2000]

    return {
        "name": name,
        "company": company,
        "phone": phone,
        "zalo": zalo,
        "segment": segment,
        "province": province,
        "source": source,
        "status": status,
        "next_contact": next_contact,
        "note": note,
    }


def list_prospects(status: str = "", q: str = "", limit: int = 20, offset: int = 0) -> list[dict]:
    """Danh sách khách tiềm năng theo bộ lọc trạng thái và tìm kiếm."""
    conditions: list[str] = []
    params: list[Any] = []

    if status and status in STATUSES:
        conditions.append("status = %s")
        params.append(status)

    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        conditions.append("(LOWER(name) LIKE %s OR LOWER(company) LIKE %s OR LOWER(phone) LIKE %s OR LOWER(note) LIKE %s)")
        params.extend([needle, needle, needle, needle])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT id, name, company, phone, zalo, segment, province, source, status,
               next_contact, note, last_contact_at, created_at, updated_at
        FROM public.prospects
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
    """
    params.extend([max(1, int(limit)), max(0, int(offset))])
    rows = db.query(sql, tuple(params), fetchall=True)
    return rows or []


def count_prospects(status: str = "", q: str = "") -> int:
    """Đếm tổng số khách tiềm năng theo bộ lọc."""
    conditions: list[str] = []
    params: list[Any] = []

    if status and status in STATUSES:
        conditions.append("status = %s")
        params.append(status)

    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        conditions.append("(LOWER(name) LIKE %s OR LOWER(company) LIKE %s OR LOWER(phone) LIKE %s OR LOWER(note) LIKE %s)")
        params.extend([needle, needle, needle, needle])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    row = db.query(f"SELECT COUNT(*) AS cnt FROM public.prospects {where}", tuple(params) if params else None, fetch=True)
    return int(row["cnt"]) if row and "cnt" in row else 0


def status_counts() -> dict[str, int]:
    """Số lượng khách theo từng trạng thái."""
    counts = {k: 0 for k in STATUSES}
    rows = db.query("SELECT status, COUNT(*) AS cnt FROM public.prospects GROUP BY status", fetchall=True) or []
    for r in rows:
        st = r.get("status")
        if st in counts:
            counts[st] = int(r.get("cnt", 0))
    return counts


def get(id: int) -> dict | None:
    """Lấy thông tin một khách tiềm năng."""
    return db.query("SELECT * FROM public.prospects WHERE id = %s", (id,), fetch=True)


def phone_taken(phone: str, exclude_id: int | None = None) -> bool:
    """True khi một khách khác đã dùng số điện thoại này."""
    phone = normalize_phone(phone)
    if not phone:
        return False
    if exclude_id is None:
        row = db.query("SELECT id FROM public.prospects WHERE phone = %s", (phone,), fetch=True)
    else:
        row = db.query("SELECT id FROM public.prospects WHERE phone = %s AND id <> %s", (phone, exclude_id), fetch=True)
    return bool(row)


def create(data: dict) -> int | None:
    """Thêm khách tiềm năng mới. Trả id hoặc None khi thiếu tên / trùng SĐT."""
    cleaned = clean(data)
    if not cleaned["name"]:
        return None

    row = db.query("""
        INSERT INTO public.prospects (
            name, company, phone, zalo, segment, province, source, status, next_contact, note
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT DO NOTHING
        RETURNING id;
    """, (
        cleaned["name"], cleaned["company"], cleaned["phone"], cleaned["zalo"],
        cleaned["segment"], cleaned["province"], cleaned["source"], cleaned["status"],
        cleaned["next_contact"], cleaned["note"]
    ), fetch=True)
    if not row or not row.get("id"):
        return None
    return int(row["id"])


def update(id: int, data: dict) -> bool:
    """Cập nhật khách tiềm năng. Trả False khi thiếu tên hoặc trùng SĐT đã có."""
    cleaned = clean(data)
    if not cleaned["name"]:
        return False
    if phone_taken(cleaned["phone"], id):
        return False

    next_contact = None if cleaned["status"] in ("won", "lost") else cleaned["next_contact"]

    ok = db.query("""
            UPDATE public.prospects
            SET name = %s, company = %s, phone = %s, zalo = %s, segment = %s,
                province = %s, source = %s, status = %s, next_contact = %s,
                note = %s, updated_at = NOW()
            WHERE id = %s
        """, (
        cleaned["name"], cleaned["company"], cleaned["phone"], cleaned["zalo"],
        cleaned["segment"], cleaned["province"], cleaned["source"], cleaned["status"],
        next_contact, cleaned["note"], id
    ))
    return bool(ok)


def delete(id: int) -> bool:
    """Xóa một khách tiềm năng."""
    ok = db.query("DELETE FROM public.prospects WHERE id = %s", (id,))
    return bool(ok)


def set_status(id: int, status: str, next_contact: Any = None) -> bool:
    """Cập nhật trạng thái và ngày hẹn gọi lại. last_contact_at = NOW() khi khác 'new'."""
    if status not in STATUSES:
        return False

    if status in ("won", "lost"):
        parsed_next: date | None = None
    elif next_contact:
        if isinstance(next_contact, datetime):
            parsed_next = next_contact.date()
        elif isinstance(next_contact, date):
            parsed_next = next_contact
        else:
            try:
                parsed_next = datetime.strptime(str(next_contact).strip()[:10], "%Y-%m-%d").date()
            except ValueError:
                parsed_next = None
    else:
        parsed_next = None

    if status != "new":
        ok = db.query("""
            UPDATE public.prospects
            SET status = %s, next_contact = %s, last_contact_at = NOW(), updated_at = NOW()
            WHERE id = %s
        """, (status, parsed_next, id))
    else:
        ok = db.query("""
            UPDATE public.prospects
            SET status = %s, next_contact = %s, updated_at = NOW()
            WHERE id = %s
        """, (status, parsed_next, id))
    return bool(ok)


def import_text(text: str, segment: str = "other", province: str = "", source: str = "import") -> dict:
    """Nhập danh sách từ văn bản dạng bảng (tab, ';' hoặc ','). Tối đa 500 dòng."""
    res = {"added": 0, "duplicate": 0, "invalid": 0}
    if not text or not text.strip():
        return res

    raw_lines = [line.strip() for line in text.strip().splitlines()]
    lines = [line for line in raw_lines if line][:500]

    seen_phones: set[str] = set()

    for line in lines:
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        elif ";" in line:
            parts = [p.strip() for p in line.split(";")]
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
        else:
            parts = [line.strip()]

        name = parts[0] if len(parts) > 0 else ""
        if not name:
            res["invalid"] += 1
            continue

        company = parts[1] if len(parts) > 1 else ""
        raw_phone = parts[2] if len(parts) > 2 else ""
        note = parts[3] if len(parts) > 3 else ""
        if len(parts) > 4:
            note = " ".join(parts[3:])

        phone = normalize_phone(raw_phone)
        if phone:
            if phone in seen_phones:
                res["duplicate"] += 1
                continue
            seen_phones.add(phone)

        prospect_id = create({
            "name": name,
            "company": company,
            "phone": phone,
            "note": note,
            "segment": segment,
            "province": province,
            "source": source,
        })
        if prospect_id is None:
            res["duplicate"] += 1
        else:
            res["added"] += 1

    return res


def due(today: date, limit: int = 15) -> tuple[list[dict], int]:
    """Khách đến hạn gọi lại (next_contact <= today hoặc status = 'new' chưa có ngày hẹn)."""
    rows = db.query("""
        SELECT * FROM public.prospects
        WHERE status IN ('new', 'contacted', 'replied', 'quoted')
          AND (next_contact <= %s OR (status = 'new' AND next_contact IS NULL))
        ORDER BY next_contact ASC NULLS FIRST, created_at ASC
        LIMIT %s
    """, (today, limit), fetchall=True) or []

    cnt_row = db.query("""
        SELECT COUNT(*) AS cnt FROM public.prospects
        WHERE status IN ('new', 'contacted', 'replied', 'quoted')
          AND (next_contact <= %s OR (status = 'new' AND next_contact IS NULL))
    """, (today,), fetch=True)
    total = int(cnt_row["cnt"]) if cnt_row and "cnt" in cnt_row else 0
    return rows, total


def _short(text: str, limit: int = 40) -> str:
    s = (text or "").strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def build_message(rows: list[dict], total: int) -> str:
    """Tạo tin nhắn nhắc khách cần gọi hôm nay gửi qua Telegram."""
    lines = [f"🎯 Khách tiềm năng cần liên hệ hôm nay ({total})"]
    for i, r in enumerate(rows, 1):
        name = r.get("name", "")
        company = r.get("company", "")
        who = f"{name} ({company})" if company else name
        status_lbl = STATUSES.get(r.get("status", ""), r.get("status", ""))
        parts = [who]
        if r.get("phone"):
            parts.append(r["phone"])
        if status_lbl:
            parts.append(status_lbl)
        note = _short(r.get("note", ""), 40)
        if note:
            parts.append(note)
        lines.append(f"{i}. " + " — ".join(parts))
    if total > len(rows):
        lines.append(f"… và {total - len(rows)} khách nữa")
    return "\n".join(lines)


def tick(now: datetime | None = None, notify: Callable[..., bool] | None = None) -> str:
    """Tác vụ định kỳ hằng ngày sau 08:00 nhắc danh sách khách cần liên hệ.
    Trả: early / done / empty / sent / failed.
    """
    now = now or datetime.now()
    if now.hour < SEND_AFTER_HOUR:
        return "early"
    today = now.date().isoformat()
    if get_app_setting(LAST_DATE_KEY) == today:
        return "done"
    rows, total = due(now.date(), limit=15)
    if total == 0:
        set_app_setting(LAST_DATE_KEY, today)
        return "empty"
    if notify is None:
        from backend.services.telegram_service import notify_telegram as notify
    if not notify(build_message(rows, total), important=True, prefix=""):
        return "failed"
    set_app_setting(LAST_DATE_KEY, today)
    return "sent"
