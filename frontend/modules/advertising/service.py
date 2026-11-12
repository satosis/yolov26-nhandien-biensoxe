"""Service layer for Google Ads & Remote Dynamic Campaigns."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_app_setting
from frontend.context.company_context import get_current_company_id
from frontend.modules.advertising.vn_provinces import build_province_list

logger = logging.getLogger(__name__)

# Google Geo Target Constants — built from vn_provinces canonical list
VIETNAM_PROVINCES = build_province_list()

PROVINCE_BY_NAME = {p["name"].lower(): p for p in VIETNAM_PROVINCES}

# Mapping of form field keys → app_setting storage keys
SETTING_KEYS = {
    "developer_token": "google_ads_developer_token",
    "customer_id": "google_ads_customer_id",
    "login_customer_id": "google_ads_login_customer_id",
    "refresh_token": "google_ads_refresh_token",
    "client_id": "google_client_id",
    "client_secret": "google_client_secret",
}
SECRET_FIELDS = {"developer_token", "refresh_token", "client_secret"}


def get_advertising_settings() -> Dict[str, str]:
    """Retrieve Google Ads configuration credentials from app_settings."""
    return {
        "developer_token": get_app_setting("google_ads_developer_token", ""),
        "customer_id": get_app_setting("google_ads_customer_id", ""),
        "client_id": get_app_setting("google_client_id", ""),
        "client_secret": get_app_setting("google_client_secret", ""),
        "refresh_token": get_app_setting("google_ads_refresh_token", ""),
        "login_customer_id": get_app_setting("google_ads_login_customer_id", ""),
    }


def get_advertising_settings_view() -> dict:
    """Return settings safe for display: secret fields are masked."""
    raw = get_advertising_settings()
    result: dict = {}
    for field_key in SETTING_KEYS:
        val = raw.get(field_key, "")
        if field_key in SECRET_FIELDS:
            if val:
                result[field_key] = {"configured": True, "hint": "•••• " + val[-4:]}
            else:
                result[field_key] = {"configured": False, "hint": "Chưa nhập"}
        else:
            result[field_key] = val
    return result


def save_advertising_settings(settings: Dict[str, str]) -> tuple[bool, str]:
    """Save Google Ads credentials into app_settings.

    Secret fields left blank are kept as-is (old value preserved).
    Plain fields left blank are cleared.
    Returns (ok, message).
    """
    id_fields = {"customer_id", "login_customer_id"}
    normalized: dict[str, str] = {}
    for field_key in SETTING_KEYS:
        v = settings.get(field_key, "").strip()

        # Normalise ID fields — digits only
        if field_key in id_fields:
            v_norm = re.sub(r"[^0-9]", "", v)
            if v_norm and len(v_norm) != 10:
                return False, "ID tài khoản Google Ads gồm 10 chữ số"
            v = v_norm

        normalized[field_key] = v

    for field_key, setting_key in SETTING_KEYS.items():
        v = normalized[field_key]
        if field_key in SECRET_FIELDS:
            # Empty → keep existing value
            if not v:
                continue
        set_app_setting(setting_key, v)

    return True, "Đã lưu cấu hình Google Ads API thành công."


def list_campaigns(status_filter: str | None = None) -> List[Dict[str, Any]]:
    """List campaigns filtered by active company context."""
    company_id = get_current_company_id()
    where_parts = []
    params: List[Any] = []

    where_parts.append("company_id = %s")
    params.append(company_id)

    if status_filter and status_filter != "all":
        where_parts.append("status = %s")
        params.append(status_filter)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = db._execute_query(f"""
        SELECT id, company_id, title, target_province, target_city, geo_id, radius_km,
               service_type, daily_budget, start_date, end_date, status,
               google_campaign_id, notes, impressions, clicks, calls, spent_amount,
               created_at, updated_at
        FROM public.advertising_campaigns
        {where_sql}
        ORDER BY 
            CASE status 
                WHEN 'active' THEN 1 
                WHEN 'scheduled' THEN 2 
                WHEN 'paused' THEN 3 
                ELSE 4 
            END,
            start_date ASC, id DESC
    """, tuple(params) if params else None, fetchall=True, commit=False) or []

    today = date.today()
    for row in rows:
        row["daily_budget_fmt"] = f"{int(row['daily_budget']):,} đ".replace(",", ".")
        row["spent_amount_fmt"] = f"{int(row['spent_amount'] or 0):,} đ".replace(",", ".")
        row["is_running_now"] = (row["status"] == "active" and row["start_date"] <= today <= row["end_date"])

        # Derived metrics
        spent = Decimal(str(row.get("spent_amount") or 0))
        clicks = int(row.get("clicks") or 0)
        calls = int(row.get("calls") or 0)
        impressions = int(row.get("impressions") or 0)

        if clicks > 0:
            cpc = spent / clicks
            row["cpc_fmt"] = f"{int(cpc):,} đ".replace(",", ".")
        else:
            row["cpc_fmt"] = "—"

        if calls > 0:
            cpc_call = spent / calls
            row["cost_per_call_fmt"] = f"{int(cpc_call):,} đ".replace(",", ".")
        else:
            row["cost_per_call_fmt"] = "—"

        if impressions > 0:
            ctr = clicks / impressions * 100
            row["ctr_fmt"] = f"{ctr:.2f}%"
        else:
            row["ctr_fmt"] = "—"

    return rows


def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve single campaign details by ID."""
    company_id = get_current_company_id()
    row = db._execute_query("""
        SELECT * FROM public.advertising_campaigns
        WHERE id = %s AND (company_id = %s OR company_id IS NULL OR %s IS NULL)
    """, (campaign_id, company_id, company_id), fetch=True, commit=False)
    return row


