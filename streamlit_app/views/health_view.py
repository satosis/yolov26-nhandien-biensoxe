import streamlit as st
import pandas as pd

def health_view(db):
    st.subheader("❤️ Camera Health Monitor")
    if db is None:
        st.warning("Không kết nối được DB.")
        return

    h_hours = st.slider("Hiển thị sự kiện trong N giờ gần nhất", 1, 168, 24)
    events = db.get_camera_health(hours=h_hours)
    if events:
        df_h = pd.DataFrame(events)
        st.dataframe(df_h, use_container_width=True, hide_index=True)
    else:
        st.success(f"Không có sự kiện health trong {h_hours} giờ qua. ✅")

    st.divider()
    st.subheader("🔒 Legal Hold")
    hold_path = st.text_input("Đường dẫn file cần giữ lại")
    hold_reason = st.text_input("Lý do")
    hold_by = st.text_input("Người yêu cầu")
    c1, c2 = st.columns(2)
    if c1.button("🔒 Đặt Legal Hold"):
        if hold_path and hold_reason and hold_by:
            db.add_legal_hold(hold_path, hold_reason, hold_by)
            st.success("Đã đặt legal hold.")
    if c2.button("🔓 Giải phóng Hold"):
        if hold_path:
            db.release_legal_hold(hold_path)
            st.success("Đã giải phóng.")
