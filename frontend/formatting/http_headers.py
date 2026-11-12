"""HTTP header helpers."""

import re
import unicodedata
from urllib.parse import quote


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Download header safe for Vietnamese file names.

    HTTP headers must be latin-1, so a raw "Bảng cân đối.xlsx" crashes the response. Send an ASCII
    fallback name plus the exact UTF-8 name (RFC 6266 filename*), which every current browser uses.
    """
    plain = unicodedata.normalize("NFKD", str(filename).replace("đ", "d").replace("Đ", "D"))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", plain).strip("_") or "download"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(str(filename), safe='')}"
