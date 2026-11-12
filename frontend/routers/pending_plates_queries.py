"""Truy vấn cơ sở dữ liệu của frontend.routers.pending_plates_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db


def get_pending_plate_by_id_select_pending_plates(pending_id):
    return db.execute_query(
        "SELECT pending_id, plate_raw, plate_norm, image_path, plate_type, site_id, first_seen_utc "
        "FROM pending_plates WHERE pending_id = %s AND LOWER(TRIM(status)) = 'pending'",
        (pending_id,),
        fetch=True
    )


def update_pending_plate_update_pending_plates(plate_raw, plate_norm, pending_id):
    return db.execute_query(
        """UPDATE pending_plates
              SET plate_raw = %s, plate_norm = %s
            WHERE pending_id = %s AND LOWER(TRIM(status)) = 'pending'
            RETURNING pending_id""",
        (plate_raw, plate_norm, pending_id), fetch=True, commit=True,
    )


def delete_pending_plate_by_id_delete_pending_plates(pending_id):
    return db.execute_query(
        """WITH deleted_plates AS (
               DELETE FROM pending_plates AS plate
               USING pending_plates AS target
               WHERE target.pending_id = %s
                 AND LOWER(TRIM(target.status)) = 'pending'
                 AND LOWER(TRIM(plate.status)) = 'pending'
                 AND (
                     plate.pending_id = target.pending_id
                     OR (
                         target.plate_norm IS NOT NULL
                         AND target.plate_norm <> ''
                         AND UPPER(regexp_replace(plate.plate_norm, '[^a-zA-Z0-9]', '', 'g')) =
                             UPPER(regexp_replace(target.plate_norm, '[^a-zA-Z0-9]', '', 'g'))
                     )
                 )
               RETURNING plate.pending_id, plate.image_path
           ), deleted_logs AS (
               DELETE FROM stranger_logs
               WHERE image_path IN (
                   SELECT image_path FROM deleted_plates
                   WHERE pending_id = %s AND image_path IS NOT NULL
               )
               RETURNING id
           )
           SELECT EXISTS(
               SELECT 1 FROM deleted_plates WHERE pending_id = %s
           ) AS deleted""",
        (pending_id, pending_id, pending_id), fetch=True, commit=True,
    )


def register_page_select_users():
    return db.select('users', columns=['id', 'full_name', 'username'], order_by='full_name ASC')


def get_pending_plates(site_id=None, limit=None, offset=None) -> list[dict]:
    query = "SELECT p.*, p.first_seen_utc AS first_seen, s.site_name FROM pending_plates p LEFT JOIN sites s ON p.site_id = s.id WHERE LOWER(TRIM(p.status)) = 'pending'"
    params = []
    if site_id:
        query += " AND (p.site_id = %s OR p.site_id IS NULL)"
        params.append(site_id)
    query += " ORDER BY p.first_seen_utc DESC"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def count_pending_plates(site_id=None) -> int:
    query = "SELECT COUNT(*) AS total FROM pending_plates p WHERE LOWER(TRIM(p.status)) = 'pending'"
    params = []
    if site_id:
        query += " AND (p.site_id = %s OR p.site_id IS NULL)"
        params.append(site_id)
    row = db.execute_query(query, tuple(params) if params else None, fetch=True)
    return int((row or {}).get("total") or 0)
