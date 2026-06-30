"""Inquiries router — admin management of landing page contact submissions."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import logging
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/inquiries")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("")
async def inquiries_page(request: Request, user: dict = Depends(require_menu("inquiries"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "inquiries"
    ctx["inquiries"] = get_db().get_inquiries(limit=200)
    ctx["unread_count"] = get_db().count_unread_inquiries()
    return templates.TemplateResponse("pages/inquiries.html", {"request": request, **ctx})


@router.post("/read-all", response_class=HTMLResponse)
async def mark_all_read(__user: dict = Depends(require_menu("inquiries"))):
    get_db().mark_all_inquiries_read()
    return HTMLResponse("ok")


@router.post("/{inquiry_id}/read", response_class=HTMLResponse)
async def mark_read(inquiry_id: int, _user: dict = Depends(require_menu("inquiries"))):
    get_db().mark_inquiry_read(inquiry_id)
    return HTMLResponse('<svg class="w-5 h-5 text-green-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>')


@router.post("/{inquiry_id}/note", response_class=HTMLResponse)
async def save_note(inquiry_id: int, note: str = Form(""), _user: dict = Depends(require_menu("inquiries"))):
    get_db().update_inquiry_note(inquiry_id, note.strip())
    # HTMX form uses hx-swap="none" — return an empty body instead of a wasteful
    # full-page redirect that HTMX would fetch and discard.
    return HTMLResponse("")


@router.delete("/{inquiry_id}", response_class=HTMLResponse)
async def delete_inquiry(inquiry_id: int, _user: dict = Depends(require_menu("inquiries"))):
    get_db().delete_inquiry(inquiry_id)
    return HTMLResponse("")
