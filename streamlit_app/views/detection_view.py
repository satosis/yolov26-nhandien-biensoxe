import streamlit as st
import cv2
import numpy as np
import os
import time
import threading
import av
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, WebRtcMode
from streamlit_app.controllers.tracker import SimpleTracker
from streamlit_app.controllers.detection_controller import draw_label
from streamlit_app.controllers.camera_controller import camera_controller
from util.drawing_utils import draw_text_premium
from streamlit_app.utils.webrtc_utils import get_ice_servers


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
    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.tracker = SimpleTracker()
        self._last_detections_p = []
        self._last_detections_f = []
        self.processing_interval = 2
        self._frame_count = 0
        
        self._lock = threading.Lock()
        self._new_frame = None
        self._is_running = True
        self._ai_thread = threading.Thread(target=self._ai_worker, daemon=True)
        self._ai_thread.start()

    def _ai_worker(self):
        while self._is_running:
            frame_to_proc = None
            with self._lock:
                if self._new_frame is not None:
                    frame_to_proc = self._new_frame.copy()
                    self._new_frame = None
            
            if frame_to_proc is not None:
                try:
                    _, det_p, _ = self.controller.detect_plates(frame_to_proc, self.config, draw=False)
                    _, det_f, _ = self.controller.detect_faces(frame_to_proc, self.config['known_dir'], draw=False)
                    with self._lock:
                        self._last_detections_p = det_p
                        self._last_detections_f = det_f
                except Exception as e:
                    print(f"[ERROR] AI worker: {e}")
            time.sleep(0.01)

    def recv(self, frame):
        try:
            img = frame.to_ndarray(format="bgr24")
            self._frame_count += 1
            
            if self._frame_count % self.processing_interval == 0:
                with self._lock:
                    self._new_frame = img.copy()
            
            with self._lock:
                to_track = []
                for d in self._last_detections_f:
                    det_item = d.copy()
                    det_item["type"] = "face"
                    to_track.append(det_item)
                det_p = self._last_detections_p.copy()
            
            tracks = self.tracker.update(to_track)
            canvas = img.copy()
            
            # Compact stats banner: people, vehicles, rolling doors
            n_people = len(to_track)
            n_vehicles = len([d for d in det_p if d.get('type') == 'vehicle'])
            n_doors = len([d for d in det_p if d.get('type') == 'door'])
            
            # Using clean text labels for maximum compatibility
            stats = f"People: {n_people} | Vehicle: {n_vehicles} | Door: {n_doors}"
            canvas = draw_text_premium(canvas, stats, (10, 8), font_size=16, color=(0, 255, 0), bgcolor=(20, 20, 20, 180))

            # Draw tracked persons
            for tid, track in tracks.items():
                if track["lost"] > 0:
                    continue
                x1, y1, x2, y2 = map(int, track["bbox"])
                d = track.get("data", {})
                confirmed = d.get("confirmed_identity")
                
                if confirmed:
                    f_name = confirmed.get('full_name') or confirmed.get('name')
                    sim = confirmed.get('similarity', 0.0)
                    label = f"{strip_vn_accents(f_name)} ({sim:.2f})"
                    color = (0, 255, 0)
                else:
                    sim = d.get('similarity', 0.0)
                    label = f"Undefined ({sim:.2f})" if sim > 0 else "person"
                    color = (0, 165, 255)
                
                draw_label(canvas, x1, y1, x2, y2, label, color)

            # Draw plates
            for plate in det_p:
                px1, py1, px2, py2 = plate["bbox"]
                if plate.get("plate_text"):
                    draw_label(canvas, px1, py1, px2, py2, plate["plate_text"], (0, 255, 255))
                else:
                    cv2.rectangle(canvas, (px1, py1), (px2, py2), (0, 255, 255), 2)

            return av.VideoFrame.from_ndarray(canvas, format="bgr24")
        except Exception as e:
            err_img = frame.to_ndarray(format="bgr24")
            cv2.putText(err_img, f"ERROR: {e}", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            return av.VideoFrame.from_ndarray(err_img, format="bgr24")


def _rtsp_detection_tab(controller, config):
    """RTSP camera stream with AI detection, grouped by site."""
    cameras = camera_controller.get_active_cameras()

    if not cameras:
        st.info("Chưa có camera nào được cấu hình. Vào trang Cameras để thêm.")
        return

    # Group cameras by first site name
    grouped = {}
    for cam in cameras:
        site_names = cam.get('site_names') or []
        sname = site_names[0] if site_names else 'Chưa gán địa điểm'
        grouped.setdefault(sname, []).append(cam)

    site_names_list = list(grouped.keys())
    if not site_names_list:
        st.info("Chưa có camera nào được cấu hình.")
        return
    sel_site = st.selectbox("Chọn địa điểm", site_names_list, key="rtsp_zone_sel")
    _raw = grouped.get(sel_site)
    site_cams = _raw if _raw is not None else []

    cam_options = {cam['id']: cam['camera_name'] for cam in site_cams}
    sel_cam_id = st.selectbox(
        "Chọn camera", options=list(cam_options.keys()),
        format_func=lambda x: cam_options[x], key="rtsp_cam_sel"
    )

    sel_cam = next((c for c in site_cams if c['id'] == sel_cam_id), None)
    if not sel_cam or not sel_cam.get('rtsp_url'):
        st.warning("Camera chưa có RTSP URL.")
        return

    # Controls
    c1, c2 = st.columns([1, 1])
    run_key = "rtsp_running"
    if c1.button("Bắt đầu stream", type="primary", disabled=st.session_state.get(run_key, False)):
        st.session_state[run_key] = True
        st.session_state.pop('rtsp_stop', None)
        st.rerun()
    if c2.button("Dừng", disabled=not st.session_state.get(run_key, False)):
        st.session_state[run_key] = False
        st.session_state['rtsp_stop'] = True
        st.rerun()

    if not st.session_state.get(run_key, False):
        st.info(f"Nhấn 'Bắt đầu stream' để xem camera {sel_cam['camera_name']}")
        return

    # Stream loop
    frame_holder = st.empty()
    stats_holder = st.empty()
    tracker = SimpleTracker()

    cap = cv2.VideoCapture(sel_cam['rtsp_url'], cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        st.error(f"Không thể kết nối camera: {sel_cam['camera_name']}")
        st.session_state[run_key] = False
        return

    frame_count = 0
    last_det_p, last_det_f = [], []

    try:
        while st.session_state.get(run_key, False) and not st.session_state.get('rtsp_stop', False):
            ret, frame = cap.read()
            if not ret:
                cap.release()
                time.sleep(1)
                cap = cv2.VideoCapture(sel_cam['rtsp_url'], cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                continue

            frame_count += 1

            # Run AI every 3 frames
            if frame_count % 3 == 0:
                try:
                    _, last_det_p, _ = controller.detect_plates(frame, config, draw=False)
                    _, last_det_f, _ = controller.detect_faces(frame, config['known_dir'], draw=False)
                except Exception:
                    pass

            # Draw detections on frame
            canvas = frame.copy()

            # Stats banner
            n_people = len(last_det_f)
            n_vehicles = len([d for d in last_det_p if d.get('type') == 'vehicle'])
            stats_text = f"Nguoi: {n_people} | Xe: {n_vehicles}"
            canvas = draw_text_premium(canvas, stats_text, (10, 8), font_size=16,
                                       color=(0, 255, 0), bgcolor=(20, 20, 20, 180))

            # Track faces
            to_track = [dict(d, type="face") for d in last_det_f]
            tracks = tracker.update(to_track)
            for tid, track in tracks.items():
                if track["lost"] > 0:
                    continue
                x1, y1, x2, y2 = map(int, track["bbox"])
                d = track.get("data", {})
                confirmed = d.get("confirmed_identity")
                if confirmed:
                    f_name = confirmed.get('full_name') or confirmed.get('name')
                    sim = confirmed.get('similarity', 0.0)
                    label = f"{strip_vn_accents(f_name)} ({sim:.2f})"
                    color = (0, 255, 0)
                else:
                    sim = d.get('similarity', 0.0)
                    label = f"Undefined ({sim:.2f})" if sim > 0 else "person"
                    color = (0, 165, 255)
                draw_label(canvas, x1, y1, x2, y2, label, color)

            # Draw plates
            for plate in last_det_p:
                px1, py1, px2, py2 = plate["bbox"]
                if plate.get("plate_text"):
                    draw_label(canvas, px1, py1, px2, py2, plate["plate_text"], (0, 255, 255))
                else:
                    cv2.rectangle(canvas, (px1, py1), (px2, py2), (0, 255, 255), 2)

            frame_holder.image(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB), use_container_width=True)
            time.sleep(0.03)
    finally:
        cap.release()
        st.session_state[run_key] = False


def detection_view(controller, config):
    """Simplified detection view — live webcam + upload + RTSP camera."""
    st.subheader("🔍 Nhận diện")

    is_mobile = st.query_params.get("mobile") == "1"
    if is_mobile:
        st.info("📱 Chế độ mobile")
        tab_live, tab_upload = st.tabs(["🎥 Live Camera", "📁 Upload ảnh"])
        tab_rtsp = None
    else:
        tab_live, tab_rtsp, tab_upload = st.tabs(["🎥 Live Webcam", "📹 Camera khu vực", "📁 Upload ảnh"])

    # ========== TAB 1: LIVE WEBCAM ==========
    with tab_live:
        st.markdown("<h5><span class='live-dot'></span> Nhận diện trực tiếp</h5>", unsafe_allow_html=True)

        # Camera source selector
        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown("**Chọn nguồn camera:**")
        with col2:
            camera_source = st.selectbox(
                "Nguồn camera",
                options=["default", "iphone", "environment"],
                format_func=lambda x: {
                    "default": "💻 Webcam máy tính",
                    "iphone": "📱 Camera trước (selfie)",
                    "environment": "📷 Camera sau (chính)",
                }.get(x, x),
                key="webcam_source_sel",
                label_visibility="collapsed"
            )

        # Build media constraints based on selected source
        _constraints_map = {
            "default": {"video": True, "audio": False},
            "iphone": {
                "video": {"facingMode": "user", "width": {"ideal": 1280}, "height": {"ideal": 720}},
                "audio": False,
            },
            "environment": {
                "video": {"facingMode": "environment", "width": {"ideal": 1280}, "height": {"ideal": 720}},
                "audio": False,
            },
        }
        media_constraints = _constraints_map.get(camera_source, {"video": True, "audio": False})

        webrtc_streamer(
            key=f"det-webrtc-v2-{st.session_state.get('webcam_ver', 0)}-{camera_source}",
            mode=WebRtcMode.SENDRECV,
            video_processor_factory=lambda: DetectionProcessor(controller, config),
            rtc_configuration={"iceServers": get_ice_servers()},
            media_stream_constraints=media_constraints,
            async_processing=True,
        )

    # ========== TAB 2: CAMERA KHU VỰC (RTSP) — desktop only ==========
    if tab_rtsp is not None:
        with tab_rtsp:
            _rtsp_detection_tab(controller, config)

    # ========== TAB 3: UPLOAD ==========
    with tab_upload:
        uploaded = st.file_uploader("Chọn ảnh (JPG/PNG)", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
        if uploaded:
            for f in uploaded:
                arr = np.frombuffer(f.read(), np.uint8)
                im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if im is not None:
                    st.divider()
                    st.markdown(f"### 📄 {f.name}")
                    with st.spinner("Đang phân tích..."):
                        a_p, d_p, _ = controller.detect_plates(im, config)
                        ann, d_f, _ = controller.detect_faces(a_p, config['known_dir'])
                    st.image(cv2.cvtColor(ann, cv2.COLOR_BGR2RGB), use_container_width=True)
                    
                    # Summary stats
                    col1, col2 = st.columns(2)
                    col1.metric("🚗 Biển số", len([d for d in d_p if d.get('plate_text')]))
                    col2.metric("👤 Khuôn mặt", len(d_f))
