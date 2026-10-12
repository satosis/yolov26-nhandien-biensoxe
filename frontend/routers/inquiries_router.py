from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
import logging
from frontend.deps import get_db, get_menu_context, require_menu, templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/inquiries")


def get_inquiries(db, limit: int = 100, offset: int = 0, unread_only: bool = False) -> list:
    where = "WHERE is_read = FALSE" if unread_only else ""
    return db._execute_query(
        f"SELECT * FROM inquiries {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset), fetchall=True, commit=False
    )


def count_unread_inquiries(db) -> int:
    row = db._execute_query("SELECT COUNT(*) AS cnt FROM inquiries WHERE is_read = FALSE", fetch=True, commit=False)
    return row["cnt"] if row else 0


def mark_inquiry_read(db, inquiry_id: int) -> bool:
    return bool(db._execute_query("UPDATE inquiries SET is_read = TRUE WHERE id = %s", (inquiry_id,), commit=True))


def update_inquiry_note(db, inquiry_id: int, note: str) -> bool:
    return bool(db._execute_query("UPDATE inquiries SET note = %s WHERE id = %s", (note, inquiry_id), commit=True))


def mark_all_inquiries_read(db) -> bool:
    return bool(db._execute_query("UPDATE inquiries SET is_read = TRUE WHERE is_read = FALSE", commit=True))


def delete_inquiry_record(db, inquiry_id: int) -> bool:
    return bool(db._execute_query("DELETE FROM inquiries WHERE id = %s", (inquiry_id,), commit=True))


def save_inquiry(db, full_name: str, phone: str, company_name: str = "", demand: str = "", source_page: str = "home") -> int | None:
    row = db._execute_query(
        "INSERT INTO inquiries (full_name, phone, company_name, demand, source_page) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (full_name, phone, company_name, demand, source_page), fetch=True, commit=True
    )
    return row["id"] if row else None


@router.get("")
async def inquiries_page(request: Request, user: dict = Depends(require_menu("inquiries"))):
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "inquiries"
    ctx["inquiries"] = get_inquiries(db, limit=200)
    ctx["unread_count"] = count_unread_inquiries(db)
    return templates.TemplateResponse("pages/inquiries.html", {"request": request, **ctx})


@router.post("/read-all", response_class=HTMLResponse)
async def mark_all_read(__user: dict = Depends(require_menu("inquiries"))):
    mark_all_inquiries_read(get_db())
    return HTMLResponse("ok")


@router.post("/{inquiry_id}/read", response_class=HTMLResponse)
async def mark_read(inquiry_id: int, _user: dict = Depends(require_menu("inquiries"))):
    mark_inquiry_read(get_db(), inquiry_id)
    return HTMLResponse('<svg class="w-5 h-5 text-green-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>')


@router.post("/{inquiry_id}/note", response_class=HTMLResponse)
async def save_note(inquiry_id: int, note: str = Form(""), _user: dict = Depends(require_menu("inquiries"))):
    update_inquiry_note(get_db(), inquiry_id, note.strip())
    return HTMLResponse("")


@router.delete("/{inquiry_id}", response_class=HTMLResponse)
async def delete_inquiry_endpoint(inquiry_id: int, _user: dict = Depends(require_menu("inquiries"))):
    delete_inquiry_record(get_db(), inquiry_id)
    return HTMLResponse("")
