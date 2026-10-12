import requests

from backend.services.database import DatabaseManager


def _telegram_bots() -> list[dict]:
    return DatabaseManager().select("telegram_bots")


def notify_telegram(message, important=False):
    prefix = "[IMPORTANT] " if important else "[INFO] "
    for bot in _telegram_bots():
        chat_id = bot.get("chat_id")
        token = bot.get("token")
        if not chat_id or not token:
            continue
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": prefix + message},
            timeout=5,
        ).raise_for_status()


def notify_telegram_video(video_path, caption="", important=False):
    prefix = "[VIDEO IMPORTANT] " if important else "[VIDEO] "
    for bot in _telegram_bots():
        chat_id = bot.get("chat_id")
        token = bot.get("token")
        if not chat_id or not token:
            continue
        with open(video_path, "rb") as video:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendVideo",
                files={"video": video},
                data={"chat_id": chat_id, "caption": prefix + caption},
                timeout=30,
            ).raise_for_status()