def update_campaign_metrics(
    campaign_id: int,
    impressions: int,
    clicks: int,
    calls: int,
    spent_amount: Decimal | int | float | str,
) -> tuple[bool, str]:
    """Update manually-entered performance metrics for a campaign.

    Validates non-negative integers for impressions/clicks/calls and
    non-negative Decimal for spent_amount. Respects company_id scope via
    get_campaign().
    """
    # Validation
    for name, val in [("impressions", impressions), ("clicks", clicks), ("calls", calls)]:
        if not isinstance(val, int) or val < 0:
            return False, f"Giá trị '{name}' phải là số nguyên không âm."
    try:
        spent_dec = Decimal(str(spent_amount))
        if spent_dec < Decimal("0"):
            return False, "Số tiền chi tiêu phải là số không âm."
    except Exception:
        return False, "Số tiền chi tiêu không hợp lệ."

    # Check campaign exists and belongs to current company
    if not get_campaign(campaign_id):
        return False, "Không tìm thấy chiến dịch."

    db._execute_query("""
        UPDATE public.advertising_campaigns
        SET impressions = %s,
            clicks      = %s,
            calls       = %s,
            spent_amount = %s,
            updated_at  = NOW()
        WHERE id = %s
    """, (impressions, clicks, calls, spent_dec, campaign_id), commit=True)

    return True, "Đã cập nhật số liệu."


# Loại dịch vụ của chiến dịch: (giá trị, nhãn trong form, nhãn thẻ trong danh sách, màu thẻ).
SERVICE_TYPES = (
    ("crane_weights", "Cả xe cẩu & quả cân thử tải", "Xe cẩu & Quả cân trọn gói", "info"),
    ("crane", "Chỉ xe cẩu (cẩu ca, cẩu máy, tiện chuyến)", "Xe cẩu tự hành", "primary"),
    ("weights", "Chỉ quả cân (kiểm định trạm cân xe tải)", "Quả cân kiểm định", "warning"),
    ("container", "Cho thuê container (văn phòng, kho)", "Cho thuê container", "secondary"),
    ("solar", "Thiết bị điện mặt trời", "Thiết bị điện mặt trời", "success"),
)

# Từ khóa mặc định của các dịch vụ sản phẩm; {prov} là tỉnh nhắm tới.
PRODUCT_KEYWORDS = {
    "container": [
        "thuê container {prov}",
        "cho thuê container {prov}",
        "thuê container văn phòng {prov}",
        "cho thuê container kho {prov}",
        "giá thuê container {prov}",
        "cẩu hạ container {prov}",
    ],
    "solar": [
        "thiết bị điện mặt trời {prov}",
        "mua thiết bị điện mặt trời {prov}",
        "tấm pin mặt trời {prov}",
        "giá tấm pin mặt trời {prov}",
        "biến tần điện mặt trời {prov}",
        "pin lưu trữ điện mặt trời {prov}",
    ],
}


