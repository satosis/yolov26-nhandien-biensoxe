import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from backend.services.database import db
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


@router.get("")
async def telegram_page(request: Request, user: dict = Depends(require_menu("telegram"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "telegram"
    ctx["bots"] = db.select("telegram_bots", order_by="id ASC")
    return templates.TemplateResponse("pages/telegram.html", {"request": request, **ctx})


@router.post("")
async def add_bot(
    request: Request,
    bot_name: str = Form(...),
    token: str = Form(None),
    bot_token: str = Form(None),
    chat_id: str = Form(...),
    user: dict = Depends(require_menu("telegram")),
):
    final_token = token or bot_token
    if not final_token:
        logger.error("Missing token for bot_name=%s", bot_name)
        return RedirectResponse(url="/admin/telegram", status_code=303)
        
    upsert_telegram_bot(bot_name.strip(), final_token.strip(), chat_id.strip())
    return RedirectResponse(url="/admin/telegram", status_code=303)


@router.post("/{bot_name}/test")
async def test_bot(
    bot_name: str,
    request: Request,
    user: dict = Depends(require_menu("telegram")),
):
    import httpx
    bot = db.select("telegram_bots", conditions={"bot_name": bot_name}, fetchall=False)
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
    db.delete("telegram_bots", {"bot_name": bot_name})
    return RedirectResponse(url="/admin/telegram", status_code=303)
