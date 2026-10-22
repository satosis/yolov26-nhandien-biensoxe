"""Conservative Vietnamese salary advance extraction for Zalo group messages."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from frontend.modules.bank_transfer import remove_accents
from frontend.modules.payroll.advances import create_staff_salary_advance
from frontend.modules.payroll.staff_payroll import staff_members


VIETNAM_TZ = timezone(timedelta(hours=7))
MONEY_PATTERN = re.compile(
    r"(?<![a-z0-9])(?P<number>\d+(?:[.,]\d+)*)\s*"
    r"(?P<unit>trieu|tr|nghin|ngan|k|dong|vnd|d)\b"
)
HONORIFICS = {"chu", "bac", "anh", "chi", "co", "ong", "ba", "em", "ban"}


def normalize_vietnamese(value: str) -> str:
    return " ".join(remove_accents(value).casefold().split())


def looks_like_salary_advance(text: str) -> bool:
    plain = normalize_vietnamese(text)
    if plain.startswith("qr tam ung luong"):
        return False
    return bool(re.search(r"\bluong\b", plain) and re.search(r"\b(chuyen|tam ung|ung|tra)\b", plain))


def _parse_amount(number: str, unit: str) -> Decimal:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
        value = Decimal(re.sub(r"[.,]", "", number))
    elif number.count(".") + number.count(",") <= 1:
        value = Decimal(number.replace(",", "."))
    else:
        raise ValueError("Số tiền trong tin nhắn không rõ ràng.")
    multiplier = {
        "trieu": 1_000_000, "tr": 1_000_000,
        "nghin": 1_000, "ngan": 1_000, "k": 1_000,
        "dong": 1, "vnd": 1, "d": 1,
    }[unit]
    amount = value * multiplier
    if not amount.is_finite() or amount != amount.to_integral_value() or amount <= 0:
        raise ValueError("Số tiền tạm ứng phải là số nguyên đồng lớn hơn 0.")
    return amount


def _clean_recipient(raw: str) -> str:
    words = re.findall(r"[a-z]+", raw)
    while words and words[0] in HONORIFICS:
        words.pop(0)
    if not words or len(words) > 5:
        raise ValueError("Không xác định được tên người nhận tạm ứng trong tin nhắn.")
    return " ".join(words)


def parse_salary_advance(text: str) -> tuple[str, Decimal] | None:
    """Return a named recipient and exact amount; never guess ambiguous requests."""
    if not looks_like_salary_advance(text):
        return None
    plain = normalize_vietnamese(text)
    if re.search(r"\b(dung|huy|chua|khong)\s+(?:[a-z]+\s+){0,2}(chuyen|tam ung|ung|tra)\b", plain):
        raise ValueError("Tin nhắn có ý phủ định, không tự tạo tạm ứng.")
    amounts = list(MONEY_PATTERN.finditer(plain))
    if len(amounts) != 1:
        raise ValueError("Cần đúng một số tiền tạm ứng rõ ràng trong tin nhắn.")
    amount_match = amounts[0]
    before = plain[:amount_match.start()]
    recipient_match = re.search(r"\bcho\s+([a-z\s]{1,70}?)\s*$", before)
    if recipient_match:
        recipient = _clean_recipient(recipient_match.group(1))
    else:
        after = plain[amount_match.end():]
        recipient_match = re.search(r"\bcho\s+(.{1,70})", after)
        if not recipient_match:
            raise ValueError("Cần ghi rõ người nhận cùng số tiền, ví dụ: cho Chú Tư 3 triệu tiền lương.")
        raw_recipient = re.split(
            r"[?.!,]|\b(?:nhe|nha|duoc|khong|giup|voi|thang|hom|nay)\b",
            recipient_match.group(1), maxsplit=1,
        )[0]
        recipient = _clean_recipient(raw_recipient)
    try:
        amount = _parse_amount(amount_match.group("number"), amount_match.group("unit"))
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("Số tiền trong tin nhắn không hợp lệ.") from exc
    return recipient, amount


def _aliases(member: dict) -> set[str]:
    name = normalize_vietnamese(str(member.get("full_name") or ""))
    aliases = {name} if name else set()
    if name:
        aliases.add(name.rsplit(" ", 1)[-1])
    for nickname in str(member.get("nickname") or "").split(","):
        alias = normalize_vietnamese(nickname)
        if alias:
            aliases.add(alias)
            words = alias.split()
            while words and words[0] in HONORIFICS:
                words.pop(0)
            if words:
                aliases.add(" ".join(words))
    return aliases


def match_staff(recipient: str, members: list[dict]) -> dict:
    recipient = _clean_recipient(recipient)
    matches = [member for member in members if recipient in _aliases(member)]
    if not matches:
        raise ValueError(f"Không tìm thấy nhân sự tên/nickname '{recipient}' trong công ty.")
    if len(matches) != 1:
        raise ValueError(f"Tên/nickname '{recipient}' trùng nhiều nhân sự; cần ghi rõ tên khác.")
    return matches[0]


def month_from_message(item: dict) -> date:
    raw = str(item.get("sent_at") or item.get("received_at") or "")
    try:
        sent_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Thời gian tin nhắn không hợp lệ.") from exc
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    local = sent_at.astimezone(VIETNAM_TZ)
    return date(local.year, local.month, 1)


def create_advance_from_message(item: dict, company_id: int | None = None) -> tuple[dict, dict]:
    parsed = parse_salary_advance(str(item.get("text") or ""))
    if parsed is None:
        raise ValueError("Tin nhắn không phải yêu cầu tạm ứng lương.")
    if not company_id:
        raise ValueError("Cần chọn công ty đang làm việc để xác định nhân sự tạm ứng.")
    recipient, amount = parsed
    member = match_staff(recipient, staff_members(company_id))
    month = month_from_message(item)
    request_id = str(uuid5(NAMESPACE_URL, f"zalo-salary-advance:{item['id']}"))
    payment = create_staff_salary_advance(member["id"], month, amount, request_id, company_id, None)
    return member, payment
