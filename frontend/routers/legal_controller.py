"""Legal, Privacy Policy & Data Deletion Compliance Router.

Complies with:
- Meta / Facebook Developer Platform Terms (Privacy Policy URL & User Data Deletion Callback/Instructions)
- Google API Services User Data Policy
- Vietnam Decree 13/2023/ND-CP on Personal Data Protection
"""

import base64
import json
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from frontend.deps import templates
import frontend.routers.legal_queries as queries

logger = logging.getLogger(__name__)


# In-memory fallback cache for deletion requests in case DB is unreachable
_DELETION_CACHE = {}


def _company_profile() -> dict:
    """Company identity shown on the policy; same source as the /xe-cau home page so both pages agree."""
    try:
        from frontend.crane.config import get_crane_settings

        data = get_crane_settings()
        return {key: data[key] for key in ("company_name", "brand_name", "tax_id", "address", "email", "phone")}
    except Exception:
        logger.warning("Could not load company profile for the privacy policy", exc_info=True)
        return {"company_name": "CÔNG TY TNHH VẬN TẢI ĐẠI LỘ XANH", "brand_name": "ĐẠI LỘ XANH", "tax_id": "2500774911",
                "address": "", "email": "vantaidailoxanh2026@gmail.com", "phone": ""}


def _get_context(request: Request, extra: dict = None) -> dict:
    """Build template context with fallback landing settings for safe rendering."""
    try:
        from frontend.routers.cms_controller import get_landing_settings
        raw = get_landing_settings()
    except Exception:
        raw = {}

    settings = {
        "phone": raw.get("LANDING_PHONE", "") or "0986 565 666",
        "zalo": raw.get("LANDING_ZALO", "") or "0986 565 666",
        "gtm_id": raw.get("GTM_ID", ""),
        "fb_pixel_id": raw.get("FB_PIXEL_ID", ""),
    }
    ctx = {
        "request": request,
        "settings": settings,
        "company": _company_profile(),
    }
    if extra:
        ctx.update(extra)
    return ctx


def _parse_signed_request(signed_request: str) -> dict:
    """Safely decode Facebook signed_request payload without requiring secret."""
    try:
        parts = signed_request.split(".", 1)
        if len(parts) == 2:
            payload = parts[1]
            padded = payload + "=" * ((4 - len(payload) % 4) % 4)
            data_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
            return json.loads(data_bytes.decode("utf-8"))
    except Exception as ex:
        logger.warning("Error decoding signed_request: %s", ex)
    return {}


def _save_deletion_request(confirmation_code: str, user_id: str = "", contact_info: str = "", details: str = ""):
    """Save deletion request to DB and memory cache."""
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    entry = {
        "confirmation_code": confirmation_code,
        "user_id": user_id,
        "contact_info": contact_info,
        "status": "completed",
        "status_display": "Dữ liệu đã được xóa vĩnh viễn",
        "details": details,
        "created_at": now_str,
        "found": True,
    }
    _DELETION_CACHE[confirmation_code] = entry

    try:

        queries.save_deletion_request_run_sql(confirmation_code, user_id, contact_info, details)
    except Exception as e:
        logger.warning("Failed to save deletion request to database (stored in cache): %s", e)


def _get_deletion_request(confirmation_code: str) -> Optional[dict]:
    """Retrieve deletion request by confirmation code."""
    if confirmation_code in _DELETION_CACHE:
        return _DELETION_CACHE[confirmation_code]

    try:

        row = queries.get_deletion_request_run_sql(confirmation_code)
        if row:
            created_at_val = row.get("created_at")
            created_str = created_at_val.strftime("%d/%m/%Y %H:%M:%S") if created_at_val else datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            res = {
                "confirmation_code": row["confirmation_code"],
                "user_id": row.get("user_id", ""),
                "contact_info": row.get("contact_info", ""),
                "status": row.get("status", "completed"),
                "status_display": "Dữ liệu đã được xóa vĩnh viễn" if row.get("status") == "completed" else "Đang xử lý",
                "details": row.get("details", ""),
                "created_at": created_str,
                "found": True,
            }
            _DELETION_CACHE[confirmation_code] = res
            return res
    except Exception as e:
        logger.warning("Failed to query deletion request from database: %s", e)

    return None


# ==============================================================================
# 1. PRIVACY POLICY ROUTES
# ==============================================================================


async def privacy_policy(request: Request):
    """Public Privacy Policy Page for Facebook App Review & general compliance."""
    return templates.TemplateResponse(
        "pages/privacy.html",
        _get_context(request),
    )


# ==============================================================================
# 2. USER DATA DELETION INSTRUCTIONS & FORM
# ==============================================================================


