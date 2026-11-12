"""Manage API credentials stored as application key-value settings."""

import json
import logging
import re

from fastapi import Request
from fastapi.responses import RedirectResponse

from backend.services.settings_service import get_all_app_settings
from frontend.deps import get_menu_context, templates
from frontend.tracking import clean_gads_label, clean_gads_tag_id, clean_gtm_id, clean_pixel_id
import frontend.routers.api_keys_queries as queries

logger = logging.getLogger(__name__)

_MANAGED_KEYS_SETTING = "API_KEYS_MANAGED_NAMES"
_INITIAL_KEYS = ("BKAV_PARTNER_GUID", "BKAV_PARTNER_TOKEN")
# Keys the app knows how to use: shown as empty rows until filled, skipped on save while empty.
_SUGGESTED_KEYS = {
    "FB_PAGE_ID": "ID số của Facebook Page",
    "FB_PAGE_TOKEN": "Page Access Token (dài hạn)",
    "GTM_ID": "Mã Google Tag Manager, dạng GTM-XXXXXXX",
    "FB_PIXEL_ID": "Mã Meta Pixel (dãy số); để trống nếu Pixel đã đặt trong GTM",
    "GADS_TAG_ID": "Mã Google tag của Google Ads, dạng AW-1234567890 (dùng thay GTM nếu không cài GTM)",
    "GADS_LABEL_LEAD": "Nhãn chuyển đổi Google Ads cho sự kiện gửi form (phần sau dấu / trong send_to)",
    "GADS_LABEL_CALL": "Nhãn chuyển đổi Google Ads cho sự kiện bấm gọi",
    "GADS_LABEL_ZALO": "Nhãn chuyển đổi Google Ads cho sự kiện bấm Zalo",
}
# Tracking identifiers are injected into public pages, so their format is checked before saving.
_TRACKING_CLEANERS = {
    "GTM_ID": clean_gtm_id,
    "FB_PIXEL_ID": clean_pixel_id,
    "GADS_TAG_ID": clean_gads_tag_id,
    "GADS_LABEL_LEAD": clean_gads_label,
    "GADS_LABEL_CALL": clean_gads_label,
    "GADS_LABEL_ZALO": clean_gads_label,
}
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


async def api_keys_page(request: Request, user: dict):
    settings = get_all_app_settings()
    ctx = get_menu_context(request, user)
    ctx.update(
        current_menu="api_keys",
        api_keys=[{"key": key, "value": settings[key]} for key in _managed_keys(settings)],
        suggested_keys=[
            {"key": key, "hint": hint} for key, hint in _SUGGESTED_KEYS.items() if key not in settings
        ],
        saved=request.query_params.get("saved"),
        error=request.query_params.get("error"),
    )
    return templates.TemplateResponse("pages/api_keys.html", {"request": request, **ctx})


async def save_api_keys(keys: list[str], values: list[str], user: dict):
    if len(keys) != len(values):
        return RedirectResponse("/admin/api-keys?error=invalid", status_code=303)

    entries: dict[str, str] = {}
    for raw_key, raw_value in zip(keys, values):
        key = raw_key.strip()
        value = raw_value.strip()
        if (not key or key in _SUGGESTED_KEYS) and not value:
            continue
        if not _allowed_key(key) or not value or key in entries:
            return RedirectResponse("/admin/api-keys?error=invalid", status_code=303)
        cleaner = _TRACKING_CLEANERS.get(key)
        if cleaner:
            cleaned = cleaner(value)
            if not cleaned:
                return RedirectResponse("/admin/api-keys?error=invalid", status_code=303)
            value = cleaned
        entries[key] = value

    settings = get_all_app_settings()
    managed = set(_managed_keys(settings))
    if any(key in settings and key not in managed for key in entries):
        return RedirectResponse("/admin/api-keys?error=conflict", status_code=303)

    queries.save_entries(managed - entries.keys(), entries, _MANAGED_KEYS_SETTING)

    logger.info("API key entries updated by user %s", user.get("username"))
    return RedirectResponse("/admin/api-keys?saved=1", status_code=303)


async def alias_api_keys():
    return RedirectResponse("/admin/api-keys", status_code=303)
