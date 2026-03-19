import streamlit as st
from streamlit_app.controllers.pending_plate_controller import pending_plate_controller
from streamlit_app.utils.toast import show_toast


def pending_plates_view():
    st.title("🚘 Xe lạ (Biển số chờ duyệt)")

    pending_msg = st.session_state.pop("pending_toast", None)
    if pending_msg:
        show_toast(pending_msg, type="success")

    user = st.session_state.get('user', {})
    username = user.get('username', 'unknown')

    plates = pending_plate_controller.get_pending()

    if not plates:
        st.info("Không có biển số lạ nào đang chờ duyệt.")
        return

    st.caption(f"{len(plates)} biển số chờ duyệt")

    for p in plates:
        plate_norm = p[0] if isinstance(p, (list, tuple)) else p.get('plate_norm', '')
        plate_raw = p[1] if isinstance(p, (list, tuple)) else p.get('plate_raw', '')
        first_seen = p[2] if isinstance(p, (list, tuple)) else p.get('first_seen_utc', '')

        with st.container(border=True):
            c_info, c_action = st.columns([3, 2])

            with c_info:
                st.markdown(f"### {plate_norm}")
                if plate_raw and plate_raw != plate_norm:
                    st.caption(f"Biển gốc: {plate_raw}")
                st.caption(f"Phát hiện lúc: {first_seen}")

            with c_action:
                bc1, bc2 = st.columns(2)
                if bc1.button("✅ Duyệt", key=f"approve_{plate_norm}", use_container_width=True, type="primary"):
                    pending_plate_controller.approve(plate_norm, username)
                    st.session_state["pending_toast"] = f"Đã duyệt biển số {plate_norm} vào whitelist"
                    st.rerun()
                if bc2.button("❌ Từ chối", key=f"reject_{plate_norm}", use_container_width=True):
                    pending_plate_controller.reject(plate_norm, username)
                    st.session_state["pending_toast"] = f"Đã từ chối biển số {plate_norm}"
                    st.rerun()
