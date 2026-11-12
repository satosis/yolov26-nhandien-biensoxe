"""Generate and export Quotation (Bảng báo giá vận chuyển) matching the standard landscape template."""

from __future__ import annotations

from datetime import date, datetime
from html import escape
import io
import re
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


def _place_from_address(address: str | None) -> str:
    """Extract province/city name from address by stripping 'Việt Nam' and prefixes."""
    if not address or not isinstance(address, str):
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return ""
    if parts[-1].lower() in ("việt nam", "vietnam", "vn"):
        parts.pop()
    if not parts:
        return ""
    last = parts[-1].strip()
    prefixes = ("thành phố ", "tỉnh ", "tp. ", "tp ")
    lower_last = last.lower()
    for prefix in prefixes:
        if lower_last.startswith(prefix):
            return last[len(prefix):].strip()
    return last


def _quotation_info(
    customer: dict | None,
    seller: dict | None,
    quote_date: str | date | datetime | None = None,
) -> dict[str, Any]:
    """Prepare and normalize data for quotation rendering."""
    seller = seller or {}
    customer = customer or {}

    seller_name = (seller.get("name") or "").strip()
    seller_name_upper = seller_name.upper()
    seller_address = (seller.get("address") or "").strip()
    seller_tax_code = (seller.get("tax_code") or "").strip()
    seller_phone = (seller.get("phone") or "").strip()
    seller_email = (seller.get("email") or "").strip()

    place = _place_from_address(seller_address)
    footer_date = f"{place}, ngày ... tháng ... năm ..." if place else ", ngày ... tháng ... năm ..."

    date_str = ""
    if quote_date:
        if isinstance(quote_date, (date, datetime)):
            date_str = quote_date.strftime("%d.%m.%Y")
        elif isinstance(quote_date, str):
            q_strip = quote_date.strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}", q_strip):
                parts = q_strip[:10].split("-")
                date_str = f"{parts[2]}.{parts[1]}.{parts[0]}"
            elif re.match(r"^\d{2}\.\d{2}\.\d{4}", q_strip):
                date_str = q_strip[:10]
            elif re.match(r"^\d{2}/\d{2}/\d{4}", q_strip):
                parts = q_strip[:10].split("/")
                date_str = f"{parts[0]}.{parts[1]}.{parts[2]}"
            else:
                try:
                    date_str = datetime.fromisoformat(q_strip).strftime("%d.%m.%Y")
                except Exception:
                    date_str = date.today().strftime("%d.%m.%Y")
    if not date_str:
        date_str = date.today().strftime("%d.%m.%Y")

    cust_name = (customer.get("name") or "").strip()
    cust_phone = (customer.get("phone") or "").strip()
    cust_email = (customer.get("email") or "").strip()

    kinh_gui = cust_name if cust_name else "...................................................................................................."
    cust_phone_disp = cust_phone if cust_phone else "........................"
    cust_email_disp = cust_email if cust_email else "........................"

    seller_phone_disp = seller_phone if seller_phone else "......"
    seller_email_disp = seller_email if seller_email else "......"

    return {
        "seller_name": seller_name,
        "seller_name_upper": seller_name_upper,
        "seller_address": seller_address,
        "seller_tax_code": seller_tax_code,
        "seller_phone": seller_phone,
        "seller_email": seller_email,
        "place": place,
        "footer_date": footer_date,
        "date_str": date_str,
        "customer_name": cust_name,
        "kinh_gui": kinh_gui,
        "customer_phone_disp": cust_phone_disp,
        "customer_email_disp": cust_email_disp,
        "seller_phone_disp": seller_phone_disp,
        "seller_email_disp": seller_email_disp,
    }


