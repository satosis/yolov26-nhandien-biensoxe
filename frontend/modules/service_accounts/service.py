"""Management for external transport & service portal accounts (VETC, ePass, fuel, registry, GPS, etc.)."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from backend.services.database import db

CATEGORIES = {
    "toll": {
        "name": "Thu phí tự động (VETC / ePass)",
        "badge_class": "badge-soft-primary",
        "icon": "bx-traffic-cone",
        "color": "#3b82f6",
    },
    "fuel": {
        "name": "Xăng dầu & Nhiên liệu",
        "badge_class": "badge-soft-warning",
        "icon": "bx-gas-pump",
        "color": "#f59e0b",
    },
    "registry": {
        "name": "Đăng kiểm phương tiện",
        "badge_class": "badge-soft-info",
        "icon": "bx-check-shield",
        "color": "#06b6d4",
    },
    "gps": {
        "name": "Giám sát hành trình (GPS)",
        "badge_class": "badge-soft-success",
        "icon": "bx-navigation",
        "color": "#10b981",
    },
    "insurance": {
        "name": "Bảo hiểm xe",
        "badge_class": "badge-soft-purple",
        "icon": "bx-shield-quarter",
        "color": "#8b5cf6",
    },
    "port": {
        "name": "Cảng biển & Kho bãi",
        "badge_class": "badge-soft-dark",
        "icon": "bx-anchor",
        "color": "#475569",
    },
    "invoice": {
        "name": "Hóa đơn điện tử",
        "badge_class": "badge-soft-secondary",
        "icon": "bx-receipt",
        "color": "#64748b",
    },
    "other": {
        "name": "Cổng dịch vụ khác",
        "badge_class": "badge-soft-secondary",
        "icon": "bx-globe",
        "color": "#6b7280",
    },
}

PORTAL_PRESETS = [
    {
        "id": "vetc",
        "title": "VETC - Thu phí tự động",
        "category": "toll",
        "service_name": "VETC - Cổng Khách Hàng Doanh Nghiệp",
        "portal_url": "https://auth.vetc.com.vn/realms/tasid/protocol/openid-connect/auth?response_type=code&scope=openid&client_id=customer-portal&redirect_uri=https://customer.vetc.com.vn/Account/Login?ReturnUrl=/Corporate/Home",
        "note": "Tra cứu số dư, lịch sử trừ cước BOT qua trạm, xuất hóa đơn VETC điện tử.",
    },
    {
        "id": "epass",
        "title": "ePass - Thu phí tự động VDTC",
        "category": "toll",
        "service_name": "ePass VDTC - Cổng Doanh Nghiệp",
        "portal_url": "https://epass-vdtc.com.vn/khach-hang-doanh-nghiep",
        "note": "Tra cứu tài khoản giao thông Viettel ePass, lịch sử giao dịch BOT, tải bảng kê hóa đơn.",
    },
    {
        "id": "petrolimex",
        "title": "Petrolimex - Hóa đơn xăng dầu",
        "category": "fuel",
        "service_name": "Petrolimex - Tra cứu Hóa đơn điện tử",
        "portal_url": "https://hoadon.petrolimex.com.vn",
        "note": "Tra cứu và tải hóa đơn GTGT điện tử xăng dầu Petrolimex.",
    },
    {
        "id": "pvoil",
        "title": "PVOIL - Hóa đơn / B2B",
        "category": "fuel",
        "service_name": "PVOIL B2B - Cổng Khách Hàng",
        "portal_url": "https://pvoil.com.vn",
        "note": "Quản lý hợp đồng cung cấp xăng dầu và hóa đơn điện tử PVOIL.",
    },
    {
        "id": "registry",
        "title": "Cục Đăng Kiểm Việt Nam",
        "category": "registry",
        "service_name": "Cục Đăng Kiểm - Tra cứu Thông tin xe",
        "portal_url": "http://vr.org.vn/ptpublic_web/ThongTinPTPublic.aspx",
        "note": "Tra cứu hồ sơ kiểm định xe cơ giới, hạn đăng kiểm và nộp phí đường bộ.",
    },
    {
        "id": "gps",
        "title": "Giám sát hành trình GPS",
        "category": "gps",
        "service_name": "Hệ thống Giám sát hành trình xe",
        "portal_url": "https://dinhvi.vn",
        "note": "Theo dõi vị trí xe trực tuyến, tốc độ, lộ trình và truyền dữ liệu Cục Đường Bộ.",
    },
    {
        "id": "bkav",
        "title": "BKAV eHoadon - Hóa đơn điện tử",
        "category": "invoice",
        "service_name": "BKAV eHoadon - Cổng Tra Cứu & Quản Lý Hóa Đơn",
        "portal_url": "https://van.ehoadon.vn",
        "note": "Tra cứu, ký số và quản lý phát hành hóa đơn điện tử BKAV. Cấu hình Partner GUID & Token tại menu Quản lý API Key.",
    },
]




def _service_account_conditions(category=None, search=None, status=None, company_id=None):
    conditions = []
    params: List[Any] = []
    if category and category in CATEGORIES:
        conditions.append("category = %s")
        params.append(category)
    if status and status in ("active", "locked", "expired"):
        conditions.append("status = %s")
        params.append(status)
    if company_id is not None:
        conditions.append("(company_id = %s OR company_id IS NULL)")
        params.append(company_id)
    if search:
        search_pattern = f"%{search.strip()}%"
        conditions.append(
            "(service_name ILIKE %s OR account_username ILIKE %s OR plate_number ILIKE %s OR customer_code ILIKE %s OR note ILIKE %s)"
        )
        params.extend([search_pattern] * 5)
    return (" WHERE " + " AND ".join(conditions) if conditions else ""), params


def count_service_accounts(category=None, search=None, status=None, company_id=None) -> int:
    where_clause, params = _service_account_conditions(category, search, status, company_id)
    row = db._execute_query(
        f"SELECT COUNT(*) AS total FROM public.transport_service_accounts{where_clause}",
        tuple(params) if params else None, fetch=True, commit=False,
    ) or {}
    return int(row.get("total") or 0)


def service_account_stats(company_id=None) -> Dict[str, Any]:
    where_clause, params = _service_account_conditions(company_id=company_id)
    row = db._execute_query(
        f"""SELECT COUNT(*) AS total_count,
                   COUNT(*) FILTER (WHERE status = 'active') AS active_count,
                   COUNT(*) FILTER (WHERE category = 'toll') AS toll_count,
                   COUNT(*) FILTER (WHERE category = 'fuel') AS fuel_count,
                   COALESCE(SUM(current_balance) FILTER (WHERE category = 'toll'), 0) AS total_balance
            FROM public.transport_service_accounts{where_clause}""",
        tuple(params) if params else None, fetch=True, commit=False,
    ) or {}
    return row


def list_service_accounts(
    category: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    company_id: Optional[int] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Retrieve filtered service accounts with category details."""
    where_clause, params = _service_account_conditions(category, search, status, company_id)
    query = f"""
        SELECT id, service_name, category, portal_url, account_username, account_password,
               customer_code, plate_number, phone_otp, manager_name, current_balance,
               last_balance_update, status, note, created_at, updated_at
        FROM public.transport_service_accounts
        {where_clause}
        ORDER BY status = 'active' DESC, id DESC
    """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset is not None:
        query += " OFFSET %s"
        params.append(offset)

    rows = db._execute_query(query, tuple(params) if params else None, fetchall=True, commit=False) or []
    for r in rows:
        cat_info = CATEGORIES.get(r.get("category", "other"), CATEGORIES["other"])
        r["category_name"] = cat_info["name"]
        r["category_badge"] = cat_info["badge_class"]
        r["category_icon"] = cat_info["icon"]
        r["category_color"] = cat_info["color"]
        # Format domain for display
        url = r.get("portal_url", "")
        if "://" in url:
            domain = url.split("://", 1)[1].split("/", 1)[0]
        else:
            domain = url.split("/", 1)[0]
        r["domain"] = domain
    return rows


