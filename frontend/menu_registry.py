
MENU_GROUPS = {
    "💼 Phương Nam": {
        "debts": "📑 Quản lý công nợ",
        "sales": "🧾 Lịch sử bán hàng",
        "inventory": "📦 Sổ tồn kho",
        "suppliers": "📦 Nhà cung cấp",
    },
    "🏢 Doanh nghiệp": {
        "enterprise": "🏢 Thông tin doanh nghiệp",
        "departments": "🏛️ Cơ cấu phòng ban",
        "staff": "👥 Quản lý nhân sự",
        "attendance": "📅 Chấm công & Phép",
        "advances": "💵 Tạm ứng & Hoàn ứng",
        "payroll": "💰 Lương nhân sự",
    },
    "🚚 Vận chuyển": {
        "vehicles": "🚚 Quản lý vận chuyển",
        "tax_checklist": "📑 Checklist Kế toán & Thuế",
    },
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
    "📢 Quảng cáo": {
        "advertising": "📢 Chiến dịch Google Ads",
        "advertising_keywords": "🔑 Kế hoạch & Từ khóa",
        "facebook_page": "📘 Facebook Page",
        "listings": "📋 Tin rao vặt",
        "project_news": "📰 Tin dự án",
        "prospects": "🎯 Khách tiềm năng",
        "partners": "🤝 Đối tác giới thiệu",
    },
    "🛒 Bán hàng": {
        "account_products": "🛍 Sản phẩm dịch vụ",
        "account_orders": "🧾 Đơn hàng",
        "inquiries": "📬 Tin tư vấn",
        "account_live_chat": "💬 Live Chat",
    },
    "⚙️ Hệ thống": {
        "companies": "🏢 Danh sách đơn vị",
        "users": "👥 Người dùng",
        "rbac": "🔐 Phân quyền",
        "administrative_divisions": "🗺️ Tỉnh, xã",
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
