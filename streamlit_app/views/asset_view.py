import streamlit as st
import pandas as pd
from datetime import datetime

def asset_view(registry):
    st.subheader("🗂 Asset Registry — CMDB Camera")
    if registry is None:
        st.warning("Asset Registry chưa khởi tạo.")
        return

    assets = registry.get_all()
    if assets:
        df_assets = pd.DataFrame(assets)
        st.dataframe(df_assets, use_container_width=True, hide_index=True)
        csv = df_assets.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Export CSV", data=csv, file_name="camera_assets.csv", mime="text/csv")
    else:
        st.info("Chưa có camera nào trong registry.")

    st.divider()
    st.subheader("➕ Thêm / Cập nhật camera")
    with st.form("asset_form"):
        af1, af2 = st.columns(2)
        a_id = af1.text_input("Camera ID *")
        a_name = af2.text_input("Tên hiển thị *")
        af3, af4 = st.columns(2)
        a_ip = af3.text_input("IP Address")
        a_mac = af4.text_input("MAC Address")
        af5, af6 = st.columns(2)
        a_model = af5.text_input("Model")
        a_rtsp = af6.text_input("RTSP URL")
        a_notes = st.text_area("Ghi chú")
        submitted = st.form_submit_button("💾 Lưu")
        if submitted:
            if not a_id or not a_name:
                st.error("Camera ID và Tên là bắt buộc.")
            else:
                registry.upsert({
                    "cam_id": a_id.strip(), "name": a_name.strip(),
                    "ip": a_ip, "mac": a_mac, "model": a_model,
                    "rtsp_url": a_rtsp, "notes": a_notes,
                    "install_date": datetime.utcnow().date().isoformat(),
                })
                st.success(f"Đã lưu camera: {a_id}")
                st.rerun()
