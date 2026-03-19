import streamlit as st
from collections import Counter
from streamlit_app.controllers.camera_controller import camera_controller, get_camera_providers
from streamlit_app.controllers.provider_controller import provider_controller
from streamlit_app.utils.toast import show_toast
from streamlit_app.util.ui_components import render_page_header, render_metric_card, render_table_header, render_table_row, get_status_dot, render_provider_badge


def _render_stats(cameras):
    """Render 4 metric cards at top."""
    total = len(cameras)
    active = sum(1 for c in cameras if c['is_active'])
    inactive = total - active
    providers = len(set(c['camera_type'] for c in cameras))

    cols = st.columns(4)
    metrics = [
        ("📹", "Tổng Camera", total, "text-white"),
        ("🟢", "Đang hoạt động", active, "text-emerald-400"),
        ("⚫", "Không hoạt động", inactive, "text-slate-500"),
        ("🏭", "Nhà cung cấp", providers, "text-cyan-400"),
    ]
    for col, (icon, label, value, css_class) in zip(cols, metrics):
        col.markdown(render_metric_card(icon, label, value, css_class), unsafe_allow_html=True)


def _render_filters(cameras, provider_types, provider_labels):
    """Render filter bar. Returns (provider_filter, status_filter, search_query)."""
    fc1, fc2, fc3 = st.columns([3, 2, 3])
    with fc1:
        all_types = ["all"] + list(provider_types)
        provider_filter = st.selectbox(
            "Nhà cung cấp",
            all_types,
            format_func=lambda x: "Tất cả" if x == "all" else provider_labels.get(x, x),
            key="cam_filter_provider",
            label_visibility="collapsed",
        )
    with fc2:
        status_filter = st.selectbox(
            "Trạng thái",
            ["all", "active", "inactive"],
            format_func=lambda x: {"all": "Tất cả trạng thái", "active": "🟢 Hoạt động", "inactive": "⚫ Tắt"}[x],
            key="cam_filter_status",
            label_visibility="collapsed",
        )
    with fc3:
        search_query = st.text_input("Tìm kiếm", placeholder="Tìm tên camera, device ID...", key="cam_search", label_visibility="collapsed")
    return provider_filter, status_filter, search_query


def _apply_filters(cameras, provider_filter, status_filter, search_query, active_site_id):
    """Apply all filters to camera list."""
    filtered = cameras
    if active_site_id:
        filtered = [c for c in filtered if active_site_id in (c.get('site_ids') or [])]
    if provider_filter != "all":
        filtered = [c for c in filtered if c['camera_type'] == provider_filter]
    if status_filter == "active":
        filtered = [c for c in filtered if c['is_active']]
    elif status_filter == "inactive":
        filtered = [c for c in filtered if not c['is_active']]
    if search_query:
        q = search_query.lower()
        filtered = [c for c in filtered if q in c['camera_name'].lower() or q in (c.get('imou_device_id') or '').lower() or q in (c.get('rtsp_url') or '').lower()]
    return filtered


def _render_camera_table(cameras, provider_labels):
    """Render camera data table."""
    if not cameras:
        st.info("Không có camera nào phù hợp với bộ lọc.")
        return

    # Table header
    col_specs = [
        {'label': 'TT', 'width': '50px', 'align': 'center'},
        {'label': 'Tên Camera', 'width': '180px'},
        {'label': 'Nhà cung cấp', 'width': '110px'},
        {'label': 'Địa điểm', 'width': '140px'},
        {'label': 'Device ID', 'width': '170px'},
        {'label': 'RTSP URL', 'width': '200px'},
        {'label': 'API', 'width': '60px', 'align': 'center'},
        {'label': 'Cập nhật', 'width': '100px'},
    ]
    st.markdown(render_table_header(col_specs), unsafe_allow_html=True)

    for cam in cameras:
        _render_camera_row(cam, provider_labels)


