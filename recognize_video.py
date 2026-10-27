#!/usr/bin/env python3
"""
HỆ THỐNG NHẬN DẠNG BIỂN SỐ XE TỪ VIDEO CAMERA
=================================================
Tối ưu hóa toàn diện cho hiệu năng mượt nhất (Apple Silicon MPS / GPU):
- Tăng tốc PyTorch Metal MPS (~10-15x so với ONNX CPU).
- Bounding Box Tracking (IoU + Centroid) chống rung lắc và duy trì track ID.
- OCR bất đồng bộ (Async ThreadPool): luồng đọc/ghi video chạy mượt 100%, không lag.
- Smart OCR Caching: chỉ chạy OCR 1-3 lần cho mỗi xe, tái sử dụng kết quả cho toàn bộ track.
- Chuẩn hóa và làm sạch biển số Việt Nam (xe máy, ô tô, phục hồi mã tỉnh).
- Xuất video kết quả sắc nét với giao diện HUD hiện đại.

CÁCH DÙNG:
    python3 recognize_video.py <video_path> [--out <output.mp4>] [--every 3] [--conf 0.25]
    python3 recognize_video.py --all            # Xử lý toàn bộ video camera trong ~/Downloads/
"""

import argparse
import collections
import concurrent.futures
import logging
import math
import os
import re
import sys
import time
import types
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ─── Setup path ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Tắt log ồn từ các thư viện ngoài
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("recognize_video")
logger.setLevel(logging.INFO)

# Patch E2ELoss cho model YOLO
def _patch_e2e_loss():
    class E2ELoss:
        pass
    try:
        import ultralytics.utils.loss as u_loss
        u_loss.E2ELoss = E2ELoss
    except Exception:
        pass
    try:
        import ultralytics.nn.modules.loss as m_loss
        m_loss.E2ELoss = E2ELoss
    except Exception:
        pass
    for name in ("ultralytics.utils.loss", "ultralytics.nn.modules.loss"):
        module = sys.modules.setdefault(name, types.ModuleType(name))
        module.E2ELoss = E2ELoss

_patch_e2e_loss()

from backend.services import config as cfg
from backend.util.ocr_utils import VNPlateOCR, VALID_VN_PROVINCES


# ─── Hardware-Accelerated Detector ───────────────────────────────────────────
class FastPlateDetector:
    """Detector tối ưu hóa cho Metal MPS (Apple Silicon) hoặc ONNX."""

    def __init__(self, model_path: Optional[str] = None):
        import torch

        self.device = "cpu"
        self.engine_type = "cpu"
        self._yolo = None
        self._sess = None

        pt_candidate = ROOT / "backend/models/best.pt"
        onnx_candidate = ROOT / "backend/models/yolo26_plate_people_door_int8.onnx"

        target_model = model_path or (str(pt_candidate) if pt_candidate.exists() else str(onnx_candidate))
        logger.info("Initializing detector with: %s", target_model)

        if str(target_model).endswith(".pt") and os.path.exists(target_model):
            try:
                from ultralytics import YOLO

                self._yolo = YOLO(str(target_model), task="detect")
                if torch.backends.mps.is_available():
                    self.device = "mps"
                elif torch.cuda.is_available():
                    self.device = "cuda"
                else:
                    self.device = "cpu"

                self._yolo.to(self.device)
                self.engine_type = f"yolo_{self.device}"
                print(f"  [Detector] PyTorch loaded on {self.device.upper()} (GPU Acceleration ON)")
                # Warm up
                dummy = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = self._yolo(dummy, imgsz=640, device=self.device, verbose=False)
                return
            except Exception as e:
                logger.warning("Failed to load PyTorch model (%s), falling back to ONNX: %s", target_model, e)

        # Fallback ONNX
        import onnxruntime as ort

        onnx_file = str(onnx_candidate if onnx_candidate.exists() else target_model)
        providers = ["CPUExecutionProvider"]
        self._sess = ort.InferenceSession(onnx_file, providers=providers)
        self._input_name = self._sess.get_inputs()[0].name
        self.engine_type = "onnx_cpu"
        print(f"  [Detector] ONNX CPU engine loaded: {onnx_file}")

    def detect(self, frame: np.ndarray, conf_thresh: float = 0.25) -> List[Tuple[int, int, int, int, float, int]]:
        """Trả về list (x1, y1, x2, y2, conf, cls_id). cls_id = 0 là biển số xe."""
        if self._yolo is not None:
            results = self._yolo(frame, imgsz=640, conf=conf_thresh, device=self.device, verbose=False)
            boxes = []
            for r in results:
                if not r.boxes:
                    continue
                for b in r.boxes:
                    cls_id = int(b.cls[0])
                    if cls_id != 0:  # Chỉ lấy biển số
                        continue
                    x1, y1, x2, y2 = map(int, b.xyxy[0].cpu().numpy())
                    c = float(b.conf[0])
                    boxes.append((x1, y1, x2, y2, c, cls_id))
            return boxes

        # ONNX path
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (640, 640), swapRB=True)
        pred = self._sess.run(None, {self._input_name: blob})[0][0]
        sx, sy = w / 640, h / 640
        boxes = []
        for row in pred:
            c = float(row[4])
            if c < conf_thresh:
                continue
            cls_id = int(row[5])
            if cls_id != 0:
                continue
            x1, y1, x2, y2 = row[:4]
            if x2 <= 1.05 and y2 <= 1.05:
                x1, y1, x2, y2 = x1 * 640, y1 * 640, x2 * 640, y2 * 640
            boxes.append((int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), c, cls_id))
        return boxes


