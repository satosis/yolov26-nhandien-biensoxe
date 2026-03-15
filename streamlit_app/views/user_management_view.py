import streamlit as st
import pandas as pd
import os
import time
from datetime import datetime
from streamlit_app.models.detector_model import get_db, get_password_hash # I need to make sure get_password_hash is available in the model

def strip_vn_accents(s):
    """Simple VN accent removal for OpenCV putText."""
    s = s.replace('à','a').replace('á','a').replace('ả','a').replace('ã','a').replace('ạ','a')
    s = s.replace('ă','a').replace('ằ','a').replace('ắ','a').replace('ẳ','a').replace('ẵ','a').replace('ặ','a')
    s = s.replace('â','a').replace('ầ','a').replace('ấ','a').replace('ẩ','a').replace('ẫ','a').replace('ậ','a')
    s = s.replace('è','e').replace('é','e').replace('ẻ','e').replace('ẽ','e').replace('ẹ','e')
    s = s.replace('ê','e').replace('ề','e').replace('ế','e').replace('ể','e').replace('ễ','e').replace('ệ','e')
    s = s.replace('ì','i').replace('í','i').replace('ỉ','i').replace('ĩ','i').replace('ị','i')
    s = s.replace('ò','o').replace('ó','o').replace('ỏ','o').replace('õ','o').replace('ọ','o')
    s = s.replace('ô','o').replace('ồ','o').replace('ố','o').replace('ổ','o').replace('ỗ','o').replace('ộ','o')
    s = s.replace('ơ','o').replace('ờ','o').replace('ớ','o').replace('ở','o').replace('ỡ','o').replace('ợ','o')
    s = s.replace('ù','u').replace('ú','u').replace('ủ','u').replace('ũ','u').replace('ụ','u')
    s = s.replace('ư','u').replace('ừ','u').replace('ứ','u').replace('ử','u').replace('ữ','u').replace('ự','u')
    s = s.replace('ỳ','y').replace('ý','y').replace('ỷ','y').replace('ỹ','y').replace('ỵ','y')
    s = s.replace('đ','d').replace('Đ','D')
    return s

def user_management_view():
    st.subheader("👥 Quản lý người dùng (Admin Only)")
    
    db = get_db()
    if not db:
        st.error("Database connection failed")
        return

    # Check for Edit Mode
    editing_id = st.session_state.get('editing_user_id')
    if editing_id:
        render_user_edit_view(db, editing_id)
        return

    # Tabs for different operations
    tab_list, tab_add = st.tabs(["📋 Danh sách người dùng", "➕ Thêm người dùng mới"])

    with tab_list:
        users = db.get_all_users()
        if users:
            # Custom Header
            h_cols = st.columns([0.5, 1.5, 3.5, 1.5, 1, 1])
            h_cols[0].markdown("**ID**")
            h_cols[1].markdown("**Username**")
            h_cols[2].markdown("**Họ và tên**")
            h_cols[3].markdown("**Role**")
            h_cols[4].markdown("**Sửa**")
            h_cols[5].markdown("**Xóa**")
            st.divider()

            for u in users:
                u_id = u['id']
                u_username = u['username']
                # [ID, Username, Full Name, Role, Sửa, Xóa]
                cols = st.columns([0.5, 1.5, 3.5, 1.5, 1, 1])
                cols[0].write(u_id)
                cols[1].write(u_username)
                cols[2].write(u.get('full_name') or "-")
                cols[3].write(u.get('role', 'N/A'))
                
                # Edit button - Sets state to "redirect"
                if cols[4].button("✏️", key=f"edit_btn_{u_id}", width='stretch'):
                    st.session_state['editing_user_id'] = u_id
                    st.rerun()
                
                # Delete button (skip for admin)
                if u_username != 'admin':
                    if cols[5].button("🗑️", key=f"del_btn_{u_id}", width='stretch', type="secondary"):
                        if db.delete_user(u_id):
                            st.warning(f"Đã xóa {u_username}")
                            st.rerun()
                        else:
                            st.error("Lỗi khi xóa")
                else:
                    cols[5].write("")
        else:
            st.info("Chưa có người dùng nào")

    with tab_add:
        st.markdown("### 🆕 Tạo tài khoản / Nhân sự mới")
        with st.form("add_user_form"):
            col1, col2 = st.columns(2)
            with col1:
                user_name = st.text_input("Username (Dùng để đăng nhập)", placeholder="VD: nguyenvan_a")
                user_pass = st.text_input("Password", type="password")
                roles = db.get_roles()
                role_names = [r['role_name'] for r in roles]
                user_role = st.selectbox("Role hệ thống", role_names, index=role_names.index('Security') if 'Security' in role_names else 0)
            
            with col2:
                full_name = st.text_input("Họ và tên (Nhân sự)", placeholder="VD: Nguyễn Văn A")
                note = st.text_area("Ghi chú", placeholder="VD: Nhân viên kho", height=100)
            
            zones = db.get_zones()
            zone_options = {z['id']: z['zone_name'] for z in zones}
            selected_zones = st.multiselect("Khu vực cho phép", options=list(zone_options.keys()), format_func=lambda x: zone_options[x])

            if st.form_submit_button("🚀 Tạo người dùng/nhân sự", width='stretch'):
                if user_name:
                    hashed = get_password_hash(user_pass) if user_pass else None
                    if db.create_user(user_name, hashed, user_role, full_name, note, selected_zones):
                        st.success(f"Đã tạo thành công {user_name}")
                        st.rerun()
                    else:
                        st.error("Lỗi: Username đã tồn tại hoặc có lỗi CSDL!")
                else:
                    st.warning("Vui lòng nhập ít nhất Username.")


