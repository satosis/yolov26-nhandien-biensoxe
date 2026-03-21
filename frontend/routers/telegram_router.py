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
    ctx["bots"] = get_db().get_telegram_bots()
    return templates.TemplateResponse("pages/telegram.html", {"request": request, **ctx})


@router.post("")
async def add_bot(
    request: Request,
    bot_name: str = Form(...),
    token: str = Form(...),
    chat_id: str = Form(...),
    user: dict = Depends(require_menu("telegram")),
):
    ok = get_db().upsert_telegram_bot(bot_name.strip(), token.strip(), chat_id.strip())
    if not ok:
        logger.error("upsert_telegram_bot failed for bot_name=%s", bot_name)
    return RedirectResponse(url="/telegram", status_code=303)


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