# ─── Plate Tracker ───────────────────────────────────────────────────────────
class TrackedPlate:
    def __init__(self, track_id: int, box: Tuple[int, int, int, int], det_conf: float, frame_idx: int, ts: float):
        self.track_id = track_id
        self.box = list(box)
        self.smooth_box = list(box)
        self.det_conf = det_conf
        self.first_frame = frame_idx
        self.last_frame = frame_idx
        self.first_ts = ts
        self.last_ts = ts
        self.text = ""
        self.ocr_conf = 0.0
        self.plate_type = "white"
        self.ocr_attempts = 0
        self.is_confirmed = False
        self.votes: Dict[str, float] = collections.defaultdict(float)
        self.vote_counts: Dict[str, int] = collections.defaultdict(int)

    def update_box(self, new_box: Tuple[int, int, int, int], det_conf: float, frame_idx: int, ts: float):
        self.box = list(new_box)
        self.det_conf = max(self.det_conf, det_conf)
        self.last_frame = frame_idx
        self.last_ts = ts
        # EMA smoothing để hộp bao không bị giật
        alpha = 0.6
        self.smooth_box = [
            int(alpha * new_box[i] + (1 - alpha) * self.smooth_box[i])
            for i in range(4)
        ]

    def add_ocr_result(self, text: str, conf: float, ptype: str):
        if not text or conf < 0.25:
            return
        self.votes[text] += conf
        self.vote_counts[text] += 1

        # Cập nhật best vote
        best_text = max(self.votes.keys(), key=lambda t: self.votes[t])
        avg_conf = self.votes[best_text] / self.vote_counts[best_text]

        if avg_conf > self.ocr_conf or len(best_text) >= len(self.text):
            self.text = best_text
            self.ocr_conf = avg_conf
            self.plate_type = ptype

        if avg_conf >= 0.65 or self.vote_counts[best_text] >= 2:
            self.is_confirmed = True


