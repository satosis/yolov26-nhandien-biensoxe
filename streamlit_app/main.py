import streamlit as st
import os
import sys
import html as html_mod

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit_app.models.detector_model import load_yolo_model, load_ocr, load_face_app
from streamlit_app.controllers.detection_controller import DetectionController
from streamlit_app.controllers.dori_controller import DoriController
from streamlit_app.controllers.rbac_controller import rbac_controller
from streamlit_app.views.detection_view import detection_view
from streamlit_app.views.dori_view import dori_view
from streamlit_app.views.multi_telegram_view import multi_telegram_view
from streamlit_app.views.camera_management_view import camera_management_view
from streamlit_app.views.sys_config_view import sys_config_view
from streamlit_app.views.user_management_view import user_management_view
from streamlit_app.views.rbac_management_view import rbac_management_view
from streamlit_app.views.parking_dashboard_view import parking_dashboard_view
from streamlit_app.views.parking_logs_view import parking_logs_view
from streamlit_app.views.parking_analytics_view import parking_analytics_view
from streamlit_app.views.vehicle_management_view import vehicle_management_view
from streamlit_app.views.hpc_engine_view import hpc_engine_view, auto_start_hpc
from streamlit_app.views.login_view import check_auth, logout_user, flush_pending_localstorage
from streamlit_app.views.provider_management_view import provider_management_view
from streamlit_app.views.door_provider_view import door_provider_view
from streamlit_app.views.stranger_management_view import stranger_management_view
from streamlit_app.views.pending_plates_view import pending_plates_view
from streamlit_app.views.ocr_review_view import ocr_review_view
from streamlit_app.views.sites_view import sites_view
from streamlit_app.menu_registry import MENU_GROUPS


def main():
    st.set_page_config(page_title="Parking Camera MVC", page_icon="🅿", layout="wide")

    # Load global CSS at the top level
    from streamlit_app.util.ui_utils import load_css
    load_css()

    # Mặc định khởi động AI Engine (HPC) ngầm
    auto_start_hpc()
    
    # Force login for all pages
    check_auth()
    
    user_data = st.session_state.get('user', {})
    user_role = user_data.get('role', 'Guest')

    # 1. Determine Allowed Menus (RBAC) - MUST HAPPEN BEFORE RENDERING
    menu_groups = MENU_GROUPS

    roles = rbac_controller.get_roles()
    role_info = next((r for r in roles if r['role_name'] == user_role), None)
    allowed_menus_str = role_info.get('allowed_menus', '') if role_info else ''
    allowed_list = allowed_menus_str.split(',') if allowed_menus_str and allowed_menus_str != '*' else ('*' if allowed_menus_str == '*' else [])

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

    # If RBAC returned nothing, use first available menu as minimum access
    if not all_flattened_menus:
        first_group = next(iter(menu_groups))
        first_key = next(iter(menu_groups[first_group]))
        all_flattened_menus = {first_key: menu_groups[first_group][first_key]}
        filtered_groups[first_group] = all_flattened_menus

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

    @st.cache_resource
    def get_det_controller(_yolo, _ocr, _face):
        from streamlit_app.controllers.detection_controller import DetectionController
        return DetectionController(_yolo, _ocr, _face)

    det_controller = get_det_controller(yolo, ocr, face)

    # Premium UI CSS Injection
    

    st.markdown(f"""
<style>
/* Remove Streamlit default top padding so custom header sits flush */
.block-container {{ padding-top: 0 !important; margin-top: 0 !important; }}
header[data-testid="stHeader"] {{ display: none !important; }}
</style>
<div style="display:flex;align-items:center;justify-content:space-between;padding:0 16px;height:50px;background:rgba(15,23,42,0.6);border-bottom:1px solid rgba(255,255,255,0.08);border-radius:0 0 12px 12px;margin-bottom:16px;">
    <span style="font-size:1.1rem;font-weight:700;color:#f8fafc;">🅿 Parking Camera</span>
    <span style="font-size:0.85rem;color:#94a3b8;">👤 <b style="color:#6366f1;">{html_mod.escape(user_data.get('username','User'))}</b> &nbsp;·&nbsp; <span style="background:rgba(99,102,241,0.15);color:#6366f1;padding:2px 8px;border-radius:8px;font-size:0.8rem;">{html_mod.escape(user_role)}</span></span>
</div>
""", unsafe_allow_html=True)

    # Sidebar Rendering
    with st.sidebar:
        # Global site selector — always visible
        from streamlit_app.controllers.site_controller import site_controller
        _sites = site_controller.get_sites(active_only=True)
        _site_opts = {0: "🌐 Tất cả địa điểm"}
        _site_opts.update({s['id']: f"📍 {s['site_name']}" for s in _sites})
        _cur_site = st.session_state.get('active_site_id') or 0
        _sel_site = st.selectbox(
            "Địa điểm", options=list(_site_opts.keys()),
            format_func=lambda x: _site_opts[x],
            index=list(_site_opts.keys()).index(_cur_site) if _cur_site in _site_opts else 0,
            key="global_site_selector", label_visibility="collapsed"
        )
        new_site_val = _sel_site if _sel_site else None
        if new_site_val != st.session_state.get('active_site_id'):
            st.session_state['active_site_id'] = new_site_val
            st.rerun()

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
            logout_user()

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
    elif selected_key == "sites":
        sites_view()
    elif selected_key == "providers":
        provider_management_view()
    elif selected_key == "door_providers":
        door_provider_view()
    elif selected_key == "dori":
        dori_controller = DoriController()
        dori_view(dori_controller)
    elif selected_key == "config":
        sys_config_view()
    elif selected_key == "users":
        user_management_view()
    elif selected_key == "rbac":
        rbac_management_view()
    elif selected_key == "vehicles":
        vehicle_management_view()
    elif selected_key == "strangers":
        stranger_management_view()
    elif selected_key == "pending_plates":
        pending_plates_view()
    elif selected_key == "ocr_review":
        ocr_review_view()
    elif selected_key == "telegram":
        multi_telegram_view()
    elif selected_key == "parking_dashboard":
        parking_dashboard_view()
    elif selected_key == "parking_logs":
        parking_logs_view()
    elif selected_key == "parking_analytics":
        parking_analytics_view()
    elif selected_key == "hpc":
        hpc_engine_view()

    # Flush any pending localStorage ops (save/clear remember token) after page render
    flush_pending_localstorage()

if __name__ == "__main__":
    main()
