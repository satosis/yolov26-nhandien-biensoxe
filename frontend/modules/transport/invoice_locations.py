"""Normalize invoice route places against the stored official commune directory."""

import asyncio
import re
import unicodedata

from backend.services.database import db
from backend.services.google_maps import canonical_name, canonical_place


_WORD = re.compile(r"\w+", re.UNICODE)
_FACILITY = re.compile(r"\b(?:kcn|ccn|khu cong nghiep|cum cong nghiep|cong ty|nha may|cang)\b")
_KCN_PREFIXES = (("khu", "cong", "nghiep"), ("kcn",))
# Only commune-level prefixes. Districts (huyện, quận, thị xã) are NOT communes in the DB.
_COMMUNE_PREFIXES = (
    ("xa",), ("phuong",), ("p",), ("thi", "tran"), ("tt",), ("dac", "khu")
)
_PROVINCE_PREFIXES = (("tinh",), ("thanh", "pho"), ("tp",), ("t",))
# Districts no longer exist; an address may still carry one between its commune and its province.
_DISTRICT_PREFIXES = (("huyen",), ("quan",), ("thi", "xa"), ("thanh", "pho"), ("tp",), ("tx",), ("h",), ("q",))
_COUNTRY_REGEX = re.compile(r",?\s*(?:việt\s*nam|viet\s*nam|vietnam|vn)[\s.]*$", re.IGNORECASE)
_PLUS_CODE_REGEX = re.compile(r"^[A-Z0-9]{4,8}\+[A-Z0-9]{2,4}\b\s*,?\s*", re.IGNORECASE)


def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.casefold())
                   if not unicodedata.combining(char)).replace("đ", "d")


def _words(value: str) -> list[tuple[str, int, int]]:
    return [(_fold(match.group()), match.start(), match.end()) for match in _WORD.finditer(value)]


