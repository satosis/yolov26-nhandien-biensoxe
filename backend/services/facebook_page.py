"""Graph API client for the company Facebook Page (FB_PAGE_ID / FB_PAGE_TOKEN in Quản lý API Key)."""

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from backend.services.settings_service import get_app_setting, set_app_setting

GRAPH = "https://graph.facebook.com/v26.0/"
POST_FIELDS = ("id,created_time,message,permalink_url,full_picture,"
               "attachments{media_type,subattachments.limit(10){media_type}}")
# Facebook only accepts scheduled posts between 10 minutes and 30 days ahead.
MIN_SCHEDULE_SECONDS = 10 * 60
MAX_SCHEDULE_SECONDS = 30 * 24 * 3600

_sleep = time.sleep

_REDACT_RE = re.compile(
    r"(access_token|input_token|client_secret|appsecret_proof)=[^&\s\"',]+",
    re.IGNORECASE,
)


def _redact(text: str, *secrets: str) -> str:
    """Replace token params and known secrets in text with ***."""
    result = _REDACT_RE.sub(r"\1=***", str(text or ""))
    for secret in secrets:
        if secret and isinstance(secret, str):
            result = result.replace(secret, "***")
    return result


class GraphError(RuntimeError):
    """Structured Facebook Graph API error with classification."""

    def __init__(self, message: str, code: int | None = None, subcode: int | None = None,
                 http_status: int | None = None, kind: str = "unknown",
                 retryable: bool = False, user_message: str = ""):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.http_status = http_status
        self.kind = kind
        self.retryable = retryable
        self.user_message = user_message or message
        self.maybe_posted: bool = False


def classify_error(payload: dict | None, http_status: int | None) -> GraphError:
    """Classify a Graph API error payload into a structured GraphError."""
    code: int | None = None
    subcode: int | None = None
    raw_message = ""

    if payload:
        err = payload.get("error", payload)
        if isinstance(err, dict):
            code = err.get("code")
            subcode = err.get("error_subcode")
            raw_message = str(err.get("message") or err)
        else:
            raw_message = str(err)

    raw_message = _redact(raw_message)

    # HTTP >= 500 without a specific graph client error code is transient
    if http_status is not None and http_status >= 500 and code not in (190, 10, 368, 506):
        return GraphError(raw_message or f"HTTP {http_status}", code=code, subcode=subcode,
                          http_status=http_status, kind="transient", retryable=True,
                          user_message="Facebook hoặc đường truyền đang gián đoạn, thử lại sau.")

    if code == 190:
        return GraphError(raw_message or "OAuthException: 190", code=code, subcode=subcode,
                          http_status=http_status, kind="auth", retryable=False,
                          user_message="Token Facebook đã hết hạn hoặc bị thu hồi. Tạo lại token và lưu vào Quản lý API Key (FB_PAGE_TOKEN).")

    if code == 10 or (code is not None and 200 <= code <= 299):
        return GraphError(raw_message or f"Permission error: {code}", code=code, subcode=subcode,
                          http_status=http_status, kind="permission", retryable=False,
                          user_message="Token thiếu quyền đăng bài (pages_manage_posts). Cấp lại quyền cho ứng dụng.")

    if code in (4, 17, 32, 341, 613, 80001):
        return GraphError(raw_message or f"Rate limit: {code}", code=code, subcode=subcode,
                          http_status=http_status, kind="rate_limit", retryable=False,
                          user_message="Facebook đang giới hạn tốc độ, thử lại sau ít phút.")

    if code in (1, 2) or (http_status is not None and http_status >= 500):
        return GraphError(raw_message or f"Transient error: {code or http_status}", code=code, subcode=subcode,
                          http_status=http_status, kind="transient", retryable=True,
                          user_message="Facebook hoặc đường truyền đang gián đoạn, thử lại sau.")

    if code == 100:
        msg = f"Tham số yêu cầu không hợp lệ: {raw_message}" if raw_message else "Tham số yêu cầu không hợp lệ."
        return GraphError(raw_message or "Invalid parameter: 100", code=code, subcode=subcode,
                          http_status=http_status, kind="invalid", retryable=False,
                          user_message=msg)

    if code == 368:
        return GraphError(raw_message or "Policy blocked: 368", code=code, subcode=subcode,
                          http_status=http_status, kind="blocked", retryable=False,
                          user_message="Facebook tạm chặn đăng bài của Page (nghi vi phạm chính sách), kiểm tra Hộp thư hỗ trợ của Page.")

    if code == 506:
        return GraphError(raw_message or "Duplicate post: 506", code=code, subcode=subcode,
                          http_status=http_status, kind="duplicate", retryable=False,
                          user_message="Facebook từ chối vì bài trùng với bài vừa đăng.")

    um = (f"Lỗi Facebook không xác định (mã {code}): {raw_message}" if code
          else (f"Lỗi Facebook không xác định: {raw_message}" if raw_message else "Lỗi Facebook không xác định."))
    return GraphError(raw_message or "Lỗi Facebook không xác định", code=code, subcode=subcode,
                      http_status=http_status, kind="unknown", retryable=False,
                      user_message=um)


