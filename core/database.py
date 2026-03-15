import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

class DatabaseManager:
    def __init__(self, dsn=None):
        # Use DATABASE_URL from env if dsn not provided
        self.dsn = dsn or os.environ.get("DATABASE_URL", "postgresql://camera_user:password@localhost:5432/camera_ai")
        # init_db is now handled by init.sql in the Postgres container.
        self._apply_migrations()

    def _apply_migrations(self):
        """Programmatically apply database schema updates."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # Upgrade camera_zones
                    cursor.execute("""
                        DO $$ 
                        BEGIN 
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='camera_zones' AND column_name='total_slots') THEN
                                ALTER TABLE camera_zones ADD COLUMN total_slots INTEGER DEFAULT 0;
                            END IF;
                        END $$;
                    """)
                    
                    # Upgrade vehicle_sessions
                    new_cols = [
                        ("camera_id_in", "INTEGER REFERENCES cameras(id)"),
                        ("camera_id_out", "INTEGER REFERENCES cameras(id)"),
                        ("zone_id", "INTEGER REFERENCES camera_zones(id)"),
                        ("entry_image", "TEXT"),
                        ("exit_image", "TEXT"),
                        ("status", "VARCHAR(20) DEFAULT 'parking'"),
                        ("stay_duration", "INTEGER"),
                        ("fee", "NUMERIC(15,2) DEFAULT 0")
                    ]
                    for col_name, col_type in new_cols:
                        cursor.execute(f"""
                            DO $$ 
                            BEGIN 
                                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='vehicle_sessions' AND column_name='{col_name}') THEN
                                    ALTER TABLE vehicle_sessions ADD COLUMN {col_name} {col_type};
                                END IF;
                            END $$;
                        """)

                    # Access Management Upgrades
                    cursor.execute("""
                        DO $$ 
                        BEGIN 
                            -- plate_whitelist
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='plate_whitelist' AND column_name='zone_id') THEN
                                ALTER TABLE plate_whitelist ADD COLUMN zone_id INTEGER REFERENCES camera_zones(id);
                            END IF;
                            
                            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'plate_whitelist_plate_zone_uc') THEN
                                BEGIN
                                    ALTER TABLE plate_whitelist DROP CONSTRAINT IF EXISTS plate_whitelist_plate_number_key;
                                EXCEPTION WHEN OTHERS THEN NULL;
                                END;
                                ALTER TABLE plate_whitelist ADD CONSTRAINT plate_whitelist_plate_zone_uc UNIQUE (plate_number, zone_id);
                            END IF;

                            -- Consolidation: Move Personnel into Users table
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='full_name') THEN
                                ALTER TABLE users ADD COLUMN full_name VARCHAR(100);
                            END IF;
                          IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='note') THEN
                                ALTER TABLE users ADD COLUMN note TEXT;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='zone_ids') THEN
                                ALTER TABLE users ADD COLUMN zone_ids INTEGER[];
                            END IF;
                            
                            -- Allow NULL password for personnel-only entries
                            ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;

                            -- Data Migration from people_whitelist to users if table exists
                            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='people_whitelist') THEN
                                INSERT INTO users (username, hashed_password, full_name, note)
                                SELECT 
                                    COALESCE(person_identity, 'migrated_' || id), 
                                    'personnel_only', 
                                    full_name, 
                                    note
                                FROM people_whitelist
                                ON CONFLICT (username) DO NOTHING;
                               
                                -- Drop old table
                                DROP TABLE IF EXISTS people_whitelist;
                            END IF;
                        END $$;
                    """)
                    conn.commit()
        except Exception as e:
            print(f"Migration Error: {e}")

    def _get_connection(self):
        return psycopg2.connect(self.dsn)

    def is_plate_whitelisted(self, plate_norm, zone_id=None):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    if zone_id:
                        cursor.execute('SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND zone_id = %s AND is_active = TRUE LIMIT 1', (plate_norm, zone_id))
                    else:
                        cursor.execute('SELECT 1 FROM plate_whitelist WHERE plate_number = %s AND is_active = TRUE LIMIT 1', (plate_norm,))
                    return cursor.fetchone() is not None
        except Exception as e:
            print(f"Postgres Error: {e}")
            return False

    def is_person_whitelisted(self, identity, zone_id=None):
        if identity == "STRANGER": return False
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # Search by username if identity is string, or id if integer
                    search_col = "username" if isinstance(identity, str) else "id"
                    if zone_id:
                        cursor.execute(f'SELECT 1 FROM users WHERE {search_col} = %s AND %s = ANY(zone_ids) LIMIT 1', (identity, zone_id))
                    else:
                        cursor.execute(f'SELECT 1 FROM users WHERE {search_col} = %s LIMIT 1', (identity,))
                    return cursor.fetchone() is not None
        except Exception as e:
            print(f"Postgres Error (is_person_whitelisted): {e}")
            return False

    def get_person_by_identity(self, identity):
        if identity == "STRANGER": return None
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    if isinstance(identity, int) or (isinstance(identity, str) and identity.isdigit()):
                        cursor.execute('SELECT id, full_name, zone_ids, note, username FROM users WHERE id = %s OR username = %s', (int(identity), str(identity)))
                    else:
                        cursor.execute('SELECT id, full_name, zone_ids, note, username FROM users WHERE username = %s', (identity,))
                    row = cursor.fetchone()
                    if row:
                        return {
                            "identity": str(row[0]),
                            "full_name": row[1],
                            "zones": row[2] or [],
                            "note": row[3],
                            "username": row[4]
                        }
                    return None
        except Exception as e:
            print(f"Postgres Error (get_person_by_identity): {e}")
            return None

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

    def upsert_vehicle_whitelist(self, plate_norm, label, added_by, zone_id=None, note=None):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        INSERT INTO plate_whitelist (plate_number, list_type, owner_name, added_by, zone_id, note, is_active)
                        VALUES (%s, 'white', %s, %s, %s, %s, TRUE)
                        ON CONFLICT(plate_number, zone_id) DO UPDATE SET
                            owner_name=EXCLUDED.owner_name,
                            added_by=EXCLUDED.added_by,
                            note=EXCLUDED.note,
                            updated_at=NOW()
                        ''',
                        (plate_norm, label, added_by, zone_id, note)
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

    # ───────────────── AI AUDITOR ──────────────────
    def get_low_confidence_events(self, limit=10, max_conf=0.65, hours_back=24):
        """Return plate events with lowest confidence for AI auditing."""
        try:
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
                          AND event_time >= NOW() - INTERVAL '%s hours'
                        ORDER BY confidence ASC
                        LIMIT %s
                    """, (max_conf, hours_back, limit))
                    return cursor.fetchall()
        except Exception as e:
            print(f"AI Auditor DB Error: {e}")
            return []

    def mark_event_audited(self, event_id, gemini_result, is_mismatch):
        """Store Gemini's audit result for a plate event."""
        try:
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
        except Exception as e:
            print(f"AI Auditor DB Error: {e}")
            return False

    def get_app_setting(self, key):
        """Get a single app setting value."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
                    row = cursor.fetchone()
                    return row[0] if row else None
        except:
            return None

    def set_app_setting(self, key, value):
        """Upsert a single app setting."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO app_settings (key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """, (key, value))
                conn.commit()
                return True
        except Exception as e:
            print(f"Settings Error: {e}")
            return False
    # ───────────────── END AI AUDITOR ──────────────

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

    def create_user(self, username: str, hashed_password: str, role_name: str, 
                    full_name: str = None, 
                    note: str = None, zone_ids: list = None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO users (username, hashed_password, role_id, full_name, note, zone_ids) 
                           VALUES (%s, %s, (SELECT id FROM roles WHERE role_name = %s LIMIT 1), %s, %s, %s) 
                           ON CONFLICT DO NOTHING RETURNING id''',
                        (username, hashed_password, role_name, full_name, note, zone_ids)
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
                        '''SELECT u.id, u.username, r.role_name as role, u.created_at, u.full_name 
                           FROM users u 
                           LEFT JOIN roles r ON u.role_id = r.id 
                           ORDER BY u.id'''
                    )
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"Error in get_all_users: {e}")
            return []

    def update_user(self, user_id, username, role_name, hashed_password=None,
                    full_name=None, note=None, zone_ids=None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # Construct update query dynamically to avoid overwriting with None if not intended
                    # But for simplicity, we'll update everything provided
                    if hashed_password:
                        cursor.execute(
                            '''UPDATE users 
                               SET username=%s, 
                                   role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1), 
                                   hashed_password=%s,
                                   full_name=%s,
                                   note=%s,
                                   zone_ids=%s
                               WHERE id=%s''',
                            (username, role_name, hashed_password, full_name, note, zone_ids, user_id)
                        )
                    else:
                        cursor.execute(
                            '''UPDATE users 
                               SET username=%s, 
                                   role_id=(SELECT id FROM roles WHERE role_name=%s LIMIT 1),
                                   full_name=%s,
                                   note=%s,
                                   zone_ids=%s
                               WHERE id=%s''',
                            (username, role_name, full_name, note, zone_ids, user_id)
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

    def add_camera(self, name, cam_type, imou_device_id, rtsp_url, is_active=True, imou_channel_id="0", zone_id=None,
                   imou_app_id=None, imou_app_secret=None, imou_email=None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''INSERT INTO cameras (camera_name, camera_type, imou_device_id, imou_channel_id, rtsp_url, is_active, zone_id, 
                                               imou_app_id, imou_app_secret, imou_email)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                        (name, cam_type, imou_device_id, imou_channel_id, rtsp_url, is_active, zone_id, 
                         imou_app_id, imou_app_secret, imou_email)
                    )
                    return True
        except Exception as e:
            print(f"Error adding camera: {e}")
            return False

    def update_camera(self, camera_id, name, cam_type, imou_device_id, rtsp_url, is_active, imou_channel_id="0", zone_id=None,
                      imou_app_id=None, imou_app_secret=None, imou_email=None) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''UPDATE cameras 
                           SET camera_name=%s, camera_type=%s, imou_device_id=%s, imou_channel_id=%s, rtsp_url=%s, is_active=%s, zone_id=%s, 
                               imou_app_id=%s, imou_app_secret=%s, imou_email=%s, updated_at=NOW()
                           WHERE id=%s''',
                        (name, cam_type, imou_device_id, imou_channel_id, rtsp_url, is_active, zone_id, 
                         imou_app_id, imou_app_secret, imou_email, camera_id)
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
    # ==========================================
    # SMART PARKING ENGINE
    # ==========================================
    def record_vehicle_entry(self, plate_norm, vehicle_type, camera_id, image_path, zone_id=None) -> bool:
        """Records a vehicle entering the parking zone."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    # Check if there's an active 'parking' session for this plate
                    cursor.execute("SELECT id FROM vehicle_sessions WHERE plate_norm = %s AND status = 'parking' LIMIT 1", (plate_norm,))
                    active = cursor.fetchone()
                    
                    if active:
                        # Update existing session (maybe it missed an exit or re-entered)
                        cursor.execute("""
                            UPDATE vehicle_sessions 
                            SET entered_at_utc = NOW(), camera_id_in = %s, entry_image = %s, zone_id = %s
                            WHERE id = %s
                        """, (camera_id, image_path, zone_id, active[0]))
                    else:
                        # Create new session
                        cursor.execute("""
                            INSERT INTO vehicle_sessions (plate_norm, vehicle_type, camera_id_in, entry_image, zone_id, status, entered_at_utc)
                            VALUES (%s, %s, %s, %s, %s, 'parking', NOW())
                        """, (plate_norm, vehicle_type, camera_id, image_path, zone_id))
                    return True
        except Exception as e:
            print(f"Error recording entry: {e}")
            return False

    def record_vehicle_exit(self, plate_norm, camera_id, image_path) -> dict | None:
        """Records a vehicle exiting and calculates duration/fee."""
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    # Find the active session
                    cursor.execute("""
                        SELECT * FROM vehicle_sessions 
                        WHERE plate_norm = %s AND status = 'parking' 
                        ORDER BY entered_at_utc DESC LIMIT 1
                    """, (plate_norm,))
                    session = cursor.fetchone()
                    
                    if not session:
                        return None
                    
                    session = dict(session)
                    exit_time = datetime.now()
                    duration = (exit_time.astimezone() - session['entered_at_utc']).total_seconds()
                    
                    # Simple fee logic: 5000 VND / hour for motorbikes, 20000 for cars
                    is_car = session.get('vehicle_type') == 'car'
                    rate = 20000 if is_car else 5000
                    fee = float(round((duration / 3600) * rate, -3)) # Rounded to nearest 1000
                    if fee < rate: fee = float(rate) # Min fee
                    
                    cursor.execute("""
                        UPDATE vehicle_sessions 
                        SET exited_at_utc = NOW(), 
                            camera_id_out = %s, 
                            exit_image = %s, 
                            status = 'completed',
                            stay_duration = %s,
                            fee = %s
                        WHERE id = %s
                    """, (camera_id, image_path, int(duration), fee, session['id']))
                    
                    session['exited_at_utc'] = exit_time
                    session['stay_duration'] = int(duration)
                    session['fee'] = fee
                    return session
        except Exception as e:
            print(f"Error recording exit: {e}")
            return None

    def get_occupancy_stats(self) -> list[dict]:
        """Returns occupancy stats per zone."""
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT 
                            cz.id, 
                            cz.zone_name, 
                            cz.total_slots,
                            COUNT(vs.id) FILTER (WHERE vs.status = 'parking') as occupied_slots
                        FROM camera_zones cz
                        LEFT JOIN vehicle_sessions vs ON cz.id = vs.zone_id
                        GROUP BY cz.id, cz.zone_name, cz.total_slots
                        ORDER BY cz.zone_name
                    """)
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"Error fetching occupancy: {e}")
            return []

    def update_zone_slots(self, zone_id, slots) -> bool:
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("UPDATE camera_zones SET total_slots = %s WHERE id = %s", (slots, zone_id))
                    return True
        except:
            return False

    def get_parking_history(self, limit=50) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("""
                        SELECT vs.*, cz.zone_name, c_in.camera_name as cam_in, c_out.camera_name as cam_out
                        FROM vehicle_sessions vs
                        LEFT JOIN camera_zones cz ON vs.zone_id = cz.id
                        LEFT JOIN cameras c_in ON vs.camera_id_in = c_in.id
                        LEFT JOIN cameras c_out ON vs.camera_id_out = c_out.id
                        ORDER BY vs.entered_at_utc DESC
                        LIMIT %s
                    """, (limit,))
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"Error fetching history: {e}")
            return []

    def get_whitelist_vehicles(self, zone_id=None) -> list[dict]:
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    query = "SELECT w.*, cz.zone_name FROM plate_whitelist w LEFT JOIN camera_zones cz ON w.zone_id = cz.id"
                    params = []
                    if zone_id:
                        query += " WHERE w.zone_id = %s"
                        params.append(zone_id)
                    query += " ORDER BY w.created_at DESC"
                    cursor.execute(query, params)
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"Error fetching whitelist vehicles: {e}")
            return []

    # Personnel-related queries now use unified user queries or specific filters
    def get_whitelist_people(self, zone_id=None) -> list[dict]:
        """Retrieves personnel from the users table. Used by UI components."""
        try:
            with self._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    query = "SELECT u.id, u.username, u.full_name, u.person_identity, u.note, u.zone_ids FROM users u"
                    params = []
                    if zone_id:
                        query += " WHERE %s = ANY(u.zone_ids)"
                        params.append(zone_id)
                    else:
                        query += " WHERE u.person_identity IS NOT NULL"
                    
                    query += " ORDER BY u.created_at DESC"
                    cursor.execute(query, params)
                    return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"Error fetching whitelist people: {e}")
            return []

    def delete_whitelist_person(self, identity, zone_id=None):
        """Removes a specific zone from a person or deletes them if personnel-only."""
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    if zone_id:
                        cursor.execute("UPDATE users SET zone_ids = array_remove(zone_ids, %s) WHERE person_identity = %s", (zone_id, identity))
                    else:
                        cursor.execute("DELETE FROM users WHERE person_identity = %s", (identity,))
                    return True
        except Exception as e:
            print(f"Error deleting personnel: {e}")
            return False

