import streamlit as st
import os

from streamlit_app.utils.toast import show_toast
from streamlit_app.controllers.system_controller import system_controller

def sys_config_view():
    st.subheader("⚙️ Cấu hình hệ thống")
    if 'just_saved_config' in st.session_state:
        show_toast(st.session_state.pop('just_saved_config'), type="success")
    try:
        from core.config import settings_mgr
        current = settings_mgr.settings
    except ImportError:
        st.error("Lỗi import settings manager.")
        return

    with st.form("sys_config_form"):
        st.markdown("**Hiệu năng & Stream:**")
        new_process_w = st.number_input("AI Process Width (px)", value=current.get("PROCESS_WIDTH", 640))
        new_fps = st.slider("Stream FPS", 1, 30, current.get("STREAM_FPS", 15))
        
        st.divider()
        st.markdown("**Tham số AI:**")
        new_conf = st.slider("Confidence", 0.1, 0.9, current.get("GENERAL_DETECT_CONF", 0.25))
        new_line_ratio = st.slider("Vạch đếm (Ratio)", 0.1, 0.9, current.get("LINE_Y_RATIO", 0.62))
        
        if st.form_submit_button("💾 Lưu cấu hình"):
            updated = {
                "PROCESS_WIDTH": new_process_w,
                "STREAM_FPS": new_fps,
                "GENERAL_DETECT_CONF": new_conf,
                "LINE_Y_RATIO": new_line_ratio,
            }
            if settings_mgr.save_settings(updated):
                st.session_state['just_saved_config'] = "Đã lưu cấu hình!"
                st.rerun()
            else:
                st.error("❌ Lỗi khi lưu.")

    st.divider()
    st.markdown("### 📡 WebRTC / TURN Server")
    st.caption("Cấu hình TURN server cho mobile camera view (qua NAT/cellular)")
    with st.form("turn_config_form"):
        turn_url = st.text_input("TURN URL", value=system_controller.get_app_setting("WEBRTC_TURN_URL") or "",
            placeholder="turn:your-server.com:3478")
        turn_user = st.text_input("TURN Username", value=system_controller.get_app_setting("WEBRTC_TURN_USERNAME") or "")
        turn_pass = st.text_input("TURN Password", type="password",
            value=system_controller.get_app_setting("WEBRTC_TURN_PASSWORD") or "")
        if st.form_submit_button("💾 Lưu TURN config", type="primary"):
            system_controller.set_many_app_settings({
                "WEBRTC_TURN_URL": turn_url,
                "WEBRTC_TURN_USERNAME": turn_user,
                "WEBRTC_TURN_PASSWORD": turn_pass,
            })
            show_toast("Đã lưu TURN config", type="success")
            st.rerun()
