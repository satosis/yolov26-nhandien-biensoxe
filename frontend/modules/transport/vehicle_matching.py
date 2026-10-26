"""Dynamic vehicle and driver matching based on public.users and plate_whitelist.

No hardcoded names or nicknames. Automatically resolves vehicles based on:
1. Plate numbers (e.g. 19H-147.98, 147.98, 14798)
2. User's nickname in public.users (e.g. "bố", "anh Ba")
3. User's full name / given name in public.users (e.g. "NGUYỄN KIM QUYẾT" -> "Quyết", "Lợi")
4. Vehicle's owner_name in plate_whitelist matching the user's full_name
"""

import re
import unicodedata
from typing import Optional, Tuple
from backend.services.database import db

HONORIFICS = {"chu", "bac", "anh", "chi", "co", "ong", "ba", "em", "ban"}


def _fold(value: str) -> str:
    """Normalize string: lowercase, remove Vietnamese accents, 'đ' -> 'd', strip extra whitespace."""
    if not value:
        return ""
    value = unicodedata.normalize("NFD", str(value).casefold())
    return re.sub(r"\s+", " ", "".join(c for c in value if unicodedata.category(c) != "Mn").replace("đ", "d")).strip()


def build_user_driver_aliases(users: Optional[list[dict]] = None) -> list[dict]:
    """
    Build alias dictionary for all users in public.users.
    Supports full_name, given_name (last word), nicknames (comma-separated), and usernames.
    """
    if users is None:
        try:
            users = db._execute_query(
                "SELECT id, username, full_name, nickname FROM public.users",
                fetchall=True, commit=False
            ) or []
        except Exception:
            users = []

    user_lookup = []
    for u in users:
        fn = (u.get("full_name") or "").strip()
        nick = (u.get("nickname") or "").strip()
        username = (u.get("username") or "").strip()

        aliases = set()
        if fn:
            folded_fn = _fold(fn)
            aliases.add(folded_fn)
            parts = folded_fn.split()
            if parts:
                aliases.add(parts[-1])  # Given name (e.g. "quyet", "loi")
                if len(parts) >= 2:
                    aliases.add(" ".join(parts[-2:]))

        if nick:
            # Nickname can contain multiple comma/slash separated aliases
            for n in re.split(r"[,;/]+", nick):
                fnick = _fold(n.strip())
                if fnick:
                    aliases.add(fnick)
                    # If nickname contains honorific e.g. "chu ba" -> also add "ba"
                    w = fnick.split()
                    while w and w[0] in HONORIFICS:
                        w.pop(0)
                    if w:
                        aliases.add(" ".join(w))

        if username:
            aliases.add(_fold(username.replace("_", " ")))
            aliases.add(_fold(username))

        user_lookup.append({
            "user": u,
            "full_name": fn,
            "nickname": nick,
            "aliases": aliases,
        })

    return user_lookup


def resolve_vehicle_from_identifier(
    identifier: str,
    all_vehicles: Optional[list[dict]] = None,
    users: Optional[list[dict]] = None,
) -> Tuple[Optional[dict], Optional[dict]]:
    """
    Resolve a vehicle and linked user dynamically from an identifier.
    The identifier can be:
    - A plate number (19H-147.98, 147.98, 14798)
    - A user nickname (e.g. 'bố', 'bo', 'chú Ba')
    - A user's full name or given name ('Quyết', 'Nguyễn Kim Quyết', 'Lợi')
    
    Returns (matched_vehicle, matched_user).
    """
    if not identifier:
        return None, None

    if all_vehicles is None:
        try:
            all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
        except Exception:
            all_vehicles = []

    folded_id = _fold(identifier)
    clean_id = re.sub(r"[^a-z0-9]", "", folded_id)

    # 1. Check plate match
    for v in all_vehicles:
        clean_plate = re.sub(r"[^a-z0-9]", "", _fold(v.get("plate_number") or ""))
        if clean_id and (clean_id == clean_plate or (len(clean_id) >= 4 and clean_id in clean_plate)):
            return v, None

    # 2. Check user lookup
    user_lookup = build_user_driver_aliases(users)

    # Strip prefixes: "xe", "cua", honorifics
    tokens = folded_id.split()
    if tokens and tokens[0] == "xe":
        tokens.pop(0)
    if tokens and tokens[0] == "cua":
        tokens.pop(0)
    if tokens and tokens[0] in HONORIFICS:
        tokens.pop(0)
    search_str = " ".join(tokens)

    matched_entry = None
    # Exact match against alias set
    for entry in user_lookup:
        if folded_id in entry["aliases"] or (search_str and search_str in entry["aliases"]):
            matched_entry = entry
            break

    # Partial / last word match if unique
    if not matched_entry and search_str:
        last_word = tokens[-1] if tokens else ""
        if last_word and last_word not in HONORIFICS:
            candidates = [e for e in user_lookup if last_word in e["aliases"]]
            if len(candidates) == 1:
                matched_entry = candidates[0]

    if matched_entry:
        matched_user = matched_entry["user"]
        user_fn_folded = _fold(matched_entry["full_name"])
        matched_vehicle = None

        # Match vehicle by owner_name
        for v in all_vehicles:
            owner_folded = _fold(v.get("owner_name") or "")
            if user_fn_folded and owner_folded == user_fn_folded:
                matched_vehicle = v
                break
        if not matched_vehicle and user_fn_folded:
            for v in all_vehicles:
                owner_folded = _fold(v.get("owner_name") or "")
                if owner_folded and (owner_folded in user_fn_folded or user_fn_folded in owner_folded):
                    matched_vehicle = v
                    break

        return matched_vehicle, matched_user

    return None, None