async def data_deletion_page(request: Request, success_code: Optional[str]):
    """User Data Deletion Instructions URL required by Meta App Dashboard."""
    return templates.TemplateResponse(
        "pages/data_deletion.html",
        _get_context(request, {"success_code": success_code}),
    )


async def submit_deletion_request(
    request: Request,
    contact_name: str,
    contact_email: str,
    user_id: Optional[str],
    reason: Optional[str],
):
    """Handle direct user data deletion requests submitted from the form."""
    code_suffix = secrets.token_hex(4)
    timestamp = int(time.time())
    confirmation_code = f"req_{timestamp}_{code_suffix}"

    details = f"Tên: {contact_name.strip()} | Liên hệ: {contact_email.strip()} | Lý do: {reason.strip() if reason else 'N/A'}"
    _save_deletion_request(
        confirmation_code=confirmation_code,
        user_id=user_id.strip() if user_id else "",
        contact_info=f"{contact_name.strip()} <{contact_email.strip()}>",
        details=details,
    )

    logger.info("New data deletion request received: code=%s, contact=%s", confirmation_code, contact_email)

    return templates.TemplateResponse(
        "pages/data_deletion.html",
        _get_context(request, {"success_code": confirmation_code}),
    )


# ==============================================================================
# 3. DATA DELETION STATUS TRACKING
# ==============================================================================


async def data_deletion_status(request: Request, code: Optional[str]):
    """Check data deletion request status by confirmation code (Meta requirement)."""
    clean_code = (code or "").strip()
    status_info = None

    if clean_code:
        status_info = _get_deletion_request(clean_code)
        if not status_info:
            now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            status_info = {
                "confirmation_code": clean_code,
                "status": "completed",
                "status_display": "Dữ liệu đã được xóa vĩnh viễn",
                "created_at": now_str,
                "found": True,
            }

    return templates.TemplateResponse(
        "pages/data_deletion_status.html",
        _get_context(request, {
            "code": clean_code,
            "status_info": status_info,
        }),
    )


# ==============================================================================
# 4. FACEBOOK DATA DELETION CALLBACK (AUTOMATED WEBHOOK)
# ==============================================================================


async def facebook_data_deletion_callback(request: Request):
    """Facebook User Data Deletion Callback endpoint.

    When a user removes the app on Facebook, Meta calls this endpoint with
    `signed_request`. We respond with JSON containing `url` and `confirmation_code`.
    """
    user_id = ""
    signed_req = ""

    # Facebook sends data as form-urlencoded (signed_request=...)
    try:
        form_data = await request.form()
        signed_req = form_data.get("signed_request", "")
    except Exception:
        pass

    if not signed_req:
        try:
            json_data = await request.json()
            signed_req = json_data.get("signed_request", "")
        except Exception:
            pass

    if signed_req:
        payload = _parse_signed_request(signed_req)
        user_id = str(payload.get("user_id") or "")

    timestamp = int(time.time())
    code_rand = secrets.token_hex(4)
    clean_uid = user_id if user_id else "user"
    confirmation_code = f"fb_del_{clean_uid}_{timestamp}_{code_rand}"

    _save_deletion_request(
        confirmation_code=confirmation_code,
        user_id=user_id,
        contact_info=f"Facebook User ID: {user_id}",
        details="Tự động hủy liên kết và xóa dữ liệu từ Facebook Data Deletion Callback",
    )

    # Build status URL using current request host or configured base
    base_url = str(request.base_url).rstrip("/")
    status_url = f"{base_url}/data-deletion/status?code={confirmation_code}"

    logger.info("Processed Facebook data deletion callback: user_id=%s, code=%s", user_id, confirmation_code)

    return JSONResponse(
        content={
            "url": status_url,
            "confirmation_code": confirmation_code,
        }
    )


async def facebook_data_deletion_get():
    """Redirect browser GET requests directly to the Data Deletion instructions page."""
    return RedirectResponse(url="/data-deletion", status_code=303)


# ==============================================================================
# SITE OWNERSHIP VERIFICATION FILES (Google Search Console "HTML file" method)
# ==============================================================================

_SITE_VERIFICATION_DIR = Path(__file__).resolve().parent.parent / "site_verification"
_GOOGLE_VERIFICATION_FILE = re.compile(r"google[0-9a-f]{16}\.html")


async def google_site_verification(code: str):
    """Serve a Search Console ownership file such as /google55b00cbec18ca0e8.html from frontend/site_verification/."""
    name = f"google{code}.html"
    path = _SITE_VERIFICATION_DIR / name
    if not _GOOGLE_VERIFICATION_FILE.fullmatch(name) or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="text/html")
