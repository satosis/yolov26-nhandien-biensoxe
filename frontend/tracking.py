"""Marketing attribution utilities for the frontend."""
from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lead source labels (Vietnamese)
# ---------------------------------------------------------------------------
LEAD_SOURCE_LABELS: dict[str, str] = {
    "google_ads": "Google Ads",
    "facebook_ads": "Facebook Ads",
    "facebook_page": "Facebook (bài đăng)",
    "facebook_group": "Facebook (nhóm)",
    "partner": "Đối tác giới thiệu",
    "organic_search": "Tìm kiếm tự nhiên",
    "referral": "Trang khác giới thiệu",
    "direct": "Truy cập trực tiếp",
}

# ---------------------------------------------------------------------------
# Attribution limits
# ---------------------------------------------------------------------------
ATTRIBUTION_LIMITS: dict[str, int] = {
    "gclid": 300,
    "gbraid": 300,
    "wbraid": 300,
    "fbclid": 300,
    "utm_source": 200,
    "utm_medium": 200,
    "utm_campaign": 200,
    "utm_term": 200,
    "utm_content": 200,
    "landing_url": 500,
    "referrer": 500,
}

_GTM_PATTERN = re.compile(r"^GTM-[A-Z0-9]{4,12}$")
_PIXEL_PATTERN = re.compile(r"^\d{8,20}$")
_GADS_TAG_PATTERN = re.compile(r"^AW-\d{6,15}$")
_GADS_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

# Conversion label (the part after "AW-xxxx/" in Google Ads' event snippet) per website event.
GADS_LABEL_KEYS = {
    "generate_lead": "GADS_LABEL_LEAD",
    "click_call": "GADS_LABEL_CALL",
    "click_zalo": "GADS_LABEL_ZALO",
}
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_FB_SOURCES = {"facebook", "fb", "instagram", "meta", "ig"}
_FB_PAID_MEDIUMS = {"cpc", "ppc", "paid", "paid_social", "paidsocial", "ads"}

# In-memory cache for tracking IDs (60s TTL)
_cache_ttl: float = 60.0
_cache_time: float = 0.0
_cached_ids: dict[str, str] | None = None


def get_app_setting(key: str, default: str = "") -> str:
    """Retrieve an application setting (lazy import)."""
    from backend.services.settings_service import get_app_setting as _gas
    return _gas(key, default)


def clean_gtm_id(value: object) -> str:
    """Validate and clean Google Tag Manager ID.

    Matches GTM-[A-Z0-9]{4,12}. Returns empty string if invalid.
    """
    if not value or not isinstance(value, str):
        return ""
    cleaned = value.strip().upper()
    if _GTM_PATTERN.fullmatch(cleaned):
        return cleaned
    return ""


def clean_pixel_id(value: object) -> str:
    """Validate and clean Meta (Facebook) Pixel ID.

    Matches 8 to 20 digits. Returns empty string if invalid.
    """
    if not value or not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if _PIXEL_PATTERN.fullmatch(cleaned):
        return cleaned
    return ""


def clean_gads_tag_id(value: object) -> str:
    """Validate the Google Ads tag ID (AW-xxxxxxxxxx). Returns empty string if invalid."""
    if not value or not isinstance(value, str):
        return ""
    cleaned = value.strip().upper()
    return cleaned if _GADS_TAG_PATTERN.fullmatch(cleaned) else ""


def clean_gads_label(value: object) -> str:
    """Validate a Google Ads conversion label (letters, digits, - and _). Returns empty string if invalid."""
    if not value or not isinstance(value, str):
        return ""
    cleaned = value.strip()
    return cleaned if _GADS_LABEL_PATTERN.fullmatch(cleaned) else ""