def render_quotation_html(
    customer: dict | None,
    seller: dict | None,
    quote_date: str | date | datetime | None = None,
    preview: bool = True,
) -> str:
    """Render quotation as full landscape A4 HTML."""
    info = _quotation_info(customer, seller, quote_date)

    toolbar_html = ""
    if preview:
        toolbar_html = """
    <div class="no-print" style="position: sticky; top: 0; z-index: 1000; background: #0f172a; color: #fff; padding: 10px 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <span style="background: #2563eb; color: #fff; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 4px; text-transform: uppercase;">Báo giá vận chuyển</span>
        <span style="font-weight: 600; font-size: 13.5px; color: #e2e8f0;">Đơn vị: <strong style="color: #60a5fa;">""" + escape(info["seller_name"]) + """</strong></span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
        <button type="button" onclick="window.print()" style="background: #2563eb; color: #fff; border: none; padding: 6px 14px; border-radius: 5px; font-weight: 600; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
          In / Lưu PDF
        </button>
        <button type="button" onclick="downloadDocAsset('pdf')" style="background: #059669; color: #fff; border: none; padding: 6px 14px; border-radius: 5px; font-weight: 600; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Tải PDF
        </button>
        <button type="button" onclick="downloadDocAsset('docx')" style="background: #0284c7; color: #fff; border: none; padding: 6px 14px; border-radius: 5px; font-weight: 600; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
          Tải Word (.docx)
        </button>
      </div>
    </div>
    <script>
      function downloadDocAsset(fmt) {
        var url = new URL(window.location.href);
        url.searchParams.delete('view');
        url.searchParams.set('format', fmt);
        url.searchParams.set('download', fmt);
        window.location.href = url.toString();
      }
    </script>
    """

    rows_html = ""
    for i in range(1, 7):
        rows_html += f"""
        <tr class="q-table-row">
          <td class="stt">{i}</td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>Bảng báo giá vận chuyển</title>
  <style>
    @page {{
      size: A4 landscape;
      margin: 8mm;
    }}
    * {{
      box-sizing: border-box;
    }}
    html, body {{
      margin: 0;
      padding: 0;
      background: #525659;
      font-family: Arial, Helvetica, sans-serif;
      color: #000;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}
    .q-page {{
      width: 297mm;
      min-height: 210mm;
      margin: 15px auto;
      padding: 8mm 10mm;
      background: #fff;
      box-shadow: 0 4px 15px rgba(0,0,0,0.3);
      position: relative;
    }}
    @media print {{
      html, body {{
        background: #fff !important;
      }}
      .q-page {{
        margin: 0 !important;
        padding: 0 !important;
        box-shadow: none !important;
        width: 100% !important;
        min-height: auto !important;
      }}
      .no-print {{
        display: none !important;
      }}
    }}
    .q-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      page-break-inside: avoid;
    }}
    .q-table th {{
      background-color: #2F4B7C;
      color: #ffffff;
      font-size: 11px;
      font-weight: bold;
      text-align: center;
      padding: 6px 3px;
      border: none;
      vertical-align: middle;
    }}
    .q-table-sub {{
      background-color: #B8CCE4;
      height: 6px;
    }}
    .q-table-sub td {{
      padding: 0;
      height: 6px;
      border: none;
    }}
    .q-table-row {{
      height: 34px;
    }}
    .q-table-row td {{
      border: 1px dotted #888888;
      padding: 3px;
      font-size: 11px;
      vertical-align: middle;
    }}
    .q-table-row td.stt {{
      text-align: center;
    }}
    .q-table-total td {{
      background-color: #3B5A8A;
      color: #ffffff;
      font-weight: bold;
      font-size: 11.5px;
      padding: 6px;
      border: none;
      vertical-align: middle;
    }}
    .q-notes {{
      page-break-inside: avoid;
      margin-top: 8px;
      font-size: 11px;
      line-height: 1.45;
    }}
    .q-notes-title {{
      background-color: #FFFF00;
      font-weight: bold;
      font-size: 12.5px;
      padding: 1px 3px;
    }}
    .q-footer {{
      page-break-inside: avoid;
      margin-top: 10px;
      text-align: right;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  {toolbar_html}
  <div class="q-page">
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 6px;">
      <tr>
        <td style="text-align: center; vertical-align: middle;">
          <div style="font-size: 15px; font-weight: bold; color: #2A7B8C; text-transform: uppercase; margin-bottom: 3px;">
            {escape(info["seller_name_upper"])}
          </div>
          <div style="font-size: 11.5px; margin-bottom: 2px;">
            <strong>Văn phòng:</strong> {escape(info["seller_address"])}
          </div>
          <div style="font-size: 11.5px;">
            Hotline: {escape(info["seller_phone"])} &nbsp;&nbsp;&nbsp;&nbsp; Mã số thuế: {escape(info["seller_tax_code"])}
          </div>
        </td>
      </tr>
    </table>

    <div style="background-color: #4A7EBB; color: #ffffff; text-align: center; font-size: 14.5px; font-weight: bold; padding: 5px 0; margin-bottom: 6px; text-transform: uppercase;">
      BẢNG BÁO GIÁ VẬN CHUYỂN ĐƯỜNG BỘ
    </div>

    <div style="font-size: 11.5px; line-height: 1.4; margin-bottom: 5px;">
      <div><strong>Ngày:</strong> {escape(info["date_str"])}</div>
      <div><strong>Kính gửi:</strong> <strong>{escape(info["kinh_gui"])}</strong></div>
      <table style="width: 100%; border-collapse: collapse; font-size: 11.5px;">
        <tr>
          <td style="text-align: left; padding: 0;">SĐT: {escape(info["customer_phone_disp"])}</td>
          <td style="text-align: right; padding: 0;">Email: {escape(info["customer_email_disp"])}</td>
        </tr>
      </table>
    </div>

    <div style="font-size: 11px; font-style: italic; margin-bottom: 6px; line-height: 1.35;">
      Lời đầu tiên, xin trân trọng cảm ơn quý khách hàng đã quan tâm đến dịch vụ vận chuyển của công ty chúng tôi. Chúng tôi xin gửi đến quý khách hàng bảng báo giá như sau:
    </div>

    <table class="q-table">
      <colgroup>
        <col style="width: 4%;">
        <col style="width: 14%;">
        <col style="width: 5%;">
        <col style="width: 9%;">
        <col style="width: 11%;">
        <col style="width: 17%;">
        <col style="width: 11%;">
        <col style="width: 9%;">
        <col style="width: 10%;">
        <col style="width: 10%;">
      </colgroup>
      <thead>
        <tr>
          <th>STT</th>
          <th>TÊN HÀNG HÓA</th>
          <th>SL</th>
          <th>LOẠI XE</th>
          <th>TRỌNG TẢI XE (TẤN)</th>
          <th>TUYẾN ĐƯỜNG ĐI</th>
          <th>CƯỚC VẬN CHUYỂN</th>
          <th>PHỤ PHÍ</th>
          <th>THÀNH TIỀN</th>
          <th>GHI CHÚ</th>
        </tr>
        <tr class="q-table-sub">
          <td colspan="10"></td>
        </tr>
      </thead>
      <tbody>
{rows_html}
        <tr class="q-table-total">
          <td colspan="6" style="text-align: center;">TỔNG CỘNG:</td>
          <td></td><td></td><td></td><td></td>
        </tr>
      </tbody>
    </table>

    <div class="q-notes">
      <div style="margin-bottom: 3px;">
        <span class="q-notes-title">Ghi chú:</span>
      </div>
      <div>- Giá trên chưa bao gồm VAT 8%</div>
      <div>- Giao hàng sau ..... ngày kể từ ngày ký hợp đồng</div>
      <div>- Bảng báo giá chỉ áp dụng trong vòng 15 ngày kể từ ngày khách hàng nhận báo giá.</div>
      <div>- Mọi chi tiết vui lòng liên hệ: Mr/Ms ........................ &nbsp;&nbsp;Di động {escape(info["seller_phone_disp"])} &nbsp;&nbsp;Email {escape(info["seller_email_disp"])}</div>
    </div>

    <div class="q-footer">
      <div style="font-style: italic; margin-bottom: 3px;">
        {escape(info["footer_date"])}
      </div>
      <div style="font-weight: bold; text-transform: uppercase;">
        {escape(info["seller_name_upper"])}
      </div>
    </div>
  </div>
</body>
</html>
"""
    return html


