"""Vòng lặp nền dùng chung cho các tác vụ định kỳ của backend."""
from __future__ import annotations

import logging
import threading
from typing import Callable


def start_periodic(job: Callable[[], object], *, name: str, interval_secs: int,
                   error_wait_secs: int, error_message: str,
                   logger: logging.Logger) -> threading.Event:
    """Chạy `job` mỗi `interval_secs` giây trong luồng daemon; lỗi thì ghi log và chờ ít nhất `error_wait_secs`."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            wait = interval_secs
            try:
                job()
            except Exception:
                logger.exception(error_message)
                wait = max(interval_secs, error_wait_secs)
            stop.wait(wait)

    threading.Thread(target=loop, name=name, daemon=True).start()
    return stop
