import streamlit as st
from streamlit_app.models.detector_model import get_db

def parking_dashboard_view():
    st.title("🅿️ Dashboard Bãi xe")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    stats = db.get_occupancy_stats()
    
    if not stats:
        st.info("Chưa có dữ liệu vận hành bãi xe.")
        return

    # Overview Metrics
    total_capacity = sum(s['total_slots'] or 0 for s in stats)
    total_occupied = sum(s['occupied_slots'] or 0 for s in stats)
    
    m1, m2, m3 = st.columns(3)
    m1.metric("📊 Tổng Sức chứa", total_capacity)
    m2.metric("🚗 Đang đỗ", total_occupied)
    free_slots = total_capacity - total_occupied
    m3.metric("🟢 Chỗ trống", free_slots, delta=f"{free_slots} slots")

    st.divider()
    
    # Detailed Zone breakdown
    st.subheader("🗺 Trạng thái theo Khu vực")
    cols = st.columns(min(len(stats), 3))
    for i, s in enumerate(stats):
        with cols[i % len(cols)]:
            capacity = s['total_slots'] or 1 # Avoid div zero
            occupied = s['occupied_slots']
            percent = (occupied / capacity) * 100
            
            with st.container(border=True):
                st.markdown(f"#### {s['zone_name']}")
                st.progress(min(percent / 100, 1.0), text=f"{occupied}/{s['total_slots']}")
                
                if percent >= 90:
                    st.error("🔥 Sắp đầy")
                elif percent >= 70:
                    st.warning("⚠️ Lấp đầy cao")
                else:
                    st.success("✅ Còn nhiều chỗ")
                
                st.write(f"Số lượng: `{occupied}` / `{s['total_slots']}`")

    st.divider()
    
    # Quick View of Currently Parking Vehicles
    st.subheader("🚗 Xe đang trong bãi")
    history = db.get_parking_history(limit=10)
    currently_parking = [v for v in history if v['status'] == 'parking']
    
    if currently_parking:
        for v in currently_parking:
            with st.expander(f"🔢 {v['plate_norm']} - {v.get('zone_name','Default')}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Vào lúc:** {v['entered_at_utc'].strftime('%H:%M:%S %d/%m/%Y')}")
                    st.write(f"**Camera vào:** {v.get('cam_in', 'N/A')}")
                with c2:
                    if v.get('entry_image'):
                        st.image(v['entry_image'], caption="Ảnh lúc vào", width='stretch')
    else:
        st.info("Hiện không có xe nào đang đỗ.")
