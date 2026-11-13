"""Generate and export Transport Contracts (Hợp đồng vận chuyển) matching the standard template."""

from __future__ import annotations

import base64
import io
from html import escape
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.services import config as cfg
try:
    import fitz  # PyMuPDF fallback
except ImportError:
    fitz = None
from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Mm, Pt, RGBColor
from frontend.modules.gender_utils import split_rep_honorific
from frontend.modules.invoices.company_images import company_image_print_width_mm


def _to_data_uri_if_local(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith("data:"):
        return url
    clean_url = url.split("?")[0].lstrip("/")
    if clean_url.startswith("upload/"):
        disk_path = Path(cfg.BASE_DIR) / clean_url
        if disk_path.exists() and disk_path.is_file():
            mime = "image/png"
            suffix = disk_path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                mime = "image/jpeg"
            elif suffix == ".webp":
                mime = "image/webp"
            elif suffix == ".svg":
                mime = "image/svg+xml"
            try:
                data = base64.b64encode(disk_path.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{data}"
            except Exception:
                return url
    return url


def _signature_print_width_mm(url: str) -> float:
    clean_url = url.split("?")[0].lstrip("/")
    if clean_url.startswith("upload/"):
        return company_image_print_width_mm(Path(cfg.BASE_DIR) / clean_url)
    return company_image_print_width_mm("")


def _remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "D")


def derive_company_code(name: str) -> str:
    """Derive a clean, concise uppercase alphanumeric code for the contract number (e.g. CN247, NSKB)."""
    if not name:
        return "KH"
    clean = re.sub(
        r"^(CÔNG TY|CONG TY|CTCP|TNHH|CP|DNTN|DOANH NGHIỆP|TẬP ĐOÀN)\s+",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    ).strip()
    clean = re.sub(
        r"^((TNHH|CỔ PHẦN|CO PHAN|MTV|MỘT THÀNH VIÊN)\s+)+",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()

    digit_match = re.search(r"\b(\d+)\b", clean)
    if digit_match:
        digits = digit_match.group(1)
        words = [w for w in re.findall(r"[A-Za-zÀ-ỹ0-9]+", clean) if not w.isdigit()]
        if words:
            prefix = "".join(_remove_accents(w[0]) for w in words[-2:]).upper()
            return f"{prefix}{digits}"
        return digits

    words = re.findall(r"[A-Za-zÀ-ỹ0-9]+", clean)
    stopwords = {"VA", "VÀ", "THE", "OF", "AND", "CHO", "CỦA"}
    words = [w for w in words if _remove_accents(w).upper() not in stopwords]
    if not words:
        return "KH"
    if len(words) == 1:
        return _remove_accents(words[0]).upper()[:6]
    return "".join(_remove_accents(w[0]) for w in words).upper()[:6]


def default_contract_no(tax_code: str | None, c_date: date, cust_code: str, seller_code: str) -> str:
    """Số hợp đồng mặc định: MST bên khách/HĐNT/KH-NB; khách không có MST thì dùng ngày ký ddmmyy."""
    head = (tax_code or "").strip() or f"{c_date.day:02d}{c_date.month:02d}{str(c_date.year)[-2:]}"
    return f"{head}/HĐNT/{cust_code}-{seller_code}"


def _contract_bank_accounts(company: dict | None, fallback_number: str = "", fallback_name: str = "") -> list[dict]:
    accounts = (company or {}).get("bank_accounts") or []
    if accounts:
        return accounts
    number = (company or {}).get("bank_account") or fallback_number
    bank = (company or {}).get("bank_name") or fallback_name
    return [{"account_number": number, "bank_name": bank, "branch": ""}] if number or bank else []


def _contract_bank_text(account: dict) -> str:
    """'Tại ngân hàng <ngân hàng> - <chi nhánh>'; chi nhánh chỉ thêm khi có."""
    label = " - ".join(
        part for part in ((account.get("bank_name") or "").strip(), (account.get("branch") or "").strip()) if part
    )
    return f"Tại ngân hàng {label}" if label else ""


def _contract_bank_rows(accounts: list[dict]) -> str:
    rows = []
    for idx, account in enumerate(accounts):
        num = escape(str(account.get("account_number") or ""))
        bank_str = escape(_contract_bank_text(account))
        prefix = ": " if idx == 0 else '<span style="visibility:hidden">: </span>'
        label = "Tài khoản" if idx == 0 else ""
        acc_part = f'<span class="acc-num">{num}</span>' if num else ""
        rows.append(
            f'<tr><td class="col-label">{label}</td>'
            f'<td class="col-mid">{prefix}{acc_part}</td>'
            f'<td class="col-right acc-bank">{bank_str}</td></tr>'
        )
    return "\n".join(rows)


def _format_rep_html(rep: str, gender: str | None = None) -> str:
    prefix, name = split_rep_honorific(rep, gender)
    if not name:
        return "Ông/Bà ................................................."
    return f"{escape(prefix)}<span class=\"bold\">{escape(name.upper())}</span>"


def get_contract_context(
    customer_or_trip: dict,
    customer_or_none: dict | None = None,
    seller: dict | None = None,
    *,
    contract_date: date | str | None = None,
    contract_no: str | None = None,
) -> dict[str, Any]:
    """Prepare all template data from a company (and optional trip) and seller company."""
    if customer_or_none is not None and ("tax_code" in customer_or_none or "name" in customer_or_none):
        # Called as (trip, customer, seller)
        trip = customer_or_trip
        customer = customer_or_none
    else:
        # Called as (customer, trip=None, seller=seller)
        customer = customer_or_trip
        trip = customer_or_none or {}

    # Determine contract date
    c_date: date
    if contract_date:
        if isinstance(contract_date, datetime):
            c_date = contract_date.date()
        elif isinstance(contract_date, date):
            c_date = contract_date
        elif isinstance(contract_date, str) and contract_date:
            try:
                c_date = date.fromisoformat(contract_date[:10])
            except ValueError:
                c_date = date.today()
        else:
            c_date = date.today()
    else:
        raw_date = trip.get("invoice_date") or trip.get("departure_time") if trip else None
        if isinstance(raw_date, datetime):
            c_date = raw_date.date()
        elif isinstance(raw_date, date):
            c_date = raw_date
        elif isinstance(raw_date, str) and raw_date:
            try:
                c_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                c_date = date.today()
        else:
            c_date = date.today()

    day_str = f"{c_date.day:02d}"
    month_str = f"{c_date.month:02d}"
    year_str = str(c_date.year)

    customer_name = (customer.get("name") or "").strip()
    cust_code = derive_company_code(customer_name)

    # Bước 2: Bắt buộc phải có công ty đang vào (Bên B), không chạy nhánh dự phòng
    if not seller or not isinstance(seller, dict):
        raise ValueError("Bắt buộc phải chọn công ty đang làm việc (Bên B) trước khi xuất hợp đồng.")
    seller_name = (seller.get("name") or "").strip()
    if not seller_name:
        raise ValueError("Tên công ty đang làm việc (Bên B) không được để trống.")

    seller_code = derive_company_code(seller_name)
    if not contract_no:
        contract_no = default_contract_no(customer.get("tax_code"), c_date, cust_code, seller_code)

    seller_rep = (seller.get("director_name") or "").strip()
    seller_addr = (seller.get("address") or "").strip()
    seller_phone = (seller.get("phone") or "").strip()
    seller_tax = (seller.get("tax_code") or "").strip()
    seller_bank_acc = (seller.get("bank_account") or "").strip()
    seller_bank_name = (seller.get("bank_name") or "").strip()
    seller_email = (seller.get("email") or "").strip()
    seller_signature_url = (seller.get("signature_url") or "").strip()
    seller_stamp_url = (seller.get("stamp_url") or "").strip()

    customer_rep = (customer.get("director_name") or "").strip()
    customer_addr = (customer.get("address") or "").strip()
    customer_phone = (customer.get("phone") or "").strip()
    customer_tax = (customer.get("tax_code") or "").strip()
    customer_bank_acc = (customer.get("bank_account") or "").strip()
    customer_bank_name = (customer.get("bank_name") or "").strip()
    customer_signature_url = (customer.get("signature_url") or "").strip()
    customer_stamp_url = (customer.get("stamp_url") or "").strip()

    return {
        "trip_id": trip.get("id") if trip else None,
        "contract_no": contract_no,
        "contract_date": c_date,
        "day_str": day_str,
        "month_str": month_str,
        "year_str": year_str,
        "valid_until": f"31/12/{year_str}",
        # Customer (Bên A)
        "customer_name": customer_name,
        "customer_rep": customer_rep,
        "customer_pos": (customer.get("director_title") or "Giám đốc").strip(),
        "customer_addr": customer_addr,
        "customer_phone": customer_phone,
        "customer_tax": customer_tax,
        "customer_bank_acc": customer_bank_acc,
        "customer_bank_name": customer_bank_name,
        "customer_bank_accounts": _contract_bank_accounts(customer),
        "customer_signature_url": customer_signature_url,
        "customer_stamp_url": customer_stamp_url,
        "customer_gender": customer.get("director_gender") or customer.get("gender"),
        # Seller (Bên B)
        "seller_name": seller_name,
        "seller_rep": seller_rep,
        "seller_pos": (seller.get("director_title") or "Giám đốc").strip(),
        "seller_addr": seller_addr,
        "seller_phone": seller_phone,
        "seller_tax": seller_tax,
        "seller_bank_acc": seller_bank_acc,
        "seller_bank_name": seller_bank_name,
        "seller_bank_accounts": _contract_bank_accounts(seller, seller_bank_acc, seller_bank_name),
        "seller_email": seller_email,
        "seller_signature_url": seller_signature_url,
        "seller_stamp_url": seller_stamp_url,
        "seller_gender": seller.get("director_gender") or seller.get("gender"),
        "trip": trip,
    }


def render_contract_html(ctx: dict[str, Any], preview: bool = False) -> str:
    """Render the exact HTML representation matching Hợp đồng ĐLX - 247.pdf."""
    customer_rep_html = _format_rep_html(ctx.get("customer_rep", ""), ctx.get("customer_gender"))
    seller_rep_html = _format_rep_html(ctx.get("seller_rep", ""), ctx.get("seller_gender"))
    customer_bank_rows = _contract_bank_rows(ctx.get("customer_bank_accounts") or [
        {"account_number": ctx.get("customer_bank_acc"), "bank_name": ctx.get("customer_bank_name"), "branch": ""}
    ])
    seller_bank_rows = _contract_bank_rows(ctx.get("seller_bank_accounts") or [
        {"account_number": ctx.get("seller_bank_acc"), "bank_name": ctx.get("seller_bank_name"), "branch": ""}
    ])

    def _sig_slot_html(sig_url: str) -> str:
        sig_data = _to_data_uri_if_local(sig_url) if sig_url else ""
        if sig_data:
            width_mm = _signature_print_width_mm(sig_url)
            return f'<div class="sig-slot-inner"><img src="{sig_data}" alt="Chữ ký" style="width:{width_mm:g}mm"></div>'
        return '<div class="sig-slot-inner"></div>'

    customer_sig_url = ctx.get("customer_signature_url") or ctx.get("customer_stamp_url", "")
    seller_sig_url = ctx.get("seller_signature_url") or ctx.get("seller_stamp_url", "")
    customer_sig_html = _sig_slot_html(customer_sig_url)
    seller_sig_html = _sig_slot_html(seller_sig_url)

    top_bar = ""
    if preview:
        top_bar = f"""
        <div class="no-print" style="position: sticky; top: 0; z-index: 1000; background: #1e293b; color: #fff; padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 8px rgba(0,0,0,0.2);">
          <div style="font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 8px;">
            <span>Hợp đồng vận chuyển: <strong>{ctx['contract_no']}</strong></span>
          </div>
          <div style="display: flex; gap: 10px;">
            <button type="button" onclick="window.print()" style="background: #2563eb; color: #fff; border: none; padding: 7px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px;">
              In / Lưu PDF
            </button>
            <button type="button" onclick="downloadContractAsset('pdf')" style="background: #059669; color: #fff; border: none; padding: 7px 16px; border-radius: 6px; font-weight: 600; font-size: 13px; cursor: pointer;">
              Tải file PDF
            </button>
            <button type="button" onclick="downloadContractAsset('docx')" style="background: #0284c7; color: #fff; border: none; padding: 7px 16px; border-radius: 6px; font-weight: 600; font-size: 13px; cursor: pointer;">
              Tải file Word (.docx)
            </button>
            <button type="button" onclick="closeContractPreview()" style="background: #475569; color: #fff; border: none; padding: 7px 14px; border-radius: 6px; font-size: 13px; cursor: pointer;">
              Đóng
            </button>
          </div>
        </div>
        <script>
          function downloadContractAsset(format) {{
            try {{
              var url = new URL(window.location.href);
              url.searchParams.set('download', format);
              url.searchParams.delete('view');
              var link = document.createElement('a');
              link.href = url.toString();
              link.setAttribute('download', '');
              document.body.appendChild(link);
              link.click();
              link.remove();
            }} catch (e) {{
              var sep = window.location.href.indexOf('?') !== -1 ? '&' : '?';
              window.location.href = window.location.href + sep + 'download=' + format;
            }}
          }}
          function closeContractPreview() {{
            try {{
              if (window.parent && window.parent !== window && window.parent.$) {{
                window.parent.$('#contractPreviewModal').modal('hide');
                return;
              }}
            }} catch (e) {{}}
            try {{ window.close(); }} catch (e) {{}}
          }}
        </script>
        """

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>Hợp đồng vận chuyển - {ctx['contract_no']}</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 7mm 15mm;
  }}
  * {{
    box-sizing: border-box;
  }}
  html, body {{
    font-family: "Times New Roman", Times, serif;
    font-size: 11.5pt;
    line-height: 1.28;
    color: #000;
    margin: 0;
    padding: 0;
    background: #f1f5f9;
  }}
  .contract-container {{
    max-width: 210mm;
    margin: 0 auto;
    background: #fff;
  }}
  .page {{
    padding: 16mm 18mm;
    background: #fff;
    min-height: 297mm;
    box-shadow: 0 4px 14px rgba(0,0,0,0.06);
    margin-bottom: 20px;
    page-break-after: always;
  }}
  .page:last-child {{
    line-height: 1.16;
    font-size: 10.5pt;
  }}
  @media print {{
    html, body {{
      background: #fff !important;
    }}
    .no-print {{
      display: none !important;
    }}
    .contract-container {{
      max-width: 100% !important;
      margin: 0 !important;
      box-shadow: none !important;
    }}
    .page {{
      padding: 0 !important;
      min-height: auto !important;
      box-shadow: none !important;
      margin: 0 !important;
      page-break-after: always !important;
      break-after: page !important;
    }}
    .page:last-child {{
      page-break-after: auto !important;
      break-after: auto !important;
    }}
    .sig-table {{
      page-break-inside: avoid !important;
      break-inside: avoid !important;
    }}
  }}
  .text-center {{ text-align: center; }}
  .bold {{ font-weight: bold; }}
  .italic {{ font-style: italic; }}
  .underline {{ text-decoration: underline; }}
  .red-text {{ color: #d60000; }}
  
  .header-quoc-hieu {{
    font-size: 12pt;
    line-height: 1.25;
    margin-bottom: 4px;
  }}
  .doc-title {{
    font-size: 15pt;
    letter-spacing: 0.5px;
    margin-top: 2px;
    margin-bottom: 2px;
  }}
  .contract-no {{
    font-size: 11.5pt;
    margin-bottom: 4px;
  }}
  .can-cu {{
    font-style: italic;
    font-size: 11pt;
    margin-bottom: 2px;
    padding-left: 15px;
    text-indent: -15px;
  }}
  .intro-date {{
    margin-top: 3px;
    margin-bottom: 3px;
    text-align: justify;
  }}
  .party-table {{
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    margin-bottom: 3px;
  }}
  .party-table td {{
    padding: 1px 0;
    vertical-align: top;
    font-size: 11.5pt;
  }}
  .party-header {{
    font-weight: bold;
    padding-bottom: 3px !important;
  }}
  .party-header .company-name {{
    font-size: 13pt;
  }}
  .col-label {{
    width: 95px;
    white-space: nowrap;
  }}
  .col-mid {{
    width: 48%;
  }}
  .col-right {{
    width: 38%;
  }}
  .acc-num {{
    display: inline-block;
    min-width: 130px;
    margin-right: 15px;
    vertical-align: baseline;
  }}
  .acc-bank {{
    white-space: nowrap;
  }}
  .section-title {{
    font-weight: bold;
    font-size: 13pt;
    margin-top: 7px;
    margin-bottom: 2px;
  }}
  .article-title {{
    font-weight: bold;
    text-decoration: underline;
    margin-top: 8px;
    margin-bottom: 2px;
  }}
  .section-title + .article-title {{
    margin-top: 5px;
  }}
  p {{
    margin: 2px 0;
    text-align: justify;
  }}
  .bullet-item {{
    padding-left: 18px;
    text-indent: -18px;
    margin: 1px 0;
    text-align: justify;
  }}
  .sig-table {{
    width: 100%;
    margin-top: 0;
    border-collapse: collapse;
    page-break-inside: avoid;
    break-inside: avoid;
  }}
  .sig-header {{
    width: 50%;
    text-align: center;
    vertical-align: top;
    padding-bottom: 0;
  }}
  .sig-slot {{
    width: 50%;
    height: 55mm;
    min-height: 55mm;
    text-align: center;
    vertical-align: middle;
    padding: 0;
  }}
  .sig-slot-inner {{
    height: 55mm;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .sig-slot-inner img {{
    max-height: 58mm;
    max-width: none;
    object-fit: contain;
  }}
  .sig-name {{
    width: 50%;
    text-align: center;
    vertical-align: top;
    padding-top: 2px;
  }}
</style>
</head>
<body>

{top_bar}

<div class="contract-container">
  <!-- TRANG 1 -->
  <div class="page">
    <div class="header-quoc-hieu text-center">
      <div class="bold">CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</div>
      <div class="bold">Độc lập - Tự do - Hạnh phúc</div>
      <div>----------o0o----------</div>
    </div>

    <div class="text-center">
      <div class="doc-title bold">HỢP ĐỒNG VẬN CHUYỂN</div>
      <div class="contract-no italic red-text">Số : {ctx['contract_no']}</div>
    </div>

    <div class="can-cu">- &nbsp; Căn cứ Bộ luật Dân sự số 91/2015/QH13 ngày 24/11/2015;</div>
    <div class="can-cu">- &nbsp; Căn cứ Luật Thương mại số 36/2005/QH11 ngày 14/06/2005;</div>
    <div class="can-cu">- &nbsp; Căn cứ vào nhu cầu và khả năng của hai bên.</div>

    <div class="intro-date">
      Hôm nay, ngày {ctx['day_str']} tháng {ctx['month_str']} năm {ctx['year_str']} ,tại văn phòng {escape(ctx['seller_name'])}, chúng tôi đại diện cho các bên tham gia ký kết hợp đồng gồm:
    </div>

    <!-- BÊN A -->
    <table class="party-table">
      <colgroup><col style="width: 95px;"><col style="width: 40%;"><col></colgroup>
      <tr>
        <td colspan="3" class="party-header">
          Bên thuê vận chuyển(Bên A): &nbsp; <span class="bold company-name">{ctx['customer_name']}</span>
        </td>
      </tr>
      <tr>
        <td class="col-label">Đại diện</td>
        <td class="col-mid">: {customer_rep_html}</td>
        <td class="col-right">Chức vụ: <span class="bold">{escape(ctx.get('customer_pos') or 'Giám đốc')}</span></td>
      </tr>
      <tr>
        <td class="col-label">Địa chỉ</td>
        <td colspan="2">: {ctx['customer_addr']}</td>
      </tr>
      <tr>
        <td class="col-label">Điện thoại</td>
        <td colspan="2">: {ctx['customer_phone']}</td>
      </tr>
      <tr>
        <td class="col-label">Mã số thuế</td>
        <td colspan="2">: {ctx['customer_tax']}</td>
      </tr>
      {customer_bank_rows}
    </table>

    <!-- BÊN B -->
    <table class="party-table">
      <colgroup><col style="width: 95px;"><col style="width: 40%;"><col></colgroup>
      <tr>
        <td colspan="3" class="party-header">
          Bên vận chuyển(Bên B): &nbsp; <span class="bold company-name">{ctx['seller_name']}</span>
        </td>
      </tr>
      <tr>
        <td class="col-label">Đại diện</td>
        <td class="col-mid">: {seller_rep_html}</td>
        <td class="col-right">Chức vụ: <span class="bold">{escape(ctx.get('seller_pos') or 'Giám đốc')}</span></td>
      </tr>
      <tr>
        <td class="col-label">Địa chỉ</td>
        <td colspan="2">: {ctx['seller_addr']}</td>
      </tr>
      <tr>
        <td class="col-label">Điện thoại</td>
        <td colspan="2">: {ctx['seller_phone']}</td>
      </tr>
      <tr>
        <td class="col-label">Mã số thuế</td>
        <td colspan="2">: {ctx['seller_tax']}</td>
      </tr>
      {seller_bank_rows}
      <tr>
        <td class="col-label">Email</td>
        <td colspan="2">: {ctx['seller_email']}</td>
      </tr>
    </table>

    <p class="italic">Sau khi thương thảo, bàn bạc, hai bên thống nhất ký kết hợp đồng vận chuyển với các điều khoản sau:</p>
    
    <div class="section-title">NỘI DUNG CÔNG VIỆC GIAO DỊCH :</div>
    <div class="article-title">Điều 1: NỘI DUNG CỦA HỢP ĐỒNG</div>
    <p>Bên A có nhu cầu thuê vận chuyển và bên B đồng ý nhận vận chuyển hàng cho bên A tới các công trình theo thỏa thuận hai bên.</p>

    <div class="article-title">Điều 2: ĐƠN GIÁ, KHỐI LƯỢNG, ĐỊA ĐIỂM GIAO NHẬN HÀNG HÓA</div>
    <p>2.1. Đơn giá áp dụng theo từng chuyến do hai bên thỏa thuận theo từng thời điểm căn cứ theo báo giá, tin nhắn hoặc văn bản xác nhận giữa hai bên. Đơn giá trên đã bao gồm thuế giá trị gia tăng (VAT), trừ trường hợp hai bên có thỏa thuận khác.</p>
    <p>2.2. Khối lượng hàng hoá chở tối thiểu trên một chuyến theo định mức của bên vận chuyển.</p>
    <p>2.3 Địa điểm giao nhận hàng hóa cụ thể từng chuyến sẽ thông báo theo yêu cầu của bên A. Bên A có trách nhiệm thông báo trước.</p>
    <p>2.4. Phí chờ, lưu xe và hủy chuyến: Trường hợp xe của Bên B đã đến đúng giờ và địa điểm theo thông báo nhưng Bên A bốc xếp chậm quá 02 giờ hoặc Bên A hủy chuyến, Bên A có trách nhiệm thanh toán chi phí chờ xe, lưu ca hoặc phí hủy chuyến theo thỏa thuận thực tế của hai bên.</p>

    <div class="article-title">Điều 3: PHƯƠNG THỨC THANH TOÁN</div>
    <p>3.1. Thanh toán bằng chuyển khoản hoặc bù trừ công nợ (có biên bản bù trừ). Tiền mặt chỉ áp dụng cho khoản dưới 5.000.000 đồng.</p>
    <p>3.2. Giá trị thanh toán căn cứ theo số chuyến thực tế và đơn giá thỏa thuận tại mục 2.1.</p>
    <p>3.3. Sau khi hoàn thành vận chuyển từng chuyến, hai bên tiến hành đối chiếu khối lượng hàng hóa và bên B xuất hóa đơn GTGT theo từng chuyến. Chậm nhất ngày 10 hàng tháng (hoặc trong vòng 30 ngày làm việc kể từ ngày nhận đầy đủ hóa đơn GTGT và chứng từ hợp lệ), bên A có trách nhiệm thanh toán 100% cước vận chuyển cho bên B.</p>
  </div>

  <!-- TRANG 2 -->
  <div class="page">
    <p>3.4. Nếu bên A chậm thanh toán, bên B có quyền tạm dừng vận chuyển. Bên A phải trả thêm lãi trên số tiền chậm trả, theo lãi suất nợ quá hạn trung bình trên thị trường tại thời điểm thanh toán.</p>

    <div class="article-title">Điều 4: TRÁCH NHIỆM MỖI BÊN</div>
    <p class="bold">4.1. Bên A:</p>
    <div class="bullet-item">- &nbsp; Thông báo lịch vận chuyển ít nhất là 06 tiếng để bên B bố trí phương tiện (trong trường hợp phát sinh 2 bên sẽ thỏa thuận tạo điều kiện hỗ trợ).</div>
    <div class="bullet-item">- &nbsp; Cung cấp cho bên B các hồ sơ chứng từ cần thiết cho việc vận chuyển hàng hóa.</div>
    <div class="bullet-item">- &nbsp; Chịu trách nhiệm dọn bãi cho xe bên B vào cẩu hàng.</div>
    <div class="bullet-item">- &nbsp; Chịu trách nhiệm hoàn toàn trước pháp luật về tính hợp pháp, hóa đơn chứng từ nguồn gốc hàng hóa và không yêu cầu bên B chở hàng quá khổ, quá tải trọng theo quy định.</div>
    <div class="bullet-item">- &nbsp; Đảm bảo chất lượng, quy cách đóng gói và tiến độ sản phẩm trước khi bốc xếp vận chuyển.</div>
    <div class="bullet-item">- &nbsp; Bố trí người nhận hàng tại công trình, tạo điều kiện cho Bên B trả hàng nhanh chóng.</div>
    <div class="bullet-item">- &nbsp; Thanh toán cho bên B theo đúng điều 3 của hợp đồng.</div>

    <p class="bold" style="margin-top: 6px;">4.2. Bên B:</p>
    <div class="bullet-item">- &nbsp; Bố trí phương tiện vận chuyển không làm lỡ kế hoạch của bên A.</div>
    <div class="bullet-item">- &nbsp; Đảm bảo người vận hành các xe, thiết bị phục vụ nhu cầu vận chuyển của bên A phải xuất trình các giấy tờ cần thiết khi bị các ban ngành Nhà nước kiểm tra.</div>
    <div class="bullet-item">- &nbsp; Có trách nhiệm kiểm tra và giữ nguyên số lượng, tình trạng hàng hóa như lúc nhận trong suốt quá trình vận chuyển.</div>
    <div class="bullet-item">- &nbsp; Trường hợp hàng bị hư hỏng phải có biên bản hư hỏng hàng hóa có đầy đủ xác nhận của 2 bên tại hiện trường và phiếu nhập kho do bên A cung cấp.</div>
    <div class="bullet-item">- &nbsp; Trong quá trình vận chuyển nếu do lỗi trực tiếp của bên B làm sản phẩm của bên A bị lỗi (nứt, sứt mẻ, gãy…) làm cho khách hàng của bên A từ chối nhận hàng thì bên B phải chịu trách nhiệm đền bù về chi phí trực tiếp của các lỗi đó; loại trừ các hư hại do bên A đóng gói, xếp hàng hoặc do sự kiện bất khả kháng.</div>
    <div class="bullet-item">- &nbsp; Đảm bảo khi giao hàng tại công trình của bên A phải có chữ ký xác nhận của cán bộ được ủy quyền của bên nhận hàng.</div>
    <div class="bullet-item">- &nbsp; Cấp hóa đơn GTGT hợp pháp cho bên A.</div>

    <div class="article-title">Điều 5: ĐIỀU KHOẢN CHUNG</div>
    <div class="bullet-item">- &nbsp; Hai bên cam kết thực hiện đúng những điều khoản ghi trong hợp đồng. Nếu bên nào vi phạm hợp đồng mà không thuộc trường hợp bất khả kháng sẽ phải chịu phạt vi phạm với mức tối đa 8% giá trị phần nghĩa vụ bị vi phạm theo quy định của Luật Thương mại, đồng thời bồi thường thiệt hại thực tế phát sinh cho bên kia.</div>
    <div class="bullet-item">- &nbsp; Sự kiện bất khả kháng: Trường hợp xảy ra các sự kiện bất khả kháng (thiên tai, bão lũ, dịch bệnh, cấm đường theo quyết định của cơ quan nhà nước, tai nạn giao thông không do lỗi của bên vận chuyển...) dẫn đến chậm trễ hoặc không thể thực hiện chuyến hàng, bên gặp sự kiện được miễn trừ trách nhiệm nhưng phải kịp thời thông báo cho bên kia để cùng phối hợp giải quyết.</div>
    <div class="bullet-item">- &nbsp; Các thay đổi khác nếu có ngoài nội dung hợp đồng này, hai bên thông báo cho nhau bằng văn bản và thỏa thuận để làm phụ lục và bổ sung hợp đồng (phụ lục hợp đồng là một phần nội dung của hợp đồng này).</div>
    <div class="bullet-item">- &nbsp; Quá trình thực hiện nếu có gì vướng mắc hai bên cùng bàn bạc giải quyết thông qua thương lượng trên tinh thần hợp tác và bình đẳng. Nếu hai bên không giải quyết được bằng thương lượng hòa giải thì một trong các bên sẽ yêu cầu Tòa án có thẩm quyền tại Tỉnh Phú Thọ giải quyết theo quy định của pháp luật hiện hành.</div>

    <div class="article-title">Điều 6: HIỆU LỰC CỦA HỢP ĐỒNG</div>
    <div class="bullet-item">- &nbsp; Hợp đồng này có hiệu lực kể từ ngày ký đến hết ngày {ctx['valid_until']}. Khi hết hạn, nếu hai bên không có văn bản thông báo chấm dứt trước 30 ngày thì hợp đồng tự động gia hạn thêm từng năm (01 năm).</div>
    <div class="bullet-item">- &nbsp; Khi hợp đồng chấm dứt hoặc hết hạn, các bên vẫn có nghĩa vụ thực hiện đầy đủ các quyền và nghĩa vụ công nợ, thanh toán còn lại cho đến khi hoàn tất 100%.</div>
    <div class="bullet-item">- &nbsp; Hợp đồng này được lập thành 02 bản, mỗi bên giữ 01 bản có giá trị pháp lý như nhau.</div>

    <table class="sig-table">
      <tr>
        <td class="sig-header">
          <div class="bold" style="font-size: 12pt;">ĐẠI DIỆN BÊN A</div>
          <div class="italic" style="font-size: 11pt; margin-top: 3px;">(Ký, đóng dấu)</div>
        </td>
        <td class="sig-header">
          <div class="bold" style="font-size: 12pt;">ĐẠI DIỆN BÊN B</div>
          <div class="italic" style="font-size: 11pt; margin-top: 3px;">(Ký, đóng dấu)</div>
        </td>
      </tr>
      <tr>
        <td class="sig-slot">
          {customer_sig_html}
        </td>
        <td class="sig-slot">
          {seller_sig_html}
        </td>
      </tr>
    </table>
  </div>
</div>

</body>
</html>
"""


def _find_browser_exe() -> str | None:
    """Find a Chromium-based browser to print PDF."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _stop_chrome_and_clean(proc: subprocess.Popen, tmp_dir: Path) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    shutil.rmtree(tmp_dir, ignore_errors=True)


def render_pdf_from_html(html_content: str, title: str = "Tài liệu") -> bytes:
    """Render PDF from HTML using headless Chrome/Edge, or PyMuPDF fallback."""
    browser_exe = _find_browser_exe()
    if browser_exe:
        tmp_dir = Path(tempfile.mkdtemp())
        html_file = tmp_dir / "doc.html"
        pdf_file = tmp_dir / "doc.pdf"
        html_file.write_text(html_content, encoding="utf-8")

        cmd = [
            browser_exe,
            "--headless=new",
            "--disable-gpu",
            f"--user-data-dir={tmp_dir / 'profile'}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_file}",
            str(html_file),
        ]
        # Chrome 156 ghi xong PDF nhưng không tự thoát: chờ file PDF đứng kích thước rồi lấy file;
        # tắt Chrome (máy bận mất vài chục giây) và xóa thư mục tạm chạy ở nền.
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 25
        last_size = -1
        while proc.poll() is None and time.monotonic() < deadline:
            size = pdf_file.stat().st_size if pdf_file.exists() else -1
            if size > 1000 and size == last_size:
                break
            last_size = size
            time.sleep(0.5)
        pdf_bytes = pdf_file.read_bytes() if pdf_file.exists() and pdf_file.stat().st_size > 1000 else b""
        threading.Thread(target=_stop_chrome_and_clean, args=(proc, tmp_dir), daemon=True).start()
        if pdf_bytes:
            return pdf_bytes

    # PyMuPDF Fallback: convert HTML using basic PyMuPDF story / rect
    if fitz is not None:
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), f"{title}\nVui lòng mở trên trình duyệt có Chrome/Edge để in PDF chuẩn.", fontsize=12)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    return b""


def render_contract_pdf(ctx: dict[str, Any]) -> bytes:
    """Render PDF using headless Chrome/Edge, or PyMuPDF fallback."""
    html_content = render_contract_html(ctx, preview=False)
    return render_pdf_from_html(html_content, f"HỢP ĐỒNG VẬN CHUYỂN\nSố: {ctx.get('contract_no', '')}")


def render_contract_docx(ctx: dict[str, Any]) -> bytes:
    """Export the contract as a standard Microsoft Word (.docx) document."""
    doc = Document()

    # Set page margins: 16mm top/bottom, 18mm left/right
    for section in doc.sections:
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    # Style: Normal
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11.5)
    style.font.color.rgb = RGBColor(0, 0, 0)

    # Quốc hiệu
    p_qh = doc.add_paragraph()
    p_qh.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_qh = p_qh.add_run("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n")
    r_qh.bold = True
    r_qh.font.size = Pt(12)
    r_tn = p_qh.add_run("Độc lập - Tự do - Hạnh phúc\n")
    r_tn.bold = True
    r_tn.font.size = Pt(12)
    r_o0o = p_qh.add_run("----------o0o----------")
    r_o0o.font.size = Pt(11)

    # Tiêu đề
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_t = p_title.add_run("\nHỢP ĐỒNG VẬN CHUYỂN\n")
    r_t.bold = True
    r_t.font.size = Pt(15)
    r_no = p_title.add_run(f"Số : {ctx['contract_no']}")
    r_no.italic = True
    r_no.font.color.rgb = RGBColor(214, 0, 0)

    # Căn cứ
    can_cus = [
        "Căn cứ Bộ luật Dân sự số 91/2015/QH13 ngày 24/11/2015;",
        "Căn cứ Luật Thương mại số 36/2005/QH11 ngày 14/06/2005;",
        "Căn cứ vào nhu cầu và khả năng của hai bên.",
    ]
    for cc in can_cus:
        p_cc = doc.add_paragraph(f"-   {cc}")
        p_cc.paragraph_format.left_indent = Inches(0.2)
        p_cc.runs[0].italic = True
        p_cc.runs[0].font.size = Pt(11)

    p_intro = doc.add_paragraph(
        f"Hôm nay, ngày {ctx['day_str']} tháng {ctx['month_str']} năm {ctx['year_str']} ,tại văn phòng {ctx['seller_name']}, chúng tôi đại diện cho các bên tham gia ký kết hợp đồng gồm:"
    )
    p_intro.paragraph_format.space_before = Pt(6)

    # Helper for party table
    def add_party_section(title, name, rep, pos, addr, phone, tax, acc, bank, email=None, bank_accounts=None, gender=None):
        p_p = doc.add_paragraph()
        p_p.paragraph_format.space_before = Pt(5)
        p_p.paragraph_format.space_after = Pt(2)
        r_pt = p_p.add_run(f"{title}:  ")
        r_pt.bold = True
        r_pn = p_p.add_run(name)
        r_pn.bold = True
        r_pn.font.size = Pt(13)

        tbl = doc.add_table(rows=0, cols=3)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.autofit = False

        prefix, rep_name = split_rep_honorific(rep, gender)
        pos_str = (pos or "Giám đốc").strip()

        # 1. Đại diện & Chức vụ
        row_rep = tbl.add_row()
        c0, c1, c2 = row_rep.cells
        c0.width = Inches(1.1)
        c1.width = Inches(3.2)
        c2.width = Inches(2.7)

        p0 = c0.paragraphs[0]
        r0 = p0.add_run("Đại diện")
        r0.font.size = Pt(11.5)

        p1 = c1.paragraphs[0]
        if rep_name:
            r1_pre = p1.add_run(f": {prefix}")
            r1_pre.font.size = Pt(11.5)
            r1_name = p1.add_run(rep_name.upper())
            r1_name.bold = True
            r1_name.font.size = Pt(11.5)
        else:
            r1 = p1.add_run(": Ông/Bà .....................................")
            r1.font.size = Pt(11.5)

        p2 = c2.paragraphs[0]
        r2_lbl = p2.add_run("Chức vụ: ")
        r2_lbl.font.size = Pt(11.5)
        r2_pos = p2.add_run(pos_str)
        r2_pos.bold = True
        r2_pos.font.size = Pt(11.5)

        # Helper cho hàng gộp cột 2 và 3 (Địa chỉ, Điện thoại, MST, Email)
        def add_info_row(label: str, val: str):
            row = tbl.add_row()
            cells = row.cells
            cells[0].width = Inches(1.1)
            p_lbl = cells[0].paragraphs[0]
            r_lbl = p_lbl.add_run(label)
            r_lbl.font.size = Pt(11.5)

            merged = cells[1].merge(cells[2])
            merged.width = Inches(5.9)
            p_val = merged.paragraphs[0]
            r_val = p_val.add_run(f": {val}")
            r_val.font.size = Pt(11.5)

        add_info_row("Địa chỉ", addr)
        add_info_row("Điện thoại", phone)
        add_info_row("Mã số thuế", tax)

        acc_list = bank_accounts or [{"account_number": acc, "bank_name": bank, "branch": ""}]
        for idx, account in enumerate(acc_list):
            row_bank = tbl.add_row()
            c0, c1, c2 = row_bank.cells
            c0.width = Inches(1.1)
            c1.width = Inches(3.2)
            c2.width = Inches(2.7)

            p0 = c0.paragraphs[0]
            if idx == 0:
                r0 = p0.add_run("Tài khoản")
                r0.font.size = Pt(11.5)

            acc_num = account.get('account_number') or ''
            prefix_acc = ": " if idx == 0 else "  "
            bank_text = _contract_bank_text(account)

            r_m = c1.paragraphs[0].add_run(f"{prefix_acc}{acc_num}")
            r_m.font.size = Pt(11.5)
            if bank_text:
                r_b = c2.paragraphs[0].add_run(bank_text)
                r_b.font.size = Pt(11.5)

        if email:
            add_info_row("Email", email)

        for row in tbl.rows:
            for cell in row.cells:
                cell.paragraphs[0].paragraph_format.space_after = Pt(1.5)
                cell.paragraphs[0].paragraph_format.space_before = Pt(0)
                cell.paragraphs[0].paragraph_format.line_spacing = 1.15

    add_party_section(
        "Bên thuê vận chuyển(Bên A)",
        ctx["customer_name"],
        ctx["customer_rep"],
        ctx["customer_pos"],
        ctx["customer_addr"],
        ctx["customer_phone"],
        ctx["customer_tax"],
        ctx["customer_bank_acc"],
        ctx["customer_bank_name"],
        bank_accounts=ctx.get("customer_bank_accounts"),
        gender=ctx.get("customer_gender"),
    )

    doc.add_paragraph().paragraph_format.space_after = Pt(2)

    add_party_section(
        "Bên vận chuyển(Bên B)",
        ctx["seller_name"],
        ctx["seller_rep"],
        ctx["seller_pos"],
        ctx["seller_addr"],
        ctx["seller_phone"],
        ctx["seller_tax"],
        ctx["seller_bank_acc"],
        ctx["seller_bank_name"],
        email=ctx["seller_email"],
        bank_accounts=ctx.get("seller_bank_accounts"),
        gender=ctx.get("seller_gender"),
    )

    p_mid = doc.add_paragraph("Sau khi thương thảo, bàn bạc, hai bên thống nhất ký kết hợp đồng vận chuyển với các điều khoản sau:")
    p_mid.runs[0].italic = True
    p_mid.paragraph_format.space_before = Pt(6)

    def add_sec_title(txt, underline=True, space_before=14, font_size=None):
        p = doc.add_paragraph()
        r = p.add_run(txt)
        r.bold = True
        if underline:
            r.underline = True
        if font_size:
            r.font.size = Pt(font_size)
        p.paragraph_format.space_before = Pt(space_before)
        p.paragraph_format.space_after = Pt(2)

    add_sec_title("NỘI DUNG CÔNG VIỆC GIAO DỊCH :", underline=False, space_before=7, font_size=13)
    add_sec_title("Điều 1: NỘI DUNG CỦA HỢP ĐỒNG", space_before=5)
    doc.add_paragraph("Bên A có nhu cầu thuê vận chuyển và bên B đồng ý nhận vận chuyển hàng cho bên A tới các công trình theo thỏa thuận hai bên.")

    add_sec_title("Điều 2: ĐƠN GIÁ, KHỐI LƯỢNG, ĐỊA ĐIỂM GIAO NHẬN HÀNG HÓA")
    doc.add_paragraph("2.1. Đơn giá áp dụng theo từng chuyến do hai bên thỏa thuận theo từng thời điểm căn cứ theo báo giá, tin nhắn hoặc văn bản xác nhận giữa hai bên. Đơn giá trên đã bao gồm thuế giá trị gia tăng (VAT), trừ trường hợp hai bên có thỏa thuận khác.")
    doc.add_paragraph("2.2. Khối lượng hàng hoá chở tối thiểu trên một chuyến theo định mức của bên vận chuyển.")
    doc.add_paragraph("2.3 Địa điểm giao nhận hàng hóa cụ thể từng chuyến sẽ thông báo theo yêu cầu của bên A. Bên A có trách nhiệm thông báo trước.")
    doc.add_paragraph("2.4. Phí chờ, lưu xe và hủy chuyến: Trường hợp xe của Bên B đã đến đúng giờ và địa điểm theo thông báo nhưng Bên A bốc xếp chậm quá 02 giờ hoặc Bên A hủy chuyến, Bên A có trách nhiệm thanh toán chi phí chờ xe, lưu ca hoặc phí hủy chuyến theo thỏa thuận thực tế của hai bên.")

    add_sec_title("Điều 3: PHƯƠNG THỨC THANH TOÁN")
    doc.add_paragraph("3.1. Thanh toán bằng chuyển khoản hoặc bù trừ công nợ (có biên bản bù trừ). Tiền mặt chỉ áp dụng cho khoản dưới 5.000.000 đồng.")
    doc.add_paragraph("3.2. Giá trị thanh toán căn cứ theo số chuyến thực tế và đơn giá thỏa thuận tại mục 2.1.")
    doc.add_paragraph("3.3. Sau khi hoàn thành vận chuyển từng chuyến, hai bên tiến hành đối chiếu khối lượng hàng hóa và bên B xuất hóa đơn GTGT theo từng chuyến. Chậm nhất ngày 10 hàng tháng (hoặc trong vòng 30 ngày làm việc kể từ ngày nhận đầy đủ hóa đơn GTGT và chứng từ hợp lệ), bên A có trách nhiệm thanh toán 100% cước vận chuyển cho bên B.")

    # Trang 2
    doc.add_page_break()

    doc.add_paragraph("3.4. Nếu bên A chậm thanh toán, bên B có quyền tạm dừng vận chuyển. Bên A phải trả thêm lãi trên số tiền chậm trả, theo lãi suất nợ quá hạn trung bình trên thị trường tại thời điểm thanh toán.")

    add_sec_title("Điều 4: TRÁCH NHIỆM MỖI BÊN")
    p_41 = doc.add_paragraph()
    r_41 = p_41.add_run("4.1. Bên A:")
    r_41.bold = True

    items_41 = [
        "Thông báo lịch vận chuyển ít nhất là 06 tiếng để bên B bố trí phương tiện (trong trường hợp phát sinh 2 bên sẽ thỏa thuận tạo điều kiện hỗ trợ).",
        "Cung cấp cho bên B các hồ sơ chứng từ cần thiết cho việc vận chuyển hàng hóa.",
        "Chịu trách nhiệm dọn bãi cho xe bên B vào cẩu hàng.",
        "Chịu trách nhiệm hoàn toàn trước pháp luật về tính hợp pháp, hóa đơn chứng từ nguồn gốc hàng hóa và không yêu cầu bên B chở hàng quá khổ, quá tải trọng theo quy định.",
        "Đảm bảo chất lượng, quy cách đóng gói và tiến độ sản phẩm trước khi bốc xếp vận chuyển.",
        "Bố trí người nhận hàng tại công trình, tạo điều kiện cho Bên B trả hàng nhanh chóng.",
        "Thanh toán cho bên B theo đúng điều 3 của hợp đồng.",
    ]
    for it in items_41:
        p_it = doc.add_paragraph(f"-   {it}")
        p_it.paragraph_format.left_indent = Inches(0.2)
        p_it.paragraph_format.space_after = Pt(2)

    p_42 = doc.add_paragraph()
    r_42 = p_42.add_run("4.2. Bên B:")
    r_42.bold = True

    items_42 = [
        "Bố trí phương tiện vận chuyển không làm lỡ kế hoạch của bên A.",
        "Đảm bảo người vận hành các xe, thiết bị phục vụ nhu cầu vận chuyển của bên A phải xuất trình các giấy tờ cần thiết khi bị các ban ngành Nhà nước kiểm tra.",
        "Có trách nhiệm kiểm tra và giữ nguyên số lượng, tình trạng hàng hóa như lúc nhận trong suốt quá trình vận chuyển.",
        "Trường hợp hàng bị hư hỏng phải có biên bản hư hỏng hàng hóa có đầy đủ xác nhận của 2 bên tại hiện trường và phiếu nhập kho do bên A cung cấp.",
        "Trong quá trình vận chuyển nếu do lỗi trực tiếp của bên B làm sản phẩm của bên A bị lỗi (nứt, sứt mẻ, gãy…) làm cho khách hàng của bên A từ chối nhận hàng thì bên B phải chịu trách nhiệm đền bù về chi phí trực tiếp của các lỗi đó; loại trừ các hư hại do bên A đóng gói, xếp hàng hoặc do sự kiện bất khả kháng.",
        "Đảm bảo khi giao hàng tại công trình của bên A phải có chữ ký xác nhận của cán bộ được ủy quyền của bên nhận hàng.",
        "Cấp hóa đơn GTGT hợp pháp cho bên A.",
    ]
    for it in items_42:
        p_it = doc.add_paragraph(f"-   {it}")
        p_it.paragraph_format.left_indent = Inches(0.2)
        p_it.paragraph_format.space_after = Pt(2)

    add_sec_title("Điều 5: ĐIỀU KHOẢN CHUNG")
    items_d5 = [
        "Hai bên cam kết thực hiện đúng những điều khoản ghi trong hợp đồng. Nếu bên nào vi phạm hợp đồng mà không thuộc trường hợp bất khả kháng sẽ phải chịu phạt vi phạm với mức tối đa 8% giá trị phần nghĩa vụ bị vi phạm theo quy định của Luật Thương mại, đồng thời bồi thường thiệt hại thực tế phát sinh cho bên kia.",
        "Sự kiện bất khả kháng: Trường hợp xảy ra các sự kiện bất khả kháng (thiên tai, bão lũ, dịch bệnh, cấm đường theo quyết định của cơ quan nhà nước, tai nạn giao thông không do lỗi của bên vận chuyển...) dẫn đến chậm trễ hoặc không thể thực hiện chuyến hàng, bên gặp sự kiện được miễn trừ trách nhiệm nhưng phải kịp thời thông báo cho bên kia để cùng phối hợp giải quyết.",
        "Các thay đổi khác nếu có ngoài nội dung hợp đồng này, hai bên thông báo cho nhau bằng văn bản và thỏa thuận để làm phụ lục và bổ sung hợp đồng (phụ lục hợp đồng là một phần nội dung của hợp đồng này).",
        "Quá trình thực hiện nếu có gì vướng mắc hai bên cùng bàn bạc giải quyết thông qua thương lượng trên tinh thần hợp tác và bình đẳng. Nếu hai bên không giải quyết được bằng thương lượng hòa giải thì một trong các bên sẽ yêu cầu Tòa án có thẩm quyền tại Tỉnh Phú Thọ giải quyết theo quy định của pháp luật hiện hành.",
    ]
    for it in items_d5:
        p_it = doc.add_paragraph(f"-   {it}")
        p_it.paragraph_format.left_indent = Inches(0.2)
        p_it.paragraph_format.space_after = Pt(2)

    add_sec_title("Điều 6: HIỆU LỰC CỦA HỢP ĐỒNG")
    items_d6 = [
        f"Hợp đồng này có hiệu lực kể từ ngày ký đến hết ngày {ctx['valid_until']}. Khi hết hạn, nếu hai bên không có văn bản thông báo chấm dứt trước 30 ngày thì hợp đồng tự động gia hạn thêm từng năm (01 năm).",
        "Khi hợp đồng chấm dứt hoặc hết hạn, các bên vẫn có nghĩa vụ thực hiện đầy đủ các quyền và nghĩa vụ công nợ, thanh toán còn lại cho đến khi hoàn tất 100%.",
        "Hợp đồng này được lập thành 02 bản, mỗi bên giữ 01 bản có giá trị pháp lý như nhau.",
    ]
    for it in items_d6:
        p_it = doc.add_paragraph(f"-   {it}")
        p_it.paragraph_format.left_indent = Inches(0.2)
        p_it.paragraph_format.space_after = Pt(2)

    # Chữ ký 2 bên (2 hàng: Tiêu đề -> Chỗ ký / con dấu)
    sig_tbl = doc.add_table(rows=2, cols=2)
    sig_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    sig_tbl.autofit = False
    for row in sig_tbl.rows:
        row.cells[0].width = Inches(3.5)
        row.cells[1].width = Inches(3.5)

    c_a0, c_b0 = sig_tbl.rows[0].cells
    p_sa = c_a0.paragraphs[0]
    p_sa.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sa.paragraph_format.space_before = Pt(0)
    p_sa.paragraph_format.space_after = Pt(2)
    r_sa1 = p_sa.add_run("ĐẠI DIỆN BÊN A\n")
    r_sa1.bold = True
    r_sa1.font.size = Pt(12)
    r_sa2 = p_sa.add_run("(Ký, đóng dấu)")
    r_sa2.italic = True
    r_sa2.font.size = Pt(11)

    p_sb = c_b0.paragraphs[0]
    p_sb.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sb.paragraph_format.space_before = Pt(0)
    p_sb.paragraph_format.space_after = Pt(2)
    r_sb1 = p_sb.add_run("ĐẠI DIỆN BÊN B\n")
    r_sb1.bold = True
    r_sb1.font.size = Pt(12)
    r_sb2 = p_sb.add_run("(Ký, đóng dấu)")
    r_sb2.italic = True
    r_sb2.font.size = Pt(11)

    # Hàng 2: Ảnh chữ ký (chỉ điền ảnh chữ ký, cân đối độ cao cả 2 bên)
    sig_tbl.rows[1].height = Pt(165)
    c_a1, c_b1 = sig_tbl.rows[1].cells
    c_a1.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    c_b1.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    def _set_docx_sig_cell(cell, sig_url: str):
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        valid_path = None
        if sig_url:
            clean_path = sig_url.split("?")[0].lstrip("/")
            test_p = Path(cfg.BASE_DIR) / clean_path
            if test_p.exists() and test_p.is_file():
                valid_path = test_p
        if valid_path:
            try:
                p.add_run().add_picture(
                    str(valid_path), width=Mm(_signature_print_width_mm(sig_url))
                )
                return
            except Exception:
                pass
        p.paragraph_format.line_spacing = Pt(20)
        p.add_run("\n\n\n")

    _set_docx_sig_cell(c_a1, ctx.get("customer_signature_url") or ctx.get("customer_stamp_url", ""))
    _set_docx_sig_cell(c_b1, ctx.get("seller_signature_url") or ctx.get("seller_stamp_url", ""))

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
