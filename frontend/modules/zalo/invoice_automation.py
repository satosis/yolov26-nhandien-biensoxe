"""Automated grouping of Zalo messages for invoice creation in a fixed window after the buyer is recognised."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

import httpx
from functools import lru_cache

from backend.services.database import db
from backend.services.google_maps import (
    geocode_address_google, resolve_place, reverse_geocode_admin_google, route_distance_km)
from frontend.context.company_context import company_context
from frontend.formatting.money import MILLION_PRICE as _MILLION, million_price
from frontend.formatting.text import fold_text
from frontend.modules.invoices.export import CRANE_BY_SHIFT_SERVICE, same_place
from frontend.modules.invoices.companies import lookup_tax_code, normalize_tax_code, save_company
from frontend.modules.zalo.inbox import INBOX_DIR, get_message, list_messages, save_ocr_text
from frontend.modules.transport.invoice_forms import liters_for_distance
from frontend.modules.transport.invoice_locations import (
    google_normalized_place, load_commune_directory, normalize_invoice_location)
from frontend.modules.transport.vehicle_matching import (
    vehicle_owner_in_text, vehicle_split_in_text, vehicle_with_plate_in_text)

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_SECONDS = 180  # 3 minutes from the buyer being recognised
SESSIONS_DIR = INBOX_DIR / "invoice_sessions"
# Orders that came without a tax code wait here for the reply that carries it.
WAITING_DIR = INBOX_DIR / "invoice_waiting"
WAITING_SECONDS = 3600
ASK_TAX_CODE = ("🤖 Tin này chưa có mã số thuế (MST) của bên mua nên chưa lập được hóa đơn. "
                "Anh/chị gửi giúp MST công ty cần xuất hóa đơn.")
DRAFTS_DIR = INBOX_DIR / "trip_drafts"
# Every message of a session schedules a wait and a restart resumes it: the first finalizer claims the session.
_FINALIZE_LOCK = threading.Lock()

# Google answers kept in memory: a message is resolved once, however many steps ask for its place
_GEOCODE_STANDARD_CACHE: dict[str, str] = {}
_CARD_COORDINATES_CACHE: dict[str, tuple[float, float]] = {}
_FINALIZE_WAITERS: dict[str, asyncio.Task] = {}

_COORD_RE = re.compile(r"([0-9]{1,2}\.[0-9]{4,8})[\s,]+([0-9]{2,3}\.[0-9]{4,8})")


def _clean_coordinate_key(lat: float, lon: float) -> str:
    return f"{round(lat, 5)},{round(lon, 5)}"


def geocode_card_address(address: str) -> tuple[float, float]:
    """Coordinates of a Zalo location card that carries only its address text, from the Google Geocoding API."""
    if address not in _CARD_COORDINATES_CACHE:
        found = asyncio.run(geocode_address_google(address, raise_errors=True))
        _CARD_COORDINATES_CACHE[address] = (found["lat"], found["lon"])
        logger.info("Zalo location card %r: Google Geocoding -> %s", address, _CARD_COORDINATES_CACHE[address])
    return _CARD_COORDINATES_CACHE[address]


def _typed_place(commune: str, province: str, directory: list[dict]) -> str:
    """Google names a ward/commune and province without their type; the official list supplies it:
    'Vĩnh Phúc', 'Phú Thọ' -> 'Phường Vĩnh Phúc, Tỉnh Phú Thọ'. '' when the list cannot settle it.
    When Google names an old merged province, a commune name unique in the whole country supplies its new province:
    'Đèo Gia', 'Bắc Giang' -> 'Xã Đèo Gia, Tỉnh Bắc Ninh'."""
    wanted_province = tuple(_fold_words(province))
    provinces = {row["province_name"] for row in directory
                 if _split_type(_fold_words(row["province_name"]), _PROVINCE_TYPES)[1] == wanted_province}
    wanted_commune = tuple(_fold_words(commune))
    if len(provinces) != 1:
        communes = [row for row in directory
                    if _split_type(_fold_words(row["commune_name"]), _COMMUNE_TYPES)[1] == wanted_commune]
        return f"{communes[0]['commune_name']}, {communes[0]['province_name']}" if len(communes) == 1 else ""
    official = next(iter(provinces))
    communes = {row["commune_name"] for row in directory if row["province_name"] == official
                and _split_type(_fold_words(row["commune_name"]), _COMMUNE_TYPES)[1] == wanted_commune}
    return f"{next(iter(communes))}, {official}" if len(communes) == 1 else ""


def resolve_coordinates_address_standard(lat: float, lon: float, directory: list[dict]) -> str:
    """Current 'Xã/Phường, Tỉnh' of a point, from the Google Geocoding API."""
    key = _clean_coordinate_key(lat, lon)
    if key not in _GEOCODE_STANDARD_CACHE:
        commune, province = asyncio.run(reverse_geocode_admin_google(lat, lon))
        _GEOCODE_STANDARD_CACHE[key] = _typed_place(commune, province, directory) or f"{commune}, {province}"
        logger.info("Zalo pin (%s, %s): Google Geocoding -> %r", lat, lon, _GEOCODE_STANDARD_CACHE[key])
    return _GEOCODE_STANDARD_CACHE[key]


def message_place(message: dict, directory: list[dict]) -> tuple[tuple[float, float], str] | None:
    """The one way a Zalo message becomes a place: (pin, current 'Xã/Phường, Tỉnh'), None when it shares none.

    The pin is typed coordinates, the Google Maps link of a card, or the address text of a card that has no
    pin (Google turns it into coordinates). Google then names the current ward/commune and province.
    """
    text = str(message.get("text") or "").strip()
    found = _COORD_RE.search(text)
    pin = (float(found.group(1)), float(found.group(2))) if found else _card_coordinates(message)
    place_from_list = ""
    if not pin and message.get("attachment_type") == "chat.location.new":
        address = re.sub(r",\s*việt\s*nam\s*$", "", text, flags=re.IGNORECASE).strip()
        parts = [
            part.strip() for part in address.split(",")
            if part.strip()
            and fold_text(part.strip()) not in {"duong chua dat ten", "duong khong ten", "unnamed road"}
            and not re.fullmatch(r"[23456789CFGHJMPQRVWX]{2,8}\+[23456789CFGHJMPQRVWX]{0,3}", part.strip().upper())
        ]
        # A card ending in a current province ("…, Đèo Gia, Bắc Ninh") names a current commune: the first part the
        # official list knows in that province. One ending in a merged province ("…, Bình Xuyên, Vĩnh Phúc") lists
        # former communes and districts, so Google settles it from the pin.
        province = " ".join(_split_type(_fold_words(parts[-1]), _PROVINCE_TYPES)[1]) if parts else ""
        if province and any(" ".join(_split_type(_fold_words(row["province_name"]), _PROVINCE_TYPES)[1]) == province
                            for row in directory):
            place_from_list = next(filter(None, (_typed_place(part, province, directory) for part in parts[:-1])), "")
        cleaned_address = ", ".join(parts)
        pin = geocode_card_address(cleaned_address) if cleaned_address else None
    return (pin, place_from_list or resolve_coordinates_address_standard(*pin, directory)) if pin else None


def extract_matching_company(text: str, exclude_company_id: int | None = None) -> Optional[dict]:
    """Check if the text contains a tax code matching an active company in invoice_companies.

    `exclude_company_id` - the seller that owns the Zalo group - is never the buyer.
    """
    if not text:
        return None
    codes = re.findall(r"\b\d{10}(?:-\d{3})?\b", text)
    if not codes:
        return None
    rows = db._execute_query(
        "SELECT id, name, tax_code, address, phone, email FROM public.invoice_companies "
            "WHERE tax_code = ANY(%s) AND id IS DISTINCT FROM %s",
        (codes, exclude_company_id),
        fetchall=True,
    )
    if rows:
        return rows[0]
    return None


def read_image_text(path: Path) -> str:
    import cv2
    import numpy as np
    from backend.util.ocr.aster_engine import get_aster_recognizer, read_document_text

    path_obj = Path(path)
    if not path_obj.is_file():
        return ""
    arr = np.fromfile(str(path_obj), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return ""
    return read_document_text(img, get_aster_recognizer(), is_bgr=True)


def _is_own_invoice_photo(ocr_text: str, owner_id: int | None) -> bool:
    """A photo that carries the group owner's own tax code is an invoice the seller already issued, not an order."""
    if not owner_id:
        return False
    rows = db._execute_query("SELECT tax_code FROM public.invoice_companies WHERE id = %s", (owner_id,),
                             fetchall=True, commit=False) or []
    own = str(rows[0]["tax_code"] or "")[:10] if rows else ""
    return bool(own) and own in re.findall(r"\b(\d{10})(?:-\d{3})?\b", ocr_text or "")


def _is_image_message(item: dict) -> bool:
    if not item or not item.get("saved_file"):
        return False
    if item.get("attachment_type") == "chat.photo":
        return True
    name = str(item.get("attachment_name") or "").lower()
    return any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))


def labelled_tax_codes(text: str) -> list[str]:
    if not text:
        return []
    folded = fold_text(text)
    nospace = re.sub(r"\s+", "", folded)
    matches = re.findall(r"(?:masothue|mst)(?:\(taxcode\))?[^0-9]{0,3}(\d{10}(?:-?\d{3})?)", nospace)
    results = []
    seen = set()
    for raw in matches:
        try:
            norm = normalize_tax_code(raw)
            if norm not in seen:
                seen.add(norm)
                results.append(norm)
        except ValueError:
            continue
    return results


def valid_tax_checksum(code: str) -> bool:
    digits = [int(c) for c in str(code or "") if c.isdigit()]
    if len(digits) < 10:
        return False
    weights = [31, 29, 23, 19, 17, 13, 7, 5, 3]
    total = sum(d * w for d, w in zip(digits[:9], weights))
    check = 10 - (total % 11)
    return check < 10 and check == digits[9]


async def ensure_company_for_tax_code(code: str) -> dict | None:
    rows = db._execute_query(
        "SELECT id, name, tax_code, address, phone, email FROM public.invoice_companies WHERE tax_code = %s",
        (code,),
        fetchall=True,
    )
    if rows:
        return rows[0]

    data = await lookup_tax_code(code)

    if data.get("id"):
        rows = db._execute_query(
            "SELECT id, name, tax_code, address, phone, email FROM public.invoice_companies WHERE tax_code = %s",
            (code,),
            fetchall=True,
        )
        if rows:
            return rows[0]
        return {
            "id": data["id"],
            "name": data.get("name") or "",
            "tax_code": data.get("tax_code") or code,
            "address": data.get("address") or "",
            "phone": data.get("phone") or "",
            "email": data.get("email") or "",
        }

    saved = save_company(
        code,
        data["name"],
        data["address"],
        data.get("email") or "",
        phone=data.get("phone") or "",
        director_user_id=data.get("director_user_id"),
        business_activity=data.get("business_activity") or None,
    )
    logger.info("Đã tạo đơn vị %s (%s) từ tin Zalo", data.get("name"), code)
    if saved and saved.get("id"):
        return {
            "id": saved["id"],
            "name": saved.get("name") or "",
            "tax_code": saved.get("tax_code") or code,
            "address": saved.get("address") or "",
            "phone": saved.get("phone") or "",
            "email": saved.get("email") or "",
        }
    rows = db._execute_query(
        "SELECT id, name, tax_code, address, phone, email FROM public.invoice_companies WHERE tax_code = %s",
        (code,),
        fetchall=True,
    )
    if rows:
        return rows[0]
    return None