def get_service_account(account_id: int) -> Optional[Dict[str, Any]]:
    """Get single service account by id."""
    row = db._execute_query(
        """
        SELECT id, service_name, category, portal_url, account_username, account_password,
               customer_code, plate_number, phone_otp, manager_name, current_balance,
               last_balance_update, status, note, created_at, updated_at
        FROM public.transport_service_accounts
        WHERE id = %s
    """,
        (account_id,),
        fetch=True,
        commit=False,
    )
    if row:
        cat_info = CATEGORIES.get(row.get("category", "other"), CATEGORIES["other"])
        row["category_name"] = cat_info["name"]
        row["category_badge"] = cat_info["badge_class"]
        row["category_icon"] = cat_info["icon"]
    return row


def save_service_account(data: Dict[str, Any], account_id: Optional[int] = None) -> int:
    """Create or update a service account."""

    portal_url = str(data.get("portal_url", "")).strip()
    if not portal_url:
        raise ValueError("Đường dẫn (URL) không được để trống.")
    if not (portal_url.startswith("http://") or portal_url.startswith("https://")):
        portal_url = "https://" + portal_url

    account_username = str(data.get("account_username", "")).strip()
    if not account_username:
        raise ValueError("Tài khoản / Tên đăng nhập không được để trống.")

    account_password = str(data.get("account_password", "")).strip()
    if not account_password:
        raise ValueError("Mật khẩu không được để trống.")

    service_name = str(data.get("service_name", "")).strip()
    if not service_name:
        domain = portal_url.split("://", 1)[1].split("/", 1)[0]
        service_name = domain

    category = str(data.get("category", "toll")).strip()
    if category not in CATEGORIES:
        category = "other"

    customer_code = str(data.get("customer_code", "")).strip()
    plate_number = str(data.get("plate_number", "")).strip()
    phone_otp = str(data.get("phone_otp", "")).strip()
    manager_name = str(data.get("manager_name", "")).strip()
    status = str(data.get("status", "active")).strip()
    if status not in ("active", "locked", "expired"):
        status = "active"

    note = str(data.get("note", "")).strip()

    raw_balance = str(data.get("current_balance", "0")).replace(".", "").replace(",", ".").strip() or "0"
    try:
        current_balance = Decimal(raw_balance)
        if current_balance < 0:
            current_balance = Decimal("0")
    except (InvalidOperation, ValueError):
        current_balance = Decimal("0")

    if account_id:
        db._execute_query(
            """
            UPDATE public.transport_service_accounts
            SET service_name = %s,
                category = %s,
                portal_url = %s,
                account_username = %s,
                account_password = %s,
                customer_code = %s,
                plate_number = %s,
                phone_otp = %s,
                manager_name = %s,
                current_balance = %s,
                last_balance_update = now(),
                status = %s,
                note = %s,
                updated_at = now()
            WHERE id = %s
        """,
            (
                service_name,
                category,
                portal_url,
                account_username,
                account_password,
                customer_code,
                plate_number,
                phone_otp,
                manager_name,
                current_balance,
                status,
                note,
                account_id,
            ),
        )
        return account_id
    else:
        company_id = data.get("company_id")
        new_id = db._execute_query(
            """
            INSERT INTO public.transport_service_accounts (
                service_name, category, portal_url, account_username, account_password,
                customer_code, plate_number, phone_otp, manager_name, current_balance,
                status, note, company_id
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            ) RETURNING id
        """,
            (
                service_name,
                category,
                portal_url,
                account_username,
                account_password,
                customer_code,
                plate_number,
                phone_otp,
                manager_name,
                current_balance,
                status,
                note,
                company_id,
            ),
            return_id=True,
        )
        return int(new_id)


def update_account_balance(account_id: int, raw_balance: Any) -> bool:
    """Quick update for account balance."""
    clean = str(raw_balance or "0").replace(".", "").replace(",", ".").strip() or "0"
    try:
        balance = Decimal(clean)
        if balance < 0:
            balance = Decimal("0")
    except (InvalidOperation, ValueError):
        return False

    return bool(
        db._execute_query(
            """
        UPDATE public.transport_service_accounts
        SET current_balance = %s, last_balance_update = now(), updated_at = now()
        WHERE id = %s
    """,
            (balance, account_id),
        )
    )


def delete_service_account(account_id: int) -> bool:
    """Delete a service account."""
    return bool(
        db._execute_query(
            "DELETE FROM public.transport_service_accounts WHERE id = %s",
            (account_id,),
        )
    )


def get_available_plates() -> List[str]:
    """Get list of active vehicle plates to assign to accounts."""
    rows = (
        db._execute_query(
            "SELECT plate_number FROM public.plate_whitelist WHERE is_active = TRUE ORDER BY plate_number ASC",
            fetchall=True,
            commit=False,
        )
        or []
    )
    plates = [r["plate_number"] for r in rows if r.get("plate_number")]
    return plates
