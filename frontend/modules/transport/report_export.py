"""Export the monthly transport report in the layout of the user's workbook.

All exported figures are read from the database.  The source workbook contains
broken cross-sheet references, so this export writes calculated values rather
than copying those formulas or any September 2026 source records.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.services.database import db
from frontend.modules.payroll.vehicle_payroll import monthly_payroll_rows
from frontend.modules.transport.settlements import monthly_trip_settlements
from frontend.modules.transport.report_template import preserve_transport_template


SHEET_NAMES = (
    "BÁO CÁO DT_CP_LN", "DANHMUC", "NHATTRINH", "VANTAI_THEOXE",
    "DODAU", "TAMUNG", "KHAUHAO", "VECAUDUONG", "SUACHUA", "CONGNO",
    "QUỸ", "CHI TIẾT CÔNG NỢ PHẢI THU", "CHI TIẾT CÔNG NỢ PHẢI TRẢ", "LUONG",
)
BLUE = "17365D"
PALE = "DDEBF7"
MONEY_FORMAT = '#,##0.00;[Red](#,##0.00);–'
DATE_FORMAT = "dd/mm/yyyy"


def _number(value):
    return float(value) if isinstance(value, Decimal) else value


def _day(value):
    return value.date() if isinstance(value, datetime) else value


def _invoice_gross(trip):
    if trip["freight_revenue"] is None:
        return None
    return (trip["freight_revenue"] * (1 + (trip["vat_rate"] or 0))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    )


def _title(sheet, value: str, last_col: int):
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    cell = sheet.cell(1, 1, value)
    cell.fill = PatternFill("solid", fgColor=BLUE)
    cell.font = Font(name="Arial", size=14, bold=True, color="FFFFFF")
    cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.sheet_view.showGridLines = False


def _headers(sheet, row: int, labels: list[str]):
    for col, label in enumerate(labels, 1):
        cell = sheet.cell(row, col, label)
        cell.fill = PatternFill("solid", fgColor=PALE)
        cell.font = Font(name="Arial", bold=True, color=BLUE)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(bottom=Side(style="thin", color="B7C9DC"))
        sheet.column_dimensions[get_column_letter(col)].width = min(45, max(14, len(label) + 4))
    sheet.row_dimensions[row].height = 34
    sheet.freeze_panes = f"A{row + 1}"
    sheet.auto_filter.ref = f"A{row}:{get_column_letter(len(labels))}{max(row + 1, sheet.max_row)}"


def _append(sheet, row: int, values, *, money=(), dates=(), percent=()):
    for col, value in enumerate(values, 1):
        cell = sheet.cell(row, col, _number(_day(value)))
        cell.font = Font(name="Arial", size=10)
        cell.alignment = Alignment(vertical="top", wrap_text=False)
        if col in money and value is not None:
            cell.number_format = MONEY_FORMAT
        elif col in dates and value is not None:
            cell.number_format = DATE_FORMAT
        elif col in percent and value is not None:
            cell.number_format = "0.0%"
    if row % 2 == 0:
        for cell in sheet[row][:len(values)]:
            cell.fill = PatternFill("solid", fgColor="F4F8FC")


def _sheet(book, name, title, headers, header_row=3):
    sheet = book[name]
    _title(sheet, title, len(headers))
    _headers(sheet, header_row, headers)
    return sheet


def _rows_for_month(start: date, end: date, visible_ids: set[int]):
    trips = db._execute_query(
        """SELECT t.*, c.tax_code, c.name AS company_name, v.fuel_consumption
             FROM public.cargo_trips t
             LEFT JOIN public.invoice_companies c ON c.id = t.company_id
             JOIN public.plate_whitelist v ON v.id = t.vehicle_id
            WHERE t.invoice_date >= %s AND t.invoice_date < %s
            ORDER BY t.invoice_date, t.id""",
        (start, end), fetchall=True, commit=False,
    ) or []
    invoice_lines = db._execute_query(
        """SELECT i.invoice_guid::text AS invoice_guid, l.line_number, i.invoice_date,
                  lpad(i.invoice_number::text, 8, '0') AS document_number,
                  l.vehicle_id, v.plate_number AS plate_number,
                  v.fuel_consumption,
                  i.buyer_company_id AS company_id, c.tax_code, c.name AS company_name,
                  l.origin, l.destination, l.cargo_description, l.cargo_weight_tons,
                  l.distance_km, l.amount AS freight_revenue,
                  COALESCE(l.tax_amount / NULLIF(l.amount, 0), i.vat_rate) AS vat_rate,
                  l.fuel_liters, l.fuel_price_per_liter, l.fuel_cost_total,
                  i.payment_status, i.debt_amount AS invoice_debt, i.gross AS invoice_gross,
                  l.tax_amount
             FROM public.bkav_invoice_lines l
             JOIN public.bkav_invoices i ON i.invoice_guid = l.invoice_guid
             JOIN public.plate_whitelist v ON v.id = l.vehicle_id
             LEFT JOIN public.invoice_companies c ON c.id = i.buyer_company_id
            WHERE i.invoice_date >= %s AND i.invoice_date < %s
            ORDER BY i.invoice_date, i.invoice_guid, l.line_number""",
        (start, end), fetchall=True, commit=False,
    ) or []
    by_invoice = {}
    for line in invoice_lines:
        by_invoice.setdefault(line["invoice_guid"], []).append(line)
    for guid, lines in by_invoice.items():
        invoice_debt = Decimal(str(lines[0]["invoice_debt"] or 0))
        invoice_gross = Decimal(str(lines[0]["invoice_gross"] or 0))
        allocated = Decimal(0)
        for index, line in enumerate(lines):
            gross = Decimal(str(line["freight_revenue"])) + (
                Decimal(str(line["tax_amount"])) if line["tax_amount"] is not None
                else Decimal(str(line["freight_revenue"])) * Decimal(str(line["vat_rate"] or 0)))
            debt = (invoice_debt - allocated if index == len(lines) - 1 else
                    (invoice_debt * gross / invoice_gross).quantize(Decimal("0.01"))
                    if invoice_gross else Decimal(0))
            allocated += debt
            trips.append({**line, "id": f"{guid}:{line['line_number']}",
                          "source_ref": f"bkav:{guid}", "debt_amount": debt,
                          "driver_name": None, "trip_quantity": None,
                          "trip_wage": None, "trip_other_cost": None,
                          "trip_note": None, "odometer_end": None,
                          "vehicle_owner_type": None, "outsourced_freight": None})
    expenses = db._execute_query(
        """SELECT e.*, v.plate_number FROM public.vehicle_expenses e
             JOIN public.plate_whitelist v ON v.id = e.vehicle_id
            WHERE e.expense_date >= %s AND e.expense_date < %s
            ORDER BY e.expense_date, e.id""",
        (start, end), fetchall=True, commit=False,
    ) or []
    return ([t for t in trips if t["vehicle_id"] in visible_ids],
            [e for e in expenses if e["vehicle_id"] in visible_ids])


def build_monthly_transport_workbook(start: date, end: date, report_rows: list[dict],
                                     *, include_global: bool = True,
                                     visible_vehicle_ids: set[int] | None = None) -> bytes:
    """Return an XLSX for one month, using the same visible vehicles as the page."""
    visible_ids = visible_vehicle_ids if visible_vehicle_ids is not None else {v["id"] for v in report_rows}
    trips, expenses = _rows_for_month(start, end, visible_ids)
    trip_fuel_vehicle_ids = {t["vehicle_id"] for t in trips if t["fuel_cost_total"] is not None}
    payroll = monthly_payroll_rows(start, end, visible_ids)
    drivers = payroll
    settlements = monthly_trip_settlements(start, end, visible_ids)
    cash = [row for row in settlements if row["paid_amount"]]
    company_ids = {trip["company_id"] for trip in trips if trip.get("company_id")}
    companies = db._execute_query(
        "SELECT id, name, tax_code, email FROM public.invoice_companies ORDER BY name, id",
        fetchall=True, commit=False,
    ) or []
    companies = [company for company in companies if company["id"] in company_ids]

    book = Workbook()
    book.active.title = SHEET_NAMES[0]
    for name in SHEET_NAMES[1:]:
        book.create_sheet(name)
    if start.year == end.year - 1 and start.month == 1 and end.month == 1:
        month_label = f"NĂM {start.year}"
        title_prefix = "BÁO CÁO TỔNG HỢP DOANH THU - CHI PHÍ - LỢI NHUẬN"
    else:
        month_label = f"{start.month:02d}/{start.year}"
        title_prefix = "BÁO CÁO TỔNG HỢP DOANH THU - CHI PHÍ - LỢI NHUẬN THÁNG"
    book.calculation.fullCalcOnLoad = True

    # The first sheet has the same twelve summary columns as the example.
    summary = _sheet(book, SHEET_NAMES[0], f"{title_prefix} {month_label}",
                     ["STT", "Biển số xe", "Loại xe", "Số chuyến", "Doanh thu (đ)",
                      "Chi phí dầu (đ)", "Chi phí sửa chữa (đ)", "Vé cầu đường (đ)",
                      "Khấu hao (đ)", "Tổng chi phí (đ)", "Lợi nhuận (đ)", "Tỷ suất LN"], 4)
    summary["A2"] = "Doanh thu trước VAT; tổng chi phí gồm dầu, sửa chữa, cầu đường, khấu hao, lương, tiền ăn và chi phí chuyến."
    for n, v in enumerate(report_rows, 1):
        fuel_amount = v["fuel_cost"] if v["fuel_entries"] or v["id"] in trip_fuel_vehicle_ids else None
        _append(summary, n + 4,
                [n, v["plate_number"], v["vehicle_type"], v["trip_count"],
                 v["revenue_net"] if v["trip_count"] else None,
                 fuel_amount, v["repair"] if v["repair_entries"] else None,
                 v["toll"] if v["toll_entries"] else None, v["depreciation"],
                 v["total_cost"], v["profit"], v["profit_margin"]],
                money=range(5, 12), percent=(12,))
    if report_rows:
        total_row = len(report_rows) + 5
        total_revenue = sum((v["revenue_net"] for v in report_rows), Decimal(0))
        total_profit = sum((v["profit"] for v in report_rows), Decimal(0))
        totals = ["TỔNG CỘNG", None, None, sum(v["trip_count"] for v in report_rows)]
        totals += [total_revenue if any(v["trip_count"] for v in report_rows) else None,
                   sum((v["fuel_cost"] for v in report_rows), Decimal(0))
                   if any(v["fuel_entries"] or v["id"] in trip_fuel_vehicle_ids for v in report_rows) else None]
        for key, entry_key in (("repair", "repair_entries"), ("toll", "toll_entries")):
            totals.append(sum((v[key] for v in report_rows), Decimal(0))
                          if any(v[entry_key] for v in report_rows) else None)
        totals += [sum((v["depreciation"] or 0 for v in report_rows), Decimal(0))
                   if any(v["depreciation"] is not None for v in report_rows) else None,
                   sum((v["total_cost"] for v in report_rows), Decimal(0)),
                   total_profit]
        totals.append(total_profit / total_revenue if total_revenue else None)
        _append(summary, total_row, totals, money=range(5, 12), percent=(12,))
        for cell in summary[total_row]:
            cell.font = Font(name="Arial", bold=True, color=BLUE)

    # Shared lists reproduce the source workbook's DANHMUC sections.
    categories = book["DANHMUC"]
    _title(categories, "DANH MỤC DỮ LIỆU DÙNG CHUNG", 28)
    for col, title in ((1, "A. DANH SÁCH XE"), (10, "B. DANH SÁCH TÀI XẾ"),
                       (17, "C. KHÁCH HÀNG/NCC"), (24, "D. NHÀ CUNG CẤP")):
        categories.cell(3, col, title)
    _headers(categories, 4, ["STT", "Biển số xe", "Loại xe", "Tải trọng (tấn)", "Ngày mua",
                            "Nguyên giá (đ)", "Số năm KH", "Ngày bắt đầu KH", "Định mức dầu (lít/100km)",
                            "STT", "Họ tên tài xế", "Biển số xe phụ trách", "Lương cơ bản (đ/tháng)",
                            "Ngày vào làm", "Tên LX", "Mã lái xe", "STT", "Tên khách hàng", "Nhà cung cấp",
                            "Khách hàng/NCC", "Mã", "Liên hệ", "", "STT", "Tên nhà cung cấp",
                            "Loại hình", "Liên hệ", ""])
    for n, v in enumerate(report_rows, 1):
        for col, value in ((1, n), (2, v["plate_number"]), (3, v["vehicle_type"]),
                           (6, v["purchase_price"]), (7, v["depreciation_years"]),
                           (8, v["depreciation_start_date"])):
            categories.cell(n + 4, col, _number(value))
        categories.cell(n + 4, 6).number_format = MONEY_FORMAT
        categories.cell(n + 4, 8).number_format = DATE_FORMAT
    for n, d in enumerate((d for d in drivers if d.get("vehicle_id") in visible_ids), 1):
        for col, value in ((10, n), (11, d["full_name"]), (12, d["plate_number"]),
                           (13, d["monthly_base_salary"]), (14, d["hire_date"]),
                           (15, d["full_name"]), (16, d["driver_code"])):
            categories.cell(n + 4, col, _number(value))
    for n, c in enumerate(companies, 1):
        categories.cell(n + 4, 17, n)
        categories.cell(n + 4, 18, c["name"])
        categories.cell(n + 4, 20, c["name"])
        categories.cell(n + 4, 21, c["tax_code"])
        categories.cell(n + 4, 22, c["email"] or None)

    itinerary = _sheet(book, "NHATTRINH", "THEO DÕI NHẬT TRÌNH VẬN TẢI HÀNG NGÀY",
                       ["STT", "Ngày", "Số HĐ", "Biển số xe", "Tài xế", "Nơi bốc", "Nơi trả",
                        "Loại hàng hóa", "Khách hàng", "Số Chuyến", "Khối lượng chuyến (m3)",
                        "Tổng KL", "Đơn giá cước phải thu", "Doanh thu chuyến (đ)", "Lương chuyến",
                        "Tiền chi phí", "Nội dung chi phí", "Ghi chú", "STT theo xe", "Khóa liên kết",
                        "Tháng", "Số Km", "Km cuối", "Định mức dầu (lít/100km)",
                        "Số lượng dầu (lít)", "Đơn giá dầu (đ/lít)", "Thành tiền dầu (đ)",
                        "Chủ xe", "Khối lượng", "Đơn giá tấn phải trả", "Cước trả xe ngoài",
                        "Mã nguồn", "Trạng thái thanh toán", "Thuế GTGT", "Cước vận chuyển"])
    per_vehicle_sequence = defaultdict(int)
    for n, t in enumerate(trips, 1):
        per_vehicle_sequence[t["plate_number"]] += 1
        seq = per_vehicle_sequence[t["plate_number"]]
        _append(itinerary, n + 3,
                [n, t["invoice_date"], t["document_number"], t["plate_number"], t["driver_name"],
                 t["origin"], t["destination"], t["cargo_description"], t["tax_code"],
                 t["trip_quantity"], t["cargo_weight_tons"], None, None, t["freight_revenue"],
                 t["trip_wage"], t["trip_other_cost"], None, t["trip_note"], seq,
                 f'{t["plate_number"]}|{seq}', month_label, t["distance_km"], t["odometer_end"],
                 t["fuel_consumption"], t["fuel_liters"], t["fuel_price_per_liter"],
                 t["fuel_cost_total"], {"company": "Xe công ty", "external": "Xe thuê ngoài"}.get(t["vehicle_owner_type"]),
                 t["cargo_weight_tons"], None, t["outsourced_freight"], t["source_ref"],
                 t["payment_status"], t["vat_rate"], t["freight_revenue"]],
                money=(14, 15, 16, 26, 27, 31, 35), dates=(2,), percent=(34,))

    by_vehicle = _sheet(book, "VANTAI_THEOXE", "VẬN TẢI CHI TIẾT THEO TỪNG XE",
                        ["STT chuyến", "Ngày", "Tài xế", "Điểm đi", "Điểm đến", "Loại hàng",
                         "Khối lượng (tấn)", "Số Km", "Đơn giá cước", "Doanh thu chuyến (đ)",
                         "Ghi chú", "Km cuối", "Định mức dầu (lít/100km)", "Số lượng dầu (lít)",
                         "Đơn giá dầu (đ/lít)", "Thành tiền dầu (đ)", "Chủ xe",
                         "Tiền cước thuê xe ngoài (đ)", "Biển số xe"], 5)
    by_vehicle["A3"] = "Tất cả xe trong báo cáo"
    for n, t in enumerate(sorted(trips, key=lambda t: (t["plate_number"], t["invoice_date"], str(t["id"]))), 1):
        _append(by_vehicle, n + 5,
                [n, t["invoice_date"], t["driver_name"], t["origin"], t["destination"],
                 t["cargo_description"], t["cargo_weight_tons"], t["distance_km"], None, t["freight_revenue"],
                 t["trip_note"], t["odometer_end"], t["fuel_consumption"], t["fuel_liters"],
                 t["fuel_price_per_liter"], t["fuel_cost_total"], t["vehicle_owner_type"],
                 t["outsourced_freight"], t["plate_number"]], money=(10, 15, 16, 18), dates=(2,))

    fuel = _sheet(book, "DODAU", "THEO DÕI ĐỔ DẦU (NHIÊN LIỆU)",
                  ["STT", "Ngày", "Biển số xe", "Số lít đổ", "Đơn giá (đ/lít)",
                   "Thành tiền (đ)", "Địa điểm/Cây xăng", "Số HĐ", "Ghi chú"])
    for n, e in enumerate((e for e in expenses if e["expense_type"] == "fuel"), 1):
        _append(fuel, n + 3, [n, e["expense_date"], e["plate_number"], e["quantity"],
                              e["unit_price"], e["amount"], e["vendor"], e["invoice_number"], e["note"]],
                money=(5, 6), dates=(2,))

    _sheet(book, "TAMUNG", "THEO DÕI TẠM ỨNG LÁI XE",
           ["STT", "Ngày", "Tài xế", "Số tiền tạm ứng (đ)", "Nội dung", "Đã hoàn ứng (đ)", "Còn lại (đ)", "Tháng"])

    depreciation = _sheet(book, "KHAUHAO", "TÍNH KHẤU HAO TÀI SẢN THEO XE",
                          ["STT", "Biển số xe", "Nguyên giá (đ)", "Ngày bắt đầu sử dụng", "Số năm KH",
                           "Khấu hao/năm (đ)", "Khấu hao/tháng (đ)", "Số tháng đã sử dụng",
                           "Khấu hao lũy kế (đ)", "Giá trị còn lại (đ)"])
    for n, v in enumerate(report_rows, 1):
        years = v["depreciation_years"]
        price = v["purchase_price"]
        started = v["depreciation_start_date"]
        months = max(0, min(years * 12, (start.year - started.year) * 12 + start.month - started.month + 1)) if started and years else None
        cumulative = price * Decimal(months) / Decimal(years * 12) if price is not None and months is not None and years else None
        _append(depreciation, n + 3,
                [n, v["plate_number"], price, started, years, price / years if price is not None and years else None,
                 v["depreciation"], months, cumulative, price - cumulative if cumulative is not None else None],
                money=(3, 6, 7, 9, 10), dates=(4,))

    toll = _sheet(book, "VECAUDUONG", "THEO DÕI VÉ CẦU ĐƯỜNG (BOT/PHÍ ĐƯỜNG BỘ)",
                  ["STT", "Ngày", "Biển số xe", "Tuyến đường/Trạm thu phí", "Số tiền (đ)", "Ghi chú"])
    for n, e in enumerate((e for e in expenses if e["expense_type"] == "toll"), 1):
        _append(toll, n + 3, [n, e["expense_date"], e["plate_number"], e["vendor"] or e["note"],
                              e["amount"], e["note"] if e["vendor"] else None],
                money=(5,), dates=(2,))
    repair = _sheet(book, "SUACHUA", "THEO DÕI SỬA CHỮA THEO XE",
                    ["STT", "Ngày", "Biển số xe", "Hạng mục sửa chữa", "Gara/Đơn vị sửa chữa", "Chi phí (đ)", "Ghi chú"])
    for n, e in enumerate((e for e in expenses if e["expense_type"] == "repair"), 1):
        _append(repair, n + 3, [n, e["expense_date"], e["plate_number"], e["note"],
                                e["vendor"], e["amount"], e["invoice_number"]], money=(6,), dates=(2,))

    debt = _sheet(book, "CONGNO", "CÔNG NỢ PHẢI THU (KHÁCH HÀNG)",
                  ["STT", "Ngày", "Khách hàng", "Số HĐ", "Nội dung/Chuyến hàng",
                   "Nợ cũ", "Phải thu (đ)", "Đã thu (đ)", "Còn phải thu (đ)", "Liên hệ"])
    for n, d in enumerate(settlements, 1):
        _append(debt, n + 3,
                [n, d["invoice_date"], d["company_name"], d["document_number"],
                 f'{d["service_name"]} · {d["plate_number"]}',
                 None, d["invoice_total"], d["paid_amount"],
                 d["outstanding"], d.get("company_email") or None],
                money=(6, 7, 8, 9), dates=(2,))

    cash_sheet = _sheet(book, "QUỸ", "SỔ QUỸ TIỀN MẶT — LỊCH SỬ TỔNG SỐ TIỀN ĐÃ TRẢ",
                        ["Ngày hóa đơn", "Mã số thuế", "Đơn vị", "Người nộp/nhận tiền",
                         "Diễn giải", "Hình thức", "Thu (đ)", "Chi (đ)", "Tồn (đ)"], 5)
    if not include_global:
        cash_sheet["A2"] = "Lịch sử số tiền đơn vị đã thanh toán cho xe đang xem."
    else:
        cash_sheet["A2"] = "Lịch sử tổng số tiền đã thanh toán; kỳ tính theo ngày hóa đơn."
    running = Decimal("0")
    for n, c in enumerate(cash, 1):
        running += c["paid_amount"]
        _append(cash_sheet, n + 5,
                [c["invoice_date"], c["tax_code"], c["company_name"],
                 c["company_name"],
                 f"{c['plate_number']} · {c['service_name']}",
                 c["payment_method"] or "",
                 c["paid_amount"], None, running],
                money=(7, 9), dates=(1,))
    # Tổng cộng row
    if cash:
        total_row = n + 6
        _append(cash_sheet, total_row,
                ["TỔNG CỘNG", None, None, None, None, None,
                 sum(c["paid_amount"] for c in cash), None, running],
                money=(7, 9))

    detail_headers = ["Ngày", "Biển số xe", "Nơi bốc", "Nơi trả", "Loại hàng hóa",
                      "Khách hàng", "Số chuyến", "Doanh thu chuyến (đ)", "Phát sinh", "Ghi chú"]
    income_detail = _sheet(book, "CHI TIẾT CÔNG NỢ PHẢI THU", "BẢNG KÊ CHI TIẾT CÔNG NỢ PHẢI THU",
                           detail_headers, 4)
    for n, t in enumerate(trips, 1):
        gross = _invoice_gross(t)
        _append(income_detail, n + 4,
                [t["invoice_date"], t["plate_number"], t["origin"], t["destination"],
                 t["cargo_description"], t["tax_code"], t["trip_quantity"], gross,
                 t["debt_amount"] if t["payment_status"] == "debt" else None, t["trip_note"]],
                money=(8, 9), dates=(1,))
    payable_detail = _sheet(book, "CHI TIẾT CÔNG NỢ PHẢI TRẢ", "BẢNG KÊ CHI TIẾT CÔNG NỢ PHẢI TRẢ",
                            detail_headers, 4)
    for n, t in enumerate((t for t in trips if t["outsourced_freight"] is not None), 1):
        _append(payable_detail, n + 4,
                [t["invoice_date"], t["plate_number"], t["origin"], t["destination"],
                 t["cargo_description"], t["tax_code"], t["trip_quantity"],
                 t["outsourced_freight"], None, t["trip_note"]], money=(8,), dates=(1,))

    salary = _sheet(book, "LUONG", f"PHIẾU LƯƠNG CHI TIẾT LÁI XE THÁNG {month_label}",
                    ["Mã lái xe", "Họ tên tài xế", "Biển số xe phụ trách", "Ngày vào làm",
                     "Lương chính theo công (đ)", "Ngày công", "Phụ cấp theo công (đ)",
                     "Lương chuyến (đ)", "Chi phí chuyến (đ)", "Tổng thu nhập (đ)",
                     "Tạm ứng (đ)", "Khấu trừ (đ)", "Thực lĩnh (đ)",
                     "Thưởng / tăng ca (đ)"], 7)
    salary["A3"] = "Tháng lương"
    salary["B3"] = month_label
    for n, p in enumerate((p for p in payroll if p.get("vehicle_id") in visible_ids), 1):
        _append(salary, n + 7,
                [p["driver_code"], p["full_name"], p["plate_number"], p["hire_date"],
                 p["base_salary"], p["work_days"], p["meal_allowance"], p["trip_wage"],
                 p["trip_other_cost"], p["total_salary"], p["advance_amount"],
                 p["salary_deductions"], p["net_salary"], p["bonus_pay"]],
                money=(5, 7, 8, 9, 10, 11, 12, 13, 14), dates=(4,))

    buffer = BytesIO()
    book.save(buffer)
    return preserve_transport_template(buffer.getvalue(), start, end, report_rows)