def _rewind_files(files) -> None:
    """Put uploaded file handles back at the start so a retry sends the whole file, not an empty one."""
    for part in (files or {}).values():
        handle = part[1] if isinstance(part, tuple) and len(part) > 1 else part
        seek = getattr(handle, "seek", None)
        if callable(seek):
            seek(0)


def graph(method: str, path: str, token: str, files=None, idempotent: bool = False, **params):
    """Call the Graph API, classify errors, redact secrets, and retry transient/network failures."""
    params_clean = {k: v for k, v in params.items() if k != "idempotent"}
    params_clean["access_token"] = token
    body = method == "POST"
    can_retry = (method in ("GET", "DELETE") or idempotent)
    delays = [1, 3]
    last_error: GraphError | None = None
    all_secrets = [token] + [str(v) for k, v in params_clean.items() if "token" in k or "secret" in k]

    for attempt in range(3 if can_retry else 1):
        if attempt > 0:
            _sleep(delays[attempt - 1])
            _rewind_files(files)
        try:
            response = requests.request(
                method, GRAPH + path,
                params=None if body else params_clean,
                data=params_clean if body else None,
                files=files,
                timeout=120,
            )
        except requests.exceptions.Timeout as exc:
            last_error = GraphError(
                _redact(str(exc), *all_secrets),
                kind="network", retryable=True,
                user_message="Facebook hoặc đường truyền đang gián đoạn, thử lại sau.",
            )
            if can_retry and last_error.retryable and attempt < 2:
                continue
            raise last_error from exc
        except requests.exceptions.RequestException as exc:
            last_error = GraphError(
                _redact(str(exc), *all_secrets),
                kind="network", retryable=True,
                user_message="Facebook hoặc đường truyền đang gián đoạn, thử lại sau.",
            )
            if can_retry and last_error.retryable and attempt < 2:
                continue
            raise last_error from exc

        try:
            data = response.json()
        except ValueError:
            raw_text = _redact(response.text[:200], *all_secrets)
            err = classify_error({"error": {"message": raw_text}}, response.status_code)
            last_error = err
            if can_retry and err.retryable and attempt < 2:
                continue
            raise last_error

        if isinstance(data, dict) and "error" in data:
            err = classify_error(data, response.status_code)
            last_error = err
            if can_retry and err.retryable and attempt < 2:
                continue
            raise last_error

        return data

    raise last_error  # type: ignore[misc]


def credentials() -> tuple[str, str]:
    page_id = get_app_setting("FB_PAGE_ID").strip()
    token = get_app_setting("FB_PAGE_TOKEN").strip()
    if not page_id or not token:
        raise GraphError(
            "Chưa lưu FB_PAGE_ID / FB_PAGE_TOKEN trong Quản lý API Key.",
            kind="invalid", retryable=False,
            user_message="Chưa lưu FB_PAGE_ID / FB_PAGE_TOKEN trong Quản lý API Key.",
        )
    return page_id, token


def page_info() -> dict:
    page_id, token = credentials()
    return graph("GET", page_id, token, fields="name,link,followers_count,category")


def list_posts(limit: int = 10) -> list[dict]:
    page_id, token = credentials()
    return graph("GET", f"{page_id}/posts", token, fields=POST_FIELDS, limit=limit)["data"]


def list_scheduled() -> list[dict]:
    page_id, token = credentials()
    return graph("GET", f"{page_id}/scheduled_posts", token,
                 fields="id,message,scheduled_publish_time,permalink_url")["data"]


def edit_post(post_id: str, message: str) -> None:
    _, token = credentials()
    graph("POST", post_id, token, message=message)


def delete_post(post_id: str) -> None:
    _, token = credentials()
    graph("DELETE", post_id, token)


def _upload_photo(page_id: str, token: str, path: Path, published: bool, caption: str = "",
                  temporary: bool = False) -> str:
    params: dict = {"published": str(published).lower()}
    if caption:
        params["caption"] = caption
    if temporary:
        params["temporary"] = "true"  # required for photos attached to a scheduled post
    with path.open("rb") as handle:
        return graph("POST", f"{page_id}/photos", token,
                     files={"source": (path.name, handle)},
                     idempotent=not published, **params)["id"]


