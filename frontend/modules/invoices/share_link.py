"""Public link generation and routing for BKAV invoice PDFs."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from frontend.formatting.http_headers import content_disposition
from frontend.modules.invoices.bkav_sync import get_or_fetch_bkav_invoice_files
import frontend.modules.invoices.share_link_queries as queries

logger = logging.getLogger(__name__)


def _secret() -> bytes:
    secret = os.environ["SESSION_SECRET"]
    return f"{secret}:invoice-pdf".encode("utf-8")


def share_token(invoice_guid: str | UUID) -> str:
    guid = str(UUID(str(invoice_guid)))
    return hmac.new(_secret(), guid.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def verify_token(invoice_guid: str | UUID, token: str) -> bool:
    try:
        expected = share_token(invoice_guid)
    except (ValueError, TypeError, AttributeError):
        return False
    if not token or not isinstance(token, str):
        return False
    return hmac.compare_digest(expected, token)


def public_base_url(request: Request | None = None) -> str:
    app_url = (os.environ.get("APP_URL") or "").strip()
    if app_url and not any(h in app_url for h in ("localhost", "127.0.0.1")):
        return app_url.rstrip("/")

    if request is not None:
        proto = (
            request.headers.get("x-forwarded-proto")
            or getattr(getattr(request, "url", None), "scheme", None)
            or "http"
        )
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            return f"{proto}://{host}".rstrip("/")

    return "http://127.0.0.1:8001"


def share_path(invoice_guid: str | UUID) -> str:
    guid = str(UUID(str(invoice_guid)))
    return f"/hd/{guid}/{share_token(guid)}"


def share_url(invoice_guid: str | UUID, request: Request | None = None) -> str:
    return f"{public_base_url(request)}{share_path(invoice_guid)}"


async def get_public_invoice_pdf(invoice_guid: UUID, token: str):
    if not verify_token(invoice_guid, token):
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn.")

    invoice = queries.get_public_invoice_pdf_select_bkav_invoices(invoice_guid)
    if not invoice or not isinstance(invoice, dict):
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn.")

    try:
        _, pdf_path = await run_in_threadpool(get_or_fetch_bkav_invoice_files, str(invoice_guid))
        pdf_path = Path(pdf_path) if pdf_path else None
        if not pdf_path or not pdf_path.is_file():
            raise HTTPException(status_code=404, detail="Không tìm thấy file PDF hóa đơn.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Could not fetch BKAV invoice PDF for %s: %s", invoice_guid, exc)
        raise HTTPException(status_code=404, detail="Không tìm thấy file PDF hóa đơn.")

    serial = str(invoice.get('invoice_serial') or '')
    code = str(invoice.get('invoice_code') or '')
    number = int(invoice.get('invoice_number') or 0)
    filename = f"{serial}-{number:08d}-{code}-DPH.pdf" if code else f"{serial}-{number:08d}.pdf"
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(filename, "inline"),
            "Cache-Control": "private, max-age=300",
        },
    )
