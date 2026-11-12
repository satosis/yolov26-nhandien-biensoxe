import asyncio
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from backend.services.crypto_service import decrypt_value
from backend.services.telegram_service import _bot_path, _post_bot, is_valid_bot_token
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
import frontend.routers.telegram_queries as queries

logger = logging.getLogger(__name__)


def _requested_page(request: Request) -> int:
    try:
        return max(1, int(request.query_params.get("page", "1") or 1))
    except (TypeError, ValueError):
        return 1


def _telegram_page(request: Request, user: dict, error: str | None = None, status_code: int = 200):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "telegram"
    requested_page = _requested_page(request)
    count_row = queries.telegram_page_select_telegram_bots() or {}
    pagination = paginate_counts(
        int(count_row.get("total") or 0), page=requested_page, per_page=20,
        base_url="/admin/telegram",
    )
    raw_bots = queries.telegram_page_select_telegram_bots_2(pagination) or []
    for b in raw_bots:
        b["token"] = decrypt_value(b.get("token"))
    ctx["bots"] = raw_bots
    ctx["pagination"] = pagination
    ctx["current_page"] = pagination["page"]
    ctx["error"] = error
    return templates.TemplateResponse(
        "pages/telegram.html", {"request": request, **ctx}, status_code=status_code
    )


def _test_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status_code)


async def telegram_page(request: Request, user: dict):
    return _telegram_page(request, user)


async def add_bot(request: Request, bot_name: str, token: str, bot_token: str, chat_id: str, user: dict):
    final_token = (token.strip() if token and token.strip() else (bot_token or "").strip())
    bot_name = bot_name.strip()
    chat_id = chat_id.strip()
    if not bot_name or not chat_id:
        return _telegram_page(request, user, "Tên bot và Chat ID không được để trống.", 422)
    if not is_valid_bot_token(final_token):
        return _telegram_page(request, user, "Token bot Telegram không hợp lệ.", 422)

    if not queries.upsert_telegram_bot(bot_name, final_token, chat_id):
        return _telegram_page(request, user, "Không thể lưu cấu hình bot.", 500)
    return RedirectResponse(url=f"/admin/telegram?page={_requested_page(request)}", status_code=303)


async def test_bot(bot_name: str, _: dict):
    bot = queries.test_bot_select_telegram_bots(bot_name)
    if not bot:
        return _test_error("Không tìm thấy bot Telegram.", 404)

    token = decrypt_value(bot.get("token"))
    if not is_valid_bot_token(token):
        logger.error("Telegram test aborted: invalid stored bot token")
        return _test_error("Cấu hình bot Telegram không hợp lệ.", 502)
    path = _bot_path(token, "sendMessage")

    payload = {
        "chat_id": bot["chat_id"],
        "text": f"🤖 Thông báo thử nghiệm\n\nCấu hình: {bot_name}\nTrạng thái: Hoạt động ✅",
    }
    await asyncio.to_thread(_post_bot, path, json=payload, timeout=10.0)
    return {"ok": True, "message": "Đã gửi tin nhắn thử."}


async def delete_bot(bot_name: str, _: dict):
    deleted = queries.delete_bot_delete_telegram_bots(bot_name)
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy bot Telegram."}, status_code=404)
    return {"ok": True}
