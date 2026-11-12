"""Google Ads REST API client and campaign synchronization service.

Designed according to Google Ads REST API v24/v25/v26 specifications.
Developer tokens were retired by Google on 2026-09-09; access follows OAuth Cloud Project.
Defaults to dry-run mode (google_ads_live == "1" required for live mutations).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import logging
import re
import time
from typing import Any, Callable

import requests

from backend.services.database import db
from backend.services.settings_service import get_app_setting

logger = logging.getLogger(__name__)

ADS_SYNC_LAST_AT = "ADS_SYNC_LAST_AT"
DEFAULT_API_VERSION = "v25"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_API_BASE = "https://googleads.googleapis.com"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_REDACT_PARAM_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9_\-\.~+/=]+|"
    r"(access_token|refresh_token|client_secret|developer_token|client_id)=[^&\s\"',]+",
    re.IGNORECASE,
)


def _redact(text: Any, *secrets: str) -> str:
    """Che các chuỗi bí mật, token, Bearer... trong text."""
    s = str(text or "")
    s = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.~+/=]+", "Bearer ***", s, flags=re.IGNORECASE)
    s = _REDACT_PARAM_RE.sub(lambda m: "Bearer ***" if m.group(1) else f"{m.group(2)}=***", s)
    for secret in secrets:
        if secret and isinstance(secret, str) and len(secret) >= 2:
            s = s.replace(secret, "***")
    return s


class GoogleAdsError(RuntimeError):
    """Lỗi Google Ads API có cấu trúc, phân loại lỗi và thông báo tiếng Việt."""

    def __init__(
        self,
        message: str,
        kind: str = "unknown",
        http_status: int | None = None,
        retryable: bool = False,
        user_message: str = "",
        secrets: tuple[str, ...] = (),
    ):
        redacted_msg = _redact(message, *secrets)
        redacted_user_msg = _redact(user_message or message, *secrets)
        super().__init__(redacted_msg)
        self.kind = kind
        self.http_status = http_status
        self.retryable = retryable
        self.user_message = redacted_user_msg


def load_config() -> dict[str, str]:
    """Đọc cấu hình Google Ads API từ app_settings."""
    raw_customer = (get_app_setting("google_ads_customer_id", "") or "").strip()
    raw_login_customer = (get_app_setting("google_ads_login_customer_id", "") or "").strip()
    client_id = (get_app_setting("google_client_id", "") or "").strip()
    client_secret = (get_app_setting("google_client_secret", "") or "").strip()
    refresh_token = (get_app_setting("google_ads_refresh_token", "") or "").strip()
    raw_version = (get_app_setting("google_ads_api_version", DEFAULT_API_VERSION) or DEFAULT_API_VERSION).strip()
    api_version = raw_version if re.match(r"^v\d+$", raw_version) else DEFAULT_API_VERSION
    live_val = (get_app_setting("google_ads_live", "0") or "0").strip()

    customer_id = re.sub(r"[^0-9]", "", raw_customer)
    login_customer_id = re.sub(r"[^0-9]", "", raw_login_customer)

    return {
        "customer_id": customer_id,
        "google_ads_customer_id": customer_id,
        "login_customer_id": login_customer_id,
        "google_ads_login_customer_id": login_customer_id,
        "client_id": client_id,
        "google_client_id": client_id,
        "client_secret": client_secret,
        "google_client_secret": client_secret,
        "refresh_token": refresh_token,
        "google_ads_refresh_token": refresh_token,
        "google_ads_api_version": api_version,
        "google_ads_live": live_val,
    }


def is_configured(config: dict | None) -> bool:
    """Kiểm tra cấu hình có đủ 4 khóa tối thiểu để chạy hay không."""
    if not config or not isinstance(config, dict):
        return False
    cust = config.get("customer_id") if "customer_id" in config else config.get("google_ads_customer_id")
    cid = config.get("client_id") if "client_id" in config else config.get("google_client_id")
    csec = config.get("client_secret") if "client_secret" in config else config.get("google_client_secret")
    rtok = config.get("refresh_token") if "refresh_token" in config else config.get("google_ads_refresh_token")
    return bool(cust and cid and csec and rtok)


def is_live(config: dict | None) -> bool:
    """Kiểm tra có đang bật chế độ tự động thật (google_ads_live == '1') hay không."""
    if not config or not isinstance(config, dict):
        return False
    return str(config.get("google_ads_live", "")).strip() == "1"


def _default_http(
    method: str,
    url: str,
    headers: dict | None = None,
    json_body: dict | None = None,
    form_body: dict | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    resp = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=json_body,
        data=form_body,
        timeout=timeout,
    )
    try:
        data = resp.json()
    except Exception:
        data = resp.text
    return resp.status_code, data


class GoogleAdsClient:
    """Client giao tiếp Google Ads REST API v24/v25/v26."""

    def __init__(
        self,
        config: dict | None = None,
        http: Callable[..., tuple[int, Any]] | None = None,
        sleep: Callable[[float | int], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ):
        self.config = config if config is not None else load_config()
        self._http = http or _default_http
        self._sleep = sleep
        self._now = now
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def customer_id(self) -> str:
        if "customer_id" in self.config:
            raw = self.config.get("customer_id") or ""
        else:
            raw = self.config.get("google_ads_customer_id") or ""
        return re.sub(r"[^0-9]", "", str(raw))

    @property
    def api_version(self) -> str:
        return self.config.get("google_ads_api_version") or DEFAULT_API_VERSION

    @property
    def is_live(self) -> bool:
        return is_live(self.config)

    def _secrets(self) -> tuple[str, ...]:
        secs = [
            self.config.get("client_secret", ""),
            self.config.get("google_client_secret", ""),
            self.config.get("refresh_token", ""),
            self.config.get("google_ads_refresh_token", ""),
            self.config.get("developer_token", ""),
            self.config.get("google_ads_developer_token", ""),
            self._access_token or "",
            get_app_setting("google_ads_developer_token", "") or "",
            get_app_setting("google_client_secret", "") or "",
            get_app_setting("google_ads_refresh_token", "") or "",
        ]
        return tuple(s for s in secs if s and isinstance(s, str))

    def access_token(self) -> str:
        """Đổi refresh token lấy access token qua OAuth2 Google."""
        if "client_id" in self.config:
            cid = (self.config.get("client_id") or "").strip()
        else:
            cid = (self.config.get("google_client_id") or "").strip()

        if "client_secret" in self.config:
            csec = (self.config.get("client_secret") or "").strip()
        else:
            csec = (self.config.get("google_client_secret") or "").strip()

        if "refresh_token" in self.config:
            rtok = (self.config.get("refresh_token") or "").strip()
        else:
            rtok = (self.config.get("google_ads_refresh_token") or "").strip()

        if not (cid and csec and rtok):
            raise GoogleAdsError(
                "Thiếu cấu hình Google OAuth client_id/client_secret/refresh_token",
                kind="config",
                user_message="Chưa cấu hình đầy đủ thông tin OAuth Google Ads trong Quảng cáo ▸ Cấu hình API.",
                secrets=self._secrets(),
            )

        now_ts = self._now()
        if self._access_token and now_ts < self._token_expires_at:
            return self._access_token

        form_data = {
            "grant_type": "refresh_token",
            "client_id": cid,
            "client_secret": csec,
            "refresh_token": rtok,
        }

        try:
            status_code, data = self._http(
                "POST",
                OAUTH_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                json_body=None,
                form_body=form_data,
                timeout=60,
            )
        except Exception as exc:
            err_msg = _redact(f"Lỗi kết nối khi làm mới OAuth token: {exc}", *self._secrets())
            logger.warning("%s", err_msg)
            raise GoogleAdsError(
                err_msg,
                kind="transient",
                retryable=True,
                user_message="Không thể kết nối máy chủ xác thực Google — thử lại sau.",
                secrets=self._secrets(),
            ) from exc

        err_str = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
        if status_code != 200 or not isinstance(data, dict) or "access_token" not in data:
            logger.warning("Lỗi làm mới access token Google Ads: %s", _redact(err_str, *self._secrets()))
            if "invalid_grant" in err_str or "invalid_client" in err_str:
                raise GoogleAdsError(
                    f"OAuth authentication error: {err_str}",
                    kind="auth",
                    http_status=status_code,
                    retryable=False,
                    user_message="Refresh token Google Ads đã hết hạn hoặc bị thu hồi — tạo lại và lưu ở Quảng cáo ▸ Cấu hình API.",
                    secrets=self._secrets(),
                )
            raise GoogleAdsError(
                f"Lỗi lấy access token Google Ads ({status_code}): {err_str}",
                kind="auth" if status_code in (400, 401) else "transient",
                http_status=status_code,
                retryable=(status_code >= 500),
                user_message="Không thể lấy Access Token Google Ads — kiểm tra lại thông tin cấu hình.",
                secrets=self._secrets(),
            )

        token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 3600))
        self._access_token = token
        self._token_expires_at = now_ts + expires_in - 60
        return token

    def _build_headers(self, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if "login_customer_id" in self.config:
            login_cust = self.config.get("login_customer_id") or ""
        else:
            login_cust = self.config.get("google_ads_login_customer_id") or ""
        if login_cust:
            digits_only = re.sub(r"[^0-9]", "", str(login_cust))
            if digits_only:
                headers["login-customer-id"] = digits_only
        return headers

    def _build_url(self, path: str) -> str:
        version = self.api_version
        clean_path = path.lstrip("/")
        return f"{GOOGLE_ADS_API_BASE}/{version}/{clean_path}"

    def _classify_error(self, status_code: int, data: Any) -> GoogleAdsError:
        data_str = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
        raw_msg = _redact(data_str, *self._secrets())

        if status_code == 401 or "UNAUTHENTICATED" in data_str or "AUTHENTICATION_ERROR" in data_str:
            return GoogleAdsError(
                raw_msg,
                kind="auth",
                http_status=status_code,
                retryable=False,
                user_message="Refresh token Google Ads đã hết hạn hoặc bị thu hồi — tạo lại và lưu ở Quảng cáo ▸ Cấu hình API.",
                secrets=self._secrets(),
            )

        if "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION" in data_str or "ACTION_NOT_PERMITTED" in data_str:
            return GoogleAdsError(
                raw_msg,
                kind="permission",
                http_status=status_code,
                retryable=False,
                user_message="Dự án Google Cloud chưa được duyệt chạy tài khoản thật — xin mức Explorer tại https://console.cloud.google.com/google/ads-apis/overview",
                secrets=self._secrets(),
            )

        if status_code == 403 or "PERMISSION_DENIED" in data_str or "AUTHORIZATION_ERROR" in data_str:
            return GoogleAdsError(
                raw_msg,
                kind="permission",
                http_status=status_code,
                retryable=False,
                user_message="Tài khoản Google Ads từ chối quyền truy cập — kiểm tra Customer ID và quyền hạn OAuth.",
                secrets=self._secrets(),
            )

        if status_code == 429 or "RESOURCE_EXHAUSTED" in data_str or "RATE_EXCEEDED" in data_str:
            return GoogleAdsError(
                raw_msg,
                kind="rate_limit",
                http_status=status_code,
                retryable=False,
                user_message="Google Ads API đang bị giới hạn tần suất (rate limit) — thử lại sau.",
                secrets=self._secrets(),
            )

        if "INVALID_ARGUMENT" in data_str or "QUERY_ERROR" in data_str:
            return GoogleAdsError(
                raw_msg,
                kind="invalid",
                http_status=status_code,
                retryable=False,
                user_message="Yêu cầu Google Ads không hợp lệ (lỗi cú pháp GAQL hoặc tham số).",
                secrets=self._secrets(),
            )

        if status_code == 404 or "NOT_FOUND" in data_str:
            return GoogleAdsError(
                raw_msg,
                kind="config",
                http_status=status_code,
                retryable=False,
                user_message=f"Đường dẫn API không tồn tại ({status_code}) — kiểm tra lại cấu hình google_ads_api_version (đang là {self.api_version}).",
                secrets=self._secrets(),
            )

        if status_code >= 500:
            return GoogleAdsError(
                raw_msg,
                kind="transient",
                http_status=status_code,
                retryable=True,
                user_message="Máy chủ Google Ads gặp sự cố tạm thời (5xx) — thử lại sau.",
                secrets=self._secrets(),
            )

        return GoogleAdsError(
            raw_msg,
            kind="unknown",
            http_status=status_code,
            retryable=False,
            user_message=f"Lỗi gọi Google Ads API ({status_code}).",
            secrets=self._secrets(),
        )

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        """Gửi yêu cầu HTTP đến Google Ads REST API kèm xử lý 401 refresh và 429/5xx retry."""
        url = self._build_url(path)
        token = self.access_token()
        headers = self._build_headers(token)

        backoffs = [1, 3]
        max_attempts = 3
        attempt = 0
        refreshed_on_401 = False

        while attempt < max_attempts:
            attempt += 1
            try:
                status_code, data = self._http(
                    method,
                    url,
                    headers=headers,
                    json_body=body,
                    form_body=None,
                    timeout=60,
                )
            except Exception as exc:
                if attempt < max_attempts:
                    sleep_time = backoffs[attempt - 1]
                    self._sleep(sleep_time)
                    continue
                err_msg = _redact(f"Lỗi mạng khi gọi Google Ads API: {exc}", *self._secrets())
                logger.warning("%s", err_msg)
                raise GoogleAdsError(
                    err_msg,
                    kind="transient",
                    retryable=True,
                    user_message="Không thể kết nối tới Google Ads API — vui lòng thử lại sau.",
                    secrets=self._secrets(),
                ) from exc

            if 200 <= status_code < 300:
                return data

            if status_code == 401 and not refreshed_on_401:
                refreshed_on_401 = True
                self._access_token = None
                self._token_expires_at = 0.0
                token = self.access_token()
                headers = self._build_headers(token)
                continue

            if (status_code == 429 or status_code >= 500) and attempt < max_attempts:
                sleep_time = backoffs[attempt - 1]
                self._sleep(sleep_time)
                continue

            err = self._classify_error(status_code, data)
            logger.warning("Google Ads API lỗi %s (%s): %s", status_code, err.kind, err.args[0])
            raise err

    def search(self, query: str, max_pages: int = 20) -> list[dict]:
        """Thực thi câu truy vấn GAQL qua googleAds:search và nối các trang theo nextPageToken."""
        cust_id = self.customer_id
        if not cust_id:
            raise GoogleAdsError("Chưa cấu hình customer_id", kind="config", user_message="Thiếu Customer ID Google Ads.")
        path = f"customers/{cust_id}/googleAds:search"
        results: list[dict] = []
        page_token = None
        for _ in range(max_pages):
            body: dict[str, Any] = {"query": query}
            if page_token:
                body["pageToken"] = page_token
            resp = self.request("POST", path, body=body)
            if isinstance(resp, dict):
                rows = resp.get("results")
                if isinstance(rows, list):
                    results.extend(rows)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            else:
                break
        return results

    def test_connection(self) -> dict[str, Any]:
        """Kiểm tra kết nối và tính hợp lệ của tài khoản Google Ads."""
        try:
            rows = self.search("SELECT customer.id, customer.descriptive_name, customer.currency_code FROM customer LIMIT 1")
            if not rows:
                return {"ok": False, "message": "Không tìm thấy thông tin tài khoản trên Google Ads."}
            cust = rows[0].get("customer", {})
            name = cust.get("descriptiveName") if "descriptiveName" in cust else cust.get("descriptive_name", "")
            currency = cust.get("currencyCode") if "currencyCode" in cust else cust.get("currency_code", "")
            warning = f"Tài khoản đang dùng tiền tệ {currency} (khuyến nghị VND)" if currency and currency != "VND" else ""
            res: dict[str, Any] = {"ok": True, "name": name, "currency": currency}
            if warning:
                res["warning"] = warning
            return res
        except GoogleAdsError as exc:
            return {"ok": False, "message": exc.user_message}
        except Exception as exc:
            return {"ok": False, "message": _redact(str(exc), *self._secrets())}

    def fetch_campaigns(self) -> list[dict]:
        """Lấy danh sách chiến dịch Google Ads chưa bị REMOVED."""
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, campaign_budget.amount_micros "
            "FROM campaign WHERE campaign.status != 'REMOVED'"
        )
        rows = self.search(query)
        campaigns = []
        for r in rows:
            camp = r.get("campaign", {})
            cid = str(camp.get("id", ""))
            name = str(camp.get("name", ""))
            status = str(camp.get("status", ""))
            budget_obj = r.get("campaignBudget") or r.get("campaign_budget") or {}
            micros_raw = budget_obj.get("amountMicros") if "amountMicros" in budget_obj else budget_obj.get("amount_micros", 0)
            try:
                micros = int(float(micros_raw or 0))
                daily_budget = Decimal(str(micros)) / Decimal(1_000_000)
            except Exception:
                daily_budget = Decimal(0)
            campaigns.append({
                "id": cid,
                "name": name,
                "status": status,
                "daily_budget": daily_budget,
            })
        return campaigns

    def fetch_metrics(self, google_campaign_id: Any, start: Any, end: Any) -> dict[str, Any]:
        """Kéo số liệu hiệu quả (impressions, clicks, calls, cost) của chiến dịch Google Ads."""
        gid_str = str(google_campaign_id).strip()
        if not gid_str.isdigit():
            raise ValueError(f"google_campaign_id phải chỉ gồm chữ số: {google_campaign_id}")

        start_iso = start.isoformat() if isinstance(start, (date, datetime)) else str(start).strip()
        end_iso = end.isoformat() if isinstance(end, (date, datetime)) else str(end).strip()

        if not (_ISO_DATE_RE.fullmatch(start_iso) and _ISO_DATE_RE.fullmatch(end_iso)):
            raise ValueError(f"Ngày phải có dạng YYYY-MM-DD: {start_iso!r}, {end_iso!r}")

        query = (
            f"SELECT metrics.impressions, metrics.clicks, metrics.cost_micros, "
            f"metrics.conversions, metrics.phone_calls "
            f"FROM campaign WHERE campaign.id = {gid_str} "
            f"AND segments.date BETWEEN '{start_iso}' AND '{end_iso}'"
        )
        rows = self.search(query)

        tot_imp = 0
        tot_clicks = 0
        tot_cost_micros = 0
        tot_conversions = 0.0
        tot_calls = 0

        for r in rows:
            m = r.get("metrics", {})
            imp = m.get("impressions", 0)
            clk = m.get("clicks", 0)
            cost_m = m.get("costMicros") if "costMicros" in m else m.get("cost_micros", 0)
            conv = m.get("conversions", 0)
            calls = m.get("phoneCalls") if "phoneCalls" in m else m.get("phone_calls", 0)

            tot_imp += int(float(imp or 0))
            tot_clicks += int(float(clk or 0))
            tot_cost_micros += int(float(cost_m or 0))
            tot_conversions += float(conv or 0)
            tot_calls += int(float(calls or 0))

        cost_dec = Decimal(str(tot_cost_micros)) / Decimal(1_000_000)
        return {
            "impressions": tot_imp,
            "clicks": tot_clicks,
            "calls": tot_calls,
            "conversions": tot_conversions,
            "cost": cost_dec,
        }

    def set_campaign_status(self, google_campaign_id: Any, enabled: bool) -> dict[str, Any]:
        """Cập nhật trạng thái chiến dịch Google Ads (ENABLED hoặc PAUSED). Mặc định dry-run."""
        gid_str = str(google_campaign_id).strip()
        if not gid_str.isdigit():
            raise ValueError(f"google_campaign_id phải chỉ gồm chữ số: {google_campaign_id}")

        target_status = "ENABLED" if enabled else "PAUSED"
        if not self.is_live:
            logger.info("Dry-run set_campaign_status: campaign %s -> %s", gid_str, target_status)
            return {"dry_run": True, "would_set": target_status}

        cust_id = self.customer_id
        resource_name = f"customers/{cust_id}/campaigns/{gid_str}"
        body = {
            "operations": [
                {
                    "updateMask": "status",
                    "update": {
                        "resourceName": resource_name,
                        "status": target_status,
                    },
                }
            ]
        }
        resp = self.request("POST", f"customers/{cust_id}/campaigns:mutate", body=body)
        res_name = resource_name
        if isinstance(resp, dict):
            res_list = resp.get("results", [])
            if res_list and isinstance(res_list, list) and isinstance(res_list[0], dict):
                res_name = res_list[0].get("resourceName") or resource_name
        return {"dry_run": False, "resource": res_name}


# ==========================================
# ĐỒNG BỘ DỮ LIỆU VÀ TRẠNG THÁI (SYNC ENGINE)
# ==========================================


def record_sync_log(
    campaign_id: int | None,
    action: str,
    detail: str,
    dry_run: bool,
    ok: bool,
) -> None:
    """Ghi một bản ghi nhật ký đồng bộ."""
    db._execute_query("""
            INSERT INTO public.advertising_sync_log (campaign_id, action, detail, dry_run, ok)
            VALUES (%s, %s, %s, %s, %s)
        """, (campaign_id, action, detail, dry_run, ok), commit=True)


def link_by_name(client: GoogleAdsClient) -> int:
    """Tự động liên kết chiến dịch nội bộ với Google Ads nếu trùng đúng tên duy nhất."""
    google_camps = client.fetch_campaigns()

    def _norm(s: Any) -> str:
        return " ".join(str(s or "").strip().lower().split())

    g_by_name: dict[str, list[dict]] = {}
    for gc in google_camps:
        n = _norm(gc.get("name", ""))
        if n:
            g_by_name.setdefault(n, []).append(gc)

    rows = db._execute_query("""
            SELECT id, title FROM public.advertising_campaigns
            WHERE google_campaign_id IS NULL OR google_campaign_id = ''
        """, fetchall=True, commit=False) or []

    linked_count = 0
    for r in rows:
        title_norm = _norm(r.get("title", ""))
        matches = g_by_name.get(title_norm, [])
        if len(matches) == 1:
            matched_gid = matches[0]["id"]
            db._execute_query("""
                    UPDATE public.advertising_campaigns
                    SET google_campaign_id = %s, updated_at = NOW()
                    WHERE id = %s
                """, (matched_gid, r["id"]), commit=True)
            record_sync_log(
                r["id"],
                "linked",
                f"Đã liên kết với chiến dịch Google {matched_gid} ({matches[0].get('name')})",
                dry_run=False,
                ok=True,
            )
            linked_count += 1
        elif len(matches) > 1:
            record_sync_log(
                r["id"],
                "link_skipped",
                f"Nhiều chiến dịch Google trùng tên '{r.get('title')}' ({len(matches)} ứng viên)",
                dry_run=False,
                ok=False,
            )

    return linked_count


def pull_metrics(client: GoogleAdsClient, today: date | None = None) -> int:
    """Kéo số liệu hiệu năng từ Google Ads về lưu vào database."""
    if today is None:
        today = date.today()

    rows = db._execute_query("""
            SELECT id, google_campaign_id, start_date, end_date
            FROM public.advertising_campaigns
            WHERE google_campaign_id IS NOT NULL AND google_campaign_id <> ''
        """, fetchall=True, commit=False) or []

    updated_count = 0
    for r in rows:
        s_date = r.get("start_date")
        e_date = r.get("end_date")
        if isinstance(s_date, str):
            try:
                s_date = date.fromisoformat(s_date)
            except Exception:
                pass
        if isinstance(e_date, str):
            try:
                e_date = date.fromisoformat(e_date)
            except Exception:
                pass

        # Bỏ qua chiến dịch chưa tới start_date
        if isinstance(s_date, date) and s_date > today:
            continue

        effective_end = min(e_date, today) if isinstance(e_date, date) else today
        gid = str(r.get("google_campaign_id", "")).strip()
        if not gid.isdigit():
            continue

        m = client.fetch_metrics(gid, s_date, effective_end)
        db._execute_query("""
                UPDATE public.advertising_campaigns
                SET impressions = %s, clicks = %s, calls = %s, spent_amount = %s, updated_at = NOW()
                WHERE id = %s
            """, (m["impressions"], m["clicks"], m["calls"], m["cost"], r["id"]), commit=True)
        updated_count += 1

    return updated_count


def desired_google_status(local_status: str, start_date: Any, end_date: Any, today: date) -> str:
    """Tính trạng thái Google Ads mong muốn: ENABLED nếu active và trong hạn, còn lại PAUSED."""
    if isinstance(start_date, str):
        try:
            start_date = date.fromisoformat(start_date)
        except Exception:
            pass
    if isinstance(end_date, str):
        try:
            end_date = date.fromisoformat(end_date)
        except Exception:
            pass
    if local_status == "active" and isinstance(start_date, date) and isinstance(end_date, date):
        if start_date <= today <= end_date:
            return "ENABLED"
    return "PAUSED"


def push_statuses(client: GoogleAdsClient, today: date | None = None, max_changes: int = 10) -> list[dict]:
    """So sánh và cập nhật trạng thái chiến dịch lên Google Ads."""
    if today is None:
        today = date.today()

    google_camps_list = client.fetch_campaigns()
    google_camps = {c["id"]: c for c in google_camps_list}

    rows = db._execute_query("""
            SELECT id, title, status, start_date, end_date, google_campaign_id
            FROM public.advertising_campaigns
            WHERE google_campaign_id IS NOT NULL AND google_campaign_id <> ''
            ORDER BY id ASC
        """, fetchall=True, commit=False) or []

    changes: list[dict] = []
    for r in rows:
        if len(changes) >= max_changes:
            break
        gid = str(r.get("google_campaign_id", "")).strip()
        g_camp = google_camps.get(gid)
        if not g_camp:
            continue
        curr_g_status = g_camp.get("status", "")
        if curr_g_status == "REMOVED":
            continue

        s_date = r.get("start_date")
        e_date = r.get("end_date")
        if isinstance(s_date, str):
            try:
                s_date = date.fromisoformat(s_date)
            except Exception:
                pass
        if isinstance(e_date, str):
            try:
                e_date = date.fromisoformat(e_date)
            except Exception:
                pass

        target_status = desired_google_status(r.get("status", ""), s_date, e_date, today)
        if curr_g_status == target_status:
            continue

        # KHÔNG BAO GIỜ bật chiến dịch có end_date < today
        if target_status == "ENABLED" and isinstance(e_date, date) and e_date < today:
            continue

        res = client.set_campaign_status(gid, enabled=(target_status == "ENABLED"))
        dry_run_val = res.get("dry_run", True)
        record_sync_log(
            r["id"],
            "status_change",
            f"{r.get('title')}: {curr_g_status} -> {target_status}",
            dry_run=dry_run_val,
            ok=True,
        )
        changes.append({
            "campaign_id": r["id"],
            "title": r.get("title", ""),
            "google_campaign_id": gid,
            "from": curr_g_status,
            "to": target_status,
            "dry_run": dry_run_val,
        })

    return changes


def sync_all(client: GoogleAdsClient | None = None, today: date | None = None) -> dict[str, Any]:
    """Thực hiện toàn bộ quy trình đồng bộ: kiểm tra, link, pull metrics, push statuses."""
    if today is None:
        today = date.today()
    if client is None:
        client = GoogleAdsClient()

    if not is_configured(client.config):
        return {
            "ok": False,
            "linked": 0,
            "metrics_updated": 0,
            "changes": [],
            "dry_run": True,
            "message": "Google Ads API chưa được cấu hình.",
        }

    conn = client.test_connection()
    if not conn.get("ok"):
        record_sync_log(
            None,
            "sync_failed",
            f"Kiểm tra kết nối thất bại: {conn.get('message')}",
            dry_run=not client.is_live,
            ok=False,
        )
        return {
            "ok": False,
            "linked": 0,
            "metrics_updated": 0,
            "changes": [],
            "dry_run": not client.is_live,
            "message": conn.get("message", "Kết nối Google Ads thất bại."),
        }

    linked = link_by_name(client)
    metrics_updated = pull_metrics(client, today=today)
    changes = push_statuses(client, today=today)

    return {
        "ok": True,
        "linked": linked,
        "metrics_updated": metrics_updated,
        "changes": changes,
        "dry_run": not client.is_live,
        "message": "Đồng bộ Google Ads thành công.",
    }
