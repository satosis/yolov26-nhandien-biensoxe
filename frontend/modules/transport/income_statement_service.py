"""Income Statement (Báo cáo kết quả hoạt động kinh doanh - Mẫu số B02-DNN) service for Dai Lo Xanh."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from frontend.modules.transport.trial_balance_service import (
    build_closing_entries,
    get_trial_balance_data,
)


B02_ROWS: list[tuple[str, str, bool]] = [
    ("01", "1. Doanh thu bán hàng và cung cấp dịch vụ", False),
    ("02", "2. Các khoản giảm trừ doanh thu", False),
    ("10", "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10 = 01 - 02)", True),
    ("11", "4. Giá vốn hàng bán", False),
    ("20", "5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ (20 = 10 - 11)", True),
    ("21", "6. Doanh thu hoạt động tài chính", False),
    ("22", "7. Chi phí tài chính", False),
    ("23", "   - Trong đó: Chi phí lãi vay", False),
    ("24", "8. Chi phí quản lý kinh doanh", False),
    ("30", "9. Lợi nhuận thuần từ hoạt động kinh doanh (30 = 20 + 21 - 22 - 24)", True),
    ("31", "10. Thu nhập khác", False),
    ("32", "11. Chi phí khác", False),
    ("40", "12. Lợi nhuận khác (40 = 31 - 32)", True),
    ("50", "13. Tổng lợi nhuận kế toán trước thuế (50 = 30 + 40)", True),
    ("51", "14. Chi phí thuế thu nhập doanh nghiệp", False),
    ("60", "15. Lợi nhuận sau thuế thu nhập doanh nghiệp (60 = 50 - 51)", True),
]


def build_income_statement(tb_data: dict) -> dict[str, int]:
    """Xây dựng báo cáo kết quả HĐKD theo mẫu B02-DNN từ dữ liệu bảng cân đối kế toán."""
    closing = build_closing_entries(tb_data)
    entries = closing.get("entries") or []

    rev_01 = 0
    rev_21 = 0
    rev_31 = 0
    exp_11 = 0
    exp_22 = 0
    exp_24 = 0
    exp_32 = 0
    exp_51 = 0

    for e in entries:
        amt = int(round(float(e.get("amount") or 0)))
        debit = str(e.get("debit") or "").strip()
        credit = str(e.get("credit") or "").strip()

        if credit == "911":
            if debit.startswith("511"):
                rev_01 += amt
            elif debit.startswith("515"):
                rev_21 += amt
            elif debit.startswith("711"):
                rev_31 += amt
        elif debit == "911":
            if credit.startswith("632"):
                exp_11 += amt
            elif credit.startswith("635"):
                exp_22 += amt
            elif credit.startswith("642"):
                exp_24 += amt
            elif credit.startswith("811"):
                exp_32 += amt
            elif credit.startswith("821"):
                exp_51 += amt

    row_02 = 0
    row_23 = 0
    row_10 = rev_01 - row_02
    row_20 = row_10 - exp_11
    row_30 = row_20 + rev_21 - exp_22 - exp_24
    row_40 = rev_31 - exp_32
    row_50 = row_30 + row_40
    row_60 = row_50 - exp_51

    return {
        "01": rev_01,
        "02": row_02,
        "10": row_10,
        "11": exp_11,
        "20": row_20,
        "21": rev_21,
        "22": exp_22,
        "23": row_23,
        "24": exp_24,
        "30": row_30,
        "31": rev_31,
        "32": exp_32,
        "40": row_40,
        "50": row_50,
        "51": exp_51,
        "60": row_60,
    }


def estimate_cit(profit_before_tax: int, revenue: int) -> dict | None:
    """Tạm tính thuế TNDN theo doanh thu."""
    if profit_before_tax <= 0:
        return None
    if revenue <= 3_000_000_000:
        rate = 15
    elif revenue <= 50_000_000_000:
        rate = 17
    else:
        rate = 20

    amount = int(round(profit_before_tax * rate / 100))
    after_tax = profit_before_tax - amount
    return {
        "rate": rate,
        "amount": amount,
        "after_tax": after_tax,
    }


def _shift_year_back(d: date) -> date:
    """Lùi 1 năm; 29/02 thành 28/02."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return date(d.year - 1, 2, 28)


