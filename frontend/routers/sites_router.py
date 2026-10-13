import json
from html import escape
from fastapi import APIRouter, Request, Form, Depends
from starlette.responses import HTMLResponse
from frontend.deps import get_menu_context, require_menu, templates
from backend.services.database import db

router = APIRouter(prefix="/sites")


def _sites_list_html(request: Request, user: dict) -> str:
    sites = db.select("sites", order_by="site_name")
    rows = []
    for s in sites:
        name = escape(str(s.get("site_name") or ""), quote=True)
        address = escape(str(s.get("address") or "—"), quote=True)
        delete_message = escape(json.dumps(f"Bạn chắc chắn muốn xóa địa điểm {s.get('site_name') or ''}?", ensure_ascii=False), quote=True)
        is_active = bool(s.get("is_active"))
        btn_cls = "btn-success" if is_active else "btn-secondary"
        dot_bg = "#28a745" if is_active else "#6c757d"
        active_txt = "Bật" if is_active else "Tắt"
        rows.append(f'''<tr id="site-{s['id']}">
      <td><span class="font-weight-bold" style="font-size:13px;">{name}</span></td>
      <td class="text-muted" style="font-size:13px;">{address}</td>
      <td class="text-muted" style="font-size:13px;">🅿️ {s.get('total_slots') or 0} chỗ</td>
      <td class="text-center">
        <form hx-post="/admin/sites/{s['id']}/toggle" hx-target="#sites-list" hx-swap="innerHTML">
          <button type="submit" class="btn btn-sm {btn_cls} d-inline-flex align-items-center" style="gap:6px;">
            <span class="rounded-circle" style="width:8px;height:8px;display:inline-block;background:{dot_bg};"></span>
            {active_txt}
          </button>
        </form>
      </td>
      <td class="text-right">
        <div class="d-flex justify-content-end" style="gap:6px;">
          <button class="btn btn-sm btn-light" title="Sửa" hx-get="/admin/sites/{s['id']}/edit" hx-target="#site-{s['id']}" hx-swap="outerHTML"><i class="bx bx-edit-alt"></i></button>
          <button class="btn btn-sm btn-danger" title="Xóa"
                  @click="window.dispatchEvent(new CustomEvent('show-confirm', {{
                    detail: {{
                      title: 'Xóa địa điểm',
                      message: {delete_message},
                      confirmText: 'Xác nhận xóa',
                      confirmUrl: '/admin/sites/{s['id']}',
                      confirmMethod: 'DELETE',
                      confirmTarget: '#site-{s['id']}'
                    }}
                  }}))"><i class="bx bx-trash"></i></button>
        </div>
      </td>
    </tr>''')
    return "\n".join(rows) if rows else '<tr><td colspan="5" class="text-center text-muted py-4">Chưa có địa điểm nào.</td></tr>'


@router.get("")
async def sites_page(request: Request, user: dict = Depends(require_menu("sites"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "sites"
    ctx["site_list"] = db.select("sites", order_by="site_name")
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
    site_id = db.insert("sites", {
        "site_name": site_name,
        "address": address or "",
        "description": description or "",
        "total_slots": total_slots,
        "is_active": active
    }, returning="id")
    if not site_id:
        return HTMLResponse("Không thể thêm địa điểm.", status_code=500)
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã thêm {site_name}", "type": "success"}})
    return resp


@router.get("/{site_id}/edit")
async def edit_site_form(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    site = db.select("sites", conditions={"id": site_id}, fetchall=False)
    if not site:
        return HTMLResponse('<div class="px-5 py-4 text-status-red text-sm">Không tìm thấy địa điểm.</div>', status_code=404)
    checked = "checked" if site.get("is_active") else ""
    site_name = escape(str(site.get("site_name") or ""), quote=True)
    address = escape(str(site.get("address") or ""), quote=True)
    html = f'''<tr id="site-{site_id}">
  <td colspan="5" class="p-3 bg-light border-top border-bottom">
    <form hx-post="/admin/sites/{site_id}/edit" hx-target="#sites-list" hx-swap="innerHTML">
      <div class="row">
        <div class="col-md-6 mb-2">
          <label class="font-weight-bold" style="font-size:12px;">Tên địa điểm *</label>
          <input name="site_name" value="{site_name}" required class="form-control form-control-sm" />
        </div>
        <div class="col-md-6 mb-2">
          <label class="font-weight-bold" style="font-size:12px;">Địa chỉ</label>
          <input name="address" value="{address}" class="form-control form-control-sm" />
        </div>
      </div>
      <div class="row">
        <div class="col-md-6 mb-2">
          <label class="font-weight-bold" style="font-size:12px;">Sức chứa</label>
          <input name="total_slots" type="number" min="0" value="{site.get('total_slots') or 0}" class="form-control form-control-sm" />
        </div>
        <div class="col-md-6 mb-2 d-flex align-items-center pt-3">
          <div class="form-check">
            <input type="checkbox" name="is_active" value="on" {checked} class="form-check-input" id="site-active-{site_id}" />
            <label class="form-check-label font-weight-medium" for="site-active-{site_id}" style="font-size:13px;">Đang hoạt động</label>
          </div>
        </div>
      </div>
      <div class="d-flex mt-2" style="gap:8px;">
        <button type="submit" class="btn btn-primary btn-sm font-weight-bold"><i class="bx bx-save mr-1"></i> Cập nhật</button>
        <button type="button" class="btn btn-light btn-sm" hx-get="/admin/sites" hx-target="#sites-list" hx-swap="innerHTML">Hủy</button>
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
    updated = db.update("sites", {
        "site_name": site_name,
        "address": address or "",
        "description": description or "",
        "is_active": active,
        "total_slots": total_slots
    }, {"id": site_id})
    if not updated:
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã cập nhật {site_name}", "type": "success"}})
    return resp


@router.delete("/{site_id}")
async def delete_site(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    if not db.delete("sites", {"id": site_id}):
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": "Đã xóa địa điểm", "type": "success"}})
    return resp


@router.post("/{site_id}/toggle")
async def toggle_site_status(site_id: int, request: Request, user: dict = Depends(require_menu("sites"))):
    site = db.select("sites", conditions={"id": site_id}, fetchall=False)
    if not site:
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    new_status = not site.get("is_active", False)
    db.update("sites", {"is_active": new_status}, {"id": site_id})
    msg = f"Đã {'bật' if new_status else 'tắt'} {site['site_name']}"
    html = _sites_list_html(request, user)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": msg, "type": "success" if new_status else "info"}})
    return resp
