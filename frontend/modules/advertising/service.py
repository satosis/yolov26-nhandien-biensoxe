"""Service layer for Google Ads & Remote Dynamic Campaigns."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import logging
from typing import Any, Dict, List, Optional

from backend.services.database import db
from backend.services.settings_service import get_app_setting, set_app_setting
from frontend.context.company_context import get_current_company_id

logger = logging.getLogger(__name__)

# Google Geo Target Constants for Northern Vietnam & Key Industrial Hubs
VIETNAM_PROVINCES = [
    {"code": "lai_chau", "name": "Lai Châu", "geo_id": "20703", "region": "Tây Bắc"},
    {"code": "phu_tho", "name": "Phú Thọ", "geo_id": "20719", "region": "Đông Bắc"},
    {"code": "ha_noi", "name": "Hà Nội", "geo_id": "20701", "region": "Đồng bằng sông Hồng"},
    {"code": "hai_phong", "name": "Hải Phòng", "geo_id": "20700", "region": "Đồng bằng sông Hồng"},
    {"code": "vinh_phuc", "name": "Vĩnh Phúc", "geo_id": "20732", "region": "Đồng bằng sông Hồng"},
    {"code": "bac_ninh", "name": "Bắc Ninh", "geo_id": "20692", "region": "Đồng bằng sông Hồng"},
    {"code": "bac_giang", "name": "Bắc Giang", "geo_id": "20691", "region": "Đông Bắc"},
    {"code": "quang_ninh", "name": "Quảng Ninh", "geo_id": "20721", "region": "Đông Bắc"},
    {"code": "thai_nguyen", "name": "Thái Nguyên", "geo_id": "20728", "region": "Đông Bắc"},
    {"code": "lao_cai", "name": "Lào Cai", "geo_id": "20705", "region": "Tây Bắc"},
    {"code": "yen_bai", "name": "Yên Bái", "geo_id": "20733", "region": "Tây Bắc"},
    {"code": "tuyen_quang", "name": "Tuyên Quang", "geo_id": "20731", "region": "Đông Bắc"},
    {"code": "dien_bien", "name": "Điện Biên", "geo_id": "20697", "region": "Tây Bắc"},
    {"code": "son_la", "name": "Sơn La", "geo_id": "20726", "region": "Tây Bắc"},
    {"code": "hoa_binh", "name": "Hòa Bình", "geo_id": "20702", "region": "Tây Bắc"},
    {"code": "ha_nam", "name": "Hà Nam", "geo_id": "20699", "region": "Đồng bằng sông Hồng"},
    {"code": "hung_yen", "name": "Hưng Yên", "geo_id": "20704", "region": "Đồng bằng sông Hồng"},
    {"code": "hai_duong", "name": "Hải Dương", "geo_id": "20698", "region": "Đồng bằng sông Hồng"},
    {"code": "nam_dinh", "name": "Nam Định", "geo_id": "20711", "region": "Đồng bằng sông Hồng"},
    {"code": "ninh_binh", "name": "Ninh Bình", "geo_id": "20714", "region": "Đồng bằng sông Hồng"},
    {"code": "thanh_hoa", "name": "Thanh Hóa", "geo_id": "20729", "region": "Bắc Trung Bộ"},
    {"code": "nghe_an", "name": "Nghệ An", "geo_id": "20713", "region": "Bắc Trung Bộ"},
    {"code": "ha_tinh", "name": "Hà Tĩnh", "geo_id": "20700", "region": "Bắc Trung Bộ"},
]

PROVINCE_BY_NAME = {p["name"].lower(): p for p in VIETNAM_PROVINCES}


def init_advertising_tables() -> None:
    """Ensure database table exists for advertising campaigns."""
    try:
        db._execute_query("""
            CREATE TABLE IF NOT EXISTS public.advertising_campaigns (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES public.invoice_companies(id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL,
                target_province VARCHAR(100) NOT NULL,
                target_city VARCHAR(100),
                geo_id VARCHAR(50),
                radius_km INTEGER DEFAULT 25,
                service_type VARCHAR(50) NOT NULL DEFAULT 'crane_weights',
                daily_budget NUMERIC(15, 2) NOT NULL DEFAULT 100000,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
                google_campaign_id VARCHAR(50),
                keywords JSONB DEFAULT '[]'::jsonb,
                notes TEXT,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                calls INTEGER DEFAULT 0,
                spent_amount NUMERIC(15, 2) DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_adv_company ON public.advertising_campaigns(company_id);
            CREATE INDEX IF NOT EXISTS idx_adv_status ON public.advertising_campaigns(status);
            CREATE INDEX IF NOT EXISTS idx_adv_dates ON public.advertising_campaigns(start_date, end_date);
        """, commit=True)
    except Exception as e:
        logger.warning(f"Failed to ensure advertising tables: {e}")


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


def save_advertising_settings(settings: Dict[str, str]) -> bool:
    """Save Google Ads credentials into app_settings."""
    try:
        for k, v in settings.items():
            if v is not None:
                key = f"google_ads_{k}" if not k.startswith("google_") else k
                set_app_setting(key, str(v).strip())
        return True
    except Exception as e:
        logger.exception("Error saving advertising settings: %s", e)
        return False


def list_campaigns(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List campaigns filtered by active company context."""
    init_advertising_tables()
    company_id = get_current_company_id()
    where_parts = []
    params: List[Any] = []

    if company_id:
        where_parts.append("(company_id = %s OR company_id IS NULL)")
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
    return rows


def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve single campaign details by ID."""
    init_advertising_tables()
    company_id = get_current_company_id()
    row = db._execute_query("""
        SELECT * FROM public.advertising_campaigns
        WHERE id = %s AND (company_id = %s OR company_id IS NULL OR %s IS NULL)
    """, (campaign_id, company_id, company_id), fetch=True, commit=False)
    return row


def generate_default_keywords(target_province: str, service_type: str) -> List[str]:
    """Generate high-converting keywords tailored to destination and services."""
    prov = target_province.strip()
    kws = []
    
    # 1. Cẩu tại chỗ & Cẩu gần đây
    if service_type in ("crane", "crane_weights"):
        kws.extend([
            f"thuê xe cẩu tại {prov}",
            f"xe cẩu tự hành {prov}",
            f"xe cẩu {prov} giá rẻ",
            f"thuê cẩu máy gấp {prov}",
            f"cần thuê xe cẩu {prov}",
            f"xe cẩu gần đây",
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
    init_advertising_tables()
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
    init_advertising_tables()
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
        WHERE (company_id = %s OR company_id IS NULL OR %s IS NULL)
    """, (company_id, company_id), fetch=True, commit=False) or {}

    total_spent = Decimal(str(row.get("total_spent") or 0))
    return {
        "total_campaigns": int(row.get("total_campaigns") or 0),
        "active_count": int(row.get("active_count") or 0),
        "scheduled_count": int(row.get("scheduled_count") or 0),
        "total_spent_fmt": f"{int(total_spent):,} đ".replace(",", "."),
        "total_impressions": int(row.get("total_impressions") or 0),
        "total_clicks": int(row.get("total_clicks") or 0),
        "total_calls": int(row.get("total_calls") or 0),
    }


def get_advertising_resources() -> dict:
    """Read stored advertising plans, keyword files and copy templates from quang_cao/ directory."""
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent.parent.parent.parent / "quang_cao"
    
    def _read_file(subpath: str) -> str:
        fp = base_dir / subpath
        if fp.exists():
            try:
                return fp.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Error reading {fp}: {e}")
        return ""

    return {
        "tu_khoa_xe_cau": _read_file("bo_tu_khoa/tu_khoa_xe_cau.txt"),
        "tu_khoa_qua_can": _read_file("bo_tu_khoa/tu_khoa_qua_can.txt"),
        "tu_khoa_chuyen_ve": _read_file("bo_tu_khoa/tu_khoa_chuyen_ve_cac_tinh.txt"),
        "tu_khoa_phu_dinh": _read_file("bo_tu_khoa/tu_khoa_phu_dinh.txt"),
        "ad_copy": _read_file("mau_noi_dung_ad_copy/mau_quang_cao_xe_cau_qua_can.md"),
        "ke_hoach": _read_file("ke_hoach_chien_dich/ke_hoach_tong_the.md"),
    }

