import streamlit as st
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit_app.models.detector_model import load_yolo_model, load_ocr, load_face_app, get_db, get_asset_registry
from streamlit_app.controllers.detection_controller import DetectionController
from streamlit_app.controllers.dori_controller import DoriController
from streamlit_app.views.detection_view import detection_view
from streamlit_app.views.dori_view import dori_view
from streamlit_app.views.asset_view import asset_view
from streamlit_app.views.sla_view import sla_view
from streamlit_app.views.health_view import health_view
from streamlit_app.views.multi_telegram_view import multi_telegram_view
from streamlit_app.views.camera_management_view import camera_management_view
from streamlit_app.views.sys_config_view import sys_config_view
from streamlit_app.views.user_management_view import user_management_view
from streamlit_app.views.rbac_management_view import rbac_management_view
from streamlit_app.views.zones_view import zones_view
from streamlit_app.views.parking_dashboard_view import parking_dashboard_view
from streamlit_app.views.parking_logs_view import parking_logs_view
from streamlit_app.views.parking_analytics_view import parking_analytics_view
from streamlit_app.views.vehicle_management_view import vehicle_management_view
from streamlit_app.views.ai_auditor_view import ai_auditor_view
from streamlit_app.views.hpc_engine_view import hpc_engine_view, auto_start_hpc
from streamlit_app.views.login_view import check_auth


