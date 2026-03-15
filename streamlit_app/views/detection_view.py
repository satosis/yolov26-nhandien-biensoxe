import streamlit as st
import cv2
import numpy as np
import os
import time
import re
import threading
import av
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, WebRtcMode
from streamlit_app.controllers.tracker import SimpleTracker
from streamlit_app.controllers.detection_controller import draw_label

def strip_vn_accents(s):
    """Simple VN accent removal for OpenCV putText."""
    if not s: return ""
    s = str(s)
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
    s = s.replace('ỳ','y').replace('ý','y').replace('ỷ','y').replace('ỵ','y')
    s = s.replace('đ','d').replace('Đ','D')
    return s

class DetectionProcessor(VideoProcessorBase):
    def __init__(self, controller, config, db, test_zone_id, t_zone_names):
        self.controller = controller
        self.config = config
        self.db = db
        self.test_zone_id = test_zone_id
        self.t_zone_names = t_zone_names
        self.tracker = SimpleTracker()
        self.webcam_ver = st.session_state.get('webcam_ver', 0)
        self._last_detections_p = []
        self._last_detections_f = []
        self.processing_interval = 2 # Process every 2nd frame
        
        # Threading for smooth video
        self._lock = threading.Lock()
        self._new_frame = None
        self._is_running = True
        self._ai_thread = threading.Thread(target=self._ai_worker, daemon=True)
        self._ai_thread.start()

    def _ai_worker(self):
        """Background thread for AI processing."""
        while self._is_running:
            frame_to_proc = None
            with self._lock:
                if self._new_frame is not None:
                    frame_to_proc = self._new_frame.copy()
                    self._new_frame = None
            
            if frame_to_proc is not None:
                try:
                    _, det_p, _ = self.controller.detect_plates(frame_to_proc, self.config)
                    _, det_f, _ = self.controller.detect_faces(frame_to_proc, self.config['known_dir'])
                    
                    with self._lock:
                        self._last_detections_p = det_p
                        self._last_detections_f = det_f
                    
                    # Log finding for sidebar/debug
                    if det_f and len(det_f) > 0:
                        top = det_f[0]
                        name = top.get('name', 'STRANGER')
                        fname = top.get('full_name', '???')
                        self.controller._last_log = f"Match: [{name}] {fname} ({top.get('similarity',0):.2f})"
                except Exception as e:
                    print(f"[ERROR] AI worker error: {str(e)}")
            
            time.sleep(0.01)

    def recv(self, frame):
        try:
            img = frame.to_ndarray(format="bgr24")
            h, w = img.shape[:2]
            
            # Pass frame to worker
            if self.tracker.frame_count % self.processing_interval == 0:
                with self._lock:
                    self._new_frame = img.copy()
            
            # Get results from worker
            with self._lock:
                to_track = []
                for d in self._last_detections_f:
                    det_item = d.copy()
                    det_item["type"] = "face"
                    to_track.append(det_item)
                det_p = self._last_detections_p.copy()
            
            # Update tracker
            tracks = self.tracker.update(to_track)
            canvas = img.copy()
            
            # 1. Stats Banner
            cv2.rectangle(canvas, (0, 0), (w, 40), (20, 20, 20), -1)
            fps = self.tracker.get_fps()
            stats_text = f"LIVE | FPS: {fps:.1f} | People: {len(to_track)} | ID: {self.webcam_ver}"
            cv2.putText(canvas, stats_text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # 2. Draw Tracks
            for tid, track in tracks.items():
                if track["lost"] > 0: continue
                x1, y1, x2, y2 = map(int, track["bbox"])
                
                d = track.get("data", {})
                name = str(d.get("name", ""))
                is_known = name and name not in ["STRANGER", "UNKNOWN", "None", ""]
                
                if is_known:
                    f_name = d.get('full_name') or "???"
                    u_name = d.get('username') or "???"
                    sim = d.get('similarity', 0.0)
                    
                    # Display format: [ID] Full Name
                    display_text = f"[{name}] {f_name}" if f_name != "???" else f"ID:{name}"
                    
                    label = f"{strip_vn_accents(display_text)} ({sim:.2f})"
                    color = (0, 255, 0)
                else:
                    sim = d.get('similarity', 0.0)
                    label = f"UNKNOWN ({sim:.2f})"
                    color = (0, 165, 255)
                
                # Use standard controller draw_label for consistent UI
                draw_label(canvas, x1, y1, label, color)
                # Small badge for tracker ID
                cv2.rectangle(canvas, (x1, y1), (x1+30, y1+20), color, -1)
                cv2.putText(canvas, str(tid), (x1+5, y1+15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 2)

            # 3. Plates
            for plate in det_p:
                px1, py1, px2, py2 = plate["bbox"]
                cv2.rectangle(canvas, (px1, py1), (px2, py2), (0, 255, 255), 2)
                if plate.get("plate_text"):
                    cv2.putText(canvas, plate["plate_text"], (px1, py1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            return av.VideoFrame.from_ndarray(canvas, format="bgr24")
        except Exception as e:
            err_img = frame.to_ndarray(format="bgr24")
            cv2.putText(err_img, f"UI ERROR: {str(e)}", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2)
            return av.VideoFrame.from_ndarray(err_img, format="bgr24")

def detection_view(controller, config):
    st.subheader("🔍 Nhận diện")
    
    from streamlit_app.models.detector_model import get_db
    db = get_db()
    if not db:
        st.error("Lỗi kết nối CSDL")
        return

    # Shared Zone Selection for Testing
    t_zones = db.get_zones()
    t_zone_names = {z['id']: z['zone_name'] for z in t_zones}
    
    st.markdown("""
        <style>
            .zone-badge {
                background: rgba(99, 102, 241, 0.1);
                color: #6366f1;
                padding: 2px 8px;
                border-radius: 12px;
                font-size: 0.8rem;
                font-weight: 600;
                border: 1px solid rgba(99, 102, 241, 0.2);
            }
            .auth-card {
                border-radius: 12px;
                padding: 20px;
                text-align: center;
                margin-bottom: 15px;
                border: 1px solid transparent;
            }
            .auth-true { background: rgba(34, 197, 94, 0.1); border-color: #22c55e; color: #22c55e; }
            .auth-false { background: rgba(239, 68, 68, 0.1); border-color: #ef4444; color: #ef4444; }
            .auth-unknown { background: rgba(245, 158, 11, 0.1); border-color: #f59e0b; color: #f59e0b; }
            .live-dot {
                height: 10px;
                width: 10px;
                background-color: #ef4444;
                border-radius: 50%;
                display: inline-block;
                margin-right: 5px;
                animation: blinker 1.5s linear infinite;
            }
            @keyframes blinker {
                50% { opacity: 0; }
            }
        </style>
    """, unsafe_allow_html=True)

    with st.expander("🎯 Cấu hình Khu vực thử nghiệm", expanded=False):
        test_zone_id = st.selectbox("Chọn khu vực để đối soát quyền truy cập:", 
                                   [None] + [z['id'] for z in t_zones],
                                   format_func=lambda x: t_zone_names.get(x, "Không xác định") if x else "Mặc định (Tất cả)",
                                   key="test_zone_select_view")

    sub_live, sub_upload, sub_cam, sub_camera_view = st.tabs([
        "🎥 Live Webcam", "📁 Upload ảnh", "📷 Snapshot Webcam", "📹 Xem Camera (RTSP)"
    ])
    
    images_to_process = []

    # ========== TAB 0: LIVE WEBCAM ==========
    with sub_live:
        st.markdown("<h5><span class='live-dot'></span> Chế độ Nhận diện Trực tiếp</h5>", unsafe_allow_html=True)
        
        if st.sidebar.button("🔄 Force Reload & Reset", width='stretch'):
            controller.clear_face_cache()
            st.session_state['webcam_ver'] = st.session_state.get('webcam_ver', 0) + 1
            st.rerun()

        with st.expander("🛠️ Diagnostics & Face Cache", expanded=False):
            status = controller.get_cache_status()
            st.write(f"**Thư mục:** `{os.path.abspath('./config/faces')}`")
            st.write(f"**Trạng thái:** {status['log']}")
            st.write(f"**ID đã tải:** `{', '.join(status['identities']) if status['identities'] else 'Không có'}`")
            if st.button("♻️ Làm mới Cache", key="refresh_cache_btn"):
                controller.clear_face_cache()
                st.rerun()

        webrtc_streamer(
            key=f"detection-webrtc-{st.session_state.get('webcam_ver', 0)}",
            mode=WebRtcMode.SENDRECV,
            video_processor_factory=lambda: DetectionProcessor(controller, config, db, test_zone_id, t_zone_names),
            rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
            media_stream_constraints={"video": True, "audio": False},
            async_processing=True,
        )
    
    # ========== TAB 1: UPLOAD ==========
    with sub_upload:
        uploaded = st.file_uploader(
            "Chọn ảnh (JPG/PNG)",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
        )
        if uploaded:
            for f in uploaded:
                arr = np.frombuffer(f.read(), np.uint8)
                im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if im is not None:
                    images_to_process.append({
                        "name": f.name, 
                        "img": im, 
                        "zone_id": test_zone_id, 
                        "zone_name": t_zone_names.get(test_zone_id)
                    })

    # ========== TAB 2: WEBCAM SNAPSHOT / URL ==========
    with sub_cam:
        col_inp, col_web = st.columns([1, 1])
        with col_inp:
            st.markdown("##### 🔗 Nhập URL / Đường dẫn")
            img_url = st.text_input("URL ảnh hoặc đường dẫn file locally", key="img_url_input")
            if img_url:
                try:
                    import urllib.request
                    if img_url.startswith("http"):
                        with urllib.request.urlopen(img_url, timeout=5) as resp:
                            arr = np.frombuffer(resp.read(), np.uint8)
                            im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    else:
                        im = cv2.imread(img_url)
                    if im is not None:
                        images_to_process.append({
                            "name": os.path.basename(img_url), 
                            "img": im, 
                            "zone_id": test_zone_id, 
                            "zone_name": t_zone_names.get(test_zone_id)
                        })
                except Exception as e:
                    st.error(f"Lỗi tải ảnh: {e}")
        
        with col_web:
            st.markdown("##### 📷 Chụp nhanh Webcam")
            cam_img = st.camera_input("Bấm chụp để phân tích chi tiết", key="webcam_capture")
            if cam_img:
                arr = np.frombuffer(cam_img.getvalue(), np.uint8)
                im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if im is not None:
                    images_to_process.append({
                        "name": "webcam_capture.jpg", 
                        "img": im, 
                        "zone_id": test_zone_id, 
                        "zone_name": t_zone_names.get(test_zone_id)
                    })

    # ========== TAB 3: XEM CAMERA (RTSP) ==========
    with sub_camera_view:
        active_cameras = db.get_active_cameras()
        if not active_cameras:
            st.info("Chưa có camera nào được kích hoạt.")
        else:
            zone_names = sorted(list(set(c.get('zone_name') or 'Chưa phân khu' for c in active_cameras)))
            col_zone, col_cam = st.columns([1, 2])
            with col_zone:
                selected_zone = st.selectbox("🗺 Lọc theo Khu vực", ["Tất cả"] + zone_names, key="det_zone_filter")
            
            filtered_cams = active_cameras if selected_zone == "Tất cả" else [c for c in active_cameras if (c.get('zone_name') or 'Chưa phân khu') == selected_zone]
            
            with col_cam:
                if filtered_cams:
                    selected_cam_idx = st.selectbox("📹 Chọn Camera", range(len(filtered_cams)), 
                                                   format_func=lambda i: f"[{filtered_cams[i].get('zone_name') or 'N/A'}] {filtered_cams[i]['camera_name']}", 
                                                   key="det_cam_select")
                    selected_camera = filtered_cams[selected_cam_idx]
                    rtsp_url = selected_camera.get('rtsp_url', '')
                    if rtsp_url:
                        if st.button("📸 Chụp từ Camera", type="primary", width='stretch'):
                            with st.spinner("Đang kết nối..."):
                                frame = capture_rtsp_frame(rtsp_url)
                            if frame is not None:
                                st.session_state['camera_snapshot'] = frame
                                st.session_state['camera_snapshot_name'] = selected_camera['camera_name']
                                st.session_state['camera_snapshot_zone_id'] = selected_camera.get('zone_id')
                                st.session_state['camera_snapshot_zone_name'] = selected_camera.get('zone_name')
                            else:
                                st.error("Lỗi kết nối camera.")

    # From camera snapshot (Tab 3)
    if 'camera_snapshot' in st.session_state and st.session_state.get('camera_snapshot') is not None:
        snap = st.session_state.pop('camera_snapshot')
        images_to_process.append({
            "name": f"{st.session_state.pop('camera_snapshot_name')}.jpg", 
            "img": snap, 
            "zone_id": st.session_state.pop('camera_snapshot_zone_id'), 
            "zone_name": st.session_state.pop('camera_snapshot_zone_name')
        })

    # ========== PROCESSING RESULTS ==========
    if images_to_process:
        for idx, item in enumerate(images_to_process):
            fname, img, z_id, z_name = item["name"], item["img"], item["zone_id"], item["zone_name"]
            st.divider()
            st.markdown(f"### 📄 Nhận diện chi tiết: **{fname}**")
            if z_name: st.markdown(f"📍 Khu vực: <span class='zone-badge'>{z_name}</span>", unsafe_allow_html=True)
            
            with st.spinner("Đang phân tích..."):
                a_p, d_p, t_p = controller.detect_plates(img, config)
                ann, d_f, t_f = controller.detect_faces(a_p, config['known_dir'])
            
            c_img, c_info = st.columns([3, 2])
            c_img.image(cv2.cvtColor(ann, cv2.COLOR_BGR2RGB), width='stretch')
            
            with c_info:
                # Results Cards (simplified from previous version but still premium)
                for p in [d for d in d_p if d.get('plate_text')]:
                    is_auth = db.is_plate_whitelisted(p['plate_text'], z_id)
                    st.markdown(f"<div class='auth-card {'auth-true' if is_auth else 'auth-false'}'><b>{'AUTHORIZED' if is_auth else 'UNAUTHORIZED'}</b><br><span style='font-size:1.5rem'>{p['plate_text']}</span></div>", unsafe_allow_html=True)
                for f in d_f:
                    is_auth = db.is_person_whitelisted(f['name'], z_id)
                    cls = "auth-true" if is_auth else ("auth-unknown" if f['name'] == "STRANGER" else "auth-false")
                    label = "AUTHORIZED" if is_auth else ("NGƯỜI LẠ" if f['name'] == "STRANGER" else "SAI KHU VỰC")
                    f_name = f.get('full_name', f['name'])
                    display_id = f"ID: {f['name']}" if f['name'] != "STRANGER" else "STRANGER"
                    
                    st.markdown(f"""
                        <div class="auth-card {cls}">
                            <div style="font-weight:bold; font-size:1.1rem;">{label}</div>
                            <h2 style="margin: 10px 0;">[{f['name']}] {f_name}</h2>
                            <div style="font-size:0.8rem; opacity:0.8;">Độ tương đồng: {f.get('similarity', 0):.2f}</div>
                        </div>
                    """, unsafe_allow_html=True)
            
            if st.button(f" Tải kết quả", key=f"dl_{idx}"):
                ok, b = cv2.imencode(".jpg", ann)
                if ok: st.download_button("Download", b.tobytes(), f"res_{fname}")

def capture_rtsp_frame(url: str):
    try:
        cap = cv2.VideoCapture(url)
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None
    except: return None
