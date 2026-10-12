import logging
from pathlib import Path

from backend.services import config as cfg
from backend.services.database import db
from backend.services.settings_service import get_app_setting

logger = logging.getLogger("retention")

class RetentionManager:

    def __init__(self, days: int = None):
        self.days = days if days is not None else self._resolve_days()

    def _resolve_days(self) -> int:
        return max(1, int(get_app_setting("DATA_RETENTION_DAYS", str(cfg.DATA_RETENTION_DAYS))))

    def is_legal_hold(self, file_path: str) -> bool:
        row = db._execute_query(
            "SELECT 1 FROM legal_hold WHERE file_path = %s AND released_at IS NULL LIMIT 1",
            (file_path,), fetch=True, commit=False
        )
        return row is not None

    def add_legal_hold(self, file_path: str, reason: str, held_by: str) -> bool:
        return db._execute_query(
            "INSERT INTO legal_hold (file_path, reason, held_by, held_at) VALUES (%s, %s, %s, NOW()) ON CONFLICT DO NOTHING",
            (file_path, reason, held_by), commit=True
        )

    def release_legal_hold(self, file_path: str) -> bool:
        query = "UPDATE legal_hold SET released_at = NOW() WHERE file_path = %s AND released_at IS NULL"
        return db._execute_query(query, (file_path,), commit=True)

    def delete_old_recordings(self, days: int) -> int:
        query = "SELECT id, file_path, thumbnail_path FROM video_recordings WHERE start_time < NOW() - (%s * INTERVAL '1 day')"
        rows = db._execute_query(query, (days,), fetchall=True, commit=False)
        deleted_count = 0
        for row in rows:
            if row.get("file_path") and self.is_legal_hold(row["file_path"]):
                continue

            for path_key in ["file_path", "thumbnail_path"]:
                path = row.get(path_key)
                if path:
                    Path(path).unlink(missing_ok=True)

            db.delete("video_recordings", {"id": row["id"]})
            deleted_count += 1

        return deleted_count

    def delete_old_stranger_logs(self, days: int) -> int:
        query = "SELECT id, image_path FROM stranger_logs WHERE event_time < NOW() - (%s * INTERVAL '1 day')"
        rows = db._execute_query(query, (days,), fetchall=True, commit=False)
        deleted_count = 0
        for row in rows:
            path = row.get("image_path")
            if path and self.is_legal_hold(path):
                continue
            if path:
                Path(path).unlink(missing_ok=True)
            db.delete("stranger_logs", {"id": row["id"]})
            deleted_count += 1

        return deleted_count

    def delete_old_status_logs(self, days: int) -> int:
        rows = db._execute_query(
            "DELETE FROM camera_status_log WHERE logged_at < NOW() - (%s * INTERVAL '1 day') RETURNING id",
            (days,), fetchall=True, commit=True
        )
        return len(rows)

    def run(self) -> dict:
        summary = {
            "days": self.days,
            "recordings": self._safe(self.delete_old_recordings, self.days),
            "stranger_logs": self._safe(self.delete_old_stranger_logs, self.days),
            "status_logs": self._safe(self.delete_old_status_logs, self.days),
        }
        total = sum(v for k, v in summary.items() if k != "days" and v > 0)
        logger.info("Retention run (>%dd): %s (total %d)", self.days, summary, total)
        return summary

    def _safe(self, fn, *args) -> int:
        try:
            return fn(*args)
        except Exception:
            logger.exception("Retention step %s failed", fn.__name__)
            return -1
