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
<div class="flex items-center justify-end px-4 h-14 bg-slate-900/60 border-b border-white/10 rounded-b-xl mb-4 shadow-md">
    <span class="text-sm text-slate-400 flex items-center gap-2">
        <span class="text-cyan-400 font-bold">{html_mod.escape(user_data.get('username','User'))}</span>
        <span class="bg-cyan-500/15 text-cyan-400 px-2 py-0.5 rounded-lg text-xs font-semibold border border-cyan-500/20">{html_mod.escape(user_role)}</span>
    </span>
</div>
""", unsafe_allow_html=True)

    # Sidebar Rendering
    with st.sidebar:
        st.markdown("<h3 style='color: #f8fafc; margin-top: -20px; margin-bottom: 24px; font-weight: 700; font-size: 1.1rem; display: flex; align-items: center; gap: 8px;'>🅿 Parking Camera</h3>", unsafe_allow_html=True)
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

        # LOGOUT AT BOTTOM
        st.markdown('<div class="logout-spacer" style="margin-top: 50px;"></div>', unsafe_allow_html=True)
        if st.button("🚪 Logout", width='stretch', type="secondary"):
            logout_user()

    # Main Area Rendering
    if selected_key == "detection":
        config = {}
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
