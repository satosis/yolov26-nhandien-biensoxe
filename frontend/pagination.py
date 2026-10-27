"""Standard pagination utilities for lists across the application."""

from __future__ import annotations

import math
from typing import Any, Sequence
from urllib.parse import urlencode

DEFAULT_PER_PAGE = 100


def build_page_base_url(base_url: str = "", extra_params: dict[str, Any] | None = None) -> str:
    """Construct a URL base ending in ? or & ready for appending page={p}."""
    params = dict(extra_params or {})
    params.pop("page", None)
    clean_params = [(k, v) for k, v in params.items() if v is not None and v != ""]
    qs = urlencode(clean_params)

    if not base_url:
        return f"?{qs}&" if qs else "?"

    if "?" in base_url:
        base = base_url.rstrip("&")
        return f"{base}&{qs}&" if qs else f"{base}&"
    return f"{base_url}?{qs}&" if qs else f"{base_url}?"


class Pagination(dict):
    """Dictionary supporting both bracket access and pagination attribute properties."""

    @property
    def items(self) -> list[Any]:
        return self.get("items", [])

    def __getitem__(self, key: str) -> Any:
        if key == "items" and "items" not in self:
            return []
        return super().__getitem__(key)

    @property
    def page(self) -> int:
        return self.get("page", 1)

    @property
    def total_pages(self) -> int:
        return self.get("total_pages", 1)

    @property
    def total(self) -> int:
        return self.get("total", 0)

    @property
    def per_page(self) -> int:
        return self.get("per_page", DEFAULT_PER_PAGE)

    @property
    def page_base_url(self) -> str:
        return self.get("page_base_url", "")


def paginate(
    items: Sequence[Any],
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    base_url: str = "",
    extra_params: dict[str, Any] | None = None,
) -> Pagination:
    """
    Paginate any sequence of items into 20 items per page (or specified per_page).
    Returns a Pagination dictionary with sliced items and pagination context keys.
    """
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page))
    page_num = max(1, min(int(page or 1), total_pages)) if total > 0 else 1

    start = (page_num - 1) * per_page
    end = start + per_page
    sliced = list(items[start:end])

    return Pagination({
        "items": sliced,
        "page": page_num,
        "total_pages": total_pages,
        "total": total,
        "per_page": per_page,
        "page_base_url": build_page_base_url(base_url, extra_params),
    })


def paginate_counts(
    total: int,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    base_url: str = "",
    extra_params: dict[str, Any] | None = None,
) -> Pagination:
    """Calculate pagination metadata when items are pre-sliced by SQL LIMIT/OFFSET."""
    total_pages = max(1, math.ceil(total / per_page))
    page_num = max(1, min(int(page or 1), total_pages)) if total > 0 else 1
    return Pagination({
        "items": [],
        "page": page_num,
        "total_pages": total_pages,
        "total": total,
        "per_page": per_page,
        "page_base_url": build_page_base_url(base_url, extra_params),
    })
