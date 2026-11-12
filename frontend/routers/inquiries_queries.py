"""Truy vấn cơ sở dữ liệu của frontend.routers.inquiries_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db

_SERVICE_CASE = """CASE
    WHEN source_page = 'crane_landing' OR (split_part(source_page, '_', 1) = 'area' AND split_part(source_page, '_', 2) IN ('thue-xe-cau', 'cau-lap-dat-thiet-bi')) THEN 'crane'
    WHEN source_page = 'weights_landing' OR (split_part(source_page, '_', 1) = 'area' AND split_part(source_page, '_', 2) = 'van-chuyen-qua-can') THEN 'weights'
    WHEN source_page = 'container_landing' OR (split_part(source_page, '_', 1) = 'area' AND split_part(source_page, '_', 2) = 'thue-container') THEN 'container'
    WHEN source_page = 'solar_landing' OR (split_part(source_page, '_', 1) = 'area' AND split_part(source_page, '_', 2) = 'dien-mat-troi') THEN 'solar'
    WHEN source_page = 'photo_landing' OR (split_part(source_page, '_', 1) = 'area' AND split_part(source_page, '_', 2) = 'thue-may-anh') THEN 'photo'
    ELSE 'other'
END"""


def _where_clause(unread_only: bool = False, service: str = "") -> tuple[str, tuple]:
    parts, params = [], []
    if unread_only:
        parts.append("is_read = FALSE")
    if service:
        parts.append(f"({_SERVICE_CASE}) = %s")
        params.append(service)
    return ("WHERE " + " AND ".join(parts)) if parts else "", tuple(params)


def get_lead_source_summary_select_inquiries(days):
    return db._execute_query(
        "SELECT lead_source, COUNT(*) AS n FROM inquiries WHERE created_at >= NOW() - make_interval(days => %s) GROUP BY lead_source ORDER BY n DESC",
        (days,),
        fetchall=True,
        commit=False,
    )


def get_inquiry_service_counts():
    return db._execute_query(
        f"SELECT ({_SERVICE_CASE}) AS service, COUNT(*) AS n FROM inquiries GROUP BY ({_SERVICE_CASE})",
        fetchall=True,
        commit=False,
    )


def get_inquiries_select_inquiries(limit, offset, unread_only: bool = False, service: str = ""):
    where, params = _where_clause(unread_only=unread_only, service=service)
    return db._execute_query(
        f"SELECT * FROM inquiries {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + (limit, offset),
        fetchall=True,
        commit=False,
    )


def count_all_inquiries_select_inquiries(unread_only: bool = False, service: str = ""):
    where, params = _where_clause(unread_only=unread_only, service=service)
    return db._execute_query(f"SELECT COUNT(*) AS cnt FROM inquiries {where}", params or None, fetch=True, commit=False)


def count_unread_inquiries_select_inquiries(service: str = ""):
    where, params = _where_clause(unread_only=True, service=service)
    return db._execute_query(f"SELECT COUNT(*) AS cnt FROM inquiries {where}", params or None, fetch=True, commit=False)


def update_inquiry_note_update_inquiries(note, inquiry_id):
    return db._execute_query("UPDATE inquiries SET note = %s WHERE id = %s", (note, inquiry_id), commit=True)


def delete_inquiry_record_delete_inquiries(inquiry_id):
    return db._execute_query("DELETE FROM inquiries WHERE id = %s", (inquiry_id,), commit=True)


def save_inquiry_insert_inquiries(inquiry):
    return db._execute_query(
        "INSERT INTO inquiries (full_name, phone, company_name, demand, source_page, gclid, gbraid, wbraid, fbclid, utm_source, utm_medium, utm_campaign, utm_term, utm_content, landing_url, referrer, lead_source, consent_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            inquiry.full_name,
            inquiry.phone,
            inquiry.company_name,
            inquiry.demand,
            inquiry.source_page,
            inquiry.gclid,
            inquiry.gbraid,
            inquiry.wbraid,
            inquiry.fbclid,
            inquiry.utm_source,
            inquiry.utm_medium,
            inquiry.utm_campaign,
            inquiry.utm_term,
            inquiry.utm_content,
            inquiry.landing_url,
            inquiry.referrer,
            inquiry.lead_source,
            inquiry.consent_at,
        ),
        fetch=True,
        commit=True,
    )


def mark_inquiry_read(inquiry_id: int) -> bool:
    sql = "UPDATE inquiries SET is_read = TRUE, read_at = COALESCE(read_at, NOW()) WHERE id = %s"
    return bool(db._execute_query(sql, (inquiry_id,), commit=True))


def mark_all_inquiries_read() -> bool:
    sql = "UPDATE inquiries SET is_read = TRUE, read_at = COALESCE(read_at, NOW()) WHERE is_read = FALSE"
    return bool(db._execute_query(sql, commit=True))