def generate_default_keywords(target_province: str, service_type: str) -> List[str]:
    """Generate high-converting keywords tailored to destination and services."""
    prov = target_province.strip()
    kws = []

    if service_type in PRODUCT_KEYWORDS:
        return [keyword.format(prov=prov) for keyword in PRODUCT_KEYWORDS[service_type]]
    
    # 1. Cẩu tại chỗ & Cẩu gần đây
    if service_type in ("crane", "crane_weights"):
        kws.extend([
            f"thuê xe cẩu tại {prov}",
            f"xe cẩu tự hành {prov}",
            f"xe cẩu {prov} giá rẻ",
            f"thuê cẩu máy gấp {prov}",
            f"cần thuê xe cẩu {prov}",
            "xe cẩu gần đây",
        ])

    # 2. Quả cân kiểm định & thử tải
    if service_type in ("weights", "crane_weights"):
        kws.extend([
            f"thuê quả cân kiểm định {prov}",
            f"quả cân chuẩn trạm cân {prov}",
            f"kiểm định trạm cân xe tải {prov}",
            f"thuê quả cân thử tải cẩu {prov}",
            f"xe cẩu thả quả cân {prov}",
        ])

    # 3. Hàng tiện chuyến / Chiều về Phú Thọ
    kws.extend([
        f"xe cẩu tiện chuyến từ {prov} về phú thọ",
        f"xe cẩu quay đầu {prov} về việt trì",
        f"gửi máy móc từ {prov} về phú thọ",
        f"vận chuyển thiết bị {prov} về phú thọ",
    ])
    return kws


