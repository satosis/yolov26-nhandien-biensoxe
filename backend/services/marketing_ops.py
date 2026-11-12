"""Bộ lập lịch chạy nền cho vận hành marketing (nhắc nhở khách và báo cáo tuần)."""
from __future__ import annotations

import logging
import threading

from backend.services import facebook_radar, lead_followup, project_news, prospects, weekly_report
from backend.services.periodic import start_periodic

logger = logging.getLogger(__name__)


_TASKS = (lead_followup, weekly_report, facebook_radar, prospects, project_news)


def tick() -> None:
    """Chạy một chu kỳ vận hành marketing; lỗi thì dừng chu kỳ và để start_periodic ghi log."""
    for task in _TASKS:
        task.tick()


def start_scheduler(interval_secs: int = 300) -> threading.Event:
    """Khởi động luồng daemon định kỳ chạy nhắc khách và báo cáo tuần."""
    return start_periodic(lambda: tick(), name="marketing-ops", interval_secs=interval_secs, error_wait_secs=1800, error_message="Marketing ops lỗi chu kỳ", logger=logger)