def _render_camera_row(cam, provider_labels):
    """Render a single camera row with actions."""
    status_dot = get_status_dot(cam['is_active'])
    cam_name = f"<span class='font-bold text-slate-50'>{cam['camera_name']}</span>"
    cam_type = cam['camera_type']
    provider_label = provider_labels.get(cam_type, cam_type.upper())
    
    site_names = cam.get('site_names') or []
    sites_html = " ".join(f'<span class="inline-flex px-2 py-0.5 rounded-md text-xs bg-slate-800 border border-slate-700 text-slate-300">{s}</span>' for s in site_names) if site_names else '<span class="text-slate-500 text-xs">—</span>'
    
    device_id = f"<span class='text-xs font-mono text-slate-400'>{cam.get('imou_device_id') or '—'}</span>"
    rtsp = cam.get('rtsp_url') or "—"
    rtsp_display = f"<span class='text-[11px] font-mono text-slate-500 truncate block' title='{rtsp}'>{(rtsp[:35] + '...') if len(rtsp) > 35 else rtsp}</span>"
    
    has_keys = bool(cam.get('provider_keys'))
    api_icon = "🔑" if has_keys else "⚠️"
    updated = cam.get('updated_at')
    updated_str = f"<span class='text-[11px] text-slate-500'>{updated.strftime('%d/%m %H:%M') if updated else '—'}</span>"

    col_data = [
        {'content': status_dot, 'width': '50px', 'align': 'center'},
        {'content': cam_name, 'width': '180px'},
        {'content': render_provider_badge(cam_type, provider_label), 'width': '110px'},
        {'content': sites_html, 'width': '140px'},
        {'content': device_id, 'width': '170px'},
        {'content': rtsp_display, 'width': '200px'},
        {'content': api_icon, 'width': '60px', 'align': 'center'},
        {'content': updated_str, 'width': '100px'}
    ]

    st.markdown(render_table_row(col_data, is_active=cam['is_active']), unsafe_allow_html=True)

    # Action buttons row
    ac1, ac2, ac3, ac4 = st.columns([1, 1, 1, 3])
    toggle_txt = "⏸" if cam['is_active'] else "▶"

    def toggle_fn(camera=cam):
        status = not camera['is_active']
        camera_controller.update_camera(camera['id'], camera['camera_name'], camera['camera_type'],
            camera.get('imou_device_id'), camera.get('rtsp_url'),
            status, camera.get('imou_channel_id', '0'), site_ids=camera.get('site_ids'))
        show_toast(f"Đã {'bật' if status else 'tạm dừng'} camera {camera['camera_name']}", type="success")

    def edit_fn(camera=cam):
        st.session_state['edit_camera_id'] = camera['id']

    def delete_fn(camera=cam):
        if camera_controller.delete_camera(camera['id']):
            show_toast(f"Đã xóa camera {camera['camera_name']}", type="success")

    ac1.button(toggle_txt, key=f"tg_{cam['id']}", on_click=toggle_fn)
    ac2.button("✏️", key=f"ed_{cam['id']}", on_click=edit_fn)
    confirm_key = f'confirm_del_cam_{cam["id"]}'
    if st.session_state.get(confirm_key):
        ac3.button("✅", key=f"yes_{cam['id']}", on_click=lambda: (delete_fn(), st.session_state.pop(confirm_key, None)))
        ac4.button("❌", key=f"no_{cam['id']}", on_click=lambda: st.session_state.pop(confirm_key, None))
    else:
        ac3.button("🗑", key=f"del_{cam['id']}", on_click=lambda: st.session_state.update({confirm_key: True}))


