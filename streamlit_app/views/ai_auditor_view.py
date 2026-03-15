"""
AI Auditor View — Kiểm định biển số nhận diện bằng Gemini Vision
"""
import streamlit as st
import os
from streamlit_app.models.detector_model import get_db


def ai_auditor_view():
    st.subheader("🔎 AI Kiểm định biển số (Gemini)")
    st.caption("Hệ thống tự động lấy các biển số có độ tin cậy thấp và nhờ Gemini kiểm tra lại.")

    db = get_db()
    if not db:
        st.error("Database connection failed")
        return

    # ─────────────── API KEY CONFIG ───────────────
    with st.expander("⚙️ Cấu hình Gemini API Key", expanded=False):
        st.markdown(
            "Lấy API Key miễn phí tại "
            "[Google AI Studio](https://aistudio.google.com/) → **Get API Key**",
        )
        saved_key = db.get_app_setting("GEMINI_API_KEY") or ""
        masked = ("*" * (len(saved_key) - 4) + saved_key[-4:]) if len(saved_key) > 4 else ""
        if saved_key:
            st.success(f"✅ Đã lưu API Key: `{masked}`")

        new_key = st.text_input(
            "Gemini API Key mới (bỏ trống nếu không muốn thay đổi)",
            type="password",
            placeholder="AIzaSy...",
        )
        if st.button("💾 Lưu API Key", type="primary"):
            if new_key.strip():
                db.set_app_setting("GEMINI_API_KEY", new_key.strip())
                st.success("Đã lưu API Key!")
                st.rerun()
            else:
                st.warning("Vui lòng nhập API Key.")

    api_key = db.get_app_setting("GEMINI_API_KEY")

    st.divider()

    # ─────────────── FILTER OPTIONS ───────────────
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        n_items = st.number_input(
            "Số biển số cần kiểm tra", min_value=1, max_value=50, value=10
        )
    with col_b:
        max_conf = st.slider("Ngưỡng Confidence tối đa", 0.1, 0.95, 0.65, 0.05)
    with col_c:
        hours_back = st.selectbox(
            "Lấy trong vòng", [1, 6, 12, 24, 48, 72], index=3, format_func=lambda h: f"{h}h qua"
        )

    st.divider()

    # ─────────────── MAIN AUDIT BUTTON ───────────────
    run_col, _ = st.columns([1, 3])
    with run_col:
        run_audit = st.button(
            "▶️ Chạy Kiểm định",
            type="primary",
            width='stretch',
            disabled=(not api_key),
        )

    if not api_key:
        st.info("⬆️ Vui lòng nhập Gemini API Key ở phần cấu hình để bắt đầu.")

    # ─────────────── LOAD EVENTS ───────────────
    events = db.get_low_confidence_events(
        limit=int(n_items), max_conf=float(max_conf), hours_back=int(hours_back)
    )

    if not events:
        st.info(f"Không tìm thấy biển số nào có confidence ≤ {max_conf} trong {hours_back}h qua.")
        return

    st.markdown(f"**{len(events)} biển số** có độ tin cậy thấp nhất được tìm thấy:")

    # ─────────────── RUN AUDIT ───────────────
    if run_audit and api_key:
        try:
            from core.ai_auditor_service import AIAuditorService
            auditor = AIAuditorService(api_key)
        except ImportError as e:
            st.error(f"Lỗi khởi tạo AI Service: {e}. Hãy chạy `pip install google-generativeai`.")
            return

        progress = st.progress(0, text="Đang khởi động Gemini...")
        results_log = {}

        for i, ev in enumerate(events):
            ev_id = ev['id']
            plate = ev['plate_number']
            img_path = ev.get('image_path') or ''
            progress.progress((i + 1) / len(events), text=f"Đang kiểm tra {plate} ({i+1}/{len(events)})...")

            result = auditor.verify_plate(img_path, plate)
            results_log[ev_id] = result

            db.mark_event_audited(
                event_id=ev_id,
                gemini_result=result.get("gemini_result") or "ERROR",
                is_mismatch=result.get("is_mismatch", False),
            )

        progress.empty()
        st.success("✅ Kiểm định hoàn tất! Kết quả đã được lưu vào database.")
        # Reload fresh data
        events = db.get_low_confidence_events(
            limit=int(n_items), max_conf=float(max_conf), hours_back=int(hours_back)
        )

    # ─────────────── DISPLAY TABLE ───────────────
    mismatch_count = 0
    for ev in events:
        ev_id = ev['id']
        plate = ev['plate_number'] or "N/A"
        conf = ev.get('confidence')
        img_path = ev.get('image_path') or ''
        event_time = ev.get('event_time')
        camera = ev.get('camera_id') or 'N/A'
        gemini_result = ev.get('ai_audit_result')
        is_mismatch = ev.get('ai_audit_mismatch') or False

        if is_mismatch:
            mismatch_count += 1

        # Row
        with st.container(border=True):
            row_cols = st.columns([1, 1.5, 1.5, 1.5, 2])

            # Image
            with row_cols[0]:
                if img_path and os.path.exists(img_path):
                    st.image(img_path, width=120)
                else:
                    st.markdown("📷 *Không có ảnh*")

            # YOLO info
            with row_cols[1]:
                st.markdown(f"**YOLO đọc:** `{plate}`")
                conf_color = "🔴" if (conf or 0) < 0.4 else "🟡"
                st.markdown(f"{conf_color} Conf: `{conf:.2%}`" if conf else "Conf: N/A")
                st.caption(f"📷 {camera}")
                if event_time:
                    st.caption(f"🕐 {event_time.strftime('%d/%m %H:%M') if hasattr(event_time, 'strftime') else str(event_time)}")

            # Gemini result
            with row_cols[2]:
                if gemini_result:
                    st.markdown(f"**Gemini đọc:** `{gemini_result}`")
                else:
                    st.markdown("**Gemini:** *Chưa kiểm định*")

            # Status badge
            with row_cols[3]:
                if gemini_result is None:
                    st.markdown("⬜ **Chờ kiểm định**")
                elif is_mismatch:
                    st.error("⚠️ Nghi ngờ sai")
                else:
                    st.success("✅ Khớp")

            # Flag note
            with row_cols[4]:
                if is_mismatch and gemini_result:
                    st.warning(
                        f"🚨 Cần kiểm tra lại **{plate}**!\n\n"
                        f"YOLO: `{plate}` → Gemini: `{gemini_result}`"
                    )
                elif gemini_result and not is_mismatch:
                    st.info(f"Gemini xác nhận: `{gemini_result}`")

    st.divider()
    # Summary
    total_audited = sum(1 for ev in events if ev.get('ai_audit_result'))
    if total_audited:
        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric("Đã kiểm định", total_audited)
        col_s2.metric("⚠️ Nghi ngờ sai", mismatch_count, delta=f"-{mismatch_count}" if mismatch_count else None, delta_color="inverse")
        col_s3.metric("✅ Khớp", total_audited - mismatch_count)
