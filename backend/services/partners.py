"""Quản lý đối tác giới thiệu và mã theo dõi khách hàng."""
from __future__ import annotations

import logging
import re
import secrets
import unicodedata
import urllib.parse
from typing import Any

from backend.services.database import db

logger = logging.getLogger(__name__)

KINDS: dict[str, str] = {
    "steel": "Cửa hàng thép, vật liệu",
    "machinery": "Đại lý máy móc, thiết bị",
    "electrical": "Nhà thầu điện, PCCC",
    "garage": "Garage, cứu hộ",
    "contractor": "Nhà thầu xây dựng",
    "other": "Khác",
}

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")


def make_code(name: str) -> str:
    """Tạo mã đối tác tự động từ tên: bỏ dấu tiếng Việt, chữ hoa, max 10 ký tự, thêm 2 số ngẫu nhiên cuối."""
    raw = str(name or "")
    raw = raw.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", raw)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    cleaned = re.sub(r"[^A-Za-z0-9]", "", stripped).upper()
    base = cleaned[:10] if cleaned else "DT"
    suffix = f"{secrets.randbelow(100):02d}"
    return f"{base}{suffix}"


def valid_code(code: str) -> bool:
    """Kiểm tra mã hợp lệ: 3-20 ký tự A-Za-z0-9_-."""
    if not code or not isinstance(code, str):
        return False
    return bool(_CODE_RE.match(code.strip()))


def get_by_code(code: str) -> dict[str, Any] | None:
    """Tra cứu đối tác theo mã (không phân biệt hoa thường)."""
    if not code or not isinstance(code, str):
        return None
    sql = "SELECT * FROM public.partners WHERE LOWER(code) = LOWER(%s)"
    return db.query(sql, (code.strip(),), fetch=True)


def get(partner_id: int) -> dict[str, Any] | None:
    """Lấy thông tin đối tác theo ID."""
    sql = "SELECT * FROM public.partners WHERE id = %s"
    return db.query(sql, (partner_id,), fetch=True)


def create(data: dict[str, Any]) -> int | None:
    """Tạo mới đối tác; mã trống tự sinh, thử lại tối đa 5 lần khi trùng mã; trả None khi trùng hoặc thiếu tên."""
    name = str(data.get("name") or "").strip()
    if not name:
        return None

    code = str(data.get("code") or "").strip()
    if code:
        if not valid_code(code):
            return None
        if get_by_code(code):
            return None
    else:
        generated = None
        for _ in range(5):
            candidate = make_code(name)
            if not get_by_code(candidate):
                generated = candidate
                break
        if not generated:
            return None
        code = generated

    phone = str(data.get("phone") or "").strip()
    kind = str(data.get("kind") or "other").strip()
    if kind not in KINDS:
        kind = "other"
    try:
        commission_percent = float(data.get("commission_percent", 3.0))
    except (ValueError, TypeError):
        commission_percent = 3.0

    raw_active = data.get("active", True)
    if isinstance(raw_active, str):
        active = raw_active.lower() in ("1", "true", "on", "yes")
    else:
        active = bool(raw_active)

    note = str(data.get("note") or "").strip()

    sql = """
        INSERT INTO public.partners (name, phone, kind, code, commission_percent, active, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    row_id = db.query(
        sql,
        (name, phone, kind, code, commission_percent, active, note),
        return_id=True,
        commit=True,
    )
    return int(row_id) if row_id else None


def update(partner_id: int, data: dict[str, Any]) -> bool:
    """Cập nhật thông tin đối tác."""
    current = get(partner_id)
    if not current:
        return False

    name = str(data.get("name") or "").strip()
    if not name:
        return False

    code = str(data.get("code") or "").strip()
    if code:
        if not valid_code(code):
            return False
        existing = get_by_code(code)
        if existing and existing.get("id") != partner_id:
            return False
    else:
        code = current.get("code", "")

    phone = str(data.get("phone") or "").strip()
    kind = str(data.get("kind") or "other").strip()
    if kind not in KINDS:
        kind = "other"
    try:
        commission_percent = float(data.get("commission_percent", 3.0))
    except (ValueError, TypeError):
        commission_percent = 3.0

    raw_active = data.get("active", True)
    if isinstance(raw_active, str):
        active = raw_active.lower() in ("1", "true", "on", "yes")
    else:
        active = bool(raw_active)

    note = str(data.get("note") or "").strip()

    sql = """
        UPDATE public.partners
        SET name = %s, phone = %s, kind = %s, code = %s, commission_percent = %s, active = %s, note = %s
        WHERE id = %s
    """
    db.query(
        sql,
        (name, phone, kind, code, commission_percent, active, note, partner_id),
        commit=True,
    )
    return True


def delete(partner_id: int) -> bool:
    """Xóa đối tác theo ID."""
    sql = "DELETE FROM public.partners WHERE id = %s"
    res = db.query(sql, (partner_id,), commit=True)
    return bool(res)


def list_partners(q: str = "", limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """Lấy danh sách đối tác theo trang và từ khóa tìm kiếm."""
    q_str = (q or "").strip()
    if q_str:
        pattern = f"%{q_str}%"
        sql = """
            SELECT * FROM public.partners
            WHERE name ILIKE %s OR phone ILIKE %s OR code ILIKE %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """
        params = (pattern, pattern, pattern, limit, offset)
    else:
        sql = """
            SELECT * FROM public.partners
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """
        params = (limit, offset)
    rows = db.query(sql, params, fetchall=True)
    return rows if rows is not None else []


def count_partners(q: str = "") -> int:
    """Đếm tổng số đối tác."""
    q_str = (q or "").strip()
    if q_str:
        pattern = f"%{q_str}%"
        sql = """
            SELECT COUNT(*) AS cnt FROM public.partners
            WHERE name ILIKE %s OR phone ILIKE %s OR code ILIKE %s
        """
        params = (pattern, pattern, pattern)
    else:
        sql = "SELECT COUNT(*) AS cnt FROM public.partners"
        params = None
    row = db.query(sql, params, fetch=True)
    return int(row["cnt"]) if row and "cnt" in row else 0


def stats() -> dict[str, dict[str, int]]:
    """Thống kê yêu cầu tư vấn theo mã đối tác: tổng và 30 ngày gần nhất (một truy vấn duy nhất)."""
    sql = """
        SELECT
            LOWER(utm_campaign) AS code,
            COUNT(*)::int AS total,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')::int AS last30
        FROM public.inquiries
        WHERE utm_source = 'partner'
        GROUP BY LOWER(utm_campaign)
    """
    rows = db.query(sql, fetchall=True) or []
    res: dict[str, dict[str, int]] = {}
    for r in rows:
        c = (r.get("code") or "").strip().lower()
        if c:
            res[c] = {
                "total": int(r.get("total") or 0),
                "last30": int(r.get("last30") or 0),
            }
    return res


def link_for(code: str, base_url: str, path: str = "/xe-cau") -> str:
    """Tạo link giới thiệu riêng cho đối tác."""
    clean_base = str(base_url or "").rstrip("/")
    quoted_code = urllib.parse.quote(str(code or "").strip(), safe="")
    return f"{clean_base}{path}?utm_source=partner&utm_medium=referral&utm_campaign={quoted_code}"