def _render_add_edit_form(provider_types, provider_labels):
    """Render add/edit camera form. Returns True if form is visible."""
    if not st.session_state.get('show_add_camera_form') and not st.session_state.get('edit_camera_id'):
        return False

    editing_id = st.session_state.get('edit_camera_id')
    editing_cam = camera_controller.get_camera(editing_id) if editing_id else None
    sites = camera_controller.get_sites()
    site_options = {s['id']: s['site_name'] for s in sites}

    with st.container(border=True):
        st.subheader("Sửa Camera" if editing_id else "Thêm Camera Mới")
        default_type_idx = 0
        if editing_cam:
            type_val = editing_cam.get('camera_type', '')
            if type_val in provider_types:
                default_type_idx = provider_types.index(type_val)

        cam_type = st.selectbox("Nhà cung cấp Camera", provider_types, index=default_type_idx,
            format_func=lambda x: provider_labels.get(x, x), key="cam_type_selector")

        global_keys = camera_controller.get_provider_api_keys(cam_type)
        camera_keys = editing_cam.get('provider_keys', {}) if editing_cam and isinstance(editing_cam.get('provider_keys'), dict) else {}

        with st.form("form_camera_details_v3"):
            c1, c2 = st.columns(2)
            cam_name = c1.text_input("Tên Camera (*)", value=editing_cam['camera_name'] if editing_cam else "", placeholder="Cam Cổng Chính")
            current_site_ids = editing_cam.get('site_ids') or [] if editing_cam else []
            selected_sites = c2.multiselect("Địa điểm", options=list(site_options.keys()),
                default=[s for s in current_site_ids if s in site_options], format_func=lambda x: site_options[x])

            rtsp_url = editing_cam.get('rtsp_url', "") if editing_cam else ""
            imou_dev_id, imou_chan_id = "", "0"
            if cam_type == "imou":
                c3, c4 = st.columns(2)
                imou_dev_id = c3.text_input("Imou Device ID (*)", value=editing_cam.get('imou_device_id', "") if editing_cam else "", placeholder="L2FXXXXXX")
                imou_chan_id = c4.text_input("Imou Channel ID", value=editing_cam.get('imou_channel_id', "0") if editing_cam else "0")

            rtsp_url = st.text_input("RTSP URL (để trống hệ thống sẽ tự thiết lập từ nhà cung cấp)", value=rtsp_url,
                placeholder="rtsp://admin:pass@ip:554/cam/realmonitor?channel=1&subtype=1")

            field_values = {}
            if global_keys:
                st.markdown("---")
                provider_label = provider_labels.get(cam_type, cam_type)
                st.caption(f"💾 Cấu hình API Key ({provider_label})")
                api_keys_list = list(global_keys.keys())
                cols = st.columns(max(len(api_keys_list), 1))
                for col, fkey in zip(cols, api_keys_list):
                    saved = camera_keys.get(fkey, "") or global_keys.get(fkey, "")
                    is_secret = "secret" in fkey or "password" in fkey
                    field_values[fkey] = col.text_input(fkey.replace("_", " ").title(), value=saved,
                        type="password" if is_secret else "default")

            is_active = st.checkbox("Kích hoạt", value=editing_cam['is_active'] if editing_cam else True)
            btn_col1, btn_col2 = st.columns(2)
            if btn_col1.form_submit_button("✅ Lưu", type="primary"):
                if not cam_name or (cam_type == 'imou' and not imou_dev_id):
                    st.warning("Vui lòng nhập đầy đủ Tên và Device ID (nếu là Imou).")
                else:
                    keys_to_save = {k: str(v).strip() for k, v in field_values.items() if str(v).strip()}
                    has_custom = any(keys_to_save.get(k) != global_keys.get(k, '') for k in keys_to_save)
                    if has_custom:
                        specific_keys_for_db = keys_to_save
                    else:
                        if keys_to_save:
                            camera_controller.update_provider_api_keys(cam_type, keys_to_save)
                        specific_keys_for_db = {}

                    final_rtsp = rtsp_url.strip()
                    if not final_rtsp:
                        p_info = provider_controller.get(cam_type)
                        template = p_info.get('rtsp_template') if p_info else ""
                        if template:
                            u = keys_to_save.get('username') or global_keys.get('username', '')
                            p = keys_to_save.get('password') or global_keys.get('password', '')
                            ip = keys_to_save.get('host') or keys_to_save.get('ip') or global_keys.get('host', global_keys.get('ip', ''))
                            if u and p and ip:
                                final_rtsp = template.replace('{user}', u).replace('{pass}', p).replace('{ip}', ip).replace('{host}', ip)

                    success = False
                    if editing_id:
                        success = camera_controller.update_camera(editing_id, cam_name, cam_type, imou_dev_id, final_rtsp, is_active, imou_chan_id, site_ids=selected_sites, provider_keys=specific_keys_for_db)
                    else:
                        success = camera_controller.add_camera(cam_name, cam_type, imou_dev_id, final_rtsp, is_active, imou_chan_id, site_ids=selected_sites, provider_keys=specific_keys_for_db)

                    if success:
                        st.session_state['show_add_camera_form'] = False
                        st.session_state['edit_camera_id'] = None
                        show_toast("Lưu camera thành công!", type="success")
                        st.rerun()
                    else:
                        st.error("Có lỗi xảy ra khi lưu vào DB.")
            if btn_col2.form_submit_button("❌ Hủy"):
                st.session_state['show_add_camera_form'] = False
                st.session_state['edit_camera_id'] = None
                st.rerun()
    return True


def camera_management_view():
    """Main camera management page with stats, filters, and data table."""
    CAMERA_PROVIDERS = get_camera_providers()
    PROVIDER_TYPES = list(CAMERA_PROVIDERS.keys())
    PROVIDER_LABELS = {k: v["label"] for k, v in CAMERA_PROVIDERS.items()}

    # Header
    hc1, hc2 = st.columns([3, 1])
    with hc1:
        render_page_header("Quản lý Camera", "📹")

    if hc2.button("➕ Thêm Camera", type="primary", use_container_width=True):
        st.session_state['show_add_camera_form'] = True

    # Add/Edit form (inline)
    _render_add_edit_form(PROVIDER_TYPES, PROVIDER_LABELS)

    # Load data
    cameras = camera_controller.get_all_cameras()

    # Stats row
    _render_stats(cameras)

    # Filters
    provider_filter, status_filter, search_query = _render_filters(cameras, PROVIDER_TYPES, PROVIDER_LABELS)

    # Apply filters
    active_site_id = st.session_state.get('active_site_id')
    filtered = _apply_filters(cameras, provider_filter, status_filter, search_query, active_site_id)

    # Results count
    st.caption(f"Hiển thị {len(filtered)}/{len(cameras)} camera")

    # Camera table
    _render_camera_table(filtered, PROVIDER_LABELS)
