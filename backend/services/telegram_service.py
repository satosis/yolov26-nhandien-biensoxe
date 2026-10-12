import requests
import re
import logging
from contextlib import closing
from http.client import HTTPSConnection

from backend.services.database import db

logger = logging.getLogger(__name__)
# urllib3 DEBUG logs include the full Telegram request path, which contains the bot token.
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
def is_valid_bot_token(token: object) -> bool:
    return isinstance(token, str) and re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is not None


def _bot_path(token: str, method: str) -> str:
    """Build a path from a token validated by the form or DB reader."""
    return f"/bot{token}/{method}"


def _post_bot(
    path: str, *, return_status: bool = False, timeout: float = 5,
    json: dict | None = None, data: dict | None = None, files: dict | None = None,
) -> bool:
    # Prepare the body against a token-free URL so transport errors never carry the bot token.
    prepared = requests.Request(
        "POST", "https://api.telegram.org", json=json, data=data, files=files
    ).prepare()
    with closing(HTTPSConnection("api.telegram.org", timeout=timeout)) as connection:
        connection.request(
            "POST", path, body=prepared.body,
            headers=dict(prepared.headers),
        )
        status = connection.getresponse().status

    if not 200 <= status < 300:
        logger.warning("Telegram request failed with HTTP %s", status)
        if return_status:
            return False
        raise RuntimeError(f"Telegram request failed with HTTP {status}")
    return True


def _telegram_bots() -> list[dict]:
    bots = []
    for bot in db.select("telegram_bots"):
        token = bot.get("token")
        raw_chat_id = bot.get("chat_id")
        chat_id = (
            str(raw_chat_id).strip()
            if isinstance(raw_chat_id, (str, int)) and not isinstance(raw_chat_id, bool)
            else ""
        )
        if not is_valid_bot_token(token) or not chat_id:
            logger.error("Telegram bot %s has invalid token or chat ID", bot.get("id"))
            raise ValueError(f"Telegram bot {bot.get('id')} has invalid token or chat ID")
        bots.append({"token": token, "chat_id": chat_id})
    return bots


def notify_telegram(message, important=False):
    prefix = "[IMPORTANT] " if important else "[INFO] "
    for bot in _telegram_bots():
        _post_bot(
            _bot_path(bot["token"], "sendMessage"),
            json={"chat_id": bot["chat_id"], "text": prefix + message},
            timeout=5,
        )


def notify_telegram_video(video_path, caption="", important=False):
    prefix = "[VIDEO IMPORTANT] " if important else "[VIDEO] "
    for bot in _telegram_bots():
        with open(video_path, "rb") as video:
            _post_bot(
                _bot_path(bot["token"], "sendVideo"),
                files={"video": video},
                data={"chat_id": bot["chat_id"], "caption": prefix + caption},
                timeout=30,
            )
