import os

import requests

from backend.services.database import DatabaseManager


def _telegram_bots() -> list[dict]:
    try:
        return DatabaseManager().select("telegram_bots") or []
    except Exception:
        return []


def notify_telegram(message, important=False):
    """Send a Telegram text notification to configured bots, if any."""
    prefix = "[IMPORTANT] " if important else "[INFO] "
    for bot in _telegram_bots():
        chat_id = bot.get("chat_id")
        token = bot.get("token")
        if not chat_id or not token:
            continue
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": prefix + message},
                timeout=5,
            )
        except requests.RequestException:
            pass


def notify_telegram_video(video_path, caption="", important=False):
    """Send a Telegram video notification to configured bots, if any."""
    if not os.path.exists(video_path):
        return

    prefix = "[VIDEO IMPORTANT] " if important else "[VIDEO] "
    for bot in _telegram_bots():
        chat_id = bot.get("chat_id")
        token = bot.get("token")
        if not chat_id or not token:
            continue
        try:
            with open(video_path, "rb") as video:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendVideo",
                    files={"video": video},
                    data={"chat_id": chat_id, "caption": prefix + caption},
                    timeout=30,
                )
        except (OSError, requests.RequestException):
            pass
