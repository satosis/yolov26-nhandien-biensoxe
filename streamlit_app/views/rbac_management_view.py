import streamlit as st
from streamlit_app.controllers.rbac_controller import rbac_controller
from streamlit_app.utils.toast import show_toast
from streamlit_app.menu_registry import ALL_MENUS

def rbac_management_view():
    st.subheader("🛡️ Quản lý Phân quyền (RBAC)")

    if 'just_added_role' in st.session_state:
        show_toast(st.session_state.pop('just_added_role'), type="success")
    if 'just_updated_role' in st.session_state:
        show_toast(st.session_state.pop('just_updated_role'), type="success")
    if 'just_deleted_role' in st.session_state:
        show_toast(st.session_state.pop('just_deleted_role'), type="success")

    st.info("Ghi chú: Admin luôn có toàn quyền (*). Những role khác chỉ nhìn thấy các menu được gán ở đây.")

    # Create Role Form
    with st.expander("➕ Tạo Role mới"):
        with st.form("create_role_form"):
            new_role_name = st.text_input("Tên Role *", placeholder="VD: Operator")
            if st.form_submit_button("✅ Tạo Role", type="primary"):
                if new_role_name:
                    ok, msg = rbac_controller.create_role(new_role_name)
                    if ok:
                        st.session_state['just_added_role'] = f"Đã tạo role: {new_role_name}"
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Vui lòng nhập tên role.")

    st.divider()
    roles = rbac_controller.get_roles()
    all_menus = ALL_MENUS

    for r in roles:
        role_desc = r.get('description', '')
        title = f"Phân quyền cho: {r['role_name']}"
        if role_desc:
            title += f" - {role_desc}"

        with st.expander(title):
            # Roles with '*' already have full access
            if r.get('allowed_menus', '') == '*':
                st.success(f"{r['role_name']} luôn có đầy đủ quyền truy cập (All Menus: *)")
                continue

            with st.form(f"rbac_form_{r['id']}"):
                current_menus = r.get('allowed_menus', '').split(',')
                selected_for_role = []

                st.write("Đánh dấu các menu hiển thị:")
                cols = st.columns(4)
                for idx, (m_key, m_label) in enumerate(all_menus.items()):
                    col = cols[idx % 4]
                    is_checked = (m_key in current_menus) or (current_menus == ['*'])
                    if col.checkbox(m_label, value=is_checked, key=f"chk_{r['id']}_{m_key}"):
                        selected_for_role.append(m_key)

                btn_save, btn_del = st.columns([3, 1])
                if btn_save.form_submit_button("💾 Lưu Cấu Hình Quyền", type="primary", use_container_width=True):
                    new_allowed = ",".join(selected_for_role)
                    if selected_for_role == list(all_menus.keys()):
                        new_allowed = "*"
                    elif not selected_for_role:
                        new_allowed = "none"
                    if rbac_controller.update_role_permissions(r['id'], new_allowed):
                        st.session_state['just_updated_role'] = f"Đã cập nhật cấu hình phân quyền cho {r['role_name']}"
                        st.rerun()
                    else:
                        st.error("Lỗi lưu cấu hình RBAC")
                if btn_del.form_submit_button("🗑 Xóa Role", use_container_width=True):
                    st.session_state[f'confirm_del_role_{r["id"]}'] = True
                    st.rerun()

            confirm_key = f'confirm_del_role_{r["id"]}'
            if st.session_state.get(confirm_key):
                st.warning(f"Xác nhận xóa role **{r['role_name']}**? Hành động này không thể hoàn tác.")
                yes_col, no_col = st.columns(2)
                if yes_col.button("✅ Xác nhận xóa", key=f"yes_del_role_{r['id']}", type="primary"):
                    if rbac_controller.delete_role(r['id']):
                        st.session_state['just_deleted_role'] = f"Đã xóa role: {r['role_name']}"
                    del st.session_state[confirm_key]
                    st.rerun()
                if no_col.button("❌ Hủy", key=f"no_del_role_{r['id']}"):
                    del st.session_state[confirm_key]
                    st.rerun()
