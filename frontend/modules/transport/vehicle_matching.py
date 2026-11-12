"""Dynamic vehicle and driver matching based on public.users and plate_whitelist.

No hardcoded names or nicknames. Automatically resolves vehicles based on:
1. Plate numbers (e.g. 19H-147.98, 147.98, 14798)
2. User's nickname in public.users (e.g. "bố", "anh Ba")
3. User's full name / given name in public.users (e.g. "NGUYỄN KIM QUYẾT" -> "Quyết", "Lợi")
4. Vehicle's owner_name in plate_whitelist matching the user's full_name
"""

import re
from typing import Optional, Tuple
from backend.services.database import db
from frontend.formatting.text import fold_text as _fold

HONORIFICS = {"chu", "bac", "anh", "chi", "co", "ong", "ba", "em", "ban"}
_OWNER_TITLES = {"chu", "bac", "anh", "em", "ong", "bo", "me"}
_NOT_NAMES = {"xuat", "chua", "roi", "di", "ve", "nay", "do", "can", "chuyen", "vat", "cho"}


def vehicle_owner_in_text(text: str) -> tuple[str, str]:
    """The person a message names as a vehicle's owner: "xe chú Tư" -> ("chú", "Tư"), "xe bố" -> ("bố", "").

    Also recognizes when a message starts with who carries: "Chú Tư chở..." -> ("Chú", "Tư"), "Bố chở..." -> ("Bố", "").
    Accents do not matter ("xe chu tu" is the same), and the words come back as typed. ("", "") when none."""
    words = re.findall(r"\w+", str(text or ""))
    folded = [_fold(word) for word in words]
    for at in range(len(words) - 1):
        if folded[at] != "xe":
            continue
        title = at + 2 if folded[at + 1] == "cua" else at + 1  # "xe của bố" is "xe bố"
        if title < len(words) and folded[title] in _OWNER_TITLES:
            named = title + 1 < len(words) and words[title + 1].isalpha() and folded[title + 1] not in _NOT_NAMES
            return words[title], words[title + 1] if named else ""
    if len(words) >= 2 and folded[0] in {"bo", "me"} and folded[1] == "cho":
        return words[0], ""
    if len(words) >= 3 and folded[0] in _OWNER_TITLES and folded[2] == "cho" and folded[1] not in _NOT_NAMES:
        return words[0], words[1]
    return "", ""


def vehicle_with_plate_in_text(text: str, vehicles: list[dict]) -> Optional[dict]:
    """The vehicle whose plate a message writes out, however it is spaced or punctuated ("xe 19H-209.07", "19H 20907")."""
    squeezed = re.sub(r"[\s.\-]", "", str(text or "")).upper()
    found = []
    for vehicle in vehicles:
        plate = re.sub(r"[\s.\-]", "", str(vehicle.get("plate_number") or "")).upper()
        at = squeezed.find(plate) if plate else -1
        if at >= 0:
            found.append((at, vehicle))
    return min(found, key=lambda hit: hit[0])[1] if found else None  # the plate written first


_VEHICLE_COUNT = re.compile(r"(\d+)\s*(?:cai\s+|chiec\s+)?xe\s+([a-z]+(?:\s+[a-z]+)?)")
_OWN_WORDS = {"minh", "nha"}


def vehicle_split_in_text(text: str) -> list[tuple[int, str]]:
    """How many trips each vehicle takes: "2 cái xe mình 1 xe chu tu" -> [(2, ""), (1, "chu tu")].
    "" stands for the company's own vehicle ("xe mình", "xe nhà"); a part naming nobody known is skipped."""
    found = []
    for count, owner in _VEHICLE_COUNT.findall(_fold(text)):
        words = owner.split()
        if words[0] in _OWN_WORDS:
            found.append((int(count), ""))
        elif words[0] in _OWNER_TITLES:
            name = words[1] if len(words) > 1 and words[1] not in _NOT_NAMES else ""
            found.append((int(count), f"{words[0]} {name}".strip()))
    return found


def build_user_driver_aliases(users: Optional[list[dict]] = None) -> list[dict]:
    """
    Build alias dictionary for all users in public.users.
    Supports full_name, given_name (last word), nicknames (comma-separated), and usernames.
    """
    if users is None:
        users = db._execute_query(
            "SELECT id, username, full_name, nickname FROM public.users",
            fetchall=True, commit=False
        ) or []

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
        all_vehicles = db.select("plate_whitelist", order_by="plate_number ASC") or []

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

    # Exact match against alias set. Two people can share a name, so keep every match: the one who owns a vehicle wins.
    entries = [e for e in user_lookup
               if folded_id in e["aliases"] or (search_str and search_str in e["aliases"])]

    # Partial / last word match if unique
    if not entries and search_str:
        last_word = tokens[-1] if tokens else ""
        if last_word and last_word not in HONORIFICS:
            candidates = [e for e in user_lookup if last_word in e["aliases"]]
            if len(candidates) == 1:
                entries = candidates

    for entry in entries:
        user_fn_folded = _fold(entry["full_name"])
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

        if matched_vehicle:
            return matched_vehicle, entry["user"]

    return (None, entries[0]["user"]) if entries else (None, None)

