"""Depreciation calculation service and Excel exporter for fixed assets (TSCĐ).

Focuses on CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH (company_id = 33).
Calculates monthly straight-line depreciation, cumulative depreciation,
and remaining value, following the standard accounting template (MISA / Thông tư 133).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.services.database import db

DAI_LO_XANH_COMPANY_ID = 33


def get_depreciation_data(
    company_id: int = DAI_LO_XANH_COMPANY_ID,
    year: int = 2026,
    as_of_month: int | None = None,
    full_year: bool = False,
) -> dict[str, Any]:
    """Calculate depreciation schedule for all vehicles and fixed assets of the company."""
    if as_of_month is None:
        today = date.today()
        as_of_month = today.month if today.year == year else 12

    # Query company info
    company = db._execute_query(
        "SELECT * FROM public.invoice_companies WHERE id = %s",
        (company_id,), fetch=True, commit=False,
    ) or {}
    company_name = company.get("name") or "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH"

    # Query vehicles from plate_whitelist
    vehicles = db._execute_query("""
        SELECT id, plate_number, vehicle_type, owner_name, purchase_price,
               depreciation_start_date, depreciation_years, company_id, note
        FROM public.plate_whitelist
        WHERE company_id = %s
        ORDER BY id ASC
    """, (company_id,), fetchall=True, commit=False) or []

    # Also query enterprise_assets if any
    assets = db._execute_query("""
        SELECT id, asset_code, name, category, serial_number, acquired_date,
               purchase_cost, assigned_to, note
        FROM public.enterprise_assets
        WHERE company_id = %s AND purchase_cost > 0
        ORDER BY id ASC
    """, (company_id,), fetchall=True, commit=False) or []

    rows: list[dict[str, Any]] = []

    totals = {
        "original_price": 0,
        "calculation_value": 0,
        "usage_months_total": 0,
        "monthly_depreciation": 0,
        "prior_usage_months": 0,
        "prior_depreciation_value": 0,
        "monthly_kh": [0] * 12,
        "year_depreciation_total": 0,
        "total_usage_months": 0,
        "cumulative_depreciation": 0,
        "remaining_usage_months": 0,
        "remaining_value": 0,
    }

    # Process vehicles
    for v in vehicles:
        plate = str(v.get("plate_number") or "").strip()
        orig_price = int(v.get("purchase_price") or 0)
        dur_years = int(v.get("depreciation_years") or 5)
        total_months = dur_years * 12 if dur_years > 0 else 60

        start_d = v.get("depreciation_start_date")
        if isinstance(start_d, str):
            try:
                start_d = date.fromisoformat(start_d[:10])
            except Exception:
                start_d = date(year, 1, 1)
        elif not isinstance(start_d, date):
            start_d = date(year, 1, 1)

        monthly_kh = round(orig_price / total_months) if total_months > 0 else 0

        # Prior usage before selected year
        if start_d.year < year:
            prior_months = (year - start_d.year) * 12 - (start_d.month - 1)
            prior_months = max(0, min(total_months, prior_months))
            prior_val = min(orig_price, prior_months * monthly_kh)
        else:
            prior_months = 0
            prior_val = 0

        # Months in selected year
        m_kh = [0] * 12
        for m in range(1, 13):
            m_first = date(year, m, 1)
            # Active if started on or before this month's end
            m_active = (start_d <= date(year, m, 28))
            if not full_year and m > as_of_month:
                m_active = False

            if m_active:
                already_used = prior_months + sum(1 for x in m_kh if x > 0)
                if already_used < total_months:
                    m_kh[m - 1] = monthly_kh

        total_yr = sum(m_kh)
        active_in_yr = sum(1 for x in m_kh if x > 0)
        used_months = prior_months + active_in_yr
        accum_kh = min(orig_price, prior_val + total_yr)
        rem_months = max(0, total_months - used_months)
        rem_val = max(0, orig_price - accum_kh)

        row_item = {
            "asset_code": plate,
            "asset_name": plate,
            "asset_type": "Phương tiện vận tải, truyền dẫn",
            "department": "BAN GIÁM ĐỐC",
            "increase_date": start_d,
            "depreciation_start_date": start_d,
            "original_price": orig_price,
            "calculation_value": orig_price,
            "usage_months_total": total_months,
            "monthly_depreciation": monthly_kh,
            "prior_usage_months": prior_months,
            "prior_depreciation_value": prior_val,
            "monthly_kh": m_kh,
            "year_depreciation_total": total_yr,
            "total_usage_months": used_months,
            "cumulative_depreciation": accum_kh,
            "remaining_usage_months": rem_months,
            "remaining_value": rem_val,
            "account_cost": "21113",
            "account_depreciation": "2141",
            "account_expense": "6422",
            "expense_item": "",
            "source": "vehicle",
            "vehicle_id": v.get("id"),
        }
        rows.append(row_item)

        totals["original_price"] += orig_price
        totals["calculation_value"] += orig_price
        totals["usage_months_total"] += total_months
        totals["monthly_depreciation"] += monthly_kh
        totals["prior_usage_months"] += prior_months
        totals["prior_depreciation_value"] += prior_val
        for idx in range(12):
            totals["monthly_kh"][idx] += m_kh[idx]
        totals["year_depreciation_total"] += total_yr
        totals["total_usage_months"] += used_months
        totals["cumulative_depreciation"] += accum_kh
        totals["remaining_usage_months"] += rem_months
        totals["remaining_value"] += rem_val

    # Process additional enterprise_assets if any
    for a in assets:
        code = str(a.get("asset_code") or f"TS-{a['id']}").strip()
        name = str(a.get("name") or "").strip()
        orig_price = int(a.get("purchase_cost") or 0)
        total_months = 60  # Default 5 years
        start_d = a.get("acquired_date") or date(year, 1, 1)
        if isinstance(start_d, str):
            try:
                start_d = date.fromisoformat(start_d[:10])
            except Exception:
                start_d = date(year, 1, 1)

        monthly_kh = round(orig_price / total_months) if total_months > 0 else 0
        prior_months = 0
        if start_d.year < year:
            prior_months = min(total_months, (year - start_d.year) * 12 - (start_d.month - 1))
        prior_val = min(orig_price, prior_months * monthly_kh)

        m_kh = [0] * 12
        for m in range(1, 13):
            if start_d <= date(year, m, 28) and (full_year or m <= as_of_month):
                if prior_months + sum(1 for x in m_kh if x > 0) < total_months:
                    m_kh[m - 1] = monthly_kh

        total_yr = sum(m_kh)
        used_months = prior_months + sum(1 for x in m_kh if x > 0)
        accum_kh = min(orig_price, prior_val + total_yr)
        rem_months = max(0, total_months - used_months)
        rem_val = max(0, orig_price - accum_kh)

        row_item = {
            "asset_code": code,
            "asset_name": name,
            "asset_type": "Thiết bị, dụng cụ quản lý" if a.get("category") == "equipment" else "Phương tiện vận tải, truyền dẫn",
            "department": str(a.get("assigned_to") or "BAN GIÁM ĐỐC").strip(),
            "increase_date": start_d,
            "depreciation_start_date": start_d,
            "original_price": orig_price,
            "calculation_value": orig_price,
            "usage_months_total": total_months,
            "monthly_depreciation": monthly_kh,
            "prior_usage_months": prior_months,
            "prior_depreciation_value": prior_val,
            "monthly_kh": m_kh,
            "year_depreciation_total": total_yr,
            "total_usage_months": used_months,
            "cumulative_depreciation": accum_kh,
            "remaining_usage_months": rem_months,
            "remaining_value": rem_val,
            "account_cost": "21113",
            "account_depreciation": "2141",
            "account_expense": "6422",
            "expense_item": "",
            "source": "asset",
            "asset_id": a.get("id"),
        }
        rows.append(row_item)

        totals["original_price"] += orig_price
        totals["calculation_value"] += orig_price
        totals["usage_months_total"] += total_months
        totals["monthly_depreciation"] += monthly_kh
        totals["prior_usage_months"] += prior_months
        totals["prior_depreciation_value"] += prior_val
        for idx in range(12):
            totals["monthly_kh"][idx] += m_kh[idx]
        totals["year_depreciation_total"] += total_yr
        totals["total_usage_months"] += used_months
        totals["cumulative_depreciation"] += accum_kh
        totals["remaining_usage_months"] += rem_months
        totals["remaining_value"] += rem_val

    return {
        "company_id": company_id,
        "company_name": company_name,
        "company": company,
        "year": year,
        "as_of_month": as_of_month,
        "full_year": full_year,
        "rows": rows,
        "totals": totals,
        "row_count": len(rows),
    }


def export_depreciation_excel(
    company_id: int = DAI_LO_XANH_COMPANY_ID,
    year: int = 2026,
    as_of_month: int | None = None,
    full_year: bool = False,
) -> bytes:
    """Build depreciation schedule Excel workbook following the exact accounting template."""
    data = get_depreciation_data(company_id=company_id, year=year, as_of_month=as_of_month, full_year=full_year)
    rows = data["rows"]
    totals = data["totals"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Báo cáo"
    ws.views.sheetView[0].showGridLines = True

    # Column widths matching the standard template
    col_widths = {
        "A": 12.0, "B": 14.0, "C": 24.0, "D": 16.0, "E": 14.0, "F": 14.0,
        "G": 16.0, "H": 16.0, "I": 12.0, "J": 14.0, "K": 12.0, "L": 16.0,
        "M": 13.0, "N": 13.0, "O": 13.0, "P": 13.0, "Q": 13.0, "R": 13.0,
        "S": 13.0, "T": 13.0, "U": 13.0, "V": 13.0, "W": 13.0, "X": 13.0,
        "Y": 16.0, "Z": 12.0, "AA": 16.0, "AB": 12.0, "AC": 16.0,
        "AD": 12.0, "AE": 12.0, "AF": 12.0, "AG": 14.0,
    }
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    ws.row_dimensions[1].height = 24.0
    ws.row_dimensions[2].height = 18.0
    ws.row_dimensions[3].height = 28.0
    ws.row_dimensions[4].height = 20.0

    # Fonts & Styles
    font_title = Font(name="Times New Roman", size=14, bold=True, color="000000")
    font_sub = Font(name="Times New Roman", size=11, bold=True, color="000000")
    font_header = Font(name="Microsoft Sans Serif", size=8.5, bold=True, color="000000")
    font_data = Font(name="Microsoft Sans Serif", size=8.5, bold=False, color="000000")
    font_total = Font(name="Microsoft Sans Serif", size=8.5, bold=True, color="000000")

    fill_header = PatternFill(start_color="ADC7E7", end_color="ADC7E7", fill_type="solid")

    border_thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    border_header = Border(
        left=Side(style="thin", color="8FAADC"),
        right=Side(style="thin", color="8FAADC"),
        top=Side(style="thin", color="8FAADC"),
        bottom=Side(style="thin", color="8FAADC"),
    )
    border_total = Border(
        left=Side(style="thin", color="A0A0A0"),
        right=Side(style="thin", color="A0A0A0"),
        top=Side(style="thin", color="000000"),
        bottom=Side(style="double", color="000000"),
    )

    fmt_money = "#,##0_);[Red](#,##0)"
    fmt_date = "dd/MM/yyyy"

    # Row 1: Title
    ws.cell(1, 1, "BẢNG TÍNH KHẤU HAO TÀI SẢN CỐ ĐỊNH THEO NĂM")
    ws.cell(1, 1).font = font_title
    ws.cell(1, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("A1:AG1")

    # Row 2: Year
    mode_text = f"Năm: {year}" + (" (Đến tháng hiện tại)" if not full_year else " (Cả năm 12 tháng)")
    ws.cell(2, 1, mode_text)
    ws.cell(2, 1).font = font_sub
    ws.cell(2, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("A2:AG2")

    # Row 3 & 4: Headers
    headers_r3 = [
        ("Mã TSCĐ", 1, 1),
        ("Tên TSCĐ", 2, 2),
        ("Loại TSCĐ", 3, 3),
        ("Đơn vị sử dụng", 4, 4),
        ("Ngày ghi tăng", 5, 5),
        ("Ngày bắt đầu tính khấu hao", 6, 6),
        ("Nguyên giá TSCĐ", 7, 7),
        ("Giá trị tính KH", 8, 8),
        ("Thời gian sử dụng (tháng)", 9, 9),
        ("Giá trị khấu hao tháng", 10, 10),
        ("Thời gian đã sử dụng trước đó (tháng)", 11, 11),
        ("Giá trị đã khấu hao trước đó", 12, 12),
        ("Giá trị khấu hao theo tháng", 13, 25),  # Spans M3:Y3
        ("Tổng thời gian đã sử dụng (tháng)", 26, 26),
        ("Hao mòn lũy kế", 27, 27),
        ("Thời gian SD còn lại (tháng)", 28, 28),
        ("Giá trị còn lại", 29, 29),
        ("TK nguyên giá", 30, 30),
        ("TK khấu hao", 31, 31),
        ("TK chi phí", 32, 32),
        ("Khoản mục chi phí", 33, 33),
    ]

    for title, col_start, col_end in headers_r3:
        cell = ws.cell(3, col_start, title)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_header
        if col_start != col_end:
            for c in range(col_start + 1, col_end + 1):
                c_cell = ws.cell(3, c)
                c_cell.font = font_header
                c_cell.fill = fill_header
                c_cell.border = border_header
            ws.merge_cells(start_row=3, start_column=col_start, end_row=3, end_column=col_end)
        else:
            # Merge down to row 4
            d_cell = ws.cell(4, col_start)
            d_cell.font = font_header
            d_cell.fill = fill_header
            d_cell.border = border_header
            ws.merge_cells(start_row=3, start_column=col_start, end_row=4, end_column=col_end)

    # Sub-headers in Row 4 for M..Y
    sub_headers_m_y = [
        "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
        "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12", "Tổng cộng",
    ]
    for idx, sub_title in enumerate(sub_headers_m_y):
        c_idx = 13 + idx
        cell = ws.cell(4, c_idx, sub_title)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_header

    # Data rows
    row_idx = 5
    for r in rows:
        ws.row_dimensions[row_idx].height = 20.0
        start_d = r["increase_date"]
        d_val = datetime(start_d.year, start_d.month, start_d.day) if isinstance(start_d, date) else None

        row_data = [
            r["asset_code"],
            r["asset_name"],
            r["asset_type"],
            r["department"],
            d_val,
            d_val,
            r["original_price"],
            r["calculation_value"],
            r["usage_months_total"],
            r["monthly_depreciation"],
            r["prior_usage_months"],
            r["prior_depreciation_value"],
            *r["monthly_kh"],
            r["year_depreciation_total"],
            r["total_usage_months"],
            r["cumulative_depreciation"],
            r["remaining_usage_months"],
            r["remaining_value"],
            r["account_cost"],
            r["account_depreciation"],
            r["account_expense"],
            r["expense_item"] or None,
        ]

        for c_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row_idx, c_idx, val)
            cell.font = font_data
            cell.border = border_thin
            if c_idx in [1, 2, 3, 4, 30, 31, 32, 33]:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif c_idx in [5, 6]:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.number_format = fmt_date
            elif c_idx in [9, 11, 26, 28]:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "#,##0"
            else:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = fmt_money

        row_idx += 1

    # Total row
    ws.row_dimensions[row_idx].height = 22.0
    tot_row_data = [
        f"Số dòng = {len(rows)}", None, None, None, None, None,
        totals["original_price"],
        totals["calculation_value"],
        totals["usage_months_total"],
        totals["monthly_depreciation"],
        totals["prior_usage_months"],
        totals["prior_depreciation_value"],
        *totals["monthly_kh"],
        totals["year_depreciation_total"],
        totals["total_usage_months"],
        totals["cumulative_depreciation"],
        totals["remaining_usage_months"],
        totals["remaining_value"],
        None, None, None, None,
    ]

    for c_idx, val in enumerate(tot_row_data, 1):
        cell = ws.cell(row_idx, c_idx, val)
        cell.font = font_total
        cell.border = border_total
        if c_idx == 1:
            cell.alignment = Alignment(horizontal="left", vertical="center")
        elif c_idx in [5, 6]:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif c_idx in [9, 11, 26, 28]:
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = "#,##0"
        elif val is not None:
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = fmt_money

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
