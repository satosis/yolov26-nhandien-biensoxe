
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
        "vehicles": "🚗 Phương tiện",
        "strangers": "👤 Người lạ",
        "pending_plates": "🚘 Biển số chờ duyệt",
    },
    "⚙️ Hệ thống": {
        "inquiries": "📬 Tin tư vấn",
        "cms": "🌐 CMS Trang chủ",
        "users": "👥 Người dùng",
        "rbac": "🔐 Phân quyền",
        "telegram": "📱 Telegram",
    },
}

ALL_MENUS = {}
for items in MENU_GROUPS.values():
    ALL_MENUS.update(items)
