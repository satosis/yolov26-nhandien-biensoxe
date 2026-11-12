"""Replacement note extraction and schema helper for BKAV replacement invoices."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from backend.services.database import db

logger = logging.getLogger(__name__)

def format_original_identify(identify: str | None) -> str:
    """Format BKAV OriginalInvoiceIdentify '[1]_[C26TAA]_[00000082]_[16/10/2026]' to note string."""
    if not identify:
        return ""
    parts = re.findall(r"\[([^\]]*)\]", str(identify))
    if len(parts) < 3:
        return ""
    m, k, s = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if not (m and k and s):
        return ""
    d = parts[3].strip() if len(parts) >= 4 else ""
    if d:
        return f"Thay thế cho hóa đơn Mẫu số {m} ký hiệu {k} số {s} ngày {d}"
    return f"Thay thế cho hóa đơn Mẫu số {m} ký hiệu {k} số {s}"


def extract_note_from_text(text: str | None) -> str:
    """Extract and normalize replacement invoice quote from plain text or PDF text."""
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", str(text))
    m = re.search(
        r"thay\s+thế\s+cho\s+hóa\s+đơn\s+mẫu\s+số[:\s]*([^\s,;]+)\s+ký\s+hiệu[:\s]*([^\s,;]+)\s+số[:\s]*([^\s,;]+)\s+ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        mau_so, ky_hieu, so, d, mo, y = m.groups()
        return f"Thay thế cho hóa đơn Mẫu số {mau_so} ký hiệu {ky_hieu} số {so} ngày {int(d):02d}/{int(mo):02d}/{y}"

    m2 = re.search(
        r"(thay\s+thế\s+cho\s+hóa\s+đơn\b.*?)\s*ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    if not m2:
        return ""
    prefix, d, mo, y = m2.groups()
    ms_m = re.search(r"mẫu\s+số[:\s]*([^\s,;]+)", prefix, re.IGNORECASE)
    kh_m = re.search(r"ký\s+hiệu[:\s]*([^\s,;]+)", prefix, re.IGNORECASE)
    so_m = re.search(r"(?<!mẫu\s)số[:\s]*([^\s,;]+)", prefix, re.IGNORECASE)
    if ms_m and kh_m and so_m:
        return f"Thay thế cho hóa đơn Mẫu số {ms_m.group(1)} ký hiệu {kh_m.group(1)} số {so_m.group(1)} ngày {int(d):02d}/{int(mo):02d}/{y}"
    return ""


def note_from_pdf(path: str | Path | None) -> str:
    """Extract replacement note from the first 3 pages of a PDF invoice."""
    if not path:
        return ""
    try:
        import fitz

        with fitz.open(str(path)) as doc:
            text_parts = []
            for i in range(min(3, len(doc))):
                text_parts.append(doc[i].get_text() or "")
            full_text = "\n".join(text_parts)
        return extract_note_from_text(full_text)
    except Exception:
        return ""


def fill_missing_notes(guids: list[str], limit: int = 20) -> dict[str, str]:
    """Fill replaces_note for invoices that have replaces_note IS NULL from stored PDFs."""
    if not guids:
        return {}
    target_guids = [str(g) for g in guids if g][:limit]
    result: dict[str, str] = {}
    for guid in target_guids:
        from frontend.modules.invoices import bkav_sync

        _png, pdf = bkav_sync.stored_bkav_invoice_files(guid)
        if not pdf:
            continue
        note = note_from_pdf(pdf) or ""
        db._execute_query(
            "UPDATE public.bkav_invoices SET replaces_note = %s WHERE invoice_guid = %s::uuid AND replaces_note IS NULL",
            (note, guid),
            commit=True,
        )
        result[guid] = note
    return result
