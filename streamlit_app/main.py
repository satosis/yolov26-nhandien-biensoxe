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
from streamlit_app.views.login_view import check_auth

def main():
    st.set_page_config(page_title="Parking Camera MVC", page_icon="🅿", layout="wide")
    
    # Force login for all pages
    check_auth()
    
    user_data = st.session_state.get('user', {})
    user_role = user_data.get('role', 'Guest')
    st.title(f"🅿 Parking Camera - Hello {user_data.get('username', 'User')} ({user_role})")

    # Initialize Models & Controllers
    yolo = load_yolo_model()
    ocr = load_ocr()
    face = load_face_app()
    db = get_db()
    registry = get_asset_registry()

    # Sidebar Navigation & Settings
    from streamlit_app.views.login_view import cookies, COOKIE_PREFIX
    with st.sidebar:
        if st.button("🚪 Logout"):
            st.session_state['authenticated'] = False
            # Clear cookie on explicit logout to prevent auto-login on reload
            if f"{COOKIE_PREFIX}username" in cookies:
                del cookies[f"{COOKIE_PREFIX}username"]
                cookies.save()
            st.rerun()
            
        st.divider()
        st.header("📌 Menu Chính")

        # Determine available menus based on role
        all_menus = {
            "detection": "🔍 Nhận diện",
            "cameras": "📹 Quản lý Camera",
            "dori": "📐 DORI",
            "asset": "🗂 Asset",
            "sla": "📊 SLA",
            "health": "❤️ Health",
            "config": "⚙️ Config",
            "telegram": "🤖 Quản lý Telegram"
        }
        if user_role == 'Admin':
            all_menus["users"] = "👥 Phân quyền"

        # Apply RBAC from database
        roles = db.get_roles()
        role_info = next((r for r in roles if r['role_name'] == user_role), None)
        allowed_menus_str = role_info.get('allowed_menus', 'detection') if role_info else 'detection'
        
        menus = {}
        if allowed_menus_str == '*':
            menus = all_menus.copy()
        else:
            allowed_list = allowed_menus_str.split(',')
            for k, v in all_menus.items():
                if k in allowed_list:
                    menus[k] = v
                    
        # Fallback if no menus allowed
        if not menus:
            menus = {"detection": "🔍 Nhận diện"}

        # Read current menu from URL query params
        query_params = st.query_params
        current_menu_key = query_params.get("menu", list(menus.keys())[0])
        
        # Validate URL param against allowed menus (prevents URL spoofing)
        if current_menu_key not in menus:
            current_menu_key = list(menus.keys())[0]

        menu_keys = list(menus.keys())
        menu_labels = list(menus.values())
        default_index = menu_keys.index(current_menu_key)

        def update_menu():
            st.query_params["menu"] = st.session_state.main_menu_nav

        selected_key = st.radio("Chuyển trang", menu_keys, index=default_index, format_func=lambda k: menus[k], key="main_menu_nav", on_change=update_menu)

        # Conditionally show detection config ONLY on detection page
        if selected_key == "detection":
            st.divider()
            st.header("⚙️ Cấu hình Nhận diện")
            mode = st.radio("Chế độ", ["🔢 Biển số xe", "👤 Khuôn mặt"], index=0)
            
            st.divider()
            st.header("🔬 Tham số AI")
            conf_thresh = st.slider("Confidence threshold", 0.1, 0.95, 0.25)
            iou_thresh = st.slider("IoU threshold", 0.1, 0.9, 0.45)
            show_enhanced = st.checkbox("Hiển thị ảnh enhanced", value=True)
            known_dir = st.text_input("Thư mục khuôn mặt", value="./config/faces")
        elif selected_key == "telegram":
            st.divider()
            st.info("💡 Chế độ Quản lý đa Bot Telegram.\nBạn có thể thêm, sửa, xóa và gửi tin nhắn kiểm tra trực tiếp từ giao diện bên phải.")

    # Main Area Rendering
    if selected_key == "detection":
        config = {
            'mode': mode,
            'conf_thresh': conf_thresh,
            'iou_thresh': iou_thresh,
            'show_enhanced': show_enhanced,
            'known_dir': known_dir
        }
        det_controller = DetectionController(yolo, ocr, face)
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
    elif selected_key == "users" and user_role == "Admin":
        user_management_view()
    elif selected_key == "telegram":
        multi_telegram_view()

if __name__ == "__main__":
    main()
