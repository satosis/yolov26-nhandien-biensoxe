import streamlit as st
from streamlit_app.models.detector_model import get_db

def camera_management_view():
    st.title("📹 Quản lý Camera")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    tab_list, tab_settings = st.tabs(["📋 Danh sách Camera", "⚙️ Cài đặt Imou API"])

    # ========== TAB 1: CAMERA LIST ==========
    with tab_list:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown("### Danh sách Camera hiện có")
        with col2:
            if st.button("➕ Thêm Camera", type="primary", width='stretch'):
                st.session_state['show_add_camera_form'] = True
                
        # Add/Edit Camera Form
        if st.session_state.get('show_add_camera_form', False) or st.session_state.get('edit_camera_id'):
            editing_id = st.session_state.get('edit_camera_id')
            editing_cam = db.get_camera(editing_id) if editing_id else None
            
            zones = db.get_zones()
            zone_options = {z['id']: z['zone_name'] for z in zones}
            
            with st.container(border=True):
                st.subheader("Sửa Camera" if editing_id else "Thêm Camera Mới")
                
                # Move type selection OUTSIDE the form for dynamic layout
                default_type_idx = 0
                if editing_cam:
                    type_val = editing_cam.get('camera_type', 'imou')
                    if type_val in ["imou", "rtsp", "onvif"]:
                        default_type_idx = ["imou", "rtsp", "onvif"].index(type_val)
                
                cam_type = st.selectbox("Loại Camera", ["imou", "rtsp", "onvif"], index=default_type_idx, key="cam_type_selector")

                with st.form("form_camera_details_v3"):
                    c1, c2 = st.columns(2)
                    cam_name = c1.text_input("Tên Camera (*)", value=editing_cam['camera_name'] if editing_cam else "", placeholder="Cam Cổng Chính")
                    zone_names = ["-- Không chọn --"] + [z['zone_name'] for z in zones]
                    current_zone_name = editing_cam.get('zone_name', "-- Không chọn --") if editing_cam else "-- Không chọn --"
                    selected_zone = c2.selectbox("Khu vực", zone_names, index=zone_names.index(current_zone_name) if current_zone_name in zone_names else 0)
                    
                    # Safe defaults
                    imou_dev_id = editing_cam.get('imou_device_id', "") if editing_cam else ""
                    imou_chan_id = editing_cam.get('imou_channel_id', "0") if editing_cam else "0"
                    rtsp_url = editing_cam.get('rtsp_url', "") if editing_cam else ""
                    
                    imou_app_id = editing_cam.get('imou_app_id', "") if editing_cam else ""
                    imou_app_secret = editing_cam.get('imou_app_secret', "") if editing_cam else ""
                    imou_email = editing_cam.get('imou_email', "") if editing_cam else ""

                    if cam_type == "imou":
                        c3, c4 = st.columns(2)
                        imou_dev_id = c3.text_input("Imou Device ID (*)", value=imou_dev_id, placeholder="L2FXXXXXX")
                        imou_chan_id = c4.text_input("Imou Channel ID", value=imou_chan_id)
                        
                        st.markdown("---")
                        st.caption("🔑 Imou API (Để trống nếu dùng mặc định)")
                        ca1, ca2, ca3 = st.columns(3)
                        imou_app_id = ca1.text_input("App ID", value=imou_app_id)
                        imou_app_secret = ca2.text_input("App Secret", value=imou_app_secret, type="password")
                        imou_email = ca3.text_input("Email", value=imou_email)
                    else:
                        rtsp_url = st.text_input("RTSP URL", value=rtsp_url, placeholder="rtsp://admin:pass@ip:554/cam/realmonitor?channel=1&subtype=1")
                        imou_dev_id = ""
                        imou_chan_id = "0"

                    is_active = st.checkbox("Kích hoạt", value=editing_cam['is_active'] if editing_cam else True)
                    
                    btn_col1, btn_col2 = st.columns([1, 1])
                    if btn_col1.form_submit_button("✅ Lưu", type="primary"):
                        if not cam_name or (cam_type == 'imou' and not imou_dev_id):
                            st.warning("Vui lòng nhập đầy đủ Tên và Device ID (nếu là Imou).")
                        else:
                            zone_id = None
                            if selected_zone != "-- Không chọn --":
                                zone_id = next((z['id'] for z in zones if z['zone_name'] == selected_zone), None)
                            
                            success = False
                            if editing_id:
                                success = db.update_camera(editing_id, cam_name, cam_type, imou_dev_id, rtsp_url, is_active, imou_chan_id, zone_id,
                                                         imou_app_id, imou_app_secret, imou_email)
                            else:
                                success = db.add_camera(cam_name, cam_type, imou_dev_id, rtsp_url, is_active, imou_chan_id, zone_id,
                                                      imou_app_id, imou_app_secret, imou_email)
                            
                            if success:
                                st.success("Lưu camera thành công!")
                                st.session_state['show_add_camera_form'] = False
                                st.session_state['edit_camera_id'] = None
                                st.rerun()
                            else:
                                st.error("Có lỗi xảy ra khi lưu vào DB.")
                    if btn_col2.form_submit_button("❌ Hủy"):
                        st.session_state['show_add_camera_form'] = False
                        st.session_state['edit_camera_id'] = None
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
                            z_tag = f"<span style='background:rgba(99,102,241,0.1); color:#6366f1; padding:2px 8px; border-radius:12px; font-size:0.7rem; margin-left:10px; border:1px solid rgba(99,102,241,0.2);'>{cam.get('zone_name') or 'N/A'}</span>"
                            st.markdown(f"{status_icon} **{cam['camera_name']}** (`{cam['camera_type'].upper()}`) {z_tag}", unsafe_allow_html=True)
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
                                    not camera['is_active'], camera.get('imou_channel_id', '0'), camera.get('zone_id'),
                                    camera.get('imou_app_id'), camera.get('imou_app_secret'), camera.get('imou_email'))
                            def delete_fn(camera=cam):
                                db.delete_camera(camera['id'])
                            def edit_fn(camera=cam):
                                st.session_state['edit_camera_id'] = camera['id']

                            btn_row1_c1, btn_row1_c2 = st.columns(2)
                            btn_row1_c1.button(toggle_txt, key=f"tg_{cam['id']}", width='stretch', on_click=toggle_fn)
                            btn_row1_c2.button("✏️", key=f"ed_{cam['id']}", width='stretch', on_click=edit_fn)
                            st.button("🗑", key=f"del_{cam['id']}", width='stretch', type="secondary", on_click=delete_fn)
                st.divider()

    # ========== TAB 2: IMOU API SETTINGS ==========
    with tab_settings:
        st.markdown("### 🔑 Cấu hình Imou Open API Mặc định")
        st.info("Cấu hình này dùng làm giá trị mặc định nếu camera không có cấu hình riêng.")
        
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
