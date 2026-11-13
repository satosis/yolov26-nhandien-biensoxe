"""Thông tin đơn vị đọc từ ảnh/tin Zalo (danh thiếp, phiếu thông tin công ty): chỉ điền các ô đang trống."""
import asyncio
import functools
import logging
import re

from backend.services.database import db
from frontend.formatting.text import fold_text
from frontend.modules.invoices.companies import canonical_bank_name, fill_missing_company_fields, list_bank_names
from frontend.modules.zalo.invoice_automation import (
    ensure_company_for_tax_code,
    labelled_tax_codes,
    valid_tax_checksum,
)

logger = logging.getLogger(__name__)

_ACCOUNT_LABEL_RE = re.compile(r"(s[o0]taikh[o0]an|taikh[o0]an|acc[o0]unt|s[o0]tk|stk)(.*)$", re.IGNORECASE)
_PHONE_LABEL_RE = re.compile(r"(dienth[o0]ai|ph[o0]ne|sdt|tel|dt)(.*)$", re.IGNORECASE)
_BANK_LINE_RE = re.compile(r"(nganhang|ng[aâ]nh[aàáảã]ng)", re.IGNORECASE)

_VOWEL_MAP = {
    "a": r"[aàáảãạăằắẳẵặâầấẩẫậ]",
    "e": r"[eèéẻẽẹêềếểễệ]",
    "i": r"[iíìịỉĩ]",
    "o": r"[oóòọỏõôốồộổỗơớờợởỡ0]",
    "u": r"[uúùụủũưứừựửữ]",
    "y": r"[yýỳỵỷỹ]",
    "d": r"[dđ]",
}


def _make_bank_pattern(b_sq: str) -> str:
    parts = []
    for c in b_sq:
        parts.append(_VOWEL_MAP.get(c, re.escape(c)))
    return r"\s*".join(parts)


@functools.lru_cache(maxsize=1)
def _get_administrative_names_map() -> dict[str, str]:
    rows = db._execute_query(
        """SELECT name FROM public.administrative_communes
           UNION
           SELECT name FROM public.administrative_provinces""",
        fetchall=True, commit=False,
    ) or []
    prefixes = ("Phường ", "Xã ", "Thị trấn ", "Đặc khu ", "Tỉnh ", "Thành phố ")
    mapping = {}
    for r in rows:
        raw_name = str(r.get("name") or "").strip()
        if not raw_name:
            continue
        cleaned = raw_name
        for p in prefixes:
            if cleaned.startswith(p):
                cleaned = cleaned[len(p):].strip()
                break
        key = re.sub(r"\s+", "", fold_text(cleaned))
        if key and key not in mapping:
            mapping[key] = cleaned
    return mapping


