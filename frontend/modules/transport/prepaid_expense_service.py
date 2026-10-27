"""Service for Prepaid Expenses & CCDC Allocation (TK 242) matching Phuong Nam Excel format."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import logging
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.services.database import db

logger = logging.getLogger(__name__)

DAI_LO_XANH_COMPANY_ID = 33


def get_prepaid_expenses_data(
    company_id: int = DAI_LO_XANH_COMPANY_ID,
    year: int | None = None,
    as_of_month: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Retrieve full allocation schedule for TK 242 across 12 months of the year."""
    today = date.today()
    selected_year = int(year) if year and str(year).isdigit() and 2000 <= int(year) <= 2100 else today.year
    active_month = as_of_month if as_of_month and 1 <= as_of_month <= 12 else (today.month if today.year == selected_year else 12)

    rows_db = db._execute_query('''
        SELECT id, item_code, name, unit, quantity, start_date, total_months,
               original_cost, accumulated_prev, monthly_allocation, offset_account, category, note
        FROM public.transport_prepaid_expenses
        WHERE company_id = %s
        ORDER BY start_date ASC, id ASC
    ''', (company_id,), fetchall=True, commit=False) or []

    items = []
    tot_original = Decimal(0)
    tot_acc_start = Decimal(0)
    tot_rem_start = Decimal(0)
    tot_alloc_year = Decimal(0)
    tot_acc_end = Decimal(0)
    tot_rem_end = Decimal(0)
    tot_month = [Decimal(0) for _ in range(12)]

    year_start_date = date(selected_year, 1, 1)

    for idx, r in enumerate(rows_db, 1):
        orig = Decimal(str(r["original_cost"] or 0))
        tot_m = int(r["total_months"] or 36)
        s_date: date = r["start_date"]
        unit = r["unit"] or "Chiếc"
        qty = r["quantity"] or 1
        off_acc = r["offset_account"] or "642"

        # monthly alloc
        if r["monthly_allocation"] and float(r["monthly_allocation"]) > 0:
            m_alloc = Decimal(str(round(float(r["monthly_allocation"]), 2)))
        else:
            m_alloc = Decimal(str(round(float(orig) / max(tot_m, 1), 2)))

        # Hao mòn lũy kế tính đến trước 01/01/selected_year
        acc_start = Decimal(0)
        if s_date < year_start_date:
            # Số tháng đã trôi qua trước năm nay
            months_before = (selected_year - s_date.year) * 12 + (1 - s_date.month)
            if s_date.day > 15:
                months_before -= Decimal("0.5")
            months_before = max(Decimal(0), min(Decimal(tot_m), Decimal(months_before)))
            
            # Nếu trong DB đã lưu accumulated_prev từ file cũ
            if r["accumulated_prev"] and float(r["accumulated_prev"]) > 0 and selected_year == 2026:
                acc_start = Decimal(str(r["accumulated_prev"]))
            else:
                acc_start = min(orig, months_before * m_alloc)

        rem_start = max(Decimal(0), orig - acc_start)

        # Tính toán phân bổ cho 12 tháng của selected_year
        m_vals = [Decimal(0) for _ in range(12)]
        cur_acc = acc_start

        for m in range(1, 13):
            m_date = date(selected_year, m, 1)
            # Nếu bắt đầu sử dụng sau tháng này hoặc đã phân bổ hết
            if s_date.year > selected_year or (s_date.year == selected_year and s_date.month > m):
                m_vals[m - 1] = Decimal(0)
            elif cur_acc >= orig:
                m_vals[m - 1] = Decimal(0)
            else:
                # Nếu đưa vào sử dụng trong tháng m
                if s_date.year == selected_year and s_date.month == m:
                    # Phân bổ theo số ngày còn lại trong tháng
                    days_in_month = 31 if m in (1, 3, 5, 7, 8, 10, 12) else (30 if m in (4, 6, 9, 11) else 28)
                    used_days = max(1, days_in_month - s_date.day + 1)
                    alloc_val = min(orig - cur_acc, Decimal(str(round(float(m_alloc) * used_days / days_in_month, 0))))
                else:
                    alloc_val = min(orig - cur_acc, m_alloc)

                m_vals[m - 1] = alloc_val
                cur_acc += alloc_val
                tot_month[m - 1] += alloc_val

        alloc_year = sum(m_vals)
        acc_end = min(orig, acc_start + alloc_year)
        rem_end = max(Decimal(0), orig - acc_end)

        tot_original += orig
        tot_acc_start += acc_start
        tot_rem_start += rem_start
        tot_alloc_year += alloc_year
        tot_acc_end += acc_end
        tot_rem_end += rem_end

        items.append({
            "id": r["id"],
            "stt": idx,
            "item_code": r["item_code"] or f"CCDC-{idx:02d}",
            "name": r["name"],
            "unit": unit,
            "quantity": qty,
            "start_date": s_date.strftime("%d/%m/%Y") if s_date else "",
            "total_months": tot_m,
            "total_years": round(tot_m / 12, 1),
            "original_cost": int(round(orig)),
            "acc_start": int(round(acc_start)),
            "rem_start": int(round(rem_start)),
            "monthly_alloc": int(round(m_alloc)),
            "months": [int(round(v)) for v in m_vals],
            "alloc_year": int(round(alloc_year)),
            "acc_end": int(round(acc_end)),
            "rem_end": int(round(rem_end)),
            "offset_account": off_acc,
            "category": r["category"] or "CCDC",
            "note": r["note"] or "",
        })

    # Tính toán kỳ phân bổ được lọc theo date_from -> date_to
    m_start = max(1, date_from.month) if (date_from and date_from.year == selected_year) else 1
    m_end = min(12, date_to.month) if (date_to and date_to.year == selected_year) else 12
    if m_start > m_end:
        m_start, m_end = 1, 12
    period_months = list(range(m_start, m_end + 1))

    for item in items:
        item["period_alloc"] = sum(item["months"][m - 1] for m in period_months)

    tot_period_alloc = sum(int(round(tot_month[m - 1])) for m in period_months)

    totals = {
        "original_cost": int(round(tot_original)),
        "acc_start": int(round(tot_acc_start)),
        "rem_start": int(round(tot_rem_start)),
        "months": [int(round(v)) for v in tot_month],
        "alloc_year": int(round(tot_alloc_year)),
        "period_alloc": tot_period_alloc,
        "period_months": period_months,
        "m_start": m_start,
        "m_end": m_end,
        "acc_end": int(round(tot_acc_end)),
        "rem_end": int(round(tot_rem_end)),
        "count": len(items),
    }

    d_from_eff = date_from or date(selected_year, m_start, 1)
    last_d = 31 if m_end in (1, 3, 5, 7, 8, 10, 12) else (30 if m_end in (4, 6, 9, 11) else 28)
    d_to_eff = date_to or date(selected_year, m_end, last_d)

    return {
        "title": f"BẢNG PHÂN BỔ CHI PHÍ TRẢ TRƯỚC DÀI HẠN (TK 242) NĂM {selected_year}",
        "company_name": "CÔNG TY TNHH TM VÀ DV PHƯƠNG NAM / ĐẠI LỘ XANH",
        "company_address": "Xóm Cây Da, Thôn Yên Lạc, Xã Đồng Văn, Huyện Yên Lạc, Tỉnh Vĩnh Phúc",
        "tax_code": "2500774911",
        "selected_year": selected_year,
        "active_month": active_month,
        "date_from": d_from_eff.strftime("%Y-%m-%d"),
        "date_to": d_to_eff.strftime("%Y-%m-%d"),
        "date_from_display": d_from_eff.strftime("%d/%m/%Y"),
        "date_to_display": d_to_eff.strftime("%d/%m/%Y"),
        "period_months": period_months,
        "items": items,
        "totals": totals,
    }


