#!/usr/bin/env python3
"""Create the PostgreSQL schema and seed baseline data for a fresh database."""

from pathlib import Path
import sys

import bcrypt

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from backend.core.database import DatabaseManager
from backend.scripts.seed_providers import seed as seed_providers

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "nhandien23456@"
ADMIN_ROLE = "Admin"

REQUIRED_COLUMNS = {
    "roles": {
        "id": "SERIAL",
        "role_name": "TEXT",
        "allowed_menus": "TEXT NOT NULL DEFAULT ''",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "sites": {
        "id": "SERIAL",
        "site_name": "TEXT",
        "address": "TEXT NOT NULL DEFAULT ''",
        "description": "TEXT NOT NULL DEFAULT ''",
        "total_slots": "INTEGER NOT NULL DEFAULT 0",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "users": {
        "id": "SERIAL",
        "username": "TEXT",
        "hashed_password": "TEXT",
        "role_id": "INTEGER",
        "full_name": "TEXT",
        "note": "TEXT",
        "site_ids": "INTEGER[] DEFAULT ARRAY[]::INTEGER[]",
        "person_identity": "TEXT",
        "phone": "TEXT",
        "email": "TEXT",
        "date_of_birth": "DATE",
        "id_number": "TEXT",
        "kyc_level": "INTEGER NOT NULL DEFAULT 0",
        "kyc_submitted_at": "TIMESTAMPTZ",
        "kyc_reviewed_at": "TIMESTAMPTZ",
        "kyc_reviewed_by": "TEXT",
        "kyc_reject_reason": "TEXT",
        "remember_token": "TEXT",
        "remember_token_expires": "TIMESTAMPTZ",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "kyc_documents": {
        "id": "SERIAL",
        "user_id": "INTEGER",
        "doc_type": "TEXT",
        "file_path": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "uploaded_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "reviewed_at": "TIMESTAMPTZ",
        "reviewed_by": "TEXT",
    },
    "camera_providers": {
        "provider_key": "TEXT",
        "name": "TEXT",
        "api_base_url": "TEXT",
        "api_doc_url": "TEXT",
        "rtsp_template": "TEXT",
        "onvif_port": "INTEGER NOT NULL DEFAULT 80",
        "default_rtsp_port": "INTEGER NOT NULL DEFAULT 554",
        "default_http_port": "INTEGER NOT NULL DEFAULT 80",
        "notes": "TEXT",
        "logo_url": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "door_providers": {
        "provider_key": "TEXT",
        "name": "TEXT",
        "connection_type": "TEXT",
        "api_doc_url": "TEXT",
        "config_template": "TEXT",
        "notes": "TEXT",
        "logo_url": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "cameras": {
        "id": "SERIAL",
        "camera_name": "TEXT",
        "camera_type": "TEXT NOT NULL DEFAULT 'rtsp'",
        "imou_device_id": "TEXT",
        "imou_channel_id": "TEXT NOT NULL DEFAULT '0'",
        "rtsp_url": "TEXT",
        "onvif_rtsp_detected": "TEXT",
        "camera_ip": "TEXT",
        "camera_port": "INTEGER NOT NULL DEFAULT 554",
        "provider_keys": "JSONB NOT NULL DEFAULT '{}'::JSONB",
        "site_ids": "INTEGER[] DEFAULT ARRAY[]::INTEGER[]",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "is_streaming": "BOOLEAN NOT NULL DEFAULT FALSE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "cameras_asset": {
        "id": "SERIAL",
        "cam_id": "TEXT",
        "name": "TEXT",
        "ip": "TEXT",
        "mac": "TEXT",
        "serial": "TEXT",
        "model": "TEXT",
        "firmware": "TEXT",
        "rtsp_url": "TEXT",
        "onvif_port": "INTEGER",
        "onvif_profiles": "JSONB",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "plate_events": {
        "id": "SERIAL",
        "event_time": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "camera_id": "TEXT",
        "site_id": "INTEGER",
        "vehicle_type": "TEXT",
        "plate_number": "TEXT",
        "confidence": "DOUBLE PRECISION",
        "image_path": "TEXT",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "plate_whitelist": {
        "id": "SERIAL",
        "plate_number": "TEXT",
        "list_type": "TEXT NOT NULL DEFAULT 'white'",
        "owner_name": "TEXT",
        "added_by": "TEXT",
        "site_ids": "JSONB NOT NULL DEFAULT '[]'::JSONB",
        "note": "TEXT",
        "fuel_consumption": "NUMERIC(6,2)",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "pending_plates": {
        "id": "SERIAL",
        "pending_id": "TEXT",
        "event_id": "INTEGER",
        "plate_raw": "TEXT",
        "plate_norm": "TEXT",
        "first_seen_utc": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "site_id": "INTEGER",
        "image_path": "TEXT",
        "plate_type": "TEXT NOT NULL DEFAULT 'white'",
        "confirmed_at_utc": "TIMESTAMPTZ",
        "confirmed_by": "TEXT",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "vehicle_sessions": {
        "id": "SERIAL",
        "plate_norm": "TEXT",
        "vehicle_type": "TEXT",
        "camera_id_in": "TEXT",
        "camera_id_out": "TEXT",
        "entry_time": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "exit_time": "TIMESTAMPTZ",
        "entry_image": "TEXT",
        "exit_image": "TEXT",
        "site_id": "INTEGER",
        "status": "TEXT NOT NULL DEFAULT 'parking'",
        "stay_duration": "INTEGER",
        "fee": "DOUBLE PRECISION NOT NULL DEFAULT 0",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "camera_status_log": {
        "id": "SERIAL",
        "camera_id": "TEXT",
        "status": "TEXT",
        "event_type": "TEXT",
        "notes": "TEXT",
        "logged_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "video_recordings": {
        "id": "SERIAL",
        "cam_id": "TEXT",
        "site_id": "INTEGER",
        "event_type": "TEXT",
        "event_text": "TEXT",
        "file_path": "TEXT",
        "thumbnail_path": "TEXT",
        "start_time": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "duration_seconds": "INTEGER",
        "file_size_bytes": "BIGINT",
        "is_favorite": "BOOLEAN NOT NULL DEFAULT FALSE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "legal_hold": {
        "id": "SERIAL",
        "file_path": "TEXT",
        "reason": "TEXT",
        "held_by": "TEXT",
        "held_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "released_at": "TIMESTAMPTZ",
    },
    "monthly_reports": {
        "id": "SERIAL",
        "report_month": "DATE",
        "data": "JSONB NOT NULL DEFAULT '{}'::JSONB",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "telegram_bots": {
        "id": "SERIAL",
        "bot_name": "TEXT",
        "token": "TEXT",
        "chat_id": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "app_settings": {
        "key": "TEXT",
        "value": "TEXT",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "projects": {
        "id": "SERIAL",
        "name": "TEXT",
        "location": "TEXT",
        "area": "TEXT",
        "height": "TEXT",
        "bays": "TEXT",
        "load_capacity": "TEXT",
        "image": "TEXT",
        "description": "TEXT",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "news": {
        "id": "SERIAL",
        "slug": "TEXT",
        "title": "TEXT",
        "category": "TEXT",
        "summary": "TEXT",
        "content": "TEXT",
        "image": "TEXT",
        "publish_date": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "is_published": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "inquiries": {
        "id": "SERIAL",
        "full_name": "TEXT",
        "phone": "TEXT",
        "company_name": "TEXT NOT NULL DEFAULT ''",
        "demand": "TEXT NOT NULL DEFAULT ''",
        "source_page": "TEXT NOT NULL DEFAULT 'home'",
        "is_read": "BOOLEAN NOT NULL DEFAULT FALSE",
        "note": "TEXT",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "rolling_doors": {
        "id": "SERIAL",
        "name": "TEXT",
        "supplier_key": "TEXT",
        "site_id": "INTEGER",
        "relay_type": "TEXT NOT NULL DEFAULT 'http'",
        "relay_config": "TEXT",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "camera_id": "INTEGER",
        "auto_close_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
        "auto_close_delay_sec": "INTEGER NOT NULL DEFAULT 300",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "stranger_logs": {
        "id": "SERIAL",
        "camera_id": "TEXT",
        "similarity": "DOUBLE PRECISION",
        "image_path": "TEXT",
        "site_id": "INTEGER",
        "face_embedding": "BYTEA",
        "status": "TEXT NOT NULL DEFAULT 'new'",
        "direction": "TEXT NOT NULL DEFAULT 'frontal'",
        "converted_user_id": "INTEGER",
        "note": "TEXT",
        "event_time": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "ocr_training_samples": {
        "id": "SERIAL",
        "plate_image_path": "TEXT",
        "ocr_text": "TEXT",
        "confidence": "DOUBLE PRECISION",
        "camera_id": "TEXT",
        "corrected_text": "TEXT",
        "reviewed_by": "TEXT",
        "reviewed_at": "TIMESTAMPTZ",
        "status": "TEXT NOT NULL DEFAULT 'pending'",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "cargo_trips": {
        "id": "SERIAL",
        "vehicle_id": "INTEGER",
        "plate_number": "TEXT",
        "departure_time": "TIMESTAMPTZ",
        "return_time": "TIMESTAMPTZ",
        "origin": "TEXT",
        "destination": "TEXT",
        "fuel_liters": "NUMERIC(12,2)",
        "fuel_price_per_liter": "NUMERIC(14,2)",
        "fuel_cost_total": "NUMERIC(14,2) GENERATED ALWAYS AS (fuel_liters * fuel_price_per_liter) STORED",
        "freight_revenue": "NUMERIC(14,2)",
        "distance_km": "NUMERIC(12,2)",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
    "admin_seeds": {
        "id": "SERIAL",
        "username": "TEXT",
        "note": "TEXT",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    },
}


def run_schema(db: DatabaseManager) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    schema_sql = schema_path.read_text(encoding="utf-8")
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(schema_sql)
        conn.commit()


def ensure_required_columns(db: DatabaseManager) -> None:
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            for table, columns in REQUIRED_COLUMNS.items():
                for column, definition in columns.items():
                    cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
                    )
        conn.commit()


def validate_schema(db: DatabaseManager) -> list[str]:
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                """
            )
            actual = {(table, column) for table, column in cursor.fetchall()}
    missing = []
    for table, columns in REQUIRED_COLUMNS.items():
        for column in columns:
            if (table, column) not in actual:
                missing.append(f"{table}.{column}")
    return missing


def seed_admin(db: DatabaseManager) -> None:
    hashed = bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode()
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO roles (role_name, allowed_menus)
                VALUES (%s, '*')
                ON CONFLICT (role_name) DO UPDATE SET allowed_menus = EXCLUDED.allowed_menus
                RETURNING id
                """,
                (ADMIN_ROLE,),
            )
            role_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO users (username, hashed_password, role_id, full_name, note, site_ids)
                VALUES (%s, %s, %s, %s, %s, ARRAY[]::INTEGER[])
                ON CONFLICT (username) DO UPDATE SET
                    hashed_password = EXCLUDED.hashed_password,
                    role_id = EXCLUDED.role_id,
                    full_name = EXCLUDED.full_name,
                    note = EXCLUDED.note,
                    updated_at = NOW()
                """,
                (
                    ADMIN_USERNAME,
                    hashed,
                    role_id,
                    "Administrator",
                    "Seeded default admin account",
                ),
            )
            cursor.execute(
                """
                INSERT INTO admin_seeds (username, note)
                VALUES (%s, %s)
                ON CONFLICT (username) DO UPDATE SET note = EXCLUDED.note
                """,
                (ADMIN_USERNAME, "Seeded default admin account"),
            )
        conn.commit()


def main() -> None:
    db = DatabaseManager()
    print("Creating/updating database schema...")
    run_schema(db)
    print("Ensuring required columns...")
    ensure_required_columns(db)
    print("Seeding camera and door providers...")
    seed_providers()
    print("Seeding admin account...")
    seed_admin(db)
    missing = validate_schema(db)
    if missing:
        raise RuntimeError("Missing DB columns after migration: " + ", ".join(missing))
    print(f"Done. Login with: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()
