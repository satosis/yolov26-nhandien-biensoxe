"""Helper functions for saving and restoring list view query state."""

from __future__ import annotations

import urllib.parse
from typing import Any


def remember_list_url(
    request: Any,
    key: str,
    path: str,
    allowed_params: tuple[str, ...],
) -> str:
    """Build and store list URL in session with only allowed non-empty query params."""
    qp = getattr(request, "query_params", {})
    pairs: list[tuple[str, str]] = []
    for param in allowed_params:
        val = qp.get(param)
        if val is not None and str(val).strip() != "":
            pairs.append((param, str(val)))
    query = urllib.parse.urlencode(pairs)
    url = f"{path}?{query}" if query else path

    if hasattr(request, "session") and request.session is not None:
        request.session[f"list_url:{key}"] = url
    return url


def recall_list_url(request: Any, key: str, path: str, default: str) -> str:
    """Recall list URL from session, validating path prefix and rejecting malicious inputs."""
    val = None
    if hasattr(request, "session") and request.session is not None:
        val = request.session.get(f"list_url:{key}")

    if not isinstance(val, str):
        return default
    if len(val) > 600:
        return default
    if "\n" in val or "\r" in val:
        return default
    if "//" in val:
        return default

    parsed = urllib.parse.urlsplit(val)
    if parsed.scheme or parsed.netloc:
        return default

    if not val.startswith(path):
        return default
    if len(val) > len(path) and val[len(path)] not in ("?", "#", "/"):
        return default

    return val


def url_with_params(url: str, **params: Any) -> str:
    """Add or overwrite query parameters in URL while preserving others."""
    parsed = urllib.parse.urlsplit(url)
    existing_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    param_keys_to_update = {k: str(v) for k, v in params.items() if v is not None}

    new_pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for k, v in existing_pairs:
        if k in param_keys_to_update:
            if k not in seen:
                new_pairs.append((k, param_keys_to_update[k]))
                seen.add(k)
        else:
            new_pairs.append((k, v))

    for k, v in param_keys_to_update.items():
        if k not in seen:
            new_pairs.append((k, v))
            seen.add(k)

    new_query = urllib.parse.urlencode(new_pairs)
    return urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        new_query,
        parsed.fragment,
    ))


def url_month(url: str | None) -> str | None:
    """Extract month query parameter from URL, if present."""
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qs(parsed.query)
    months = qs.get("month")
    if months and months[0]:
        return months[0].strip()
    return None