def _bare_name(name: str, prefixes: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    parts = tuple(word for word, _, _ in _words(name))
    for prefix in prefixes:
        if parts[:len(prefix)] == prefix:
            return parts[len(prefix):]
    return parts


def _without_type(words: list[tuple[str, int, int]], end: int,
                  prefixes: tuple[tuple[str, ...], ...]) -> int:
    for prefix in prefixes:
        if end >= len(prefix) and tuple(word for word, _, _ in words[end-len(prefix):end]) == prefix:
            return end - len(prefix)
    return end


def _kcn_prefix_length(words: tuple[str, ...]) -> int:
    for prefix in _KCN_PREFIXES:
        if words[:len(prefix)] == prefix:
            return len(prefix)
    return 0


def _kcn_location(text: str, words: list[tuple[str, int, int]], prefix_end: int,
                  province_start: int, province: str, directory: list[dict]) -> str:
    """Keep an industrial park name separate from a verified locality suffix."""
    if province_start <= prefix_end:
        return text  # "KCN Bắc Ninh" can be the park's name, not a province.

    before = tuple(word for word, _, _ in words[:province_start])
    matches = []
    for row in directory:
        if row["province_name"] != province:
            continue
        commune = row["commune_name"]
        bare = _bare_name(commune, _COMMUNE_PREFIXES)
        if not bare or before[-len(bare):] != bare:
            continue
        start = _without_type(words, province_start - len(bare), _COMMUNE_PREFIXES)
        # A locality after the park name is distinct; the only name after
        # "KCN" (e.g. "KCN Bình Xuyên") is the park itself.
        if start > prefix_end and (len(bare) >= 2 or start < province_start - len(bare)):
            matches.append((len(bare), start, commune))

    locality = ""
    name_end = province_start
    if matches:
        longest = max(match[0] for match in matches)
        best = [match for match in matches if match[0] == longest]
        if len({match[2] for match in best}) == 1:
            _, name_end, commune = best[0]
            locality = commune[0].lower() + commune[1:]

    name = text[words[prefix_end][1]:words[name_end][1]].strip(" ,.;-")
    if not name:
        return text
    province_label = province[0].lower() + province[1:]
    return ", ".join(part for part in (f"KCN {name}", locality, province_label) if part)


def _match_communes(text: str, words: list[tuple[str, int, int]], slice_folded: tuple[str, ...],
                    candidates: list[tuple[str, object]]) -> list[tuple[int, int, object]]:
    """Candidates (the name to look for, what it stands for) that the slice of words ends with: (name length in
    words, index of the word the place starts at, what it stands for)."""
    found = []
    for matching_name, result in candidates:
        bare = _bare_name(matching_name, _COMMUNE_PREFIXES)
        if bare and len(slice_folded) >= len(bare) and slice_folded[-len(bare):] == bare:
            start = _without_type(words, len(slice_folded) - len(bare), _COMMUNE_PREFIXES)
            explicit = start < len(slice_folded) - len(bare)
            leading_text = text[:words[start][1]]
            facility_without_separator = (_FACILITY.search(_fold(leading_text))
                                          and not re.search(r"[,;/]\s*$", leading_text))
            if facility_without_separator and not explicit:
                continue
            if len(bare) >= 2 or explicit:
                found.append((len(bare), start, result))
    return found


def _old_communes(*, new_province: str = "", old_province: str = "") -> list[tuple[str, tuple[str, str], tuple[str, ...]]]:
    """(old commune name, (the commune that replaced it, its province), its old district's bare words) from the
    official old -> new conversion table, for the communes now in `new_province` or the ones that were in
    `old_province`."""
    column = "new_province_name" if new_province else "old_province_name"
    rows = db._execute_query(
        "SELECT old_commune_name, old_district_name, new_commune_name, new_province_name "
        f"FROM public.administrative_commune_mappings WHERE {column} = %s",
        (new_province or old_province,), fetchall=True, commit=False,
    ) or []
    return [(row["old_commune_name"], (row["new_commune_name"], row["new_province_name"]),
             _bare_name(row["old_district_name"] or "", _DISTRICT_PREFIXES)) for row in rows]


def _resolve_old(text: str, words: list[tuple[str, int, int]], slice_folded: tuple[str, ...],
                 rows: list[tuple[str, tuple[str, str], tuple[str, ...]]],
                 district: tuple[str, ...] = ()) -> tuple[int, tuple[str, str]] | None:
    """(start word, (new commune, province)) of the old commune the slice ends with. A district written in the address
    picks among communes of the same name; without it, or when it fits none, the name must be unique."""
    if district:
        picked = _only_replacement(_match_communes(
            text, words, slice_folded, [(name, new) for name, new, old_district in rows if old_district == district]))
        if picked:
            return picked
    return _only_replacement(_match_communes(text, words, slice_folded, [(name, new) for name, new, _ in rows]))


def _split_district(text: str, words: list[tuple[str, int, int]], province_start: int,
                    known: set[tuple[str, ...]] = frozenset()) -> tuple[str, tuple[str, ...]] | None:
    """('Xã Hương Sơn, huyện Bình Xuyên, Tỉnh Phú Thọ') -> ('Xã Hương Sơn, Tỉnh Phú Thọ', ('binh', 'xuyen')): the district
    written between the commune and the province is dropped from the address and kept apart. None without one.
    A segment with no "huyện/quận/thị xã/thành phố" is a district only when it is one of the `known` old districts."""
    province_at = words[province_start][1]
    head = text[:province_at].rstrip(" ,.;-")
    cut = head.rfind(",")
    if cut < 0 or not head[:cut].strip(" ,.;-"):
        return None
    segment = _words(head[cut + 1:])
    rest = f"{head[:cut].rstrip(' ,.;-')}, {text[province_at:]}"
    for prefix in _DISTRICT_PREFIXES:
        if len(segment) > len(prefix) and tuple(word for word, _, _ in segment[:len(prefix)]) == prefix:
            return rest, tuple(word for word, _, _ in segment[len(prefix):])
    bare = tuple(word for word, _, _ in segment)
    return (rest, bare) if bare in known else None


def _only_replacement(matches: list[tuple[int, int, object]]) -> tuple[int, object] | None:
    """(start word, replacement) of the longest match when all the longest matches agree, else None."""
    if not matches:
        return None
    longest = max(match[0] for match in matches)
    results = {match[2] for match in matches if match[0] == longest}
    if len(results) != 1:
        return None
    return next((match[1], match[2]) for match in matches if match[0] == longest)


def _old_address_location(text: str, words: list[tuple[str, int, int]], folded: tuple[str, ...]) -> str:
    """An address that ends with a province that no longer exists ("Hương Sơn, Vĩnh Phúc"): its old commune replaced
    by the commune and province that took its place. Empty when it names no old province or no single old commune."""
    old_provinces = [row["old_province_name"] for row in db._execute_query(
        "SELECT DISTINCT old_province_name FROM public.administrative_commune_mappings "
        "WHERE old_province_name IS NOT NULL", fetchall=True, commit=False) or []]
    ended = [(len(bare), name) for name in old_provinces
             if (bare := _bare_name(name, _PROVINCE_PREFIXES)) and len(folded) > len(bare) and folded[-len(bare):] == bare]
    if not ended:
        return ""
    size, old_province = max(ended)
    province_start = _without_type(words, len(words) - size, _PROVINCE_PREFIXES)
    district: tuple[str, ...] = ()
    rows = _old_communes(old_province=old_province)
    split = _split_district(text, words, province_start, {old_district for _, _, old_district in rows if old_district})
    if split:
        text, district = split
        words = _words(text)
        folded = tuple(word for word, _, _ in words)
        province_start = _without_type(words, len(words) - size, _PROVINCE_PREFIXES)
    picked = _resolve_old(text, words, folded[:province_start], rows, district)
    if not picked:
        return ""
    start, (commune, province) = picked
    prefix = text[:words[start][1]].rstrip(" ,.;-")
    return f"{prefix}, {commune}, {province}" if prefix else f"{commune}, {province}"


def normalize_invoice_location(value: str, directory: list[dict], _district: tuple[str, ...] = ()) -> str:
    """Expand a verified commune/province suffix; leave unknown places unchanged.

    A commune that was merged into another ("Hương Sơn, Phú Thọ", "Hương Sơn, Vĩnh Phúc") becomes the commune that
    replaced it ("Xã Bình Xuyên, Tỉnh Phú Thọ"), by the official conversion table, when that is one commune. A
    district written before the province ("Xã Hương Sơn, huyện Bình Xuyên, Phú Thọ") is dropped and picks the
    commune when the old name alone is ambiguous. A bare old name ("Hương Sơn") is taken as the one in the local
    province."""
    text = unicodedata.normalize("NFC", " ".join(str(value or "").split()).strip())
    if not text or text.startswith(("http://", "https://")):
        return text

    # Strip Google Plus Code prefix if present (e.g. "G3XV+32W ")
    text = _PLUS_CODE_REGEX.sub("", text).strip(" ,.;-")
    if not text:
        return ""

    # Strip trailing country name (e.g. ", Việt Nam", ", Vietnam", "Việt Nam.")
    text = _COUNTRY_REGEX.sub("", text).strip(" ,.;-")
    if not text:
        return ""

    words = _words(text)
    folded = tuple(word for word, _, _ in words)
    if not folded:
        return text

    provinces = {row["province_name"] for row in directory}

    province_match = None
    for province in provinces:
        bare = _bare_name(province, _PROVINCE_PREFIXES)
        if bare and len(folded) >= len(bare) and folded[-len(bare):] == bare:
            start = _without_type(words, len(words) - len(bare), _PROVINCE_PREFIXES)
            if province_match is None or len(bare) > province_match[0]:
                province_match = (len(bare), start, province)

    if province_match:
        _, province_start, province = province_match
        kcn_prefix_end = _kcn_prefix_length(folded)
        if kcn_prefix_end:
            return _kcn_location(text, words, kcn_prefix_end, province_start, province, directory)
        split = None if _district else _split_district(text, words, province_start)
        if split:
            return normalize_invoice_location(split[0], directory, split[1])
        before = folded[:province_start]

        current = [(row["commune_name"], row["commune_name"]) for row in directory if row["province_name"] == province]
        matches = _match_communes(text, words, before, current)
        if not matches:
            old = _resolve_old(text, words, before, _old_communes(new_province=province), _district)
            if old:
                start, (commune, _) = old
                prefix = text[:words[start][1]].rstrip(" ,.;-")
                return f"{prefix}, {commune}, {province}" if prefix else f"{commune}, {province}"

        if matches:
            longest = max(match[0] for match in matches)
            names = {match[2] for match in matches if match[0] == longest}
            if len(names) == 1:
                _, start, commune = next(match for match in matches if match[0] == longest)
                prefix = text[:words[start][1]].rstrip(" ,.;-")
                if prefix:
                    return f"{prefix}, {commune}, {province}"
                return f"{commune}, {province}"
        prefix = text[:words[province_start][1]].rstrip(" ,.;-")
        if _FACILITY.fullmatch(_fold(prefix)):
            return text
        if prefix:
            return f"{prefix}, {province}"
        return province

    old_address = _old_address_location(text, words, folded)
    if old_address:
        return old_address

    matches = []
    for row in directory:
        bare = _bare_name(row["commune_name"], _COMMUNE_PREFIXES)
        if len(bare) >= 2 and (folded == bare or len(folded) == len(bare) and folded == bare):
            matches.append((row["commune_name"], row["province_name"]))
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        commune, province = unique_matches[0]
        return f"{commune}, {province}"
    if len(unique_matches) > 1:
        # Prioritize matching local province if multiple provinces have same commune name
        phu_tho = [m for m in unique_matches if "Phú Thọ" in m[1]]
        if len(phu_tho) == 1:
            return f"{phu_tho[0][0]}, {phu_tho[0][1]}"

    local = next((name for name in provinces if "Phú Thọ" in name), "")
    old = _resolve_old(text, words, folded, _old_communes(new_province=local)) if local else None
    if old and old[0] == 0:
        return ", ".join(old[1])

    return text


def split_invoice_destination_stops(value: str, directory: list[dict]) -> tuple[list[str], str]:
    """Separate parks only when both are explicitly named as destinations."""
    normalized = normalize_invoice_location(value, directory)
    text = " ".join(str(value or "").split()).strip()
    words = _words(text)
    starts = [words[index][1] for index in range(len(words))
              if _kcn_prefix_length(tuple(word for word, _, _ in words[index:]))]
    if len(starts) < 2 or starts[0] != 0:
        return [], normalized

    stops = []
    for start, end in zip(starts, starts[1:]):
        chunk = text[start:end].rstrip()
        if chunk.endswith(","):
            chunk = chunk[:-1].rstrip()
        else:
            chunk_words = _words(chunk)
            if not chunk_words or chunk_words[-1][0] != "va":
                return [], normalized
            chunk = chunk[:chunk_words[-1][1]].rstrip()
        if not chunk:
            return [], normalized
        stops.append(normalize_invoice_location(chunk, directory))

    destination = normalize_invoice_location(text[starts[-1]:], directory)
    return (stops, destination) if destination else ([], normalized)


async def google_normalized_place(value: str, directory: list[dict]) -> str:
    """The place with its names spelled the way Google knows them. A bare name takes Google's name and address; in an
    address each part before the official commune / province tail ("Xã Bình Nguyên, Tỉnh Phú Thọ") is corrected on its
    own, looked up near that tail. Parts Google does not clearly recognise, and the tail itself, stay as they are."""
    text = " ".join(str(value or "").split())
    if not text or text.startswith(("http://", "https://")):
        return text
    if "," not in text:
        return await canonical_place(text) or text
    parts = [part.strip() for part in text.split(",") if part.strip()]
    provinces = {_fold(row["province_name"]) for row in directory}
    tail_at = next((at for at, part in enumerate(parts) if any(_fold(part).endswith(name) for name in provinces)), None)
    if tail_at and re.match(r"(?:xa|phuong|thi tran|dac khu)\s", _fold(parts[tail_at - 1])):
        tail_at -= 1  # "Phường Vĩnh Yên, Tỉnh Phú Thọ": the official ward and its province form the tail together
    names, tail = (parts, "") if tail_at is None else (parts[:tail_at], ", ".join(parts[tail_at:]))

    async def corrected(part: str) -> str:
        lead = re.match(r"(?:tại|tai|ở|gần)\s+", part, re.IGNORECASE)
        prefix = lead.group(0) if lead else ""
        return prefix + (await canonical_name(part[len(prefix):], tail) or part[len(prefix):])

    fixed = await asyncio.gather(*(corrected(part) for part in names))
    return ", ".join([*fixed, *([] if tail_at is None else parts[tail_at:])])


def load_commune_directory() -> list[dict]:
    """Official communes with their province, the list invoice places are completed from."""
    return db._execute_query(
        "SELECT c.name AS commune_name, p.name AS province_name FROM public.administrative_communes c "
        "JOIN public.administrative_provinces p ON p.code = c.province_code WHERE c.is_active AND p.is_active",
        fetchall=True, commit=False,
    ) or []
