"""Trial balance router for Dai Lo Xanh (Mau F01-DNN)."""

from __future__ import annotations

from datetime import date, datetime
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.transport.trial_balance_service import (
    DAI_LO_XANH_COMPANY_ID,
    SAMPLE_EXCEL_PATH,
    export_account_ledger_excel,
    export_trial_balance_excel,
    get_account_ledger_data,
    get_trial_balance_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transport/trial-balance")


def _resolve_period_dates(year: int | None, period: str | None, date_from_str: str | None, date_to_str: str | None) -> tuple[int, str, date, date]:
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year

    d_from: date | None = None
    d_to: date | None = None

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass

    p = (period or "").lower().strip()

    if p and p != "custom":
        if p == "today":
            d_from = date(selected_year, 1, 1)
            d_to = min(today, date(selected_year, 12, 31))
            return selected_year, "today", d_from, d_to
        elif p == "q1":
            d_from = date(selected_year, 1, 1)
            d_to = date(selected_year, 3, 31)
            return selected_year, "q1", d_from, d_to
        elif p == "q2":
            d_from = date(selected_year, 4, 1)
            d_to = date(selected_year, 6, 30)
            return selected_year, "q2", d_from, d_to
        elif p == "q3":
            d_from = date(selected_year, 7, 1)
            d_to = date(selected_year, 9, 30)
            return selected_year, "q3", d_from, d_to
        elif p == "q4":
            d_from = date(selected_year, 10, 1)
            d_to = date(selected_year, 12, 31)
            return selected_year, "q4", d_from, d_to
        elif p.startswith("m") and p[1:].isdigit():
            m = int(p[1:])
            if 1 <= m <= 12:
                last_day = 31 if m in (1, 3, 5, 7, 8, 10, 12) else (30 if m in (4, 6, 9, 11) else 28)
                d_from = date(selected_year, m, 1)
                d_to = date(selected_year, m, last_day)
                return selected_year, p, d_from, d_to
        elif p in ("all", "year"):
            d_from = date(selected_year, 1, 1)
            d_to = date(selected_year, 12, 31)
            return selected_year, "all", d_from, d_to

    if d_from and d_to:
        return selected_year, "custom", d_from, d_to

    # default
    d_from = date(selected_year, 1, 1)
    d_to = min(today, date(selected_year, 12, 31))
    return selected_year, "today", d_from, d_to


@router.get("", include_in_schema=False)
@router.get("/")
async def trial_balance_page(
    request: Request,
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    mode: str = Query("all"),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    selected_year, active_period, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    tb_data = get_trial_balance_data(
        company_id=DAI_LO_XANH_COMPANY_ID,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "trial_balance",
        "transport_active": "trial_balance",
        "tb_data": tb_data,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "view_mode": mode,
        "sample_excel_path": SAMPLE_EXCEL_PATH,
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/trial_balance.html", {"request": request, **ctx})


@router.get("/export")
async def export_trial_balance(
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    mode: str = Query("all"),
    user: dict = Depends(require_menu("vehicles")),
):
    selected_year, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    include_sub = (mode != "summary")
    excel_bytes = export_trial_balance_excel(
        company_id=DAI_LO_XANH_COMPANY_ID,
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
        include_sub_accounts=include_sub,
    )

    sub_suffix = "_CHI_TIET" if include_sub else "_TONG_HOP"
    filename = f"F01_DNN_Bang_can_doi_tai_khoan_DAI_LO_XANH_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}{sub_suffix}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/ledger")
async def api_account_ledger(
    code: str = Query(...),
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    _, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    ledger = get_account_ledger_data(
        code=code,
        company_id=DAI_LO_XANH_COMPANY_ID,
        date_from=d_from,
        date_to=d_to,
    )
    return JSONResponse({"ok": True, "ledger": ledger})


@router.get("/export-ledger")
async def export_ledger(
    code: str = Query(...),
    year: int = Query(None),
    period: str = Query("all"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    _, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    excel_bytes = export_account_ledger_excel(
        code=code,
        company_id=DAI_LO_XANH_COMPANY_ID,
        date_from=d_from,
        date_to=d_to,
    )
    filename = f"So_tong_hop_TK_{code}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
