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
    """Render sites as table rows consistent with door_providers."""
    db = get_db()
    sites = db.select("sites", order_by="site_name") or []
    rows = []
    for s in sites:
        active = "Bật" if s.get("is_active") else "Tắt"
        active_cls = "text-status-green" if s.get("is_active") else "text-txt-mut"
        dot_cls = "bg-status-green animate-pulse" if s.get("is_active") else "bg-txt-mut"
        rows.append(f'''<tr class="border-b border-divider hover:bg-white/[0.02] transition-colors" id="site-{s['id']}">
      <td class="px-5 py-3.5">
        <div class="text-[13px] font-semibold text-white">{s['site_name']}</div>
      </td>
      <td class="px-5 py-3.5 text-[12px] text-txt-sec">
        {s.get('address') or '—'}
      </td>
      <td class="px-5 py-3.5 text-[12px] text-txt-ter">
        🅿️ {s.get('total_slots') or 0} chỗ
      </td>
      <td class="px-5 py-3.5 text-center">
        <form hx-post="/admin/sites/{s['id']}/toggle" hx-target="#sites-list" hx-swap="innerHTML">
          <button type="submit" class="inline-flex items-center gap-1.5 text-[11px] font-medium {active_cls} mx-auto">
            <span class="w-2 h-2 rounded-full {dot_cls}"></span>
            {active}
          </button>
        </form>
      </td>
      <td class="px-5 py-3.5 text-right">
        <div class="flex justify-end gap-3">
          <button class="text-txt-sec hover:text-accent text-sm" hx-get="/admin/sites/{s['id']}/edit" hx-target="#site-{s['id']}" hx-swap="outerHTML">✏️</button>
          <button class="text-txt-sec hover:text-status-red text-sm cursor-pointer" 
                  @click="window.dispatchEvent(new CustomEvent('show-confirm', {{
                    detail: {{
                      title: 'Xóa địa điểm',
                      message: 'Xóa địa điểm {s['site_name']}?',
                      confirmText: 'Xác nhận xóa',
                      confirmUrl: '/admin/sites/{s['id']}',
                      confirmMethod: 'DELETE',
                      confirmTarget: '#site-{s['id']}'
                    }}
                  }}))">🗑</button>
        </div>
      </td>
    </tr>''')
    return "\n".join(rows) if rows else '<tr><td colspan="5" class="px-5 py-8 text-center text-txt-ter text-sm">Chưa có địa điểm nào.</td></tr>'


@router.get("")
async def sites_page(request: Request, user: dict = Depends(require_menu("sites"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "sites"
    db = get_db()
    ctx["site_list"] = db.select("sites", order_by="site_name") or []
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
    db = get_db()
    db.insert("sites", {
        "site_name": site_name,
        "address": address or "",
        "description": description or "",
        "total_slots": total_slots,
        "is_active": active
    }, returning="id")
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã thêm {site_name}", "type": "success"}})
    return resp


@router.get("/{site_id}/edit")
async def edit_site_form(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    """Return inline edit form block for HTMX swap."""
    db = get_db()
    site = db.select("sites", conditions={"id": site_id}, fetchall=False)
    if not site:
        return HTMLResponse('<div class="px-5 py-4 text-status-red text-sm">Không tìm thấy địa điểm.</div>')
    checked = "checked" if site.get("is_active") else ""
    html = f'''<tr class="bg-white/5" id="site-{site_id}">
  <td colspan="5" class="p-5 border-b border-divider">
    <form hx-post="/admin/sites/{site_id}/edit" hx-target="#sites-list" hx-swap="innerHTML" class="flex flex-col gap-4">
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-txt-sec text-[11px] uppercase tracking-wider font-semibold mb-1">Tên địa điểm *</label>
          <input name="site_name" value="{site['site_name']}" required
                 class="w-full bg-bg-inset border border-white/5 rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-accent/50" />
        </div>
        <div>
          <label class="block text-txt-sec text-[11px] uppercase tracking-wider font-semibold mb-1">Địa chỉ</label>
          <input name="address" value="{site.get('address') or ''}"
                 class="w-full bg-bg-inset border border-white/5 rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-accent/50" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-txt-sec text-[11px] uppercase tracking-wider font-semibold mb-1">Sức chứa</label>
          <input name="total_slots" type="number" min="0" value="{site.get('total_slots') or 0}"
                 class="w-full bg-bg-inset border border-white/5 rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-accent/50" />
        </div>
        <div>
          <label class="block text-txt-sec text-[11px] uppercase tracking-wider font-semibold mb-1">Trạng thái</label>
          <label class="flex items-center gap-2 text-sm text-white pt-2 cursor-pointer">
            <input type="checkbox" name="is_active" value="on" {checked} class="accent-accent" /> Đang hoạt động
          </label>
        </div>
      </div>
      <div class="flex gap-3">
        <button type="submit" class="bg-accent text-bg-inset font-semibold text-[13px] px-5 py-2 rounded-md hover:bg-cyan-300">💾 Cập nhật</button>
        <button type="button" class="text-txt-sec text-[13px] px-4 py-2 rounded-md hover:bg-white/5"
                hx-get="/admin/sites" hx-target="#sites-list" hx-swap="innerHTML">✕ Hủy</button>
      </div>
    </form>
  </td>
  </tr>'''
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
    db = get_db()
    db.update("sites", {
        "site_name": site_name,
        "address": address or "",
        "description": description or "",
        "is_active": active,
        "total_slots": total_slots
    }, {"id": site_id})
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã cập nhật {site_name}", "type": "success"}})
    return resp


@router.delete("/{site_id}")
async def delete_site(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    db = get_db()
    db.delete("sites", {"id": site_id})
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": "Đã xóa địa điểm", "type": "success"}})
    return resp


@router.post("/{site_id}/toggle")
async def toggle_site_status(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    db = get_db()
    site = db.select("sites", conditions={"id": site_id}, fetchall=False)
    if site:
        new_status = not site.get("is_active", False)
        db.update("sites", {"is_active": new_status}, {"id": site_id})
        msg = f"Đã {'bật' if new_status else 'tắt'} {site['site_name']}"
        html = _sites_list_html(request, user)
        resp = HTMLResponse(html)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": msg, "type": "success" if new_status else "info"}})
        return resp
    return HTMLResponse(_sites_list_html(request, user))
