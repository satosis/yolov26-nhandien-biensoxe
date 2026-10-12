import asyncio
import logging

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import JSONResponse, RedirectResponse
from backend.services.database import db
from backend.services.telegram_service import _bot_url, _post_bot, is_valid_bot_token
from frontend.deps import get_menu_context, require_menu, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram")


def upsert_telegram_bot(bot_name: str, token: str, chat_id: str) -> bool:
    query = """
        INSERT INTO telegram_bots (bot_name, token, chat_id, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (bot_name) DO UPDATE SET
        token = EXCLUDED.token,
        chat_id = EXCLUDED.chat_id,
        updated_at = NOW()
    """
    return db.execute_query(query, (bot_name, token, chat_id), commit=True)


def _telegram_page(request: Request, user: dict, error: str | None = None, status_code: int = 200):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "telegram"
    ctx["bots"] = db.select("telegram_bots", order_by="id ASC")
    ctx["error"] = error
    return templates.TemplateResponse(
        "pages/telegram.html", {"request": request, **ctx}, status_code=status_code
    )


def _test_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status_code)


@router.get("")
async def telegram_page(request: Request, user: dict = Depends(require_menu("telegram"))):
    return _telegram_page(request, user)


@router.post("")
async def add_bot(
    request: Request,
    bot_name: str = Form(...),
    token: str = Form(None),
    bot_token: str = Form(None),
    chat_id: str = Form(...),
    user: dict = Depends(require_menu("telegram")),
):
    final_token = (token.strip() if token and token.strip() else (bot_token or "").strip())
    bot_name = bot_name.strip()
    chat_id = chat_id.strip()
    if not bot_name or not chat_id:
        return _telegram_page(request, user, "Tên bot và Chat ID không được để trống.", 422)
    if not is_valid_bot_token(final_token):
        return _telegram_page(request, user, "Token bot Telegram không hợp lệ.", 422)

    if not upsert_telegram_bot(bot_name, final_token, chat_id):
        logger.error("Telegram bot configuration could not be saved")
        return _telegram_page(request, user, "Không thể lưu cấu hình bot.", 500)
    return RedirectResponse(url="/admin/telegram", status_code=303)


@router.post("/{bot_name}/test")
async def test_bot(
    bot_name: str,
    user: dict = Depends(require_menu("telegram")),
):
    bot = db.select("telegram_bots", conditions={"bot_name": bot_name}, fetchall=False)
    if not bot:
        return _test_error("Không tìm thấy bot Telegram.", 404)

    token = bot.get("token")
    if not is_valid_bot_token(token):
        logger.error("Telegram test aborted: invalid stored bot token")
        return _test_error("Cấu hình bot Telegram không hợp lệ.", 502)
    url = _bot_url(token, "sendMessage")

    payload = {
        "chat_id": bot["chat_id"],
        "text": f"🤖 Thông báo thử nghiệm\n\nCấu hình: {bot_name}\nTrạng thái: Hoạt động ✅",
    }
    delivered = await asyncio.to_thread(
        _post_bot, url, return_status=True, json=payload, timeout=10.0
    )
    if not delivered:
        return _test_error("Telegram không gửi được tin nhắn thử. Kiểm tra cấu hình bot.", 502)
    return {"ok": True, "message": "Đã gửi tin nhắn thử."}


@router.delete("/{bot_name}")
async def delete_bot(
    bot_name: str,
    request: Request,
    user: dict = Depends(require_menu("telegram")),
):
    deleted = db.execute_query(
        "DELETE FROM telegram_bots WHERE bot_name = %s RETURNING id",
        (bot_name,),
        fetch=True,
        commit=True,
    )
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy bot Telegram."}, status_code=404)
    return {"ok": True}