def tracking_ids() -> dict:
    """Retrieve GTM, Meta Pixel and Google Ads tag settings, cached in process for 60 seconds."""
    global _cache_time, _cached_ids
    now = time.monotonic()
    if _cached_ids is not None and (now - _cache_time) < _cache_ttl:
        return _cached_ids

    raw_gtm = get_app_setting("GTM_ID", "")
    raw_pixel = get_app_setting("FB_PIXEL_ID", "")
    gads_tag = clean_gads_tag_id(get_app_setting("GADS_TAG_ID", ""))
    labels = {}
    for event, key in GADS_LABEL_KEYS.items():
        label = clean_gads_label(get_app_setting(key, ""))
        if label:
            labels[event] = label
    result = {
        "gtm_id": clean_gtm_id(raw_gtm),
        "fb_pixel_id": clean_pixel_id(raw_pixel),
        "gads_tag_id": gads_tag,
        "gads_labels": labels if gads_tag else {},
    }
    _cached_ids = result
    _cache_time = now
    return result


def clean_attribution(raw: dict | None) -> dict[str, str]:
    """Sanitise attribution dictionary.

    Retains only allowed keys, strips control characters and whitespace,
    and truncates to configured limits.
    """
    result: dict[str, str] = {}
    if not isinstance(raw, dict):
        return result

    for k, v in raw.items():
        if k in ATTRIBUTION_LIMITS:
            val = "" if v is None else str(v)
            val = _CONTROL_CHARS_PATTERN.sub("", val).strip()
            result[k] = val[: ATTRIBUTION_LIMITS[k]]
    return result


def site_host() -> str:
    """Return hostname from APP_URL env var."""
    raw = (os.environ.get("APP_URL") or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlparse(raw)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def classify_lead_source(attribution: dict | None, site_host: str = "") -> str:
    """Classify lead source by attribution fields and referrer in priority order."""
    attr = attribution or {}
    gclid = str(attr.get("gclid") or "").strip()
    gbraid = str(attr.get("gbraid") or "").strip()
    wbraid = str(attr.get("wbraid") or "").strip()
    fbclid = str(attr.get("fbclid") or "").strip()
    utm_source = str(attr.get("utm_source") or "").strip().lower()
    utm_medium = str(attr.get("utm_medium") or "").strip().lower()
    referrer = str(attr.get("referrer") or "").strip()

    # 1. Google click IDs
    if gclid or gbraid or wbraid:
        return "google_ads"

    # 2. Google paid search
    if utm_source == "google" and utm_medium in {"cpc", "ppc", "paid", "paidsearch"}:
        return "google_ads"

    if utm_source == "partner":
        return "partner"

    # 3. Facebook / Instagram / Meta paid or organic. "paid_social" without any source is read as Facebook,
    #    the only paid social channel in use; another network (tiktok, ...) is not mislabelled.
    if utm_source in _FB_SOURCES:
        return "facebook_ads" if utm_medium in _FB_PAID_MEDIUMS else "facebook_page"
    if utm_medium in {"paid_social", "paidsocial"} and not utm_source:
        return "facebook_ads"

    # 4. Facebook click ID
    if fbclid:
        return "facebook_ads" if utm_medium in _FB_PAID_MEDIUMS else "facebook_page"

    # 5. Referrer search engines
    ref_host = ""
    if referrer:
        try:
            ref_parsed = urlparse(referrer if "://" in referrer else "http://" + referrer)
            ref_host = (ref_parsed.hostname or "").lower()
        except Exception:
            ref_host = ""

    if ref_host:
        if any(engine in ref_host for engine in ("google.", "bing.", "coccoc.", "duckduckgo.", "yahoo.")):
            return "organic_search"

        # 6. Referrer social media
        if any(d in ref_host for d in ("facebook.com", "fb.com", "instagram.com", "l.facebook.com")):
            return "facebook_page"

        # 7. Referral. The own host comes from SITE_URL, else from the page the visitor landed on.
        own = (site_host or "").lower().strip()
        if "://" in own:
            own = urlparse(own).hostname or own
        if not own:
            landing = str(attr.get("landing_url") or "").strip()
            if landing:
                try:
                    own = (urlparse(landing if "://" in landing else "http://" + landing).hostname or "").lower()
                except Exception:
                    own = ""
        if own and (ref_host == own or ref_host.endswith("." + own) or own.endswith("." + ref_host)):
            return "direct"
        return "referral"

    # 8. Direct / fallback
    return "direct"
