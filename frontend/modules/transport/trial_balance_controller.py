"""Trial balance router for Dai Lo Xanh (Mau F01-DNN)."""

from __future__ import annotations

from datetime import date, datetime
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from frontend.deps import get_menu_context, templates
from frontend.modules.transport.cash_bank_service import get_company_info
from frontend.formatting.http_headers import content_disposition
from frontend.modules.transport.balance_sheet_service import (
    export_balance_sheet_excel,
    get_balance_sheet_data,
)
from frontend.modules.transport.financial_notes_service import (
    export_financial_notes_excel,
    get_financial_notes_data,
)
from frontend.modules.transport.income_statement_service import (
    export_income_statement_excel,
    get_income_statement_data,
)
from frontend.modules.transport.trial_balance_service import (
    build_closing_entries,
    export_account_ledger_excel,
    export_trial_balance_excel,
    get_account_ledger_data,
    get_trial_balance_data,
)
from frontend.modules.transport.date_utils import resolve_period_dates


logger = logging.getLogger(__name__)


def _resolve_period_dates(year, period, date_from, date_to):
    return resolve_period_dates(year, "all" if period is None else period, date_from, date_to, default="today")


async def trial_balance_page(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    mode: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    selected_year, active_period, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    tb_data = get_trial_balance_data(
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
        "closing": build_closing_entries(tb_data),
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "view_mode": mode,
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/trial_balance.html", {"request": request, **ctx})


async def export_trial_balance(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    mode: str,
    user: dict,
):
    selected_year, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    include_sub = (mode != "summary")
    excel_bytes = export_trial_balance_excel(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
        include_sub_accounts=include_sub,
    )

    comp_info = get_company_info()
    safe_name = "".join(c for c in (comp_info.get("name") or "company") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    sub_suffix = "_CHI_TIET" if include_sub else "_TONG_HOP"
    filename = f"F01_DNN_Bang_can_doi_tai_khoan_{safe_name}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}{sub_suffix}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def income_statement_page(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    selected_year, active_period, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    report = get_income_statement_data(selected_year, d_from, d_to)

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    prev_from = report.get("prev_from")
    prev_to = report.get("prev_to")
    prev_from_display = prev_from.strftime("%d/%m/%Y") if isinstance(prev_from, (date, datetime)) else ""
    prev_to_display = prev_to.strftime("%d/%m/%Y") if isinstance(prev_to, (date, datetime)) else ""

    ctx.update({
        "current_menu": "trial_balance",
        "transport_active": "income_statement",
        "report": report,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "prev_from_display": prev_from_display,
        "prev_to_display": prev_to_display,
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/income_statement.html", {"request": request, **ctx})


async def export_income_statement(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    selected_year, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    excel_bytes = export_income_statement_excel(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )

    comp_info = get_company_info()
    safe_name = "".join(c for c in (comp_info.get("name") or "company") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    filename = f"B02_DNN_Bao_cao_KQHDKD_{safe_name}_{d_from:%Y%m%d}_{d_to:%Y%m%d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def balance_sheet_page(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    selected_year, active_period, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    report = get_balance_sheet_data(selected_year, d_to)

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "trial_balance",
        "transport_active": "balance_sheet",
        "report": report,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/balance_sheet.html", {"request": request, **ctx})


async def export_balance_sheet(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    selected_year, _, _, d_to = _resolve_period_dates(year, period, date_from, date_to)
    excel_bytes = export_balance_sheet_excel(
        year=selected_year,
        date_to=d_to,
    )

    filename = f"Bao_cao_tinh_hinh_tai_chinh_B01b-DNN_{d_to:%Y-%m-%d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def financial_notes_page(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    ctx = get_menu_context(request, user)
    selected_year, active_period, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)

    report = get_financial_notes_data(selected_year, d_from, d_to)

    available_years = [selected_year, selected_year - 1, 2026, 2025, 2024]
    available_years = sorted(list(set(available_years)), reverse=True)

    ctx.update({
        "current_menu": "trial_balance",
        "transport_active": "financial_notes",
        "report": report,
        "selected_year": selected_year,
        "available_years": available_years,
        "active_period": active_period,
        "date_from": d_from.strftime("%Y-%m-%d"),
        "date_to": d_to.strftime("%Y-%m-%d"),
        "date_from_display": d_from.strftime("%d/%m/%Y"),
        "date_to_display": d_to.strftime("%d/%m/%Y"),
        "today": date.today(),
    })
    return templates.TemplateResponse("pages/transport/financial_notes.html", {"request": request, **ctx})


async def export_financial_notes(
    request: Request,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    selected_year, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    excel_bytes = export_financial_notes_excel(
        year=selected_year,
        date_from=d_from,
        date_to=d_to,
    )

    filename = f"Thuyet_minh_BCTC_B09-DNN_{d_to:%Y-%m-%d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def api_account_ledger(
    request: Request,
    code: str,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    _, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    ledger = get_account_ledger_data(
        code=code,
        date_from=d_from,
        date_to=d_to,
    )
    return JSONResponse({"ok": True, "ledger": ledger})


async def export_ledger(
    request: Request,
    code: str,
    year: int,
    period: str,
    date_from: str,
    date_to: str,
    user: dict,
):
    _, _, d_from, d_to = _resolve_period_dates(year, period, date_from, date_to)
    excel_bytes = export_account_ledger_excel(
        code=code,
        date_from=d_from,
        date_to=d_to,
    )
    filename = f"So_tong_hop_TK_{code}_{d_from.strftime('%Y%m%d')}_{d_to.strftime('%Y%m%d')}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Cache-Control": "no-store",
        },
    )


async def add_trial_balance_account(request: Request, user: dict):
    """Add a new custom account to F01-DNN trial balance."""
    from frontend.modules.transport.trial_balance_service import add_tb_custom_account
    form = await request.form()
    code = str(form.get("code") or "").strip()
    name = str(form.get("name") or "").strip()
    parent_code = str(form.get("parent_code") or "").strip()
    open_dr_raw = str(form.get("open_dr") or "0").replace(".", "").replace(",", "").strip()
    open_cr_raw = str(form.get("open_cr") or "0").replace(".", "").replace(",", "").strip()

    if not code or not name:
        return JSONResponse({"ok": False, "message": "Vui lòng nhập đầy đủ mã và tên tài khoản."}, status_code=400)

    try:
        open_dr = int(open_dr_raw or 0)
        open_cr = int(open_cr_raw or 0)
    except ValueError:
        return JSONResponse({"ok": False, "message": "Số dư đầu kỳ phải là số nguyên."}, status_code=400)

    add_tb_custom_account(code=code, name=name, parent_code=parent_code, open_dr=open_dr, open_cr=open_cr)
    return JSONResponse({"ok": True, "message": f"Đã thêm tài khoản {code} - {name} vào Bảng cân đối."})


async def edit_trial_balance_account(request: Request, code: str, user: dict):
    """Edit account name and opening balances."""
    from frontend.modules.transport.trial_balance_service import save_tb_override
    form = await request.form()
    name = str(form.get("name") or "").strip()
    open_dr_raw = form.get("open_dr")
    open_cr_raw = form.get("open_cr")

    open_dr = None
    open_cr = None
    if open_dr_raw is not None:
        try:
            open_dr = int(str(open_dr_raw).replace(".", "").replace(",", "").strip() or 0)
        except ValueError:
            pass
    if open_cr_raw is not None:
        try:
            open_cr = int(str(open_cr_raw).replace(".", "").replace(",", "").strip() or 0)
        except ValueError:
            pass

    save_tb_override(code=code, name=name if name else None, open_dr=open_dr, open_cr=open_cr)
    return JSONResponse({"ok": True, "message": f"Đã cập nhật thông tin tài khoản {code}."})


async def delete_trial_balance_account(request: Request, code: str, user: dict):
    """Delete a custom account or reset override."""
    from frontend.modules.transport.trial_balance_service import delete_tb_account
    delete_tb_account(code=code)
    return JSONResponse({"ok": True, "message": f"Đã xóa tài khoản {code}."})