def get_income_statement_data(year: int, date_from: date, date_to: date) -> dict:
    """Lấy dữ liệu Báo cáo kết quả hoạt động kinh doanh (B02-DNN)."""
    cur_tb = get_trial_balance_data(year=year, date_from=date_from, date_to=date_to)
    prev_from = _shift_year_back(date_from)
    prev_to = _shift_year_back(date_to)
    prev_tb = get_trial_balance_data(year=year - 1, date_from=prev_from, date_to=prev_to)

    cur = build_income_statement(cur_tb)
    prev = build_income_statement(prev_tb)

    is_full_year = (date_from == date(year, 1, 1) and date_to == date(year, 12, 31))

    rows = []
    for code, label, bold in B02_ROWS:
        rows.append({
            "code": code,
            "label": label,
            "bold": bold,
            "current": cur.get(code, 0),
            "previous": prev.get(code, 0),
        })

    cit_hint = estimate_cit(cur.get("50", 0), cur.get("01", 0)) if cur.get("51", 0) == 0 else None

    return {
        "company_name": cur_tb.get("company_name", ""),
        "address": cur_tb.get("address", ""),
        "tax_code": cur_tb.get("tax_code", ""),
        "director_name": cur_tb.get("director_name", ""),
        "year": year,
        "date_from": date_from,
        "date_to": date_to,
        "prev_from": prev_from,
        "prev_to": prev_to,
        "is_full_year": is_full_year,
        "rows": rows,
        "cit_hint": cit_hint,
    }


