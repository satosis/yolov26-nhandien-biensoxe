"""Zalo follow-up messages for recently finalized invoices: edit freight lines and sync to BKAV."""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import unicodedata
from datetime import datetime, timezone

from backend.services.database import db
from frontend.formatting.text import fold_text
from frontend.modules.invoices.bkav_push import saved_invoice_push_state
from frontend.modules.transport.invoice_accounting import effective_accounts, load_saved_accounts
from frontend.modules.transport.invoice_store import save_sales_invoice
from frontend.modules.zalo.inbox import list_messages

logger = logging.getLogger(__name__)

FOLLOW_UP_SECONDS = 24 * 3600
_FOLLOW_UP_LOCK = threading.Lock()

_LINE_NOTE = re.compile(
    r"c[ưu][ớo]c\s*(?:th[ứu]\s*)?(\d{1,2})\b(?![.,]\d)(?!\s*(?:tr\b|triệu|k\b|nghìn|đ\b))"
    r"[^\n]*?\bghi\s*(?:th[êe]m\s*)?(?:cho\s*(?:e|em)\s*)?(?:là\s*)?(?P<note>\S[^\n]*)",
    re.I,
)
# A message with a freight price is a new order, never a correction of an invoice already made.
_PRICE = re.compile(r"\d\s*(?:tr\b|triệu)", re.I)
_ADDRESS = re.compile(
    r"\bghi\s*(?:c[ảa]\s*|th[êe]m\s*)?[đd][ịi]a\s*ch[ỉi]\s*(?:c[ủu]a\s*)?(?:c[ôo]ng\s*ty|cty)?\s*(?P<name>[^\n]+?)"
    r"(?:\s+n[ữu]a)?(?:\s+nh[ée]|\s+nha)?[\s.!]*$",
    re.I,
)
# đuôi xã giao ở cuối ghi chú: " e với nhé", " với nhé", " nhé"... (không cắt "a"/"e" đứng riêng giữa câu)
_FILLER = re.compile(
    r"(?:\s+(?:e|em|a|anh)(?=\s+với))?(?:\s+với)?(?:\s+(?:nhé|nha|nhá))?[\s.!]*$",
    re.I,
)


def parse_follow_up(text: str) -> dict | None:
    """Parse a follow-up message into an edit instruction or return None."""
    if not text:
        return None
    normalized = unicodedata.normalize("NFC", str(text)).strip()
    if _PRICE.search(normalized):
        return None
    m_line = _LINE_NOTE.search(normalized)
    if m_line:
        line_num = int(m_line.group(1))
        raw_note = m_line.group("note") or ""
        note = _FILLER.sub("", raw_note).strip(" ,.")
        if note:
            return {"kind": "line_note", "line": line_num, "note": note}

    m_addr = _ADDRESS.search(normalized)
    if m_addr:
        name = (m_addr.group("name") or "").strip(" ,.")
        if name:
            return {"kind": "address", "name": name}

    return None


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold_text(value or ""))


def _has_run(words: list[str], run: list[str]) -> bool:
    return any(words[i:i + len(run)] == run for i in range(len(words) - len(run) + 1))


_COMPANY_MENTION = re.compile(r"(?:c[ôo]ng\s*ty|cty)\s+(?P<rest>[^,;.\n]+)", re.I)


def _company_named_in(note: str, owner_id: int | None) -> dict | None:
    """The one customer a note names after "công ty"/"cty", even by a middle part of its name ("công ty htc");
    None when the words fit no company or fit several."""
    found = _COMPANY_MENTION.search(note)
    if not found:
        return None
    typed = _words(found.group("rest"))
    companies = db._execute_query(
        "SELECT id, name, tax_code, address FROM public.invoice_companies "
        "WHERE COALESCE(is_customer, TRUE) = TRUE AND id IS DISTINCT FROM %s",
        (owner_id,), fetchall=True, commit=False) or []
    for count in range(min(len(typed), 8), 0, -1):
        run = typed[:count]
        if sum(len(word) for word in run) < 3:
            continue
        hits = [company for company in companies if _has_run(_words(company["name"]), run)]
        if len(hits) == 1:
            return dict(hits[0])
    return None


