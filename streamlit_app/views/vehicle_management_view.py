import streamlit as st
from streamlit_app.models.detector_model import get_db

def vehicle_management_view():
    st.title("🚗 Quản lý Phương tiện")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    zones = db.get_zones()
    zone_options = {z['id']: z['zone_name'] for z in zones}
    
    user_data = st.session_state.get('user', {})
    username = user_data.get('username', 'system')

    # Premium CSS for vehicle cards
    st.markdown("""
        <style>
            .vehicle-card {
                background: rgba(255, 255, 255, 0.05);
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 10px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .plate-badge {
                background: #10b981;
                color: white;
                padding: 4px 12px;
                border-radius: 8px;
                font-family: 'Courier New', Courier, monospace;
                font-weight: bold;
                font-size: 1.2rem;
                letter-spacing: 1px;
            }
        </style>
    """, unsafe_allow_html=True)

    col_form, col_list = st.columns([1, 1.5])

    with col_form:
        st.subheader("➕ Thêm Xe mới")
        with st.form("add_vehicle_form", clear_on_submit=True):
            plate = st.text_input("Biển số xe", placeholder="VD: 30A-123.45")
            owner = st.text_input("Tên chủ xe", placeholder="VD: Nguyễn Văn B")
            v_zone_id = st.selectbox("Khu vực cho phép", options=list(zone_options.keys()), format_func=lambda x: zone_options[x])
            note = st.text_input("Ghi chú")
            
            if st.form_submit_button("Đăng ký xe", type="primary", width='stretch'):
                if plate:
                    if db.upsert_vehicle_whitelist(plate.upper().replace("-", "").replace(".", ""), owner, username, v_zone_id, note):
                        st.success(f"Đã đăng ký xe: {plate}")
                        st.rerun()
                else:
                    st.error("Vui lòng nhập biển số xe")

    with col_list:
        st.subheader("📋 Danh sách Phương tiện")
        
        f_col1, f_col2 = st.columns(2)
        search_query = f_col1.text_input("🔍 Tìm kiếm biển số/chủ xe", placeholder="Nhập để lọc...")
        sel_v_zone = f_col2.selectbox("📍 Lọc theo khu vực", options=[None] + list(zone_options.keys()), format_func=lambda x: "Tất cả" if x is None else zone_options[x], key="filter_v_view")
        
        vehicles = db.get_whitelist_vehicles(sel_v_zone)
        
        if vehicles:
            if search_query:
                q = search_query.lower()
                vehicles = [v for v in vehicles if q in v['plate_number'].lower() or q in v['owner_name'].lower()]
            
            for v in vehicles:
                with st.container():
                    st.markdown(f"""
                        <div class="vehicle-card">
                            <span class="plate-badge">{v['plate_number']}</span>
                            <span style="font-size: 1.1rem; font-weight: 500; margin-left:15px;">{v['owner_name']}</span>
                            <div style="margin-top:10px; opacity:0.7; font-size:0.9rem;">
                                📍 Khu vực: {v.get('zone_name', 'N/A')} <br>
                                📝 {v['note'] or 'Không có ghi chú'}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    c_del, _ = st.columns([1, 4])
                    if c_del.button("Xóa", key=f"del_v_{v['id']}", type="secondary", icon="🗑️"):
                        if db.delete_whitelist_vehicle(v['plate_number'], v.get('zone_id')):
                            st.rerun()
        else:
            st.info("Chưa có phương tiện nào trong danh sách.")
