"""Kiểm tra sẵn sàng marketing — đọc DB/cài đặt, không gọi mạng, không ném lỗi."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta

from backend.services.settings_service import get_app_setting

logger = logging.getLogger(__name__)


def collect_checks() -> list[dict]:
    """Trả danh sách kiểm tra sẵn sàng, mỗi mục: {key, label, level, detail, fix_url}."""
    from backend.services.database import db  # import muộn để test có thể patch

    results: list[dict] = []

    def ok(key, label, detail="", fix_url=""):
        results.append({"key": key, "label": label, "level": "ok", "detail": detail, "fix_url": fix_url})

    def warn(key, label, detail="", fix_url=""):
        results.append({"key": key, "label": label, "level": "warn", "detail": detail, "fix_url": fix_url})

    def bad(key, label, detail="", fix_url=""):
        results.append({"key": key, "label": label, "level": "bad", "detail": detail, "fix_url": fix_url})

    # --- gtm / Google tag --- (dùng MỘT trong hai: Tag Manager, hoặc Google tag của Google Ads cài thẳng vào website)
    gtm_id = (get_app_setting("GTM_ID", "") or "").strip()
    gtm_ok = bool(gtm_id and re.match(r"^GTM-[A-Z0-9]{4,12}$", gtm_id))
    gads_tag = (get_app_setting("GADS_TAG_ID", "") or "").strip().upper()
    gads_ok = bool(re.match(r"^AW-\d{6,15}$", gads_tag))
    label = "Thẻ theo dõi (GTM hoặc Google tag)"
    if gtm_ok and gads_ok:
        warn("gtm", label, "Đang có cả GTM và Google tag của Google Ads — dễ đếm đôi chuyển đổi, chỉ nên dùng một", "/admin/api-keys")
    elif gtm_ok:
        ok("gtm", label, f"GTM ID: {gtm_id}", "/admin/api-keys")
    elif gads_ok:
        ok("gtm", label, f"Google tag: {gads_tag}", "/admin/api-keys")
    else:
        bad("gtm", label, "Chưa có GTM_ID hoặc GADS_TAG_ID hợp lệ — không đo được cuộc gọi/form", "/admin/api-keys")

    # --- conversion labels (only needed when the Google tag is used directly) ---
    if gads_ok and not gtm_ok:
        keys = ("GADS_LABEL_LEAD", "GADS_LABEL_CALL", "GADS_LABEL_ZALO")
        filled = [key for key in keys if (get_app_setting(key, "") or "").strip()]
        if filled:
            ok("gads_labels", "Chuyển đổi Google Ads", f"Đã nhập {len(filled)}/{len(keys)} nhãn chuyển đổi", "/admin/api-keys")
        else:
            warn("gads_labels", "Chuyển đổi Google Ads",
                 "Chưa nhập nhãn chuyển đổi — Google Ads chưa nhận lượt gửi form, bấm gọi, bấm Zalo", "/admin/api-keys")

    # --- pixel ---
    pixel_id = (get_app_setting("FB_PIXEL_ID", "") or "").strip()
    if gtm_ok and pixel_id:
        warn("pixel", "Facebook Pixel", "Có thể đếm đôi nếu Pixel đã nằm trong GTM", "/admin/api-keys")
    elif pixel_id and not gtm_ok:
        warn("pixel", "Facebook Pixel", "Chỉ có Pixel, không có GTM", "/admin/api-keys")
    else:
        ok("pixel", "Facebook Pixel", "Không dùng Pixel trực tiếp (GTM quản lý hoặc không dùng)", "/admin/api-keys")

    # --- hotline ---
    phone = (get_app_setting("LANDING_PHONE", "") or "").strip()
    if phone:
        ok("hotline", "Số hotline landing", phone, "/admin/api-keys")
    else:
        warn("hotline", "Số hotline landing", "Đang dùng số mặc định của từng trang", "/admin/api-keys")

    # --- zalo ---
    zalo = (get_app_setting("LANDING_ZALO", "") or "").strip()
    if zalo:
        ok("zalo", "Zalo landing", zalo, "/admin/api-keys")
    else:
        warn("zalo", "Zalo landing", "Chưa cấu hình số Zalo", "/admin/api-keys")

    # --- google_api --- (Google bỏ developer token từ 09/09/2026; login_customer_id chỉ cần khi truy cập qua tài khoản quản lý)
    required = ("google_ads_customer_id", "google_client_id", "google_client_secret", "google_ads_refresh_token")
    if all((get_app_setting(key, "") or "").strip() for key in required):
        ok("google_api", "Google Ads API", "Đã nhập đủ thông tin kết nối", "/admin/advertising")
    else:
        warn("google_api", "Google Ads API", "Thiếu thông tin kết nối — bật/tắt chiến dịch thủ công", "/admin/advertising")

    # --- google_live ---
    is_live_val = (get_app_setting("google_ads_live", "0") or "0").strip() == "1"
    if is_live_val:
        ok("google_live", "Tự động Google Ads", "Đang bật chế độ tự động", "/admin/advertising")
    else:
        warn("google_live", "Tự động Google Ads", "Chưa bật tự động — bật/tắt thủ công", "/admin/advertising")

    # --- campaigns ---
    try:
        from datetime import date
        thirty_ago = (date.today() - timedelta(days=30)).isoformat()
        row = db._execute_query(
            "SELECT COUNT(*) as cnt FROM public.advertising_campaigns "
            "WHERE status IN ('scheduled','active') OR (status='completed' AND end_date >= %s)",
            (thirty_ago,), fetch=True, commit=False
        )
        if row and int(row.get("cnt") or 0) > 0:
            ok("campaigns", "Chiến dịch quảng cáo", "Có chiến dịch đang chạy hoặc đã chạy gần đây", "/admin/advertising")
        else:
            warn("campaigns", "Chiến dịch quảng cáo", "Chưa có chiến dịch nào", "/admin/advertising")
    except Exception as e:
        warn("campaigns", "Chiến dịch quảng cáo", f"Không đọc được dữ liệu: {e}", "/admin/advertising")

    # --- facebook_credentials ---
    fb_page_id = (get_app_setting("FB_PAGE_ID", "") or "").strip()
    fb_page_token = (get_app_setting("FB_PAGE_TOKEN", "") or "").strip()
    if fb_page_id and fb_page_token:
        ok("facebook_credentials", "Facebook Page credentials", "Đã cấu hình Page ID và Token", "/admin/api-keys")
    else:
        bad("facebook_credentials", "Facebook Page credentials", "Thiếu FB_PAGE_ID hoặc FB_PAGE_TOKEN", "/admin/api-keys")

    # --- facebook_health ---
    fb_health_raw = (get_app_setting("FB_HEALTH_JSON", "") or "").strip()
    if not fb_health_raw:
        warn("facebook_health", "Sức khỏe Facebook token", "Chưa kiểm tra", "/admin/api-keys")
    else:
        try:
            health = json.loads(fb_health_raw)
            level = health.get("level", "warn")
            message = health.get("message", "")
            checked_at_str = health.get("checked_at", "")
            stale = False
            if checked_at_str:
                try:
                    checked_at = datetime.fromisoformat(checked_at_str.replace("Z", "+00:00"))
                    now_utc = datetime.now(timezone.utc)
                    if checked_at.tzinfo is None:
                        checked_at = checked_at.replace(tzinfo=timezone.utc)
                    if (now_utc - checked_at) > timedelta(hours=48):
                        stale = True
                except Exception:
                    stale = True
            else:
                stale = True

            if stale:
                warn("facebook_health", "Sức khỏe Facebook token", "Kiểm tra đã cũ", "/admin/api-keys")
            elif level in ("ok", "warn", "bad"):
                results.append({
                    "key": "facebook_health",
                    "label": "Sức khỏe Facebook token",
                    "level": level,
                    "detail": message or level,
                    "fix_url": "/admin/api-keys",
                })
            else:
                warn("facebook_health", "Sức khỏe Facebook token", message or "Chưa kiểm tra", "/admin/api-keys")
        except Exception:
            warn("facebook_health", "Sức khỏe Facebook token", "Chưa kiểm tra", "/admin/api-keys")

    # --- facebook_auto ---
    fb_auto = (get_app_setting("FB_AUTO_ENABLED", "") or "").strip()
    fb_topics = (get_app_setting("FB_AUTO_TOPICS", "") or "").strip()
    if fb_auto == "1" and fb_topics:
        ok("facebook_auto", "Đăng bài Facebook tự động", "Đang bật và có chủ đề", "/admin/advertising/facebook?tab=auto")
    else:
        warn("facebook_auto", "Đăng bài Facebook tự động", "Chưa bật hoặc chưa có chủ đề", "/admin/advertising/facebook?tab=auto")

    for item in results:
        item["help_links"] = HELP_LINKS.get(item["key"], [])
    return results


# Nơi lấy từng thông tin xác thực bên ngoài (mở tab mới); mục cấu hình nội bộ (hotline, Zalo...) không có.
HELP_LINKS: dict[str, list[tuple[str, str]]] = {
    "gtm": [("Tag Manager (GTM-…)", "https://tagmanager.google.com/"),
            ("Google tag AW-… (Mục tiêu → Chuyển đổi → Thiết lập thẻ)", "https://ads.google.com/aw/conversions")],
    "gads_labels": [("Nhãn chuyển đổi: mở từng chuyển đổi → Thiết lập thẻ → phần sau dấu / trong send_to",
                     "https://ads.google.com/aw/conversions")],
    "pixel": [("Pixel ID (Trình quản lý sự kiện)", "https://business.facebook.com/events_manager2")],
    "google_api": [("Customer ID (góc trên phải Google Ads)", "https://ads.google.com/"),
                   ("Bật Google Ads API", "https://console.cloud.google.com/apis/library/googleads.googleapis.com"),
                   ("Client ID / Client secret (OAuth client)", "https://console.cloud.google.com/apis/credentials"),
                   ("Refresh token (OAuth Playground, scope adwords)", "https://developers.google.com/oauthplayground/")],
    "facebook_credentials": [("Page token (Graph API Explorer)", "https://developers.facebook.com/tools/explorer/"),
                             ("Page ID (Trang → Giới thiệu → Minh bạch)", "https://www.facebook.com/pages/?category=your_pages")],
    "facebook_health": [("Kiểm tra token (Access Token Debugger)", "https://developers.facebook.com/tools/debug/accesstoken/")],
}