# Legal forms and everyday trade words. A name phrase made only of these ("thiết bị đo lường") is ordinary
# cargo vocabulary, not a customer. Province names are added at lookup time.
_GENERIC_NAME_PHRASES = (
    "cong ty", "cty", "tnhh", "trach nhiem huu han", "co phan", "mtv", "mot thanh vien", "1 thanh vien",
    "doanh nghiep tu nhan", "hop tac xa", "chi nhanh", "trung tam", "viet nam", "tm", "dv", "va",
    "thuong mai", "dich vu", "san xuat", "xay dung", "van tai", "dau tu", "phat trien", "xuat nhap khau",
    "kinh doanh", "ky thuat", "khoa hoc", "cong nghe", "cong nghiep", "thiet bi", "do luong", "dieu khien",
    "giam sat", "giai phap", "ung dung", "tu van", "kiem dinh", "moi truong", "tu dong hoa", "co khi",
)
_LEGAL_FORM_PREFIX = re.compile(
    r"^(?:(?:cong ty|cty|tnhh|trach nhiem huu han|co phan|cp|mtv|mot thanh vien|1 thanh vien|"
    r"doanh nghiep tu nhan|dntn|hop tac xa|htx|chi nhanh) )+"
)
# Words that, right before a short name ("cho Sơn Hà", "Công ty Cổ phần Tecon88"), show that a customer is named.
_NAME_CUES = (
    "cho", "cua", "ben", "khach", "khach hang", "don vi", "cong ty", "cty", "ct", "tnhh", "cp", "co phan",
    "mtv", "mot thanh vien", "trach nhiem huu han", "dntn", "htx",
)
# This app's own notices (invoice created, transfer info, AI replies) name the buyer too but are never a request.
_BOT_NOTICE_MARKERS = ("🧾", "💳", "🤖")


def _fold_words(value: str) -> list[str]:
    """Lower-case words without accents, so 'Thuỷ', 'thủy' and 'THUY' compare equal."""
    return re.findall(r"[a-z0-9]+", fold_text(value))


def _generic_pattern(provinces: Iterable[str]) -> re.Pattern:
    phrases = set(_GENERIC_NAME_PHRASES)
    for name in provinces:
        phrases.add(re.sub(r"^(?:tinh|thanh pho) ", "", " ".join(_fold_words(name))))
    phrases.discard("")
    alternatives = "|".join(re.escape(phrase) for phrase in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\S)(?:{alternatives})(?!\S)")


def _name_tails(name: str, generic: re.Pattern) -> set[tuple[str, ...]]:
    """Trailing word runs of a company name that can stand for it in chat.

    "CÔNG TY TNHH CÂN ĐIỆN TỬ THẾ HỆ MỚI" -> (can dien tu the he moi), (dien tu the he moi), ..., (the he moi), (he moi).
    """
    words = _fold_words(name)
    core = _LEGAL_FORM_PREFIX.sub("", " ".join(words) + " ").split() or words
    joined = " ".join(core)
    generic_at = {index for m in generic.finditer(joined)
                  for index in range(joined.count(" ", 0, m.start()), joined.count(" ", 0, m.end()) + 1)}
    tails = set()
    for start in range(len(core)):
        distinctive = sum(len(core[i]) for i in range(start, len(core)) if i not in generic_at) >= 3
        if not distinctive and not (start == 0 and len(core) >= 4):
            continue  # only legal form / trade words / province; a long full name is still unmistakable
        if len(core) - start >= 2 or len(core[start]) >= 6:
            tails.add(tuple(core[start:]))
    return tails


def _mentions(words: list[str], tail: tuple[str, ...]) -> bool:
    size = len(tail)
    for start in range(len(words) - size + 1):
        if tuple(words[start:start + size]) != tail:
            continue
        before = " " + " ".join(words[max(0, start - 4):start])
        if size >= 3 or any(before.endswith(" " + cue) for cue in _NAME_CUES):
            return True
    return False


def _initials_mentioned(text: str) -> set[str]:
    raw_words = re.findall(r"\w+", str(text or ""))
    folded_words = [_fold_words(w)[0] if _fold_words(w) else "" for w in raw_words]
    initials = set()
    for idx, w in enumerate(raw_words):
        if w.isalpha() and w.isupper() and len(w) >= 3:
            before = " " + " ".join(folded_words[max(0, idx - 4):idx])
            if any(before.endswith(" " + cue) for cue in _NAME_CUES):
                f = fold_text(w).lower()
                if f:
                    initials.add(f)
    return initials


_NO_BRAND = {"tnhh", "mtv", "jsc", "ltd"}
# A word right after "công ty" is a quantity when a unit follows it ("công ty 25 tấn"), and "công ty này/đó/trên"
# points at a place, not at a name.
_QUANTITY_UNITS = {
    "tan", "kg", "trieu", "tr", "k", "nghin", "dong", "d", "vnd", "xe", "chuyen", "qua", "cay", "bo", "m", "met",
    "phan", "ngay", "thang", "gio", "h",
}
_POINTING_WORDS = {"nay", "do", "ay", "kia", "tren", "duoi", "tiep", "sau"}


def _partial_company(words: list[str], companies: list[dict]) -> Optional[dict]:
    """"cho công ty 25": the one company whose name contains the word written right after "công ty" / "cty".

    Any part of a name word counts, not only its end ("25" finds VNTT25, "econ" finds TECON88);
    a number needs 2-4 digits, a word with a digit 2+ characters, plain letters 4+. It is skipped before a unit
    ("công ty 25 tấn"), and only a single matching company counts: two means ask for the tax code.
    """
    typed = set()
    for index, word in enumerate(words):
        preceding = words[max(0, index - 2):index]
        after_company = preceding == ["cong", "ty"] or preceding[-1:] in (["cty"], ["ct"])
        following = words[index + 1] if index + 1 < len(words) else ""
        # "công ty TNHH Tep": the legal form is in every company name, it names none of them
        if not after_company or word in _POINTING_WORDS or word in _NO_BRAND or following in _QUANTITY_UNITS:
            continue
        has_digit = any(char.isdigit() for char in word)
        if (word.isdigit() and 2 <= len(word) <= 4) or (not word.isdigit() and len(word) >= (2 if has_digit else 4)):
            typed.add(word)
    if not typed:
        return None
    owners = {
        company["id"]: company for company in companies
        if any(part in name_word for part in typed for name_word in _fold_words(company["name"]))
    }
    return next(iter(owners.values())) if len(owners) == 1 else None


def _swapped_letters(typed: str, word: str) -> bool:
    """`typed` is `word` with two neighbouring letters exchanged ("thm" for "tmh")."""
    if len(typed) != len(word):
        return False
    wrong = [at for at, (a, b) in enumerate(zip(typed, word)) if a != b]
    return (len(wrong) == 2 and wrong[1] == wrong[0] + 1
            and typed[wrong[0]] == word[wrong[1]] and typed[wrong[1]] == word[wrong[0]])


def _brand_companies(typed: set[str], companies: list[dict]) -> dict[int, dict]:
    """Companies whose name has a short vowel-less brand word ("TMH", "HTC") that a capitalised word written after a
    cue is, or is with two neighbouring letters swapped ("cty THM" for TMH). A word written exactly wins over a swap."""
    exact: dict[int, dict] = {}
    swapped: dict[int, dict] = {}
    for company in companies:
        words = _fold_words(company["name"])
        core = _LEGAL_FORM_PREFIX.sub("", " ".join(words) + " ").split() or words
        for word in core:
            if not (3 <= len(word) <= 5 and word.isalpha() and word not in _NO_BRAND and not set(word) & set("aeiouy")):
                continue
            for token in typed:
                if token == word:
                    exact[company["id"]] = company
                elif _swapped_letters(token, word):
                    swapped[company["id"]] = company
    return exact or swapped


def match_company_by_name(text: str, companies: list[dict], provinces: Iterable[str] = ()) -> Optional[dict]:
    """The one company whose name `text` mentions: its full name or a trailing part of it.

    A tail of 3+ words ("thế hệ mới") is enough; 1-2 words ("Sơn Hà") count only right after a cue such as
    "cho", "của" or "công ty", because short names collide with everyday words ("thành công").
    When no name tail matches, a capitalised brand word after a cue is matched against the names ("cty TMH", also
    typed "THM"), and only then the initials of 3+ words ("cty TNM" for "Thế Nam Mới").
    A tail shared by several companies, or a tie between two, matches nothing: a wrong buyer is worse than none.
    """
    words = _fold_words(text)
    if not words:
        return None
    generic = _generic_pattern(provinces)
    owners: dict[tuple[str, ...], dict] = {}
    shared: set[tuple[str, ...]] = set()
    for company in companies:
        for tail in _name_tails(company["name"], generic):
            if owners.setdefault(tail, company)["id"] != company["id"]:
                shared.add(tail)
    found = [tail for tail in owners if _mentions(words, tail)]
    if not found:
        partial = _partial_company(words, companies)
        if partial:
            return partial
        mentioned = _initials_mentioned(text)
        if not mentioned:
            return None
        branded = _brand_companies(mentioned, companies)
        if branded:
            return next(iter(branded.values())) if len(branded) == 1 else None
        initial_owners: dict[str, dict] = {}
        initial_shared: set[str] = set()
        for company in companies:
            for tail in _name_tails(company["name"], generic):
                if len(tail) >= 3:
                    abbr = "".join(w[0] for w in tail)
                    if initial_owners.setdefault(abbr, company)["id"] != company["id"]:
                        initial_shared.add(abbr)
        matched = {
            initial_owners[abbr]["id"]: initial_owners[abbr]
            for abbr in mentioned
            if abbr in initial_owners and abbr not in initial_shared
        }
        if len(matched) == 1:
            return next(iter(matched.values()))
        return None
    longest = max(len(tail) for tail in found)
    best = [tail for tail in found if len(tail) == longest]
    if shared.intersection(best) or len({owners[tail]["id"] for tail in best}) != 1:
        return None
    return owners[best[0]]


