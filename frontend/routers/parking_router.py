"""Parking router — dashboard, logs, analytics."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from pathlib import Path
from frontend.deps import get_db, get_menu_context, require_menu

router = APIRouter(prefix="/parking")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


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
    rows = get_db().get_occupancy_stats(site_id=site_id)
    ctx["stats"] = _aggregate_stats(rows)
    return templates.TemplateResponse("pages/parking_dashboard.html", {"request": request, **ctx})


@router.get("_logs")
async def logs(request: Request, user: dict = Depends(require_menu("parking_logs"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_logs"
    ctx["logs"] = get_db().get_parking_history(limit=100)
    return templates.TemplateResponse("pages/parking_logs.html", {"request": request, **ctx})


@router.get("_analytics")
async def analytics(request: Request, user: dict = Depends(require_menu("parking_analytics"))):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "parking_analytics"
    db = get_db()
    site_id = request.session.get("active_site_id") or None
    rows = db.get_occupancy_stats(site_id=site_id)
    history = db.get_parking_history(limit=200)
    ctx["stats"] = _aggregate_stats(rows)
    ctx["peak_hours"] = _build_peak_hours(history)
    ctx["recent_events"] = db.get_parking_history(limit=10)
    return templates.TemplateResponse("pages/parking_analytics.html", {"request": request, **ctx})
