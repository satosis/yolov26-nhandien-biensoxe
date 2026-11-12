"""Đơn bán hàng 'Chưa lấy' của ngày đã qua tự chuyển sang ngày hôm nay."""
from __future__ import annotations

import logging
import threading
from datetime import date

from backend.services.database import db
from backend.services.periodic import start_periodic

logger = logging.getLogger(__name__)


def roll_pending_sales(today: date | None = None) -> int:
    """Chuyển ngày đơn 'pending' có sale_date < hôm nay sang hôm nay, kèm ngày dòng công nợ gốc của đơn. Trả số đơn đã chuyển."""
    today = today or date.today()
    rows = db.query(
        """
        WITH moved AS (
            SELECT id FROM public.personal_sales
             WHERE status = 'pending' AND sale_date < %s
             FOR UPDATE
        ), first_log AS (
            SELECT DISTINCT ON (l.sale_id) l.id
              FROM public.personal_debt_logs l
              JOIN moved m ON m.id = l.sale_id
             ORDER BY l.sale_id, l.id
        ), logs AS (
            UPDATE public.personal_debt_logs l
               SET transaction_date = %s
              FROM first_log f
             WHERE l.id = f.id
            RETURNING l.id
        )
        UPDATE public.personal_sales s
           SET sale_date = %s, updated_at = NOW()
          FROM moved m
         WHERE s.id = m.id
        RETURNING s.id
        """,
        (today, today, today),
        fetchall=True,
    ) or []
    if rows:
        logger.info("Đã chuyển %s đơn chưa lấy sang ngày %s", len(rows), today)
    return len(rows)


def start_scheduler(interval_secs: int = 300) -> threading.Event:
    return start_periodic(roll_pending_sales, name="pending-sales-rollover", interval_secs=interval_secs,
                          error_wait_secs=interval_secs, error_message="Chuyển ngày đơn chưa lấy lỗi", logger=logger)
