"""Data retention — purge stale operational data (DB rows + their files).

Deletes recordings, stranger face logs and camera health logs older than the
retention window. Rows/files protected by an active `legal_hold` are never
removed. File deletion is best-effort: a path that does not exist on the host
(e.g. a DB migrated from another machine) is silently skipped while the stale
DB row is still removed.

Retention window resolution (first non-empty wins):
  1. app_settings key `DATA_RETENTION_DAYS` (DB-driven, no hardcode)
  2. config `DATA_RETENTION_DAYS`
  3. 30 days
"""
import os
import logging

from backend.services import config as cfg

logger = logging.getLogger("retention")

DEFAULT_RETENTION_DAYS = 30


class RetentionManager:
    """Coordinates retention cleanup across operational tables.

    Usage:
        summary = RetentionManager(db).run()
    """

    def __init__(self, db, days: int = None):
        self.db = db
        self.days = days if days is not None else self._resolve_days()

    def _resolve_days(self) -> int:
        try:
            row = self.db.select("app_settings", conditions={"key": "DATA_RETENTION_DAYS"}, fetchall=False)
            if row and row.get("value", "").strip():
                return max(1, int(row["value"]))
        except Exception:
            pass
        return int(getattr(cfg, "DATA_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))

    def is_legal_hold(self, file_path: str) -> bool:
        row = self.db._execute_query(
            "SELECT 1 FROM legal_hold WHERE file_path = %s AND released_at IS NULL LIMIT 1",
            (file_path,), fetch=True, commit=False
        )
        return row is not None

    def add_legal_hold(self, file_path: str, reason: str, held_by: str) -> bool:
        return self.db._execute_query(
            "INSERT INTO legal_hold (file_path, reason, held_by, held_at) VALUES (%s, %s, %s, NOW()) ON CONFLICT DO NOTHING",
            (file_path, reason, held_by), commit=True
        ) is not None

    def release_legal_hold(self, file_path: str) -> bool:
        query = "UPDATE legal_hold SET released_at = NOW() WHERE file_path = %s AND released_at IS NULL"
        return self.db._execute_query(query, (file_path,), commit=True) is not None

    def delete_old_recordings(self, days: int) -> int:
        query = "SELECT id, file_path, thumbnail_path FROM video_recordings WHERE start_time < NOW() - (%s * INTERVAL '1 day')"
        rows = self.db._execute_query(query, (days,), fetchall=True, commit=False)
        if not rows:
            return 0

        deleted_count = 0
        for row in rows:
            if row.get("file_path") and self.is_legal_hold(row["file_path"]):
                continue

            for path_key in ["file_path", "thumbnail_path"]:
                path = row.get(path_key)
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as e:
                        logger.error(f"Failed to delete recording file {path}: {e}")

            self.db.delete("video_recordings", {"id": row["id"]})
            deleted_count += 1

        return deleted_count

    def delete_old_stranger_logs(self, days: int) -> int:
        query = "SELECT id, image_path FROM stranger_logs WHERE event_time < NOW() - (%s * INTERVAL '1 day')"
        rows = self.db._execute_query(query, (days,), fetchall=True, commit=False)
        if not rows:
            return 0

        deleted_count = 0
        for row in rows:
            path = row.get("image_path")
            if path and self.is_legal_hold(path):
                continue
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    logger.error(f"Failed to delete stranger image {path}: {e}")
            self.db.delete("stranger_logs", {"id": row["id"]})
            deleted_count += 1

        return deleted_count

    def delete_old_status_logs(self, days: int) -> int:
        rows = self.db._execute_query(
            "DELETE FROM camera_status_log WHERE logged_at < NOW() - (%s * INTERVAL '1 day') RETURNING id",
            (days,), fetchall=True, commit=True
        )
        return len(rows) if rows else 0

    def run(self) -> dict:
        """Run every retention step. Returns a per-step summary; a failed step
        reports -1 instead of aborting the whole run."""
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
            return int(fn(*args) or 0)
        except Exception as e:
            logger.error("Retention step %s failed: %s", getattr(fn, "__name__", fn), e)
            return -1