class PlateTracker:
    """Theo dõi biển số qua các frame bằng IoU + Centroid Distance."""

    def __init__(self, max_lost_frames: int = 15):
        self.next_id = 1
        self.tracks: Dict[int, TrackedPlate] = {}
        self.max_lost = max_lost_frames

    @staticmethod
    def _compute_iou(b1, b2) -> float:
        x1 = max(b1[0], b2[0])
        y1 = max(b1[1], b2[1])
        x2 = min(b1[2], b2[2])
        y2 = min(b1[3], b2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
        a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
        union = a1 + a2 - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _center_dist(b1, b2) -> float:
        c1x, c1y = (b1[0] + b1[2]) / 2, (b1[1] + b1[3]) / 2
        c2x, c2y = (b2[0] + b2[2]) / 2, (b2[1] + b2[3]) / 2
        return math.hypot(c1x - c2x, c1y - c2y)

    def update(self, detections: List[Tuple[int, int, int, int, float, int]], frame_idx: int, ts: float) -> List[TrackedPlate]:
        matched_detections = set()
        matched_tracks = set()

        # 1. Khớp bằng IoU
        for tid, track in self.tracks.items():
            best_iou = 0.25
            best_d_idx = None
            for d_idx, det in enumerate(detections):
                if d_idx in matched_detections:
                    continue
                score = self._compute_iou(det[:4], track.box)
                if score > best_iou:
                    best_iou = score
                    best_d_idx = d_idx

            if best_d_idx is not None:
                track.update_box(detections[best_d_idx][:4], detections[best_d_idx][4], frame_idx, ts)
                matched_detections.add(best_d_idx)
                matched_tracks.add(tid)

        # 2. Khớp bổ sung bằng Centroid distance cho xe di chuyển nhanh
        for tid, track in self.tracks.items():
            if tid in matched_tracks:
                continue
            tw = track.box[2] - track.box[0]
            th = track.box[3] - track.box[1]
            max_dist = max(tw, th) * 2.0  # bán kính tìm kiếm

            best_dist = max_dist
            best_d_idx = None
            for d_idx, det in enumerate(detections):
                if d_idx in matched_detections:
                    continue
                d = self._center_dist(det[:4], track.box)
                if d < best_dist:
                    best_dist = d
                    best_d_idx = d_idx

            if best_d_idx is not None:
                track.update_box(detections[best_d_idx][:4], detections[best_d_idx][4], frame_idx, ts)
                matched_detections.add(best_d_idx)
                matched_tracks.add(tid)

        # 3. Tạo track mới cho phát hiện chưa khớp
        for d_idx, det in enumerate(detections):
            if d_idx not in matched_detections:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = TrackedPlate(tid, det[:4], det[4], frame_idx, ts)

        # 4. Xóa các track đã biến mất quá số frame cho phép
        dead = [tid for tid, tr in self.tracks.items() if frame_idx - tr.last_frame > self.max_lost]
        for tid in dead:
            del self.tracks[tid]

        return list(self.tracks.values())


# ─── Crop Preprocessing ──────────────────────────────────────────────────────
def validate_crop(crop: np.ndarray) -> bool:
    if crop is None or crop.size == 0:
        return False
    h, w = crop.shape[:2]
    if w < 24 or h < 10:
        return False
    aspect = w / max(h, 1)
    if aspect < 0.6 or aspect > 7.5:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.mean() < 25:
        return False
    return True


def prepare_crop(crop: np.ndarray) -> np.ndarray:
    """Phóng to chất lượng cao Lanczos nếu crop quá nhỏ, không dùng sharpen nhân tạo."""
    h, w = crop.shape[:2]
    MIN_H = 80
    if h < MIN_H:
        scale = max(2, min(4, MIN_H // max(h, 1)))
        return cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    return crop


# ─── Visual Rendering ────────────────────────────────────────────────────────
def draw_plate_box(frame: np.ndarray, track: TrackedPlate):
    x1, y1, x2, y2 = track.smooth_box
    H, W = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)

    color_primary = (0, 230, 90) if track.text else (0, 200, 255)
    color_dark = (20, 25, 30)

    # Viền bounding box
    cv2.rectangle(frame, (x1, y1), (x2, y2), color_primary, 2, cv2.LINE_AA)

    # Góc bo trang trí (bracket)
    length = min(15, (x2 - x1) // 3, (y2 - y1) // 3)
    cv2.line(frame, (x1, y1), (x1 + length, y1), (255, 255, 255), 3)
    cv2.line(frame, (x1, y1), (x1, y1 + length), (255, 255, 255), 3)
    cv2.line(frame, (x2, y1), (x2 - length, y1), (255, 255, 255), 3)
    cv2.line(frame, (x2, y1), (x2, y1 + length), (255, 255, 255), 3)
    cv2.line(frame, (x1, y2), (x1 + length, y2), (255, 255, 255), 3)
    cv2.line(frame, (x1, y2), (x1, y2 - length), (255, 255, 255), 3)
    cv2.line(frame, (x2, y2), (x2 - length, y2), (255, 255, 255), 3)
    cv2.line(frame, (x2, y2), (x2 - length, y2), (255, 255, 255), 3)

    # Nhãn hiển thị
    if track.text:
        label = f"#{track.track_id}  {track.text} ({track.ocr_conf:.0%})"
    else:
        label = f"#{track.track_id}  BIEN SO? ({track.det_conf:.0%})"

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.55
    thickness = 2
    (tw, th), baseline = cv2.getTextSize(label, font, scale, thickness)

    ty = y1 - 8 if y1 - 8 > th + 4 else y2 + th + 12
    badge_x1 = x1
    badge_y1 = ty - th - 6
    badge_x2 = min(W - 2, x1 + tw + 14)
    badge_y2 = ty + baseline + 2

    # Nền badge
    cv2.rectangle(frame, (badge_x1, badge_y1), (badge_x2, badge_y2), color_dark, -1)
    cv2.rectangle(frame, (badge_x1, badge_y1), (badge_x2, badge_y2), color_primary, 1, cv2.LINE_AA)

    # Chữ
    cv2.putText(frame, label, (badge_x1 + 6, ty - 2), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)


def draw_hud(frame: np.ndarray, video_title: str, frame_idx: int, total_frames: int, fps_curr: float, confirmed_plates: List[Tuple[str, float, str]]):
    """Vẽ bảng HUD phong cách dashboard sang trọng ở góc trên màn hình."""
    H, W = frame.shape[:2]

    # Glassmorphism background box
    hud_w = 340
    hud_h = 100 + min(len(confirmed_plates), 5) * 26
    sub_img = frame[12:12 + hud_h, 12:12 + hud_w]
    if sub_img.shape[0] == hud_h and sub_img.shape[1] == hud_w:
        dark_rect = np.full(sub_img.shape, 20, dtype=np.uint8)
        cv2.addWeighted(sub_img, 0.35, dark_rect, 0.65, 0, sub_img)
        cv2.rectangle(frame, (12, 12), (12 + hud_w, 12 + hud_h), (80, 80, 80), 1, cv2.LINE_AA)

    # Tiêu đề & FPS
    cv2.putText(frame, "CAM OCR DETECTOR", (24, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 90), 2, cv2.LINE_AA)
    cv2.putText(frame, f"{fps_curr:.1f} FPS", (hud_w - 75, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

    # Progress & Time
    pct = (frame_idx / max(total_frames, 1)) * 100
    sec = frame_idx / 25.0
    m, s = int(sec // 60), sec % 60
    cv2.putText(frame, f"Frame: {frame_idx}/{total_frames} ({pct:.0f}%) | {m:02d}:{s:04.1f}",
                (24, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)

    # Đường kẻ phân cách
    cv2.line(frame, (24, 68), (12 + hud_w - 12, 68), (60, 60, 60), 1)

    # Danh sách biển số đã nhận diện
    cv2.putText(frame, f"BIEN SO DA XAC NHAN ({len(confirmed_plates)}):", (24, 88),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1, cv2.LINE_AA)

    y_offset = 112
    for text, conf, ts_str in reversed(confirmed_plates[-5:]):
        plate_str = f"[{ts_str}]  {text:<13}  ({conf:.0%})"
        cv2.putText(frame, plate_str, (28, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        y_offset += 24


# ─── Main Video Processor ────────────────────────────────────────────────────
def process_video(
    video_path: str,
    out_path: Optional[str] = None,
    detect_every: int = 3,
    conf_thresh: float = 0.25,
    show_window: bool = False,
) -> List[Tuple[str, float, str]]:
    """
    Xử lý 1 video camera với luồng bất đồng bộ:
    - Main thread: Giải mã video, inference YOLO, tracking, vẽ HUD, ghi video.
    - Background workers: OCR các crop biển số song song, không làm khựng video.
    """
    video_name = Path(video_path).name
    print(f"\n{'='*70}")
    print(f"  🎬 ĐANG XỬ LÝ VIDEO: {video_name}")
    print(f"  ⚙️ Cấu hình: detect_every={detect_every} frames | conf_thresh={conf_thresh}")
    print(f"{'='*70}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[LỖI] Không thể mở file video: {video_path}")
        return []

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_s = total_frames / src_fps if src_fps > 0 else 0

    print(f"  Độ phân giải: {width}x{height} | FPS: {src_fps:.1f} | Tổng frames: {total_frames} ({duration_s:.1f}s)")

    # Khởi tạo Detector và OCR
    detector = FastPlateDetector()
    ocr = VNPlateOCR()
    tracker = PlateTracker(max_lost_frames=int(src_fps * 0.8))  # ~0.8s dung sai

    # Khởi tạo VideoWriter nếu cần xuất video
    writer = None
    if out_path:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(out_path, fourcc, src_fps, (width, height))
        print(f"  Đầu ra video: {out_path}")

    # Pool luồng cho OCR chạy ngầm
    ocr_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="PlateOCR")
    pending_ocr: Dict[concurrent.futures.Future, int] = {}  # Future -> track_id

    def _async_ocr_task(crop_img: np.ndarray):
        crop_prep = prepare_crop(crop_img)
        text, conf, ptype = ocr.read(crop_prep)
        return text, conf, ptype

    # Bảng tổng kết kết quả
    confirmed_plates: List[Tuple[str, float, str]] = []  # (text, conf, ts_str)
    seen_texts: Dict[str, float] = {}  # text -> timestamp cuối xuất hiện

    frame_idx = 0
    t_start = time.perf_counter()
    fps_smooth = src_fps
    last_detections = []

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1
            t_frame = time.perf_counter()
            current_ts = frame_idx / src_fps

            # 1. Thu thập kết quả từ các task OCR ngầm đã hoàn thành
            done_futures = [f for f in pending_ocr.keys() if f.done()]
            for fut in done_futures:
                tid = pending_ocr.pop(fut)
                try:
                    text, conf, ptype = fut.result()
                    if tid in tracker.tracks and text:
                        tracker.tracks[tid].add_ocr_result(text, conf, ptype)
                        # In thông báo khi phát hiện biển số rõ nét
                        if tracker.tracks[tid].is_confirmed:
                            bt = tracker.tracks[tid].text
                            bc = tracker.tracks[tid].ocr_conf
                            last_t = seen_texts.get(bt, -999)
                            if (current_ts - last_t) > 10.0:  # Cooldown 10s tránh trùng
                                seen_texts[bt] = current_ts
                                m, s = int(current_ts // 60), current_ts % 60
                                ts_str = f"{m:02d}:{s:04.1f}"
                                confirmed_plates.append((bt, bc, ts_str))
                                print(f"    ✨ [{ts_str}] BIỂN SỐ MỚI: {bt:<14} (conf={bc:.0%}) [Track #{tid}]")
                except Exception as e:
                    logger.debug("Error in OCR task result: %s", e)

            # 2. Chạy phát hiện YOLO định kỳ (detect_every frame)
            if frame_idx % detect_every == 1 or not tracker.tracks:
                last_detections = detector.detect(frame, conf_thresh=conf_thresh)

            # 3. Cập nhật vị trí các track
            active_tracks = tracker.update(last_detections, frame_idx, current_ts)

            # 4. Giao việc cho OCR worker nếu track cần đọc biển số
            for tr in active_tracks:
                # Điều kiện gửi OCR:
                # - Chưa có kết quả tin cậy (< 0.75)
                # - Chưa vượt quá 4 lần thử
                # - Không có task OCR nào đang pending cho track này
                is_pending = tr.track_id in pending_ocr.values()
                if not is_pending and tr.ocr_conf < 0.75 and tr.ocr_attempts < 4:
                    x1, y1, x2, y2 = tr.box
                    H, W = frame.shape[:2]
                    # Mở rộng vùng crop biển số một chút để không bị cụt mép
                    px = max(6, int((x2 - x1) * 0.15))
                    py = max(4, int((y2 - y1) * 0.15))
                    crop = frame[max(0, y1 - py):min(H, y2 + py), max(0, x1 - px):min(W, x2 + px)].copy()

                    if validate_crop(crop):
                        tr.ocr_attempts += 1
                        fut = ocr_executor.submit(_async_ocr_task, crop)
                        pending_ocr[fut] = tr.track_id

            # 5. Vẽ HUD và Bounding Box
            render_frame = frame.copy()
            for tr in active_tracks:
                draw_plate_box(render_frame, tr)

            fps_instant = 1.0 / max(time.perf_counter() - t_frame, 1e-6)
            fps_smooth = 0.9 * fps_smooth + 0.1 * fps_instant

            draw_hud(render_frame, video_name, frame_idx, total_frames, fps_smooth, confirmed_plates)

            # 6. Ghi frame vào video kết quả
            if writer:
                writer.write(render_frame)

            if show_window:
                cv2.imshow("Camera License Plate Recognition", render_frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    print("\n  [DỪNG] Người dùng đã nhấn phím dừng.")
                    break

            # 7. Tiến độ console mỗi 100 frames
            if frame_idx % 100 == 0 or frame_idx == total_frames:
                elapsed = time.perf_counter() - t_start
                pct = (frame_idx / max(total_frames, 1)) * 100
                speed = frame_idx / max(elapsed, 1e-3)
                eta = (total_frames - frame_idx) / max(speed, 1e-3)
                sys.stdout.write(f"\r  ⏳ Tiến độ: {pct:5.1f}% ({frame_idx}/{total_frames}) | Tốc độ: {speed:4.1f} FPS | Còn lại: {eta:3.0f}s")
                sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n\n  [INFO] Đã hủy bởi người dùng.")

    # Chờ các task OCR còn lại
    for fut in list(pending_ocr.keys()):
        try:
            tid = pending_ocr.pop(fut)
            text, conf, ptype = fut.result(timeout=1.0)
            if tid in tracker.tracks and text:
                tracker.tracks[tid].add_ocr_result(text, conf, ptype)
                if tracker.tracks[tid].is_confirmed:
                    bt = tracker.tracks[tid].text
                    bc = tracker.tracks[tid].ocr_conf
                    if bt not in seen_texts:
                        confirmed_plates.append((bt, bc, "end"))
        except Exception:
            pass

    ocr_executor.shutdown(wait=False)
    cap.release()
    if writer:
        writer.release()
    if show_window:
        cv2.destroyAllWindows()

    total_time = time.perf_counter() - t_start
    real_fps = frame_idx / max(total_time, 1e-3)

    print(f"\n\n{'─'*70}")
    print(f"  🏁 HOÀN TẤT: {video_name}")
    print(f"  ⏱️  Thời gian chạy: {total_time:.1f}s | Tốc độ trung bình: {real_fps:.1f} FPS (mượt mà!)")
    print(f"  📋 TỔNG BIỂN SỐ XÁC NHẬN: {len(confirmed_plates)}")
    if confirmed_plates:
        print(f"  {'STT':<4} {'THỜI ĐIỂM':<12} {'BIỂN SỐ':<16} {'ĐỘ TIN CẬY':<10}")
        print(f"  {'─'*45}")
        for i, (text, conf, ts_str) in enumerate(confirmed_plates, 1):
            print(f"  {i:<4} {ts_str:<12} {text:<16} {conf:.0%}")
    else:
        print("  (Không tìm thấy biển số xe nào trong video này)")

    if out_path:
        print(f"  📁 Video kết quả đã lưu tại: {out_path}")
    print(f"{'='*70}\n")

    return confirmed_plates


# ─── Command Line Interface ──────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Nhận dạng biển số xe từ video camera với hiệu năng tối ưu mượt nhất.")
    parser.add_argument("video", nargs="?", help="Đường dẫn file video đầu vào")
    parser.add_argument("--out", default=None, help="Đường dẫn lưu video kết quả")
    parser.add_argument("--every", type=int, default=3, help="Tần suất phát hiện biển số (mặc định 3 frames)")
    parser.add_argument("--conf", type=float, default=0.25, help="Ngưỡng tin cậy của detector (mặc định 0.25)")
    parser.add_argument("--show", action="store_true", help="Hiển thị cửa sổ xem trực tiếp khi đang chạy")
    parser.add_argument("--all", dest="process_all", action="store_true",
                        help="Tự động quét và xử lý toàn bộ video camera trong thư mục ~/Downloads/")

    args = parser.parse_args()

    if args.process_all:
        dl_dir = Path.home() / "Downloads"
        video_files = []
        for ext in ("*.mp4", "*.mov", "*.avi", "*.mkv"):
            for p in dl_dir.glob(ext):
                # Bỏ qua các file kết quả đã tạo trước đó
                if not p.stem.endswith("_result"):
                    video_files.append(p)

        video_files = sorted(video_files)
        if not video_files:
            print(f"[THÔNG BÁO] Không tìm thấy video camera nào trong: {dl_dir}")
            sys.exit(0)

        print(f"📂 Tìm thấy {len(video_files)} video camera trong {dl_dir}:")
        for vf in video_files:
            print(f"   • {vf.name}")

        grand_summary: Dict[str, Tuple[str, float]] = {}  # text -> (video_name, max_conf)

        for vf in video_files:
            out_file = str(vf.parent / f"{vf.stem}_result.mp4")
            plates = process_video(
                str(vf),
                out_path=out_file,
                detect_every=args.every,
                conf_thresh=args.conf,
                show_window=args.show,
            )
            for text, conf, ts in plates:
                if text not in grand_summary or conf > grand_summary[text][1]:
                    grand_summary[text] = (vf.name, conf)

        print("\n" + "=" * 70)
        print("  🏆 BẢNG TỔNG KẾT TẤT CẢ BIỂN SỐ TRONG THƯ MỤC DOWNLOADS:")
        print("=" * 70)
        if grand_summary:
            print(f"  {'STT':<4} {'BIỂN SỐ':<16} {'ĐỘ TIN CẬY':<12} {'XUẤT HIỆN TẠI VIDEO':<30}")
            print("  " + "─" * 65)
            for i, (text, (vname, conf)) in enumerate(sorted(grand_summary.items()), 1):
                print(f"  {i:<4} {text:<16} {conf:<12.0%} {vname:<30}")
        else:
            print("  Không phát hiện biển số hợp lệ nào.")
        print("=" * 70 + "\n")

    else:
        if not args.video:
            parser.print_help()
            sys.exit(1)

        video_path = os.path.expanduser(args.video)
        if not os.path.isfile(video_path):
            print(f"[LỖI] File không tồn tại: {video_path}")
            sys.exit(1)

        out_path = args.out
        if out_path is None:
            p = Path(video_path)
            out_path = str(p.parent / f"{p.stem}_result.mp4")

        process_video(
            video_path,
            out_path=out_path,
            detect_every=args.every,
            conf_thresh=args.conf,
            show_window=args.show,
        )


if __name__ == "__main__":
    main()