def _with_company_address(note: str, company: dict) -> str:
    """'… từ công ty htc' -> '… từ Công Ty TNHH …, <địa chỉ>': the company the note names, with the address saved for it."""
    address = (company.get("address") or "").strip()
    if not address or fold_text(address) in fold_text(note):
        return note
    name_words = _words(company.get("name"))
    found = _COMPANY_MENTION.search(note)
    if not name_words or not found:
        return note
    spans = [word.span() for word in re.finditer(r"\S+", found.group("rest"))]
    named = 0
    for count in range(1, min(len(spans), 8) + 1):
        typed = _words(found.group("rest")[:spans[count - 1][1]])
        if not typed or not _has_run(name_words, typed):
            break
        named = count
    if not named:
        return note
    end = found.start("rest") + spans[named - 1][1]
    return f"{note[:found.start()]}{company['name'].strip()}, {address}{note[end:]}"


def apply_follow_up(
    lines: list[dict], edit: dict, company: dict | None = None
) -> tuple[list[dict], str | None, str | None]:
    """Pure line editor: returns (new_lines, summary, problem)."""
    new_lines = [copy.deepcopy(line) for line in lines]
    kind = edit.get("kind")

    if kind == "line_note":
        n = edit.get("line")
        note = (edit.get("note") or "").strip()
        if company:  # a company the note names gets its saved address without being asked
            note = _with_company_address(note, company)
        target = None
        for line in new_lines:
            if line.get("line_number") == n:
                target = line
                break
        if target is None:
            return new_lines, None, f"chỉ có {len(lines)} dòng cước, chưa sửa được cước thứ {n}"

        desc = str(target.get("description") or "")
        if fold_text(note) in fold_text(desc):
            return new_lines, None, None

        target["description"] = f"{desc}, {note}" if desc else note
        return new_lines, f"cước thứ {n}", None

    elif kind == "address":
        name = (edit.get("name") or "").strip()
        if not company:
            return new_lines, None, f"chưa có công ty {name} trong danh sách"

        matching = [
            line for line in new_lines
            if fold_text(name) in fold_text(line.get("description") or "")
        ]
        if not matching:
            return new_lines, None, f"chưa tìm thấy dòng nào nhắc đến {name}"

        company_full = (company.get("name") or "").strip()
        address = (company.get("address") or "").strip()
        full_replacement = f"{company_full}, {address}" if address else company_full

        p1 = re.compile(r"(?:c[ôo]ng\s*ty|cty)\s+" + re.escape(name), re.I)
        p2 = re.compile(r"(?:\b|\A)" + re.escape(name) + r"(?:\b|\Z)", re.I)

        changed = False
        for line in matching:
            desc = str(line.get("description") or "")
            if address and fold_text(address) in fold_text(desc):
                continue
            if p1.search(desc):
                line["description"] = p1.sub(full_replacement, desc, count=1)
                changed = True
            elif p2.search(desc):
                line["description"] = p2.sub(full_replacement, desc, count=1)
                changed = True
            elif re.search(re.escape(name), desc, re.I):
                line["description"] = re.sub(re.escape(name), full_replacement, desc, count=1, flags=re.I)
                changed = True

        if changed:
            return new_lines, f"địa chỉ {name}", None
        return new_lines, None, None

    return new_lines, None, None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def handle_invoice_follow_up(item: dict) -> bool:
    """Handle follow-up message to modify a recently created invoice for the group."""
    edit = parse_follow_up(item.get("text") or "")
    if not edit:
        return False

    with _FOLLOW_UP_LOCK:
        group_id = str(item.get("group_id") or "")
        company_id = item.get("company_id")
        inv_num_str = ""
        try:
            # 2. Tìm hóa đơn mới nhất của nhóm trong vòng 24h
            now = datetime.now(timezone.utc)
            candidates = []
            for msg in list_messages(limit=300):
                if str(msg.get("group_id") or "") != group_id:
                    continue
                draft = msg.get("invoice_draft")
                if not isinstance(draft, dict) or not draft.get("invoice_guid"):
                    continue
                dt = _parse_iso(msg.get("received_at"))
                if dt is None:
                    continue
                delta = (now - dt).total_seconds()
                if -300 <= delta <= FOLLOW_UP_SECONDS:
                    candidates.append((dt, msg))

            if not candidates:
                return False

            candidates.sort(key=lambda p: p[0], reverse=True)
            best_msg = candidates[0][1]
            guid = str(best_msg["invoice_draft"]["invoice_guid"])

            invoice = db._execute_query(
                "SELECT * FROM public.bkav_invoices WHERE invoice_guid = %s",
                (guid,), fetch=True, commit=False,
            )
            if not invoice:
                return False
            if invoice.get("seller_company_id") != company_id:
                return False
            if invoice.get("entry_origin") not in ("manual", "edited"):
                return False

            inv_num_str = str(invoice.get("invoice_number") or "")

            # 3. Đọc dòng bkav_invoice_lines
            from frontend.modules.zalo import invoice_automation as automation

            raw_lines = db._execute_query(
                "SELECT * FROM public.bkav_invoice_lines WHERE invoice_guid = %s ORDER BY line_number",
                (guid,), fetchall=True, commit=False,
            ) or []
            lines = [dict(l) for l in raw_lines]

            company = None
            if edit.get("kind") == "address":
                name = edit.get("name", "").strip()
                company = automation.extract_company_by_name(f"cho công ty {name}", exclude_company_id=company_id)
                if not company:
                    rows = db._execute_query(
                        "SELECT id, name, tax_code, address FROM public.invoice_companies WHERE name ILIKE %s",
                        (f"%{name}%",), fetchall=True, commit=False,
                    ) or []
                    if len(rows) == 1:
                        company = dict(rows[0])
            elif edit.get("kind") == "line_note":
                company = _company_named_in(edit["note"], company_id)

            # 4. Gọi apply_follow_up
            new_lines, summary, problem = apply_follow_up(lines, edit, company)
            if problem:
                automation._ask_group(group_id, company_id, f"🤖 Hóa đơn số {inv_num_str}: {problem}.")
                return True

            if not summary:
                # Không có thay đổi thực sự -> summary = None và không lưu
                return True

            # 5. Dựng payload
            for line in new_lines:
                vp = line.get("via_points")
                if isinstance(vp, str):
                    try:
                        line["via_points"] = json.loads(vp)
                    except Exception:
                        line["via_points"] = []
                elif not isinstance(vp, list):
                    line["via_points"] = []

            accounting = effective_accounts(
                str(invoice.get("payment_status") or "debt"),
                str(invoice.get("payment_method") or "CK"),
                load_saved_accounts(guid),
            )
            payload = {
                "seller_company_id": invoice["seller_company_id"],
                "buyer_company_id": invoice["buyer_company_id"],
                "buyer_tax_code": invoice.get("buyer_tax_code") or "",
                "invoice_date": invoice["invoice_date"],
                "invoice_number": invoice["invoice_number"],
                "invoice_serial": invoice["invoice_serial"],
                "service_name": invoice["service_name"],
                "vat_rate": invoice["vat_rate"],
                "payment_method": invoice["payment_method"],
                "payment_status": invoice["payment_status"],
                "debt_amount": invoice["debt_amount"],
                "subtotal": invoice["subtotal"],
                "gross": invoice["gross"],
                "line_count": len(new_lines),
                "accounting": accounting,
                "lines": new_lines,
            }
            save_sales_invoice(payload, guid=guid, edit=True)

            # 6. Đồng bộ lại BKAV
            state = saved_invoice_push_state(guid)
            if state is None or state.get("status") == "failed":
                ok, push_problem = automation.push_invoice_to_bkav(guid)
                if push_problem:
                    automation._ask_group(
                        group_id, company_id,
                        f"🤖 Đã sửa hóa đơn số {inv_num_str} trong hệ thống nhưng chưa tạo được bản nháp trên BKAV: {push_problem}"
                    )
                else:
                    automation._ask_group(
                        group_id, company_id,
                        f"🧾 Đã cập nhật hóa đơn số {inv_num_str} ({summary})."
                    )
                    if ok:
                        automation.send_invoice_image_to_ai_group(guid)
            else:
                automation._ask_group(
                    group_id, company_id,
                    f"🤖 Đã sửa hóa đơn số {inv_num_str} trong hệ thống nhưng bản nháp tạo trên BKAV trước đó chưa được cập nhật, cần sửa lại trên BKAV."
                )
            return True

        except Exception as exc:
            logger.exception("Group %s: lỗi xử lý tin bổ sung hóa đơn %s: %s", group_id, inv_num_str, exc)
            msg = f"🤖 Chưa cập nhật được hóa đơn số {inv_num_str}, vui lòng sửa trên web." if inv_num_str else "🤖 Chưa cập nhật được hóa đơn, vui lòng sửa trên web."
            try:
                from frontend.modules.zalo import invoice_automation as automation
                automation._ask_group(group_id, company_id, msg)
            except Exception:
                pass
            return True
