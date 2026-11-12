"""Soạn nội dung tin rao vặt và theo dõi nơi đã đăng."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from backend.services.database import db
from frontend.crane.locations import AREAS, SERVICES

logger = logging.getLogger(__name__)

VALID_STATUSES = ("todo", "posted", "skip")

def _clean_url(url: Any) -> str:
    """Chỉ nhận http:// hoặc https://, dài <= 500, nếu không thì bỏ (trả về '')."""
    if not url:
        return ""
    val = str(url).strip()
    if len(val) > 500:
        return ""
    if val.startswith("http://") or val.startswith("https://"):
        return val
    return ""


def _strip_control_and_links(text: str) -> str:
    """Xóa ký tự điều khiển (trừ xuống dòng) và link http/https."""
    val = re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\r]", "", text)
    val = re.sub(r"https?://\S+", "", val)
    return val


# Câu chữ mặc định của tin đăng (viết cho dịch vụ nhận làm việc); dịch vụ khác ghi đè trong SERVICES[...]["ad_voice"].
# {company}, {label} (tên dịch vụ viết thường) và {area} được thay khi dựng tin.
DEFAULT_VOICE = {
    "need": "",
    "offer": "{company} nhận {label} trọn gói tại {area}.",
    "jobs_heading": "Các công việc nhận làm:",
    "title_2": "Dịch vụ {label} tại {area}",
    "offer_detail": "{company} cung cấp dịch vụ {label} chuyên nghiệp tại {area}.",
    "trust": "Thợ lái có chứng chỉ vận hành thiết bị nâng TCVN 4244, xe có sổ kiểm định an toàn kỹ thuật còn hạn.",
    "list_heading": "Danh mục dịch vụ nhận làm tại {area}:",
    "close": "Báo giá theo hiện trường, xuất hóa đơn VAT đầy đủ.",
    "question": "Bạn đang cần tìm dịch vụ {label} tại {area}?",
    "do_line": "{company} nhận làm việc này tại {area}.\nChúng tôi chuyên nhận làm:",
    "survey": "Khảo sát hiện trường và tư vấn tải trọng miễn phí tại khu vực {area}.",
}


def build_ads(
    service: str,
    area_slug: str,
    company: dict | str = "Đại Lộ Xanh",
    hotline: str = "0919 497 568",
) -> list[dict]:
    """Trả về đúng 3 biến thể {'title', 'body'}: ngắn, chi tiết, câu hỏi."""
    if service not in SERVICES or area_slug not in AREAS:
        raise ValueError(f"Dịch vụ '{service}' hoặc khu vực '{area_slug}' không hợp lệ.")

    svc = SERVICES[service]
    area = AREAS[area_slug]

    if isinstance(company, dict):
        company_name = company.get("brand_name") or company.get("company_name") or company.get("name") or "Đại Lộ Xanh"
        if not hotline and company.get("phone"):
            hotline = str(company["phone"])
    else:
        company_name = str(company or "Đại Lộ Xanh").strip()

    hotline_str = str(hotline or "0919 497 568").strip()

    company_name = _strip_control_and_links(company_name).strip()
    hotline_str = _strip_control_and_links(hotline_str).strip()

    area_name = area["name"]
    svc_label = svc.get("label", "")
    label = svc_label.lower()
    jobs_list = svc.get("jobs", [])[:4]
    voice = {**DEFAULT_VOICE, **svc.get("ad_voice", {})}

    def say(key: str) -> str:
        return voice[key].format(company=company_name, label=label, area=area_name)

    # Biến thể 1: Ngắn
    title_1 = f"{svc_label} tại {area_name} - {company_name}"
    if len(title_1) > 70:
        title_1 = title_1[:70].rstrip()
    jobs_1 = "\n".join(f"+ {j}" for j in jobs_list)
    body_1 = (
        f"{say('offer')}\n"
        f"{say('jobs_heading')}\n"
        f"{jobs_1}\n"
        f"Khu vực phục vụ: {area_name} và các vùng lân cận.\n"
        f"Hotline: {hotline_str}"
    )

    # Biến thể 2: Chi tiết
    title_2 = say("title_2")
    if len(title_2) > 70:
        title_2 = title_2[:70].rstrip()
    jobs_2 = "\n".join(f"- {j}" for j in jobs_list)
    body_2 = (
        f"{say('offer_detail')}\n"
        f"{say('trust')}\n"
        f"{say('list_heading')}\n"
        f"{jobs_2}\n"
        f"{say('close')}\n"
        f"Quý khách tại {area_name} vui lòng liên hệ hotline: {hotline_str}"
    )

    # Biến thể 3: Câu hỏi (mở đầu bằng câu hỏi về nhu cầu khách)
    title_3 = f"Cần {voice['need'] or label} tại {area_name}? Liên hệ ngay"
    if len(title_3) > 70:
        title_3 = title_3[:70].rstrip()
    jobs_3 = "\n".join(f"• {j}" for j in jobs_list)
    body_3 = (
        f"{say('question')}\n"
        f"{say('do_line')}\n"
        f"{jobs_3}\n"
        f"{say('survey')}\n"
        f"Gọi ngay hotline: {hotline_str}"
    )

    variants = [
        {"title": _strip_control_and_links(title_1)[:70], "body": _strip_control_and_links(body_1)[:700]},
        {"title": _strip_control_and_links(title_2)[:70], "body": _strip_control_and_links(body_2)[:700]},
        {"title": _strip_control_and_links(title_3)[:70], "body": _strip_control_and_links(body_3)[:700]},
    ]
    return variants


