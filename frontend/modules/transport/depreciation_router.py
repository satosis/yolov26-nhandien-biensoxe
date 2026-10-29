from __future__ import annotations

from datetime import date
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.transport.cash_bank_service import get_company_info
from frontend.modules.transport.depreciation_service import (
    export_depreciation_excel,
    get_depreciation_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport/depreciation")


@router.get("", include_in_schema=False)
@router.get("/")
async def depreciation_page(
    request: Request,
    year: int = Query(None),
    mode: str = Query("ytd"),
    period: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year

    p = (period or mode or "ytd").lower().strip()
    is_full_year = (p in ("full", "all"))

    if p in ("full", "all"):
        as_of_m = 12
        active_period = "full"
    elif p in ("today", "ytd"):
        as_of_m = today.month if today.year == selected_year else 12
        active_period = "ytd"
    elif p == "q1":
        as_of_m = 3
        active_period = "q1"
    elif p == "q2":
        as_of_m = 6
        active_period = "q2"
    elif p == "q3":
        as_of_m = 9
        active_period = "q3"
    elif p == "q4":
        as_of_m = 12
        active_period = "q4"
    elif p.startswith("m") and p[1:].isdigit():
        as_of_m = max(1, min(12, int(p[1:])))
        active_period = f"m{as_of_m}"
    else:
        as_of_m = today.month if today.year == selected_year else 12
        active_period = "ytd"

    depr_data = get_depreciation_data(
        year=selected_year,
        as_of_month=as_of_m,
        full_year=is_full_year,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "depreciation",
        "transport_active": "depreciation",
        "depr_data": depr_data,
        "selected_year": selected_year,
        "selected_mode": "full" if is_full_year else "ytd",
        "active_period": active_period,
        "is_full_year": is_full_year,
        "available_years": available_years,
        "today": today,
    })
    return templates.TemplateResponse("pages/transport/depreciation.html", {"request": request, **ctx})


@router.get("/export")
async def export_depreciation(
    request: Request,
    year: int = Query(None),
    mode: str = Query("ytd"),
    period: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year

    p = (period or mode or "ytd").lower().strip()
    is_full_year = (p in ("full", "all"))
    if p in ("full", "all"):
        as_of_m = 12
    elif p in ("today", "ytd"):
        as_of_m = today.month if today.year == selected_year else 12
    elif p == "q1":
        as_of_m = 3
    elif p == "q2":
        as_of_m = 6
    elif p == "q3":
        as_of_m = 9
    elif p == "q4":
        as_of_m = 12
    elif p.startswith("m") and p[1:].isdigit():
        as_of_m = max(1, min(12, int(p[1:])))
    else:
        as_of_m = today.month if today.year == selected_year else 12

    excel_bytes = export_depreciation_excel(
        year=selected_year,
        as_of_month=as_of_m,
        full_year=is_full_year,
    )

    comp_info = get_company_info()
    safe_name = "".join(c for c in (comp_info.get("name") or "CongTy") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    filename = f"BANG_TINH_KHAU_HAO_TAI_SAN_CO_DINH_NAM_{selected_year}_{safe_name}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
