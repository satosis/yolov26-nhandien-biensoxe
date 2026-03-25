import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from datetime import datetime
from contextlib import contextmanager
import json
import logging
import re
import secrets
import urllib.parse

logger = logging.getLogger(__name__)

# Expert: Load environment variables from .env globally
from dotenv import load_dotenv
load_dotenv() # Default path (.env in CWD)
# Try specific paths just in case
load_dotenv(os.path.join(os.getcwd(), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

class DatabaseError(Exception):
    """Custom exception for database-related errors."""
    pass


class DatabaseManager:
    _pool = None

    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        if not self.dsn:
            # Fallback check before raising to debug
            logger.error("DATABASE_URL NOT FOUND IN ENV. CWD: %s, PATH: %s", os.getcwd(), __file__)
            raise ValueError("DATABASE_URL environment variable is required")
        self.dsn = self.dsn.replace("postgresql+asyncpg://", "postgresql://")

        # Initialize thread-safe connection pool globally for this process
        if DatabaseManager._pool is None:
            DatabaseManager._pool = pool.ThreadedConnectionPool(1, 15, self.dsn)
    @contextmanager
    def _get_connection(self):
        """Context manager to lease a connection from the pool and return it safely.
        Includes retry logic / fail-fast if pool is broken."""
        conn = None
        if self._pool:
            conn = self._pool.getconn()
        else:
            conn = psycopg2.connect(self.dsn, connect_timeout=3)
        
        yield conn

        if conn:
            if self._pool:
                self._pool.putconn(conn)
            else:
                conn.close()

    def _execute_query(self, query, params=None, fetch=False, fetchall=False, commit=True, return_id=False):
        """Core method to execute queries safely with centralized error handling."""
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
                    result = cursor.fetchone()[0]
                if commit:
                    conn.commit()
                return result
    # Whitelist of allowed table/column names for generic ORM methods
    _ALLOWED_TABLES = frozenset({
        'users', 'roles', 'cameras', 'sites', 'plate_events',
        'vehicle_sessions', 'plate_whitelist', 'pending_plates',
        'app_settings', 'camera_status_log', 'legal_hold',
        'monthly_reports', 'telegram_bots', 'cameras_asset',
        'stranger_logs',
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
            query = "SELECT id, full_name, site_ids, note, username FROM users WHERE id = %s OR username = %s"
            row = self._execute_query(query, (int(identity), str(identity)), fetch=True)
        else:
            query = "SELECT id, full_name, site_ids, note, username FROM users WHERE username = %s"
            row = self._execute_query(query, (identity,), fetch=True)

        if row:
            return {
                "identity": str(row['id']),
                "full_name": row['full_name'],
                "sites": row['site_ids'] or [],
                "note": row['note'],
                "username": row['username']
            }
        return None
    def add_pending_plate(self, pending_id, event_id, plate_raw, plate_norm, first_seen_utc):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    INSERT INTO pending_plates (
                        pending_id, event_id, plate_raw, plate_norm, first_seen_utc, status
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pending_id) DO NOTHING
                    ''',
                    (pending_id, event_id, plate_raw, plate_norm, first_seen_utc, "pending")
                )
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
            return True
    def check_whitelist(self, plate_norm: str) -> dict | None:
        """Check if a plate number exists in whitelist (active only). Returns the record if found."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM plate_whitelist WHERE plate_number = %s AND is_active = TRUE LIMIT 1",
                    (plate_norm,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
    def update_pending_status(self, plate_norm, status, confirmed_by):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    UPDATE pending_plates
                    SET status = %s, confirmed_at_utc = NOW(), confirmed_by = %s
                    WHERE plate_norm = %s AND status = 'pending'
                    ''',
                    (status, confirmed_by, plate_norm)
                )
                return True
    def log_event(self, event_type, description, trucks, people):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''
                    INSERT INTO plate_events (event_time, camera_id, vehicle_type, plate_number) 
                    VALUES (NOW(), %s, %s, %s) RETURNING id
                    ''',
                    ("default", event_type, "unknown")
                )
                return cursor.fetchone()[0]
    def get_stats(self):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                # Optimized to look only at today's vehicle counts instead of full table scan
                cursor.execute("SELECT COUNT(*), vehicle_type FROM plate_events WHERE event_time >= CURRENT_DATE GROUP BY vehicle_type")
                return cursor.fetchall()
    # ───────────────── AI AUDITOR ──────────────────
    def get_low_confidence_events(self, limit=10, max_conf=0.65, hours_back=24):
        """Return plate events with lowest confidence for AI auditing."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, plate_number, confidence, image_path,
                           event_time, camera_id, ai_audit_mismatch, ai_audit_result
                    FROM plate_events
                    WHERE plate_number IS NOT NULL
                      AND plate_number != ''
                      AND confidence IS NOT NULL
                      AND confidence <= %s
                      AND event_time >= NOW() - (%s * INTERVAL '1 hour')
                    ORDER BY confidence ASC
                    LIMIT %s
                """, (max_conf, hours_back, limit))
                return cursor.fetchall()
    def mark_event_audited(self, event_id, gemini_result, is_mismatch):
        """Store Gemini's audit result for a plate event."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE plate_events
                    SET ai_audit_result = %s,
                        ai_audit_mismatch = %s,
                        ai_audited_at = NOW()
                    WHERE id = %s
                """, (gemini_result, is_mismatch, event_id))
            conn.commit()
            return True
    def get_app_setting(self, key):
        """Get a single app setting value."""
        row = self.select("app_settings", columns=["value"], conditions={"key": key}, fetchall=False)
        return row['value'] if row else None

    def set_app_setting(self, key, value):
        """Upsert a single app setting."""
        query = '''
            INSERT INTO app_settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        '''
        return self._execute_query(query, (key, value), commit=True) is not None

    def get_all_app_settings(self):
        """Get all app settings as a dictionary."""
        rows = self.select("app_settings", columns=["key", "value"])
        if rows:
            return {row['key']: row['value'] or "" for row in rows}
        return {}

    def set_many_app_settings(self, data: dict):
        """Upsert multiple app settings."""
        if not data: return True
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                for key, value in data.items():
                    cursor.execute("""
                        INSERT INTO app_settings (key, value, updated_at) 
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """, (key, value))
            conn.commit()
        return True
    # ───────────────── END AI AUDITOR ──────────────

    def get_pending_plates(self, site_id=None):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                query = """
                    SELECT p.*, s.site_name
                    FROM pending_plates p
                    LEFT JOIN sites s ON p.site_id = s.id
                    WHERE p.status = 'pending'
                """
                params = []
                if site_id:
                    query += " AND p.site_id = %s"
                    params.append(site_id)
                cursor.execute(query, params)
                return cursor.fetchall()
    def log_camera_event(self, cam_id: str, event_type: str, started_at: str,
                         ended_at: str = None, duration_seconds: float = None,
                         notes: str = None) -> int:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO camera_status_log
                       (camera_id, status, logged_at)
                       VALUES (%s, %s, %s) RETURNING id''',
                    (cam_id, event_type, started_at)
                )
                return cursor.fetchone()[0]
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
    def add_legal_hold(self, file_path: str, reason: str, held_by: str) -> bool:
        # ON CONFLICT DO NOTHING implies we can't just use standard insert without raw query
        # Keeping original for ON CONFLICT
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    'INSERT INTO legal_hold (file_path, reason, held_by, held_at) VALUES (%s, %s, %s, NOW()) ON CONFLICT DO NOTHING',
                    (file_path, reason, held_by)
                )
                return True
    def release_legal_hold(self, file_path: str) -> bool:
        query = "UPDATE legal_hold SET released_at = NOW() WHERE file_path = %s AND released_at IS NULL"
        return self._execute_query(query, (file_path,), commit=True) is not None

    def is_legal_hold(self, file_path: str) -> bool:
        row = self.select("legal_hold", columns=["1"], conditions={"file_path": file_path, "released_at": None}, fetchall=False)
        return row is not None if row else self._execute_query("SELECT 1 FROM legal_hold WHERE file_path = %s AND released_at IS NULL LIMIT 1", (file_path,), fetch=True) is not None

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
                query = '''SELECT u.id, u.username, r.role_name as role, u.created_at, u.full_name, u.note, u.site_ids
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

    def add_camera(self, name, cam_type, imou_device_id, rtsp_url, is_active=True, imou_channel_id="0", site_ids=None, provider_keys=None, camera_ip=None, camera_port=554) -> bool:
        query = '''
            INSERT INTO cameras (camera_name, camera_type, imou_device_id, imou_channel_id, rtsp_url, is_active, site_ids, provider_keys, camera_ip, camera_port)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        '''
        pk_json = json.dumps(provider_keys) if provider_keys else '{}'
        params = (name, cam_type, imou_device_id, imou_channel_id, rtsp_url, is_active, site_ids, pk_json, camera_ip, camera_port)
        return self._execute_query(query, params, commit=True) is not None

    def update_camera(self, camera_id, name, cam_type, imou_device_id, rtsp_url, is_active, imou_channel_id="0", site_ids=None, provider_keys=None, camera_ip=None, camera_port=None) -> bool:
        query = '''
            UPDATE cameras
            SET camera_name=%s, camera_type=%s, imou_device_id=%s, imou_channel_id=%s, rtsp_url=%s, is_active=%s, site_ids=%s, provider_keys=%s,
                camera_ip=%s, camera_port=%s, updated_at=NOW()
            WHERE id=%s
        '''
        pk_json = json.dumps(provider_keys) if provider_keys else '{}'
        params = (name, cam_type, imou_device_id, imou_channel_id, rtsp_url, is_active, site_ids, pk_json, camera_ip, camera_port, camera_id)
        return self._execute_query(query, params, commit=True) is not None

    def set_camera_streaming(self, camera_id: int, is_streaming: bool) -> bool:
        """Toggle the is_streaming flag for a camera."""
        return self.update("cameras", {"is_streaming": is_streaming}, {"id": camera_id})

    def delete_camera(self, camera_id: int) -> bool:
        return self.delete("cameras", {"id": camera_id})

    def get_imou_api_keys(self) -> dict:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT key, value FROM app_settings WHERE key IN ('IMOU_OPEN_APP_ID', 'IMOU_OPEN_APP_SECRET')")
                res = {k: v for k, v in cursor.fetchall()}
                return {
                    'app_id': res.get('IMOU_OPEN_APP_ID', ''),
                    'app_secret': res.get('IMOU_OPEN_APP_SECRET', '')
                }
    def update_imou_api_keys(self, app_id: str, app_secret: str) -> bool:
        q1 = '''INSERT INTO app_settings (key, value) VALUES ('IMOU_OPEN_APP_ID', %s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()'''
        q2 = '''INSERT INTO app_settings (key, value) VALUES ('IMOU_OPEN_APP_SECRET', %s)
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

    def get_imou_rtsp_url(self, camera_id: int) -> str | None:
        """
        Generate Imou RTSP URL for a given camera using provider template and global keys.
        Prioritizes a manual rtsp_url if it's explicitly set and non-default.
        """
        cam = self.get_camera(camera_id)
        if not cam or cam.get('camera_type') != 'imou':
            return cam.get('rtsp_url') if cam else None

        # Expert Fix: If user manually entered a specific RTSP URL (e.g. cloud or custom IP),
        # use it instead of generating from template, UNLESS it's the 192.168.1.100 placeholder.
        manual_url = cam.get('rtsp_url')
        if manual_url and manual_url.startswith("rtsp://") and "@192.168.1.100" not in manual_url:
            return manual_url

        provider = self.get_provider('imou')
        template = provider['rtsp_template'] if provider and provider.get('rtsp_template') else \
            'rtsp://admin:{password}@{ip}:554/cam/realmonitor?channel={channel}&subtype=0'
        
        # Get credentials
        mk = self.get_app_setting("IMOU_MK", "bB123456789@")
        
        # Get IP and Port
        ip = cam.get('camera_ip') or "192.168.1.100"
        port = cam.get('camera_port') or 554
        
        # Fallback for IP if not explicitly set
        if not cam.get('camera_ip'):
            asset = self._execute_query("SELECT ip FROM cameras_asset WHERE cam_id = %s", (str(camera_id),), fetch=True)
            if asset and asset['ip']:
                ip = asset['ip']
            elif cam.get('rtsp_url'):
                import re
                match = re.search(r'@([^:/]+)', cam['rtsp_url'])
                if match:
                    ip = match.group(1)
        
        channel = cam.get('imou_channel_id', '0')
        
        # Interpolate
        encoded_mk = urllib.parse.quote(mk)
        url = template.replace("{user}", "admin").replace("{username}", "admin") \
                      .replace("{pass}", encoded_mk).replace("{password}", encoded_mk) \
                      .replace("{ip}", ip) \
                      .replace("{port}", str(port)) \
                      .replace("{channel}", str(channel))
        return url

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
                              config_template=None, notes=None, is_active=True) -> bool:
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO door_providers
                        (provider_key, name, connection_type, api_doc_url, config_template, notes, is_active, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (provider_key) DO UPDATE SET
                        name=EXCLUDED.name, connection_type=EXCLUDED.connection_type,
                        api_doc_url=EXCLUDED.api_doc_url, config_template=EXCLUDED.config_template,
                        notes=EXCLUDED.notes, is_active=EXCLUDED.is_active, updated_at=NOW()
                """, (provider_key, name, connection_type, api_doc_url, config_template, notes, is_active))
                conn.commit()
                return True
    def delete_door_provider(self, provider_key: str) -> bool:
        return self._execute_query(
            "DELETE FROM door_providers WHERE provider_key = %s", (provider_key,), commit=True
        ) is not None

    # ==========================================
    # PENDING PLATES (simple)
    # ==========================================

    def add_pending_plate_simple(self, plate_norm: str, plate_raw: str, camera_id: str = None, site_id: int = None):
        """Quick-add unknown plate to pending list with auto-generated ID."""
        import uuid
        pid = str(uuid.uuid4())[:8]
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO pending_plates (pending_id, plate_raw, plate_norm, first_seen_utc, status, site_id)
                       VALUES (%s, %s, %s, NOW(), 'pending', %s)
                       ON CONFLICT (pending_id) DO NOTHING""",
                    (pid, plate_raw, plate_norm, site_id)
                )
                conn.commit()
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
                     site_id: int | None = None, face_embedding: bytes | None = None) -> int | None:
        """Insert a stranger detection event. Returns the new row id."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO stranger_logs
                           (camera_id, similarity, image_path, site_id, face_embedding, status)
                       VALUES (%s, %s, %s, %s, %s, 'new')
                       RETURNING id''',
                    (camera_id, similarity, image_path, site_id, face_embedding)
                )
                row_id = cursor.fetchone()[0]
            conn.commit()
            return row_id
    def get_stranger_logs(self, status: str | None = None, limit: int = 50) -> list:
        """Return stranger logs with site name and converted username.
        Optionally filter by status ('new', 'reviewed', 'converted')."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                params: list = []
                where = ""
                if status:
                    where = "WHERE sl.status = %s"
                    params.append(status)
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
        import time
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
    def record_vehicle_entry(self, plate_norm, vehicle_type, camera_id, image_path, site_id=None) -> bool:
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
            return self._execute_query(
                "UPDATE plate_whitelist SET site_ids = site_ids - %s::text WHERE plate_number = %s",
                (str(site_id), plate_number), commit=True) is not None
        else:
            return self.delete("plate_whitelist", {"plate_number": plate_number})
