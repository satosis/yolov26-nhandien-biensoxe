"""
core/asset_registry.py
Camera Asset Registry — CMDB (Configuration Management Database)
Lưu trữ thông tin đầy đủ của từng camera trong PostgreSQL.
"""
from datetime import datetime
from typing import Optional
from core.database import DatabaseManager


class AssetRegistry:
    TABLE = "cameras_asset"

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager()

    def upsert(self, data: dict) -> bool:
        """Insert or update a camera record. cam_id and name are required."""
        required = {"cam_id", "name"}
        if not required.issubset(data):
            raise ValueError(f"Missing required fields: {required - data.keys()}")

        cam_id = str(data["cam_id"]).strip()
        if not cam_id or any(c in cam_id for c in (";", "'", '"', "\n", "\r")):
            raise ValueError(f"Invalid cam_id: {cam_id!r}")

        data["updated_at"] = datetime.now()
        fields = [
            "cam_id", "name", "ip", "mac", "serial", "model", "firmware",
            "rtsp_url", "install_date", "location_lat", "location_lon",
            "mount_height_m", "fov_deg", "dori_class", "notes", "updated_at",
        ]
        
        cols = ", ".join(fields)
        vals_placeholders = ", ".join([f"%({f})s" for f in fields])
        updates = ", ".join([f"{f} = EXCLUDED.{f}" for f in fields if f != "cam_id"])

        query = f"""
            INSERT INTO {self.TABLE} ({cols})
            VALUES ({vals_placeholders})
            ON CONFLICT (cam_id) DO UPDATE SET {updates}
        """

        try:
            with self.db._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, data)
                conn.commit()
            return True
        except Exception as e:
            print(f"AssetRegistry Error: {e}")
            return False

    def get(self, cam_id: str) -> Optional[dict]:
        query = f"SELECT * FROM {self.TABLE} WHERE cam_id = %s"
        try:
            with self.db._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (cam_id,))
                    row = cursor.fetchone()
                    if row:
                        # Convert tuple to dict if needed, or use RealDictCursor
                        # RealDictCursor is used by DatabaseManager._get_connection() in some cases
                        # but let's assume standard cursor for simplicity or check database.py
                        desc = cursor.description
                        return dict(zip([col[0] for col in desc], row))
        except Exception:
            pass
        return None

    def get_all(self) -> list[dict]:
        query = f"SELECT * FROM {self.TABLE} ORDER BY cam_id"
        try:
            with self.db._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query)
                    rows = cursor.fetchall()
                    desc = cursor.description
                    cols = [col[0] for col in desc]
                    return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []

    def delete(self, cam_id: str) -> bool:
        query = f"DELETE FROM {self.TABLE} WHERE cam_id = %s"
        try:
            with self.db._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (cam_id,))
                conn.commit()
            return True
        except Exception:
            return False

    def export_json(self) -> list[dict]:
        """Export all records as JSON-serialisable list (masks rtsp_url credentials)."""
        records = self.get_all()
        for r in records:
            # Convert datetime to string for JSON serialization
            if isinstance(r.get("updated_at"), datetime):
                r["updated_at"] = r["updated_at"].isoformat()
                
            url = r.get("rtsp_url") or ""
            if "://" in url and "@" in url:
                scheme, rest = url.split("://", 1)
                if "@" in rest:
                    creds, host_path = rest.split("@", 1)
                    if ":" in creds:
                        user, _ = creds.split(":", 1)
                        r["rtsp_url"] = f"{scheme}://{user}:***@{host_path}"
        return records
