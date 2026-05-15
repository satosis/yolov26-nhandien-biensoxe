"""Telegram router."""
import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def telegram_page(request: Request, user: dict = Depends(require_menu("telegram"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "telegram"
    db = get_db()
    ctx["bots"] = db.get_telegram_bots()
    return templates.TemplateResponse("pages/telegram.html", {"request": request, **ctx})


@router.post("")
async def add_bot(
    request: Request,
    bot_name: str = Form(...),
    token: str = Form(None),
    bot_token: str = Form(None),  # Handle both 'token' and 'bot_token' (from add vs edit form)
    chat_id: str = Form(...),
    user: dict = Depends(require_menu("telegram")),
):
    final_token = token or bot_token
    if not final_token:
        logger.error("Missing token for bot_name=%s", bot_name)
        return RedirectResponse(url="/telegram", status_code=303)
        
    ok = get_db().upsert_telegram_bot(bot_name.strip(), final_token.strip(), chat_id.strip())
    if not ok:
        logger.error("upsert_telegram_bot failed for bot_name=%s", bot_name)
    return RedirectResponse(url="/telegram", status_code=303)


@router.post("/{bot_name}/test")
async def test_bot(
    bot_name: str,
    request: Request,
    user: dict = Depends(require_menu("telegram")),
):
    import httpx
    bot = next((b for b in get_db().get_telegram_bots() if b['bot_name'] == bot_name), None)
    if not bot:
        return {"ok": False, "message": "Bot not found"}
    
    url = f"https://api.telegram.org/bot{bot['token']}/sendMessage"
    payload = {
        "chat_id": bot['chat_id'],
        "text": f"🤖 *Thông báo thử nghiệm*\n\nCấu hình: `{bot_name}`\nTrạng thái: Hoạt động ✅\nThời gian: {request.state.start_time if hasattr(request.state, 'start_time') else ''}",
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10.0)
        result = resp.json()
        if result.get("ok"):
            return {"ok": True}
        return {"ok": False, "message": result.get("description", "Unknown error")}


@router.delete("/{bot_name}")
async def delete_bot(
    bot_name: str,
    request: Request,
    user: dict = Depends(require_menu("telegram")),
):
    ok = get_db().delete_telegram_bot(bot_name)
    if not ok:
        logger.error("delete_telegram_bot failed for bot_name=%s", bot_name)
    return RedirectResponse(url="/telegram", status_code=303)
