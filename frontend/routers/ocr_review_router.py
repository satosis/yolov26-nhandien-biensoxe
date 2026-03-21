"""OCR Review router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/ocr_review")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

@router.get("")
async def ocr_page(request: Request, user: dict = Depends(require_menu("ocr_review"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "ocr_review"
    ctx["samples"] = get_db().get_ocr_training_samples(status="pending", limit=50)
    return templates.TemplateResponse("pages/ocr_review.html", {"request": request, **ctx})

@router.post("/{sample_id}/approve")
async def approve_ocr(sample_id: int, request: Request, user: dict = Depends(require_menu("ocr_review"))):
    form = await request.form()
    corrected = form.get("corrected_text", "")
    username = user.get("username", "admin")
    get_db().review_ocr_sample(sample_id, corrected, username, status="approved")
    return RedirectResponse("/ocr_review", status_code=303)

@router.delete("/{sample_id}")
async def delete_ocr(sample_id: int, user: dict = Depends(require_menu("ocr_review"))):
    get_db().delete_ocr_sample(sample_id)
    return RedirectResponse("/ocr_review", status_code=303)