def create_post(message: str, photos: list[Path] = (), link: str = "",
                publish_at: datetime | None = None) -> str:
    """Publish now, or schedule on Facebook when `publish_at` is given. Returns the post id."""
    page_id, token = credentials()
    photos = [Path(p) for p in photos]
    if len(photos) == 1 and not link and publish_at is None:
        photo_id = _upload_photo(page_id, token, photos[0], published=True, caption=message)
        return graph("GET", photo_id, token, fields="page_story_id").get("page_story_id") or photo_id

    params: dict = {"message": message}
    if link:
        params["link"] = link
    if publish_at is not None:
        params.update(published="false", scheduled_publish_time=str(int(publish_at.timestamp())))
    for index, photo in enumerate(photos):
        media_id = _upload_photo(page_id, token, photo, published=False,
                                 temporary=publish_at is not None)
        params[f"attached_media[{index}]"] = json.dumps({"media_fbid": media_id})
    try:
        return graph("POST", f"{page_id}/feed", token, **params)["id"]
    except GraphError as exc:
        # Facebook may have created the post before the connection dropped or the 5xx came back.
        if exc.kind == "network":
            exc.user_message = ("Mất kết nối khi đăng — bài có thể đã lên Page, "
                                "kiểm tra Page trước khi duyệt lại.")
            exc.maybe_posted = True
        elif exc.kind == "transient":
            exc.user_message = ("Facebook báo lỗi tạm thời khi đăng — bài có thể đã lên Page, "
                                "kiểm tra Page trước khi duyệt lại.")
            exc.maybe_posted = True
        raise


def _worse_level(a: str, b: str) -> str:
    order = {"bad": 2, "warn": 1, "ok": 0}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def health_check(now: datetime | None = None) -> dict:
    """Read-only health check: token validity, scopes, expiry. Saves result to app_settings."""
    now = now or datetime.now().astimezone()
    level = "ok"
    messages: list[str] = []
    expires_in_days: int | None = None
    page_name = ""

    # (1) credentials present?
    try:
        page_id, token = credentials()
    except GraphError as exc:
        result: dict = {
            "checked_at": now.isoformat(),
            "level": "bad",
            "message": exc.user_message,
            "expires_in_days": None,
            "page_name": "",
        }
        set_app_setting("FB_HEALTH_JSON", json.dumps(result, ensure_ascii=False))
        return result

    # (2) page graph call
    try:
        info = graph("GET", page_id, token, fields="name")
        page_name = info.get("name", "")
    except GraphError as exc:
        if exc.kind in ("auth", "permission", "invalid"):
            level = _worse_level(level, "bad")
            messages.append(exc.user_message)
        else:
            level = _worse_level(level, "warn")
            messages.append("Facebook tạm thời không phản hồi.")

    # (3) debug_token (optional - requires FB_APP_ID and FB_APP_SECRET)
    app_id = get_app_setting("FB_APP_ID").strip()
    app_secret = get_app_setting("FB_APP_SECRET").strip()
    if app_id and app_secret:
        try:
            debug = graph("GET", "debug_token", f"{app_id}|{app_secret}", input_token=token)
            data = debug.get("data", {})
            if data.get("is_valid") is False:
                level = _worse_level(level, "bad")
                messages.append("Token Facebook không hợp lệ hoặc đã bị thu hồi.")
            else:
                candidates: list[float] = []
                for ts_key in ("expires_at", "data_access_expires_at"):
                    ts = data.get(ts_key, 0)
                    if ts and ts > 0:
                        days = (ts - now.timestamp()) / 86400
                        candidates.append(days)
                if candidates:
                    expires_in_days = math.floor(min(candidates))
                    if expires_in_days <= 0:
                        level = _worse_level(level, "bad")
                        messages.append("Token hoặc quyền dữ liệu đã hết hạn.")
                    elif expires_in_days <= 14:
                        level = _worse_level(level, "warn")
                        messages.append(f"Token/quyền dữ liệu còn {expires_in_days} ngày, cần gia hạn.")
                scopes = data.get("scopes")
                if scopes is not None and "pages_manage_posts" not in scopes:
                    level = _worse_level(level, "warn")
                    messages.append("Token thiếu quyền pages_manage_posts.")
        except Exception:
            level = _worse_level(level, "warn")
            messages.append("Không kiểm tra được hạn token.")

    if not messages:
        message_str = "Kết nối Facebook ổn định." if level == "ok" else ""
    else:
        message_str = " ".join(messages)

    result = {
        "checked_at": now.isoformat(),
        "level": level,
        "message": message_str,
        "expires_in_days": expires_in_days,
        "page_name": page_name,
    }
    set_app_setting("FB_HEALTH_JSON", json.dumps(result, ensure_ascii=False))
    return result


def read_cached_health() -> dict | None:
    """Read the last health check result from app_settings."""
    raw = get_app_setting("FB_HEALTH_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
