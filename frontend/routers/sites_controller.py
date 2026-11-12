import json
from html import escape
from fastapi import Request
from starlette.responses import HTMLResponse
from frontend.deps import get_menu_context, templates
from frontend.pagination import paginate_counts
import frontend.routers.sites_queries as queries


def _site_row_html(s: dict, idx: int, page: int = 1) -> str:
    name = escape(str(s.get("site_name") or ""), quote=True)
    address = escape(str(s.get("address") or "—"), quote=True)
    delete_message = escape(json.dumps(f"Bạn chắc chắn muốn xóa địa điểm {s.get('site_name') or ''}?", ensure_ascii=False), quote=True)
    is_active = bool(s.get("is_active"))
    btn_cls = "btn-success" if is_active else "btn-secondary"
    dot_bg = "#28a745" if is_active else "#6c757d"
    active_txt = "Bật" if is_active else "Tắt"
    return f'''<tr id="site-{s['id']}">
      <td class="text-center text-muted font-weight-medium align-middle">{idx}</td>
      <td><span class="font-weight-bold" style="font-size:13px;">{name}</span></td>
      <td class="text-muted" style="font-size:13px;">{address}</td>
      <td class="text-muted" style="font-size:13px;">🅿️ {s.get('total_slots') or 0} chỗ</td>
      <td class="text-center">
        <form hx-post="/admin/sites/{s['id']}/toggle?page={page}" hx-target="#sites-list" hx-swap="innerHTML">
          <button type="submit" class="btn btn-sm {btn_cls} d-inline-flex align-items-center" style="gap:6px;">
            <span class="rounded-circle" style="width:8px;height:8px;display:inline-block;background:{dot_bg};"></span>
            {active_txt}
          </button>
        </form>
      </td>
      <td class="text-right">
        <div class="d-flex justify-content-end" style="gap:6px;">
          <button class="btn btn-sm btn-light" title="Sửa" hx-get="/admin/sites/{s['id']}/edit?page={page}" hx-target="#site-{s['id']}" hx-swap="outerHTML"><i class="bx bx-edit-alt"></i></button>
          <button class="btn btn-sm btn-danger" title="Xóa"
                  @click="window.dispatchEvent(new CustomEvent('show-confirm', {{
                    detail: {{
                      title: 'Xóa địa điểm',
                      message: {delete_message},
                      confirmText: 'Xác nhận xóa',
                      confirmUrl: '/admin/sites/{s['id']}?page={page}',
                      confirmMethod: 'DELETE',
                      confirmTarget: '#site-{s['id']}'
                    }}
                  }}))"><i class="bx bx-trash"></i></button>
        </div>
      </td>
    </tr>'''


def _sites_list_html(request: Request, user: dict, page: int = 1) -> str:
    offset = (max(1, page) - 1) * 20
    sites = queries.sites_list_html_select_sites(offset) or []
    rows = [_site_row_html(s, idx, page) for idx, s in enumerate(sites, start=offset + 1)]
    return "\n".join(rows) if rows else '<tr><td colspan="6" class="text-center text-muted py-4">Chưa có địa điểm nào.</td></tr>'


async def sites_page(request: Request, page: int, user: dict):
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "sites-list":
        return HTMLResponse(_sites_list_html(request, user, page))

    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "sites"
    count_row = queries.sites_page_select_sites() or {}
    total_sites = int(count_row.get("total") or 0)
    page_data = paginate_counts(total_sites, page=page, per_page=20, base_url="/admin/sites")
    ctx["site_list"] = queries.sites_page_select_sites_2(page_data) or []
    ctx.update(page=page_data["page"], total_pages=page_data["total_pages"],
               total=page_data["total"], per_page=page_data["per_page"], page_base_url=page_data["page_base_url"])
    ctx["total_sites"] = total_sites
    return templates.TemplateResponse("pages/sites.html", {"request": request, **ctx})


async def create_site(
    request: Request,
    user: dict,
    site_name: str,
    address: str,
    description: str,
    total_slots: int,
    is_active: str,
    page: int,
):
    active = is_active in ("on", "true", "1", "yes")
    site_id = queries.create_site_insert_sites(site_name, total_slots, active, address, description)
    if not site_id:
        return HTMLResponse("Không thể thêm địa điểm.", status_code=500)
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = "/admin/sites?page=1"
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã thêm {site_name}", "type": "success"}})
    return resp


async def edit_site_form(site_id: int, request: Request, page: int, user: dict):
    site = queries.select_site_by_id(site_id)
    if not site:
        return HTMLResponse('<div class="px-5 py-4 text-status-red text-sm">Không tìm thấy địa điểm.</div>', status_code=404)
    checked = "checked" if site.get("is_active") else ""
    site_name = escape(str(site.get("site_name") or ""), quote=True)
    address = escape(str(site.get("address") or ""), quote=True)
    html = f'''<tr id="site-{site_id}">
  <td colspan="6" class="p-3 bg-light border-top border-bottom">
    <form hx-post="/admin/sites/{site_id}/edit?page={page}" hx-target="#sites-list" hx-swap="innerHTML">
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
        <button type="button" class="btn btn-light btn-sm" hx-get="/admin/sites/{site_id}/row?page={page}" hx-target="#site-{site_id}" hx-swap="outerHTML">Hủy</button>
      </div>
    </form>
  </td>
  </tr>'''
    return HTMLResponse(html)


async def get_site_row(site_id: int, request: Request, page: int, user: dict):
    site = queries.select_site_by_id(site_id)
    if not site:
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    offset = (max(1, page) - 1) * 20
    sites = queries.get_site_row_select_sites_2(offset) or []
    idx = offset + 1
    for i, s in enumerate(sites, start=offset + 1):
        if s.get("id") == site_id:
            idx = i
            break
    return HTMLResponse(_site_row_html(site, idx, page))


async def update_site(
    site_id: int,
    request: Request,
    user: dict,
    site_name: str,
    address: str,
    description: str,
    total_slots: int,
    is_active: str,
    page: int,
):
    active = is_active in ("on", "true", "1", "yes")
    updated = queries.update_site_update_sites(site_name, active, total_slots, site_id, address, description)
    if not updated:
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    html = _sites_list_html(request, user, page)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": f"Đã cập nhật {site_name}", "type": "success"}})
    return resp


async def delete_site(site_id: int, request: Request, page: int, user: dict):
    if not queries.delete_site_delete_sites(site_id):
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = f"/admin/sites?page={page}"
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": "Đã xóa địa điểm", "type": "success"}})
    return resp


async def toggle_site_status(site_id: int, request: Request, page: int, user: dict):
    site = queries.select_site_by_id(site_id)
    if not site:
        return HTMLResponse("Không tìm thấy địa điểm.", status_code=404)
    new_status = not site.get("is_active", False)
    queries.toggle_site_status_update_sites(new_status, site_id)
    msg = f"Đã {'bật' if new_status else 'tắt'} {site['site_name']}"
    html = _sites_list_html(request, user, page)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": msg, "type": "success" if new_status else "info"}})
    return resp
