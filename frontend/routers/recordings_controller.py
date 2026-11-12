import logging
import math
import os
import psutil
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.services.config import RECORDING_DIR
from frontend.deps import get_menu_context, templates
import frontend.routers.recordings_queries as queries

logger = logging.getLogger(__name__)

def format_size(size_bytes):
    if not size_bytes: return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1)
    return f"{s} {units[i]}"

PER_PAGE = 20


def get_storage_info() -> dict:
    if not os.path.isdir(RECORDING_DIR):
        logger.warning("Recording directory unavailable")
        return {"used": "N/A", "total": "N/A", "percent": None, "free": "N/A"}

    disk = psutil.disk_usage(RECORDING_DIR)
    return {
        "used": format_size(disk.used),
        "total": format_size(disk.total),
        "percent": disk.percent,
        "free": format_size(disk.free),
    }


async def recordings_page(request: Request, user: dict, site_id: int, page: int):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "recordings"

    active_site = site_id or request.session.get("active_site_id", 0)
    if active_site == 0: active_site = None

    total = queries.count_recordings(site_id=active_site)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * PER_PAGE

    recordings = queries.get_recordings(site_id=active_site, limit=PER_PAGE, offset=offset)

    for rec in recordings:
        if rec["file_path"]:
            rec["video_url"] = f"/data/recordings/{os.path.basename(rec['file_path'])}"
        if rec["thumbnail_path"]:
            rec["thumb_url"] = f"/data/recordings/{os.path.basename(rec['thumbnail_path'])}"
        else:
            rec["thumb_url"] = "/static/images/video-placeholder.png"
        rec["formatted_size"] = format_size(rec.get("file_size_bytes", 0))

    ctx["storage"] = get_storage_info()

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

async def delete_recording(recording_id: int, _: dict):
    rec = queries.delete_recording_select_video_recordings(recording_id)
    if not rec:
        return JSONResponse(
            {"ok": False, "success": False, "message": "Không tìm thấy bản ghi."},
            status_code=404,
        )

    for key in ("file_path", "thumbnail_path"):
        path = rec[key]
        if not path:
            continue
        if not os.path.exists(path):
            logger.error("Recording %s references a missing %s file", recording_id, key)
            continue
        os.remove(path)

    if not queries.delete_recording_delete_video_recordings(recording_id):
        return JSONResponse(
            {"ok": False, "success": False, "message": "Không tìm thấy bản ghi."},
            status_code=404,
        )

    return {"ok": True, "success": True}
