import streamlit as st
import html as html_mod
from streamlit_app.controllers.vehicle_controller import vehicle_controller
from streamlit_app.utils.toast import show_toast

def vehicle_management_view():
    st.title("🚗 Quản lý Phương tiện")
    
    if 'just_added_vehicle' in st.session_state:
        show_toast(st.session_state.pop('just_added_vehicle'), type="success")
    if 'just_deleted_vehicle' in st.session_state:
        show_toast(st.session_state.pop('just_deleted_vehicle'), type="success")
    if 'just_updated_vehicle' in st.session_state:
        show_toast(st.session_state.pop('just_updated_vehicle'), type="success")

    sites = vehicle_controller.get_sites()
    site_options = {s['id']: s['site_name'] for s in sites}
    
    user_data = st.session_state.get('user', {})
    username = user_data.get('username', 'system')

    # Premium CSS for vehicle cards
    

    col_form, col_list = st.columns([1, 1.5])

    with col_form:
        edit_v = st.session_state.get('edit_vehicle')
        st.subheader("✏️ Chỉnh sửa Xe" if edit_v else "➕ Thêm Xe mới")
        
        if not site_options:
            st.warning("Chưa có khu vực nào. Vui lòng tạo khu vực trước.")
            if st.button("🗺 Tạo khu vực", use_container_width=True):
                st.query_params["menu"] = "sites"
                st.rerun()
        else:
            with st.form("add_vehicle_form", clear_on_submit=False):
                plate = st.text_input(
                    "Biển số xe",
                    value=edit_v.get('plate_number', '') if edit_v else "",
                    placeholder="VD: 30A-123.45",
                )
                owner = st.text_input(
                    "Tên chủ xe", 
                    value=edit_v.get('owner_name', '') if edit_v else "",
                    placeholder="VD: Nguyễn Văn B"
                )
                
                # Find index for selectbox
                default_site_id = edit_v.get('site_id') if edit_v else None
                site_index = 0
                if default_site_id in site_options:
                    site_index = list(site_options.keys()).index(default_site_id)

                v_site_id = st.selectbox(
                    "Địa điểm cho phép",
                    options=list(site_options.keys()),
                    index=site_index,
                    format_func=lambda x: site_options[x],
                )
                
                note = st.text_input(
                    "Ghi chú", 
                    value=edit_v.get('note', '') if edit_v else ""
                )

                btn_label = "Cập nhật thông tin" if edit_v else "Đăng ký xe"
                if st.form_submit_button(btn_label, type="primary", use_container_width=True):
                    if plate:
                        clean_plate = plate.upper().replace("-", "").replace(".", "").replace(" ", "").strip()
                        if edit_v:
                            # Use update logic with ID
                            success = vehicle_controller.update_vehicle_whitelist(edit_v['id'], clean_plate, owner, v_site_id, note)
                            msg = "Đã cập nhật"
                        else:
                            # Use upsert logic for new
                            success = vehicle_controller.upsert_vehicle_whitelist(clean_plate, owner, username, v_site_id, note)
                            msg = "Đã đăng ký"
                        
                        if success:
                            st.session_state['just_updated_vehicle' if edit_v else 'just_added_vehicle'] = f"{msg} xe: {plate}"
                            st.session_state.pop('edit_vehicle', None)
                            st.rerun()
                        else:
                            st.error(f"Lỗi: Không thể {msg.lower()} xe. Có thể biển số đã tồn tại trong khu vực này.")
                    else:
                        st.error("Vui lòng nhập biển số xe")
            
            if edit_v:
                if st.button("❌ Hủy chỉnh sửa", use_container_width=True):
                    st.session_state.pop('edit_vehicle', None)
                    st.rerun()

    with col_list:
        st.subheader("📋 Danh sách Phương tiện")

        f_col1, f_col2 = st.columns(2)
        search_query = f_col1.text_input("🔍 Tìm kiếm", placeholder="Biển số hoặc tên chủ xe...")
        filter_options = [None] + list(site_options.keys())
        sel_v_site = f_col2.selectbox(
            "📍 Lọc địa điểm", options=filter_options,
            format_func=lambda x: "Tất cả" if x is None else site_options.get(x, "N/A"),
            key="filter_v_view",
        )

        vehicles = vehicle_controller.get_whitelist_vehicles(sel_v_site)

        if vehicles:
            if search_query:
                q = search_query.lower()
                vehicles = [v for v in vehicles if q in (v.get('plate_number') or '').lower() or q in (v.get('owner_name') or '').lower()]

            st.caption(f"{len(vehicles)} xe")
            for v in vehicles:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([2, 2, 1])
                    c1.markdown(f"**{html_mod.escape(v.get('plate_number', ''))}**")
                    c1.caption(f"{html_mod.escape(v.get('owner_name') or 'Chưa có tên')}")
                    c2.caption(f"📍 {html_mod.escape(v.get('site_name') or 'N/A')}")
                    c2.caption(f"📝 {html_mod.escape(v.get('note') or 'Không có ghi chú')}")

                    with c3:
                        if st.button("✏️ Sửa", key=f"edit_v_{v['id']}", use_container_width=True):
                            st.session_state['edit_vehicle'] = v
                            st.rerun()

                        confirm_key = f'confirm_del_v_{v["id"]}'
                        if st.session_state.get(confirm_key):
                            y, n = st.columns(2)
                            if y.button("✅", key=f"yes_dv_{v['id']}", use_container_width=True):
                                if vehicle_controller.delete_whitelist_vehicle(v['plate_number'], v.get('site_id')):
                                    st.session_state['just_deleted_vehicle'] = f"Đã xóa: {v['plate_number']}"
                                    del st.session_state[confirm_key]
                                    st.rerun()
                            if n.button("❌", key=f"no_dv_{v['id']}", use_container_width=True):
                                del st.session_state[confirm_key]
                                st.rerun()
                        else:
                            if st.button("🗑 Xóa", key=f"del_v_{v['id']}", use_container_width=True, type="secondary"):
                                st.session_state[confirm_key] = True
                                st.rerun()
        else:
            st.info("Chưa có phương tiện nào trong danh sách.")