def _set_run_arial(
    run,
    name: str = "Arial",
    size_pt: float = 10.0,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor | None = None,
    highlight: WD_COLOR_INDEX | None = None,
):
    """Configure run to use Arial font with full EastAsia support."""
    run.font.name = name
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color
    if highlight is not None:
        run.font.highlight_color = highlight
    rPr = run._r.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), name)
    rFonts.set(qn("w:hAnsi"), name)
    rFonts.set(qn("w:cs"), name)
    rFonts.set(qn("w:eastAsia"), name)


def _clear_table_borders(table):
    """Remove default borders from docx table."""
    tblPr = table._tbl.tblPr
    tblBorders = tblPr.find(qn("w:tblBorders"))
    if tblBorders is None:
        tblBorders = OxmlElement("w:tblBorders")
        anchor = next(
            (tblPr.find(qn(tag)) for tag in ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook")
             if tblPr.find(qn(tag)) is not None),
            None,
        )
        if anchor is not None:
            anchor.addprevious(tblBorders)
        else:
            tblPr.append(tblBorders)
    for border_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{border_name}")
        border.set(qn("w:val"), "nil")
        tblBorders.append(border)


def _set_cell_shading(cell, color_hex: str):
    """Apply background color to cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tcPr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex.lstrip("#"))


def _set_cell_dotted_borders(cell, color: str = "888888", sz: str = "4"):
    """Set dotted borders on all four sides of a cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = tcPr.find(qn("w:tcBorders"))
    if tcBorders is None:
        tcBorders = OxmlElement("w:tcBorders")
        tcPr.append(tcBorders)
    for side in ("top", "left", "bottom", "right"):
        b = OxmlElement(f"w:{side}")
        b.set(qn("w:val"), "dotted")
        b.set(qn("w:sz"), sz)
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), color)
        tcBorders.append(b)