def add_prepaid_expense(
    company_id: int,
    item_code: str,
    name: str,
    unit: str,
    quantity: float,
    start_date: str | date,
    total_months: int,
    original_cost: float | Decimal,
    accumulated_prev: float | Decimal = 0,
    offset_account: str = "642",
    category: str = "CCDC",
    note: str = "",
) -> tuple[bool, str, int]:
    """Add a new prepaid expense/CCDC."""
    try:
        orig = Decimal(str(original_cost))
        acc = Decimal(str(accumulated_prev or 0))
    except Exception:
        return False, "Nguyên giá không hợp lệ.", 0

    if not name.strip():
        return False, "Tên công cụ dụng cụ không được để trống.", 0

    s_date = date.today()
    if isinstance(start_date, str) and start_date.strip():
        try:
            s_date = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            s_date = date.today()
    elif isinstance(start_date, date):
        s_date = start_date

    tot_m = max(1, int(total_months or 36))
    m_alloc = Decimal(str(round(float(orig) / tot_m, 2)))

    row = db._execute_query('''
        INSERT INTO public.transport_prepaid_expenses
        (company_id, item_code, name, unit, quantity, start_date, total_months,
         original_cost, accumulated_prev, monthly_allocation, offset_account, category, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (
        company_id, item_code.strip() or "CCDC-MỚI", name.strip(), unit.strip() or "Chiếc",
        quantity or 1, s_date, tot_m, orig, acc, m_alloc, offset_account.strip() or "642",
        category.strip() or "CCDC", note.strip()
    ), fetch=True, commit=True)

    ins_id = row["id"] if row else 0
    return True, "Đã thêm CCDC phân bổ 242 thành công.", ins_id


def delete_prepaid_expense(item_id: int, company_id: int = DAI_LO_XANH_COMPANY_ID) -> tuple[bool, str]:
    """Delete a prepaid expense item."""
    row = db._execute_query('''
        DELETE FROM public.transport_prepaid_expenses
        WHERE id = %s AND company_id = %s
        RETURNING id
    ''', (item_id, company_id), fetch=True, commit=True)
    if row:
        return True, "Đã xóa CCDC thành công."
    return False, "Không tìm thấy CCDC để xóa."


def export_prepaid_expenses_excel(
    company_id: int = DAI_LO_XANH_COMPANY_ID,
    year: int = 2026,
    date_from: date | None = None,
    date_to: date | None = None,
) -> bytes:
    """Export TK 242 allocation schedule to Excel matching Phuong Nam layout."""
    data = get_prepaid_expenses_data(company_id=company_id, year=year, date_from=date_from, date_to=date_to)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"242-{year}"
    ws.views.sheetView[0].showGridLines = True

    font_title = Font(name="Times New Roman", size=13, bold=True, color="1E3A8A")
    font_bold = Font(name="Times New Roman", size=9.5, bold=True)
    font_regular = Font(name="Times New Roman", size=9.5)
    font_italic = Font(name="Times New Roman", size=8.5, italic=True)

    border_thin = Side(border_style="thin", color="000000")
    border_double = Side(border_style="double", color="000000")
    box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    fill_header1 = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_total = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

    # Header info
    ws["A1"] = data["company_name"]
    ws["A1"].font = font_bold
    ws["A2"] = data["company_address"]
    ws["A2"].font = font_regular
    ws["A3"] = f"MST: {data['tax_code']}"
    ws["A3"].font = font_regular

    ws.merge_cells("A5:W5")
    ws["A5"] = data["title"]
    ws["A5"].font = font_title
    ws["A5"].alignment = Alignment(horizontal="center")

    # Table Header Rows 7-9
    # Cols:
    # 1: STT
    # 2: Tên CCDC / Thiết bị
    # 3: Đơn vị tính
    # 4: Ngày đưa vào SD
    # 5: Số lượng
    # 6: Thời gian PB (tháng)
    # 7: Nguyên giá
    # 8: Phân bổ lũy kế đầu năm
    # 9: Giá trị còn lại đầu năm
    # 10: Phân bổ 1 tháng
    # 11-22: Tháng 1 -> Tháng 12
    # 23: Lũy kế phân bổ năm nay
    # 24: Giá trị còn lại cuối năm
    # 25: TK Hạch toán

    headers = [
        "STT", "Tên CCDC / Thiết bị", "ĐVT", "Ngày đưa vào SD", "SL", "Thời gian PB (tháng)",
        "Nguyên giá", "Lũy kế đầu năm", "Còn lại đầu năm", "Phân bổ 1 tháng",
        "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
        "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12",
        "Tổng PB năm nay", "Còn lại cuối năm", "TK Nợ"
    ]

    for col_idx, h_text in enumerate(headers, 1):
        cell = ws.cell(row=7, column=col_idx, value=h_text)
        cell.font = font_bold
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.fill = fill_header1
        cell.border = box_border

    curr_r = 8
    for item in data["items"]:
        ws.cell(row=curr_r, column=1, value=item["stt"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=2, value=item["name"]).alignment = Alignment(horizontal="left")
        ws.cell(row=curr_r, column=3, value=item["unit"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=4, value=item["start_date"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=5, value=item["quantity"]).alignment = Alignment(horizontal="center")
        ws.cell(row=curr_r, column=6, value=item["total_months"]).alignment = Alignment(horizontal="center")

        ws.cell(row=curr_r, column=7, value=item["original_cost"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=8, value=item["acc_start"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=9, value=item["rem_start"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=10, value=item["monthly_alloc"]).number_format = "#,##0"

        for m_idx in range(12):
            val = item["months"][m_idx]
            c = ws.cell(row=curr_r, column=11 + m_idx, value=val if val > 0 else "")
            c.number_format = "#,##0"
            c.alignment = Alignment(horizontal="right")

        ws.cell(row=curr_r, column=23, value=item["alloc_year"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=24, value=item["rem_end"]).number_format = "#,##0"
        ws.cell(row=curr_r, column=25, value=item["offset_account"]).alignment = Alignment(horizontal="center")

        for c in range(1, 26):
            ws.cell(row=curr_r, column=c).border = box_border
            ws.cell(row=curr_r, column=c).font = font_regular

        curr_r += 1

    # Dòng Tổng cộng
    ws.merge_cells(f"A{curr_r}:F{curr_r}")
    ws[f"A{curr_r}"] = "Tổng cộng"
    ws[f"A{curr_r}"].font = font_bold
    ws[f"A{curr_r}"].alignment = Alignment(horizontal="center")

    t = data["totals"]
    ws.cell(row=curr_r, column=7, value=t["original_cost"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=8, value=t["acc_start"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=9, value=t["rem_start"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=10, value="")

    for m_idx in range(12):
        c = ws.cell(row=curr_r, column=11 + m_idx, value=t["months"][m_idx])
        c.number_format = "#,##0"
        c.font = font_bold
        c.alignment = Alignment(horizontal="right")

    ws.cell(row=curr_r, column=23, value=t["alloc_year"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=24, value=t["rem_end"]).number_format = "#,##0"
    ws.cell(row=curr_r, column=25, value="")

    for c in range(1, 26):
        ws.cell(row=curr_r, column=c).border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_double)
        ws.cell(row=curr_r, column=c).fill = fill_total
        ws.cell(row=curr_r, column=c).font = font_bold

    curr_r += 2
    # Dòng hạch toán kế toán Nợ 642 / Có 242 (Giống hệt dòng 19-20 trong Excel Phương Nam)
    ws[f"B{curr_r}"] = "Định khoản kế toán:"
    ws[f"B{curr_r}"].font = font_bold
    ws[f"C{curr_r}"] = "Nợ 642 (Chi phí QLDN)"
    ws[f"C{curr_r}"].font = font_bold
    for m_idx in range(12):
        c = ws.cell(row=curr_r, column=11 + m_idx, value=t["months"][m_idx])
        c.number_format = "#,##0"
        c.font = font_bold

    curr_r += 1
    ws[f"C{curr_r}"] = "Có 242 (Chi phí trả trước)"
    ws[f"C{curr_r}"].font = font_bold
    for m_idx in range(12):
        c = ws.cell(row=curr_r, column=11 + m_idx, value=t["months"][m_idx])
        c.number_format = "#,##0"
        c.font = font_bold

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 15
    ws.column_dimensions["E"].width = 8
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 17
    ws.column_dimensions["H"].width = 17
    ws.column_dimensions["I"].width = 17
    ws.column_dimensions["J"].width = 15
    for m in range(11, 23):
        ws.column_dimensions[get_column_letter(m)].width = 13
    ws.column_dimensions["W"].width = 18
    ws.column_dimensions["X"].width = 18
    ws.column_dimensions["Y"].width = 10

    output = BytesIO()
    wb.save(output)
    return output.getvalue()
