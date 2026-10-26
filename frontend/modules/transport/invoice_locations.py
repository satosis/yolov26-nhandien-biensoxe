"""Normalize invoice route places against the stored official commune directory."""

import re
import unicodedata


_WORD = re.compile(r"\w+", re.UNICODE)
_FACILITY = re.compile(r"\b(?:kcn|ccn|khu cong nghiep|cum cong nghiep|cong ty|nha may|cang)\b")
_KCN_PREFIXES = (("khu", "cong", "nghiep"), ("kcn",))
# Only commune-level prefixes. Districts (huyện, quận, thị xã) are NOT communes in the DB.
_COMMUNE_PREFIXES = (
    ("xa",), ("phuong",), ("p",), ("thi", "tran"), ("tt",), ("dac", "khu")
)
_PROVINCE_PREFIXES = (("tinh",), ("thanh", "pho"), ("tp",), ("t",))
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


def normalize_invoice_location(value: str, directory: list[dict]) -> str:
    """Expand a verified commune/province suffix; leave unknown places unchanged."""
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
        before = folded[:province_start]

        def _match_communes(slice_folded):
            found = []
            for row in directory:
                if row["province_name"] != province:
                    continue
                commune = row["commune_name"]
                bare = _bare_name(commune, _COMMUNE_PREFIXES)
                if bare and len(slice_folded) >= len(bare) and slice_folded[-len(bare):] == bare:
                    start = _without_type(words, len(slice_folded) - len(bare), _COMMUNE_PREFIXES)
                    explicit = start < len(slice_folded) - len(bare)
                    leading_text = text[:words[start][1]]
                    facility_without_separator = (_FACILITY.search(_fold(leading_text))
                                                  and not re.search(r"[,;/]\s*$", leading_text))
                    if facility_without_separator and not explicit:
                        continue
                    if len(bare) >= 2 or explicit:
                        found.append((len(bare), start, commune))
            return found

        matches = _match_communes(before)

        if matches:
            longest = max(match[0] for match in matches)
            names = {match[2] for match in matches if match[0] == longest}
            if len(names) == 1:
                _, start, commune = next(match for match in matches if match[0] == longest)
                prefix = text[:words[start][1]].rstrip(" ,.;-")
                if prefix:
                    return f"{prefix}, {commune} {province}"
                return f"{commune} {province}"
        prefix = text[:words[province_start][1]].rstrip(" ,.;-")
        if _FACILITY.fullmatch(_fold(prefix)):
            return text
        if prefix:
            return f"{prefix}, {province}"
        return province

    matches = []
    for row in directory:
        bare = _bare_name(row["commune_name"], _COMMUNE_PREFIXES)
        if len(bare) >= 2 and (folded == bare or len(folded) == len(bare) and folded == bare):
            matches.append((row["commune_name"], row["province_name"]))
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        commune, province = unique_matches[0]
        return f"{commune} {province}"
    if len(unique_matches) > 1:
        # Prioritize matching local province if multiple provinces have same commune name
        phu_tho = [m for m in unique_matches if "Phú Thọ" in m[1]]
        if len(phu_tho) == 1:
            return f"{phu_tho[0][0]} {phu_tho[0][1]}"

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
