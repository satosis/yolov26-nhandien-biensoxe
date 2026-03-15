import streamlit as st
import os

def sys_config_view():
    st.subheader("⚙️ Cấu hình hệ thống")
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
                st.success("✅ Đã lưu cấu hình!")
                st.rerun()
            else:
                st.error("❌ Lỗi khi lưu.")
