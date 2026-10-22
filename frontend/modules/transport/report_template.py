"""Put live transport data into the original styled monthly workbook."""

from __future__ import annotations

from copy import copy
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import warnings

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.comments import Comment
from openpyxl.styles import Alignment


TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "transport_report_template.xlsx"


def _clear(sheet, first_row: int, last_row: int, last_col: int) -> None:
    for row in sheet.iter_rows(min_row=first_row, max_row=last_row,
                               min_col=1, max_col=last_col):
        for cell in row:
            if not isinstance(cell, MergedCell):
                cell.value = None


def _style_new_row(sheet, row: int, sample_row: int, last_col: int) -> None:
    if row <= sheet.max_row:
        return
    for col in range(1, last_col + 1):
        source = sheet.cell(sample_row, col)
        target = sheet.cell(row, col)
        if source.has_style:
            target._style = copy(source._style)
        if source.alignment:
            target.alignment = copy(source.alignment)
    sheet.row_dimensions[row].height = sheet.row_dimensions[sample_row].height


def _restyle_row(sheet, row: int, sample_row: int, last_col: int) -> None:
    for col in range(1, last_col + 1):
        sheet.cell(row, col)._style = copy(sheet.cell(sample_row, col)._style)
    sheet.row_dimensions[row].height = sheet.row_dimensions[sample_row].height


def _copy_rows(source, target, first_row: int, *, last_col: int,
               target_row: int | None = None, skip_last: bool = False) -> int:
    target_row = target_row or first_row
    stop = source.max_row - (1 if skip_last else 0)
    written = 0
    for row in range(first_row, stop + 1):
        values = [source.cell(row, col).value for col in range(1, last_col + 1)]
        if all(value is None for value in values):
            continue
        dest_row = target_row + written
        _style_new_row(target, dest_row, first_row, last_col)
        for col, value in enumerate(values, 1):
            target.cell(dest_row, col).value = value
        written += 1
    return written


def _total_row(sheet, values, row: int, style_row: int,
               saved_style=None) -> None:
    if row != style_row:
        for col in range(1, len(values) + 1):
            sheet.cell(row, col)._style = copy(
                saved_style[col - 1] if saved_style else sheet.cell(style_row, col)._style
            )
        sheet.row_dimensions[row].height = sheet.row_dimensions[style_row].height
    for col, value in enumerate(values, 1):
        cell = sheet.cell(row, col)
        if not isinstance(cell, MergedCell):
            cell.value = value


def _value_rows(source, first_row: int, last_col: int):
    for row in range(first_row, source.max_row + 1):
        values = [source.cell(row, col).value for col in range(1, last_col + 1)]
        if any(value is not None for value in values):
            yield values


