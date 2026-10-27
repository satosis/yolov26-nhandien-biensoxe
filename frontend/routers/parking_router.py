import math
import re
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse
from backend.services.database import db
from backend.services.config import normalize_plate
from frontend.deps import get_menu_context, require_menu, templates
from frontend.pagination import build_page_base_url, paginate_counts

router = APIRouter(prefix="/parking")


def get_occupancy_stats(site_id=None) -> list[dict]:
    query = """
        SELECT
            s.id,
            s.site_name,
            s.total_slots,
            COUNT(vs.id) FILTER (WHERE vs.status = 'parking') AS occupied_slots
        FROM sites s
        LEFT JOIN vehicle_sessions vs ON s.id = vs.site_id
    """
    params = []
    if site_id is not None:
        query += " WHERE s.id = %s"
        params.append(site_id)
    query += " GROUP BY s.id, s.site_name, s.total_slots ORDER BY s.site_name"
    return db.execute_query(query, tuple(params) if params else None, fetchall=True)


def count_parking_history(date_filter=None) -> int:
    where = ""
    params = []
    if date_filter:
        where = "WHERE vs.entry_time::date = %s"
        params.append(date_filter)
    row = db.execute_query(f"SELECT COUNT(*) AS total FROM vehicle_sessions vs {where}", tuple(params) if params else None, fetch=True)
    return row.get("total", 0) if row else 0


def get_parking_history(limit=20, offset=0, date_filter=None) -> list[dict]:
    where = ""
    params = []
    if date_filter:
        where = "WHERE vs.entry_time::date = %s"
        params.append(date_filter)
    params.extend((limit, offset))
    query = f"""
        SELECT
            vs.*,
            vs.plate_norm AS plate_number,
            s.site_name,
            c_in.camera_name AS cam_in,
            c_out.camera_name AS cam_out,
            COALESCE(c_in.camera_name, c_out.camera_name) AS camera_name
        FROM vehicle_sessions vs
        LEFT JOIN sites s ON vs.site_id = s.id
        LEFT JOIN cameras c_in ON c_in.id::text = vs.camera_id_in
        LEFT JOIN cameras c_out ON c_out.id::text = vs.camera_id_out
        {where}
        ORDER BY vs.entry_time DESC
        LIMIT %s OFFSET %s
    """
    return db.execute_query(query, tuple(params), fetchall=True)


def _aggregate_stats(rows: list) -> dict:
    total_slots = sum(r.get("total_slots") or 0 for r in rows)
    occupied = sum(r.get("occupied_slots") or 0 for r in rows)
    return {"total_slots": total_slots, "occupied": occupied}


def _get_analytics_kpi(site_id=None) -> dict:
    where = ""
    params = []
    if site_id is not None:
        where = "WHERE site_id = %s"
        params.append(site_id)

    query = f"""
        SELECT
            COUNT(*) FILTER (WHERE entry_time >= CURRENT_DATE) AS today_count,
            COUNT(*) FILTER (WHERE entry_time >= date_trunc('week', CURRENT_DATE)) AS week_count,
            COUNT(*) FILTER (WHERE entry_time >= date_trunc('month', CURRENT_DATE)) AS month_count,
            AVG(stay_duration) FILTER (WHERE stay_duration IS NOT NULL AND stay_duration > 0) AS avg_duration_sec
        FROM vehicle_sessions
        {where}
    """
    row = db.execute_query(query, tuple(params) if params else None, fetch=True)
    if not row:
        return {"today_count": 0, "week_count": 0, "month_count": 0, "avg_duration": "—"}

    avg_sec = row.get("avg_duration_sec")
    if avg_sec:
        avg_min = int(avg_sec // 60)
        if avg_min < 60:
            avg_str = f"{avg_min} phút"
        else:
            avg_str = f"{avg_min // 60}h {avg_min % 60}p"
    else:
        avg_str = "—"

    return {
        "today_count": row.get("today_count") or 0,
        "week_count": row.get("week_count") or 0,
        "month_count": row.get("month_count") or 0,
        "avg_duration": avg_str,
    }


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


@router.get("_dashboard")
async def dashboard(request: Request, user: dict = Depends(require_menu("parking_dashboard"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_dashboard"
    site_id = request.session.get("active_site_id") or None
    rows = get_occupancy_stats(site_id=site_id)
    ctx["stats"] = _aggregate_stats(rows)
    return templates.TemplateResponse("pages/parking_dashboard.html", {"request": request, **ctx})


@router.get("_logs")
async def logs(
    request: Request,
    date: str = None,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("parking_logs")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_logs"
    total = count_parking_history(date_filter=date or None)
    total_pages = max(1, math.ceil(total / 20))
    page_num = max(1, min(page, total_pages))
    offset = (page_num - 1) * 20

    ctx["logs"] = get_parking_history(limit=20, offset=offset, date_filter=date or None)
    ctx["page"] = page_num
    ctx["total_pages"] = total_pages
    ctx["total"] = total
    ctx["filter_date"] = date or ""
    extra = {"date": date} if date else None
    ctx["page_base_url"] = build_page_base_url("/admin/parking_logs", extra)
    return templates.TemplateResponse("pages/parking_logs.html", {"request": request, **ctx})


@router.put("/logs/{session_id}")
async def update_parking_history_plate(
    session_id: int,
    request: Request,
    user: dict = Depends(require_menu("parking_logs")),
):
    data = await request.json()
    plate = normalize_plate(str(data.get("plate") or ""))
    if not re.fullmatch(r"[A-Z0-9]{4,16}", plate):
        return JSONResponse({"ok": False, "message": "Biển số không hợp lệ."}, status_code=400)
    updated = db._execute_query(
        "UPDATE vehicle_sessions SET plate_norm = %s WHERE id = %s RETURNING id",
        (plate, session_id), fetch=True, commit=True,
    )
    if not updated:
        return JSONResponse({"ok": False, "message": "Không tìm thấy bản ghi."}, status_code=404)
    return JSONResponse({"ok": True, "plate": plate})


@router.delete("/logs/{session_id}")
async def delete_parking_history(
    session_id: int,
    user: dict = Depends(require_menu("parking_logs")),
):
    deleted = db._execute_query(
        "DELETE FROM vehicle_sessions WHERE id = %s RETURNING id",
        (session_id,), fetch=True, commit=True,
    )
    if not deleted:
        return JSONResponse({"ok": False, "message": "Không tìm thấy bản ghi."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã xóa bản ghi."})


@router.get("_analytics")
async def analytics(
    request: Request,
    page: int = Query(1, ge=1),
    user: dict = Depends(require_menu("parking_analytics")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_analytics"
    site_id = request.session.get("active_site_id") or None
    rows = get_occupancy_stats(site_id=site_id)
    history = get_parking_history(limit=200)
    kpi_stats = _get_analytics_kpi(site_id=site_id)
    kpi_stats.update(_aggregate_stats(rows))
    ctx["stats"] = kpi_stats
    ctx["peak_hours"] = _build_peak_hours(history)
    recent_pagination = paginate_counts(
        count_parking_history(), page=page, per_page=10,
        base_url="/admin/parking_analytics",
    )
    ctx["recent_events"] = get_parking_history(
        limit=recent_pagination["per_page"],
        offset=(recent_pagination["page"] - 1) * recent_pagination["per_page"],
    )
    ctx["recent_pagination"] = recent_pagination
    return templates.TemplateResponse("pages/parking_analytics.html", {"request": request, **ctx})
