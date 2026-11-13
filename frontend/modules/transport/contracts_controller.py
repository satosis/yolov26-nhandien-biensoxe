"""Generate transport contracts on demand without storing them."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any, Optional
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import backend.services.config as cfg

from frontend.deps import get_menu_context, templates
from frontend.modules.invoices.companies import get_company, get_company_bank_accounts, list_companies
from frontend.formatting.http_headers import content_disposition
from frontend.modules.transport.contract import (
    _remove_accents,
    get_contract_context,
    render_contract_docx,
    render_contract_html,
    render_contract_pdf,
    render_pdf_from_html,
)
from frontend.modules.transport.hdnt_bbbg import (
    render_bbbg_docx,
    render_hdnt_bbbg_combined_docx,
    render_hdnt_bbbg_html,
    render_hdnt_docx,
)
from frontend.modules.transport.quotation import (
    render_quotation_docx,
    render_quotation_html,
)
from frontend.modules.transport.trip_documents import (
    render_dlx_document_docx,
    render_dlx_document_html,
)
import frontend.modules.transport.contracts_queries as queries


DAI_LO_XANH_COMPANY_ID = 33

CONTRACT_CATEGORIES = [
    {"code": "hdvc", "name": "Hợp đồng vận chuyển"},
    {"code": "hdnt", "name": "Hợp đồng nguyên tắc (HĐNT)"},
    {"code": "bbvc", "name": "Biên bản vận chuyển (BBVC)"},
    {"code": "bbtl", "name": "Biên bản nghiệm thu & thanh lý"},
    {"code": "dntt", "name": "Đề nghị thanh toán (ĐNTT)"},
    {"code": "bbbg", "name": "Biên bản bàn giao (BBBG)"},
    {"code": "bgvc", "name": "Bảng báo giá vận chuyển"},
]


def _get_contract_categories() -> list[dict[str, str]]:
    db_items = queries.get_contract_categories_select_cargo_catalog_items() or []

    code_by_name = {
        "hợp đồng vận chuyển": "hdvc",
        "hợp đồng nguyên tắc": "hdnt",
        "biên bản vận chuyển": "bbvc",
        "nghiệm thu": "bbtl",
        "thanh lý": "bbtl",
        "thanh toán": "dntt",
        "bàn giao": "bbbg",
        "báo giá": "bgvc",
    }

    res: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for item in db_items:
        name = (item.get("name") or "").strip()
        lower_name = name.lower()
        matched_code = "hdvc"
        for key, code in code_by_name.items():
            if key in lower_name:
                matched_code = code
                break
        res.append({"code": matched_code, "name": name})
        seen_codes.add(matched_code)

    for default_cat in CONTRACT_CATEGORIES:
        if default_cat["code"] not in seen_codes:
            res.append(default_cat)
            seen_codes.add(default_cat["code"])

    return res


def _get_seller_company(request: Request, user: dict) -> dict[str, Any] | None:
    active_cid = get_menu_context(request, user).get("active_company_id")
    if not active_cid:
        return None
    company = get_company(int(active_cid))
    if not company:
        return None
    company["bank_accounts"] = get_company_bank_accounts(company["id"])
    return company


async def contracts_page(request: Request, company_id: Optional[int], user: dict):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    ctx["transport_active"] = "contracts"
    seller = _get_seller_company(request, user)
    from frontend.modules.transport.contract import derive_company_code
    seller_code = derive_company_code(seller["name"]) if (seller and seller.get("name")) else ""
    ctx.update({
        "seller": seller,
        "seller_code": seller_code,
        "seller_missing": seller is None,
        "companies": list_companies() or [],
        "selected_company_id": company_id,
        "today_str": date.today().isoformat(),
        "contract_categories": _get_contract_categories(),
    })
    return templates.TemplateResponse("pages/contracts.html", {"request": request, **ctx})


async def export_contract(
    request: Request,
    company_id: Optional[int],
    date_param: Optional[str],
    trip_id: Optional[int],
    contract_no: Optional[str],
    kind: str,
    download: Optional[str],
    format: Optional[str],
    view: Optional[str],
    user: dict,
):
    """Return a preview or download directly; never create a contract record."""
    seller = _get_seller_company(request, user)
    if not seller:
        raise HTTPException(
            status_code=400,
            detail="Chọn công ty đang làm việc ở thanh trên cùng trước khi xuất hợp đồng.",
        )

    if kind == "bgvc":
        seller = get_company(DAI_LO_XANH_COMPANY_ID) or seller
        quote_customer = None
        if company_id:
            quote_customer = get_company(company_id)
            if not quote_customer:
                raise HTTPException(status_code=404, detail="Không tìm thấy đơn vị khách hàng.")
        req_format = (download or format or "").lower()
        if view == "html" or req_format == "html" or (not download and not format):
            return HTMLResponse(render_quotation_html(quote_customer, seller, date_param, preview=True))
        if req_format == "pdf":
            pdf_bytes = render_pdf_from_html(
                render_quotation_html(quote_customer, seller, date_param, preview=False),
                title="Bang_bao_gia",
            )
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": content_disposition("Bang_bao_gia.pdf")},
            )
        return Response(
            content=render_quotation_docx(quote_customer, seller, date_param),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": content_disposition("Bang_bao_gia.docx")},
        )

    if not company_id:
        raise HTTPException(status_code=400, detail="Chọn đơn vị khách hàng.")
    company = get_company(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn vị khách hàng.")
    company["bank_accounts"] = get_company_bank_accounts(company_id)

    trip = None
    if trip_id:
        trip = queries.export_contract_select_cargo_trips(trip_id)
    target_date = date_param or (str(trip["invoice_date"]) if trip and trip.get("invoice_date") else None)
    req_format = (download or format or "").lower()

    # 1. Hợp đồng nguyên tắc / Biên bản bàn giao
    if kind in ("hdnt", "bbbg", "both"):
        trip_data = dict(trip) if trip else {
            "invoice_date": target_date,
            "contract_no": contract_no,
            "service_name": "Cước vận chuyển",
        }
        if contract_no:
            trip_data["contract_no"] = contract_no
        if target_date:
            trip_data["invoice_date"] = target_date

        if view == "html" or req_format == "html" or (not download and not format):
            return HTMLResponse(render_hdnt_bbbg_html(trip_data, company, seller, kind=kind, preview=True))

        c_no = contract_no or trip_data.get("contract_no") or "HDNT"
        prefix = {"hdnt": "HDNT", "bbbg": "BBBG", "both": "HDNT_BBBG"}.get(kind, "HDNT")
        safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", _remove_accents(c_no))

        if req_format == "pdf" or download == "pdf":
            html_for_pdf = render_hdnt_bbbg_html(trip_data, company, seller, kind=kind, preview=False)
            pdf_bytes = render_pdf_from_html(html_for_pdf, title=f"{prefix}_{safe_no}")
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": content_disposition(f"{prefix}_{safe_no}.pdf")},
            )

        renderer = {
            "hdnt": render_hdnt_docx,
            "bbbg": render_bbbg_docx,
            "both": render_hdnt_bbbg_combined_docx,
        }.get(kind, render_hdnt_docx)
        doc_bytes, _ = renderer(trip_data, company, seller)
        return Response(
            content=doc_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": content_disposition(f"{prefix}_{safe_no}.docx")},
        )

    # 2. Biên bản vận chuyển, Đề nghị thanh toán, Biên bản nghiệm thu & thanh lý
    if kind in ("bbvc", "dntt", "bbtl", "all"):
        inv_data = {
            "invoice_date": target_date or date.today().isoformat(),
            "contract_no": contract_no,
            "invoice_number": 1,
            "invoice_serial": "C26TAA",
        }
        lines: list[dict] = []
        if trip:
            inv_data["invoice_date"] = trip.get("invoice_date") or target_date

        if view == "html" or req_format == "html" or (not download and not format):
            return HTMLResponse(render_dlx_document_html(inv_data, lines, company, seller, kind=kind, preview=True))

        inv_num_fmt = f"{inv_data['invoice_number']:08d}"
        if req_format == "docx" or download == "docx":
            doc_bytes, prefix = render_dlx_document_docx(inv_data, lines, company, seller, kind=kind)
            return Response(
                content=doc_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": content_disposition(f"{prefix}.docx")},
            )

        html_for_pdf = render_dlx_document_html(inv_data, lines, company, seller, kind=kind, preview=False)
        pdf_bytes = render_pdf_from_html(html_for_pdf, title=f"{kind.upper()}_{inv_num_fmt}")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition(f"{kind.upper()}_{inv_num_fmt}.pdf")},
        )

    # 3. Mặc định: Hợp đồng vận chuyển
    ctx = get_contract_context(
        customer_or_trip=trip or company,
        customer_or_none=company if trip else None,
        seller=seller,
        contract_date=target_date,
        contract_no=contract_no,
    )

    if view == "html" or req_format == "html" or (not download and not format):
        return HTMLResponse(render_contract_html(ctx, preview=True))

    safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", _remove_accents(ctx["contract_no"]))
    if req_format == "docx":
        return Response(
            content=render_contract_docx(ctx),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": content_disposition(f"Hop_dong_{safe_no}.docx")},
        )

    return Response(
        content=render_contract_pdf(ctx),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{"attachment" if download == "pdf" else "inline"}; '
                                   f'filename="Hop_dong_{safe_no}.pdf"',
        },
    )


async def contract_zalo_groups(request: Request, company_id: Optional[int], user: dict):
    from frontend.modules.zalo.trip_requests import list_zalo_groups
    from frontend.modules.transport.trips_controller import _group_named_like
    try:
        groups = await list_zalo_groups()
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    customer = get_company(company_id) if company_id else None
    seller = _get_seller_company(request, user)
    suggested = (_group_named_like(groups, (customer or {}).get("name", ""))
                 or _group_named_like(groups, (seller or {}).get("name", "")))
    return JSONResponse({"ok": True, "groups": groups, "suggested_group_id": suggested})


async def send_contract_zalo(request: Request, user: dict):
    """Dựng file hợp đồng giống nút tải về rồi gửi vào nhóm Zalo đã chọn."""
    body = await request.json()
    group_id = str(body.get("group_id") or "").strip()
    group_name = str(body.get("group_name") or "").strip()
    if not group_id:
        return JSONResponse({"ok": False, "error": "Chọn nhóm Zalo nhận."}, status_code=400)
    fmt = "docx" if body.get("format") == "docx" else "pdf"
    company_id = int(body["customer_id"]) if body.get("customer_id") else None
    resp = await export_contract(
        request=request, company_id=company_id, date_param=body.get("date") or None, trip_id=None,
        contract_no=str(body.get("contract_no") or "").strip() or None, kind=body.get("kind") or "hdvc",
        download=fmt, format=fmt, view=None, user=user,
    )
    filename = re.search(r'filename="([^"]+)"', resp.headers["content-disposition"]).group(1)
    path = Path(cfg.DATA_DIR) / "contracts_zalo" / uuid.uuid4().hex / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.body)
    from frontend.modules.zalo.trip_requests import _bridge_post
    try:
        await _bridge_post("/send-invoice-file", {"group_id": group_id, "file_path": str(path.resolve())})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "message": f"Đã gửi vào nhóm {group_name}." if group_name else "Đã gửi vào nhóm Zalo."})
