"""System menu for official Vietnamese administrative divisions."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.administrative_divisions import (
    COMMUNE_SOURCE_LABEL, SOURCE_DECISION_URL, SOURCE_LABEL, SOURCE_PAGE,
    list_communes, list_provinces,
    sync_from_official_source, sync_state,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["Administrative divisions"])


@router.get("/administrative_divisions", include_in_schema=False)
@router.get("/administrative-divisions")
async def administrative_divisions_page(
    request: Request,
    province: str = Query("", max_length=2),
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    synced: str = Query(""),
    user: dict = Depends(require_menu("administrative_divisions")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "administrative_divisions"
    provinces = list_provinces()
    if province and province not in {item["code"] for item in provinces}:
        province = ""
    communes, total = list_communes(province, q.strip(), page, limit=20)
    state = sync_state()
    ctx.update({
        "provinces": provinces,
        "communes": communes,
        "commune_total": total,
        "province_filter": province,
        "query": q.strip(),
        "page": page,
        "pages": max(1, (total + 19) // 20),
        "sync_state": state,
        "source_label": SOURCE_LABEL,
        "source_page": SOURCE_PAGE,
        "commune_source_label": COMMUNE_SOURCE_LABEL,
        "commune_source_url": state["source_url"].split(" | ")[-1] if state else SOURCE_PAGE,
        "decision_url": SOURCE_DECISION_URL,
        "synced": synced,
    })
    return templates.TemplateResponse("pages/administrative_divisions.html", {"request": request, **ctx})


@router.post("/administrative_divisions/sync", include_in_schema=False)
@router.post("/administrative-divisions/sync")
async def sync_administrative_divisions(
    user: dict = Depends(require_menu("administrative_divisions")),
):
    try:
        result = await run_in_threadpool(sync_from_official_source)
        message = "updated" if result["changed"] else "unchanged"
    except Exception:
        logger.exception("Administrative division sync failed")
        message = "failed"
    return RedirectResponse(
        "/admin/administrative-divisions?" + urlencode({"synced": message}), status_code=303,
    )