def render_quotation_docx(
    customer: dict | None,
    seller: dict | None,
    quote_date: str | date | datetime | None = None,
) -> bytes:
    """Generate Word (.docx) document for the quotation."""
    info = _quotation_info(customer, seller, quote_date)

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(9)
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width = Mm(297)
    sec.page_height = Mm(210)
    sec.top_margin = Mm(10)
    sec.bottom_margin = Mm(10)
    sec.left_margin = Mm(10)
    sec.right_margin = Mm(10)

    # (a) Khối tên công ty
    t_header = doc.add_table(rows=1, cols=1)
    _clear_table_borders(t_header)
    t_header.rows[0].cells[0].width = Mm(277)

    comp_cell = t_header.rows[0].cells[0]
    comp_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p_name = comp_cell.paragraphs[0]
    p_name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_name.paragraph_format.space_before = Pt(0)
    p_name.paragraph_format.space_after = Pt(2)
    r_name = p_name.add_run(info["seller_name_upper"])
    _set_run_arial(r_name, size_pt=13.0, bold=True, color=RGBColor(0x2A, 0x7B, 0x8C))

    p_addr = comp_cell.add_paragraph()
    p_addr.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_addr.paragraph_format.space_before = Pt(0)
    p_addr.paragraph_format.space_after = Pt(2)
    r_vp = p_addr.add_run("Văn phòng: ")
    _set_run_arial(r_vp, size_pt=9.5, bold=True)
    r_addr = p_addr.add_run(info["seller_address"])
    _set_run_arial(r_addr, size_pt=9.5, bold=False)

    p_contact = comp_cell.add_paragraph()
    p_contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_contact.paragraph_format.space_before = Pt(0)
    p_contact.paragraph_format.space_after = Pt(0)
    r_contact = p_contact.add_run(f"Hotline: {info['seller_phone']}      Mã số thuế: {info['seller_tax_code']}")
    _set_run_arial(r_contact, size_pt=9.5, bold=False)

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(0)
    spacer.paragraph_format.line_spacing = Pt(4)

    # (b) Bảng 1 ô nền #4A7EBB cho dải tiêu đề
    t_title = doc.add_table(rows=1, cols=1)
    _clear_table_borders(t_title)
    t_title.rows[0].cells[0].width = Mm(277)
    _set_cell_shading(t_title.rows[0].cells[0], "4A7EBB")
    t_title.rows[0].cells[0].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p_title = t_title.rows[0].cells[0].paragraphs[0]
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(4)
    p_title.paragraph_format.space_after = Pt(4)
    r_title = p_title.add_run("BẢNG BÁO GIÁ VẬN CHUYỂN ĐƯỜNG BỘ")
    _set_run_arial(r_title, size_pt=13.0, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

    # (c) Đoạn Ngày/Kính gửi/SĐT-Email
    p_date = doc.add_paragraph()
    p_date.paragraph_format.space_before = Pt(4)
    p_date.paragraph_format.space_after = Pt(1)
    r_lbl_date = p_date.add_run("Ngày: ")
    _set_run_arial(r_lbl_date, size_pt=10.0, bold=True)
    r_val_date = p_date.add_run(info["date_str"])
    _set_run_arial(r_val_date, size_pt=10.0, bold=False)

    p_kg = doc.add_paragraph()
    p_kg.paragraph_format.space_before = Pt(0)
    p_kg.paragraph_format.space_after = Pt(1)
    r_lbl_kg = p_kg.add_run("Kính gửi: ")
    _set_run_arial(r_lbl_kg, size_pt=10.0, bold=True)
    r_val_kg = p_kg.add_run(info["kinh_gui"])
    _set_run_arial(r_val_kg, size_pt=10.0, bold=True)

    t_cust = doc.add_table(rows=1, cols=2)
    _clear_table_borders(t_cust)
    t_cust.rows[0].cells[0].width = Mm(138.5)
    t_cust.rows[0].cells[1].width = Mm(138.5)

    p_phone = t_cust.rows[0].cells[0].paragraphs[0]
    p_phone.paragraph_format.space_before = Pt(0)
    p_phone.paragraph_format.space_after = Pt(0)
    r_phone = p_phone.add_run(f"SĐT: {info['customer_phone_disp']}")
    _set_run_arial(r_phone, size_pt=10.0, bold=False)

    p_email = t_cust.rows[0].cells[1].paragraphs[0]
    p_email.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_email.paragraph_format.space_before = Pt(0)
    p_email.paragraph_format.space_after = Pt(0)
    r_email = p_email.add_run(f"Email: {info['customer_email_disp']}")
    _set_run_arial(r_email, size_pt=10.0, bold=False)

    # (d) Đoạn lời đầu nghiêng
    p_intro = doc.add_paragraph()
    p_intro.paragraph_format.space_before = Pt(3)
    p_intro.paragraph_format.space_after = Pt(4)
    r_intro = p_intro.add_run(
        "Lời đầu tiên, xin trân trọng cảm ơn quý khách hàng đã quan tâm đến dịch vụ vận chuyển của "
        "công ty chúng tôi. Chúng tôi xin gửi đến quý khách hàng bảng báo giá như sau:"
    )
    _set_run_arial(r_intro, size_pt=9.5, italic=True)

    # (e) Bảng 10 cột
    tbl = doc.add_table(rows=9, cols=10)
    _clear_table_borders(tbl)
    col_widths_mm = [11, 38, 14, 24, 30, 46, 30, 24, 30, 30]
    for row in tbl.rows:
        for idx, w in enumerate(col_widths_mm):
            row.cells[idx].width = Mm(w)

    headers = [
        "STT", "TÊN HÀNG HÓA", "SL", "LOẠI XE",
        "TRỌNG TẢI XE (TẤN)", "TUYẾN ĐƯỜNG ĐI", "CƯỚC VẬN CHUYỂN",
        "PHỤ PHÍ", "THÀNH TIỀN", "GHI CHÚ",
    ]
    for idx, h in enumerate(headers):
        cell = tbl.rows[0].cells[idx]
        _set_cell_shading(cell, "2F4B7C")
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(h)
        _set_run_arial(r, size_pt=9.0, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

    for idx in range(10):
        c_sub = tbl.rows[1].cells[idx]
        _set_cell_shading(c_sub, "B8CCE4")
        p_sub = c_sub.paragraphs[0]
        p_sub.paragraph_format.space_before = Pt(0)
        p_sub.paragraph_format.space_after = Pt(0)
        p_sub.paragraph_format.line_spacing = Pt(4)

    for i in range(6):
        r_empty = tbl.rows[2 + i]
        c0 = r_empty.cells[0]
        p0 = c0.paragraphs[0]
        p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p0.paragraph_format.space_before = Pt(2)
        p0.paragraph_format.space_after = Pt(2)
        r_num = p0.add_run(str(i + 1))
        _set_run_arial(r_num, size_pt=9.0, bold=False)
        r_empty.height = Pt(24)
        for c in r_empty.cells:
            _set_cell_dotted_borders(c)

    tbl.rows[8].cells[0].merge(tbl.rows[8].cells[5])
    for c in tbl.rows[8].cells:
        _set_cell_shading(c, "3B5A8A")
    c_tot = tbl.rows[8].cells[0]
    c_tot.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p_tot = c_tot.paragraphs[0]
    p_tot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_tot.paragraph_format.space_before = Pt(3)
    p_tot.paragraph_format.space_after = Pt(3)
    r_tot = p_tot.add_run("TỔNG CỘNG:")
    _set_run_arial(r_tot, size_pt=9.5, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

    # (f) Khối Ghi chú
    p_note_hdr = doc.add_paragraph()
    p_note_hdr.paragraph_format.space_before = Pt(4)
    p_note_hdr.paragraph_format.space_after = Pt(1)
    r_note_lbl = p_note_hdr.add_run("Ghi chú:")
    _set_run_arial(r_note_lbl, size_pt=10.5, bold=True, highlight=WD_COLOR_INDEX.YELLOW)

    note_lines = [
        "- Giá trên chưa bao gồm VAT 8%",
        "- Giao hàng sau ..... ngày kể từ ngày ký hợp đồng",
        "- Bảng báo giá chỉ áp dụng trong vòng 15 ngày kể từ ngày khách hàng nhận báo giá.",
        f"- Mọi chi tiết vui lòng liên hệ: Mr/Ms ........................  Di động {info['seller_phone_disp']}  Email {info['seller_email_disp']}",
    ]
    for nl in note_lines:
        p_line = doc.add_paragraph()
        p_line.paragraph_format.space_before = Pt(0)
        p_line.paragraph_format.space_after = Pt(1)
        r_line = p_line.add_run(nl)
        _set_run_arial(r_line, size_pt=9.0, bold=False)

    # (g) Chân căn phải
    p_ft_date = doc.add_paragraph()
    p_ft_date.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_ft_date.paragraph_format.space_before = Pt(4)
    p_ft_date.paragraph_format.space_after = Pt(1)
    r_ft_date = p_ft_date.add_run(info["footer_date"])
    _set_run_arial(r_ft_date, size_pt=9.5, italic=True)

    p_ft_comp = doc.add_paragraph()
    p_ft_comp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_ft_comp.paragraph_format.space_before = Pt(0)
    p_ft_comp.paragraph_format.space_after = Pt(0)
    r_ft_comp = p_ft_comp.add_run(info["seller_name_upper"])
    _set_run_arial(r_ft_comp, size_pt=10.0, bold=True)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
