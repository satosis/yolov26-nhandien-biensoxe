import streamlit as st
from streamlit_app.controllers.site_controller import site_controller
from streamlit_app.utils.toast import show_toast


def sites_view():
    st.title("🏢 Quản lý Địa điểm")

    sites = site_controller.get_sites()

    # Form to add new site
    with st.form("form_add_site"):
        sc1, sc2, sc3, sc4, sc5 = st.columns([2, 2, 3, 1, 1])
        new_name = sc1.text_input("Tên địa điểm *", placeholder="Tòa nhà A")
        new_addr = sc2.text_input("Địa chỉ", placeholder="123 Nguyễn Văn Linh")
        new_desc = sc3.text_input("Mô tả (tùy chọn)", placeholder="Bãi xe tầng hầm B1")
        new_slots = sc4.number_input("Sức chứa", min_value=0, value=0, step=1, key="add_slots")
        sc5.write("")
        if sc5.form_submit_button("➕ Thêm", type="primary"):
            if new_name:
                if site_controller.add_site(new_name, new_addr, new_desc, new_slots):
                    show_toast(f"Đã thêm địa điểm: {new_name}", type="success")
                    st.rerun()
                else:
                    st.error("Địa điểm đã tồn tại hoặc có lỗi.")
            else:
                st.warning("Nhập tên địa điểm.")

    st.divider()

    if not sites:
        st.info("Chưa có địa điểm nào. Hãy thêm địa điểm đầu tiên phía trên.")
        return

    for site in sites:
        with st.container(border=True):
            col_info, col_actions = st.columns([4, 1])

            with col_info:
                status_badge = "🟢" if site.get("is_active", True) else "🔴"
                st.markdown(f"#### {status_badge} {site['site_name']}")
                if site.get("address"):
                    st.caption(f"📍 {site['address']}")
                if site.get("description"):
                    st.caption(site["description"])

                st.write(f"🅿️ Sức chứa: `{site.get('total_slots', 0)}` chỗ")

                # Inline edit form
                edit_key = f"edit_site_{site['id']}"
                if st.session_state.get(edit_key):
                    with st.form(f"form_edit_site_{site['id']}"):
                        ec1, ec2 = st.columns(2)
                        edit_name = ec1.text_input("Tên địa điểm", value=site["site_name"], key=f"edit_name_{site['id']}")
                        edit_addr = ec2.text_input("Địa chỉ", value=site.get("address", ""), key=f"edit_addr_{site['id']}")
                        edit_desc = st.text_input("Mô tả", value=site.get("description", ""), key=f"edit_desc_{site['id']}")
                        edit_active = st.checkbox("Đang hoạt động", value=site.get("is_active", True), key=f"edit_active_{site['id']}")
                        edit_slots = st.number_input(
                            "Sức chứa",
                            min_value=0,
                            value=int(site.get("total_slots") or 0),
                            step=1,
                            key=f"edit_slots_{site['id']}",
                        )
                        save_col, cancel_col = st.columns(2)
                        if save_col.form_submit_button("💾 Lưu", type="primary"):
                            if edit_name:
                                if site_controller.update_site(site["id"], edit_name, edit_addr, edit_desc, edit_active, edit_slots):
                                    show_toast(f"Đã cập nhật: {edit_name}", type="success")
                                    del st.session_state[edit_key]
                                    st.rerun()
                                else:
                                    st.error("Cập nhật thất bại.")
                            else:
                                st.warning("Tên địa điểm không được để trống.")
                        if cancel_col.form_submit_button("❌ Hủy"):
                            del st.session_state[edit_key]
                            st.rerun()

            with col_actions:
                edit_key = f"edit_site_{site['id']}"
                if not st.session_state.get(edit_key):
                    if st.button("✏️ Sửa", key=f"btn_edit_{site['id']}", width="stretch"):
                        st.session_state[edit_key] = True
                        st.rerun()

                confirm_key = f"confirm_del_site_{site['id']}"
                if st.session_state.get(confirm_key):
                    st.warning(f"Xác nhận xóa '{site['site_name']}'?")
                    yes_col, no_col = st.columns(2)
                    if yes_col.button("✅ Có", key=f"yes_ds_{site['id']}", width="stretch"):
                        if site_controller.delete_site(site["id"]):
                            show_toast(f"Đã xóa địa điểm {site['site_name']}", type="success")
                        else:
                            st.error("Không thể xóa địa điểm.")
                        del st.session_state[confirm_key]
                        st.rerun()
                    if no_col.button("❌ Không", key=f"no_ds_{site['id']}", width="stretch"):
                        del st.session_state[confirm_key]
                        st.rerun()
                else:
                    if st.button("🗑 Xóa", key=f"btn_del_{site['id']}", type="secondary", width="stretch"):
                        st.session_state[confirm_key] = True
                        st.rerun()
