from fastapi import APIRouter, Request, Depends, Query
from urllib.parse import urlencode
from frontend.deps import get_db, get_menu_context, require_menu, templates
import os
import psutil
import math
from backend.services.config import RECORDING_DIR

def format_size(size_bytes):
    if not size_bytes: return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1)
    return f"{s} {units[i]}"

PER_PAGE = 20

router = APIRouter(prefix="/recordings")


def get_recordings(db, site_id=None, limit=50, offset=0):
    query = "SELECT * FROM video_recordings"
    params = []
    if site_id:
        query += " WHERE site_id = %s"
        params.append(site_id)
    query += " ORDER BY start_time DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return db._execute_query(query, tuple(params), fetchall=True, commit=False)


def count_recordings(db, site_id=None):
    query = "SELECT COUNT(*) FROM video_recordings"
    params = []
    if site_id:
        query += " WHERE site_id = %s"
        params.append(site_id)
    row = db._execute_query(query, tuple(params) if params else None, fetch=True, commit=False)
    return row["count"]


@router.get("")
async def recordings_page(
    request: Request,
    user: dict = Depends(require_menu("recordings")),
    site_id: int = Query(None),
    page: int = Query(1, ge=1)
):
    db = get_db()
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "recordings"

    active_site = site_id or request.session.get("active_site_id", 0)
    if active_site == 0: active_site = None

    total = count_recordings(db, site_id=active_site)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    recordings = get_recordings(db, site_id=active_site, limit=PER_PAGE, offset=offset)

    for rec in recordings:
        if rec["file_path"]:
            rec["video_url"] = f"/data/recordings/{os.path.basename(rec['file_path'])}"
        if rec["thumbnail_path"]:
            rec["thumb_url"] = f"/data/recordings/{os.path.basename(rec['thumbnail_path'])}"
        else:
            rec["thumb_url"] = "/static/images/video-placeholder.png"
        rec["formatted_size"] = format_size(rec.get("file_size_bytes", 0))

    try:
        disk = psutil.disk_usage(RECORDING_DIR)
        ctx["storage"] = {
            "used": format_size(disk.used),
            "total": format_size(disk.total),
            "percent": disk.percent,
            "free": format_size(disk.free)
        }
    except OSError:
        ctx["storage"] = {"used": "N/A", "total": "N/A", "percent": 0, "free": "N/A"}

    ctx["recordings"] = recordings
    ctx["page"] = page
    ctx["total_pages"] = total_pages
    ctx["total"] = total

    base_params = {}
    if active_site:
        base_params["site_id"] = active_site
    qs = urlencode(base_params)
    ctx["page_base_url"] = "/admin/recordings?" + (qs + "&" if qs else "")

    return templates.TemplateResponse("pages/recordings.html", {"request": request, **ctx})

@router.delete("/{recording_id}")
async def delete_recording(recording_id: int, user: dict = Depends(require_menu("recordings"))):
    db = get_db()
    rec = db.select("video_recordings", conditions={"id": recording_id}, fetchall=False)
    if not rec:
        return {"success": False, "message": "Record not found"}

    for key in ("file_path", "thumbnail_path"):
        path = rec[key]
        if path and os.path.exists(path):
            os.remove(path)

    db.delete("video_recordings", {"id": recording_id})
    return {"success": True}
