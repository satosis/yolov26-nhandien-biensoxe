import re
import uuid
import logging
from backend.services.database import db

logger = logging.getLogger("plate_repo")


def check_whitelist(plate_text: str) -> dict | None:
    if not plate_text:
        return None
    clean_input = re.sub(r'[^A-Z0-9]', '', plate_text.upper())
    query = """
        SELECT * FROM plate_whitelist 
        WHERE UPPER(regexp_replace(plate_number, '[^a-zA-Z0-9]', '', 'g')) = %s
        AND is_active = TRUE 
        LIMIT 1
    """
    return db.execute_query(query, (clean_input,), fetch=True)


def add_pending(plate_norm: str, plate_raw: str, camera_id: str = None,
                site_id: int = None, image_path: str = None,
                plate_type: str = "white") -> str | None:
    clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
    existing = db.execute_query("""
        SELECT pending_id FROM pending_plates
        WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s
        AND (LOWER(TRIM(status)) = 'pending'
             OR confirmed_at_utc > NOW() - INTERVAL '60 seconds')
        LIMIT 1
    """, (clean_plate,), fetch=True)
    if existing:
        return "existing"

    pid = str(uuid.uuid4())[:8]
    s_id_val = int(site_id) if (site_id is not None and str(site_id).isdigit()) else None
    saved = db.execute_query("""
        INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type)
        VALUES (%s, %s, %s, NOW(), 'pending', %s, %s, %s)
        ON CONFLICT (pending_id) DO NOTHING
    """, (pid, plate_raw, plate_norm, s_id_val, image_path, plate_type), commit=True)
    if not saved:
        logger.error("Pending plate insert returned no row for site %s", site_id)
        return None
    return "inserted"
