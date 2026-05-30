#!/usr/bin/env python3
"""Seed the default admin account and create schema first if needed."""

from pathlib import Path
import sys
import bcrypt

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from backend.core.database import DatabaseManager
from backend.scripts.setup_database import ensure_required_columns, run_schema, seed_admin

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "nhandien23456@"
ADMIN_ROLE = "Admin"
SEED_TABLE = "admin_seeds"


def create_seed_table(db: DatabaseManager):
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {SEED_TABLE} (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """)
            conn.commit()


def ensure_admin_role(db: DatabaseManager):
    existing = db.select("roles", columns=["id"], conditions={"role_name": ADMIN_ROLE}, fetchall=False)
    if existing:
        return existing["id"]
    return db.insert("roles", {"role_name": ADMIN_ROLE, "allowed_menus": "*"}, returning="id")


def ensure_admin_user(db: DatabaseManager):
    existing = db.get_user(ADMIN_USERNAME)
    if existing:
        print(f"Admin user '{ADMIN_USERNAME}' already exists. Skipping creation.")
        return False

    hashed = bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode()
    ok, msg = db.create_user(
        username=ADMIN_USERNAME,
        hashed_password=hashed,
        role_name=ADMIN_ROLE,
        full_name="Administrator",
        note="Seeded default admin account",
        site_ids=None,
    )
    if not ok:
        raise RuntimeError(msg)
    print(f"Created admin user '{ADMIN_USERNAME}'.")
    return True


def insert_seed_record(db: DatabaseManager):
    query = f"INSERT INTO {SEED_TABLE} (username, note) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING"
    with db._get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (ADMIN_USERNAME, "Seeded default admin account"))
            conn.commit()


if __name__ == "__main__":
    print("Seeding admin user and admin_seeds table...")
    db = DatabaseManager()
    run_schema(db)
    ensure_required_columns(db)
    seed_admin(db)
    print("Done.")
    print(f"Login with: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
