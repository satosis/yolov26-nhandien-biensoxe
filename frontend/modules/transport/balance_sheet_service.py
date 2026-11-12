"""Balance Sheet (Báo cáo tình hình tài chính - Mẫu B01b-DNN) service for Dai Lo Xanh."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from frontend.context.company_context import get_current_company_id
from frontend.modules.transport.prepaid_expense_service import get_prepaid_expenses_data
from frontend.modules.transport.trial_balance_service import get_trial_balance_data


B01_ROWS: list[tuple[str, str, str]] = [
    ("", "TÀI SẢN", "section"),
    ("100", "A. TÀI SẢN NGẮN HẠN (100 = 110+120+130+140+150)", "group"),
    ("110", "I. Tiền và các khoản tương đương tiền", "group"),
    ("120", "II. Đầu tư tài chính ngắn hạn", "group"),
    ("121", "1. Chứng khoán kinh doanh", "item"),
    ("122", "2. Dự phòng giảm giá chứng khoán kinh doanh (*)", "item"),
    ("123", "3. Đầu tư nắm giữ đến ngày đáo hạn ngắn hạn", "item"),
    ("130", "III. Các khoản phải thu ngắn hạn", "group"),
    ("131", "1. Phải thu ngắn hạn của khách hàng", "item"),
    ("132", "2. Trả trước cho người bán ngắn hạn", "item"),
    ("133", "3. Phải thu ngắn hạn khác", "item"),
    ("134", "4. Tài sản thiếu chờ xử lý", "item"),
    ("135", "5. Dự phòng phải thu ngắn hạn khó đòi (*)", "item"),
    ("140", "IV. Hàng tồn kho", "group"),
    ("141", "1. Hàng tồn kho", "item"),
    ("142", "2. Dự phòng giảm giá hàng tồn kho (*)", "item"),
    ("150", "V. Tài sản ngắn hạn khác", "group"),
    ("151", "1. Thuế GTGT được khấu trừ", "item"),
    ("152", "2. Tài sản ngắn hạn khác", "item"),
    ("200", "B. TÀI SẢN DÀI HẠN (200 = 210+220+230+240+250+260)", "group"),
    ("210", "I. Các khoản phải thu dài hạn", "group"),
    ("211", "1. Phải thu dài hạn của khách hàng", "item"),
    ("212", "2. Trả trước cho người bán dài hạn", "item"),
    ("213", "3. Vốn kinh doanh ở đơn vị trực thuộc", "item"),
    ("214", "4. Phải thu dài hạn khác", "item"),
    ("215", "5. Dự phòng phải thu dài hạn khó đòi (*)", "item"),
    ("220", "II. Tài sản cố định", "group"),
    ("221", "- Nguyên giá", "item"),
    ("222", "- Giá trị hao mòn lũy kế (*)", "item"),
    ("230", "III. Bất động sản đầu tư", "group"),
    ("231", "- Nguyên giá", "item"),
    ("232", "- Giá trị hao mòn lũy kế (*)", "item"),
    ("240", "IV. Xây dựng cơ bản dở dang", "item"),
    ("250", "V. Đầu tư tài chính dài hạn", "group"),
    ("251", "1. Đầu tư góp vốn vào đơn vị khác", "item"),
    ("252", "2. Dự phòng tổn thất đầu tư vào đơn vị khác (*)", "item"),
    ("253", "3. Đầu tư nắm giữ đến ngày đáo hạn dài hạn", "item"),
    ("260", "VI. Tài sản dài hạn khác", "item"),
    ("300", "TỔNG CỘNG TÀI SẢN (300 = 100+200)", "group"),
    ("", "NGUỒN VỐN", "section"),
    ("400", "C. NỢ PHẢI TRẢ (400 = 410+420)", "group"),
    ("410", "I. Nợ ngắn hạn", "group"),
    ("411", "1. Phải trả người bán ngắn hạn", "item"),
    ("412", "2. Người mua trả tiền trước ngắn hạn", "item"),
    ("413", "3. Thuế và các khoản phải nộp Nhà nước", "item"),
    ("414", "4. Phải trả người lao động", "item"),
    ("415", "5. Phải trả ngắn hạn khác", "item"),
    ("416", "6. Vay và nợ thuê tài chính ngắn hạn", "item"),
    ("417", "7. Dự phòng phải trả ngắn hạn", "item"),
    ("418", "8. Quỹ khen thưởng, phúc lợi", "item"),
    ("420", "II. Nợ dài hạn", "group"),
    ("421", "1. Phải trả người bán dài hạn", "item"),
    ("422", "2. Người mua trả tiền trước dài hạn", "item"),
    ("423", "3. Phải trả nội bộ về vốn kinh doanh", "item"),
    ("424", "4. Phải trả dài hạn khác", "item"),
    ("425", "5. Vay và nợ thuê tài chính dài hạn", "item"),
    ("426", "6. Dự phòng phải trả dài hạn", "item"),
    ("427", "7. Quỹ phát triển khoa học và công nghệ", "item"),
    ("500", "D. VỐN CHỦ SỞ HỮU (500 = 511+512+513+514+515+516+517)", "group"),
    ("511", "1. Vốn góp của chủ sở hữu", "item"),
    ("512", "2. Thặng dư vốn cổ phần", "item"),
    ("513", "3. Vốn khác của chủ sở hữu", "item"),
    ("514", "4. Cổ phiếu quỹ (*)", "item"),
    ("515", "5. Chênh lệch tỷ giá hối đoái", "item"),
    ("516", "6. Các quỹ thuộc vốn chủ sở hữu", "item"),
    ("517", "7. Lợi nhuận sau thuế chưa phân phối", "item"),
    ("600", "TỔNG CỘNG NGUỒN VỐN (600 = 400+500)", "group"),
]


def _net_map(rows: list[dict], side: str):
    """side in ('close', 'open'). Returns net(prefix) for leaf rows."""
    dr_key = f"{side}_dr"
    cr_key = f"{side}_cr"

    def net(prefix: str | int) -> int:
        prefix_str = str(prefix).strip()
        tot = 0.0
        for r in rows:
            if not r.get("is_parent"):
                c = str(r.get("code") or "").strip()
                if c.startswith(prefix_str):
                    dr = float(r.get(dr_key) or 0)
                    cr = float(r.get(cr_key) or 0)
                    tot += (dr - cr)
        return int(round(tot))

    return net


def _leaf_net(row: dict, side: str) -> int:
    return int(round(float(row.get(f"{side}_dr") or 0) - float(row.get(f"{side}_cr") or 0)))


def build_balance_sheet_values(tb_data: dict, side: str, short_242: int | None = None) -> dict[str, int]:
    """Tính toàn bộ các mã số của Báo cáo tình hình tài chính theo mẫu B01b-DNN."""
    rows = tb_data.get("all_rows") or tb_data.get("rows") or []
    net = _net_map(rows, side)
    pos = lambda x: max(x, 0)

    # 110 = net(111)+net(112)+net(113); 121 = net(121); 122 = net(2291); 123 = net(128); 120 = 121+122+123;
    row_110 = net("111") + net("112") + net("113")
    row_121 = net("121")
    row_122 = net("2291")
    row_123 = net("128")
    row_120 = row_121 + row_122 + row_123

    leaf_338 = [_leaf_net(r, side) for r in rows if str(r.get("code") or "").startswith("338") and not r.get("is_parent")]

    # 131 = pos(net(131)); 132 = pos(net(331)); 133 = net(136)+(net(138)−net(1381))+net(141)+pos(net(334))+dư Nợ từng TK chi tiết 338; 134 = net(1381); 135 = net(2293); 130 = 131+132+133+134+135;
    row_131 = pos(net("131"))
    row_132 = pos(net("331"))
    row_133 = net("136") + (net("138") - net("1381")) + net("141") + pos(net("334")) + sum(pos(n) for n in leaf_338)
    row_134 = net("1381")
    row_135 = net("2293")
    row_130 = row_131 + row_132 + row_133 + row_134 + row_135

    # 141 = net(151)+net(152)+net(153)+net(154)+net(155)+net(156)+net(157)+net(158); 142 = net(2294); 140 = 141+142;
    row_141 = (
        net("151") + net("152") + net("153") + net("154")
        + net("155") + net("156") + net("157") + net("158")
    )
    row_142 = net("2294")
    row_140 = row_141 + row_142

    # 151 = pos(net(133)); 152 = short_242 + dư Nợ từng TK chi tiết 333 (thuế nộp thừa) (nếu short_242 là None thì dùng toàn bộ net(242)); 150 = 151+152; 100 = 110+120+130+140+150;
    row_151 = pos(net("133"))
    short_242_val = net("242") if short_242 is None else short_242
    leaf_333 = [n for n in (_leaf_net(r, side) for r in rows if str(r.get("code") or "").startswith("333") and not r.get("is_parent"))]
    row_152 = short_242_val + sum(pos(n) for n in leaf_333)
    row_150 = row_151 + row_152
    row_100 = row_110 + row_120 + row_130 + row_140 + row_150

    # 211 = 212 = 213 = 214 = 215 = 0; 210 = tổng (0);
    row_211 = row_212 = row_213 = row_214 = row_215 = 0
    row_210 = 0

    # 221 = net(211)+net(212)+net(213); 222 = net(2141)+net(2142)+net(2143); 220 = 221+222;
    row_221 = net("211") + net("212") + net("213")
    row_222 = net("2141") + net("2142") + net("2143")
    row_220 = row_221 + row_222

    # 231 = net(217); 232 = net(2147); 230 = 231+232; 240 = net(241);
    row_231 = net("217")
    row_232 = net("2147")
    row_230 = row_231 + row_232
    row_240 = net("241")

    # 251 = net(221)+net(222)+net(228); 252 = net(2292); 253 = 0; 250 = 251+252+253;
    row_251 = net("221") + net("222") + net("228")
    row_252 = net("2292")
    row_253 = 0
    row_250 = row_251 + row_252 + row_253

    # 260 = (net(242) − (152 phần 242)) + net(244) — tức phần TK 242 còn lại sau khi trừ phần ngắn hạn, cộng TK 244; 200 = 210+220+230+240+250+260; 300 = 100+200.
    row_260 = (net("242") - short_242_val) + net("244")
    row_200 = row_210 + row_220 + row_230 + row_240 + row_250 + row_260
    row_300 = row_100 + row_200

    # 411 = pos(−net(331)); 412 = pos(−net(131)); 413 = pos(−net(333)); 414 = pos(−net(334)); 415 = pos(−net(335))+pos(−net(336))+pos(−net(337))+dư Có từng TK chi tiết 338+pos(−net(344)); 416 = pos(−net(341))+pos(−net(343)); 417 = −net(352); 418 = −net(353); 410 = 411+…+418; 421…427 = 0; 420 = 0; 400 = 410+420;
    row_411 = pos(-net("331"))
    row_412 = pos(-net("131"))
    row_413 = sum(pos(-n) for n in leaf_333)  # dư Có từng TK chi tiết 333
    row_414 = pos(-net("334"))
    row_415 = (
        pos(-net("335")) + pos(-net("336")) + pos(-net("337"))
        + sum(pos(-n) for n in leaf_338) + pos(-net("344"))
    )
    row_416 = pos(-net("341")) + pos(-net("343"))
    row_417 = -net("352")
    row_418 = -net("353")
    row_410 = (
        row_411 + row_412 + row_413 + row_414
        + row_415 + row_416 + row_417 + row_418
    )
    row_421 = row_422 = row_423 = row_424 = row_425 = row_426 = row_427 = 0
    row_420 = 0
    row_400 = row_410 + row_420

    # 511 = −(net(411) − net(4112) − net(4118)); 512 = −net(4112); 513 = −net(4118); 514 = −net(419); 515 = −net(413); 516 = −(net(414)+net(417)+net(418)); 517 = −net(421); 500 = 511+…+517; 600 = 400+500.
    row_511 = -(net("411") - net("4112") - net("4118"))
    row_512 = -net("4112")
    row_513 = -net("4118")
    row_514 = -net("419")
    row_515 = -net("413")
    row_516 = -(net("414") + net("417") + net("418"))
    row_517 = -net("421")
    row_500 = (
        row_511 + row_512 + row_513 + row_514
        + row_515 + row_516 + row_517
    )
    row_600 = row_400 + row_500

    return {
        "100": row_100,
        "110": row_110,
        "120": row_120,
        "121": row_121,
        "122": row_122,
        "123": row_123,
        "130": row_130,
        "131": row_131,
        "132": row_132,
        "133": row_133,
        "134": row_134,
        "135": row_135,
        "140": row_140,
        "141": row_141,
        "142": row_142,
        "150": row_150,
        "151": row_151,
        "152": row_152,
        "200": row_200,
        "210": row_210,
        "211": row_211,
        "212": row_212,
        "213": row_213,
        "214": row_214,
        "215": row_215,
        "220": row_220,
        "221": row_221,
        "222": row_222,
        "230": row_230,
        "231": row_231,
        "232": row_232,
        "240": row_240,
        "250": row_250,
        "251": row_251,
        "252": row_252,
        "253": row_253,
        "260": row_260,
        "300": row_300,
        "400": row_400,
        "410": row_410,
        "411": row_411,
        "412": row_412,
        "413": row_413,
        "414": row_414,
        "415": row_415,
        "416": row_416,
        "417": row_417,
        "418": row_418,
        "420": row_420,
        "421": row_421,
        "422": row_422,
        "423": row_423,
        "424": row_424,
        "425": row_425,
        "426": row_426,
        "427": row_427,
        "500": row_500,
        "511": row_511,
        "512": row_512,
        "513": row_513,
        "514": row_514,
        "515": row_515,
        "516": row_516,
        "517": row_517,
        "600": row_600,
    }


def _short_term_242(company_id: int | None, year: int, as_of: date, net_242: int | None = None) -> int | None:
    """Tách TK 242 thành phần ngắn hạn (phân bổ trong 12 tháng tới)."""
    try:
        cid = company_id or get_current_company_id()
        if not cid:
            return None
        res = get_prepaid_expenses_data(company_id=cid, year=as_of.year, as_of_month=as_of.month)
        items = res.get("items") or []
        tot = 0
        for it in items:
            rem = int(it.get("rem_end") or 0)
            m = int(it.get("monthly_alloc") or 0)
            tot += min(rem, 12 * m)
        if net_242 is not None:
            return max(0, min(tot, max(0, net_242)))
        return max(0, tot)
    except Exception:
        return None


def get_balance_sheet_data(year: int, date_to: date, company_id: int | None = None) -> dict[str, Any]:
    """Lấy dữ liệu Báo cáo tình hình tài chính (B01b-DNN)."""
    company_id = company_id or get_current_company_id()
    tb = get_trial_balance_data(company_id=company_id, year=year, date_from=date(year, 1, 1), date_to=date_to)
    rows_leaf = tb.get("all_rows") or tb.get("rows") or []
    net_close = _net_map(rows_leaf, "close")
    net_242 = net_close("242")
    short_242 = _short_term_242(company_id, year, date_to, net_242=net_242)

    cur = build_balance_sheet_values(tb, "close", short_242)
    opening = build_balance_sheet_values(tb, "open", None)

    rows = []
    for code, label, kind in B01_ROWS:
        rows.append({
            "code": code,
            "label": label,
            "kind": kind,
            "current": cur.get(code, 0) if kind != "section" else None,
            "opening": opening.get(code, 0) if kind != "section" else None,
        })

    total_assets = cur.get("300", 0)
    total_sources = cur.get("600", 0)
    difference = total_assets - total_sources
    is_balanced = (difference == 0)
    opening_difference = opening.get("300", 0) - opening.get("600", 0)

    return {
        "company_name": tb.get("company_name", ""),
        "address": tb.get("address", ""),
        "tax_code": tb.get("tax_code", ""),
        "director_name": tb.get("director_name", ""),
        "year": year,
        "date_to": date_to,
        "rows": rows,
        "total_assets": total_assets,
        "total_sources": total_sources,
        "difference": difference,
        "is_balanced": is_balanced,
        "opening_difference": opening_difference,
    }


def export_balance_sheet_excel(year: int, date_to: date, company_id: int | None = None) -> bytes:
    """Xuất Báo cáo tình hình tài chính (Mẫu B01b-DNN) ra Excel."""
    data = get_balance_sheet_data(year=year, date_to=date_to, company_id=company_id)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "B01b-DNN"
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
    ws["D1"] = "Mẫu số B01b - DNN"
    ws["D1"].font = font_mau
    ws["D1"].alignment = Alignment(horizontal="right")

    ws.merge_cells("D2:E2")
    ws["D2"] = "(Ban hành theo Thông tư số 133/2016/TT-BTC"
    ws["D2"].font = font_tt
    ws["D2"].alignment = Alignment(horizontal="right")

    ws.merge_cells("D3:E3")
    ws["D3"] = "ngày 26/8/2016 của Bộ Tài chính)"
    ws["D3"].font = font_tt
    ws["D3"].alignment = Alignment(horizontal="right")

    ws.merge_cells("A5:E5")
    ws["A5"] = "BÁO CÁO TÌNH HÌNH TÀI CHÍNH"
    ws["A5"].font = font_title
    ws["A5"].alignment = Alignment(horizontal="center")

    d_to_str = f"Tại ngày {date_to.day:02d} tháng {date_to.month:02d} năm {date_to.year}"
    ws.merge_cells("A6:E6")
    ws["A6"] = d_to_str
    ws["A6"].font = font_period
    ws["A6"].alignment = Alignment(horizontal="center")

    ws["E7"] = "Đơn vị tính: đồng"
    ws["E7"].font = font_dvt
    ws["E7"].alignment = Alignment(horizontal="right")

    num_fmt = '#,##0;(#,##0);"-"'
    row_idx = 9

    for r in data.get("rows", []):
        if r.get("kind") == "section":
            section_label = r.get("label", "")
            headers = [section_label, "Mã số", "Thuyết minh", "Số cuối năm", "Số đầu năm"]
            for col_idx, h in enumerate(headers, 1):
                cell = ws.cell(row_idx, col_idx, h)
                cell.font = font_tbl_hdr
                cell.fill = fill_hdr
                cell.border = border_thin
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            row_idx += 1

            sub_headers = ["1", "2", "3", "4", "5"]
            for col_idx, sh in enumerate(sub_headers, 1):
                cell = ws.cell(row_idx, col_idx, sh)
                cell.font = font_tbl_subhdr
                cell.fill = fill_hdr
                cell.border = border_thin
                cell.alignment = Alignment(horizontal="center", vertical="center")
            row_idx += 1
            continue

        is_bold = (r.get("kind") == "group")
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

        cD = ws.cell(row_idx, 4, r.get("current", 0))
        cD.font = f
        cD.border = border_thin
        cD.alignment = Alignment(horizontal="right", vertical="center")
        cD.number_format = num_fmt

        cE = ws.cell(row_idx, 5, r.get("opening", 0))
        cE.font = f
        cE.border = border_thin
        cE.alignment = Alignment(horizontal="right", vertical="center")
        cE.number_format = num_fmt

        row_idx += 1

    # Khối chữ ký cách bảng 2 hàng
    sign_date_row = row_idx + 2
    today_vn = date.today()

    ws.merge_cells(start_row=sign_date_row, start_column=4, end_row=sign_date_row, end_column=5)
    cell_date = ws.cell(sign_date_row, 4, f"Lập, ngày {today_vn.day:02d} tháng {today_vn.month:02d} năm {today_vn.year}")
    cell_date.font = font_sign_sub
    cell_date.alignment = Alignment(horizontal="center")

    title_row = sign_date_row + 1
    ws.cell(title_row, 1, "Người lập biểu").font = font_sign_title
    ws.cell(title_row, 1).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=title_row, start_column=2, end_row=title_row, end_column=3)
    ws.cell(title_row, 2, "Kế toán trưởng").font = font_sign_title
    ws.cell(title_row, 2).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=title_row, start_column=4, end_row=title_row, end_column=5)
    ws.cell(title_row, 4, "Giám đốc").font = font_sign_title
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