def extract_company_by_name(text: str, exclude_company_id: int | None = None) -> Optional[dict]:
    """Find the customer a message names when it carries no tax code, e.g. 'bàn cân cho thế hệ mới'.

    Only organisations flagged as customers are considered (same rule as the partner catalogue), and
    `exclude_company_id` - the seller that owns the Zalo group - is never the buyer.
    """
    if not text or text.lstrip().startswith(_BOT_NOTICE_MARKERS):
        return None
    companies = db._execute_query(
        "SELECT id, name, tax_code, address, phone, email FROM public.invoice_companies "
            "WHERE COALESCE(is_customer, TRUE) = TRUE AND COALESCE(partner_type, 'organization') = 'organization' "
            "ORDER BY id",
        fetchall=True, commit=False,
    ) or []
    provinces = [row["name"] for row in db._execute_query(
        "SELECT name FROM public.administrative_provinces WHERE is_active", fetchall=True, commit=False,
    ) or []]
    return match_company_by_name(
        text, [company for company in companies if company["id"] != exclude_company_id], provinces)


def get_active_session(group_id: str) -> Optional[dict]:
    """Load current gathering session for group_id if still valid (its window has not run out)."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{group_id}.json"
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
        if session.get("status") == "collecting":
            elapsed = time.time() - session.get("started_at", 0)
            if elapsed <= SESSION_TIMEOUT_SECONDS + 60:
                return session
            session["status"] = "expired"
            path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return None


def save_session(session: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{session['group_id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")


def _read_waiting(group_id: str) -> dict:
    """The orders of a group still waiting for a tax code ({} when none, or when the wait ran out)."""
    try:
        waiting = json.loads((WAITING_DIR / f"{group_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return waiting if time.time() - waiting.get("last_message_at", 0) <= WAITING_SECONDS else {}


def _is_order_without_buyer(text: str) -> bool:
    """A freight price ("cước ... 8,5tr", "xuất hóa đơn ... xe chú Tư đi 5 triệu đã bao gồm vat") that is not one of the
    system's own notices."""
    folded = fold_text(text)
    return (not text.startswith(_BOT_NOTICE_MARKERS) and bool(_MILLION.search(text))
            and any(word in folded for word in ("cuoc", "van chuyen", "boc xep", "moi xe", "moi chuyen",
                                                "hoa don", "vat")))


def _ask_group(group_id: str, company_id: int | None, text: str) -> bool:
    """Post a question into the Zalo group through the bridge. False (logged) when it did not go out."""
    from frontend.modules.zalo.inbox import bridge_secret
    from frontend.modules.zalo.trip_requests import ensure_zalo_bridge
    port = ensure_zalo_bridge()
    if not port:
        return False
    response = httpx.post(
        f"http://127.0.0.1:{port}/send-invoice-notice", headers={"X-Zalo-Bridge-Secret": bridge_secret()},
        json={"group_id": group_id, "company_id": company_id, "text": text}, timeout=30)
    return response.status_code == 200 and bool(response.json().get("ok"))


async def _hold_for_tax_code(item: dict) -> None:
    """Keep an order that has no tax code and ask the group for it once; the reply opens the session with it."""
    group_id = str(item["group_id"])
    waiting = _read_waiting(group_id)
    asked_at = waiting.get("asked_at")
    if not asked_at and await asyncio.to_thread(_ask_group, group_id, item.get("company_id"), ASK_TAX_CODE):
        asked_at = time.time()
    WAITING_DIR.mkdir(parents=True, exist_ok=True)
    (WAITING_DIR / f"{group_id}.json").write_text(json.dumps({
        "message_ids": list(dict.fromkeys([*waiting.get("message_ids", []), *_preceding_places(item), item["id"]])),
        "last_message_at": time.time(),
        "asked_at": asked_at,
    }, ensure_ascii=False), encoding="utf-8")


