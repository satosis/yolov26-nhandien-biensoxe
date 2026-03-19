import streamlit as st
import html as html_mod
from streamlit_app.controllers.stranger_controller import stranger_controller
from streamlit_app.controllers.user_controller import user_controller
from streamlit_app.utils.toast import show_toast


STATUS_LABELS = {
    None: "Tất cả",
    "new": "Mới",
    "reviewed": "Đã xem",
    "converted": "Đã tạo TK",
}

STATUS_BADGE = {
    "new": ("🔴", "#ef4444"),
    "reviewed": ("🟡", "#f59e0b"),
    "converted": ("🟢", "#22c55e"),
}


def stranger_management_view():
    st.title("👤 Quản lý Người lạ")

    for key in ("stranger_created", "stranger_deleted", "stranger_updated"):
        if key in st.session_state:
            show_toast(st.session_state.pop(key), type="success")

    col_filter, col_limit = st.columns([2, 1])
    with col_filter:
        status_options = list(STATUS_LABELS.keys())
        sel_status = st.selectbox(
            "Lọc theo trạng thái",
            options=status_options,
            format_func=lambda x: STATUS_LABELS[x],
            key="stranger_status_filter",
        )
    with col_limit:
        limit = st.number_input("Số lượng hiển thị", min_value=10, max_value=500, value=50, step=10)

    strangers = stranger_controller.get_strangers(status=sel_status, limit=int(limit))

    if not strangers:
        st.info("Không có bản ghi người lạ nào.")
        return

    st.caption(f"{len(strangers)} bản ghi")

    for s in strangers:
        sid = s['id']
        status = s.get('status', 'new')
        badge_icon, badge_color = STATUS_BADGE.get(status, ("⚪", "#94a3b8"))
        cam_label = html_mod.escape(str(s.get('camera_id') or 'N/A'))
        ts_label = html_mod.escape(str(s.get('detected_at') or s.get('created_at') or ''))
        sim_val = s.get('similarity')
        sim_label = f"{sim_val:.2f}" if sim_val is not None else "N/A"

        with st.container(border=True):
            img_col, info_col, action_col = st.columns([1, 3, 1.5])

            with img_col:
                img_path = s.get('image_path')
                if img_path:
                    import os
                    if os.path.exists(img_path):
                        st.image(img_path, use_container_width=True)
                    else:
                        st.caption("Ảnh không tồn tại")
                else:
                    st.caption("Không có ảnh")

            with info_col:
                st.markdown(
                    f"<span style='background:{badge_color};color:#fff;padding:2px 8px;"
                    f"border-radius:6px;font-size:0.78rem;'>{badge_icon} {status}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**ID:** {sid}")
                st.caption(f"Camera: {cam_label}")
                st.caption(f"Thời gian: {ts_label}")
                st.caption(f"Độ tương đồng: {sim_label}")
                if s.get('note'):
                    st.caption(f"Ghi chú: {html_mod.escape(str(s['note']))}")

            with action_col:
                if status == "new":
                    if st.button("Đã xem", key=f"review_{sid}", use_container_width=True):
                        stranger_controller.update_status(sid, "reviewed")
                        st.session_state["stranger_updated"] = f"Đã cập nhật trạng thái ID {sid}"
                        st.rerun()

                confirm_key = f"confirm_del_stranger_{sid}"
                if st.session_state.get(confirm_key):
                    y_col, n_col = st.columns(2)
                    if y_col.button("OK", key=f"yes_del_{sid}", use_container_width=True, type="primary"):
                        stranger_controller.delete(sid)
                        st.session_state.pop(confirm_key, None)
                        st.session_state["stranger_deleted"] = f"Đã xóa người lạ ID {sid}"
                        st.rerun()
                    if n_col.button("Hủy", key=f"no_del_{sid}", use_container_width=True):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("Xóa", key=f"del_{sid}", use_container_width=True, type="secondary"):
                        st.session_state[confirm_key] = True
                        st.rerun()

                if status != "converted":
                    create_key = f"create_form_{sid}"
                    if st.button("Tạo tài khoản", key=f"btn_create_{sid}", use_container_width=True):
                        st.session_state[create_key] = not st.session_state.get(create_key, False)
                        st.rerun()

            create_key = f"create_form_{sid}"
            if st.session_state.get(create_key) and status != "converted":
                _render_create_account_form(sid)


def _render_create_account_form(stranger_id):
    """Render inline form to create a user account from a stranger record."""
    roles = user_controller.get_roles()
    role_names = [r['role_name'] for r in roles]
    if not role_names:
        st.warning("Chưa có role nào trong DB.")
        return
    from streamlit_app.controllers.site_controller import site_controller
    sites = site_controller.get_sites()
    site_options = {s['id']: s['site_name'] for s in sites}

    gen_username_key = f"gen_username_{stranger_id}"
    if gen_username_key not in st.session_state:
        from streamlit_app.controllers.stranger_controller import stranger_controller as sc
        st.session_state[gen_username_key] = sc.generate_username("user")

    with st.container(border=True):
        st.markdown("**Tạo tài khoản từ người lạ**")

        with st.form(f"form_stranger_create_{stranger_id}", clear_on_submit=False):
            st.text_input(
                "Username (tự động)",
                value=st.session_state[gen_username_key],
                disabled=True,
            )
            full_name = st.text_input("Họ tên", placeholder="VD: Nguyễn Văn A")

            default_role_idx = len(role_names) - 1
            sel_role = st.selectbox("Role", role_names, index=default_role_idx)

            sel_sites = st.multiselect(
                "Địa điểm",
                options=list(site_options.keys()),
                format_func=lambda x: site_options[x],
            )
            note = st.text_input("Ghi chú", placeholder="Tùy chọn")

            submitted = st.form_submit_button("Tạo", type="primary", use_container_width=True)
            if submitted:
                from streamlit_app.controllers.stranger_controller import stranger_controller as sc
                ok, msg = sc.create_user_from_stranger(
                    stranger_id,
                    full_name=full_name or None,
                    role_name=sel_role,
                    site_ids=sel_sites or None,
                    note=note or None,
                )
                if ok:
                    st.session_state.pop(f"create_form_{stranger_id}", None)
                    st.session_state.pop(f"gen_username_{stranger_id}", None)
                    st.session_state["stranger_created"] = msg
                    st.rerun()
                else:
                    st.error(msg)
