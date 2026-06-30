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
import logging

from backend.core import config as cfg

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
            raw = self.db.get_app_setting("DATA_RETENTION_DAYS", "") or ""
            if str(raw).strip():
                return max(1, int(raw))
        except Exception:
            pass
        return int(getattr(cfg, "DATA_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))

    def run(self) -> dict:
        """Run every retention step. Returns a per-step summary; a failed step
        reports -1 instead of aborting the whole run."""
        summary = {
            "days": self.days,
            "recordings": self._safe(self.db.delete_old_recordings, self.days),
            "stranger_logs": self._safe(self.db.delete_old_stranger_logs, self.days),
            "status_logs": self._safe(self.db.delete_old_status_logs, self.days),
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