def parse_company_card(text: str) -> dict:
    if not text:
        return {}

    codes = list(dict.fromkeys(c for c in labelled_tax_codes(text) if valid_tax_checksum(c)))
    if len(codes) != 1:
        return {}

    card = {"tax_code": codes[0]}
    bank_candidates: list[tuple[str, str]] = []

    for line in text.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        folded = fold_text(line_str)
        squeezed = re.sub(r"\s+", "", folded)

        if "bank_account" not in card:
            m_acc = _ACCOUNT_LABEL_RE.search(squeezed)
            if m_acc:
                after_acc = m_acc.group(2)
                m_num = re.search(r"(\d[\d.-]*)", after_acc)
                if m_num:
                    clean_acc = re.sub(r"\D", "", m_num.group(1))
                    if 6 <= len(clean_acc) <= 20:
                        card["bank_account"] = clean_acc
                        rem_sq = squeezed[:m_acc.start()] + after_acc[m_num.end():]
                        bank_candidates.append((rem_sq, line_str))

        if _BANK_LINE_RE.search(squeezed):
            bank_candidates.append((squeezed, line_str))

        if "phone" not in card:
            m_phone = _PHONE_LABEL_RE.search(squeezed)
            if m_phone:
                after_phone = m_phone.group(2)
                m_pnum = re.search(r"(\+?[0-9][0-9.-]*)", after_phone)
                if m_pnum:
                    clean_phone = re.sub(r"\D", "", m_pnum.group(1))
                    if 9 <= len(clean_phone) <= 11 and (clean_phone.startswith("0") or clean_phone.startswith("84")):
                        card["phone"] = clean_phone

    if bank_candidates:
        bank_names = list_bank_names()
        matches = []
        for rem_sq, orig_line in bank_candidates:
            for b in bank_names:
                b_sq = re.sub(r"[^a-z0-9]", "", fold_text(b))
                if b_sq and b_sq in rem_sq:
                    matches.append((len(b_sq), b_sq, b, orig_line))
        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            _, best_sq, best_bank, orig_line = matches[0]
            card["bank_name"] = canonical_bank_name(best_bank)

            pat = _make_bank_pattern(best_sq)
            m_bank = re.search(pat, orig_line, re.IGNORECASE)
            if m_bank:
                after_bank = orig_line[m_bank.end():]
                m_cn = re.match(r"^[\s:.-]*(?:chi\s*nh[aáàạảã]nh|cn)[\s:.-]*(.*)$", after_bank, re.IGNORECASE)
                if m_cn and m_cn.group(1).strip():
                    # Chi nhánh dừng ở dấu phẩy/chấm phẩy hoặc nhãn kế tiếp viết cùng dòng ("..., ĐT 09...").
                    raw_branch = re.split(
                        r"[,;|]|\s(?:đt|dt|sđt|sdt|điện thoại|dien thoai|mst|email|e-mail|tel)\b",
                        m_cn.group(1), maxsplit=1, flags=re.IGNORECASE,
                    )[0]
                    branch_split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw_branch)
                    branch_clean = re.sub(r"\s+", " ", branch_split).strip(" ,.-:")
                    if branch_clean:
                        key = re.sub(r"\s+", "", fold_text(branch_clean))
                        admin_map = _get_administrative_names_map()
                        branch_name = admin_map.get(key, branch_clean)
                        card["branch"] = f"CN {branch_name}"

    m_email = re.search(r"(?:e-?mail|gmai[l1]?|mail)[:.\s-]+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text, re.IGNORECASE)
    if not m_email:
        m_email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if m_email:
        card["email"] = (m_email.group(1) if m_email.lastindex else m_email.group(0)).lower()

    return card


async def fill_company_from_card(text: str) -> list[str]:
    card = parse_company_card(text)
    # Chỉ một số điện thoại thì chưa chắc là thông tin công ty (tin đặt hàng hay kèm số lái xe): cần có tài khoản hoặc email.
    if not card.get("tax_code") or not any(card.get(k) for k in ("bank_account", "email")):
        return []

    company = await ensure_company_for_tax_code(card["tax_code"])
    if not company:
        return []

    bank_acc = card.get("bank_account")
    if bank_acc:
        acc_digits = re.sub(r"\D", "", str(bank_acc))
        conflict = db._execute_query(
            """SELECT 1 FROM public.company_bank_accounts
               WHERE company_id != %s AND regexp_replace(account_number, '\\D', '', 'g') = %s
               UNION
               SELECT 1 FROM public.invoice_companies
               WHERE id != %s AND regexp_replace(bank_account, '\\D', '', 'g') = %s
               LIMIT 1""",
            (company["id"], acc_digits, company["id"], acc_digits),
            fetchall=True, commit=False,
        )
        if conflict:
            card.pop("bank_account", None)
            card.pop("bank_name", None)
            card.pop("branch", None)

    filled = await asyncio.to_thread(fill_missing_company_fields, company["id"], card)
    if filled:
        logger.info(
            "Zalo: bổ sung %s cho đơn vị %s (%s) từ ảnh/tin thông tin công ty",
            filled,
            company.get("name"),
            card.get("tax_code"),
        )
    return filled
