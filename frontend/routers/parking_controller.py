import math
import re
from fastapi import Request
from fastapi.responses import JSONResponse
from backend.services.config import normalize_plate
from frontend.deps import get_menu_context, templates
from frontend.pagination import build_page_base_url, paginate_counts
import frontend.routers.parking_queries as queries


def count_parking_history(date_filter=None) -> int:
    where = ""
    params = []
    if date_filter:
        where = "WHERE vs.entry_time::date = %s"
        params.append(date_filter)
    row = queries.count_parking_history_select_vehicle_sessions(params, where)
    return row.get("total", 0) if row else 0


def _aggregate_stats(rows: list) -> dict:
    total_slots = sum(r.get("total_slots") or 0 for r in rows)
    occupied = sum(r.get("occupied_slots") or 0 for r in rows)
    return {"total_slots": total_slots, "occupied": occupied}


def _build_peak_hours(history: list) -> dict:
    counts: dict[int, int] = {}
    from datetime import date
    today = date.today()
    for row in history:
        et = row.get("entry_time")
        if et and hasattr(et, "date") and et.date() == today:
            h = et.hour
            counts[h] = counts.get(h, 0) + 1
    return counts


async def dashboard(request: Request, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_dashboard"
    site_id = request.session.get("active_site_id") or None
    rows = queries.get_occupancy_stats(site_id=site_id)
    ctx["stats"] = _aggregate_stats(rows)
    return templates.TemplateResponse("pages/parking_dashboard.html", {"request": request, **ctx})


async def logs(request: Request, date: str, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_logs"
    total = count_parking_history(date_filter=date or None)
    total_pages = max(1, math.ceil(total / 20))
    page_num = max(1, min(page, total_pages))
    offset = (page_num - 1) * 20

    ctx["logs"] = queries.get_parking_history(limit=20, offset=offset, date_filter=date or None)
    ctx["page"] = page_num
    ctx["total_pages"] = total_pages
    ctx["total"] = total
    ctx["filter_date"] = date or ""
    extra = {"date": date} if date else None
    ctx["page_base_url"] = build_page_base_url("/admin/parking_logs", extra)
    return templates.TemplateResponse("pages/parking_logs.html", {"request": request, **ctx})


async def update_parking_history_plate(session_id: int, request: Request, user: dict):
    try:
        data = await request.json()
    except ValueError:  # body rỗng hoặc không phải JSON
        data = None
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "message": "Dữ liệu gửi lên không hợp lệ."}, status_code=400)
    plate = normalize_plate(str(data.get("plate") or ""))
    if not re.fullmatch(r"[A-Z0-9]{4,16}", plate):
        return JSONResponse({"ok": False, "message": "Biển số không hợp lệ."}, status_code=400)
    updated = queries.update_parking_history_plate_update_vehicle_sessions(plate, session_id)
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy bản ghi."}, status_code=404)
    return JSONResponse({"ok": True, "plate": plate})


async def delete_parking_history(session_id: int, user: dict):
    deleted = queries.delete_parking_history_delete_vehicle_sessions(session_id)
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy bản ghi."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã xóa bản ghi."})


async def analytics(request: Request, page: int, user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_analytics"
    site_id = request.session.get("active_site_id") or None
    rows = queries.get_occupancy_stats(site_id=site_id)
    history = queries.get_parking_history(limit=200)
    kpi_stats = queries._get_analytics_kpi(site_id=site_id)
    kpi_stats.update(_aggregate_stats(rows))
    ctx["stats"] = kpi_stats
    ctx["peak_hours"] = _build_peak_hours(history)
    recent_pagination = paginate_counts(
        count_parking_history(), page=page, per_page=10,
        base_url="/admin/parking_analytics",
    )
    ctx["recent_events"] = queries.get_parking_history(
        limit=recent_pagination["per_page"],
        offset=(recent_pagination["page"] - 1) * recent_pagination["per_page"],
    )
    ctx["recent_pagination"] = recent_pagination
    return templates.TemplateResponse("pages/parking_analytics.html", {"request": request, **ctx})
