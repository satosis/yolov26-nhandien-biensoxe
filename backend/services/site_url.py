"""The address the app is reached at, for links that leave it (Telegram digests, Facebook posts)."""
from __future__ import annotations

import os

LOCAL_ADDRESS = "http://127.0.0.1:8001"


def configured_site_url() -> str:
    """APP_URL from .env, without a trailing slash; empty when unset."""
    return os.environ.get("APP_URL", "").strip().rstrip("/")


def admin_page_url(path: str) -> str:
    """Link to an admin page; without a configured address it points at this machine."""
    return (configured_site_url() or LOCAL_ADDRESS).replace("://localhost", "://127.0.0.1") + path
