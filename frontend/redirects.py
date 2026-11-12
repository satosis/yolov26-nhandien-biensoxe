"""Chuyển hướng 303 về trang quản trị kèm tham số (bỏ tham số rỗng)."""
from __future__ import annotations

import urllib.parse

from fastapi.responses import RedirectResponse


def redirect_with_query(url: str, **params) -> RedirectResponse:
    """`params` rỗng/None/0 bị bỏ; giữ thứ tự truyền vào; nối bằng '&' nếu `url` đã có '?'."""
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
    if query:
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    return RedirectResponse(url, status_code=303)
