"""Sites router — CRUD with HTMX."""
import json
from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/sites")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _sites_list_html(request: Request, user: dict) -> str:
    """Render just the sites grid (for HTMX swap)."""
    sites = get_db().get_sites()
    ctx = get_menu_context(request, user)
    ctx["site_list"] = sites
    ctx["current_menu"] = "sites"
    # Render only the grid portion
    cards = []
    for s in sites:
        active = "● Hoạt động" if s.get("is_active") else "● Tắt"
        active_cls = "text-status-green" if s.get("is_active") else "text-txt-mut"
        cards.append(f'''<div class="bg-bg-card rounded-lg p-5 flex flex-col gap-3" id="site-{s['id']}">
      <div class="flex items-center justify-between">
        <span class="text-[15px] font-semibold text-white">{s['site_name']}</span>
        <span class="text-[11px] {active_cls}">{active}</span>
      </div>
      <div class="text-[12px] text-txt-ter">{s.get('address') or '—'}</div>
      <div class="text-[12px] text-txt-sec">🅿️ {s.get('total_slots') or 0} chỗ</div>
      <div class="flex gap-2 mt-1">
        <button class="text-txt-sec hover:text-accent text-[12px]" hx-get="/sites/{s['id']}/edit" hx-target="#site-{s['id']}" hx-swap="outerHTML">✏️ Sửa</button>
        <button class="text-txt-sec hover:text-status-red text-[12px]" hx-delete="/sites/{s['id']}" hx-target="#site-{s['id']}" hx-swap="outerHTML" hx-confirm="Xóa {s['site_name']}?">🗑 Xóa</button>
      </div>
    </div>''')
    return "\n".join(cards) if cards else '<div class="col-span-full text-center text-txt-ter text-sm py-8">Chưa có địa điểm nào.</div>'


@router.get("")
async def sites_page(request: Request, user: dict = Depends(require_menu("sites"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "sites"
    ctx["site_list"] = get_db().get_sites()
    return templates.TemplateResponse("pages/sites.html", {"request": request, **ctx})


@router.post("")
async def create_site(
    request: Request,
    user: dict = Depends(require_menu("sites")),
    site_name: str = Form(...),
    address: str = Form(""),
    description: str = Form(""),
    total_slots: int = Form(0),
    is_active: str = Form("on"),
):
    active = is_active in ("on", "true", "1", "yes")
    get_db().add_site(site_name, address or "", description or "", total_slots)
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã thêm {site_name}", "type": "success"}})
    return resp


@router.get("/{site_id}/edit")
async def edit_site_form(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    """Return inline edit form card for HTMX swap."""
    sites = get_db().get_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        return HTMLResponse('<div class="text-status-red text-sm">Không tìm thấy địa điểm.</div>')
    checked = "checked" if site.get("is_active") else ""
    html = f'''<div class="bg-bg-card rounded-lg p-5 flex flex-col gap-3" id="site-{site_id}">
  <form hx-post="/sites/{site_id}/edit" hx-target="#sites-grid" hx-swap="innerHTML" class="flex flex-col gap-3">
    <input name="site_name" value="{site['site_name']}" required
           class="bg-bg-inset text-white text-sm rounded px-3 py-2 border border-white/10 focus:outline-none focus:border-accent" placeholder="Tên địa điểm" />
    <input name="address" value="{site.get('address') or ''}"
           class="bg-bg-inset text-white text-sm rounded px-3 py-2 border border-white/10 focus:outline-none focus:border-accent" placeholder="Địa chỉ" />
    <input name="description" value="{site.get('description') or ''}"
           class="bg-bg-inset text-white text-sm rounded px-3 py-2 border border-white/10 focus:outline-none focus:border-accent" placeholder="Mô tả" />
    <input name="total_slots" type="number" min="0" value="{site.get('total_slots') or 0}"
           class="bg-bg-inset text-white text-sm rounded px-3 py-2 border border-white/10 focus:outline-none focus:border-accent" placeholder="Số chỗ" />
    <label class="flex items-center gap-2 text-sm text-txt-sec">
      <input type="checkbox" name="is_active" value="on" {checked} class="accent-accent" /> Hoạt động
    </label>
    <div class="flex gap-2">
      <button type="submit" class="bg-accent text-bg-page text-xs font-semibold px-4 py-1.5 rounded hover:opacity-90">Lưu</button>
      <button type="button" class="text-txt-sec text-xs px-3 py-1.5 rounded hover:text-white"
              hx-get="/sites" hx-target="#sites-grid" hx-swap="innerHTML">Hủy</button>
    </div>
  </form>
</div>'''
    return HTMLResponse(html)


@router.post("/{site_id}/edit")
async def update_site(
    site_id: int,
    request: Request,
    user: dict = Depends(require_menu("sites")),
    site_name: str = Form(...),
    address: str = Form(""),
    description: str = Form(""),
    total_slots: int = Form(0),
    is_active: str = Form(None),
):
    active = is_active in ("on", "true", "1", "yes")
    get_db().update_site(site_id, site_name, address or "", description or "", active, total_slots)
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã cập nhật {site_name}", "type": "success"}})
    return resp


@router.delete("/{site_id}")
async def delete_site(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    get_db().delete_site(site_id)
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": "Đã xóa địa điểm", "type": "success"}})
    return resp
