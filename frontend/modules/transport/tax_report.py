"""Quarterly VAT and revenue declaration report for transport operations.

Calculates output VAT (bkav_invoices) and input VAT (transport_input_invoices)
per calendar quarter, structured for cross-checking against tax software (HTKK, MISA).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.services.database import db


def quarter_bounds(year: int, quarter: int | None = None) -> tuple[date, date, str]:
    """Return (start_date, end_date, label) for a given quarter (1-4) or full year (None or 0)."""
    if quarter == 1:
        return date(year, 1, 1), date(year, 3, 31), f"Quý 1/{year}"
    elif quarter == 2:
        return date(year, 4, 1), date(year, 6, 30), f"Quý 2/{year}"
    elif quarter == 3:
        return date(year, 7, 1), date(year, 9, 30), f"Quý 3/{year}"
    elif quarter == 4:
        return date(year, 10, 1), date(year, 12, 31), f"Quý 4/{year}"
    return date(year, 1, 1), date(year, 12, 31), f"Cả năm {year}"


def get_output_tax_invoices(start_date: date, end_date: date, seller_company_id: int | None = None) -> list[dict]:
    """Retrieve output (sales) VAT invoices within date range."""
    params: list[object] = [start_date, end_date]
    company_filter = ""
    if seller_company_id:
        company_filter = "AND inv.seller_company_id = %s"
        params.append(seller_company_id)

    query = f"""
        SELECT inv.invoice_guid, inv.invoice_number, inv.invoice_serial, inv.invoice_date,
               inv.buyer_tax_code, inv.service_name, inv.subtotal, inv.vat_rate,
               inv.gross, inv.invoice_code, inv.seller_company_id,
               COALESCE(buyer.name, inv.buyer_tax_code, 'Khách lẻ') AS buyer_name,
               COALESCE(seller.name, '') AS seller_name
        FROM public.bkav_invoices inv
        LEFT JOIN public.invoice_companies buyer ON buyer.id = inv.buyer_company_id
        LEFT JOIN public.invoice_companies seller ON seller.id = inv.seller_company_id
        WHERE inv.invoice_date >= %s AND inv.invoice_date <= %s
          {company_filter}
        ORDER BY inv.invoice_date ASC, inv.invoice_number ASC
    """
    rows = db._execute_query(query, tuple(params), fetchall=True, commit=False) or []
    results = []
    for r in rows:
        subtotal = Decimal(str(r.get("subtotal") or 0))
        gross = Decimal(str(r.get("gross") or 0))
        vat_rate = Decimal(str(r.get("vat_rate") or 0)) if r.get("vat_rate") is not None else None

        if gross and subtotal and gross >= subtotal:
            vat_amount = gross - subtotal
        elif subtotal and vat_rate is not None:
            vat_amount = (subtotal * vat_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        else:
            vat_amount = Decimal(0)

        rate_percent = int(round(vat_rate * 100)) if vat_rate is not None else None
        rate_label = f"{rate_percent}%" if rate_percent is not None else "KCT"

        results.append({
            "id": str(r["invoice_guid"]),
            "invoice_number": str(r.get("invoice_number") or "").zfill(8) if r.get("invoice_number") else "",
            "serial": str(r.get("invoice_serial") or "").strip(),
            "invoice_date": r["invoice_date"],
            "buyer_name": r["buyer_name"],
            "buyer_tax_code": str(r.get("buyer_tax_code") or "").strip(),
            "service_name": str(r.get("service_name") or "").strip(),
            "subtotal": subtotal,
            "vat_rate": vat_rate,
            "vat_rate_percent": rate_percent,
            "vat_rate_label": rate_label,
            "vat_amount": vat_amount,
            "gross": gross,
            "invoice_code": str(r.get("invoice_code") or "").strip(),
        })
    return results


def get_input_tax_invoices(start_date: date, end_date: date, buyer_company_id: int | None = None) -> list[dict]:
    """Retrieve input (purchases/expenses) VAT invoices within date range."""
    params: list[object] = [start_date, end_date]
    company_filter = ""
    if buyer_company_id:
        company_filter = "AND (buyer_company_id = %s OR buyer_company_id IS NULL)"
        params.append(buyer_company_id)

    query = f"""
        SELECT id, invoice_number, serial, invoice_date, seller_name, seller_tax_code,
               subtotal, vat_percent, vat_amount, total, note, items, source_filename,
               buyer_company_id
        FROM public.transport_input_invoices
        WHERE invoice_date >= %s AND invoice_date <= %s
          {company_filter}
        ORDER BY invoice_date ASC, id ASC
    """
    rows = db._execute_query(query, tuple(params), fetchall=True, commit=False) or []
    results = []
    for r in rows:
        subtotal = Decimal(str(r.get("subtotal") or 0))
        total = Decimal(str(r.get("total") or 0))
        vat_percent = Decimal(str(r.get("vat_percent") or 0)) if r.get("vat_percent") is not None else None
        vat_amount = Decimal(str(r.get("vat_amount") or 0)) if r.get("vat_amount") is not None else Decimal(0)

        if not vat_amount and total > subtotal:
            vat_amount = total - subtotal

        rate_int = int(round(vat_percent)) if vat_percent is not None else None

        # Phân loại KKKNT (Không kê khai nộp thuế GTGT) theo hóa đơn xăng dầu, dịch vụ không chịu thuế
        is_kkknt = (vat_amount == Decimal(0) and (rate_int == 0 or rate_int is None or total == subtotal))

        if is_kkknt:
            rate_percent_val = "kkknt"
            rate_label = ""  # Để trống theo quy định KKKNT & yêu cầu hiển thị
        elif rate_int is not None:
            rate_percent_val = str(rate_int)
            rate_label = f"{rate_int}%"
        else:
            rate_percent_val = "kct"
            rate_label = ""

        items = r.get("items") or []
        if isinstance(items, list) and items:
            desc = ", ".join(str(it.get("description") or "").strip() for it in items if str(it.get("description") or "").strip())
        else:
            desc = ""
        if not desc:
            desc = str(r.get("note") or "").strip() or "Hàng hóa, dịch vụ mua vào"

        results.append({
            "id": r["id"],
            "invoice_number": str(r.get("invoice_number") or "").strip(),
            "serial": str(r.get("serial") or "").strip(),
            "invoice_date": r["invoice_date"],
            "seller_name": str(r.get("seller_name") or "").strip(),
            "seller_tax_code": str(r.get("seller_tax_code") or "").strip(),
            "items_description": desc,
            "subtotal": subtotal,
            "vat_percent": vat_percent,
            "vat_rate_percent": rate_percent_val,
            "vat_rate_label": rate_label,
            "is_kkknt": is_kkknt,
            "vat_amount": vat_amount,
            "total": total,
            "note": str(r.get("note") or "").strip(),
            "has_file": bool(r.get("source_filename")),
        })
    return results


def get_previous_quarter_deductible(
    year: int,
    quarter: int | None,
    seller_company_id: int | None = None,
) -> Decimal:
    """Calculate [22] - deductible VAT carried forward from immediately preceding quarter."""
    if not quarter or quarter not in (1, 2, 3, 4):
        return Decimal(0)
    if quarter == 1:
        prev_year, prev_quarter = year - 1, 4
    else:
        prev_year, prev_quarter = year, quarter - 1

    start_date, end_date, _ = quarter_bounds(prev_year, prev_quarter)
    p_out = get_output_tax_invoices(start_date, end_date, seller_company_id=seller_company_id)
    p_in = get_input_tax_invoices(start_date, end_date, buyer_company_id=seller_company_id)
    if not p_out and not p_in:
        return Decimal(0)

    # Check if the previous quarter also had carry forward from earlier
    prev_prev_carry = Decimal(0)
    if prev_quarter > 1:
        pp_start, pp_end, _ = quarter_bounds(prev_year, prev_quarter - 1)
        pp_out = get_output_tax_invoices(pp_start, pp_end, seller_company_id=seller_company_id)
        pp_in = get_input_tax_invoices(pp_start, pp_end, buyer_company_id=seller_company_id)
        if pp_out or pp_in:
            pp_sum = compute_tax_summary(pp_out, pp_in, previous_deductible=Decimal(0))
            prev_prev_carry = pp_sum["vat_deductible"]

    p_sum = compute_tax_summary(p_out, p_in, previous_deductible=prev_prev_carry)
    return p_sum["vat_deductible"]


def compute_tax_summary(
    output_invoices: list[dict],
    input_invoices: list[dict],
    previous_deductible: Decimal | float | int = Decimal(0),
    adj_decrease: Decimal | float | int = Decimal(0),
    adj_increase: Decimal | float | int = Decimal(0),
    tax_received: Decimal | float | int = Decimal(0),
) -> dict:
    """Aggregate figures and map directly to Vietnamese HTKK VAT declaration metrics (Form 01/GTGT)."""
    total_out_subtotal = sum((inv["subtotal"] for inv in output_invoices), Decimal(0))
    total_out_vat = sum((inv["vat_amount"] for inv in output_invoices), Decimal(0))
    total_out_gross = sum((inv["gross"] for inv in output_invoices), Decimal(0))

    total_in_subtotal = sum((inv["subtotal"] for inv in input_invoices), Decimal(0))
    total_in_vat = sum((inv["vat_amount"] for inv in input_invoices), Decimal(0))
    total_in_total = sum((inv["total"] for inv in input_invoices), Decimal(0))

    # [36] Thuế GTGT phát sinh trong kỳ ([35] - [25])
    net_vat = total_out_vat - total_in_vat

    vat_carry_in = Decimal(str(previous_deductible or 0))
    adj_dec = Decimal(str(adj_decrease or 0))
    adj_inc = Decimal(str(adj_increase or 0))
    tax_rec = Decimal(str(tax_received or 0))

    # Công thức HTKK tờ khai 01/GTGT:
    # Cân đối thuế: [36] - [22] + [37] - [38] - [39a]
    tax_balance = net_vat - vat_carry_in + adj_dec - adj_inc - tax_rec

    if tax_balance >= Decimal(0):
        vat_payable = tax_balance       # [40a] & [40]: Thuế GTGT phải nộp trong kỳ
        vat_deductible = Decimal(0)     # [41] & [43]: Thuế còn được khấu trừ chuyển kỳ sau
    else:
        vat_payable = Decimal(0)
        vat_deductible = abs(tax_balance) # [41] & [43]

    out_by_rate: dict[str, dict[str, Decimal]] = {
        "kct": {"subtotal": Decimal(0), "vat": Decimal(0)},    # Không chịu thuế [26]
        "0": {"subtotal": Decimal(0), "vat": Decimal(0)},      # 0% [29]
        "5": {"subtotal": Decimal(0), "vat": Decimal(0)},      # 5% [30, 31]
        "8": {"subtotal": Decimal(0), "vat": Decimal(0)},      # 8% [32a, 33]
        "10": {"subtotal": Decimal(0), "vat": Decimal(0)},     # 10% [32, 33]
        "other": {"subtotal": Decimal(0), "vat": Decimal(0)},
    }
    for inv in output_invoices:
        pct = inv.get("vat_rate_percent")
        if pct is None:
            out_by_rate["kct"]["subtotal"] += inv["subtotal"]
        elif pct == 0:
            out_by_rate["0"]["subtotal"] += inv["subtotal"]
        elif pct == 5:
            out_by_rate["5"]["subtotal"] += inv["subtotal"]
            out_by_rate["5"]["vat"] += inv["vat_amount"]
        elif pct == 8:
            out_by_rate["8"]["subtotal"] += inv["subtotal"]
            out_by_rate["8"]["vat"] += inv["vat_amount"]
        elif pct == 10:
            out_by_rate["10"]["subtotal"] += inv["subtotal"]
            out_by_rate["10"]["vat"] += inv["vat_amount"]
        else:
            out_by_rate["other"]["subtotal"] += inv["subtotal"]
            out_by_rate["other"]["vat"] += inv["vat_amount"]

    in_by_rate: dict[str, dict[str, Decimal]] = {
        "kct": {"subtotal": Decimal(0), "vat": Decimal(0)},
        "kkknt": {"subtotal": Decimal(0), "vat": Decimal(0)},  # Không kê khai nộp thuế [23a]
        "0": {"subtotal": Decimal(0), "vat": Decimal(0)},
        "5": {"subtotal": Decimal(0), "vat": Decimal(0)},
        "8": {"subtotal": Decimal(0), "vat": Decimal(0)},
        "10": {"subtotal": Decimal(0), "vat": Decimal(0)},
        "other": {"subtotal": Decimal(0), "vat": Decimal(0)},
    }
    for inv in input_invoices:
        if inv.get("is_kkknt") or inv.get("vat_rate_percent") == "kkknt":
            in_by_rate["kkknt"]["subtotal"] += inv["subtotal"]
            continue
        pct = inv.get("vat_rate_percent")
        if pct is None or pct == "kct":
            in_by_rate["kct"]["subtotal"] += inv["subtotal"]
        elif pct in (0, "0"):
            in_by_rate["0"]["subtotal"] += inv["subtotal"]
        elif pct in (5, "5"):
            in_by_rate["5"]["subtotal"] += inv["subtotal"]
            in_by_rate["5"]["vat"] += inv["vat_amount"]
        elif pct in (8, "8"):
            in_by_rate["8"]["subtotal"] += inv["subtotal"]
            in_by_rate["8"]["vat"] += inv["vat_amount"]
        elif pct in (10, "10"):
            in_by_rate["10"]["subtotal"] += inv["subtotal"]
            in_by_rate["10"]["vat"] += inv["vat_amount"]
        else:
            in_by_rate["other"]["subtotal"] += inv["subtotal"]
            in_by_rate["other"]["vat"] += inv["vat_amount"]

    # Doanh số bán ra chịu thuế [27]
    taxable_out_subtotal = total_out_subtotal - out_by_rate["kct"]["subtotal"]

    return {
        "total_out_subtotal": total_out_subtotal,
        "taxable_out_subtotal": taxable_out_subtotal,
        "total_out_vat": total_out_vat,
        "total_out_gross": total_out_gross,
        "total_in_subtotal": total_in_subtotal,
        "total_in_vat": total_in_vat,
        "total_in_total": total_in_total,
        "vat_carry_in": vat_carry_in,                                    # [22] Khấu trừ kỳ trước chuyển sang
        "net_vat": net_vat,                                              # [36] Phát sinh trong kỳ
        "vat_payable": vat_payable,                                      # [40a] & [40] Thuế phải nộp
        "vat_deductible": vat_deductible,                                # [41] & [43] Khấu trừ chuyển kỳ sau
        "adj_decrease": adj_dec,                                         # [37]
        "adj_increase": adj_inc,                                         # [38]
        "tax_received": tax_rec,                                         # [39a]
        "out_by_rate": out_by_rate,
        "in_by_rate": in_by_rate,
        "out_count": len(output_invoices),
        "in_count": len(input_invoices),
    }


def export_quarterly_tax_excel(
    period_label: str,
    company: dict | None,
    output_invoices: list[dict],
    input_invoices: list[dict],
    summary: dict,
) -> bytes:
    """Generate professional Excel workbook with 3 sheets: Summary, Output, and Input."""
    wb = Workbook()
    thin = Side(border_style="thin", color="CCCCCC")
    double_bottom = Side(border_style="double", color="333333")
    border_all = Border(left=thin, right=thin, top=thin, bottom=thin)
    border_total = Border(left=thin, right=thin, top=thin, bottom=double_bottom)

    navy_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    dark_slate_fill = PatternFill(start_color="2C5282", end_color="2C5282", fill_type="solid")
    light_blue_fill = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
    soft_green_fill = PatternFill(start_color="DEF7EC", end_color="DEF7EC", fill_type="solid")
    soft_amber_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    total_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

    font_title = Font(name="Arial", size=15, bold=True, color="1E3A8A")
    font_subtitle = Font(name="Arial", size=11, bold=True, color="4B5563")
    font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    font_section = Font(name="Arial", size=11, bold=True, color="1E3A8A")
    font_bold = Font(name="Arial", size=10, bold=True, color="000000")
    font_regular = Font(name="Arial", size=10, color="1F2937")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    money_fmt = '#,##0;[Red](#,##0);0'
    date_fmt = "DD/MM/YYYY"

    company_name = (company.get("name") or "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH").upper() if company else "CÔNG TY DOANH NGHIỆP"
    tax_code = company.get("tax_code") or "" if company else ""
    address = company.get("address") or "" if company else ""

    # =========================================================================
    # SHEET 1: TỔNG HỢP CÂN ĐỐI THUẾ
    # =========================================================================
    ws1 = wb.active
    ws1.title = "CÂN ĐỐI THUẾ GTGT"
    ws1.views.sheetView[0].showGridLines = True

    ws1.cell(1, 1, company_name).font = font_subtitle
    ws1.cell(2, 1, f"MST: {tax_code}" if tax_code else "").font = font_regular
    ws1.cell(3, 1, f"Địa chỉ: {address}" if address else "").font = font_regular

    ws1.merge_cells("A5:F5")
    c = ws1.cell(5, 1, f"BẢNG KÊ CÂN ĐỐI THUẾ GTGT — {period_label.upper()}")
    c.font = font_title
    c.alignment = align_center

    ws1.merge_cells("A6:F6")
    c = ws1.cell(6, 1, "(Dùng để đối chiếu và kê khai Tờ khai thuế GTGT Mẫu 01/GTGT)")
    c.font = Font(name="Arial", size=10, italic=True, color="6B7280")
    c.alignment = align_center

    # 1.1 Khung tổng quan đối chiếu
    ws1.cell(8, 1, "I. TỔNG HỢP CHỈ TIÊU KÊ KHAI THUẾ GTGT (HTKK)").font = font_section

    headers1 = ["Chỉ tiêu", "Nội dung chỉ tiêu", "Doanh số / Tiền hàng", "Thuế GTGT", "Ghi chú đối chiếu", ""]
    for col_idx, h in enumerate(headers1[:5], 1):
        cell = ws1.cell(9, col_idx, h)
        cell.font = font_header
        cell.fill = navy_fill
        cell.alignment = align_header
        cell.border = border_all
    ws1.row_dimensions[9].height = 28

    htkk_rows = [
        ("[21]", "Không phát sinh hoạt động mua, bán trong kỳ", None, None, "Đánh dấu 'X' nếu không phát sinh"),
        ("[22]", "Thuế GTGT còn được khấu trừ kỳ trước chuyển sang", None, summary["vat_carry_in"], "Chuyển từ chỉ tiêu [43] của kỳ trước liền kề"),
        ("[23]", "Tổng giá trị HHDV mua vào trong kỳ", summary["total_in_subtotal"], None, "Tổng tiền hàng chưa thuế từ Hóa đơn đầu vào (gồm cả KKKNT)"),
        ("[23a]", "↳ Trong đó: HHDV mua vào không chịu thuế và KKKNT", summary["in_by_rate"]["kkknt"]["subtotal"], None, "Hàng hóa, xăng dầu, dịch vụ diện Không kê khai nộp thuế"),
        ("[24]", "Tổng thuế GTGT của HHDV mua vào", None, summary["total_in_vat"], "Tổng thuế GTGT từ Hóa đơn đầu vào"),
        ("[25]", "Tổng số thuế GTGT được khấu trừ kỳ này", None, summary["total_in_vat"], "Bằng chỉ tiêu [24] nếu đủ điều kiện khấu trừ"),
        ("[26]", "Hàng hóa, dịch vụ bán ra không chịu thuế GTGT", summary["out_by_rate"]["kct"]["subtotal"], None, "Doanh thu bán ra thuộc diện KCT"),
        ("[27]", "Hàng hóa, dịch vụ bán ra chịu thuế GTGT", summary["taxable_out_subtotal"], None, "[27] = [29] + [30] + [32] + [32a]"),
        ("[28]", "Thuế GTGT của HHDV bán ra", None, summary["total_out_vat"], "[28] = [31] + [33]"),
        ("[29]", "Hàng hóa, dịch vụ bán ra chịu thuế suất 0%", summary["out_by_rate"]["0"]["subtotal"], None, "Doanh thu bán ra thuế suất 0%"),
        ("[30]", "Hàng hóa, dịch vụ bán ra chịu thuế suất 5%", summary["out_by_rate"]["5"]["subtotal"], None, "Doanh thu bán ra thuế suất 5%"),
        ("[31]", "Thuế GTGT của HHDV bán ra chịu thuế suất 5%", None, summary["out_by_rate"]["5"]["vat"], "Thuế GTGT đầu ra 5%"),
        ("[32a]", "Hàng hóa, dịch vụ bán ra chịu thuế suất 8% (giảm thuế)", summary["out_by_rate"]["8"]["subtotal"], summary["out_by_rate"]["8"]["vat"], "Doanh thu & thuế suất 8% (chính sách giảm thuế)"),
        ("[32]", "Hàng hóa, dịch vụ bán ra chịu thuế suất 10%", summary["out_by_rate"]["10"]["subtotal"], summary["out_by_rate"]["10"]["vat"], "Doanh thu & thuế suất 10%"),
        ("[33]", "Thuế GTGT của HHDV bán ra chịu thuế 8% & 10%", None, summary["out_by_rate"]["8"]["vat"] + summary["out_by_rate"]["10"]["vat"], "Thuế GTGT đầu ra 8% và 10%"),
        ("[34]", "TỔNG DOANH THU BÁN RA CHƯA THUẾ", summary["total_out_subtotal"], None, "[26] + [27]"),
        ("[35]", "TỔNG THUẾ GTGT CỦA HHDV BÁN RA", None, summary["total_out_vat"], "[31] + [33]"),
        ("[36]", "Thuế GTGT phát sinh trong kỳ ([35] - [25])", None, summary["net_vat"], "Chênh lệch giữa thuế Đầu ra và Đầu vào"),
    ]

    r_idx = 10
    for code, desc, sub, vat, note in htkk_rows:
        ws1.cell(r_idx, 1, code).alignment = align_center
        ws1.cell(r_idx, 1).font = font_bold
        ws1.cell(r_idx, 2, desc).alignment = align_left
        ws1.cell(r_idx, 2).font = font_bold if code in ("[22]", "[27]", "[28]", "[34]", "[35]", "[36]") else font_regular

        cell_sub = ws1.cell(r_idx, 3, float(sub) if sub is not None else "")
        cell_sub.alignment = align_right
        cell_sub.number_format = money_fmt
        cell_sub.font = font_bold if code in ("[27]", "[34]", "[35]", "[36]") else font_regular

        cell_vat = ws1.cell(r_idx, 4, float(vat) if vat is not None else "")
        cell_vat.alignment = align_right
        cell_vat.number_format = money_fmt
        cell_vat.font = font_bold if code in ("[22]", "[28]", "[34]", "[35]", "[36]") else font_regular

        ws1.cell(r_idx, 5, note).alignment = align_left
        ws1.cell(r_idx, 5).font = Font(name="Arial", size=9, italic=True, color="6B7280")

        if code in ("[34]", "[35]"):
            for col in range(1, 6):
                ws1.cell(r_idx, col).fill = light_blue_fill
        elif code in ("[22]", "[36]"):
            for col in range(1, 6):
                ws1.cell(r_idx, col).fill = total_fill

        for col in range(1, 6):
            ws1.cell(r_idx, col).border = border_all
        r_idx += 1

    # Kết quả nghĩa vụ thuế
    r_idx += 1
    ws1.cell(r_idx, 1, "II. XÁC ĐỊNH NGHĨA VỤ THUẾ GTGT PHẢI NỘP TRONG KỲ").font = font_section
    r_idx += 1

    ws1.cell(r_idx, 1, "[40a]").alignment = align_center
    ws1.cell(r_idx, 1).font = font_bold
    ws1.cell(r_idx, 2, "Thuế GTGT phải nộp của hoạt động SXKD trong kỳ ({[40a]=([36]-[22]) >= 0})").font = font_bold
    cell = ws1.cell(r_idx, 4, float(summary["vat_payable"]))
    cell.alignment = align_right
    cell.font = Font(name="Arial", size=11, bold=True, color="B45309")
    cell.number_format = money_fmt
    ws1.cell(r_idx, 5, "Số tiền thuế GTGT doanh nghiệp cần nộp vào NSNN").font = font_regular
    for col in range(1, 6):
        ws1.cell(r_idx, col).border = border_all
        ws1.cell(r_idx, col).fill = soft_amber_fill if summary["vat_payable"] > 0 else PatternFill(fill_type=None)

    r_idx += 1
    ws1.cell(r_idx, 1, "[40]").alignment = align_center
    ws1.cell(r_idx, 1).font = font_bold
    ws1.cell(r_idx, 2, "Thuế GTGT còn phải nộp trong kỳ ([40] = [40a] - [40b])").font = font_bold
    cell = ws1.cell(r_idx, 4, float(summary["vat_payable"]))
    cell.alignment = align_right
    cell.font = Font(name="Arial", size=11, bold=True, color="B45309")
    cell.number_format = money_fmt
    ws1.cell(r_idx, 5, "Bằng [40a] nếu không có dự án đầu tư bù trừ").font = font_regular
    for col in range(1, 6):
        ws1.cell(r_idx, col).border = border_all
        ws1.cell(r_idx, col).fill = soft_amber_fill if summary["vat_payable"] > 0 else PatternFill(fill_type=None)

    r_idx += 1
    ws1.cell(r_idx, 1, "[41]").alignment = align_center
    ws1.cell(r_idx, 1).font = font_bold
    ws1.cell(r_idx, 2, "Thuế GTGT chưa khấu trừ hết kỳ này ({[41]=([36]-[22]) <= 0})").font = font_bold
    cell = ws1.cell(r_idx, 4, float(summary["vat_deductible"]))
    cell.alignment = align_right
    cell.font = Font(name="Arial", size=11, bold=True, color="047857")
    cell.number_format = money_fmt
    ws1.cell(r_idx, 5, "Số thuế GTGT đầu vào chưa khấu trừ hết").font = font_regular
    for col in range(1, 6):
        ws1.cell(r_idx, col).border = border_all
        ws1.cell(r_idx, col).fill = soft_green_fill if summary["vat_deductible"] > 0 else PatternFill(fill_type=None)

    r_idx += 1
    ws1.cell(r_idx, 1, "[43]").alignment = align_center
    ws1.cell(r_idx, 1).font = font_bold
    ws1.cell(r_idx, 2, "Thuế GTGT còn được khấu trừ chuyển kỳ sau ([43] = [41] - [42])").font = font_bold
    cell = ws1.cell(r_idx, 4, float(summary["vat_deductible"]))
    cell.alignment = align_right
    cell.font = Font(name="Arial", size=11, bold=True, color="047857")
    cell.number_format = money_fmt
    ws1.cell(r_idx, 5, "Số thuế GTGT kết chuyển sang chỉ tiêu [22] của quý tiếp theo").font = font_regular
    for col in range(1, 6):
        ws1.cell(r_idx, col).border = border_all
        ws1.cell(r_idx, col).fill = soft_green_fill if summary["vat_deductible"] > 0 else PatternFill(fill_type=None)

    ws1.column_dimensions["A"].width = 12
    ws1.column_dimensions["B"].width = 46
    ws1.column_dimensions["C"].width = 24
    ws1.column_dimensions["D"].width = 24
    ws1.column_dimensions["E"].width = 44

    # =========================================================================
    # SHEET 2: BẢNG KÊ ĐẦU RA (BÁN RA)
    # =========================================================================
    ws2 = wb.create_sheet(title="BẢNG KÊ ĐẦU RA")
    ws2.views.sheetView[0].showGridLines = True

    ws2.cell(1, 1, company_name).font = font_subtitle
    ws2.cell(2, 1, f"MST: {tax_code}" if tax_code else "").font = font_regular

    ws2.merge_cells("A4:K4")
    c = ws2.cell(4, 1, f"BẢNG KÊ HÓA ĐƠN BÁN RA (ĐẦU RA) — {period_label.upper()}")
    c.font = font_title
    c.alignment = align_center

    out_headers = [
        "STT", "Ký hiệu", "Số hóa đơn", "Ngày lập", "Tên người mua",
        "Mã số thuế", "Mặt hàng / Dịch vụ", "Doanh số chưa thuế",
        "Thuế suất", "Tiền thuế GTGT", "Tổng thanh toán"
    ]
    for col_idx, h in enumerate(out_headers, 1):
        cell = ws2.cell(6, col_idx, h)
        cell.font = font_header
        cell.fill = navy_fill
        cell.alignment = align_header
        cell.border = border_all
    ws2.row_dimensions[6].height = 26

    out_row_idx = 7
    for idx, inv in enumerate(output_invoices, 1):
        ws2.cell(out_row_idx, 1, idx).alignment = align_center
        ws2.cell(out_row_idx, 2, inv["serial"]).alignment = align_center
        ws2.cell(out_row_idx, 3, inv["invoice_number"]).alignment = align_center
        inv_d = inv["invoice_date"]
        ws2.cell(out_row_idx, 4, inv_d.strftime("%d/%m/%Y") if isinstance(inv_d, (date, datetime)) else str(inv_d)).alignment = align_center
        ws2.cell(out_row_idx, 5, inv["buyer_name"]).alignment = align_left
        ws2.cell(out_row_idx, 6, inv["buyer_tax_code"]).alignment = align_center
        ws2.cell(out_row_idx, 7, inv["service_name"]).alignment = align_left

        c_sub = ws2.cell(out_row_idx, 8, float(inv["subtotal"]))
        c_sub.alignment = align_right
        c_sub.number_format = money_fmt

        ws2.cell(out_row_idx, 9, inv["vat_rate_label"]).alignment = align_center

        c_vat = ws2.cell(out_row_idx, 10, float(inv["vat_amount"]))
        c_vat.alignment = align_right
        c_vat.number_format = money_fmt

        c_gross = ws2.cell(out_row_idx, 11, float(inv["gross"]))
        c_gross.alignment = align_right
        c_gross.number_format = money_fmt

        for col in range(1, 12):
            cell = ws2.cell(out_row_idx, col)
            cell.font = font_regular
            cell.border = border_all
        out_row_idx += 1

    # Dòng tổng cộng đầu ra
    ws2.cell(out_row_idx, 1, "TỔNG CỘNG").alignment = align_center
    ws2.merge_cells(start_row=out_row_idx, start_column=1, end_row=out_row_idx, end_column=7)

    c_tot_sub = ws2.cell(out_row_idx, 8, float(summary["total_out_subtotal"]))
    c_tot_sub.alignment = align_right
    c_tot_sub.number_format = money_fmt

    ws2.cell(out_row_idx, 9, "").alignment = align_center

    c_tot_vat = ws2.cell(out_row_idx, 10, float(summary["total_out_vat"]))
    c_tot_vat.alignment = align_right
    c_tot_vat.number_format = money_fmt

    c_tot_gross = ws2.cell(out_row_idx, 11, float(summary["total_out_gross"]))
    c_tot_gross.alignment = align_right
    c_tot_gross.number_format = money_fmt

    for col in range(1, 12):
        cell = ws2.cell(out_row_idx, col)
        cell.font = font_bold
        cell.fill = light_blue_fill
        cell.border = border_total

    ws2.column_dimensions["A"].width = 6
    ws2.column_dimensions["B"].width = 12
    ws2.column_dimensions["C"].width = 14
    ws2.column_dimensions["D"].width = 13
    ws2.column_dimensions["E"].width = 36
    ws2.column_dimensions["F"].width = 16
    ws2.column_dimensions["G"].width = 38
    ws2.column_dimensions["H"].width = 20
    ws2.column_dimensions["I"].width = 12
    ws2.column_dimensions["J"].width = 18
    ws2.column_dimensions["K"].width = 20

    # =========================================================================
    # SHEET 3: BẢNG KÊ ĐẦU VÀO (MUA VÀO)
    # =========================================================================
    ws3 = wb.create_sheet(title="BẢNG KÊ ĐẦU VÀO")
    ws3.views.sheetView[0].showGridLines = True

    ws3.cell(1, 1, company_name).font = font_subtitle
    ws3.cell(2, 1, f"MST: {tax_code}" if tax_code else "").font = font_regular

    ws3.merge_cells("A4:K4")
    c = ws3.cell(4, 1, f"BẢNG KÊ HÓA ĐƠN MUA VÀO (ĐẦU VÀO) — {period_label.upper()}")
    c.font = font_title
    c.alignment = align_center

    in_headers = [
        "STT", "Ký hiệu", "Số hóa đơn", "Ngày lập", "Tên người bán",
        "Mã số thuế", "Mặt hàng / Dịch vụ", "Giá trị chưa thuế",
        "Thuế suất", "Tiền thuế GTGT", "Tổng thanh toán"
    ]
    for col_idx, h in enumerate(in_headers, 1):
        cell = ws3.cell(6, col_idx, h)
        cell.font = font_header
        cell.fill = dark_slate_fill
        cell.alignment = align_header
        cell.border = border_all
    ws3.row_dimensions[6].height = 26

    in_row_idx = 7
    for idx, inv in enumerate(input_invoices, 1):
        ws3.cell(in_row_idx, 1, idx).alignment = align_center
        ws3.cell(in_row_idx, 2, inv["serial"]).alignment = align_center
        ws3.cell(in_row_idx, 3, inv["invoice_number"]).alignment = align_center
        inv_d = inv["invoice_date"]
        ws3.cell(in_row_idx, 4, inv_d.strftime("%d/%m/%Y") if isinstance(inv_d, (date, datetime)) else str(inv_d)).alignment = align_center
        ws3.cell(in_row_idx, 5, inv["seller_name"]).alignment = align_left
        ws3.cell(in_row_idx, 6, inv["seller_tax_code"]).alignment = align_center
        ws3.cell(in_row_idx, 7, inv["items_description"]).alignment = align_left

        c_sub = ws3.cell(in_row_idx, 8, float(inv["subtotal"]))
        c_sub.alignment = align_right
        c_sub.number_format = money_fmt

        ws3.cell(in_row_idx, 9, "" if inv.get("is_kkknt") else (inv.get("vat_rate_label") or "")).alignment = align_center

        c_vat = ws3.cell(in_row_idx, 10, float(inv["vat_amount"]))
        c_vat.alignment = align_right
        c_vat.number_format = money_fmt

        c_tot = ws3.cell(in_row_idx, 11, float(inv["total"]))
        c_tot.alignment = align_right
        c_tot.number_format = money_fmt

        for col in range(1, 12):
            cell = ws3.cell(in_row_idx, col)
            cell.font = font_regular
            cell.border = border_all
        in_row_idx += 1

    # Dòng tổng cộng đầu vào
    ws3.cell(in_row_idx, 1, "TỔNG CỘNG").alignment = align_center
    ws3.merge_cells(start_row=in_row_idx, start_column=1, end_row=in_row_idx, end_column=7)

    c_tot_sub = ws3.cell(in_row_idx, 8, float(summary["total_in_subtotal"]))
    c_tot_sub.alignment = align_right
    c_tot_sub.number_format = money_fmt

    ws3.cell(in_row_idx, 9, "").alignment = align_center

    c_tot_vat = ws3.cell(in_row_idx, 10, float(summary["total_in_vat"]))
    c_tot_vat.alignment = align_right
    c_tot_vat.number_format = money_fmt

    c_tot_tot = ws3.cell(in_row_idx, 11, float(summary["total_in_total"]))
    c_tot_tot.alignment = align_right
    c_tot_tot.number_format = money_fmt

    for col in range(1, 12):
        cell = ws3.cell(in_row_idx, col)
        cell.font = font_bold
        cell.fill = light_blue_fill
        cell.border = border_total

    ws3.column_dimensions["A"].width = 6
    ws3.column_dimensions["B"].width = 12
    ws3.column_dimensions["C"].width = 14
    ws3.column_dimensions["D"].width = 13
    ws3.column_dimensions["E"].width = 36
    ws3.column_dimensions["F"].width = 16
    ws3.column_dimensions["G"].width = 38
    ws3.column_dimensions["H"].width = 20
    ws3.column_dimensions["I"].width = 12
    ws3.column_dimensions["J"].width = 18
    ws3.column_dimensions["K"].width = 20

    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()
