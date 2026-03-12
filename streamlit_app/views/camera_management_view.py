import streamlit as st
from streamlit_app.models.detector_model import get_db

def camera_management_view():
    st.title("📹 Quản lý Camera")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    tab_list, tab_zones, tab_settings = st.tabs(["📋 Danh sách Camera", "🗺 Khu vực", "⚙️ Cài đặt Imou API"])

    # ========== TAB 1: CAMERA LIST ==========
    with tab_list:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown("### Danh sách Camera hiện có")
        with col2:
            if st.button("➕ Thêm Camera", type="primary", use_container_width=True):
                st.session_state['show_add_camera_form'] = True
                
        # Add Camera Form
        if st.session_state.get('show_add_camera_form', False):
            zones = db.get_zones()
            zone_options = {z['id']: z['zone_name'] for z in zones}
            with st.container(border=True):
                st.subheader("Thêm Camera Mới")
                with st.form("form_add_camera"):
                    c1, c2, c3 = st.columns(3)
                    cam_name = c1.text_input("Tên Camera (*)", placeholder="Cam Cổng Chính")
                    cam_type = c2.selectbox("Loại Camera", ["imou", "rtsp", "onvif"])
                    zone_names = ["-- Không chọn --"] + [z['zone_name'] for z in zones]
                    selected_zone = c3.selectbox("Khu vực", zone_names)
                    
                    imou_dev_id = ""
                    imou_chan_id = "0"
                    if cam_type == "imou":
                        c4, c5 = st.columns(2)
                        imou_dev_id = c4.text_input("Imou Device ID (*)", placeholder="L2FXXXXXX")
                        imou_chan_id = c5.text_input("Imou Channel ID", value="0")
                        
                    rtsp_url = st.text_input("RTSP URL", placeholder="rtsp://admin:pass@ip:554/cam/realmonitor?channel=1&subtype=1")
                    is_active = st.checkbox("Kích hoạt ngay", value=True)
                    
                    btn_col1, btn_col2 = st.columns([1, 1])
                    if btn_col1.form_submit_button("✅ Lưu", type="primary"):
                        if not cam_name or (cam_type == 'imou' and not imou_dev_id):
                            st.warning("Vui lòng nhập đầy đủ Tên và Device ID (nếu là Imou).")
                        else:
                            zone_id = None
                            if selected_zone != "-- Không chọn --":
                                zone_id = next((z['id'] for z in zones if z['zone_name'] == selected_zone), None)
                            if db.add_camera(cam_name, cam_type, imou_dev_id, rtsp_url, is_active, imou_chan_id, zone_id):
                                st.success("Thêm camera thành công!")
                                st.session_state['show_add_camera_form'] = False
                                st.rerun()
                            else:
                                st.error("Có lỗi xảy ra khi lưu vào DB.")
                    if btn_col2.form_submit_button("❌ Hủy"):
                        st.session_state['show_add_camera_form'] = False
                        st.rerun()

        st.divider()
        
        # List Cameras grouped by zone
        cameras = db.get_all_cameras()
        if not cameras:
            st.info("Hệ thống chưa có camera nào được cấu hình.")
        else:
            # Group by zone
            grouped = {}
            for cam in cameras:
                zone = cam.get('zone_name') or '📌 Chưa phân khu vực'
                grouped.setdefault(zone, []).append(cam)
            
            for zone_name, cams in grouped.items():
                st.markdown(f"#### 🗺 {zone_name} ({len(cams)} camera)")
                for cam in cams:
                    with st.container(border=True):
                        c_info, c_action = st.columns([4, 1])
                        with c_info:
                            status_icon = "🟢" if cam['is_active'] else "⚫"
                            st.markdown(f"{status_icon} **{cam['camera_name']}** (`{cam['camera_type'].upper()}`)", unsafe_allow_html=True)
                            details = []
                            if cam['camera_type'] == 'imou':
                                details.append(f"Device: {cam.get('imou_device_id', 'N/A')}")
                            if cam.get('rtsp_url'):
                                details.append(f"RTSP: `{cam['rtsp_url'][:60]}...`" if len(cam.get('rtsp_url','')) > 60 else f"RTSP: `{cam.get('rtsp_url')}`")
                            if details:
                                st.caption(" | ".join(details))
                        
                        with c_action:
                            toggle_txt = "⏸ Dừng" if cam['is_active'] else "▶ Bật"
                            def toggle_fn(camera=cam):
                                db.update_camera(camera['id'], camera['camera_name'], camera['camera_type'], 
                                    camera.get('imou_device_id'), camera.get('rtsp_url'), 
                                    not camera['is_active'], camera.get('imou_channel_id', '0'), camera.get('zone_id'))
                            def delete_fn(camera=cam):
                                db.delete_camera(camera['id'])

                            st.button(toggle_txt, key=f"tg_{cam['id']}", use_container_width=True, on_click=toggle_fn)
                            st.button("🗑", key=f"del_{cam['id']}", use_container_width=True, type="secondary", on_click=delete_fn)
                st.divider()

    # ========== TAB 2: ZONES ==========
    with tab_zones:
        st.markdown("### 🗺 Quản lý Khu vực Camera")
        zones = db.get_zones()
        
        with st.form("form_add_zone"):
            zc1, zc2, zc3 = st.columns([2, 3, 1])
            new_zone = zc1.text_input("Tên khu vực mới", placeholder="Tầng 2")
            new_desc = zc2.text_input("Mô tả (tùy chọn)", placeholder="Khu vực tầng 2 văn phòng")
            zc3.write("")  # spacer
            if zc3.form_submit_button("➕ Thêm", type="primary"):
                if new_zone:
                    if db.add_zone(new_zone, new_desc):
                        st.success(f"Đã thêm khu vực: {new_zone}")
                        st.rerun()
                    else:
                        st.error("Khu vực đã tồn tại hoặc có lỗi.")
                else:
                    st.warning("Nhập tên khu vực.")

        if zones:
            for z in zones:
                with st.container(border=True):
                    zc1, zc2 = st.columns([4, 1])
                    with zc1:
                        st.markdown(f"**{z['zone_name']}**")
                        if z.get('description'):
                            st.caption(z['description'])
                    with zc2:
                        def del_zone(zid=z['id']):
                            db.delete_zone(zid)
                        st.button("🗑 Xóa", key=f"dz_{z['id']}", on_click=del_zone, use_container_width=True, type="secondary")
        else:
            st.info("Chưa có khu vực nào.")

    # ========== TAB 3: IMOU API SETTINGS ==========
    with tab_settings:
        st.markdown("### 🔑 Cấu hình Imou Open API")
        st.info("Cấu hình này dùng chung cho tất cả camera Imou trong hệ thống.")
        
        current_keys = db.get_imou_api_keys()
        with st.form("imou_api_form"):
            app_id = st.text_input("Imou App ID", value=current_keys.get('app_id', ''))
            app_secret = st.text_input("Imou App Secret", value=current_keys.get('app_secret', ''), type="password")
            if st.form_submit_button("Lưu Cấu Hình API", type="primary"):
                if app_id and app_secret:
                    if db.update_imou_api_keys(app_id, app_secret):
                        st.success("Lưu API Keys thành công!")
                        st.rerun()
                    else:
                        st.error("Lỗi khi lưu Database.")
                else:
                    st.warning("Vui lòng điền đủ App ID và App Secret")