def create_campaign(
    title: str,
    target_province: str,
    start_date: date | str,
    end_date: date | str,
    daily_budget: float | int | Decimal = 100000,
    radius_km: int = 25,
    service_type: str = "crane_weights",
    target_city: str = "",
    notes: str = "",
) -> tuple[bool, str, Optional[int]]:
    """Create a new dynamic trip campaign."""
    company_id = get_current_company_id() or 33
    
    if not title.strip():
        title = f"Chuyến {target_province} ({start_date} - {end_date})"

    s_date = datetime.strptime(start_date, "%Y-%m-%d").date() if isinstance(start_date, str) else start_date
    e_date = datetime.strptime(end_date, "%Y-%m-%d").date() if isinstance(end_date, str) else end_date

    if e_date < s_date:
        return False, "Ngày kết thúc không được nhỏ hơn ngày bắt đầu.", None

    prov_meta = PROVINCE_BY_NAME.get(target_province.strip().lower(), {})
    geo_id = prov_meta.get("geo_id", "")

    keywords = generate_default_keywords(target_province, service_type)
    today = date.today()
    status = "active" if s_date <= today <= e_date else ("scheduled" if s_date > today else "completed")

    res = db._execute_query("""
        INSERT INTO public.advertising_campaigns
        (company_id, title, target_province, target_city, geo_id, radius_km,
         service_type, daily_budget, start_date, end_date, status, keywords, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        company_id, title.strip(), target_province.strip(), target_city.strip(), geo_id, radius_km,
        service_type, Decimal(str(daily_budget)), s_date, e_date, status, json.dumps(keywords), notes.strip()
    ), fetch=True, commit=True)

    if res and res.get("id"):
        return True, f"Đã lên lịch chiến dịch quảng cáo tại {target_province} thành công!", res["id"]
    return False, "Lỗi khi lưu chiến dịch quảng cáo.", None


def toggle_campaign_status(campaign_id: int) -> tuple[bool, str, str]:
    """Toggle status between active and paused."""
    row = get_campaign(campaign_id)
    if not row:
        return False, "Không tìm thấy chiến dịch.", ""
    
    curr = row["status"]
    new_status = "paused" if curr == "active" else "active"
    
    db._execute_query("""
        UPDATE public.advertising_campaigns
        SET status = %s, updated_at = NOW()
        WHERE id = %s
    """, (new_status, campaign_id), commit=True)

    msg = "Đã tạm dừng quảng cáo." if new_status == "paused" else "Đã kích hoạt quảng cáo thành công."
    return True, msg, new_status


def delete_campaign(campaign_id: int) -> tuple[bool, str]:
    """Delete a campaign by ID."""
    db._execute_query("""
        DELETE FROM public.advertising_campaigns WHERE id = %s
    """, (campaign_id,), commit=True)
    return True, "Đã xóa chiến dịch quảng cáo thành công."


def get_advertising_kpi_summary() -> Dict[str, Any]:
    """Get high level summary stats for active campaigns."""
    company_id = get_current_company_id()
    row = db._execute_query("""
        SELECT 
            COUNT(*) as total_campaigns,
            COUNT(*) FILTER (WHERE status = 'active') as active_count,
            COUNT(*) FILTER (WHERE status = 'scheduled') as scheduled_count,
            COALESCE(SUM(spent_amount), 0) as total_spent,
            COALESCE(SUM(impressions), 0) as total_impressions,
            COALESCE(SUM(clicks), 0) as total_clicks,
            COALESCE(SUM(calls), 0) as total_calls
        FROM public.advertising_campaigns
        WHERE company_id = %s
    """, (company_id,), fetch=True, commit=False) or {}

    total_spent = Decimal(str(row.get("total_spent") or 0))
    total_calls = int(row.get("total_calls") or 0)

    if total_calls > 0:
        cost_per_call_fmt = f"{int(total_spent / total_calls):,} đ".replace(",", ".")
    else:
        cost_per_call_fmt = "—"

    return {
        "total_campaigns": int(row.get("total_campaigns") or 0),
        "active_count": int(row.get("active_count") or 0),
        "scheduled_count": int(row.get("scheduled_count") or 0),
        "total_spent_fmt": f"{int(total_spent):,} đ".replace(",", "."),
        "total_impressions": int(row.get("total_impressions") or 0),
        "total_clicks": int(row.get("total_clicks") or 0),
        "total_calls": total_calls,
        "cost_per_call_fmt": cost_per_call_fmt,
    }


def get_advertising_resources() -> dict:
    """Read stored advertising plans, keyword files and copy templates from quang_cao/ directory."""
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent.parent.parent.parent / "quang_cao"
    
    def _read_file(subpath: str) -> str:
        fp = base_dir / subpath
        if fp.exists():
            return fp.read_text(encoding="utf-8")
        return ""

    return {
        "tu_khoa_xe_cau": _read_file("bo_tu_khoa/tu_khoa_xe_cau.txt"),
        "tu_khoa_qua_can": _read_file("bo_tu_khoa/tu_khoa_qua_can.txt"),
        "tu_khoa_chuyen_ve": _read_file("bo_tu_khoa/tu_khoa_chuyen_ve_cac_tinh.txt"),
        "tu_khoa_phu_dinh": _read_file("bo_tu_khoa/tu_khoa_phu_dinh.txt"),
        "tu_khoa_container": _read_file("bo_tu_khoa/tu_khoa_container.txt"),
        "ad_copy": _read_file("mau_noi_dung_ad_copy/mau_quang_cao_xe_cau_qua_can.md"),
        "ad_copy_container": _read_file("mau_noi_dung_ad_copy/mau_quang_cao_container.md"),
        "tu_khoa_dien_mat_troi": _read_file("bo_tu_khoa/tu_khoa_dien_mat_troi.txt"),
        "ad_copy_dien_mat_troi": _read_file("mau_noi_dung_ad_copy/mau_quang_cao_dien_mat_troi.md"),
        "ke_hoach": _read_file("ke_hoach_chien_dich/ke_hoach_tong_the.md"),
    }


# Bộ từ khóa / mẫu quảng cáo hiển thị ở màn Kế hoạch & Từ khóa: (khóa, nhãn, khóa trong get_advertising_resources).
KEYWORD_SETS = (
    ("xe_cau", "Xe cẩu Phú Thọ", "tu_khoa_xe_cau"),
    ("qua_can", "Quả cân kiểm định", "tu_khoa_qua_can"),
    ("container", "Thuê container", "tu_khoa_container"),
    ("dien_mat_troi", "Thiết bị điện mặt trời", "tu_khoa_dien_mat_troi"),
    ("chuyen_ve", "Chiều về Tây Bắc", "tu_khoa_chuyen_ve"),
    ("phu_dinh", "Từ khóa phủ định", "tu_khoa_phu_dinh"),
    ("ad_copy", "Tiêu đề & mô tả quảng cáo", "ad_copy"),
    ("ad_copy_container", "Quảng cáo thuê container", "ad_copy_container"),
    ("ad_copy_dien_mat_troi", "Quảng cáo điện mặt trời", "ad_copy_dien_mat_troi"),
)


def parse_keyword_text(text: str, negative: bool = False) -> list[dict]:
    """Tách file từ khóa thành từng dòng: "cụm từ" / [chính xác] / từ thường (phủ định hoặc khớp rộng), kèm nhóm."""
    items: list[dict] = []
    group = "Chung"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().strip("-").strip()
            if stripped.lstrip("#").strip().startswith("---") and title:
                group = title.title()
            continue
        if len(stripped) > 1 and stripped[0] == '"' and stripped[-1] == '"':
            match, keyword = "Cụm từ", stripped[1:-1]
        elif len(stripped) > 1 and stripped[0] == "[" and stripped[-1] == "]":
            match, keyword = "Chính xác", stripped[1:-1]
        else:
            match, keyword = ("Phủ định" if negative else "Khớp rộng"), stripped
        items.append({"text": keyword, "raw": stripped, "match": match, "group": group,
                      "length": len(keyword), "limit": 0, "over": False})
    return items


def parse_ad_copy_text(text: str) -> list[dict]:
    """Tách mẫu quảng cáo thành tiêu đề (30 ký tự), mô tả (90 ký tự) và chú thích (25 ký tự); độ dài tính lại từ nội dung."""
    items: list[dict] = []
    kind, limit, group = "", 0, ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            upper = stripped.upper()
            kind, limit = ("Tiêu đề", 30) if "TIÊU ĐỀ" in upper else ("Mô tả", 90) if "MÔ TẢ" in upper else ("", 0)
            group = ""
            continue
        if stripped.startswith("### "):
            group = stripped[4:].strip().rstrip(":")
            if "Callout" in group:
                kind, limit = "Chú thích", 25
            elif stripped.startswith("### ") and kind == "Chú thích":
                kind, limit = "", 0
            continue
        label = re.search(r"\*\*(Mô tả \d+)\*\*", stripped)
        if label:
            group = label.group(1)
            continue
        found = re.search(r"`([^`]+)`", stripped)
        if kind and found:
            content = found.group(1).strip()
            items.append({"text": content, "raw": content, "match": kind, "group": group or kind,
                          "length": len(content), "limit": limit, "over": len(content) > limit})
    return items


def get_keyword_items(set_key: str, resources: dict | None = None) -> list[dict]:
    """Các dòng của một bộ từ khóa / mẫu quảng cáo (khóa set_key trong KEYWORD_SETS)."""
    resources = resources if resources is not None else get_advertising_resources()
    source = next((resource for key, _label, resource in KEYWORD_SETS if key == set_key), None)
    if source is None:
        return []
    text = resources.get(source, "")
    if set_key.startswith("ad_copy"):
        return parse_ad_copy_text(text)
    return parse_keyword_text(text, negative=(set_key == "phu_dinh"))


def get_recent_sync_logs(limit: int = 10) -> list[dict]:
    """Lấy danh sách nhật ký đồng bộ gần nhất."""
    rows = db._execute_query("""
            SELECT id, campaign_id, action, detail, dry_run, ok, created_at
            FROM public.advertising_sync_log
            ORDER BY id DESC
            LIMIT %s
        """, (limit,), fetchall=True, commit=False) or []
    return rows if isinstance(rows, list) else []


def set_campaign_google_id(campaign_id: int, google_campaign_id: str) -> tuple[bool, str]:
    """Cập nhật hoặc gỡ liên kết google_campaign_id cho chiến dịch."""
    gid = (google_campaign_id or "").strip()
    if gid and not gid.isdigit():
        return False, "Google Campaign ID phải chỉ gồm chữ số."
    if not get_campaign(campaign_id):  # also enforces the current company scope
        return False, "Không tìm thấy chiến dịch."
    db._execute_query("""
            UPDATE public.advertising_campaigns
            SET google_campaign_id = %s, updated_at = NOW()
            WHERE id = %s
        """, (gid if gid else None, campaign_id), commit=True)
    msg = f"Đã cập nhật Google Campaign ID ({gid})" if gid else "Đã gỡ liên kết Google Campaign."
    return True, msg


def set_google_ads_live(enabled: bool, by: str = "") -> tuple[bool, str]:
    """Bật/tắt chế độ tự động thật của Google Ads API; ghi nhật ký ai đã đổi."""
    try:
        set_app_setting("google_ads_live", "1" if enabled else "0")
        logger.warning("Google Ads live mode %s by %s", "ON" if enabled else "OFF", by or "unknown")
        from backend.services.google_ads_api import record_sync_log

        record_sync_log(None, "live_on" if enabled else "live_off",
                        f"Chế độ tự động được {'bật' if enabled else 'tắt'} bởi {by or 'không rõ'}",
                        dry_run=not enabled, ok=True)
        msg = "Đã bật chế độ tự động Google Ads." if enabled else "Đã chuyển sang chế độ chạy thử."
        return True, msg
    except Exception as exc:
        return False, f"Lỗi lưu chế độ: {exc}"