def extract_vehicle_and_driver_from_texts(
    texts: list[str],
    all_vehicles: Optional[list[dict]] = None,
    users: Optional[list[dict]] = None,
) -> Tuple[Optional[dict], Optional[dict], str]:
    """
    Extract vehicle, matched user, and display identifier from Zalo message texts.
    Returns (matched_vehicle, matched_user, display_nickname_or_name).
    """
    if not texts:
        return None, None, ""

    if all_vehicles is None:
        try:
            all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []
        except Exception:
            all_vehicles = []

    if users is None:
        try:
            users = db._execute_query(
                "SELECT id, username, full_name, nickname FROM public.users",
                fetchall=True, commit=False
            ) or []
        except Exception:
            users = []

    joined = " ".join(texts)
    folded_joined = _fold(joined)

    # 1. Plate match directly in texts
    plate_re = re.search(r"\b(\d{2}[A-Z]-?\d{3}\.?\d{2})\b", joined, re.I)
    if plate_re:
        raw_plate = plate_re.group(1).upper()
        clean_target = re.sub(r"[^A-Z0-9]", "", raw_plate)
        for v in all_vehicles:
            clean_v = re.sub(r"[^A-Z0-9]", "", (v.get("plate_number") or "").upper())
            if clean_target == clean_v:
                return v, None, raw_plate

    # Check 5-digit or short plate in texts (e.g. 147.98 or 14798)
    for v in all_vehicles:
        raw_p = v.get("plate_number") or ""
        short_dots = re.search(r"\d{3}\.\d{2}", raw_p)
        if short_dots and short_dots.group(0) in joined:
            return v, None, short_dots.group(0)

    # 2. Match against user nicknames, full names, given names (accented and unaccented)
    candidate_matches = []

    for u in users:
        fn = (u.get("full_name") or "").strip()
        nick = (u.get("nickname") or "").strip()
        fn_folded = _fold(fn)

        # Check nickname
        if nick:
            for n in re.split(r"[,;/]+", nick):
                n_clean = n.strip()
                n_folded = _fold(n_clean)
                if n_clean:
                    pat1 = rf"\bxe\s+(?:cua\s+)?(?:chú|bác|anh|chị|em|ông|bà|cô|bác)?\s*{re.escape(n_clean)}\b"
                    pat2 = rf"\b{re.escape(n_clean)}\b"
                    m1 = re.search(pat1, joined, re.I)
                    m2 = re.search(pat2, joined, re.I)
                    if m1:
                        candidate_matches.append((len(m1.group(0)), u, n_clean, m1.group(0)))
                    elif m2:
                        candidate_matches.append((len(m2.group(0)), u, n_clean, m2.group(0)))
                    elif n_folded:
                        pat_fold = rf"\bxe\s+(?:cua\s+)?(?:chu|bac|anh|chi|em|ong|ba|co)?\s*{re.escape(n_folded)}\b"
                        m_fold = re.search(pat_fold, folded_joined)
                        if m_fold:
                            candidate_matches.append((len(m_fold.group(0)), u, n_clean, m_fold.group(0)))
                        elif re.search(rf"\b{re.escape(n_folded)}\b", folded_joined) and n_folded != "loi":
                            m_pure = re.search(rf"\b{re.escape(n_folded)}\b", folded_joined)
                            candidate_matches.append((len(m_pure.group(0)), u, n_clean, m_pure.group(0)))

        # Check given name (last word) and full name
        if fn_folded:
            parts = fn.split()
            given_name = parts[-1] if parts else ""
            given_name_folded = _fold(given_name)
            if given_name:
                pat_g1 = rf"\bxe\s+(?:cua\s+)?(?:chú|bác|anh|chị|em|ông|bà|cô|bác)?\s*{re.escape(given_name)}\b"
                pat_g2 = rf"\b(?:chú|bác|anh|chị|em|ông|bà|cô|bác)\s+{re.escape(given_name)}\b"
                mg1 = re.search(pat_g1, joined, re.I)
                mg2 = re.search(pat_g2, joined, re.I)
                if mg1:
                    candidate_matches.append((len(mg1.group(0)), u, given_name, mg1.group(0)))
                elif mg2:
                    candidate_matches.append((len(mg2.group(0)), u, given_name, mg2.group(0)))
                elif given_name_folded:
                    pat_g_fold = rf"\bxe\s+(?:cua\s+)?(?:chu|bac|anh|chi|em|ong|ba|co)?\s*{re.escape(given_name_folded)}\b"
                    mg_fold = re.search(pat_g_fold, folded_joined)
                    if mg_fold:
                        candidate_matches.append((len(mg_fold.group(0)), u, given_name, mg_fold.group(0)))
            if len(parts) >= 2 and fn_folded in folded_joined:
                candidate_matches.append((len(fn), u, fn, fn))

    if candidate_matches:
        candidate_matches.sort(key=lambda x: x[0], reverse=True)
        _, best_user, display_name, _ = candidate_matches[0]

        matched_vehicle = None
        user_fn_folded = _fold(best_user.get("full_name") or "")
        for v in all_vehicles:
            v_owner_folded = _fold(v.get("owner_name") or "")
            if user_fn_folded and (v_owner_folded == user_fn_folded or user_fn_folded in v_owner_folded):
                matched_vehicle = v
                break
        return matched_vehicle, best_user, display_name

    # 3. Fallback: generic "xe <tên>"
    generic_m = re.search(r"\bxe\s+(?:cua\s+)?(?:chú|bác|anh|chị|em|ông|bà|cô|bác)?\s*([A-Za-z0-9_À-ỹ]+)", joined, re.I)
    if generic_m:
        raw_name = generic_m.group(1).strip()
        # Ensure it's not a stopword
        if raw_name.lower() not in ("chở", "tai", "hang", "den", "di", "ve"):
            return None, None, raw_name

    return None, None, ""
