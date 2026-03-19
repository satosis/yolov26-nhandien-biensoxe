# Single source of truth for all menu definitions.
# Both main.py and rbac_management_view.py import from here.

MENU_GROUPS = {
    "📊 Theo dõi & AI": {
        "detection": "🔍 Nhận diện",
        "dori": "📐 DORI",
        "ocr_review": "🔤 OCR Review",
        "hpc": "⚡ AI Engine",
    },
    "🅿️ Bãi xe": {
        "parking_dashboard": "🅿️ Dashboard Bãi xe",
        "parking_logs": "📋 Nhật ký Vào-Ra",
        "parking_analytics": "📊 Phân tích Bãi xe",
    },
    "🛠 Quản lý": {
        "sites": "🏢 Quản lý Địa điểm",
        "cameras": "📹 Quản lý Camera",
        "providers": "🏭 Nhà cung cấp Camera",
        "door_providers": "🚪 Nhà cung cấp Cửa cuốn",
        "vehicles": "🚗 Quản lý Phương tiện",
        "strangers": "👤 Người lạ",
        "pending_plates": "🚘 Xe lạ",
        "telegram": "🤖 Quản lý Telegram",
    },
    "⚙️ Hệ thống": {
        "config": "⚙️ Cấu hình",
        "users": "👥 Quản lý Người dùng",
        "rbac": "🛡️ Quản lý Phân quyền",
    },
}

# Flat dict of all menus for RBAC and lookups
ALL_MENUS = {}
for items in MENU_GROUPS.values():
    ALL_MENUS.update(items)
