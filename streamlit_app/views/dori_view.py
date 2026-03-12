import streamlit as st

def dori_view(controller):
    st.subheader("📐 DORI Distance Calculator (IEC 62676-4)")
    st.caption("Tính khoảng cách tối đa cho từng mức nhận diện theo tiêu chuẩn công nghiệp.")

    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        res_w = dc1.number_input("Độ phân giải ngang (px)", min_value=320, max_value=7680, value=1920, step=1)
        res_h = dc1.number_input("Độ phân giải dọc (px)", min_value=240, max_value=4320, value=1080, step=1)
    with dc2:
        scene_w = dc2.number_input("Chiều rộng vùng giám sát (m)", min_value=0.5, max_value=50.0, value=3.0, step=0.5)
        hfov = dc2.number_input("Góc nhìn ngang — HFoV (°)", min_value=10.0, max_value=180.0, value=90.0, step=1.0)
    with dc3:
        mount_h = dc3.number_input("Chiều cao gắn camera (m)", min_value=1.0, max_value=10.0, value=3.0, step=0.5)
        target_h = dc3.number_input("Chiều cao đối tượng (m)", min_value=0.3, max_value=3.0, value=1.8, step=0.1)

    st.divider()
    st.markdown("**Kết quả khoảng cách tối đa:**")
    df_dori = controller.calculate_dori(res_w, res_h, scene_w, hfov, mount_h, target_h)
    st.dataframe(df_dori, use_container_width=True, hide_index=True)
    import altair as alt
    _order = ["Detection", "Observation", "Recognition", "Identification"]
    chart = (
        alt.Chart(df_dori)
        .mark_bar(color="#00e676")
        .encode(
            x=alt.X("Mức DORI:O", sort=_order, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Khoảng cách tối đa (m):Q"),
            tooltip=["Mức DORI", "Khoảng cách tối đa (m)", "PPM yêu cầu"],
        )
        .properties(width=600, height=220)
    )
    st.altair_chart(chart, use_container_width=False)

    st.divider()
    st.subheader("🏷 Đặt tên camera theo chuẩn công nghiệp")
    nc1, nc2, nc3, nc4, nc5, nc6 = st.columns(6)
    region = nc1.text_input("Region", "VN").upper()
    site = nc2.text_input("Site", "HCM01").upper()
    building = nc3.text_input("Building", "PARKING").upper()
    zone = nc4.text_input("Zone", "EXT").upper()
    cam_num = nc5.number_input("Số thứ tự", 1, 999, 1)
    desc = nc6.text_input("Mô tả", "ENTRANCE_GATE")
    
    cam_name = controller.generate_camera_name(region, site, building, zone, cam_num, desc)
    st.code(cam_name, language=None)
