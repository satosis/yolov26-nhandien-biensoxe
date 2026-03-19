import streamlit as st
from streamlit_app.models.detector_model import get_db
from streamlit_app.utils.toast import show_toast
import os


def ocr_review_view():
    st.title("🔤 OCR Active Learning")

    db = get_db()
    user = st.session_state.get('user', {})
    username = user.get('username', 'unknown')

    col1, col2 = st.columns([2, 1])
    status_filter = col1.selectbox("Lọc theo trạng thái", ["pending", "approved", "rejected", "Tất cả"])
    limit = col2.number_input("Số lượng", value=50, min_value=10, max_value=200)

    status_val = None if status_filter == "Tất cả" else status_filter
    samples = db.get_ocr_training_samples(status=status_val, limit=limit)

    if not samples:
        st.info("Không có mẫu OCR nào cần review.")
        return

    st.caption(f"Tìm thấy {len(samples)} mẫu (confidence 0.3-0.7)")

    for s in samples:
        with st.container(border=True):
            c_img, c_info, c_action = st.columns([1, 2, 1])

            with c_img:
                img_path = s.get('plate_image_path', '')
                if img_path and os.path.exists(img_path):
                    st.image(img_path, use_container_width=True)
                else:
                    st.caption("Ảnh không tồn tại")

            with c_info:
                st.markdown(f"OCR: **{s.get('ocr_text', 'N/A')}** (conf: {s.get('confidence', 0):.2f})")
                if s.get('corrected_text'):
                    st.markdown(f"Đã sửa: **{s['corrected_text']}**")
                st.caption(f"Camera: {s.get('camera_id', 'N/A')} | {s.get('created_at', '')}")

            with c_action:
                if s.get('status') == 'pending':
                    corrected = st.text_input("Sửa biển số", value=s.get('ocr_text', ''),
                                              key=f"ocr_fix_{s['id']}")
                    bc1, bc2 = st.columns(2)
                    if bc1.button("✅", key=f"ocr_ok_{s['id']}", use_container_width=True):
                        db.review_ocr_sample(s['id'], corrected.strip().upper(), username, "approved")
                        show_toast("Đã duyệt", type="success")
                        st.rerun()
                    if bc2.button("❌", key=f"ocr_rej_{s['id']}", use_container_width=True):
                        db.review_ocr_sample(s['id'], "", username, "rejected")
                        st.rerun()
                else:
                    badge_color = "#10b981" if s['status'] == 'approved' else "#ef4444"
                    st.markdown(f"<span style='color:{badge_color};font-weight:bold'>{s['status'].upper()}</span>",
                                unsafe_allow_html=True)
                    if s.get('reviewed_by'):
                        st.caption(f"by {s['reviewed_by']}")