def list_directories(limit: int = 20, offset: int = 0) -> list[dict]:
    """Lấy danh sách nơi đăng có phân trang, sắp xếp created_at DESC, id DESC."""
    sql = """
        SELECT id, name, url, status, posted_url, posted_on, note, created_at
        FROM public.listing_directories
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
    """
    rows = db.query(sql, (limit, offset), fetchall=True)
    return rows if rows is not None else []


def count_directories() -> int:
    """Đếm tổng số nơi đăng."""
    sql = "SELECT COUNT(*) AS cnt FROM public.listing_directories"
    row = db.query(sql, fetch=True)
    return int(row.get("cnt", 0)) if row else 0


def create(name: str, url: str = "", note: str = "") -> int | None:
    """Tạo mới một nơi đăng."""
    clean_name = str(name or "").strip()
    if not clean_name:
        return None
    clean_url = _clean_url(url)
    clean_note = str(note or "").strip()
    sql = """
        INSERT INTO public.listing_directories (name, url, status, posted_url, note)
        VALUES (%s, %s, 'todo', '', %s)
        RETURNING id
    """
    row_id = db.query(sql, (clean_name, clean_url, clean_note), return_id=True, commit=True)
    return int(row_id) if row_id else None


def update(directory_id: int, data: dict[str, Any]) -> bool:
    """Cập nhật thông tin nơi đăng."""
    if not directory_id or not isinstance(data, dict):
        return False
    name = str(data.get("name") or "").strip()
    if not name:
        return False
    url = _clean_url(data.get("url"))
    note = str(data.get("note") or "").strip()

    has_posted = "posted_url" in data or "posted_on" in data
    if has_posted:
        posted_url = _clean_url(data.get("posted_url"))
        raw_on = data.get("posted_on")
        posted_on = None
        if isinstance(raw_on, str) and raw_on.strip():
            try:
                posted_on = datetime.strptime(raw_on.strip()[:10], "%Y-%m-%d").date()
            except ValueError:
                posted_on = None
        elif isinstance(raw_on, (date, datetime)):
            posted_on = raw_on.date() if isinstance(raw_on, datetime) else raw_on

        sql = """
            UPDATE public.listing_directories
            SET name = %s, url = %s, note = %s, posted_url = %s, posted_on = %s
            WHERE id = %s
        """
        params = (name, url, note, posted_url, posted_on, directory_id)
    else:
        sql = """
            UPDATE public.listing_directories
            SET name = %s, url = %s, note = %s
            WHERE id = %s
        """
        params = (name, url, note, directory_id)

    res = db.query(sql, params, commit=True)
    return bool(res)


def delete(directory_id: int) -> bool:
    """Xóa một nơi đăng."""
    if not directory_id:
        return False
    sql = "DELETE FROM public.listing_directories WHERE id = %s"
    res = db.query(sql, (directory_id,), commit=True)
    return bool(res)


def mark(
    directory_id: int,
    status: str,
    posted_url: str = "",
    posted_on: date | datetime | str | None = None,
) -> bool:
    """Cập nhật trạng thái, link tin và ngày đăng cho nơi đăng."""
    if not directory_id:
        return False

    clean_status = str(status or "").strip().lower()
    if clean_status not in VALID_STATUSES:
        return False

    clean_posted_url = _clean_url(posted_url)

    final_posted_on: date | None = None
    if clean_status == "posted":
        if not posted_on:
            final_posted_on = date.today()
        elif isinstance(posted_on, str):
            clean_str = posted_on.strip()
            if not clean_str:
                final_posted_on = date.today()
            else:
                try:
                    final_posted_on = datetime.strptime(clean_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    final_posted_on = date.today()
        elif isinstance(posted_on, datetime):
            final_posted_on = posted_on.date()
        elif isinstance(posted_on, date):
            final_posted_on = posted_on
        else:
            final_posted_on = date.today()
    else:
        if isinstance(posted_on, str):
            clean_str = posted_on.strip()
            if clean_str:
                try:
                    final_posted_on = datetime.strptime(clean_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    final_posted_on = None
        elif isinstance(posted_on, datetime):
            final_posted_on = posted_on.date()
        elif isinstance(posted_on, date):
            final_posted_on = posted_on

    sql = """
        UPDATE public.listing_directories
        SET status = %s, posted_url = %s, posted_on = %s
        WHERE id = %s
    """
    res = db.query(sql, (clean_status, clean_posted_url, final_posted_on, directory_id), commit=True)
    return bool(res)
