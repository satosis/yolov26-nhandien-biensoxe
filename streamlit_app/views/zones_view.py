import streamlit as st
from streamlit_app.models.detector_model import get_db

def zones_view():
    st.title("🗺 Quản lý Khu vực")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    st.markdown("### 🗺 Danh sách Khu vực Camera")
    zones = db.get_zones()
    
    # Form to add new zone
    with st.form("form_add_zone_dedicated"):
        zc1, zc2, zc3, zc4 = st.columns([2, 3, 1, 1])
        new_zone = zc1.text_input("Tên khu vực mới", placeholder="Tầng 2")
        new_desc = zc2.text_input("Mô tả (tùy chọn)", placeholder="Khu vực tầng 2 văn phòng")
        new_slots = zc3.number_input("Sức chứa", min_value=0, value=10)
        zc4.write("")  # spacer
        if zc4.form_submit_button("➕ Thêm", type="primary"):
            if new_zone:
                if db.add_zone(new_zone, new_desc):
                    # Find the newly added zone id (not ideal but add_zone returns bool)
                    # For now we'll update slots via a separate call or just modify add_zone
                    # Actually let's just modify the add_zone method in database.py
                    zones = db.get_zones()
                    zid = next((z['id'] for z in zones if z['zone_name'] == new_zone), None)
                    if zid:
                        db.update_zone_slots(zid, new_slots)
                    st.success(f"Đã thêm khu vực: {new_zone}")
                    st.rerun()
                else:
                    st.error("Khu vực đã tồn tại hoặc có lỗi.")
            else:
                st.warning("Nhập tên khu vực.")

    st.divider()

    if zones:
        # Display existing zones in a grid or list
        for z in zones:
            with st.container(border=True):
                zc1, zc2 = st.columns([4, 1])
                with zc1:
                    st.markdown(f"#### {z['zone_name']}")
                    if z.get('description'):
                        st.caption(z['description'])
                    
                    cameras = db.get_all_cameras()
                    cam_count = len([c for c in cameras if c.get('zone_id') == z['id']])
                    st.write(f"📊 Sức chứa: `{z.get('total_slots', 0)}` | Hiện có: `{cam_count}` camera")
                    
                    new_slot_val = st.number_input("Cập nhật sức chứa", value=z.get('total_slots', 0), key=f"slot_{z['id']}")
                    if new_slot_val != z.get('total_slots'):
                        if db.update_zone_slots(z['id'], new_slot_val):
                            st.success("Cập nhật thành công")
                            st.rerun()
                    
                with zc2:
                    def del_zone(zid=z['id']):
                        if db.delete_zone(zid):
                             st.success("Đã xóa khu vực")
                        else:
                             st.error("Không thể xóa khu vực (co thể do còn camera liên kết)")

                    if st.button("🗑 Xóa", key=f"dz_dedicated_{z['id']}", type="secondary", width='stretch'):
                        del_zone(z['id'])
                        st.rerun()
    else:
        st.info("Chưa có khu vực nào. Hãy thêm khu vực đầu tiên phía trên.")