def render_user_edit_view(db, u_id):
    users = db.get_all_users()
    user = next((u for u in users if u['id'] == u_id), None)
    
    if not user:
        st.error("Không tìm thấy người dùng")
        if st.button("⬅️ Quay lại danh sách"):
            st.session_state.pop('editing_user_id', None)
            st.rerun()
        return

    st.markdown(f"### ✏️ Chỉnh sửa người dùng: `{user['username']}`")
    
    tab_info, tab_face = st.tabs(["📝 Thông tin tài khoản", "📷 Nhận diện khuôn mặt (AI)"])
    
    with tab_info:
        with st.container(border=True):
            roles = db.get_roles()
            role_names = [r['role_name'] for r in roles]
            
            with st.form("edit_user_form_page"):
                col1, col2 = st.columns(2)
                with col1:
                    new_username = st.text_input("Username", value=user['username'])
                    current_role = user.get('role', 'User')
                    role_idx = role_names.index(current_role) if current_role in role_names else 0
                    new_role = st.selectbox("Role", role_names, index=role_idx)
                    new_pass = st.text_input("Mật khẩu mới (để trống nếu không đổi)", type="password")
                
                with col2:
                    new_full_name = st.text_input("Họ và tên", value=user.get('full_name') or "")
                    new_note = st.text_area("Ghi chú", value=user.get('note') or "", height=100)

                zones = db.get_zones()
                zone_options = {z['id']: z['zone_name'] for z in zones}
                current_zones = user.get('zone_ids') or []
                new_zones = st.multiselect("Khu vực cho phép", options=list(zone_options.keys()), 
                                           default=current_zones, format_func=lambda x: zone_options[x])
                
                if st.form_submit_button("💾 Lưu thay đổi", width='stretch'):
                    hashed = None
                    if new_pass:
                        hashed = get_password_hash(new_pass)
                    
                    if db.update_user(u_id, new_username, new_role, hashed, new_full_name, new_note, new_zones):
                        st.success(f"Đã cập nhật {new_username}")
                        st.session_state.pop('editing_user_id', None)
                        st.rerun()
                    else:
                        st.error("Lỗi khi cập nhật")

    with tab_face:

        face_dir = os.path.join(".", "config", "faces", str(u_id))
        os.makedirs(face_dir, exist_ok=True)

        # KYC configuration
        STAGES = [
            {"key": "front",  "label": "😐 Nhìn thẳng",          "yaw": (-.15, .15),  "pitch": (-.15, .20)},
            {"key": "left",   "label": "👈 Quay trái ~30°",       "yaw": (.20, .65),   "pitch": (-.20, .20)},
            {"key": "right",  "label": "👉 Quay phải ~30°",       "yaw": (-.65, -.20), "pitch": (-.20, .20)},
            {"key": "up",     "label": "☝️ Ngẩng lên",            "yaw": (-.20, .20),  "pitch": (-.65, -.15)},
            {"key": "down",   "label": "👇 Cúi xuống",            "yaw": (-.20, .20),  "pitch": (.15, .65)},
        ]

        def stage_done(s):
            p = os.path.join(face_dir, f"{s['key']}.jpg")
            return os.path.exists(p)

        # ============================================================
        # KYC DASHBOARD: Single Fragment for Instant Updates
        # ============================================================
        @st.fragment
        def render_face_registration_dashboard():
            # 1. Calculation
            done_stages = [s for s in STAGES if stage_done(s)]
            total_done = len(done_stages)
            next_stage = next((s for s in STAGES if not stage_done(s)), None)

            # 2. Progress
            st.progress(total_done / len(STAGES), text=f"Tiến độ: {total_done}/{len(STAGES)} góc mặt")

            # 3. Header & Camera Logic
            if next_stage is None:
                st.info("Hệ thống đã có đủ dữ liệu khuôn mặt của bạn. Bạn có thể xem lại ảnh ở phần Thư viện bên dưới.")
                st.balloons()
            else:
                st.markdown(
                    f"<div style='padding:12px;background:#1e3a5f;border-radius:8px;font-size:1.2rem;text-align:center;"
                    f"font-weight:bold;color:#fff;margin-bottom:12px;'>"
                    f"Bước {STAGES.index(next_stage)+1}/{len(STAGES)}: {next_stage['label']}</div>",
                    unsafe_allow_html=True
                )

                from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, WebRtcMode
                import av
                import cv2 as _cv2
                from streamlit_app.models.detector_model import load_face_app
                face_app = load_face_app()

                # Processor class inside for scope or outside? Inside is safer for fragments sometimes
                class RegistrationProcessor(VideoProcessorBase):
                    def __init__(self, face_dir, stages):
                        self.face_dir = face_dir
                        self.stages = stages
                        self.stable_count = 0
                        self.THRESHOLD = 12
                        self.captured = False
                        
                    def get_next_needed_stage(self):
                        for stage in self.stages:
                            if not os.path.exists(os.path.join(self.face_dir, f"{stage['key']}.jpg")):
                                return stage
                        return None

                    def recv(self, frame):
                        try:
                            img = frame.to_ndarray(format="bgr24")
                            annotated = img.copy()
                            
                            stage = self.get_next_needed_stage()
                            if not stage:
                                return av.VideoFrame.from_ndarray(annotated, format="bgr24")

                            # Use global model lock
                            with face_app.lock:
                                faces = face_app.get(img)

                            if faces:
                                faces = sorted(faces, key=lambda x: (x.bbox[2]-x.bbox[0])*(x.bbox[3]-x.bbox[1]), reverse=True)
                                f = faces[0]
                                bbox = f.bbox.astype(int)
                                kps = f.kps.astype(int)

                                # Pose check
                                re, le, nose, mouth_l, mouth_r = kps[0], kps[1], kps[2], kps[3], kps[4]
                                eye_dx = abs(le[0] - re[0]) + 1e-6
                                eye_dy = abs(mouth_l[1] + mouth_r[1]) / 2.0 - (re[1] + le[1]) / 2.0 + 1e-6
                                yaw = (nose[0] - (re[0] + le[0]) / 2.0) / eye_dx
                                pitch = (nose[1] - (re[1] + le[1]) / 2.0) / eye_dy - 0.4 

                                y_min, y_max = stage["yaw"]
                                p_min, p_max = stage["pitch"]
                                in_pose = (y_min <= yaw <= y_max) and (p_min <= pitch <= p_max)

                                color = (0, 255, 0) if in_pose else (0, 120, 255)
                                _cv2.rectangle(annotated, (bbox[0], bbox[1]), (bbox[2], bbox[3]), color, 2)
                                prog_w = int((self.stable_count / self.THRESHOLD) * (bbox[2]-bbox[0]))
                                _cv2.rectangle(annotated, (bbox[0], bbox[3]+10), (bbox[0]+prog_w, bbox[3]+20), (0, 255, 0), -1)
                                
                                if in_pose:
                                    self.stable_count += 1
                                    if self.stable_count >= self.THRESHOLD:
                                        _cv2.imwrite(os.path.join(self.face_dir, f"{stage['key']}.jpg"), img)
                                        self.stable_count = 0
                                        self.captured = True 
                                else:
                                    self.stable_count = 0
                            return av.VideoFrame.from_ndarray(annotated, format="bgr24")
                        except Exception:
                            return frame

                webrtc_ctx = webrtc_streamer(
                    key=f"face_reg_v3_{u_id}", 
                    mode=WebRtcMode.SENDRECV,
                    video_processor_factory=lambda: RegistrationProcessor(face_dir, STAGES),
                    async_processing=True,
                    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
                    media_stream_constraints={"video": True, "audio": False},
                )

                if webrtc_ctx.video_processor and webrtc_ctx.video_processor.captured:
                    webrtc_ctx.video_processor.captured = False
                    st.toast("✅ Đã ghi nhận tư thế!", icon="📸")
                    st.rerun(scope="fragment")

            st.divider()

            # 4. Gallery Section
            files = [f for f in os.listdir(face_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            if not files:
                st.warning("⚠️ Người dùng này chưa có dữ liệu khuôn mặt. Hệ thống AI chưa thể nhận diện họ.")
            else:
                st.markdown(f"#### 🖼️ Thư viện ảnh nhận diện (Tổng cộng: **{len(files)}** ảnh)")
                cols = st.columns(5)
                stage_labels = {s["key"]: s["label"] for s in STAGES}
                for i, fname in enumerate(files):
                    img_path = os.path.join(face_dir, fname)
                    stem = os.path.splitext(fname)[0]
                    label = stage_labels.get(stem, fname)
                    with cols[i % 5]:
                        with st.container(border=True):
                            st.image(img_path, width='stretch', caption=label)
                            if st.button("🗑️ Xóa", key=f"del_face_{fname}", width='stretch'):
                                try:
                                    os.remove(img_path)
                                    st.toast(f"Đã xóa {label}", icon="🗑️")
                                    st.rerun(scope="fragment")
                                except Exception as e:
                                    st.error(f"Lỗi: {e}")

        render_face_registration_dashboard()