def preserve_transport_template(generated: bytes, start: date, end: date,
                                report_rows: list[dict]) -> bytes:
    """Keep the source workbook's visual layout while replacing September data."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Data Validation extension is not supported")
        book = load_workbook(TEMPLATE_PATH)
    data = load_workbook(BytesIO(generated), data_only=False)
    month_label = f"{start.month:02d}/{start.year}"

    summary = book["BÁO CÁO DT_CP_LN"]
    summary_total_style = [copy(summary.cell(12, c)._style) for c in range(1, 13)]
    _clear(summary, 5, max(summary.max_row, 12), 12)
    if len(report_rows) > 7:
        summary.unmerge_cells("A12:C12")
        _restyle_row(summary, 12, 5, 12)
    summary["A2"] = (f"Tháng {month_label}: doanh thu trước VAT; tổng chi phí gồm dầu, sửa chữa, "
                     "cầu đường, khấu hao, lương, tiền ăn và chi phí chuyến.")
    summary["E4"] = "Doanh thu trước VAT (đ)"
    n = _copy_rows(data[summary.title], summary, 5, last_col=12, skip_last=True)
    total = [data[summary.title].cell(data[summary.title].max_row, c).value
             for c in range(1, 13)] if n else ["TỔNG CỘNG", None, None] + [None] * 9
    summary_total_row = max(12, n + 5)
    if summary_total_row != 12:
        summary.merge_cells(start_row=summary_total_row, start_column=1,
                            end_row=summary_total_row, end_column=3)
    _total_row(summary, total, summary_total_row, 12, summary_total_style)

    # Keep the sample's A:L summary layout and show the extra cost components below it.
    detail_title_row = max(14, summary_total_row + 2)
    for col in range(1, 9):
        summary.cell(detail_title_row, col)._style = copy(summary.cell(1, 1)._style)
    summary.merge_cells(start_row=detail_title_row, start_column=1,
                        end_row=detail_title_row, end_column=8)
    summary.cell(detail_title_row, 1).value = "CÁC KHOẢN CHI PHÍ KHÁC ĐÃ TÍNH VÀO TỔNG CHI PHÍ"
    summary.row_dimensions[detail_title_row].height = summary.row_dimensions[1].height
    detail_header_row = detail_title_row + 1
    headers = ("Biển số xe", "Lương chính theo công", "Phụ cấp theo công", "Lương chuyến + thưởng",
               "Chi phí chuyến", "Xe thuê ngoài", "Chi phí khác", "Nguồn chi phí dầu")
    for col, label in enumerate(headers, 1):
        cell = summary.cell(detail_header_row, col)
        cell._style = copy(summary.cell(4, min(col, 12))._style)
        cell.value = label
    summary.row_dimensions[detail_header_row].height = summary.row_dimensions[4].height
    for index, vehicle in enumerate(report_rows, detail_header_row + 1):
        values = (vehicle["plate_number"], vehicle["base_salary"], vehicle["meal_allowance"],
                  (vehicle["trip_wage"] + vehicle["bonus_pay"]) or None,
                  vehicle["trip_other_cost"] or None,
                  vehicle["outsourced_freight"] or None, vehicle["other"] or None,
                  "Chứng từ đổ dầu" if vehicle["fuel_entries"] else
                  "Ước tính từ chuyến" if vehicle["estimated_fuel"] else "Chưa có dữ liệu")
        for col, value in enumerate(values, 1):
            cell = summary.cell(index, col)
            cell._style = copy(summary.cell(5, min(col, 12))._style)
            cell.value = value
            if 2 <= col <= 7:
                cell.number_format = summary["J5"].number_format
        summary.cell(index, 8).alignment = Alignment(wrap_text=True, vertical="center")
        summary.row_dimensions[index].height = 29
        if vehicle["fuel_is_estimate"] and vehicle["estimated_fuel"]:
            summary.cell(5 + index - detail_header_row - 1, 6).comment = Comment(
                "Chi phí dầu ước tính từ các chuyến hàng trong tháng; chưa có chứng từ đổ dầu.",
                "Hệ thống",
            )

    catalog = book["DANHMUC"]
    _clear(catalog, 5, 33, 36)
    _clear(catalog, 37, catalog.max_row, 4)
    source_catalog = data["DANHMUC"]
    for row in range(5, source_catalog.max_row + 1):
        if row > 33 and any(source_catalog.cell(row, col).value is not None for col in range(17, 23)):
            for col in range(17, 23):
                catalog.cell(row, col)._style = copy(catalog.cell(5, col)._style)
        for col in range(1, 29):
            value = source_catalog.cell(row, col).value
            if value is not None:
                catalog.cell(row, col).value = value
    for n, vehicle in enumerate(report_rows, 37):
        catalog.cell(n, 1).value = n - 36
        catalog.cell(n, 2).value = vehicle["plate_number"]
        catalog.cell(n, 3).value = vehicle.get("owner_name")

    starts = {
        "NHATTRINH": (4, 35), "VANTAI_THEOXE": (6, 19),
        "DODAU": (4, 9), "TAMUNG": (4, 8), "KHAUHAO": (4, 10),
        "VECAUDUONG": (4, 6), "SUACHUA": (4, 7),
        "CHI TIẾT CÔNG NỢ PHẢI THU": (5, 10),
        "CHI TIẾT CÔNG NỢ PHẢI TRẢ": (5, 10),
    }
    for name, (first_row, last_col) in starts.items():
        target = book[name]
        _clear(target, first_row, target.max_row, max(last_col, target.max_column))
        source_count = sum(1 for _ in _value_rows(data[name], first_row, last_col))
        total_style_row = {"DODAU": 8, "VECAUDUONG": 10}.get(name)
        saved_total_style = ([copy(target.cell(total_style_row, c)._style)
                              for c in range(1, last_col + 1)] if total_style_row else None)
        if name == "VANTAI_THEOXE" and source_count > 18:
            target.unmerge_cells("A24:C24")
            _restyle_row(target, 24, 6, last_col)
        if total_style_row and first_row + source_count > total_style_row:
            _restyle_row(target, total_style_row, first_row, last_col)
        count = _copy_rows(data[name], target, first_row, last_col=last_col)
        if name == "NHATTRINH":
            target.auto_filter.ref = f"A3:AG{max(4, first_row + count - 1)}"
        elif name == "VANTAI_THEOXE":
            target["B3"] = report_rows[0]["plate_number"] if len(report_rows) == 1 else "Tất cả xe"
            # S is the source workbook's matching-row helper, not a plate field.
            for row in range(first_row, first_row + count):
                target.cell(row, 19).value = None
        elif name in ("DODAU", "VECAUDUONG"):
            style_row = 8 if name == "DODAU" else 10
            amount_col = 6 if name == "DODAU" else 5
            values = [None] * last_col
            values[0] = "TỔNG CỘNG"
            values[amount_col - 1] = sum(
                (target.cell(row, amount_col).value or 0)
                for row in range(first_row, first_row + count)
            ) if count else None
            _total_row(target, values, max(style_row, first_row + count),
                       style_row, saved_total_style)
        elif name.startswith("CHI TIẾT CÔNG NỢ"):
            target["E2"] = start
            target["G2"] = end - timedelta(days=1)
            target["E3"] = None

    debt = book["CONGNO"]
    debt_total_style = [copy(debt.cell(12, col)._style) for col in range(1, 11)]
    _clear(debt, 4, debt.max_row, 28)
    debt_rows = list(_value_rows(data["CONGNO"], 4, 10))
    if len(debt_rows) > 8:
        _restyle_row(debt, 12, 4, 10)
    for index, values in enumerate(debt_rows, 4):
        _style_new_row(debt, index, 4, 10)
        for col, value in enumerate(values, 1):
            debt.cell(index, col).value = value
        debt.cell(index, 2).number_format = "dd/mm/yyyy"
    last_debt_row = max(11, len(debt_rows) + 3)
    for col in ("F", "G", "H", "I"):
        debt[f"{col}2"] = f"=SUM({col}4:{col}{last_debt_row})"
    if len(debt_rows) > 8:
        _total_row(debt, [None] * 10, len(debt_rows) + 4, 12, debt_total_style)

    cash = book["QUỸ"]
    _clear(cash, 6, cash.max_row, 12)
    running = 0
    for index, values in enumerate(_value_rows(data["QUỸ"], 6, 9), 7):
        _style_new_row(cash, index, 7, 12)
        day, tax_code, company, receiver, note, payment_method, thu, chi, ton = values
        thu_val = thu or 0
        chi_val = chi or 0
        running = ton if ton is not None else (running + thu_val - chi_val)
        for col, value in ((1, day), (2, tax_code), (3, company),
                           (4, receiver), (5, note),
                           (6, thu_val or None), (7, chi_val or None), (8, running or None)):
            cash.cell(index, col).value = value

    salary = book["LUONG"]
    salary_total_style = [copy(salary.cell(44, c)._style) for c in range(1, 18)]
    # The original sheet is a one-driver pay slip followed by a monthly pay table.
    _clear(salary, 9, 21, 9)
    _clear(salary, 40, salary.max_row, 17)
    _clear(salary, 24, 34, 2)
    for coord in ("B3", "E3", "G3", "I3", "B4", "E4", "G4", "H4", "B5", "E5", "G5"):
        salary[coord] = None
    salary["E3"] = start
    salary["G3"] = start
    salary["I3"] = end - timedelta(days=1)
    pays = list(_value_rows(data["LUONG"], 8, 14))
    if pays:
        (code, driver, plate, hire_date, base, days, allowances, trip_wage,
         trip_cost, gross, advance, deductions, net, bonus) = pays[0]
        for coord, value in (("B3", code), ("B4", driver), ("E4", plate),
                             ("G4", days), ("H4", allowances), ("B5", hire_date),
                             ("E5", base), ("G5", month_label), ("B24", base),
                             ("B26", trip_wage), ("B27", trip_cost), ("B28", allowances),
                             ("B31", gross), ("B34", net)):
            salary[coord] = value
        trips = list(_value_rows(data["NHATTRINH"], 4, 35))
        own = [t for t in trips if t[4] == driver and t[3] == plate]
        detail = own if len(own) <= 12 else own[:11] + [None]
        for n, trip in enumerate(detail, 9):
            if trip is None:
                overflow = own[11:]
                for col, value in ((1, n - 8), (4, f"Các chuyến còn lại ({len(overflow)})"),
                                   (6, sum((t[14] or 0) for t in overflow)),
                                   (7, sum((t[15] or 0) for t in overflow)),
                                   (8, sum((t[14] or 0) + (t[15] or 0) for t in overflow))):
                    salary.cell(n, col).value = value
                continue
            for col, value in ((1, n - 8), (2, trip[1]), (3, trip[3]),
                               (4, trip[5]), (5, trip[6]), (6, trip[14]),
                               (7, trip[15]), (8, (trip[14] or 0) + (trip[15] or 0))):
                salary.cell(n, col).value = value
        salary["B25"] = len(own)
        for col, value in ((6, trip_wage), (7, trip_cost),
                           (8, (trip_wage or 0) + (trip_cost or 0))):
            salary.cell(21, col).value = value
    trips_by_driver = {}
    for trip in _value_rows(data["NHATTRINH"], 4, 35):
        key = (trip[4], trip[3])
        count, revenue = trips_by_driver.get(key, (0, 0))
        trips_by_driver[key] = (count + 1, revenue + (trip[13] or 0))
    if len(pays) > 4:
        _restyle_row(salary, 44, 40, 17)
    for n, pay in enumerate(pays, 40):
        _style_new_row(salary, n, 40, 17)
        (code, driver, plate, hire_date, base, days, allowances, trip_wage,
         trip_cost, gross, advance, deductions, net, bonus) = pay
        trip_count, trip_revenue = trips_by_driver.get((driver, plate), (0, 0))
        for col, value in ((1, n - 39), (2, start), (3, code), (4, driver),
                           (5, plate), (7, base), (9, trip_count), (10, trip_revenue),
                           (11, allowances), (13, trip_wage + bonus),
                           (14, gross), (15, advance), (16, deductions), (17, net)):
            salary.cell(n, col).value = value
    total_row = max(44, 40 + len(pays))
    if total_row != 44:
        for col, style in enumerate(salary_total_style, 1):
            salary.cell(total_row, col)._style = copy(style)
        salary.row_dimensions[total_row].height = salary.row_dimensions[44].height
    salary.cell(total_row, 1).value = "TỔNG CỘNG"
    for col in (7, 10, 11, 13, 14, 15, 16, 17):
        salary.cell(total_row, col).value = sum(
            (salary.cell(row, col).value or 0) for row in range(40, 40 + len(pays))
        ) if pays else None

    book.calculation.fullCalcOnLoad = True
    output = BytesIO()
    book.save(output)
    return output.getvalue()
