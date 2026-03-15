import streamlit as st
from streamlit_app.models.detector_model import get_db

def parking_logs_view():
    st.title("📋 Nhật ký Vào - Ra")
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    st.markdown("### 🔍 Tra cứu Đối soát")
    
    col1, col2 = st.columns([2, 1])
    search_plate = col1.text_input("Tìm theo biển số", placeholder="30F-123.45").strip().upper()
    status_filter = col2.selectbox("Trạng thái", ["Tất cả", "Đang đỗ", "Đã ra"])

    history = db.get_parking_history(limit=100)
    
    # Filter
    filtered = history
    if search_plate:
        filtered = [v for v in filtered if search_plate in v['plate_norm']]
    if status_filter == "Đang đỗ":
        filtered = [v for v in filtered if v['status'] == 'parking']
    elif status_filter == "Đã ra":
        filtered = [v for v in filtered if v['status'] == 'completed']

    if not filtered:
        st.info("Không tìm thấy dữ liệu đối soát nào.")
        return

    for v in filtered:
        with st.container(border=True):
            # Header row
            h1, h2, h3 = st.columns([1, 2, 1])
            status_color = "green" if v['status'] == 'completed' else "blue"
            h1.markdown(f"**🔢 {v['plate_norm']}**")
            h2.caption(f"📍 {v.get('zone_name','Default')} | 🎥 In: {v.get('cam_in','N/A')}")
            h3.markdown(f"<p style='text-align:right; color:{status_color}; font-weight:bold;'>{v['status'].upper()}</p>", unsafe_allow_html=True)
            
            # Content row
            c1, c2 = st.columns(2)
            with c1:
                st.write(f"⏱ **Vào:** {v['entered_at_utc'].strftime('%H:%M:%S %d/%m/%Y')}")
                if v.get('entry_image'):
                    st.image(v['entry_image'], caption="Ảnh lúc vào", width='stretch')
                else:
                    st.info("Không có ảnh vào")
            
            with c2:
                if v['status'] == 'completed':
                    st.write(f"⏱ **Ra:** {v['exited_at_utc'].strftime('%H:%M:%S %d/%m/%Y')}")
                    if v.get('exit_image'):
                        st.image(v['exit_image'], caption="Ảnh lúc ra", width='stretch')
                    else:
                        st.info("Không có ảnh ra")
                    
                    duration_min = round(v['stay_duration'] / 60)
                    st.warning(f"⏳ Thời gian: **{duration_min} phút** | 🪙 Phí: **{v['fee']:,} VND**")
                else:
                    st.info("Xe đang đỗ trong bãi...")
            
            st.divider()
