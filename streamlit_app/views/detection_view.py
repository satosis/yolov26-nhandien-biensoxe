import streamlit as st
import cv2
import numpy as np
import os
import time

def detection_view(controller, config):
    st.subheader("🔍 Nhận diện")
    sub_upload, sub_cam, sub_camera_view = st.tabs(["📁 Upload ảnh", "📷 Webcam / URL", "📹 Xem Camera"])
    
    with sub_upload:
        uploaded = st.file_uploader(
            "Chọn ảnh (JPG/PNG)",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
        )
    with sub_cam:
        c1, c2 = st.columns([3, 1])
        with c1:
            img_url = st.text_input("URL ảnh hoặc đường dẫn file")
        with c2:
            use_webcam = st.checkbox("Dùng webcam")

    # ========== TAB 3: XEM CAMERA ==========
    with sub_camera_view:
        from streamlit_app.models.detector_model import get_db
        db = get_db()
        if not db:
            st.error("Lỗi kết nối CSDL")
        else:
            zones = db.get_zones()
            active_cameras = db.get_active_cameras()
            
            if not active_cameras:
                st.info("Chưa có camera nào được kích hoạt. Vui lòng thêm camera trong mục **Quản lý Camera**.")
            else:
                # Build zone filter
                zone_names = list(set(c.get('zone_name') or 'Chưa phân khu' for c in active_cameras))
                zone_names.sort()
                
                col_zone, col_cam = st.columns([1, 2])
                with col_zone:
                    selected_zone = st.selectbox("🗺 Khu vực", ["Tất cả"] + zone_names, key="det_zone_filter")
                
                # Filter cameras by zone
                if selected_zone == "Tất cả":
                    filtered_cams = active_cameras
                else:
                    filtered_cams = [c for c in active_cameras if (c.get('zone_name') or 'Chưa phân khu') == selected_zone]
                
                with col_cam:
                    cam_labels = [f"{c['camera_name']} ({c['camera_type'].upper()})" for c in filtered_cams]
                    if cam_labels:
                        selected_cam_idx = st.selectbox("📹 Chọn Camera", range(len(cam_labels)), format_func=lambda i: cam_labels[i], key="det_cam_select")
                    else:
                        selected_cam_idx = None
                        st.warning("Không có camera trong khu vực này.")

                if selected_cam_idx is not None and filtered_cams:
                    selected_camera = filtered_cams[selected_cam_idx]
                    
                    st.markdown(f"**Camera:** {selected_camera['camera_name']} | **Loại:** {selected_camera['camera_type'].upper()} | **Khu vực:** {selected_camera.get('zone_name', 'N/A')}")
                    
                    rtsp_url = selected_camera.get('rtsp_url', '')
                    
                    if not rtsp_url:
                        st.warning("Camera này chưa có RTSP URL. Vui lòng cấu hình trong Quản lý Camera.")
                    else:
                        col_snap, col_auto = st.columns([1, 1])
                        with col_snap:
                            snap_btn = st.button("📸 Chụp & Nhận diện", type="primary", use_container_width=True, key="snap_detect")
                        with col_auto:
                            snap_only = st.button("👁 Chỉ xem (không nhận diện)", use_container_width=True, key="snap_only")
                        
                        if snap_btn or snap_only:
                            with st.spinner("Đang kết nối camera..."):
                                frame = capture_rtsp_frame(rtsp_url)
                            
                            if frame is not None:
                                if snap_only:
                                    st.image(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), 
                                             caption=f"Snapshot từ {selected_camera['camera_name']} — {frame.shape[1]}×{frame.shape[0]}px",
                                             use_container_width=True)
                                else:
                                    # Run AI detection
                                    st.session_state['camera_snapshot'] = frame
                                    st.session_state['camera_snapshot_name'] = selected_camera['camera_name']
                            else:
                                st.error(f"Không thể kết nối camera. Kiểm tra lại RTSP URL:\n`{rtsp_url}`")

    # ========== PROCESS IMAGES ==========
    images_to_process = []
    
    # From uploads
    if uploaded:
        for f in uploaded:
            arr = np.frombuffer(f.read(), np.uint8)
            im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if im is not None:
                images_to_process.append((f.name, im))
    
    # From URL
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
                images_to_process.append((os.path.basename(img_url), im))
            else:
                st.error("Không đọc được ảnh.")
        except Exception as e:
            st.error(f"Lỗi tải ảnh: {e}")
    
    # From webcam
    if use_webcam:
        cam_img = st.camera_input("Chụp ảnh từ webcam")
        if cam_img:
            arr = np.frombuffer(cam_img.read(), np.uint8)
            im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if im is not None:
                images_to_process.append(("webcam.jpg", im))

    # From camera snapshot (Tab 3)
    if 'camera_snapshot' in st.session_state and st.session_state.get('camera_snapshot') is not None:
        snap = st.session_state.pop('camera_snapshot')
        snap_name = st.session_state.pop('camera_snapshot_name', 'camera')
        images_to_process.append((f"{snap_name}.jpg", snap))

    if not images_to_process:
        st.info("Upload ảnh, nhập URL, hoặc chụp từ Camera để bắt đầu nhận diện.")
    else:
        for fname, img in images_to_process:
            st.subheader(f"📄 {fname}  —  {img.shape[1]}×{img.shape[0]}px")
            is_plate = "Biển số" in config['mode']
            if is_plate:
                annotated, detections, det_ms = controller.detect_plates(img, config['conf_thresh'], config['iou_thresh'])
            else:
                annotated, detections, det_ms = controller.detect_faces(img, config['known_dir'])

            col_orig, col_ann = st.columns(2)
            with col_orig:
                st.caption("Ảnh gốc")
                st.image(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), use_container_width=True)
            with col_ann:
                st.caption(f"Kết quả  ({det_ms:.0f} ms)")
                st.image(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB), use_container_width=True)

            if is_plate:
                # Separate detections
                vehicles = [d for d in detections if d.get('type') == 'vehicle']
                persons = [d for d in detections if d.get('type') == 'person']
                doors = [d for d in detections if d.get('type') == 'door']
                plates_found = [d for d in vehicles if d.get('plate_text')]
                
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("🚗 Phương tiện", len(vehicles))
                m2.metric("🧑 Người", len(persons))
                m3.metric("🔢 Biển số", len(plates_found))
                m4.metric("🚪 Cửa cuốn", len(doors))

                # Special summary for best plate and door status
                if plates_found or doors:
                    s1, s2 = st.columns(2)
                    if plates_found:
                        best = max(plates_found, key=lambda d: d.get("ocr_conf", 0))
                        s1.info(f"🔢 Biển số tốt nhất: **{best['plate_text']}**")
                    if doors:
                        status_list = [d['cls_name'] for d in doors]
                        status_str = ", ".join([s.replace('_', ' ').upper() for s in status_list])
                        s2.warning(f"🚪 Trạng thái cửa: **{status_str}**")

                # Show vehicle details with plates
                if vehicles:
                    st.markdown("**🚗 Chi tiết Phương tiện:**")
                    cols = st.columns(min(len(vehicles), 4))
                    for i, det in enumerate(vehicles):
                        with cols[i % len(cols)]:
                            crop = det.get("crop")
                            if crop is not None and crop.size > 0:
                                st.image(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB),
                                         caption=f"{det['cls_name'].upper()} #{i+1}", use_container_width=True)
                            if config.get('show_enhanced') and det.get("enhanced") is not None and det["enhanced"].size > 0:
                                st.image(cv2.cvtColor(det["enhanced"], cv2.COLOR_BGR2RGB),
                                         caption="Enhanced", use_container_width=True)
                            plate = det.get('plate_text', '')
                            if plate:
                                st.success(f"🔢 **`{plate}`**")
                                st.caption(f"det={det['det_conf']:.2f} | ocr={det['ocr_conf']:.2f}")
                            else:
                                st.warning("Không đọc được biển số")
                                st.caption(f"det={det['det_conf']:.2f}")
                
                # Show Door details
                if doors:
                    st.markdown("**🚪 Chi tiết Cửa cuốn:**")
                    d_cols = st.columns(min(len(doors), 4))
                    for i, det in enumerate(doors):
                        with d_cols[i % len(d_cols)]:
                            x1, y1, x2, y2 = det["bbox"]
                            crop = img[max(0,y1):y2, max(0,x1):x2]
                            if crop.size > 0:
                                st.image(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB),
                                         caption=f"Door #{i+1}", use_container_width=True)
                                status = det['cls_name'].replace('_', ' ').upper()
                                if 'OPEN' in status:
                                    st.error(f"🔓 **{status}**")
                                else:
                                    st.success(f"🔒 **{status}**")

                # Show persons
                if persons:
                    st.markdown("**🧑 Chi tiết Người:**")
                    p_cols = st.columns(min(len(persons), 6))
                    for i, det in enumerate(persons):
                        with p_cols[i % len(p_cols)]:
                            x1, y1, x2, y2 = det["bbox"]
                            crop = img[max(0,y1):y2, max(0,x1):x2]
                            if crop.size > 0:
                                st.image(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB),
                                         caption=f"Person #{i+1} ({det['det_conf']:.0%})", use_container_width=True)
            else:
                m1, m2 = st.columns(2)
                m1.metric("Khuôn mặt", len(detections))
                if detections:
                    best = max(detections, key=lambda d: d["similarity"])
                    m2.metric("Người nhận diện", best["name"])
                if detections:
                    cols = st.columns(min(len(detections), 4))
                    for i, det in enumerate(detections):
                        with cols[i % len(cols)]:
                            x1, y1, x2, y2 = det["bbox"]
                            fc = img[max(0,y1):y2, max(0,x1):x2]
                            if fc.size > 0:
                                st.image(cv2.cvtColor(fc, cv2.COLOR_BGR2RGB),
                                         caption=f"{det['name']} ({det['similarity']:.2f})",
                                         use_container_width=True)

            sc, _ = st.columns([1, 3])
            with sc:
                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if ok:
                    st.download_button("💾 Tải ảnh kết quả", data=buf.tobytes(),
                                       file_name=f"qa_{fname}", mime="image/jpeg",
                                       key=f"dl_{fname}")
            st.divider()


def capture_rtsp_frame(rtsp_url: str, timeout_sec: int = 10):
    """Capture a single frame from an RTSP stream using OpenCV."""
    try:
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_sec * 1000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_sec * 1000)
        
        if not cap.isOpened():
            return None
        
        ret, frame = cap.read()
        cap.release()
        
        if ret and frame is not None:
            return frame
        return None
    except Exception:
        return None
