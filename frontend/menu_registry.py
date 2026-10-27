
MENU_GROUPS = {
    "📊 Theo dõi & AI": {
        "detection": "🔍 Nhận diện",
        "recordings": "📹 Ghi hình",
    },
    "🅿️ Bãi xe": {
        "parking_dashboard": "🅿️ Dashboard Bãi xe",
        "parking_logs": "📋 Nhật ký Vào-Ra",
        "parking_analytics": "📊 Phân tích Bãi xe",
    },
    "🛠 Quản lý": {
        "sites": "🏢 Địa điểm",
        "cameras": "📹 Camera",
        "providers": "🏭 NCC Camera",
        "door_providers": "🚪 NCC Cửa cuốn",
        "rolling_doors": "🚪 Cửa cuốn",
        "strangers": "👤 Người lạ",
        "pending_plates": "🚘 Biển số chờ duyệt",
    },
    "🏢 Doanh nghiệp": {
        "enterprise": "🏢 Thông tin doanh nghiệp",
        "departments": "🏛️ Cơ cấu phòng ban",
        "staff": "👥 Quản lý nhân sự",
        "attendance": "📅 Chấm công & Phép",
        "advances": "💵 Tạm ứng & Hoàn ứng",
        "assets": "📦 Tài sản",
        "payroll": "💰 Lương nhân sự",
        "debts": "📑 Quản lý công nợ",
        "sales": "🧾 Lịch sử bán hàng",
        "suppliers": "📦 Nhà cung cấp",
    },
    "🚚 Vận chuyển": {
        "vehicles": "🚚 Quản lý vận chuyển",
        "tax_checklist": "📑 Checklist Kế toán & Thuế",
    },
    "⚙️ Hệ thống": {
        "companies": "🏢 Danh sách đơn vị",
        "users": "👥 Người dùng",
        "rbac": "🔐 Phân quyền",
        "administrative_divisions": "🗺️ Tỉnh, xã",
        "inquiries": "📬 Tin tư vấn",
        "cms": "🌐 CMS Trang chủ",
        "telegram": "📱 Telegram",
        "zalo_inbox": "💬 Yêu cầu Zalo",
        "service_accounts": "🌐 Tài khoản dịch vụ",
        "api_keys": "🔑 Quản lý API Key",
    },
}

ALL_MENUS = {}
for items in MENU_GROUPS.values():
    ALL_MENUS.update(items)
