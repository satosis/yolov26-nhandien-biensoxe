"""Truy vấn cơ sở dữ liệu của frontend.routers.telegram_controller: mỗi hàm chạy đúng một câu lệnh, controller không gọi db trực tiếp."""
from backend.services.database import db
from backend.services.crypto_service import encrypt_value
import logging

logger = logging.getLogger(__name__)


def telegram_page_select_telegram_bots():
    return db._execute_query("SELECT COUNT(*) AS total FROM telegram_bots", fetch=True, commit=False)


def telegram_page_select_telegram_bots_2(pagination):
    return db._execute_query(
        "SELECT bot_name, token, chat_id FROM telegram_bots ORDER BY id DESC LIMIT %s OFFSET %s",
        (pagination["per_page"], (pagination["page"] - 1) * pagination["per_page"]),
        fetchall=True, commit=False,
    )


def test_bot_select_telegram_bots(bot_name):
    return db.select("telegram_bots", conditions={"bot_name": bot_name}, fetchall=False)


def delete_bot_delete_telegram_bots(bot_name):
    return db.execute_query(
        "DELETE FROM telegram_bots WHERE bot_name = %s RETURNING id",
        (bot_name,),
        fetch=True,
        commit=True,
    )


def upsert_telegram_bot(bot_name: str, token: str, chat_id: str) -> bool:
    query = """
        INSERT INTO telegram_bots (bot_name, token, chat_id, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (bot_name) DO UPDATE SET
        token = EXCLUDED.token,
        chat_id = EXCLUDED.chat_id,
        updated_at = NOW()
    """
    saved = db.execute_query(query, (bot_name, encrypt_value(token), chat_id), commit=True)
    if not saved:
        logger.error("Telegram bot configuration upsert affected no rows")
    return saved
