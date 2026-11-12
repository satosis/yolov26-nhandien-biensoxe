"""Một lỗi lặp lại ở mỗi lượt chạy định kỳ chỉ ghi log lần đầu; hết lỗi thì `log_resolved` để lần lỗi sau ghi lại."""
import logging

_last: dict[str, str] = {}


def log_once(logger: logging.Logger, level: int, key: str, message: str, *args, exc_info: bool = False) -> bool:
    text = message % args if args else message
    if _last.get(key) == text:
        return False
    _last[key] = text
    logger.log(level, text, exc_info=exc_info)
    return True


def log_resolved(key: str) -> None:
    _last.pop(key, None)