def main():
    st.set_page_config(page_title="Parking Camera MVC", page_icon="🅿", layout="wide")
    
    # Mặc định khởi động AI Engine (HPC) ngầm
    auto_start_hpc()
    
    # Force login for all pages
    check_auth()
    
    user_data = st.session_state.get('user', {})
    user_role = user_data.get('role', 'Guest')

    # Initialize Base Dependencies (DB needed for RBAC)
    db = get_db()
    if not db:
        st.error("Database initialization failed")
        return

    # 1. Determine Allowed Menus (RBAC) - MUST HAPPEN BEFORE RENDERING
    menu_groups = {
        "📊 Theo dõi & AI": {
            "detection": "🔍 Nhận diện",
            "dori": "📐 DORI",
            "sla": "📊 SLA",
            "health": "❤️ Health",
            "auditor": "🔎 AI Kiểm định",
            "hpc": "⚡ AI Engine"
        },
        "🅿️ Bãi xe": {
            "parking_dashboard": "🅿️ Dashboard Bãi xe",
            "parking_logs": "📋 Nhật ký Vào-Ra",
            "parking_analytics": "📊 Phân tích Bãi xe"
        },
        "🛠 Quản lý": {
            "cameras": "📹 Quản lý Camera",
            "zones": "🗺 Quản lý Khu vực",
            "vehicles": "🚗 Quản lý Phương tiện",
            "asset": "🗂 Asset",
            "telegram": "🤖 Quản lý Telegram"
        },
        "⚙️ Hệ thống": {
            "config": "⚙️ Cấu hình",
            "users": "👥 Quản lý Người dùng",
            "rbac": "🛡️ Quản lý Phân quyền"
        }
    }

    roles = db.get_roles()
    role_info = next((r for r in roles if r['role_name'] == user_role), None)
    allowed_menus_str = role_info.get('allowed_menus', 'detection') if role_info else 'detection'
    allowed_list = allowed_menus_str.split(',') if allowed_menus_str != '*' else '*'

    filtered_groups = {}
    all_flattened_menus = {}
    for group_name, items in menu_groups.items():
        current_group_items = {}
        for k, v in items.items():
            if allowed_list == '*' or k in allowed_list:
                current_group_items[k] = v
                all_flattened_menus[k] = v
        if current_group_items:
            filtered_groups[group_name] = current_group_items

    if not all_flattened_menus:
        all_flattened_menus = {"detection": "🔍 Nhận diện"}
        filtered_groups["📊 Theo dõi & AI"] = {"detection": "🔍 Nhận diện"}

    # 2. Early Redirection for Invalid/Unauthorized Menus
    query_params = st.query_params
    current_menu_key = query_params.get("menu")
    default_menu = list(all_flattened_menus.keys())[0]

    # Handle redirection if missing or invalid
    if not current_menu_key or current_menu_key not in all_flattened_menus:
        st.query_params["menu"] = default_menu
        st.rerun()

    # Now we are on a valid menu.
    selected_key = current_menu_key

    # Initialize AI Models
    yolo = load_yolo_model()
    ocr = load_ocr()
    face = load_face_app()
    registry = get_asset_registry()

    @st.cache_resource
    def get_det_controller(_yolo, _ocr, _face):
        from streamlit_app.controllers.detection_controller import DetectionController
        return DetectionController(_yolo, _ocr, _face)

    det_controller = get_det_controller(yolo, ocr, face)
    from streamlit_app.views.login_view import cookies, COOKIE_PREFIX

    # Premium UI CSS Injection
    st.markdown("""
        <style>
            [data-testid="stSidebar"] {
                background: rgba(15, 23, 42, 0.7) !important;
                backdrop-filter: blur(20px) !important;
                border-right: 1px solid rgba(255, 255, 255, 0.1) !important;
            }
            [data-testid="stSidebarNav"] { background-color: transparent !important; }
            .stButton>button { border-radius: 10px !important; transition: all 0.3s ease !important; }
            .stButton>button:hover { border-color: #6366f1 !important; color: #6366f1 !important; box-shadow: 0 0 15px rgba(99, 102, 241, 0.2) !important; }
            .logout-container { position: fixed; bottom: 20px; left: 20px; width: calc(100% - 40px); z-index: 1000; }
            [data-testid="stExpander"] { border: none !important; background: rgba(255, 255, 255, 0.03) !important; border-radius: 12px !important; margin-bottom: 10px !important; }
        </style>
    """, unsafe_allow_html=True)

    st.title(f"🅿 Parking Camera - Hello {user_data.get('username', 'User')} ({user_role})")

    # Sidebar Rendering
    with st.sidebar:
        st.markdown("<h3 style='margin-bottom:15px; color:#f8fafc;'>📍 Điều hướng</h3>", unsafe_allow_html=True)
        
        for group_name, items in filtered_groups.items():
            is_group_active = selected_key in items
            with st.expander(group_name, expanded=is_group_active):
                for k, v in items.items():
                    btn_type = "primary" if selected_key == k else "secondary"
                    if st.button(v, key=f"nav_{k}_{group_name}", width='stretch', type=btn_type):
                        st.query_params["menu"] = k
                        st.rerun()

        # Context-aware configuration configs in sidebar
        if selected_key == "detection":
            st.divider()
            st.caption("⚙️ AI CONFIG")
            mode = st.radio("Chế độ", ["🔢 Biển số xe", "👤 Khuôn mặt"], index=1 if "mode" in dir() and mode == "👤 Khuôn mặt" else 1, label_visibility="collapsed")
            conf_thresh = st.slider("Conf", 0.1, 0.95, 0.25)
            iou_thresh = st.slider("IoU", 0.1, 0.9, 0.45)
            show_enhanced = st.checkbox("Enhanced", value=True)
            aggressive_ocr = st.checkbox("Aggressive OCR", value=False)
            default_face_dir = os.path.abspath("./config/faces")
            known_dir = st.text_input("Faces Dir", value=default_face_dir)
        elif selected_key == "telegram":
            st.divider()
            st.info("🤖 Quản lý Telegram Bot")


        # LOGOUT AT BOTTOM
        st.markdown('<div class="logout-spacer" style="margin-top: 50px;"></div>', unsafe_allow_html=True)
        if st.button("🚪 Logout", width='stretch', type="secondary"):
            st.session_state['authenticated'] = False
            if f"{COOKIE_PREFIX}username" in cookies:
                del cookies[f"{COOKIE_PREFIX}username"]
                cookies.save()
            st.rerun()

    # Main Area Rendering
    if selected_key == "detection":
        config = {
            'mode': mode,
            'conf_thresh': conf_thresh,
            'iou_thresh': iou_thresh,
            'show_enhanced': show_enhanced,
            'aggressive_ocr': aggressive_ocr,
            'known_dir': known_dir
        }
        detection_view(det_controller, config)
    elif selected_key == "cameras":
        camera_management_view()
    elif selected_key == "dori":
        dori_controller = DoriController()
        dori_view(dori_controller)
    elif selected_key == "asset":
        asset_view(registry)
    elif selected_key == "sla":
        sla_view(db)
    elif selected_key == "health":
        health_view(db)
    elif selected_key == "config":
        sys_config_view()
    elif selected_key == "users" and (user_role == "Admin" or "users" in allowed_list or allowed_list == '*'):
        user_management_view()
    elif selected_key == "rbac" and (user_role == "Admin" or "rbac" in allowed_list or allowed_list == '*'):
        rbac_management_view()
    elif selected_key == "zones":
        zones_view()
    elif selected_key == "vehicles":
        vehicle_management_view()
    elif selected_key == "telegram":
        multi_telegram_view()
    elif selected_key == "parking_dashboard":
        parking_dashboard_view()
    elif selected_key == "parking_logs":
        parking_logs_view()
    elif selected_key == "parking_analytics":
        parking_analytics_view()
    elif selected_key == "auditor":
        ai_auditor_view()
    elif selected_key == "hpc":
        hpc_engine_view()

if __name__ == "__main__":
    main()
