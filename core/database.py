import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

class DatabaseManager:
    def __init__(self, dsn=None):
        # Use DATABASE_URL from env if dsn not provided
        self.dsn = dsn or os.environ.get("DATABASE_URL", "postgresql://camera_user:password@localhost:5432/camera_ai")
        # init_db is now handled by init.sql in the Postgres container.

    def _get_connection(self):
        return psycopg2.connect(self.dsn)

    def is_plate_whitelisted(self, plate_norm):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND is_active = TRUE LIMIT 1', (plate_norm,))
                    return cursor.fetchone() is not None
        except Exception as e:
            print(f"Postgres Error: {e}")
            return False

    def add_pending_plate(self, pending_id, event_id, plate_raw, plate_norm, first_seen_utc):
        try:
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
        except Exception as e:
            print(f"Postgres Error: {e}")

    def upsert_vehicle_whitelist(self, plate_norm, label, added_by, note=None):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        INSERT INTO plate_whitelist (plate_number, list_type, owner_name, added_by, note, is_active)
                        VALUES (%s, 'white', %s, %s, %s, TRUE)
                        ON CONFLICT(plate_number) DO UPDATE SET
                            owner_name=EXCLUDED.owner_name,
                            added_by=EXCLUDED.added_by,
                            note=EXCLUDED.note,
                            updated_at=NOW()
                        ''',
                        (plate_norm, label, added_by, note)
                    )
                    return True
        except Exception as e:
            print(f"Postgres Error: {e}")
            return False

    def update_pending_status(self, plate_norm, status, confirmed_by):
        try:
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
        except Exception as e:
            print(f"Postgres Error: {e}")
            return False

    def log_event(self, event_type, description, trucks, people):
        try:
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
        except Exception as e:
            print(f"Postgres Error: {e}")
            return None

    def get_stats(self):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*), vehicle_type FROM plate_events GROUP BY vehicle_type')
                    return cursor.fetchall()
        except:
            return []

    def get_pending_plates(self):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT plate_norm, plate_raw, first_seen_utc FROM pending_plates WHERE status = 'pending'")
                    return cursor.fetchall()
        except:
            return []

    def log_camera_event(self, cam_id: str, event_type: str, started_at: str,
                         ended_at: str = None, duration_seconds: float = None,
                         notes: str = None) -> int:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO camera_status_log
                           (camera_id, status, logged_at)
                           VALUES (%s, %s, %s) RETURNING id''',
                        (cam_id, event_type, started_at)
                    )
                    return cursor.fetchone()[0]
        except:
            return 0

    def get_camera_health(self, cam_id: str = None, hours: int = 24) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    if cam_id:
                        cursor.execute(
                            "SELECT * FROM camera_status_log WHERE camera_id = %s AND logged_at >= NOW() - INTERVAL '%s hours' ORDER BY logged_at DESC",
                            (cam_id, hours)
                        )
                    else:
                        cursor.execute(
                            "SELECT * FROM camera_status_log WHERE logged_at >= NOW() - INTERVAL '%s hours' ORDER BY logged_at DESC",
                            (hours,)
                        )
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def add_legal_hold(self, file_path: str, reason: str, held_by: str) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO legal_hold (file_path, reason, held_by, held_at) VALUES (%s, %s, %s, NOW()) ON CONFLICT DO NOTHING',
                        (file_path, reason, held_by)
                    )
                    return True
        except:
            return False

    def release_legal_hold(self, file_path: str) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE legal_hold SET released_at = NOW() WHERE file_path = %s AND released_at IS NULL",
                        (file_path,)
                    )
                    return True
        except:
            return False

    def is_legal_hold(self, file_path: str) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM legal_hold WHERE file_path = %s AND released_at IS NULL LIMIT 1", (file_path,))
                    return cursor.fetchone() is not None
        except:
            return False

    def upsert_sla_daily(self, report_date: str, cam_id: str, uptime_pct: float,
                         gap_count: int, gap_total_seconds: float, offline_count: int):
        # Using monthly_reports table which is the closest match in init.sql
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO monthly_reports
                           (report_type, report_date, camera_id, total_vehicles)
                           VALUES ('daily', %s, %s, %s)
                           ON CONFLICT(report_type, report_date, camera_id) DO UPDATE SET
                               total_vehicles=EXCLUDED.total_vehicles''',
                        (report_date, cam_id, int(uptime_pct)) # Mock mapping
                    )
        except:
            pass

    def get_sla_daily(self, days: int = 30) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM monthly_reports WHERE report_type = 'daily' AND report_date >= CURRENT_DATE - INTERVAL '%s days' ORDER BY report_date DESC, camera_id",
                        (days,)
                    )
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def get_user(self, username: str) -> dict:
        try:
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
        except:
            return None

    def create_user(self, username: str, hashed_password: str, role_name: str) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO users (username, hashed_password, role_id) 
                           VALUES (%s, %s, (SELECT id FROM roles WHERE role_name = %s LIMIT 1)) 
                           ON CONFLICT DO NOTHING RETURNING id''',
                        (username, hashed_password, role_name)
                    )
                    return cursor.fetchone() is not None
        except Exception as e:
            print(f"DB Error create_user: {e}")
            return False

    def update_role_permissions(self, role_id: int, allowed_menus: str) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE roles SET allowed_menus = %s WHERE id = %s",
                        (allowed_menus, role_id)
                    )
                    return True
        except Exception as e:
            print(f"Update Role Error: {e}")
            return False

    def get_all_users(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(
                        '''SELECT u.id, u.username, r.role_name as role, u.created_at 
                           FROM users u 
                           JOIN roles r ON u.role_id = r.id 
                           ORDER BY u.id'''
                    )
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def update_user(self, user_id, username, role_name, hashed_password=None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    if hashed_password:
                        cursor.execute(
                            '''UPDATE users 
                               SET username=%s, 
                                   role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1), 
                                   hashed_password=%s 
                               WHERE id=%s''',
                            (username, role_name, hashed_password, user_id)
                        )
                    else:
                        cursor.execute(
                            '''UPDATE users 
                               SET username=%s, 
                                   role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1) 
                               WHERE id=%s''',
                            (username, role_name, user_id)
                        )
                    return True
        except Exception as e:
            print(f"Update User Error: {e}")
            return False

    def delete_user(self, user_id) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                    return True
        except:
            return False

    def get_telegram_bots(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM telegram_bots ORDER BY bot_name")
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def upsert_telegram_bot(self, bot_name, token, chat_important, chat_normal) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO telegram_bots (bot_name, token, chat_id_important, chat_id_normal)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT(bot_name) DO UPDATE SET
                               token=EXCLUDED.token,
                               chat_id_important=EXCLUDED.chat_id_important,
                               chat_id_normal=EXCLUDED.chat_id_normal''',
                        (bot_name, token, chat_important, chat_normal)
                    )
                    return True
        except:
            return False

    def delete_telegram_bot(self, bot_name) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM telegram_bots WHERE bot_name = %s", (bot_name,))
                    return True
        except:
            return False

    def get_roles(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM roles ORDER BY id")
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    # ==========================================
    # CAMERA ZONES
    # ==========================================
    def get_zones(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM camera_zones ORDER BY id")
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def add_zone(self, zone_name, description="") -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO camera_zones (zone_name, description) VALUES (%s, %s)",
                        (zone_name, description)
                    )
                    return True
        except:
            return False

    def delete_zone(self, zone_id) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # Unlink cameras first
                    cursor.execute("UPDATE cameras SET zone_id = NULL WHERE zone_id = %s", (zone_id,))
                    cursor.execute("DELETE FROM camera_zones WHERE id = %s", (zone_id,))
                    return True
        except:
            return False

    # ==========================================
    # CAMERA MANAGEMENT (New table: cameras)
    # ==========================================
    def get_all_cameras(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT c.*, cz.zone_name 
                        FROM cameras c 
                        LEFT JOIN camera_zones cz ON c.zone_id = cz.id
                        ORDER BY cz.zone_name NULLS LAST, c.camera_name
                    """)
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"Error fetching cameras: {e}")
            return []

    def get_cameras_by_zone(self, zone_id) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT c.*, cz.zone_name 
                        FROM cameras c 
                        LEFT JOIN camera_zones cz ON c.zone_id = cz.id
                        WHERE c.zone_id = %s AND c.is_active = TRUE
                        ORDER BY c.camera_name
                    """, (zone_id,))
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def get_active_cameras(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT c.*, cz.zone_name 
                        FROM cameras c 
                        LEFT JOIN camera_zones cz ON c.zone_id = cz.id
                        WHERE c.is_active = TRUE
                        ORDER BY cz.zone_name NULLS LAST, c.camera_name
                    """)
                    return [dict(r) for r in cursor.fetchall()]
        except:
            return []

    def get_camera(self, camera_id: int) -> dict:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM cameras WHERE id = %s", (camera_id,))
                    row = cursor.fetchone()
                    return dict(row) if row else None
        except:
            return None

    def add_camera(self, name, cam_type, imou_device_id, rtsp_url, is_active=True, imou_channel_id="0", zone_id=None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO cameras (camera_name, camera_type, imou_device_id, imou_channel_id, rtsp_url, is_active, zone_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                        (name, cam_type, imou_device_id, imou_channel_id, rtsp_url, is_active, zone_id)
                    )
                    return True
        except Exception as e:
            print(f"Error adding camera: {e}")
            return False

    def update_camera(self, camera_id, name, cam_type, imou_device_id, rtsp_url, is_active, imou_channel_id="0", zone_id=None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''UPDATE cameras 
                           SET camera_name=%s, camera_type=%s, imou_device_id=%s, imou_channel_id=%s, rtsp_url=%s, is_active=%s, zone_id=%s, updated_at=NOW()
                           WHERE id=%s''',
                        (name, cam_type, imou_device_id, imou_channel_id, rtsp_url, is_active, zone_id, camera_id)
                    )
                    return True
        except:
            return False

    def delete_camera(self, camera_id: int) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM cameras WHERE id = %s", (camera_id,))
                    return True
        except:
            return False

    def get_imou_api_keys(self) -> dict:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT key, value FROM app_settings WHERE key IN ('IMOU_OPEN_APP_ID', 'IMOU_OPEN_APP_SECRET')")
                    res = {k: v for k, v in cursor.fetchall()}
                    return {
                        'app_id': res.get('IMOU_OPEN_APP_ID', ''),
                        'app_secret': res.get('IMOU_OPEN_APP_SECRET', '')
                    }
        except:
            return {'app_id': '', 'app_secret': ''}

    def update_imou_api_keys(self, app_id: str, app_secret: str) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO app_settings (key, value) VALUES ('IMOU_OPEN_APP_ID', %s)
                           ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()''',
                        (app_id,)
                    )
                    cursor.execute(
                        '''INSERT INTO app_settings (key, value) VALUES ('IMOU_OPEN_APP_SECRET', %s)
                           ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()''',
                        (app_secret,)
                    )
                    return True
        except Exception as e:
            print(f"Error updating Imou API Keys: {e}")
            return False