# A Zalo location card links to Google Maps with its pin in the query: .../maps?q=20.83,106.08&entry=gps
_MAPS_PIN = re.compile(r"^https?://(?:www\.)?google\.com/maps\S*?(?:[?&]q=|@)(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
# "từ xã Hoàng An đến vị trí trên ...": where the goods are picked up, when it starts with a place word.
_STATED_ORIGIN = re.compile(
    r"\b(?:từ|tu)\s+((?:xã|xa|phường|phuong|thị trấn|thi tran|kcn|khu công nghiệp|cụm công nghiệp)\b.+?)"
    r"\s+(?:đến|den|đi|di|tới|toi|sang|về|ve)\b", re.I)
# "từ tề lỗ đến ...": the same, written without the place word.
_ROUTE_ORIGIN = re.compile(r"\b(?:từ|tu)\s+(.+?)\s+(?:đến|den|đi|di|tới|toi|sang|về|ve)\b", re.I)
# "đến vị trí trên" points at a location card sent just before the order text.
LOCATION_LOOKBACK_SECONDS = 180
IMAGE_LOOKBACK_SECONDS = 600
DOCUMENT_LOOKBACK_SECONDS = 600  # an old invoice sent up to 10 minutes before an order names its buyer
_COMMUNE_TYPES = (("xa",), ("phuong",), ("thi", "tran"), ("dac", "khu"))
_PROVINCE_TYPES = (("tinh",), ("thanh", "pho"))
_NO_DESTINATION = "Địa điểm giao hàng"
# "công ty này" / "cty đó" as a place: the buyer company itself, so its address
_THIS_COMPANY = re.compile(r"^(?:(?:chinh|ngay|tai)\s+)?(?:cong ty|cty)\s+(?:nay|do|ay)\b")
# "6.5 triệu đã bao gồm vat": the price is what the customer pays, tax inside ("chưa vat" / "không gồm vat" is not)
_VAT_INCLUDED = re.compile(
    r"\bda\s+(?:bao\s+gom\s+|gom\s+|co\s+)?(?:vat|thue)\b|\b(?:bao\s+gom|gom)\s+(?:ca\s+)?(?:vat|thue)\b")
_VAT_EXCLUDED = re.compile(r"\b(?:chua|khong)\s+(?:bao\s+)?(?:gom\s+|co\s+)?(?:ca\s+)?(?:vat|thue)\b")


def _net_of_total(total: float) -> int:
    """The goods amount whose 8% tax, in whole đồng, brings the invoice to exactly `total` (the nearest when none does)."""
    guess = round(total / 1.08)
    return min((guess, guess - 1, guess + 1), key=lambda net: abs(net + round(net * 0.08) - total))


def _price_includes_vat(text: str) -> bool:
    folded = fold_text(text)
    return bool(_VAT_INCLUDED.search(folded)) and not _VAT_EXCLUDED.search(folded)

# Where a place written in an order ends: the plate ("BSX: 19H-..."), the vehicle ("xe chú Tư", "xe bố") or the
# price ("giá 3.5tr", "500k") after it
_PLACE_END = (r"\.(?:\s|$)|;|\n|$|,?\s*(?:BSX|BKS|biển\s+số|bien\s+so)\b"
              r"|,?\s+xe\s+(?:của\s+|cua\s+)?(?:chú|chu|bác|bac|anh|em|ông|ong|bố|bo|mẹ|me)\b"
              r"|,?\s+(?:giá\s+|gia\s+)?(?:\d+(?:[.,]\d+)?\s*(?:triệu|tr)|\d{2,4}\s*(?:ngàn|nghìn|ngan|nghin|k))\b")
# "từ <điểm đi> đến <điểm đến> để phục vụ ...": the places of one trip, as the sender wrote them.
_LEG_ROUTE = re.compile(
    r"\b(?:từ|tu)\s+(?P<origin>.{3,150}?)\s+(?:đến|den)\s+(?P<dest>.{3,400}?)"
    r"(?=\s+(?:để|de|và|va)\s|" + _PLACE_END + ")",
    re.I | re.S)
# "vận chuyển 01 cont trong KCN Phú Hà, Phú Thọ": a trip inside one place, which is both its origin and destination
_WITHIN_PLACE = (r"(?:kcn|khu công nghiệp|cụm công nghiệp|ccn|xã|phường|thị trấn)\b.{2,150}?(?=" + _PLACE_END + ")")
_WITHIN_ROUTE = re.compile(r"\btrong\s+(?=(?P<origin>" + _WITHIN_PLACE + "))(?P<dest>" + _WITHIN_PLACE + ")", re.I)
# "Nơi đi: Thôn Vân Nội, Xã Hội Thịnh\nNơi đến: Nhà máy ..., Xã Phúc Thọ": the places of one trip, labelled
_LABELLED_ROUTE = re.compile(
    r"\b(?:nơi|noi|điểm|diem)\s+(?:đi|di)\s*[:：]?\s*(?P<origin>[^\n;]{3,150}?)\s*(?:\n|;)\s*"
    r"(?:nơi|noi|điểm|diem)\s+(?:đến|den)\s*[:：]?\s*(?P<dest>[^\n;]{3,400}?)\s*\.?\s*(?=\n|;|$)",
    re.I)
_PER_VEHICLE = re.compile(r"\b(?:moi|1|mot)\s+(?:xe|chuyen)\b")
# "Cước vận chuyển chặng khai quang Phú hà BKS 19B-..." on an invoice: the route of that trip
_DOCUMENT_ROUTE = re.compile(r"ch[ặa]ng\s+(.+?)(?=\s+(?:BKS|biển)\b|\n|$)", re.I)
_ROUTE_SEPARATOR = re.compile(r"\s*(?:\s-\s|–|—|→|\s(?:đi|đến|tới)\s)\s*", re.I)
_CARRIES = re.compile(r"\bchở\s+(.+)", re.I)


# "cẩu vận chuyển bàn cân từ …", "chở kết cấu bê tông, …": the goods in the sender's own words. The cargo catalogue
# only lists names already known; a trip may carry anything.
_CARGO_AFTER = re.compile(
    r"\b(?:vận chuyển|van chuyen|chở)\s+(?P<cargo>[^,;\n.]+?)"
    r"(?=\s+(?:từ|tu|đến|den|đi|di|tới|toi|cho|để|de|trên|trong|xe|cước|cuoc|với|và|va|về|ve)\b"
    r"|\s+\d+(?:[.,]\d+)?\s*(?:triệu|tr)\b|[,;\n.]|$)", re.I)
_NOT_CARGO = {"mot", "hai", "ba", "bon", "bo", "me", "chu", "bac", "anh", "em", "ong"}


def _stated_cargo(text: str) -> str:
    """The goods a text says it carries ("" when it names none; "chở một xe …" is a count of vehicles, not goods)."""
    for found in _CARGO_AFTER.finditer(text):
        # "vận chuyển cẩu hạ 01 cont": the crane work is the service, the goods are the container
        goods = _tidy_place(re.sub(r"^cẩu(?:\s+(?:hạ|nâng))?\s+", "", found.group("cargo").strip(), flags=re.I))
        if goods and fold_text(goods).split()[0] not in _NOT_CARGO:
            return goods
    return ""


def _tidy_place(text: str) -> str:
    """A place as typed in chat, with its spacing fixed: 'tại cum  ( HÀ NỘI )  , xã' -> 'Tại cum (HÀ NỘI), xã'."""
    text = re.sub(r"\s+", " ", text).strip(" ,.;-")
    text = re.sub(r"\s+([,.;)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return text[:1].upper() + text[1:]


def _card_coordinates(message: dict) -> tuple[float, float] | None:
    """Pin of a Zalo location card, read from its Google Maps link."""
    found = _MAPS_PIN.match(urllib.parse.unquote(str(message.get("attachment_url") or "")))
    if not found:
        return None
    lat, lon = float(found.group(1)), float(found.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _commune_directory() -> list[dict]:
    """Official communes with their province, the list the invoice form completes places from."""
    return load_commune_directory()


def _split_type(words: list[str], types: tuple) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """('xa', 'hoang', 'an') -> (('xa',), ('hoang', 'an')): the type word(s) of a place name, and the name."""
    for kind in types:
        if tuple(words[:len(kind)]) == kind and len(words) > len(kind):
            return kind, tuple(words[len(kind):])
    return (), tuple(words)


def with_province(place: str, directory: list[dict], hint: str = "") -> str:
    """Add the province to a place that names only a commune: 'xã Hoàng An' -> 'Xã Hoàng An, Tỉnh Phú Thọ'.

    The province comes from the official commune list. A commune name used in several provinces is settled by
    `hint` (the customer's address) or else left as written: a guessed province is worse than none.
    """
    text = " ".join(str(place or "").split())
    if not text or text.startswith(("http://", "https://")):
        return text
    tokens = [("".join(_fold_words(match.group())), match.start()) for match in re.finditer(r"\w+", text)]
    folded = [word for word, _ in tokens]
    for province in {row["province_name"] for row in directory}:
        _, bare = _split_type(_fold_words(province), _PROVINCE_TYPES)
        before = folded[-len(bare) - 1] if bare and len(folded) > len(bare) else ""
        # "xã Phú Thọ" is a commune named like a province; a province is not preceded by a commune type word
        if bare and tuple(folded[-len(bare):]) == bare and before not in {kind[-1] for kind in _COMMUNE_TYPES}:
            return text  # already ends with a province
    found: dict[str, tuple[dict, int]] = {}
    for row in directory:
        kind, bare = _split_type(_fold_words(row["commune_name"]), _COMMUNE_TYPES)
        start = len(folded) - len(bare)
        if not bare or start < 0 or tuple(folded[start:]) != bare:
            continue
        explicit = bool(kind) and tuple(folded[max(0, start - len(kind)):start]) == kind
        if explicit or (start == 0 and len(bare) >= 2):  # "đường Hoàng An" is a street, not the commune
            found.setdefault(row["province_name"], (row, start - len(kind) if explicit else start))
    if len(found) > 1 and hint:
        folded_hint = " ".join(_fold_words(hint))
        near = {name: hit for name, hit in found.items()
                if " ".join(_split_type(_fold_words(name), _PROVINCE_TYPES)[1]) in folded_hint}
        found = near or found
    if len(found) != 1:
        return text
    row, cut = next(iter(found.values()))
    head = text[:tokens[cut][1]].rstrip(" ,.;-") if cut > 0 else ""
    return ", ".join(part for part in (head, row["commune_name"], row["province_name"]) if part)


def _named_commune(texts: list[str], directory: list[dict], hint: str = "") -> str:
    """A pick-up place written without "xã" ("từ tề lỗ đến"), taken only when the official list knows the commune."""
    for found in filter(None, map(_ROUTE_ORIGIN.search, texts)):
        place = " ".join(found.group(1).split())
        completed = with_province(place, directory, hint)
        if completed != place:
            return completed
    return ""


def _sent_at(message: dict) -> float | None:
    try:
        return datetime.fromisoformat(str(message.get("sent_at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_session(group_id: str) -> dict:
    try:
        return json.loads((SESSIONS_DIR / f"{group_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _shares_pin(message: dict) -> bool:
    """A message that carries a pin: typed coordinates, a card with a Maps link, or a card with an address."""
    return bool(_COORD_RE.search(str(message.get("text") or "")) or _card_coordinates(message)
                or message.get("attachment_type") == "chat.location.new")


def _preceding_places(item: dict) -> list[str]:
    """Location cards and route messages ("Nơi đi …/Nơi đến …", "từ X đến Y") the same person sent just before `item`."""
    sent_at = _sent_at(item)
    if sent_at is None:
        return []
    used = set(_read_session(item["group_id"]).get("message_ids") or [])  # a finished session already used its cards
    cards = []
    for message in list_messages(limit=60):
        moment = _sent_at(message)
        text = str(message.get("text") or "")
        if (moment is None or message["id"] == item["id"] or message["id"] in used
                or message.get("group_id") != item["group_id"] or message.get("sender_id") != item.get("sender_id")
                or not sent_at - LOCATION_LOOKBACK_SECONDS <= moment <= sent_at
                or not (_shares_pin(message)
                        or (not _MILLION.search(text) and (_LEG_ROUTE.search(text) or _LABELLED_ROUTE.search(text))))):
            continue
        cards.append((moment, message["id"]))
    return [message_id for _, message_id in sorted(cards)]


def _messages_around_image(item: dict) -> list[str]:
    group_id = str(item.get("group_id") or "")
    sender_id = str(item.get("sender_id") or "")
    item_sent = _sent_at(item)
    threshold = (item_sent - IMAGE_LOOKBACK_SECONDS) if item_sent is not None else float("-inf")
    used = set(_read_session(group_id).get("message_ids") or [])

    collected: list[tuple[float, str]] = []
    if item.get("id"):
        collected.append((item_sent if item_sent is not None else 0.0, item["id"]))

    for msg in list_messages(limit=60):
        mid = msg.get("id")
        if not mid or mid == item.get("id") or mid in used:
            continue
        if str(msg.get("group_id") or "") != group_id:
            continue
        if str(msg.get("sender_id") or "") != sender_id:
            continue
        moment = _sent_at(msg)
        if moment is None or moment < threshold:
            continue
        collected.append((moment, mid))

    collected.sort(key=lambda x: x[0])
    seen: set[str] = set()
    result: list[str] = []
    for _, mid in collected:
        if mid not in seen:
            seen.add(mid)
            result.append(mid)
    return result


def _default_vehicle_id(seller_id: int | None, vehicles: list[dict]) -> int | None:
    """The vehicle of the seller's director, which carries the order unless the text names someone else's;
    the first vehicle on the list when the director owns none."""
    director = db.execute_query(
        "SELECT u.full_name FROM public.invoice_companies c JOIN public.users u ON u.id = c.director_user_id "
        "WHERE c.id = %s", (seller_id,), fetch=True) if seller_id else None
    name = fold_text(director["full_name"]) if director else ""
    owned = next((v for v in vehicles if name and fold_text(v.get("owner_name")) == name), None)
    chosen = owned or (vehicles[0] if vehicles else None)
    return chosen["id"] if chosen else None


def _run_async(coroutine):
    """Run a coroutine from sync code. A worker thread has its own event loop, whether or not the caller is inside one."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


async def _with_current_commune(place: str, directory: list[dict]) -> str:
    """'KCN Phú Hà, tỉnh Phú Thọ' -> 'KCN Phú Hà, Phường Phú Thọ, Tỉnh Phú Thọ': a place that names its province but no
    current ward/commune gets the one it lies in (Google finds the place, the official list names the ward). Left as it
    is when it already names a ward, names no province, or Google places it outside that province."""
    parts = [part.strip() for part in place.split(",") if part.strip()]
    if len(parts) < 2:
        return place
    written = _split_type(_fold_words(parts[-1]), _PROVINCE_TYPES)[1]
    province = next((row["province_name"] for row in directory
                     if _split_type(_fold_words(row["province_name"]), _PROVINCE_TYPES)[1] == written), "")
    if not province:
        return place
    communes = {_split_type(_fold_words(row["commune_name"]), _COMMUNE_TYPES)[1]
                for row in directory if row["province_name"] == province}
    if any(_split_type(_fold_words(part), _COMMUNE_TYPES)[1] in communes for part in parts[:-1]):
        return place
    found = await resolve_place(place)
    if not found:
        return place
    typed = _typed_place(*await reverse_geocode_admin_google(found["lat"], found["lon"]), directory)
    return ", ".join([*parts[:-1], typed]) if typed.endswith(f", {province}") else place


def _google_places(places: set[str], directory: list[dict]) -> dict[str, str]:
    """Each place with its names spelled the way Google knows them (see invoice_locations.google_normalized_place),
    and its current ward/commune when it names only a province."""
    async def normalized(place: str) -> str:
        return await _with_current_commune(await google_normalized_place(place, directory), directory)

    async def one(place: str) -> tuple[str, str]:
        try:
            return place, await asyncio.wait_for(normalized(place), 25)
        except Exception:
            return place, place

    async def every() -> list[tuple[str, str]]:
        return list(await asyncio.gather(*(one(place) for place in places if place != _NO_DESTINATION)))

    return dict(_run_async(every()))


def _route_distances(pairs: list[tuple[str, str]]) -> list[float | None]:
    """Driving km of each (from, to) pair by the Google Routes API; None where Google cannot work a route out."""
    async def one(start: str, end: str) -> float | None:
        try:
            km, _ = await asyncio.wait_for(route_distance_km(start, end), 25)
        except Exception:
            return None
        return round(float(km), 2) if km is not None and math.isfinite(float(km)) and km > 0 else None

    async def every() -> list[float | None]:
        return list(await asyncio.gather(*(one(start, end) for start, end in pairs)))

    return _run_async(every())


def _fill_distance_and_fuel(lines: list[dict], vehicles: list[dict]) -> None:
    """Like the invoice form: the km of each line, and from them the litres and cost of the fuel its vehicle burns."""
    todo = [line for line in lines if line["origin"] and line["destination"] != _NO_DESTINATION
            and not same_place(line["origin"], line["destination"])]
    pairs = [(line["origin"], line["destination"]) for line in todo]
    price = None
    for line, km in zip(todo, _route_distances(pairs) if pairs else []):
        if km is None:
            continue
        line["distance_km"] = Decimal(str(km))
        vehicle = next((v for v in vehicles if v["id"] == line["vehicle_id"]), None)
        if not vehicle or vehicle.get("fuel_consumption") is None:
            continue
        line["fuel_liters"] = liters_for_distance(line["distance_km"], vehicle["fuel_consumption"])
        if price is None:
            from frontend.modules.invoices.bkav_sync import _fuel_price_for_date
            price = _fuel_price_for_date(date.today())
        if price is not None:
            line["fuel_price_per_liter"] = price
            line["fuel_cost_total"] = (line["fuel_liters"] * price).quantize(Decimal("0.01"))


def _split_priced_lines(message: dict) -> list[dict]:
    """A message listing several priced trips ("1. ... 6.5 triệu  2. ... 6.5 triệu") reads as one message per trip.
    Each trip keeps the lines written above its price ("Dịch vụ vận chuyển … từ A đến B" over "1 chuyến giá 3.5tr")."""
    trips, above = [], []
    for line in (line.strip() for line in str(message.get("text") or "").split("\n")):
        if not line:
            continue
        above.append(line)
        if _MILLION.search(line):
            trips.append("\n".join(above))
            above = []
    return [{**message, "text": trip} for trip in trips] if len(trips) > 1 else [message]


# ", BSX: 19H-209.07" / ", BSX: chú Tư" in an order: it says which vehicle carries the trip, it is not part of the
# invoice line
_WRITTEN_PLATE = re.compile(
    r"[,;]?\s*(?:(?:BSX|BKS|biển\s+số(?:\s+xe)?|bien\s+so(?:\s+xe)?|xe)\s*[:：]?\s*)?\b\d{2}\s*[A-Z]{1,2}\d?\s*-?\s*\d{3}\.?\d{2}\b"
    r"|[,;]?\s*(?:BSX|BKS|biển\s+số(?:\s+xe)?|bien\s+so(?:\s+xe)?)\s*[:：]?\s*"
    r"(?:chú|chu|bác|bac|anh|em|ông|ong|bố|bo|mẹ|me)\b(?:\s+[^\W\d_]+)?",
    re.I)


def _written_line(text: str) -> str:
    """The invoice line an order already spells out: "Dịch vụ vận chuyển … BSX:19H-209.07" above "1 chuyến giá 3.5tr"
    goes on the invoice word for word, without the plate. "" when the first line is not one ("Dịch vụ"/"Cước" first,
    the price below it)."""
    lines = [line.strip() for line in str(text or "").split("\n") if line.strip()]
    if len(lines) >= 2 and fold_text(lines[0]).startswith(("dich vu", "cuoc")) and not _MILLION.search(lines[0]):
        return " ".join(_WRITTEN_PLATE.sub("", lines[0]).split()).strip(" ,;-")
    return ""


def _with_saved_places(line: str, origin: str, destination: str) -> str:
    """The sender's invoice line with its places as the invoice form saves them (the form rewrites "từ … đến …" with
    the normalized places): "… từ Hương Sơn, Phú Thọ đến KCN Phú Hà, Phú Thọ" -> "… từ Hương Sơn, Tỉnh Phú Thọ đến …"."""
    route = _LEG_ROUTE.search(line)
    if route:
        return f"{line[:route.start()]}từ {origin} đến {destination}{line[route.end():]}"
    within = _WITHIN_ROUTE.search(line)
    if within:
        return f"{line[:within.start()]}trong {destination}{line[within.end():]}"
    return line


def _document_route(messages: list[dict]) -> tuple[str, str] | None:
    """The route an invoice sent with the order was written for: "chặng khai quang Phú hà" -> ("Khai Quang", "Phú Hà").
    The two places split at a dash/"đi"/"đến", or, written without one, two words each. None when it cannot tell."""
    for message in messages:
        found = _DOCUMENT_ROUTE.search(str(message.get("ocr_text") or ""))
        if not found:
            continue
        written = " ".join(found.group(1).split()).strip(" .,;-")
        parts = [part for part in _ROUTE_SEPARATOR.split(f" {written} ") if part.strip()]
        if len(parts) != 2:
            words = written.split()
            if len(words) != 4:
                continue
            parts = [" ".join(words[:2]), " ".join(words[2:])]
        origin, destination = (" ".join(w[:1].upper() + w[1:] for w in part.split()) for part in parts)
        return origin, destination
    return None


def extract_multiline_invoice_data(messages: list[dict], seller_company_id: int | None = None,
                                   buyer_company: dict | None = None, directory: list[dict] | None = None) -> dict:
    """Parse messages from Zalo group and extract complete multi-line invoice payload matching standard schema.

    `buyer_company` is the customer a session was opened for by name; it is the buyer only when no text has a tax code.
    `directory` is the official commune list places get their province from (loaded when omitted).
    The cargo is the goods the sender names after "vận chuyển" / "chở": no catalogue is consulted, a trip may carry anything.
    A delivery place always comes from Google Geocoding (card address -> coordinates -> current ward/commune
    and province); a Google error is not caught here.
    """
    texts = [str(m.get("text") or "").strip() for m in messages]
    all_text = " \n ".join(texts)

    # 1. Tax code & Buyer Company
    matched_comp = None
    tax_codes = re.findall(r"\b\d{10}(?:-\d{3})?\b", all_text)
    if tax_codes:
        matched_comp = extract_matching_company(tax_codes[0])
    elif buyer_company:
        matched_comp = buyer_company

    buyer_id = matched_comp["id"] if matched_comp else None
    buyer_name = matched_comp["name"] if matched_comp else ""
    buyer_tax = matched_comp["tax_code"] if matched_comp else (tax_codes[0] if tax_codes else "")
    buyer_addr = matched_comp.get("address", "") if matched_comp else ""

    # 2. Seller Company (from messages or default)
    seller_id = seller_company_id or messages[0].get("company_id")
    if not seller_id:
        seller_id = 33  # CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH default

    # 3. Origin: where the text says the goods are picked up ("từ xã Hoàng An đến ..."), always with its province.
    # A text that says none leaves it empty: the group is asked, no place is assumed.
    if directory is None:
        directory = _commune_directory()
    stated = next((found.group(1) for found in map(_STATED_ORIGIN.search, texts) if found), "")
    origin = ""
    named = "" if stated else _named_commune(texts, directory, buyer_addr)
    if stated:
        origin = with_province(stated[0].upper() + stated[1:], directory, buyer_addr)
    elif named:
        origin = named
    else:
        for t in texts:
            if "phu tho" in t.lower() or "vinh phuc" in t.lower() or "vinh yen" in t.lower():
                # Check if text contains "phường Vĩnh Yên, tỉnh Phú Thọ"
                m_orig = re.search(r"((?:phường|xã|thị trấn)\s+[^,]+,\s*(?:tỉnh|thành phố)\s+[^,\.]+)", t, re.I)
                if m_orig:
                    origin = m_orig.group(1).strip(" .,-")
                    break

    # 4. Vehicle: the seller director's, unless the price's own message names someone else's ("xe chú Tư").
    all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
    default_vehicle_id = _default_vehicle_id(seller_id, all_vehicles)
    from frontend.modules.transport.trips_controller import _find_vehicle_by_nickname

    def vehicle_of(nickname: str) -> int | None:
        """The vehicle of the owner a nickname names ("Tư" is chú Tư's). Any other vehicle a text names is the seller
        director's (Nguyễn Kim Quyết), the default; nothing more is checked."""
        found = _find_vehicle_by_nickname(nickname, all_vehicles) if nickname else None
        return found["id"] if found else default_vehicle_id

    def named_vehicle(text: str) -> tuple[str, int | None]:
        plated = vehicle_with_plate_in_text(text, all_vehicles)
        if plated:
            return plated["plate_number"], plated["id"]
        title, name = vehicle_owner_in_text(text)
        nickname = f"{title} {name}".strip()
        return nickname, vehicle_of(nickname)

    # 5. Cargo & Service
    # The goods the senders themselves name in this session ("vận chuyển bàn cân", "chở quả chuẩn m1"): a message that
    # names them without those verbs ("... xe của bố chừa quả chuẩn từ ...") is read by those same words.
    session_goods = sorted({goods for text in texts for line in text.splitlines() if (goods := _stated_cargo(line))},
                           key=len, reverse=True)

    def cargo_in(text: str) -> str:
        """The goods a text names: its own words after "vận chuyển" / "chở", else goods named elsewhere in the session."""
        return _stated_cargo(text) or next((goods for goods in session_goods if fold_text(goods) in fold_text(text)), "")

    cargo = next(filter(None, (_stated_cargo(text) for text in texts if _MILLION.search(text))), "")
    service = "Cước cẩu, vận chuyển"
    if any(k in all_text.lower() for k in ("cước cẩu", "cước cau", "vận chuyển")):
        service = "Cước cẩu, vận chuyển"

    def names_buyer(place: str) -> bool:
        return bool(_THIS_COMPANY.match(fold_text(place))
                    or (matched_comp and match_company_by_name(place, [matched_comp])))

    def route_places(route) -> tuple[str, str, bool]:
        leg_origin = _tidy_place(route.group("origin"))
        if buyer_addr and names_buyer(leg_origin):
            leg_origin = buyer_addr
        else:
            leg_origin = with_province(leg_origin, directory, buyer_addr)
        text_destination = _tidy_place(route.group("dest"))
        if buyer_addr and names_buyer(text_destination):
            text_destination = buyer_addr
        points_up = False
        if fold_text(text_destination).startswith("vi tri"):
            text_destination = ""  # "đến vị trí trên" points at a location card
            points_up = True
        return leg_origin, text_destination, points_up

    def assignments() -> list[dict]:
        asg = []
        seen_nicks = set()
        for m in messages:
            t = str(m.get("text") or "").strip()
            if _MILLION.search(t):
                continue
            vehicle_nick, vehicle_id = named_vehicle(t)
            if not vehicle_nick:
                continue
            carried = _CARRIES.search(t)
            if not carried:
                continue
            after = carried.group(1)
            cargo_name = cargo_in(f"chở {after}")
            if not cargo_name:
                continue
            fnick = fold_text(vehicle_nick)
            if fnick in seen_nicks:
                continue
            seen_nicks.add(fnick)
            asg.append({
                "vehicle_nick": vehicle_nick,
                "vehicle_id": vehicle_id,
                "cargo": cargo_name,
            })
        return asg

    # 6. Extract Legs / Segments: a pin (typed coordinates or a location card) is the destination of the nearest
    # price, whether it was sent before or after that price
    legs = []
    waiting_place = ""
    last_place = ""  # the latest pin above the message being read: "đến vị trí trên" points at it
    last_route: tuple[str, str, bool] | None = None
    last_written = ""  # the invoice line the order of last_route spells out, for a price sent after it
    for m in (part for message in messages for part in _split_priced_lines(message)):
        t = str(m.get("text") or "").strip()
        located = message_place(m, directory)
        place = located[1] if located else ""
        if place:
            last_place = place
            open_leg = next((leg for leg in legs if not leg["destination"]), None)
            if open_leg:
                open_leg["destination"] = place
            else:
                waiting_place = place
        pm = _MILLION.search(t)
        route = _LEG_ROUTE.search(t) or _LABELLED_ROUTE.search(t) or _WITHIN_ROUTE.search(t)
        if not pm and route:
            last_route = route_places(route)
            last_written = _written_line(t)
        if pm:
            amt = float(million_price(pm))
            vehicle_nick, vehicle_id = named_vehicle(t)
            document_route = None
            if route:
                leg_origin, text_destination, points_up = route_places(route)
            elif last_route:
                leg_origin, text_destination, points_up = last_route
            elif "tuong tu" in fold_text(t) and _document_route(messages):
                # "xuất hóa đơn về công ty trên chặng đường tương tự": the route of the invoice sent with the order.
                # A name the commune list does not know keeps the buyer's province, so Google looks for it there.
                province = re.search(r"(?:Tỉnh|Thành phố)\s+[^,.]+", buyer_addr or "")
                def in_province(name: str) -> str:
                    known = with_province(name, directory, buyer_addr)
                    return known if known != name or not province else f"{name}, {province.group(0).strip()}"
                document_route = _document_route(messages)
                leg_origin, text_destination = (in_province(name) for name in document_route)
                points_up = False
            else:
                leg_origin = text_destination = ""
                points_up = False
            legs.append({
                "destination": waiting_place or (last_place if points_up else ""),
                "origin": leg_origin,
                "text_destination": text_destination,
                "amount": amt,
                "vat_included": _price_includes_vat(t),
                "cargo": cargo_in(t),
                "text": t,
                "vehicle_nick": vehicle_nick,
                "vehicle_id": vehicle_id,
                "sender": str(m.get("sender_name") or ""),
                "document_route": document_route,
                # "3.500.000 chưa vat" answering an order above takes that order's own words
                "written": _written_line(t) or (last_written if not route and not document_route else ""),
            })
            waiting_place = ""  # reset for next leg
    for leg in legs:  # a pin wins over the place written in the text
        leg["destination"] = leg["destination"] or leg["text_destination"] or _NO_DESTINATION
    if len(legs) == 1 and _PER_VEHICLE.search(fold_text(legs[0]["text"])):
        asg = assignments()
        if len(asg) >= 2:
            base_leg = legs[0]
            legs = [
                {**base_leg, "vehicle_nick": a["vehicle_nick"], "vehicle_id": a["vehicle_id"], "cargo": a["cargo"]}
                for a in asg
            ]
    if len(legs) == 1 and not legs[0]["vehicle_nick"]:
        # A single trip takes the vehicle any other message of the session names; with several trips, which one
        # is meant is unknown, so each keeps the default.
        for t in texts:
            vehicle_nick, vehicle_id = named_vehicle(t)
            if vehicle_nick:
                legs[0]["vehicle_nick"], legs[0]["vehicle_id"] = vehicle_nick, vehicle_id
                break
    open_legs = [leg for leg in legs if not leg["vehicle_nick"]]
    if len(open_legs) > 1:
        # "2 cái xe mình 1 xe chu tu" for three trips: it does not say which, so the vehicles go to the trips at random.
        for t in texts:
            split = vehicle_split_in_text(t)
            if split and sum(count for count, _ in split) == len(open_legs):
                owners = [nickname for count, nickname in split for _ in range(count)]
                random.shuffle(owners)
                for leg, nickname in zip(open_legs, owners):
                    leg["vehicle_nick"], leg["vehicle_id"] = nickname, vehicle_of(nickname)
                break

    # No vehicle named: the trip is the sender's own ("Nguyễn Kim Quyết" owns 19H-147.98, "Chú Tư" owns 19H-209.07).
    # A sender who owns none (the office) leaves the seller director's vehicle.
    for leg in legs:
        if not leg["vehicle_nick"]:
            owned = _find_vehicle_by_nickname(leg["sender"], all_vehicles)
            if owned:
                leg["vehicle_id"] = owned["id"]

    # An order sent again (by another person, or the same one) is one trip: the same vehicle, route, cargo and price
    # are written once.
    # The goods may be worded again, fuller or shorter ("bàn cân 4500x10m", then "bàn cân" once the group asked for
    # fewer details): still the same trip, and the latest wording stands.
    seen_trips: list[tuple] = []
    distinct = []
    for leg in legs:
        trip = (leg["vehicle_id"], leg["amount"], leg["vat_included"], leg["origin"] or origin, leg["destination"])
        goods = (leg["cargo"] or cargo).casefold()
        for index, (seen, seen_goods) in enumerate(seen_trips):
            if seen == trip and (goods == seen_goods or goods and seen_goods and (goods in seen_goods or seen_goods in goods)):
                if goods != seen_goods:
                    distinct[index] = leg
                    seen_trips[index] = (trip, goods)
                break
        else:
            seen_trips.append((trip, goods))
            distinct.append(leg)
    legs = distinct

    # A trip with no pick-up or drop-off is asked about, never given a default one.
    missing_places = [name for name, absent in (
        ("điểm đi", any(not (leg["origin"] or origin) for leg in legs)),
        ("điểm đến", any(leg["destination"] == _NO_DESTINATION for leg in legs))) if absent]

    # Prices that include the 8% tax: each trip's goods are its price / 1.08, the lines together adding up to the
    # whole invoice's price / 1.08, so the tax BKAV and the invoice form work out on the total is the price paid
    inclusive = [leg for leg in legs if leg["vat_included"]]
    if inclusive:
        nets = [round(leg["amount"] / 1.08) for leg in inclusive]
        nets[-1] += _net_of_total(sum(leg["amount"] for leg in inclusive)) - sum(nets)
        for leg, net in zip(inclusive, nets):
            leg["net"] = net

    # Build bkav_invoice_lines
    # The same normalizing the invoice form calls, so a place is saved as "… Xã Bình Nguyên Tỉnh Phú Thọ", and a bare
    # name with a typo as the name and address Google knows it by.
    # A route copied from the invoice sent with the order is kept as written there (no renaming).
    places = [(leg["origin"], leg["destination"]) if leg.get("document_route") else
              (normalize_invoice_location(leg["origin"] or origin, directory),
               normalize_invoice_location(leg["destination"], directory)) for leg in legs]
    google_names = _google_places({place for leg, pair in zip(legs, places) if not leg.get("document_route")
                                   for place in pair}, directory)
    lines = []
    for idx, (leg, (leg_origin, dest)) in enumerate(zip(legs, places), 1):
        amt = leg["amount"]
        leg_origin, dest = google_names.get(leg_origin, leg_origin), google_names.get(dest, dest)
        if leg["vat_included"]:  # the price is the amount to pay: the goods are worked back from it
            amt = float(leg["net"])
        leg_cargo = leg["cargo"] or cargo
        crane_job = same_place(leg_origin, dest)  # điểm đi trùng điểm đến: ca cẩu
        route_words = f"tại {dest}" if crane_job else f"từ {leg_origin} đi {dest}"
        desc = " ".join(part for part in (CRANE_BY_SHIFT_SERVICE if crane_job else service,
                                          leg_cargo[:1].lower() + leg_cargo[1:], route_words) if part)  # "(Code: K2-04)" kept as written
        if leg.get("document_route"):  # "Cước vận chuyển chặng Khai Quang - Phú Hà", as that invoice says
            desc = "Cước vận chuyển chặng {} - {}".format(*leg["document_route"])
        if leg.get("written"):  # the sender wrote the invoice line out: it goes in as written, its places normalized
            desc = _with_saved_places(leg["written"], leg_origin, dest)
        lines.append({
            "line_number": idx,
            "vehicle_id": leg["vehicle_id"],
            "cargo_description": leg_cargo,
            "cargo_weight_tons": None,
            "origin": leg_origin,
            "destination": dest,
            "amount": amt,
            "tax_amount": 0,  # worked out below, as BKAV does
            "description": desc,
            "via_points": [],
        })

    from frontend.modules.invoices.bkav_push import whole_dong_taxes
    for line, tax in zip(lines, whole_dong_taxes([Decimal(str(line["amount"])) for line in lines], Decimal("0.08"))):
        line["tax_amount"] = int(tax)

    _fill_distance_and_fuel(lines, all_vehicles)
    for line, leg in zip(lines, legs):
        if leg.get("document_route"):  # measured with the province; saved with the names as written
            line["origin"], line["destination"] = leg["document_route"]

    subtotal = sum(l["amount"] for l in lines) if lines else 0.0
    vat_rate = 0.08
    gross = round(sum(l["amount"] + l["tax_amount"] for l in lines))
    debt_amount = gross

    return {
        "seller_company_id": seller_id,
        "buyer_company_id": buyer_id,
        "buyer_name": buyer_name,
        "buyer_tax_code": buyer_tax,
        "invoice_date": date.today().isoformat(),
        "service_name": CRANE_BY_SHIFT_SERVICE if lines and all(same_place(l["origin"], l["destination"]) for l in lines) else service,
        "vat_rate": vat_rate,
        "payment_method": "CK",
        "payment_status": "debt",
        "debt_amount": debt_amount,
        "subtotal": subtotal,
        "gross": gross,
        "line_count": len(lines),
        "missing_places": missing_places,
        "vehicle_nickname": next((leg["vehicle_nick"] for leg in legs if leg["vehicle_nick"]), ""),
        "lines": lines,
    }


def create_bkav_invoice_from_data(payload: dict) -> str:
    """Save the order as a sales invoice, numbered and posted to the books like one typed by hand; returns its guid."""
    from frontend.modules.transport.invoice_store import save_sales_invoice
    saved = save_sales_invoice({**payload, "invoice_number": None, "invoice_serial": None}, guid=str(uuid4()))
    logger.info("Created invoice %s (No. %s-%s) from Zalo messages", saved.guid, saved.invoice_number, saved.invoice_serial)
    return saved.guid


def push_invoice_to_bkav(invoice_guid: str) -> tuple[bool, str]:
    """Create the saved invoice as a draft on BKAV, as its 'Gửi BKAV' button does.
    (True, "") when it is there; (False, "") when BKAV is not set up; (False, reason) when it did not go through."""
    from frontend.modules.invoices.bkav_push import BkavConfigurationError, push_saved_invoice
    from frontend.modules.invoices.companies import get_company
    try:
        invoice = db._execute_query("SELECT * FROM public.bkav_invoices WHERE invoice_guid = %s",
                                    (invoice_guid,), fetch=True, commit=False)
        lines = db._execute_query("""
            SELECT line.*, vehicle.plate_number AS plate_number
            FROM public.bkav_invoice_lines line
            LEFT JOIN public.plate_whitelist vehicle ON vehicle.id = line.vehicle_id
            WHERE line.invoice_guid = %s ORDER BY line.line_number
        """, (invoice_guid,), fetchall=True, commit=False) or []
        seller = get_company(invoice["seller_company_id"])
        buyer = get_company(invoice["buyer_company_id"]) if invoice.get("buyer_company_id") else None
        if not buyer:
            return False, "chưa có thông tin bên mua"
        result = push_saved_invoice(dict(invoice), [dict(line) for line in lines], buyer, seller)
    except BkavConfigurationError as exc:
        logger.info("Invoice %s not sent to BKAV: %s", invoice_guid, exc)
        return False, ""
    logger.info("Invoice %s sent to BKAV as a draft (%s)", invoice_guid, result.get("status"))
    return True, ""


def _bkav_invoice_picture(invoice_guid: str, attempts: int = 3, wait_seconds: float = 10) -> Path | None:
    """The invoice picture BKAV itself renders (the system's "Ảnh hóa đơn"); a fresh draft may need a few seconds."""
    from frontend.modules.invoices.bkav_sync import get_or_fetch_bkav_invoice_files
    for attempt in range(attempts):
        try:
            png_path, _ = get_or_fetch_bkav_invoice_files(invoice_guid, force_refresh=True)
            if png_path and Path(png_path).exists():
                return Path(png_path)
        except Exception as exc:
            logger.info("Invoice %s: BKAV picture not ready (%s)", invoice_guid, exc)
        if attempt < attempts - 1:
            time.sleep(wait_seconds)
    return None


def send_invoice_image_to_ai_group(invoice_guid: str) -> bool:
    """After a draft reaches BKAV, the picture BKAV renders of it goes to the AI group for the owner to check.
    No picture is drawn by the system (owner's rule 2026-11-09); when BKAV has none, nothing is sent. Never raises."""
    from frontend.modules.zalo.trip_requests import _bridge_post, list_zalo_groups
    image = _bkav_invoice_picture(invoice_guid)
    if image is None:
        logger.warning("Invoice %s: BKAV gave no picture; nothing sent to the AI group.", invoice_guid)
        return False
    try:
        name = (INBOX_DIR / "ai-group-name").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    name = (name or "AI").casefold()
    groups = _run_async(list_zalo_groups())
    group_id = next((str(group["group_id"]) for group in groups
                     if str(group.get("name") or "").strip().casefold() == name), "")
    if not group_id:
        logger.warning("Invoice %s: the AI group %r is not among the Zalo groups; no picture sent.", invoice_guid, name)
        return False
    _run_async(_bridge_post("/send-invoice-file", {
        "group_id": group_id, "file_path": str(Path(image).resolve())}))
    return True


def _session_buyer(session: dict) -> Optional[dict]:
    """The customer a session opened by name, photo or invoice document was built around; its texts carry no tax code."""
    if session.get("matched_by") not in ("name", "image", "document") or not session.get("company_id"):
        return None
    return {"id": session["company_id"], "name": session.get("company_name") or "",
            "tax_code": session.get("tax_code") or "", "address": session.get("address") or ""}


def finalize_invoice_session_sync(group_id: str) -> Optional[dict]:
    """Group all messages in the session, build trip draft, save invoice in database, and link URLs."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{group_id}.json"
    with _FINALIZE_LOCK:
        if not path.exists():
            return None
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if session.get("status") not in ("collecting", "ready_to_finalize"):
            return None
        session["status"] = "finalizing"
        save_session(session)

    message_ids = session.get("message_ids") or []
    if not message_ids:
        session["status"] = "cancelled"
        save_session(session)
        return None

    messages = [get_message(mid) for mid in message_ids]
    messages = [m for m in messages if m is not None]
    if not messages:
        session["status"] = "cancelled"
        save_session(session)
        return None

    from frontend.modules.zalo.trip_requests import build_trip_draft
    from frontend.modules.administrative_divisions import list_provinces
    from frontend.modules.transport.catalog import list_items as list_catalog_items

    try:
        directory = _commune_directory()
        provinces = [row["name"] for row in list_provinces()]
        # No web request says which company is working here, so name the seller whose catalogue to read.
        with company_context(messages[0].get("company_id")):
            services = [row["name"] for row in list_catalog_items("service")]

        draft = build_trip_draft(
            messages,
            provinces=provinces,
            services=services,
            directory=directory,
        )
    except Exception as exc:
        logger.exception("Failed to build trip draft for group %s: %s", group_id, exc)
        session["status"] = "error"
        session["error"] = str(exc)
        save_session(session)
        return None

    # A session opened by company name has no tax code in its texts: the named company is the buyer.
    buyer = _session_buyer(session)
    if buyer and "Mã số thuế bên mua" in draft["missing"]:
        draft.update(
            company_id=buyer["id"], company_name=buyer["name"],
            buyer_company_id=buyer["id"], buyer_company_name=buyer["name"], tax_code=buyer["tax_code"],
        )
        draft["missing"] = [item for item in draft["missing"] if item != "Mã số thuế bên mua"]

    # Multi-line invoice extraction
    try:
        invoice_payload = extract_multiline_invoice_data(
            messages, seller_company_id=draft.get("seller_company_id"), buyer_company=buyer, directory=directory)
    except Exception as exc:
        logger.exception("Failed to extract invoice lines for group %s: %s", group_id, exc)
        session["status"] = "error"
        session["error"] = str(exc)
        save_session(session)
        return None
    lines = invoice_payload.get("lines") or []
    missing_places = invoice_payload.get("missing_places") or []
    if lines and not draft.get("cargo"):  # the goods are named by the order's own words
        draft["cargo"] = lines[0]["cargo_description"]
        draft["missing"] = [item for item in draft.get("missing", []) if item != "Loại hàng hóa"]

    # Hand the trip form the same places the invoice carries: the commune with its province, the pin's address.
    draft["origin"] = with_province(draft.get("origin") or "", directory, session.get("address") or "")
    if lines and draft.get("service") and not draft.get("source_note") and lines[0]["destination"] != _NO_DESTINATION:
        draft["note"] = " ".join(part for part in (
            draft["service"], (draft.get("cargo") or "").casefold(),
            "từ", lines[0]["origin"], "đến", lines[0]["destination"]) if part)

    invoice_guid = None
    # An invoice line needs a vehicle: when the text names one the list cannot identify, leave it to a person.
    needs_vehicle = any(line["vehicle_id"] is None for line in lines)
    if needs_vehicle:
        logger.warning("Group %s: vehicle %r is not in the vehicle list; no invoice created.",
                       group_id, invoice_payload.get("vehicle_nickname"))
    elif lines and missing_places:
        logger.warning("Group %s: the order has no %s; no invoice created.", group_id, " and ".join(missing_places))
    elif lines:
        invoice_guid = create_bkav_invoice_from_data(invoice_payload)
    missing_for_invoice = []
    if not lines:
        missing_for_invoice.append("số tiền cước")
    elif needs_vehicle:
        nickname = invoice_payload.get("vehicle_nickname")
        missing_for_invoice.append(f"xe vận chuyển (chưa nhận ra xe \"{nickname}\")" if nickname else "xe vận chuyển")
    elif missing_places:
        missing_for_invoice.append(" và ".join(missing_places))
    if missing_for_invoice:
        buyer_name = invoice_payload.get("buyer_name") or session.get("company_name") or "bên mua"
        _ask_group(group_id, messages[0].get("company_id"),
                   f"🤖 Chưa lập được hóa đơn cho {buyer_name}: còn thiếu {', '.join(missing_for_invoice)}. "
                   "Anh/chị gửi bổ sung giúp.")
    if (not lines or missing_places) and not needs_vehicle:
        # The price or places sent next open this order again, with its buyer and every message so far.
        WAITING_DIR.mkdir(parents=True, exist_ok=True)
        (WAITING_DIR / f"{group_id}.json").write_text(json.dumps({
            "message_ids": message_ids, "last_message_at": time.time(), "asked_at": time.time(),
            "matched_by": session.get("matched_by"), "missing": "price" if not lines else "places",
            "buyer": {"id": session.get("company_id"), "name": session.get("company_name") or "",
                      "tax_code": session.get("tax_code") or "", "address": session.get("address") or ""},
        }, ensure_ascii=False), encoding="utf-8")

    draft_id = f"draft_{group_id}_{int(time.time())}"
    draft["draft_id"] = draft_id
    draft["group_id"] = group_id
    draft["created_at"] = datetime.now(timezone.utc).isoformat()
    draft["session_messages_count"] = len(messages)
    draft["invoice_guid"] = invoice_guid
    draft["invoice_url"] = f"/admin/vehicles/cargo/invoices/{invoice_guid}/edit" if invoice_guid else ""
    draft["lines"] = invoice_payload.get("lines", [])
    draft["subtotal"] = invoice_payload.get("subtotal", draft.get("amount", 0))
    draft["gross"] = invoice_payload.get("gross", draft.get("amount", 0))

    # Save draft file
    draft_path = DRAFTS_DIR / f"{draft_id}.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, default=str), encoding="utf-8")

    # Update session status
    session["status"] = "completed"
    session["draft_id"] = draft_id
    session["invoice_guid"] = invoice_guid
    session["invoice_url"] = draft["invoice_url"]
    session["completed_at"] = time.time()
    save_session(session)

    # Update individual messages with invoice draft link
    for m in messages:
        mid = m["id"]
        m_path = INBOX_DIR / f"{mid}.json"
        if m_path.exists():
            try:
                m_data = json.loads(m_path.read_text(encoding="utf-8"))
                m_data["invoice_draft"] = {
                    "draft_id": draft_id,
                    "invoice_guid": invoice_guid,
                    "invoice_url": draft["invoice_url"],
                    "company_name": invoice_payload.get("buyer_name") or draft.get("company_name") or session.get("company_name", ""),
                    "amount": invoice_payload.get("gross", draft.get("amount", 0)),
                    "subtotal": invoice_payload.get("subtotal", 0),
                    "line_count": len(invoice_payload.get("lines", [])),
                    "vehicle": draft.get("vehicle_nickname", ""),
                    "destination": lines[0]["destination"] if lines else draft.get("destination", ""),
                    "state": "needs_vehicle" if needs_vehicle else "needs_places" if missing_places else "ready",
                    "missing": missing_places,
                }
                m_path.write_text(json.dumps(m_data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    logger.info("Successfully finalized invoice session for group %s -> draft %s (Invoice: %s)", group_id, draft_id, invoice_guid)

    if invoice_guid:
        # The order is not typed in again on BKAV: its draft is created here, ready to be signed. This runs last, so an
        # invoice already saved is never left unrecorded (session 'finalizing', messages unmarked) when BKAV fails.
        pushed, problem = push_invoice_to_bkav(invoice_guid)
        if problem:
            buyer_name = invoice_payload.get("buyer_name") or session.get("company_name") or "bên mua"
            _ask_group(group_id, messages[0].get("company_id"),
                       f"🤖 Đã lập hóa đơn cho {buyer_name} trong hệ thống nhưng chưa tạo được bản nháp trên BKAV: {problem}")
        elif pushed:
            send_invoice_image_to_ai_group(invoice_guid)
    return draft


async def _delay_and_finalize(group_id: str, scheduled_for: float) -> None:
    """The group's one waiter: finalize the session SESSION_TIMEOUT_SECONDS after it was opened."""
    try:
        while True:
            remaining = scheduled_for - time.time()
            if remaining > 0:
                await asyncio.sleep(min(remaining, 5.0))
                session = get_active_session(group_id)
                if not session or session.get("status") != "collecting":
                    return
                scheduled_for = session.get("started_at", 0) + SESSION_TIMEOUT_SECONDS
                continue
            await asyncio.to_thread(finalize_invoice_session_sync, group_id)
            # A message that opened a new session while this one was being finalized needs its own wait.
            session = _read_session(group_id)
            if session.get("status") != "collecting":
                return
            scheduled_for = session.get("started_at", 0) + SESSION_TIMEOUT_SECONDS
    finally:
        if _FINALIZE_WAITERS.get(group_id) is asyncio.current_task():
            del _FINALIZE_WAITERS[group_id]


def schedule_session_finalization(group_id: str, scheduled_for: float) -> None:
    """Start the group's waiter unless one is running: it re-reads the session, so later messages extend its wait."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    waiter = _FINALIZE_WAITERS.get(group_id)
    if waiter and not waiter.done() and waiter.get_loop() is loop:
        return
    _FINALIZE_WAITERS[group_id] = loop.create_task(_delay_and_finalize(group_id, scheduled_for))


def resume_pending_sessions() -> None:
    """A restart drops the waits held in memory: wait again for every session still collecting messages."""
    for path in SESSIONS_DIR.glob("*.json"):
        session = _read_session(path.stem)
        if session.get("status") == "collecting":
            schedule_session_finalization(path.stem, session.get("started_at", 0) + SESSION_TIMEOUT_SECONDS)


def _is_pdf_message(item: dict) -> bool:
    return bool(item and str(item.get("saved_file") or "").lower().endswith(".pdf"))


def read_pdf_text(path: Path) -> str:
    """The text layer of an issued invoice PDF (its first pages)."""
    import fitz
    with fitz.open(str(path)) as document:
        return "\n".join(page.get_text() for page in list(document)[:3])


def _document_text(message: dict) -> str:
    """Text of an attached PDF (its text layer) or photo (OCR), kept on the message so it is read once."""
    path = INBOX_DIR / message["saved_file"]
    text = read_pdf_text(path) if _is_pdf_message(message) else read_image_text(path)
    save_ocr_text(message["id"], text)
    message["ocr_text"] = text
    return text


async def _buyer_in_document(text: str, owner_id: int | None) -> dict | None:
    """The buyer of an issued invoice: the company whose tax code stands under "Người mua" (the seller's own never counts)."""
    start = re.search(r"người mua|\(buyer\)", text or "", re.I)
    block = text[start.start():] if start else (text or "")
    end = re.search(r"Tên hàng|\bSTT\b", block, re.I)  # the goods table; below it are footers (MISA, BKAV...)
    if end and end.start() > 0:
        block = block[:end.start()]
    codes = labelled_tax_codes(block) + re.findall(r"\b\d{10}(?:-\d{3})?\b", block)
    for code in dict.fromkeys(codes):  # in the order written
        if valid_tax_checksum(code):
            created = await ensure_company_for_tax_code(code)
            if created and created["id"] != owner_id:
                return created
    return None


async def _buyer_from_recent_document(item: dict, owner_id: int | None) -> tuple[dict, list[str]] | None:
    """An old invoice (PDF or photo) the same person sent just before an order names its buyer: "xuất hóa đơn về công
    ty này" is the company on it. Returns that company and the document's message id."""
    sent_at = _sent_at(item)
    if sent_at is None:
        return None
    used = set(_read_session(item["group_id"]).get("message_ids") or [])  # a finished session already used its documents
    documents = []
    for message in list_messages(limit=60):
        moment = _sent_at(message)
        if (moment is None or message["id"] in used or message.get("group_id") != item["group_id"]
                or message.get("sender_id") != item.get("sender_id") or not message.get("saved_file")
                or not sent_at - DOCUMENT_LOOKBACK_SECONDS <= moment <= sent_at
                or not (_is_pdf_message(message) or _is_image_message(message))):
            continue
        documents.append((moment, message))
    for _, document in sorted(documents, key=lambda pair: pair[0], reverse=True):
        text = document.get("ocr_text") or await asyncio.to_thread(_document_text, document)
        company = await _buyer_in_document(text, owner_id)
        if company:
            return company, [document["id"]]
    return None


async def process_invoice_message_ingest(item: dict) -> None:
    """Inspect ingested message; if MST or customer name matches DB, or a session is active, group and set 5-min timer."""
    group_id = str(item.get("group_id") or "")
    if not group_id:
        return

    text = str(item.get("text") or "").strip()
    if text.startswith(_BOT_NOTICE_MARKERS):
        return  # the system's own notice or question
    from frontend.modules.zalo.company_card import fill_company_from_card
    if _is_image_message(item) or _is_pdf_message(item):
        await fill_company_from_card(item.get("ocr_text") or await asyncio.to_thread(_document_text, item))
    elif text:
        await fill_company_from_card(text)
    from frontend.modules.zalo.invoice_follow_up import handle_invoice_follow_up
    if text and not get_active_session(group_id) and await asyncio.to_thread(handle_invoice_follow_up, item):
        return  # a correction to the invoice already made from this group's orders
    owner_id = item.get("company_id")  # the seller of the group: a company never buys from itself
    matched_company = extract_matching_company(text, owner_id)
    matched_by = "tax_code"
    active_session = get_active_session(group_id)

    if not matched_company and not active_session:
        for code in labelled_tax_codes(text):
            if valid_tax_checksum(code):
                created = await ensure_company_for_tax_code(code)
                if created and created["id"] != owner_id:
                    matched_company = created
                    matched_by = "tax_code"
                    break

    document_ids: list[str] = []
    if not matched_company and not active_session and _is_order_without_buyer(text):
        found = await _buyer_from_recent_document(item, owner_id)
        if found:
            matched_company, document_ids = found
            matched_by = "document"

    if _is_pdf_message(item) and not item.get("ocr_text"):
        await asyncio.to_thread(_document_text, item)
    if (not matched_company and not active_session and _read_waiting(group_id)
            and (_is_pdf_message(item) or _is_image_message(item))):
        # Orders waited for a tax code: the old invoice sent now names the buyer.
        matched_company = await _buyer_in_document(item.get("ocr_text") or await asyncio.to_thread(_document_text, item), owner_id)
        matched_by = "document"

    if not matched_company and not active_session and _is_image_message(item):
        saved_file = item.get("saved_file")
        image_path = INBOX_DIR / saved_file if saved_file else None
        if image_path:
            ocr_text = item.get("ocr_text") or await asyncio.to_thread(_document_text, item)
            if await asyncio.to_thread(_is_own_invoice_photo, ocr_text, owner_id):
                logger.info("Group %s: the photo is an invoice of the seller itself; no session opened.", group_id)
                return
            comp = extract_matching_company(ocr_text, owner_id)
            if not comp:
                for code in labelled_tax_codes(ocr_text):
                    if valid_tax_checksum(code):
                        created = await ensure_company_for_tax_code(code)
                        if created and created["id"] != owner_id:
                            comp = created
                            break
            if comp:
                matched_company = comp
                matched_by = "image"

    if not matched_company and not active_session:
        # A name is weaker evidence than a tax code: it may open a session, never re-target one being collected.
        matched_company = extract_company_by_name(text, exclude_company_id=item.get("company_id"))
        matched_by = "name"
    waiting_reply = False
    if not matched_company and not active_session:
        waiting = _read_waiting(group_id)
        if waiting.get("missing") == "price":
            answers = bool(_MILLION.search(text))
        else:
            answers = not _MILLION.search(text) and bool(
                _shares_pin(item) or _LEG_ROUTE.search(text) or _LABELLED_ROUTE.search(text))
        if waiting.get("buyer") and answers:
            matched_company, matched_by = waiting["buyer"], waiting.get("matched_by") or "tax_code"
            waiting_reply = True  # the price or places an order was waiting for: it goes on with the same buyer
    now = time.time()

    if matched_company:
        # A new session also takes the location card sent just before this message; a running one keeps its own.
        if matched_by == "image":
            carried = _messages_around_image(item)
        else:
            carried = ((active_session.get("message_ids", []) if active_session else _preceding_places(item))
                       + document_ids + [item["id"]])
        if not active_session and (waiting_reply or not _read_waiting(group_id).get("buyer")):
            carried = _read_waiting(group_id).get("message_ids", []) + carried  # orders that waited for this tax code
        session = {
            "group_id": group_id,
            "group_name": item.get("group_name", ""),
            "company_id": matched_company["id"],
            "company_name": matched_company["name"],
            "tax_code": matched_company["tax_code"],
            "address": matched_company.get("address") or "",
            "matched_by": matched_by,
            "message_ids": list(dict.fromkeys(carried)),
            "started_at": active_session.get("started_at", now) if active_session else now,
            "last_message_at": now,
            "status": "collecting",
        }
        save_session(session)
        (WAITING_DIR / f"{group_id}.json").unlink(missing_ok=True)
        schedule_session_finalization(group_id, session["started_at"] + SESSION_TIMEOUT_SECONDS)
        logger.info(
            "Group %s: message matched company %s (%s) by %s. Session closes %d seconds after it opened.",
            group_id, matched_company["name"], matched_company["tax_code"], matched_by, SESSION_TIMEOUT_SECONDS,
        )
    elif active_session and active_session.get("status") == "collecting":
        active_session["message_ids"].append(item["id"])
        active_session["message_ids"] = list(dict.fromkeys(active_session["message_ids"]))
        active_session["last_message_at"] = now
        save_session(active_session)
        schedule_session_finalization(group_id, active_session["started_at"] + SESSION_TIMEOUT_SECONDS)
        logger.info(
            "Group %s: message added to active invoice session for company '%s'.",
            group_id, active_session.get("company_name", ""),
        )
    elif _is_order_without_buyer(text):
        await _hold_for_tax_code(item)
