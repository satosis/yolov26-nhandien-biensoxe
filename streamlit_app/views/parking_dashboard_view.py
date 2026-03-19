import streamlit as st
from streamlit_app.controllers.parking_controller import parking_controller

def parking_dashboard_view():
    st.title("🅿️ Dashboard Bãi xe")

    # Quick action buttons
    act1, act2, act3, act4 = st.columns(4)
    if act1.button("📋 Nhật ký Vào-Ra", use_container_width=True):
        st.query_params["menu"] = "parking_logs"
        st.rerun()
    if act2.button("📊 Phân tích", use_container_width=True):
        st.query_params["menu"] = "parking_analytics"
        st.rerun()
    if act3.button("🚗 Quản lý Xe", use_container_width=True):
        st.query_params["menu"] = "vehicles"
        st.rerun()
    if act4.button("🗺 Quản lý Khu vực", use_container_width=True):
        st.query_params["menu"] = "sites"
        st.rerun()

    st.divider()

    with st.spinner("Đang tải dữ liệu..."):
        stats = parking_controller.get_occupancy_stats(site_id=st.session_state.get('active_site_id'))

    if st.session_state.get('active_site_id'):
        st.caption("📍 Đang lọc theo địa điểm")

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
            capacity = s['total_slots'] or 1
            occupied = s['occupied_slots']
            percent = (occupied / capacity) * 100

            with st.container(border=True):
                st.markdown(f"#### {s['site_name']}")
                st.progress(min(percent / 100, 1.0), text=f"{occupied}/{s['total_slots']}")

                st.write(f"So luong: `{occupied}` / `{s['total_slots']}`")

    st.divider()

    # Quick View of Currently Parking Vehicles
    st.subheader("🚗 Xe đang trong bãi")
    history = parking_controller.get_parking_history(limit=10)
    currently_parking = [v for v in history if v['status'] == 'parking']

    if currently_parking:
        for v in currently_parking:
            entry_time = v.get('entry_time') or v.get('entered_at_utc')
            time_str = entry_time.strftime('%H:%M:%S %d/%m/%Y') if entry_time else 'N/A'
            with st.expander(f"🔢 {v['plate_norm']} - {v.get('site_name','Default')}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Vào lúc:** {time_str}")
                    st.write(f"**Camera vào:** {v.get('cam_in', 'N/A')}")
                with c2:
                    if v.get('entry_image'):
                        st.image(v['entry_image'], caption="Ảnh lúc vào", use_container_width=True)
    else:
        st.info("Hiện không có xe nào đang đỗ.")
