"""Parking router — dashboard, logs, analytics."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/parking")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def get_occupancy_stats(db, site_id=None) -> list[dict]:
    """Lấy thống kê chỗ đỗ theo cơ sở."""
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
    return db.execute_query(query, tuple(params) if params else None, fetchall=True) or []


def get_parking_history(db, limit=50, date_filter=None) -> list[dict]:
    """Lấy lịch sử xe ra vào bãi đỗ."""
    where = ""
    params = []
    if date_filter:
        where = "WHERE vs.entry_time::date = %s"
        params.append(date_filter)
    params.append(limit)
    query = f"""
        SELECT vs.*, s.site_name, c_in.camera_name as cam_in, c_out.camera_name as cam_out
        FROM vehicle_sessions vs
        LEFT JOIN sites s ON vs.site_id = s.id
        LEFT JOIN cameras c_in ON vs.camera_id_in = c_in.id
        LEFT JOIN cameras c_out ON vs.camera_id_out = c_out.id
        {where}
        ORDER BY vs.entry_time DESC
        LIMIT %s
    """
    return db.execute_query(query, tuple(params), fetchall=True) or []


def _aggregate_stats(rows: list) -> dict:
    """Collapse per-site occupancy rows into a single summary dict."""
    total_slots = sum(r.get("total_slots") or 0 for r in rows)
    occupied = sum(r.get("occupied_slots") or 0 for r in rows)
    return {"total_slots": total_slots, "occupied": occupied}


def _build_peak_hours(history: list) -> dict:
    """Build hour→count dict from parking history entry_time."""
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
    db = get_db()
    rows = get_occupancy_stats(db, site_id=site_id)
    ctx["stats"] = _aggregate_stats(rows)
    return templates.TemplateResponse("pages/parking_dashboard.html", {"request": request, **ctx})


@router.get("_logs")
async def logs(request: Request, date: str = None, user: dict = Depends(require_menu("parking_logs"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_logs"
    db = get_db()
    ctx["logs"] = get_parking_history(db, limit=100, date_filter=date or None)
    ctx["filter_date"] = date or ""
    return templates.TemplateResponse("pages/parking_logs.html", {"request": request, **ctx})


@router.get("_analytics")
async def analytics(request: Request, user: dict = Depends(require_menu("parking_analytics"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_analytics"
    db = get_db()
    site_id = request.session.get("active_site_id") or None
    rows = get_occupancy_stats(db, site_id=site_id)
    history = get_parking_history(db, limit=200)
    ctx["stats"] = _aggregate_stats(rows)
    ctx["peak_hours"] = _build_peak_hours(history)
    ctx["recent_events"] = get_parking_history(db, limit=10)
    return templates.TemplateResponse("pages/parking_analytics.html", {"request": request, **ctx})
