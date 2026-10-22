"""Generate transport contracts on demand without storing them."""

from __future__ import annotations

from datetime import date
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from backend.services.database import db
from frontend.deps import get_menu_context, require_menu, templates
from frontend.modules.invoices.companies import get_company, get_company_bank_accounts, list_companies
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
from frontend.modules.transport.trip_documents import (
    render_dlx_document_docx,
    render_dlx_document_html,
)

router = APIRouter(prefix="/transport")

CONTRACT_CATEGORIES = [
    {"code": "hdvc", "name": "Hợp đồng vận chuyển"},
    {"code": "hdnt", "name": "Hợp đồng nguyên tắc (HĐNT)"},
    {"code": "bbvc", "name": "Biên bản vận chuyển (BBVC)"},
    {"code": "bbtl", "name": "Biên bản nghiệm thu & thanh lý"},
    {"code": "dntt", "name": "Đề nghị thanh toán (ĐNTT)"},
    {"code": "bbbg", "name": "Biên bản bàn giao (BBBG)"},
]


def _get_contract_categories() -> list[dict[str, str]]:
    db_items = db._execute_query(
        "SELECT id, name FROM public.cargo_catalog_items WHERE kind = 'contract_category' ORDER BY id",
        fetchall=True, commit=False,
    ) or []

    code_by_name = {
        "hợp đồng vận chuyển": "hdvc",
        "hợp đồng nguyên tắc": "hdnt",
        "biên bản vận chuyển": "bbvc",
        "nghiệm thu": "bbtl",
        "thanh lý": "bbtl",
        "thanh toán": "dntt",
        "bàn giao": "bbbg",
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


@router.get("/contracts")
async def contracts_page(
    request: Request,
    company_id: Optional[int] = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    ctx = get_menu_context(request, user)
    ctx["current_menu"] = "vehicles"
    ctx["transport_active"] = "contracts"
    seller = _get_seller_company(request, user)
    from frontend.modules.transport.contract_import import derive_company_code
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


@router.get("/contracts/export")
async def export_contract(
    request: Request,
    company_id: int = Query(...),
    date_param: Optional[str] = Query(None, alias="date"),
    trip_id: Optional[int] = Query(None),
    contract_no: Optional[str] = Query(None),
    kind: str = Query("hdvc"),
    download: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    user: dict = Depends(require_menu("vehicles")),
):
    """Return a preview or download directly; never create a contract record."""
    seller = _get_seller_company(request, user)
    if not seller:
        raise HTTPException(
            status_code=400,
            detail="Chọn công ty đang làm việc ở thanh trên cùng trước khi xuất hợp đồng.",
        )

    company = get_company(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn vị khách hàng.")
    company["bank_accounts"] = get_company_bank_accounts(company_id)

    trip = None
    if trip_id:
        trip = db._execute_query(
            "SELECT * FROM cargo_trips WHERE id = %s", (trip_id,), fetch=True, commit=False,
        )
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
                headers={"Content-Disposition": f'attachment; filename="{prefix}_{safe_no}.pdf"'},
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
            headers={"Content-Disposition": f'attachment; filename="{prefix}_{safe_no}.docx"'},
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
                headers={"Content-Disposition": f'attachment; filename="{prefix}.docx"'},
            )

        html_for_pdf = render_dlx_document_html(inv_data, lines, company, seller, kind=kind, preview=False)
        pdf_bytes = render_pdf_from_html(html_for_pdf, title=f"{kind.upper()}_{inv_num_fmt}")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{kind.upper()}_{inv_num_fmt}.pdf"'},
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

    safe_no = re.sub(r"[^A-Za-z0-9_-]", "_", ctx["contract_no"])
    if req_format == "docx":
        return Response(
            content=render_contract_docx(ctx),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="Hop_dong_{safe_no}.docx"'},
        )

    return Response(
        content=render_contract_pdf(ctx),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{"attachment" if download == "pdf" else "inline"}; '
                                   f'filename="Hop_dong_{safe_no}.pdf"',
        },
    )

