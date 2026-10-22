"""Manage API credentials stored as application key-value settings."""

import json
import logging
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from backend.services.database import db
from backend.services.settings_service import get_all_app_settings
from frontend.deps import get_menu_context, require_menu, templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys")
alias_router = APIRouter(prefix="/api_keys")

_MANAGED_KEYS_SETTING = "API_KEYS_MANAGED_NAMES"
_INITIAL_KEYS = ("BKAV_PARTNER_GUID", "BKAV_PARTNER_TOKEN")
_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def _allowed_key(key: str) -> bool:
    return bool(_KEY_PATTERN.fullmatch(key)) and key != _MANAGED_KEYS_SETTING and not key.upper().startswith("GOOGLE_")


def _managed_keys(settings: dict[str, str]) -> list[str]:
    try:
        saved = json.loads(settings.get(_MANAGED_KEYS_SETTING) or "[]")
    except (TypeError, ValueError):
        saved = []
    if not isinstance(saved, list):
        saved = []
    names = set(_INITIAL_KEYS) | {key for key in saved if isinstance(key, str)}
    return sorted(key for key in names if _allowed_key(key) and key in settings)


@router.get("")
async def api_keys_page(
    request: Request,
    user: dict = Depends(require_menu("api_keys")),
):
    settings = get_all_app_settings()
    ctx = get_menu_context(request, user)
    ctx.update(
        current_menu="api_keys",
        api_keys=[{"key": key, "value": settings[key]} for key in _managed_keys(settings)],
        saved=request.query_params.get("saved"),
        error=request.query_params.get("error"),
    )
    return templates.TemplateResponse("pages/api_keys.html", {"request": request, **ctx})


@router.post("/entries")
async def save_api_keys(
    keys: list[str] = Form([]),
    values: list[str] = Form([]),
    user: dict = Depends(require_menu("api_keys")),
):
    if len(keys) != len(values):
        return RedirectResponse("/admin/api-keys?error=invalid", status_code=303)

    entries: dict[str, str] = {}
    for raw_key, raw_value in zip(keys, values):
        key = raw_key.strip()
        value = raw_value.strip()
        if not key and not value:
            continue
        if not _allowed_key(key) or not value or key in entries:
            return RedirectResponse("/admin/api-keys?error=invalid", status_code=303)
        entries[key] = value

    settings = get_all_app_settings()
    managed = set(_managed_keys(settings))
    if any(key in settings and key not in managed for key in entries):
        return RedirectResponse("/admin/api-keys?error=conflict", status_code=303)

    try:
        with db._get_connection() as conn:
            with conn.cursor() as cursor:
                removed = managed - entries.keys()
                if removed:
                    cursor.execute("DELETE FROM app_settings WHERE key = ANY(%s)", (list(removed),))
                for key, value in entries.items():
                    cursor.execute(
                        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                        (key, value),
                    )
                cursor.execute(
                    "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
                    (_MANAGED_KEYS_SETTING, json.dumps(sorted(entries))),
                )
                conn.commit()
    except Exception:
        logger.exception("Could not save API key entries")
        return RedirectResponse("/admin/api-keys?error=save_failed", status_code=303)

    logger.info("API key entries updated by user %s", user.get("username"))
    return RedirectResponse("/admin/api-keys?saved=1", status_code=303)


@alias_router.get("")
async def alias_api_keys():
    return RedirectResponse("/admin/api-keys", status_code=303)
