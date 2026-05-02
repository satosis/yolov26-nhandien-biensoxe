import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from datetime import datetime
from contextlib import contextmanager
import json
import logging
import re
import time
import uuid
import secrets
import urllib.parse
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
load_dotenv(os.path.join(os.getcwd(), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

class DatabaseError(Exception):
    """Custom exception for database-related errors."""
    pass


class DatabaseManager:
    _pool = None

    def __init__(self, dsn=None, max_pool_size=None):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        if not self.dsn:
            # Fallback check before raising to debug
            logger.error("DATABASE_URL NOT FOUND IN ENV. CWD: %s, PATH: %s", os.getcwd(), __file__)
            raise ValueError("DATABASE_URL environment variable is required")
        self.dsn = self.dsn.replace("postgresql+asyncpg://", "postgresql://")

        if not DatabaseManager._pool:
            maxconn = int(max_pool_size) if max_pool_size else 15
            DatabaseManager._pool = pool.ThreadedConnectionPool(1, maxconn, self.dsn)

    @contextmanager
    def _get_connection(self):
        """Context manager to lease a connection from the pool and return it safely.
        Includes retry logic / fail-fast if pool is broken."""
        conn = None
        if self._pool:
            conn = self._pool.getconn()
        else:
            conn = psycopg2.connect(self.dsn, connect_timeout=3)

        try:
            yield conn
        except Exception:
            if conn:
                try:
                    conn.rollback()
                except psycopg2.InterfaceError:
                    pass  # Already closed
            raise
        finally:
            if conn:
                if self._pool:
                    self._pool.putconn(conn)
                else:
                    conn.close()

    def _execute_query(self, query, params=None, fetch=False, fetchall=False, commit=True, return_id=False):
        """Core method to execute queries safely with centralized error handling."""
        try:
            with self._get_connection() as conn:
                cursor_factory = RealDictCursor if fetch or fetchall else None
                with conn.cursor(cursor_factory=cursor_factory) as cursor:
                    cursor.execute(query, params)
                    result = True
                    if fetch:
                        result = cursor.fetchone()
                        if result and cursor_factory:
                            result = dict(result)
                    elif fetchall:
                        result = cursor.fetchall()
                        if result and cursor_factory:
                            result = [dict(r) for r in result]
                    elif return_id:
                        row = cursor.fetchone()
                        result = row[0] if row else None
                    if commit:
                        conn.commit()
                    else:
                        conn.rollback()  # Clear transaction state for read-only queries
                    return result
        except (psycopg2.Error, Exception) as e:
            logger.error("Database query failed: %s\nQuery: %s\nParams: %s", str(e), query, params, exc_info=True)
            raise DatabaseError(f"Database query failed: {str(e)}") from e
    # Whitelist of allowed table/column names for generic ORM methods
    _ALLOWED_TABLES = frozenset({
        'users', 'roles', 'cameras', 'sites', 'plate_events',
        'vehicle_sessions', 'plate_whitelist', 'pending_plates',
        'app_settings', 'camera_status_log', 'legal_hold',
        'monthly_reports', 'telegram_bots', 'cameras_asset',
        'stranger_logs', 'rolling_doors', 'video_recordings',
        'inquiries', 'projects', 'news',
    })

    @staticmethod
    def _validate_identifier(name: str):
        """Validate that a SQL identifier contains only safe characters."""
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            raise ValueError(f"Invalid SQL identifier: {name}")

    def insert(self, table_name, data: dict, returning=None):
        """Generic insert."""
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Table '{table_name}' not in allowed list")
        for col in data.keys():
            self._validate_identifier(col)
        if returning:
            self._validate_identifier(returning)

        columns = ', '.join(data.keys())
        values_template = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {table_name} ({columns}) VALUES ({values_template})"
        if returning:
            query += f" RETURNING {returning}"

        return self._execute_query(query, tuple(data.values()), commit=True, return_id=bool(returning))

    def update(self, table_name, data: dict, conditions: dict):
        """Generic update."""
        if not data or not conditions:
            return False
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Table '{table_name}' not in allowed list")
        for col in list(data.keys()) + list(conditions.keys()):
            self._validate_identifier(col)

        set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
        where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
        query = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"

        params = tuple(data.values()) + tuple(conditions.values())
        return self._execute_query(query, params, commit=True) is not None

    def select(self, table_name, columns='*', conditions=None, order_by=None, limit=None, fetchall=True):
        """Generic select."""
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Table '{table_name}' not in allowed list")

        if isinstance(columns, list):
            for col in columns:
                self._validate_identifier(col)
            columns = ', '.join(columns)
        elif columns != '*':
            for col in columns.split(','):
                self._validate_identifier(col.strip())

        query = f"SELECT {columns} FROM {table_name}"
        params = []

        if conditions:
            for col in conditions.keys():
                self._validate_identifier(col)
            where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
            query += f" WHERE {where_clause}"
            params = tuple(conditions.values())

        if order_by:
            # Validate order_by: allow "col ASC/DESC" patterns
            for part in order_by.split(','):
                token = part.strip().split()[0]
                self._validate_identifier(token)
            query += f" ORDER BY {order_by}"

        if limit:
            query += " LIMIT %s"
            params = tuple(params) + (int(limit),)

        if fetchall:
            return self._execute_query(query, params, fetchall=True, commit=False) or []
        else:
            return self._execute_query(query, params, fetch=True, commit=False)

    def delete(self, table_name, conditions: dict):
        """Generic delete."""
        if not conditions:
            return False
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Table '{table_name}' not in allowed list")
        for col in conditions.keys():
            self._validate_identifier(col)

        where_clause = ' AND '.join([f"{k} = %s" for k in conditions.keys()])
        query = f"DELETE FROM {table_name} WHERE {where_clause}"
        return self._execute_query(query, tuple(conditions.values()), commit=True) is not None

    def is_plate_whitelisted(self, plate_norm, site_id=None):
        if site_id:
            # Use JSONB containment operator @>
            query = "SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND site_ids @> %s::jsonb AND is_active = TRUE LIMIT 1"
            return self._execute_query(query, (plate_norm, json.dumps([site_id])), fetch=True) is not None
        else:
            query = "SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND is_active = TRUE LIMIT 1"
            return self._execute_query(query, (plate_norm,), fetch=True) is not None
    def is_person_whitelisted(self, identity, site_id=None):
        if identity == "STRANGER": return False
        search_col = "username" if isinstance(identity, str) else "id"
        if site_id:
            query = f"SELECT 1 FROM users WHERE {search_col} = %s AND %s = ANY(site_ids) LIMIT 1"
            return self._execute_query(query, (identity, site_id), fetch=True) is not None
        else:
            query = f"SELECT 1 FROM users WHERE {search_col} = %s LIMIT 1"
            return self._execute_query(query, (identity,), fetch=True) is not None
    def get_person_by_identity(self, identity):
        if identity == "STRANGER": return None
        if isinstance(identity, int) or (isinstance(identity, str) and identity.isdigit()):
            query = "SELECT id, full_name, site_ids, note, username, person_identity FROM users WHERE id = %s OR username = %s OR person_identity = %s"
            row = self._execute_query(query, (int(identity), str(identity), str(identity)), fetch=True)
        else:
            query = "SELECT id, full_name, site_ids, note, username, person_identity FROM users WHERE username = %s OR person_identity = %s"
            row = self._execute_query(query, (identity, identity), fetch=True)

        if row:
            return {
                "identity": str(row['id']),
                "full_name": row['full_name'],
                "username": row['username'],
                "person_identity": row['person_identity'],
                "site_ids": row['site_ids'],
                "note": row['note']
            }
        return None
    def add_pending_plate(self, pending_id, event_id, plate_raw, plate_norm, first_seen_utc, site_id=None, plate_type="white"):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    INSERT INTO pending_plates (
                        pending_id, event_id, plate_raw, plate_norm, first_seen_utc, status, site_id, plate_type
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pending_id) DO NOTHING
                    ''',
                    (pending_id, event_id, plate_raw, plate_norm, first_seen_utc, "pending", site_id, plate_type)
                )
                conn.commit()
    def upsert_vehicle_whitelist(self, plate_norm, label, added_by, site_ids=None, note=None):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    INSERT INTO plate_whitelist (plate_number, list_type, owner_name, added_by, site_ids, note, is_active)
                    VALUES (%s, 'white', %s, %s, %s, %s, TRUE)
                    ON CONFLICT(plate_number) DO UPDATE SET
                    owner_name=EXCLUDED.owner_name,
                    added_by=EXCLUDED.added_by,
                    site_ids=EXCLUDED.site_ids,
                    note=EXCLUDED.note,
                    updated_at=NOW()
                    ''',
                    (plate_norm, label, added_by, json.dumps(site_ids) if site_ids else '[]', note)
                )
            conn.commit()
            self._trigger_ai_reload()
            return True
    def update_vehicle_whitelist(self, vehicle_id, plate_norm, label, site_ids=None, note=None):
        """Updates a whitelisted vehicle by ID."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    UPDATE plate_whitelist
                    SET plate_number = %s, owner_name = %s, site_ids = %s, note = %s, updated_at = NOW()
                    WHERE id = %s
                    ''',
                    (plate_norm, label, json.dumps(site_ids) if site_ids else '[]', note, vehicle_id)
                )
            conn.commit()
            self._trigger_ai_reload()
            return True
    def check_whitelist(self, plate_text: str) -> dict | None:
        """
        Check if a plate exists in whitelist using separator-insensitive matching.
        Normalizes both the input and the database entries to A-Z 0-9 for comparison.
        """
        if not plate_text: return None
        
        # Strip everything except alphanumeric for the lookup parameter
        clean_input = re.sub(r'[^A-Z0-9]', '', plate_text.upper())
        
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Use PostgreSQL regexp_replace to strip separators from the stored plate_number for comparison
                cursor.execute(
                    """
                    SELECT * FROM plate_whitelist 
                    WHERE regexp_replace(plate_number, '[^a-zA-Z0-9]', '', 'g') = %s 
                    AND is_active = TRUE 
                    LIMIT 1
                    """,
                    (clean_input,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
    def update_pending_status_by_id(self, pending_id, status, confirmed_by):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    UPDATE pending_plates
                    SET status = %s, confirmed_at_utc = NOW(), confirmed_by = %s
                    WHERE pending_id = %s AND LOWER(TRIM(status)) = 'pending'
                    ''',
                    (status, confirmed_by, pending_id)
                )
                if cursor.rowcount > 0:
                    logger.info(f"Updated pending record {pending_id} to {status}")
                    self._trigger_ai_reload()
                    return True
                else:
                    logger.warning(f"No pending record found with ID {pending_id} or status not 'pending'")
                    return False

    def _trigger_ai_reload(self):
        """Touch the .reload signal file to force AI processes to refresh their caches."""
        from backend.core import config as cfg
        rt = os.path.join(cfg.KNOWN_FACES_DIR, ".reload")
        os.makedirs(os.path.dirname(rt), exist_ok=True)
        with open(rt, "w") as f:
            f.write(str(time.time()))
        logger.info("AI Reload trigger sent successfully")

    def get_pending_plate_by_id(self, pending_id) -> dict | None:
        """Fetch details for a specific pending plate by its ID."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT pending_id, plate_raw, plate_norm, image_path, plate_type, site_id, first_seen_utc FROM pending_plates WHERE pending_id = %s",
                    (pending_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def delete_pending_plate_by_id(self, pending_id) -> bool:
        """Permanently delete a pending plate record, any duplicate pending tasks for the same plate, and associated logs."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Fetch plate_norm and image_path to clear associated records
                cursor.execute("SELECT plate_norm, image_path FROM pending_plates WHERE pending_id = %s", (pending_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                
                plate_norm, img_path = row[0], row[1]

                # 2. Delete ALL pending records for this plate (Separator-insensitive deduplication)
                # Ensure all formatting variants (e.g. 88A-000.24 and 88A00024) are cleared
                clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
                cursor.execute("""
                    DELETE FROM pending_plates 
                    WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s 
                    AND LOWER(TRIM(status)) = 'pending'
                """, (clean_plate,))
                deleted_pending_count = cursor.rowcount

                # 3. Cascading delete from stranger_logs using image_path
                deleted_logs_count = 0
                if img_path:
                    cursor.execute("DELETE FROM stranger_logs WHERE image_path = %s", (img_path,))
                    deleted_logs_count = cursor.rowcount

                conn.commit()
                self._trigger_ai_reload()

                logger.info(f"Cascading Plate Delete: Removed {deleted_pending_count} pending entries for {plate_norm} and {deleted_logs_count} activity logs.")
                return True


    def update_pending_status(self, plate_norm, status, confirmed_by):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    UPDATE pending_plates
                    SET status = %s, confirmed_at_utc = NOW(), confirmed_by = %s
                    WHERE plate_norm = %s AND LOWER(TRIM(status)) = 'pending'
                    ''',
                    (status, confirmed_by, plate_norm)
                )
                res = cursor.rowcount > 0
                if res: self._trigger_ai_reload()
                return res
    def log_event(self, event_type, description, trucks, people):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    INSERT INTO plate_events (event_time, camera_id, vehicle_type, plate_number, confidence)
                    VALUES (NOW(), %s, %s, %s, %s) RETURNING id
                    ''',
                    ("default", event_type, description or "unknown", 0.0)
                )
                conn.commit()
                return cursor.fetchone()[0]

    def add_video_recording(self, cam_id, site_id, event_type, event_text, file_path, thumbnail_path, duration, file_size=None):
        """Register a new video recording in the database."""
        return self.insert("video_recordings", {
            "cam_id": str(cam_id),
            "site_id": site_id,
            "event_type": event_type,
            "event_text": event_text,
            "file_path": file_path,
            "thumbnail_path": thumbnail_path,
            "duration_seconds": int(duration),
            "file_size_bytes": file_size
        }, returning="id")

    def get_recordings(self, site_id=None, limit=50, offset=0):
        """Fetch list of recordings, optionally filtered by site."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = "SELECT * FROM video_recordings"
                params = []
                if site_id:
                    query += " WHERE site_id = %s"
                    params.append(site_id)
                query += " ORDER BY start_time DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])
                cursor.execute(query, params)
                return [dict(r) for r in cursor.fetchall()]

    def count_recordings(self, site_id=None):
        """Count total recordings, optionally filtered by site."""
        query = "SELECT COUNT(*) FROM video_recordings"
        params = []
        if site_id:
            query += " WHERE site_id = %s"
            params.append(site_id)
        row = self._execute_query(query, params, fetchall=False)
        return row[0] if row else 0

    def delete_old_recordings(self, days: int):
        """Find and remove recordings older than 'days' days, including physical files."""
        query = "SELECT id, file_path, thumbnail_path FROM video_recordings WHERE start_time < NOW() - (%s * INTERVAL '1 day')"
        rows = self._execute_query(query, (days,), fetchall=True)
        if not rows:
            return 0
            
        deleted_count = 0
        for row in rows:
            # 1. Delete physical files
            for path_key in ["file_path", "thumbnail_path"]:
                path = row[path_key]
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as e:
                        logger.error(f"Failed to delete recording file {path}: {e}")
            
            # 2. Delete DB record
            self.delete("video_recordings", {"id": row["id"]})
            deleted_count += 1
            
        return deleted_count
    def get_stats(self):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # Optimized to look only at today's vehicle counts instead of full table scan
                cursor.execute("SELECT COUNT(*), vehicle_type FROM plate_events WHERE event_time >= CURRENT_DATE GROUP BY vehicle_type")
                return cursor.fetchall()
    # ───────────────── END AI AUDITOR ──────────────

    def get_pending_plates(self, site_id=None):
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = "SELECT p.*, p.first_seen_utc AS first_seen, s.site_name FROM pending_plates p LEFT JOIN sites s ON p.site_id = s.id WHERE LOWER(TRIM(p.status)) = 'pending'"
                params = []
                if site_id:
                    # Show site-specific AND global (NULL) pending plates
                    query += " AND (p.site_id = %s OR p.site_id IS NULL)"
                    params.append(site_id)
                query += " ORDER BY p.first_seen_utc DESC"
                cursor.execute(query, params)
                return cursor.fetchall()
    def log_camera_event(self, cam_id: str, event_type: str, started_at: str,
                         ended_at: str = None, duration_seconds: float = None,
                         notes: str = None) -> int:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO camera_status_log
                       (camera_id, status, event_type, logged_at)
                       VALUES (%s, %s, %s, %s) RETURNING id''',
                    (cam_id, event_type, event_type, started_at)
                )
                result = cursor.fetchone()[0]
                conn.commit()
                return result
    def get_camera_health(self, cam_id: str = None, hours: int = 24) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if cam_id:
                    cursor.execute(
                        "SELECT * FROM camera_status_log WHERE camera_id = %s AND logged_at >= NOW() - (%s * INTERVAL '1 hour') ORDER BY logged_at DESC",
                        (cam_id, hours)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM camera_status_log WHERE logged_at >= NOW() - (%s * INTERVAL '1 hour') ORDER BY logged_at DESC",
                        (hours,)
                    )
                return [dict(r) for r in cursor.fetchall()]

    def get_cameras_latest_status(self) -> dict:
        """Get latest health status for each camera. Returns {camera_id_str: {status, logged_at}}."""
        query = """
            SELECT DISTINCT ON (camera_id) camera_id, status, logged_at
            FROM camera_status_log
            ORDER BY camera_id, logged_at DESC
        """
        rows = self._execute_query(query, fetchall=True, commit=False) or []
        return {str(r['camera_id']): r for r in rows}

    def add_legal_hold(self, file_path: str, reason: str, held_by: str) -> bool:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    'INSERT INTO legal_hold (file_path, reason, held_by, held_at) VALUES (%s, %s, %s, NOW()) ON CONFLICT DO NOTHING',
                    (file_path, reason, held_by)
                )
                conn.commit()
                return True
    def release_legal_hold(self, file_path: str) -> bool:
        query = "UPDATE legal_hold SET released_at = NOW() WHERE file_path = %s AND released_at IS NULL"
        return self._execute_query(query, (file_path,), commit=True) is not None

    def is_legal_hold(self, file_path: str) -> bool:
        return self._execute_query(
            "SELECT 1 FROM legal_hold WHERE file_path = %s AND released_at IS NULL LIMIT 1",
            (file_path,), fetch=True
        ) is not None

    def get_user(self, username: str) -> dict:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    '''SELECT u.*, r.role_name as role 
                       FROM users u 
                       JOIN roles r ON u.role_id = r.id 
                       WHERE u.username = %s''', 
                    (username,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    '''SELECT u.id, u.username, u.full_name, u.role_id, u.site_ids, u.note, u.person_identity, r.role_name as role 
                       FROM users u 
                       JOIN roles r ON u.role_id = r.id 
                       WHERE u.id = %s''', 
                    (user_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
    # --- Remember Token (secure "remember me", stored in users table) ---

    def create_remember_token(self, user_id: int, days: int = 7) -> str | None:
        """Create a secure remember token for auto-login. Returns token string."""
        token = secrets.token_urlsafe(32)
        self._execute_query(
            "UPDATE users SET remember_token = %s, remember_token_expires = NOW() + (%s * INTERVAL '1 day') WHERE id = %s",
            (token, days, user_id), commit=True
        )
        return token
    def get_user_by_remember_token(self, token: str) -> dict | None:
        """Lookup user by valid (non-expired) remember token."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    '''SELECT u.*, r.role_name as role
                       FROM users u
                       JOIN roles r ON u.role_id = r.id
                       WHERE u.remember_token = %s AND u.remember_token_expires > NOW()''',
                    (token,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
    def delete_remember_tokens(self, user_id: int):
        """Clear remember token for a user (logout / password change)."""
        self._execute_query(
            "UPDATE users SET remember_token = NULL, remember_token_expires = NULL WHERE id = %s",
            (user_id,), commit=True
        )
    def create_user(self, username: str, hashed_password: str, role_name: str,
                    full_name: str = None,
                    note: str = None, site_ids: list | None = None) -> tuple[bool, str]:
        existing = self.select("users", columns="id", conditions={"username": username}, fetchall=False)
        if existing:
            return False, f"Username '{username}' đã tồn tại."
        query = '''
            INSERT INTO users (username, hashed_password, role_id, full_name, note, site_ids)
            VALUES (%s, %s, (SELECT id FROM roles WHERE role_name = %s LIMIT 1), %s, %s, %s)
            RETURNING id
        '''
        result = self._execute_query(query, (username, hashed_password, role_name, full_name, note, site_ids), commit=True)
        return (True, "") if result is not None else (False, "Lỗi CSDL khi tạo người dùng.")

    def update_role_permissions(self, role_id: int, allowed_menus: str) -> bool:
        return self.update("roles", {"allowed_menus": allowed_menus}, {"id": role_id})

    def get_all_users(self, limit=None, offset=None) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = '''SELECT u.id, u.username, r.role_name as role, u.created_at, u.full_name, u.note, u.site_ids, u.person_identity, u.kyc_level
                           FROM users u
                           LEFT JOIN roles r ON u.role_id = r.id
                           ORDER BY u.id'''
                params = []
                if limit is not None:
                    query += " LIMIT %s"
                    params.append(limit)
                if offset is not None:
                    query += " OFFSET %s"
                    params.append(offset)
                cursor.execute(query, params)
                return [dict(r) for r in cursor.fetchall()]
    def update_user(self, user_id, username, role_name, hashed_password=None,
                    full_name=None, note=None, site_ids=None) -> bool:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                if hashed_password:
                    cursor.execute(
                        '''UPDATE users
                           SET username=%s,
                               role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                               hashed_password=%s,
                               full_name=%s,
                               note=%s,
                               site_ids=%s
                           WHERE id=%s''',
                        (username, role_name, hashed_password, full_name, note, site_ids, user_id)
                    )
                else:
                    cursor.execute(
                        '''UPDATE users
                           SET username=%s,
                               role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                               full_name=%s,
                               note=%s,
                               site_ids=%s
                           WHERE id=%s''',
                        (username, role_name, full_name, note, site_ids, user_id)
                    )
                conn.commit()
                return True
    def delete_user(self, user_id) -> bool:
        return self.delete("users", {"id": user_id})

    # ── KYC Methods ──────────────────────────────────────────────────────────

    def update_kyc_basic_info(self, user_id, full_name, phone, email, dob, id_number):
        """Level 1: Save basic personal info and advance to kyc_level=1."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''UPDATE users SET full_name=%s, phone=%s, email=%s,
                       date_of_birth=%s, id_number=%s, kyc_level=GREATEST(kyc_level, 1)
                       WHERE id=%s''',
                    (full_name, phone, email, dob, id_number, user_id)
                )
                conn.commit()
                return True

    def insert_kyc_document(self, user_id, doc_type, file_path):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO kyc_documents (user_id, doc_type, file_path)
                       VALUES (%s, %s, %s)
                       ON CONFLICT DO NOTHING''',
                    (user_id, doc_type, file_path)
                )
                conn.commit()
                return True

    def delete_kyc_document(self, user_id, doc_type):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM kyc_documents WHERE user_id=%s AND doc_type=%s",
                    (user_id, doc_type)
                )
                conn.commit()
                return True

    def get_kyc_documents(self, user_id):
        query = "SELECT * FROM kyc_documents WHERE user_id=%s ORDER BY uploaded_at"
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, (user_id,))
                return [dict(r) for r in cursor.fetchall()]

    def update_kyc_level(self, user_id, level):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                if level == 3:
                    cursor.execute(
                        "UPDATE users SET kyc_level=%s, kyc_submitted_at=NOW() WHERE id=%s",
                        (level, user_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE users SET kyc_level=%s WHERE id=%s",
                        (level, user_id)
                    )
                conn.commit()
                return True

    def get_kyc_user_detail(self, user_id):
        query = '''SELECT u.id, u.username, u.full_name, u.phone, u.email,
                          u.date_of_birth, u.id_number, u.kyc_level,
                          u.kyc_submitted_at, u.kyc_reviewed_at,
                          u.kyc_reviewed_by, u.kyc_reject_reason,
                          r.role_name as role
                   FROM users u LEFT JOIN roles r ON u.role_id = r.id
                   WHERE u.id=%s'''
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_pending_kyc_users(self):
        query = '''SELECT u.id, u.username, u.full_name, u.phone, u.email,
                          u.id_number, u.kyc_level, u.kyc_submitted_at,
                          r.role_name as role
                   FROM users u LEFT JOIN roles r ON u.role_id = r.id
                   WHERE u.kyc_level = 3
                   ORDER BY u.kyc_submitted_at'''
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query)
                return [dict(r) for r in cursor.fetchall()]

    def approve_kyc(self, user_id, reviewer):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''UPDATE users SET kyc_level=4, kyc_reviewed_at=NOW(),
                       kyc_reviewed_by=%s, kyc_reject_reason=NULL WHERE id=%s''',
                    (reviewer, user_id)
                )
                cursor.execute(
                    "UPDATE kyc_documents SET status='approved', reviewed_at=NOW(), reviewed_by=%s WHERE user_id=%s",
                    (reviewer, user_id)
                )
                conn.commit()
                return True

    def reject_kyc(self, user_id, reviewer, reason):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''UPDATE users SET kyc_level=-1, kyc_reviewed_at=NOW(),
                       kyc_reviewed_by=%s, kyc_reject_reason=%s WHERE id=%s''',
                    (reviewer, reason, user_id)
                )
                cursor.execute(
                    "UPDATE kyc_documents SET status='rejected', reviewed_at=NOW(), reviewed_by=%s WHERE user_id=%s",
                    (reviewer, user_id)
                )
                conn.commit()
                return True

    def reset_kyc(self, user_id):
        """Reset KYC for re-submission after rejection."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''UPDATE users SET kyc_level=0, kyc_submitted_at=NULL,
                       kyc_reviewed_at=NULL, kyc_reviewed_by=NULL, kyc_reject_reason=NULL
                       WHERE id=%s''',
                    (user_id,)
                )
                cursor.execute("DELETE FROM kyc_documents WHERE user_id=%s", (user_id,))
                conn.commit()
                return True

    def get_telegram_bots(self) -> list[dict]:
        return self.select("telegram_bots", order_by="bot_name")

    def upsert_telegram_bot(self, bot_name, token, chat_id) -> bool:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO telegram_bots (bot_name, token, chat_id)
                       VALUES (%s, %s, %s)
                       ON CONFLICT(bot_name) DO UPDATE SET
                           token=EXCLUDED.token,
                           chat_id=EXCLUDED.chat_id''',
                    (bot_name, token, chat_id)
                )
                return True
    def delete_telegram_bot(self, bot_name) -> bool:
        return self.delete("telegram_bots", {"bot_name": bot_name})

    def get_roles(self) -> list[dict]:
        return self.select("roles", order_by="id")

    def create_role(self, role_name: str, description: str = "") -> tuple[bool, str]:
        existing = self.select("roles", conditions={"role_name": role_name}, fetchall=False)
        if existing:
            return False, "Role đã tồn tại."
        result = self.insert("roles", {"role_name": role_name, "allowed_menus": "none"}, returning="id")
        return (True, "") if result is not None else (False, "Lỗi CSDL khi tạo role.")

    def delete_role(self, role_id: int) -> bool:
        return self.delete("roles", {"id": role_id})

    # ==========================================
    # SITES (Multi-site management)
    # ==========================================
    def get_sites(self, active_only=False) -> list[dict]:
        conditions = {"is_active": True} if active_only else None
        return self.select("sites", conditions=conditions, order_by="site_name")

    def add_site(self, site_name, address="", description="", total_slots=0) -> bool:
        return self.insert("sites", {
            "site_name": site_name, "address": address,
            "description": description, "total_slots": total_slots
        }, returning="id") is not None

    def update_site(self, site_id, site_name, address, description, is_active, total_slots=None) -> bool:
        data = {
            "site_name": site_name, "address": address,
            "description": description, "is_active": is_active
        }
        if total_slots is not None:
            data["total_slots"] = total_slots
        return self.update("sites", data, {"id": site_id})

    def delete_site(self, site_id) -> bool:
        return self.delete("sites", {"id": site_id})

    def get_cameras_by_site(self, site_id) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT c.*,
                           (SELECT array_agg(s.site_name)
                            FROM sites s WHERE s.id = ANY(c.site_ids)) AS site_names
                    FROM cameras c
                    WHERE %s = ANY(c.site_ids) AND c.is_active = TRUE
                    ORDER BY c.camera_name
                """, (site_id,))
                return [dict(r) for r in cursor.fetchall()]
    # ==========================================
    # CAMERA MANAGEMENT (New table: cameras)
    # ==========================================
    def get_all_cameras(self, site_id: int | None = None) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT c.*,
                           (SELECT array_agg(s.site_name)
                            FROM sites s WHERE s.id = ANY(c.site_ids)) AS site_names
                    FROM cameras c
                    WHERE (%s IS NULL OR %s = ANY(c.site_ids))
                    ORDER BY c.camera_name
                """, (site_id, site_id))
                return [dict(r) for r in cursor.fetchall()]
    def get_active_cameras(self, site_id: int | None = None) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT c.*,
                           (SELECT array_agg(s.site_name)
                            FROM sites s WHERE s.id = ANY(c.site_ids)) AS site_names
                    FROM cameras c
                    WHERE c.is_active = TRUE
                      AND (%s IS NULL OR %s = ANY(c.site_ids))
                    ORDER BY c.camera_name
                """, (site_id, site_id))
                return [dict(r) for r in cursor.fetchall()]
    def get_camera(self, camera_id: int) -> dict:
        return self.select("cameras", conditions={"id": camera_id}, fetchall=False)

    def add_camera(self, name, cam_type, imou_device_id, rtsp_url, is_active=True, imou_channel_id="0", site_ids=None, provider_keys=None, camera_ip=None, camera_port=554):
        """Insert a new camera. Returns the new camera ID (int) or None on failure."""
        query = '''
            INSERT INTO cameras (camera_name, camera_type, imou_device_id, imou_channel_id, rtsp_url, is_active, site_ids, provider_keys, camera_ip, camera_port)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        '''
        pk_json = json.dumps(provider_keys) if provider_keys else '{}'
        dev_id = imou_device_id.strip() if imou_device_id else None
        params = (name, cam_type, dev_id, imou_channel_id, rtsp_url, is_active, site_ids, pk_json, camera_ip, camera_port)
        return self._execute_query(query, params, commit=True, return_id=True)

    def update_camera(self, camera_id, name, cam_type, imou_device_id, rtsp_url, is_active, imou_channel_id="0", site_ids=None, provider_keys=None, camera_ip=None, camera_port=None) -> bool:
        query = '''
            UPDATE cameras
            SET camera_name=%s, camera_type=%s, imou_device_id=%s, imou_channel_id=%s, rtsp_url=%s, is_active=%s, site_ids=%s, provider_keys=%s,
                camera_ip=%s, camera_port=%s, updated_at=NOW()
            WHERE id=%s
        '''
        pk_json = json.dumps(provider_keys) if provider_keys else '{}'
        dev_id = imou_device_id.strip() if imou_device_id else None
        params = (name, cam_type, dev_id, imou_channel_id, rtsp_url, is_active, site_ids, pk_json, camera_ip, camera_port, camera_id)
        return self._execute_query(query, params, commit=True) is not None

    def set_camera_streaming(self, camera_id: int, is_streaming: bool) -> bool:
        """Toggle the is_streaming flag for a camera."""
        return self.update("cameras", {"is_streaming": is_streaming}, {"id": camera_id})

    def delete_camera(self, camera_id: int) -> bool:
        return self.delete("cameras", {"id": camera_id})

    def upsert_camera_asset(self, cam_id: str, name: str, ip: str = None,
                             serial: str = None, model: str = None,
                             firmware: str = None, rtsp_url: str = None,
                             onvif_port: int = None, onvif_profiles: list = None,
                             mac: str = None) -> bool:
        """Upsert a discovered camera into the cameras_asset CMDB table."""
        import json as _json
        profiles_json = _json.dumps(onvif_profiles) if onvif_profiles else None
        query = """
            INSERT INTO cameras_asset (cam_id, name, ip, mac, serial, model, firmware, rtsp_url, onvif_port, onvif_profiles, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
            ON CONFLICT (cam_id) DO UPDATE SET
                ip = COALESCE(EXCLUDED.ip, cameras_asset.ip),
                mac = COALESCE(EXCLUDED.mac, cameras_asset.mac),
                serial = COALESCE(EXCLUDED.serial, cameras_asset.serial),
                model = COALESCE(EXCLUDED.model, cameras_asset.model),
                firmware = COALESCE(EXCLUDED.firmware, cameras_asset.firmware),
                name = COALESCE(EXCLUDED.name, cameras_asset.name),
                rtsp_url = COALESCE(EXCLUDED.rtsp_url, cameras_asset.rtsp_url),
                onvif_port = COALESCE(EXCLUDED.onvif_port, cameras_asset.onvif_port),
                onvif_profiles = COALESCE(EXCLUDED.onvif_profiles, cameras_asset.onvif_profiles),
                updated_at = NOW()
        """
        return self._execute_query(query, (cam_id, name, ip, mac, serial, model, firmware,
                                           rtsp_url, onvif_port, profiles_json), commit=True) is not None

    def get_imou_api_keys(self) -> dict:
        # Ưu tiên đọc từ provider keys (trang Quản lý Key)
        provider_keys = self.get_provider_api_keys("imou")
        app_id = provider_keys.get('open_app_id', '')
        app_secret = provider_keys.get('open_app_secret', '')

        # Fallback: đọc key cũ IMOU_APP_ID/IMOU_APP_SECRET
        if not app_id or not app_secret:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT key, value FROM app_settings WHERE key IN ('IMOU_APP_ID', 'IMOU_APP_SECRET')")
                    res = {k: v for k, v in cursor.fetchall()}
                    if not app_id:
                        app_id = res.get('IMOU_APP_ID', '')
                    if not app_secret:
                        app_secret = res.get('IMOU_APP_SECRET', '')

        return {'app_id': app_id, 'app_secret': app_secret}
    def update_imou_api_keys(self, app_id: str, app_secret: str) -> bool:
        q1 = '''INSERT INTO app_settings (key, value) VALUES ('IMOU_APP_ID', %s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()'''
        q2 = '''INSERT INTO app_settings (key, value) VALUES ('IMOU_APP_SECRET', %s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()'''
        
        ok1 = self._execute_query(q1, (app_id,), commit=True) is not None
        ok2 = self._execute_query(q2, (app_secret,), commit=True) is not None
        return ok1 and ok2

    def get_provider_api_keys(self, provider: str) -> dict:
        """Get API keys for a camera provider (imou, hikvision, dahua, etc.)."""
        prefix = f"{provider.upper()}_"
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT key, value FROM app_settings WHERE key LIKE %s", (prefix + '%',))
                result = {}
                for k, v in cursor.fetchall():
                    short_key = k[len(prefix):].lower()
                    result[short_key] = v or ''
                return result
    def update_provider_api_keys(self, provider: str, data: dict) -> bool:
        """Upsert API keys for a camera provider and delete removed ones."""
        prefix = f"{provider.upper()}_"
        new_keys = {f"{prefix}{k.upper()}": v for k, v in data.items()}
        
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Delete old keys with prefix that are not in the new set
                if new_keys:
                    cursor.execute(
                        "DELETE FROM app_settings WHERE key LIKE %s AND key NOT IN %s",
                        (prefix + '%', tuple(new_keys.keys()))
                    )
                else:
                    cursor.execute("DELETE FROM app_settings WHERE key LIKE %s", (prefix + '%',))
                
                # 2. Upsert new keys
                for k, v in new_keys.items():
                    cursor.execute(
                        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                        (k, v)
                    )
            conn.commit()
        return True

    def get_app_setting(self, key: str, default: str = None) -> str:
        """Get a single setting from app_settings."""
        row = self._execute_query("SELECT value FROM app_settings WHERE key = %s", (key,), fetch=True)
        return row['value'] if row else default

    def get_all_app_settings(self) -> dict:
        """Get all key/value pairs from app_settings."""
        rows = self._execute_query("SELECT key, value FROM app_settings ORDER BY key", fetchall=True)
        return {r['key']: r['value'] for r in rows}

    def set_app_setting(self, key: str, value: str) -> bool:
        """Upsert a single setting into app_settings."""
        return self.set_many_app_settings({key: value})

    def set_many_app_settings(self, settings: dict) -> bool:
        """Upsert multiple settings into app_settings."""
        if not settings:
            return True
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                for k, v in settings.items():
                    cursor.execute(
                        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                        (k, v)
                    )
            conn.commit()
        return True

    def get_landing_settings(self) -> dict:
        """
        Batch-get tất cả LANDING_* keys từ app_settings.
        Return: dict với keys như LANDING_PHONE_WAREHOUSE, LANDING_ZALO_WAREHOUSE, etc.
        Nếu key không tồn tại → value là None (template sẽ handle).
        Không throw exception — return empty dict nếu query fail.
        """
        try:
            rows = self._execute_query(
                "SELECT key, value FROM app_settings WHERE key LIKE 'LANDING_%'",
                fetchall=True,
                commit=False,
            )
            if rows is None:
                return {}
            return {row["key"]: row["value"] for row in rows}
        except Exception as e:
            logger.warning("get_landing_settings: query failed: %s", e)
            return {}

    def get_projects(self, limit: int = 10) -> list:
        """Fetch projects for landing page, ordered by newest first."""
        query = """
            SELECT id, name, location, area, height, bays, load_capacity, image, description
            FROM projects
            ORDER BY created_at DESC
            LIMIT %s
        """
        return self._execute_query(query, (limit,), fetchall=True) or []

    def get_news(self, limit: int = 5) -> list:
        """Fetch latest news items, with publish_date serialized to ISO string."""
        query = """
            SELECT id, title, category, summary, image, publish_date
            FROM news
            ORDER BY publish_date DESC
            LIMIT %s
        """
        rows = self._execute_query(query, (limit,), fetchall=True) or []
        for row in rows:
            if row.get("publish_date") and not isinstance(row["publish_date"], str):
                row["publish_date"] = row["publish_date"].isoformat()
        return rows

    def get_news_by_id(self, news_id: int) -> dict | None:
        """Fetch a single published news item by ID."""
        rows = self._execute_query(
            "SELECT id, title, category, summary, content, image, publish_date FROM news WHERE id = %s AND is_published IS DISTINCT FROM false",
            (news_id,), fetchall=True
        ) or []
        if not rows:
            return None
        item = dict(rows[0])
        if item.get("publish_date") and hasattr(item["publish_date"], "strftime"):
            item["publish_date_str"] = item["publish_date"].strftime("%d/%m/%Y")
        else:
            item["publish_date_str"] = str(item.get("publish_date", ""))
        return item

    def save_inquiry(self, full_name: str, phone: str, company_name: str = "", demand: str = "", source_page: str = "home") -> int | None:
        row = self._execute_query(
            "INSERT INTO inquiries (full_name, phone, company_name, demand, source_page) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (full_name, phone, company_name, demand, source_page),
            fetch=True,
        )
        return row["id"] if row else None

    def get_inquiries(self, limit: int = 100, offset: int = 0, unread_only: bool = False) -> list:
        where = "WHERE is_read = FALSE" if unread_only else ""
        rows = self._execute_query(
            f"SELECT * FROM inquiries {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
            fetchall=True,
            commit=False,
        )
        return rows or []

    def count_unread_inquiries(self) -> int:
        row = self._execute_query(
            "SELECT COUNT(*) AS cnt FROM inquiries WHERE is_read = FALSE",
            fetch=True,
            commit=False,
        )
        return row["cnt"] if row else 0

    def mark_inquiry_read(self, inquiry_id: int) -> bool:
        return bool(self._execute_query(
            "UPDATE inquiries SET is_read = TRUE WHERE id = %s",
            (inquiry_id,),
        ))

    def update_inquiry_note(self, inquiry_id: int, note: str) -> bool:
        return bool(self._execute_query(
            "UPDATE inquiries SET note = %s WHERE id = %s",
            (note, inquiry_id),
        ))

    def mark_all_inquiries_read(self) -> bool:
        return bool(self._execute_query("UPDATE inquiries SET is_read = TRUE WHERE is_read = FALSE"))

    def delete_inquiry(self, inquiry_id: int) -> bool:
        return bool(self._execute_query(
            "DELETE FROM inquiries WHERE id = %s",
            (inquiry_id,),
        ))

    def get_camera_rtsp_url(self, camera_id: int, subtype: int = 1) -> str | None:
        """
        Resolve the RTSP URL for a camera.
        Priority:
        1. ONVIF-detected URL (highest trust — camera reported its own URL)
        2. Manual rtsp_url (if valid, non-placeholder)
        3. Generate from provider's rtsp_template
        """
        import urllib.parse

        cam = self.get_camera(camera_id)
        if not cam:
            return None

        # Priority 1: ONVIF-detected RTSP URL
        onvif_url = cam.get('onvif_rtsp_detected')
        if onvif_url and onvif_url.startswith("rtsp://"):
            return onvif_url

        # Priority 2: Manual URL if already valid
        manual_url = cam.get('rtsp_url')
        is_placeholder = "admin:password@192.168.1.100" in (manual_url or "")
        is_broken = "@@" in (manual_url or "")

        if manual_url and manual_url.startswith("rtsp://") and not is_placeholder and not is_broken:
            return manual_url

        # Priority 3: Generate from template
        ip = cam.get('camera_ip')
        if not ip:
            logger.warning(f"Camera {camera_id} has no IP configured. Cannot generate RTSP URL.")
            return None

        cam_type = cam.get('camera_type', 'rtsp')
        provider = self.get_provider(cam_type)
        template = (provider.get('rtsp_template') if provider else None) or \
            'rtsp://{user}:{password}@{ip}:{port}/stream'

        provider_keys = cam.get('provider_keys') or {}
        if isinstance(provider_keys, str):
            import json
            provider_keys = json.loads(provider_keys)

        user = provider_keys.get('user', 'admin')

        mk = provider_keys.get('mk')
        if not mk:
            mk = self.get_app_setting(f"{cam_type.upper()}_MK", "")
        if not mk:
            mk = self.get_app_setting("IMOU_MK", "")
        if not mk:
            logger.warning(f"Camera {camera_id} has no password configured. Cannot generate RTSP URL.")
            return None

        encoded_mk = urllib.parse.quote(mk, safe='')

        port = cam.get('camera_port') or 554
        channel = cam.get('imou_channel_id') or '1'
        global_subtype = self.get_app_setting("IMOU_SUBTYPE", str(subtype))

        try:
            return template.format(
                user=user,
                password=encoded_mk,
                ip=ip,
                port=port,
                channel=channel,
                subtype=global_subtype
            )
        except KeyError as e:
            logger.error(f"RTSP template format error for camera {camera_id}: {e}")
            return f"rtsp://{user}:{encoded_mk}@{ip}:{port}/stream"

    def store_onvif_detected_rtsp(self, camera_id: int, rtsp_url: str) -> bool:
        """Store the ONVIF-detected RTSP URL in cameras table (highest priority source)."""
        return self._execute_query(
            "UPDATE cameras SET onvif_rtsp_detected = %s, updated_at = NOW() WHERE id = %s",
            (rtsp_url, camera_id), commit=True
        ) is not None

    def regenerate_rtsp_url(self, camera_id: int) -> bool:
        """Clear stored rtsp_url AND onvif_rtsp_detected so get_camera_rtsp_url() regenerates from template."""
        return self._execute_query(
            "UPDATE cameras SET rtsp_url = NULL, onvif_rtsp_detected = NULL, updated_at = NOW() WHERE id = %s",
            (camera_id,), commit=True
        ) is not None

    # --- Camera Providers ---

    def get_all_providers(self) -> list:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM camera_providers ORDER BY name")
                return [dict(r) for r in cursor.fetchall()]
    def get_provider(self, provider_key: str) -> dict | None:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM camera_providers WHERE provider_key = %s", (provider_key,))
                row = cursor.fetchone()
                return dict(row) if row else None
    def upsert_provider(self, provider_key: str, name: str, api_base_url: str = None,
                        api_doc_url: str = None, rtsp_template: str = None,
                        onvif_port: int = 80, default_rtsp_port: int = 554,
                        default_http_port: int = 80, notes: str = None,
                        logo_url: str = None, is_active: bool = True) -> bool:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO camera_providers
                        (provider_key, name, api_base_url, api_doc_url, rtsp_template,
                         onvif_port, default_rtsp_port, default_http_port, notes, logo_url, is_active, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (provider_key) DO UPDATE SET
                        name=EXCLUDED.name, api_base_url=EXCLUDED.api_base_url,
                        api_doc_url=EXCLUDED.api_doc_url, rtsp_template=EXCLUDED.rtsp_template,
                        onvif_port=EXCLUDED.onvif_port, default_rtsp_port=EXCLUDED.default_rtsp_port,
                        default_http_port=EXCLUDED.default_http_port, notes=EXCLUDED.notes,
                        logo_url=EXCLUDED.logo_url, is_active=EXCLUDED.is_active, updated_at=NOW()
                """, (provider_key, name, api_base_url, api_doc_url, rtsp_template,
                      onvif_port, default_rtsp_port, default_http_port, notes, logo_url, is_active))
            conn.commit()
        return True
    def delete_provider(self, provider_key: str) -> bool:
        return self._execute_query(
            "DELETE FROM camera_providers WHERE provider_key = %s", (provider_key,), commit=True
        ) is not None

    # ==========================================
    # DOOR PROVIDERS
    # ==========================================

    def get_all_door_providers(self) -> list:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM door_providers ORDER BY name")
                return [dict(r) for r in cursor.fetchall()]
    def get_door_provider(self, provider_key: str) -> dict | None:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM door_providers WHERE provider_key = %s", (provider_key,))
                row = cursor.fetchone()
                return dict(row) if row else None
    def upsert_door_provider(self, provider_key, name, connection_type=None, api_doc_url=None,
                              config_template=None, notes=None, logo_url=None, is_active=True) -> bool:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO door_providers
                        (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (provider_key) DO UPDATE SET
                        name=EXCLUDED.name, connection_type=EXCLUDED.connection_type,
                        api_doc_url=EXCLUDED.api_doc_url, config_template=EXCLUDED.config_template,
                        notes=EXCLUDED.notes, logo_url=EXCLUDED.logo_url, is_active=EXCLUDED.is_active, updated_at=NOW()
                """, (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active))
                conn.commit()
                return True
    def delete_door_provider(self, provider_key: str) -> bool:
        return self._execute_query(
            "DELETE FROM door_providers WHERE provider_key = %s", (provider_key,), commit=True
        ) is not None

    # ==========================================
    # PENDING PLATES (simple)
    # ==========================================

    def add_pending_plate_simple(self, plate_norm: str, plate_raw: str, camera_id: str = None, site_id: int = None, image_path: str = None, plate_type: str = "white"):
        """Quick-add unknown plate to pending list with auto-generated ID, with deduplication."""
        # 1. Deduplication: Don't add if already in pending list (Separator-insensitive)
        # Normalize input to alphanumeric only for lookup
        clean_plate = re.sub(r'[^A-Z0-9]', '', plate_norm.upper())
        
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # Use regexp_replace to match stored plate_norm robustly
                cursor.execute("""
                    SELECT pending_id FROM pending_plates 
                    WHERE regexp_replace(plate_norm, '[^a-zA-Z0-9]', '', 'g') = %s 
                    AND LOWER(TRIM(status)) = 'pending' 
                    LIMIT 1
                """, (clean_plate,))
                
                if cursor.fetchone():
                    logger.info(f"Plate {plate_norm} already pending (normalized match: {clean_plate}). Skipping insertion.")
                    return

                # 2. Insert new record
                pid = str(uuid.uuid4())[:8]
                logger.info(f"Inserting new unknown plate: {plate_norm} (site: {site_id}, cam: {camera_id})")
                cursor.execute(
                    """INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type)
                       VALUES (%s, %s, %s, NOW(), 'pending', %s, %s, %s)
                       ON CONFLICT (pending_id) DO NOTHING""",
                    (pid, plate_raw, plate_norm, site_id, image_path, plate_type)
                )
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Successfully added pending plate {plate_norm} with ID {pid}")
                else:
                    logger.warning(f"Conflicting pending ID {pid}? No row inserted for {plate_norm}")
    # ==========================================
    # OCR TRAINING SAMPLES
    # ==========================================

    def save_ocr_training_sample(self, image_path: str, ocr_text: str, confidence: float, camera_id: str = None):
        self._execute_query(
            """INSERT INTO ocr_training_samples (plate_image_path, ocr_text, confidence, camera_id)
               VALUES (%s, %s, %s, %s)""",
            (image_path, ocr_text, confidence, camera_id), commit=True
        )

    def get_ocr_training_samples(self, status: str = None, limit: int = 50) -> list:
        if status:
            return self._execute_query(
                "SELECT * FROM ocr_training_samples WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                (status, limit), fetchall=True
            ) or []
        return self._execute_query(
            "SELECT * FROM ocr_training_samples ORDER BY created_at DESC LIMIT %s",
            (limit,), fetchall=True
        ) or []

    def review_ocr_sample(self, sample_id: int, corrected_text: str, reviewed_by: str, status: str = "approved"):
        self._execute_query(
            """UPDATE ocr_training_samples
               SET corrected_text = %s, reviewed_by = %s, reviewed_at = NOW(), status = %s
               WHERE id = %s""",
            (corrected_text, reviewed_by, status, sample_id), commit=True
        )

    def delete_ocr_sample(self, sample_id: int):
        self._execute_query("DELETE FROM ocr_training_samples WHERE id = %s", (sample_id,), commit=True)
    def log_stranger(self, camera_id: str, similarity: float, image_path: str,
                     site_id: int | None = None, face_embedding: bytes | None = None,
                     direction: str = "frontal") -> int | None:
        """Insert a stranger detection event. Returns the new row id."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO stranger_logs
                           (camera_id, similarity, image_path, site_id, face_embedding, status, direction, event_time)
                       VALUES (%s, %s, %s, %s, %s, 'new', %s, NOW())
                       RETURNING id''',
                    (camera_id, similarity, image_path, site_id, face_embedding, direction)
                )
                row_id = cursor.fetchone()[0]
            conn.commit()
            return row_id
    def get_stranger_logs(self, status: str | None = None, limit: int = 50, site_id: int | None = None) -> list:
        """Return stranger logs with site name and converted username.
        Optionally filter by status ('new', 'reviewed', 'converted') and site_id."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                params: list = []
                where_clauses = []
                if status:
                    where_clauses.append("LOWER(TRIM(sl.status)) = LOWER(%s)")
                    params.append(status)
                if site_id:
                    # Show site-specific AND global (NULL) stranger logs
                    where_clauses.append("(sl.site_id = %s OR sl.site_id IS NULL)")
                    params.append(site_id)
                
                where = ""
                if where_clauses:
                    where = "WHERE " + " AND ".join(where_clauses)
                
                params.append(limit)
                cursor.execute(f"""
                    SELECT sl.*,
                           s.site_name,
                           u.username AS converted_username
                    FROM stranger_logs sl
                    LEFT JOIN sites s ON sl.site_id = s.id
                    LEFT JOIN users u ON sl.converted_user_id = u.id
                    {where}
                    ORDER BY sl.event_time DESC
                    LIMIT %s
                """, params)
                return [dict(r) for r in cursor.fetchall()]

    def get_stranger_log(self, stranger_id: int):
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT sl.*, s.site_name, u.username as converted_username
                    FROM stranger_logs sl
                    LEFT JOIN sites s ON sl.site_id = s.id
                    LEFT JOIN users u ON sl.converted_user_id = u.id
                    WHERE sl.id = %s
                """, (stranger_id,))
                res = cursor.fetchone()
                return dict(res) if res else None

    def update_stranger_status(self, stranger_id: int, status: str,
                               converted_user_id: int | None = None, note: str | None = None) -> bool:
        """Update status (and optionally converted_user_id / note) for a stranger log row."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''UPDATE stranger_logs
                       SET status = %s,
                           converted_user_id = COALESCE(%s, converted_user_id),
                           note = COALESCE(%s, note)
                       WHERE id = %s''',
                    (status, converted_user_id, note, stranger_id)
                )
            conn.commit()
            return True
    def delete_stranger_log(self, stranger_id: int) -> bool:
        """Hard-delete a single stranger log row."""
        return self.delete("stranger_logs", {"id": stranger_id})

    def generate_unique_username(self, prefix: str = "user") -> str:
        """Return the next available username in the form prefix_001, prefix_002, …"""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT username FROM users WHERE username LIKE %s ORDER BY username",
                    (f"{prefix}_%",)
                )
                existing = {row[0] for row in cursor.fetchall()}
        counter = 1
        while counter < 10000:
            candidate = f"{prefix}_{counter:03d}"
            if candidate not in existing:
                return candidate
            counter += 1
        return f"{prefix}_{int(time.time())}"
    # ==========================================
    # SMART PARKING ENGINE
    # ==========================================
    def record_vehicle_entry(self, plate_norm, vehicle_type, camera_id, image_path, site_id=None, plate_type="white") -> bool:
        """Records a vehicle entering the parking site."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM vehicle_sessions WHERE plate_norm = %s AND status = 'parking' LIMIT 1", (plate_norm,))
                active = cursor.fetchone()

                if active:
                    cursor.execute("""
                        UPDATE vehicle_sessions
                        SET entry_time = NOW(), camera_id_in = %s, entry_image = %s, site_id = %s
                        WHERE id = %s
                    """, (camera_id, image_path, site_id, active[0]))
                else:
                    cursor.execute("""
                        INSERT INTO vehicle_sessions (plate_norm, vehicle_type, camera_id_in, entry_image, site_id, status)
                        VALUES (%s, %s, %s, %s, %s, 'parking')
                    """, (plate_norm, vehicle_type, camera_id, image_path, site_id))
                conn.commit()
                return True
    def record_vehicle_exit(self, plate_norm, camera_id, image_path) -> dict | None:
        """Records a vehicle exiting and calculates duration/fee."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT * FROM vehicle_sessions
                    WHERE plate_norm = %s AND status = 'parking'
                    ORDER BY entry_time DESC LIMIT 1
                """, (plate_norm,))
                session = cursor.fetchone()

                if not session:
                    return None

                session = dict(session)
                exit_time = datetime.now()
                entry_time = session['entry_time']
                # Both are naive timestamps, safe to subtract directly
                duration = (exit_time - entry_time).total_seconds()

                fee = 0.0

                cursor.execute("""
                    UPDATE vehicle_sessions
                    SET exit_time = NOW(),
                        camera_id_out = %s,
                        exit_image = %s,
                        status = 'completed',
                        stay_duration = %s,
                        fee = %s
                    WHERE id = %s
                """, (camera_id, image_path, int(duration), fee, session['id']))
                conn.commit()

                session['exit_time'] = exit_time
                session['stay_duration'] = int(duration)
                session['fee'] = fee
                return session
    def get_occupancy_stats(self, site_id=None) -> list[dict]:
        """Returns occupancy stats per site, optionally filtered by site."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT
                        s.id,
                        s.site_name,
                        s.total_slots,
                        COUNT(vs.id) FILTER (WHERE vs.status = 'parking') AS occupied_slots
                    FROM sites s
                    LEFT JOIN vehicle_sessions vs ON s.id = vs.site_id
                """
                params = []
                if site_id is not None:
                    query += " WHERE s.id = %s"
                    params.append(site_id)
                query += " GROUP BY s.id, s.site_name, s.total_slots ORDER BY s.site_name"
                cursor.execute(query, params)
                return [dict(r) for r in cursor.fetchall()]
    def get_parking_history(self, limit=50) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT vs.*, s.site_name, c_in.camera_name as cam_in, c_out.camera_name as cam_out
                    FROM vehicle_sessions vs
                    LEFT JOIN sites s ON vs.site_id = s.id
                    LEFT JOIN cameras c_in ON vs.camera_id_in = c_in.id
                    LEFT JOIN cameras c_out ON vs.camera_id_out = c_out.id
                    ORDER BY vs.entry_time DESC
                    LIMIT %s
                """, (limit,))
                return [dict(r) for r in cursor.fetchall()]
    def get_whitelist_vehicles(self, site_id=None, limit=None, offset=None) -> list[dict]:
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT w.*,
                           (SELECT array_agg(s.site_name)
                            FROM sites s WHERE s.id IN (SELECT value::int FROM jsonb_array_elements_text(COALESCE(w.site_ids, '[]'::jsonb)))) AS site_names
                    FROM plate_whitelist w
                """
                params = []
                if site_id:
                    query += " WHERE (w.site_ids @> %s::jsonb OR w.site_ids = '[]'::jsonb OR w.site_ids IS NULL)"
                    params.append(json.dumps([site_id]))
                query += " ORDER BY w.created_at DESC"
                if limit is not None:
                    query += " LIMIT %s"
                    params.append(limit)
                if offset is not None:
                    query += " OFFSET %s"
                    params.append(offset)
                cursor.execute(query, params)
                return [dict(r) for r in cursor.fetchall()]
    # Personnel-related queries now use unified user queries or specific filters
    def get_whitelist_people(self, site_id=None) -> list[dict]:
        """Retrieves personnel from the users table. Used by UI components."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = "SELECT u.id, u.username, u.full_name, u.person_identity, u.note, u.site_ids FROM users u"
                params = []
                if site_id:
                    query += " WHERE %s = ANY(u.site_ids)"
                    params.append(site_id)
                else:
                    query += " WHERE u.person_identity IS NOT NULL"

                query += " ORDER BY u.created_at DESC"
                cursor.execute(query, params)
                return [dict(r) for r in cursor.fetchall()]
    def delete_whitelist_person(self, identity, site_id=None):
        """Removes a specific site from a person or deletes them if personnel-only."""
        if site_id:
            return self._execute_query("UPDATE users SET site_ids = array_remove(site_ids, %s) WHERE person_identity = %s", (site_id, identity), commit=True) is not None
        else:
            return self.delete("users", {"person_identity": identity})
    def delete_whitelist_vehicle(self, plate_number, site_id=None):
        """Removes a vehicle from the whitelist."""
        if site_id:
            # Remove the site_id from the JSONB array
            res = self._execute_query(
                "UPDATE plate_whitelist SET site_ids = site_ids - %s::text WHERE plate_number = %s",
                (str(site_id), plate_number), commit=True) is not None
            if res: self._trigger_ai_reload()
            return res
        else:
            res = self.delete("plate_whitelist", {"plate_number": plate_number})
            if res: self._trigger_ai_reload()
            return res

    # --- Rolling Doors ---

    def get_all_rolling_doors(self) -> list:
        query = """
            SELECT rd.*, dp.name as supplier_name, s.site_name 
            FROM rolling_doors rd
            LEFT JOIN door_providers dp ON rd.supplier_key = dp.provider_key
            LEFT JOIN sites s ON rd.site_id = s.id
            ORDER BY dp.name ASC, rd.name ASC
        """
        return self._execute_query(query, fetchall=True, commit=False)

    def get_rolling_door(self, door_id: int) -> dict:
        return self.select('rolling_doors', conditions={'id': door_id}, fetchall=False)

    def upsert_rolling_door(self, **data):
        door_id = data.pop('id', None)
        if door_id:
            return self.update('rolling_doors', data, {'id': door_id})
        return self.insert('rolling_doors', data)

    def delete_rolling_door(self, door_id: int):
        return self.delete('rolling_doors', {'id': door_id})

    def get_doors_for_auto_close(self) -> list:
        """Get all rolling doors with auto_close_enabled and a linked camera."""
        query = """
            SELECT rd.id, rd.name, rd.camera_id, rd.auto_close_delay_sec,
                   rd.relay_type, rd.relay_config, rd.site_id
            FROM rolling_doors rd
            WHERE rd.auto_close_enabled = TRUE AND rd.camera_id IS NOT NULL AND rd.is_active = TRUE
        """
        return self._execute_query(query, fetchall=True, commit=False) or []

    def log_door_event(self, door_id: int, event_type: str, notes: str = None) -> bool:
        """Log a door control event."""
        query = """
            INSERT INTO camera_status_log (camera_id, status, event_type, notes, logged_at)
            VALUES (%s, %s, %s, %s, NOW())
        """
        return self._execute_query(
            query, (str(door_id), event_type, event_type, notes), commit=True
        ) is not None