def export_income_statement_excel(year: int, date_from: date, date_to: date) -> bytes:
    """Xuất Báo cáo kết quả hoạt động kinh doanh (mẫu B02-DNN) ra Excel."""
    data = get_income_statement_data(year=year, date_from=date_from, date_to=date_to)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "B02-DNN"
    ws.views.sheetView[0].showGridLines = True

    col_widths = {
        "A": 62.0,
        "B": 10.0,
        "C": 12.0,
        "D": 20.0,
        "E": 20.0,
    }
    for col, width in col_widths.items():
        ws.column_dimensions[col].width = width

    font_company = Font(name="Times New Roman", size=11, bold=True)
    font_addr = Font(name="Times New Roman", size=10, italic=True)
    font_mau = Font(name="Times New Roman", size=11, bold=True)
    font_tt = Font(name="Times New Roman", size=9, italic=True)
    font_title = Font(name="Times New Roman", size=15, bold=True)
    font_period = Font(name="Times New Roman", size=11, bold=True)
    font_dvt = Font(name="Times New Roman", size=10, italic=True)
    font_tbl_hdr = Font(name="Times New Roman", size=10, bold=True)
    font_tbl_subhdr = Font(name="Times New Roman", size=10, italic=True)
    font_sign_title = Font(name="Times New Roman", size=11, bold=True)
    font_sign_sub = Font(name="Times New Roman", size=10, italic=True)

    border_thin = Border(
        left=Side(style="thin", color="A0A0A0"),
        right=Side(style="thin", color="A0A0A0"),
        top=Side(style="thin", color="A0A0A0"),
        bottom=Side(style="thin", color="A0A0A0"),
    )
    fill_hdr = PatternFill(start_color="F2F4F7", end_color="F2F4F7", fill_type="solid")

    ws["A1"] = data.get("company_name", "")
    ws["A1"].font = font_company
    ws["A2"] = data.get("address", "")
    ws["A2"].font = font_addr
    ws["A3"] = f"Mã số thuế: {data.get('tax_code', '')}"
    ws["A3"].font = font_addr

    ws.merge_cells("D1:E1")
    ws["D1"] = "Mẫu số: B02-DNN"
    ws["D1"].font = font_mau
    ws["D1"].alignment = Alignment(horizontal="right")

    ws.merge_cells("D2:E2")
    ws["D2"] = "(Ban hành theo Thông tư số 133/2016/TT-BTC"
    ws["D2"].font = font_tt
    ws["D2"].alignment = Alignment(horizontal="right")

    ws.merge_cells("D3:E3")
    ws["D3"] = "ngày 26/08/2016 của Bộ Tài chính)"
    ws["D3"].font = font_tt
    ws["D3"].alignment = Alignment(horizontal="right")

    ws.merge_cells("A5:E5")
    ws["A5"] = "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH"
    ws["A5"].font = font_title
    ws["A5"].alignment = Alignment(horizontal="center")

    d_from_str = date_from.strftime("%d/%m/%Y")
    d_to_str = date_to.strftime("%d/%m/%Y")
    period_text = f"Năm {year}" if data.get("is_full_year") else f"Từ ngày {d_from_str} đến ngày {d_to_str}"
    ws.merge_cells("A6:E6")
    ws["A6"] = period_text
    ws["A6"].font = font_period
    ws["A6"].alignment = Alignment(horizontal="center")

    ws["E7"] = "Đơn vị tính: VND"
    ws["E7"].font = font_dvt
    ws["E7"].alignment = Alignment(horizontal="right")

    headers = ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm nay", "Năm trước"]
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(9, col_idx, h)
        cell.font = font_tbl_hdr
        cell.fill = fill_hdr
        cell.border = border_thin
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    sub_headers = ["1", "2", "3", "4", "5"]
    for col_idx, sh in enumerate(sub_headers, 1):
        cell = ws.cell(10, col_idx, sh)
        cell.font = font_tbl_subhdr
        cell.fill = fill_hdr
        cell.border = border_thin
        cell.alignment = Alignment(horizontal="center", vertical="center")

    num_fmt = '#,##0;(#,##0);"-"'
    row_idx = 11
    for r in data.get("rows", []):
        is_bold = r.get("bold", False)
        f = Font(name="Times New Roman", size=10, bold=is_bold)

        cA = ws.cell(row_idx, 1, r["label"])
        cA.font = f
        cA.border = border_thin
        cA.alignment = Alignment(horizontal="left", vertical="center")

        cB = ws.cell(row_idx, 2, r["code"])
        cB.font = f
        cB.border = border_thin
        cB.alignment = Alignment(horizontal="center", vertical="center")

        cC = ws.cell(row_idx, 3, "")
        cC.font = f
        cC.border = border_thin
        cC.alignment = Alignment(horizontal="center", vertical="center")

        cD = ws.cell(row_idx, 4, r["current"])
        cD.font = f
        cD.border = border_thin
        cD.alignment = Alignment(horizontal="right", vertical="center")
        cD.number_format = num_fmt

        cE = ws.cell(row_idx, 5, r["previous"])
        cE.font = f
        cE.border = border_thin
        cE.alignment = Alignment(horizontal="right", vertical="center")
        cE.number_format = num_fmt

        row_idx += 1

    # Khối chữ ký cách bảng 2 hàng
    sign_date_row = row_idx + 2
    today_vn = date.today()

    ws.merge_cells(start_row=sign_date_row, start_column=4, end_row=sign_date_row, end_column=5)
    cell_date = ws.cell(sign_date_row, 4, f"Lập, ngày {today_vn.day} tháng {today_vn.month} năm {today_vn.year}")
    cell_date.font = font_sign_sub
    cell_date.alignment = Alignment(horizontal="center")

    title_row = sign_date_row + 1
    ws.cell(title_row, 1, "Người lập biểu").font = font_sign_title
    ws.cell(title_row, 1).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=title_row, start_column=2, end_row=title_row, end_column=3)
    ws.cell(title_row, 2, "Kế toán trưởng").font = font_sign_title
    ws.cell(title_row, 2).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=title_row, start_column=4, end_row=title_row, end_column=5)
    ws.cell(title_row, 4, "Người đại diện theo pháp luật").font = font_sign_title
    ws.cell(title_row, 4).alignment = Alignment(horizontal="center")

    sub_row = title_row + 1
    ws.cell(sub_row, 1, "(Ký, họ tên)").font = font_sign_sub
    ws.cell(sub_row, 1).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=sub_row, start_column=2, end_row=sub_row, end_column=3)
    ws.cell(sub_row, 2, "(Ký, họ tên)").font = font_sign_sub
    ws.cell(sub_row, 2).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=sub_row, start_column=4, end_row=sub_row, end_column=5)
    ws.cell(sub_row, 4, "(Ký, họ tên, đóng dấu)").font = font_sign_sub
    ws.cell(sub_row, 4).alignment = Alignment(horizontal="center")

    dir_row = sub_row + 4
    ws.merge_cells(start_row=dir_row, start_column=4, end_row=dir_row, end_column=5)
    ws.cell(dir_row, 4, data.get("director_name", "")).font = font_company
    ws.cell(dir_row, 4).alignment = Alignment(horizontal="center")

    out = BytesIO()
    wb.save(out)
    return out.getvalue()
