"""Luồng biển số dùng chung cho mọi nguồn, không chạm tới DB hay camera.

Ảnh tải lên, webcam, camera trên trang, xem trước video và dịch vụ camera nền đều đi qua đúng một chuỗi:
detect_plate_regions → crop_plate → plate_crop_quality → OCRTrackAggregator (OCR qua
PlateFrameAnalyzer.recognize_crop). Khác biệt chỉ nằm ở PROFILES; bốn tab của trang Nhận diện (Camera,
Webcam, Ảnh, Video) dùng chung đúng một cấu hình ở đó.

Một track chốt biển theo ba nguồn bằng chứng, cùng qua bộ gom track: từng crop tự đọc được;
bằng chứng của nhiều crop gộp lại (recognize_track); và với biển quá nhỏ, số kẻ trên thân xe
ngay quanh biển (plate_context) khi chính biển không đọc ra chuỗi nào khác.
"""
from __future__ import annotations

import contextlib
import functools
import itertools
import re
import threading
import time

import cv2
import numpy as np

from backend.util.bbox_utils import box_iou, merge_plate_boxes
from backend.util.ocr.pipeline import OCRTrackAggregator, assess_image

# Cấu hình theo loại luồng. include_people: lấy cả người; min_support: số khung khác nhau phải cùng đọc ra một
# biển mới chốt (luồng chỉ có một khung — ảnh đơn — thì một lần đọc là đủ); max_gap_seconds: cửa sổ ghép
# track tối thiểu (luồng xử lý chậm hơn nhịp khung thì cửa sổ nới theo nhịp thực tế, nếu không mỗi khung
# thành một track riêng); max_crops: số crop giữ lại mỗi track để đọc; iou_threshold: ngưỡng ghép track.
#
# Bốn tab của trang Nhận diện (Camera, Webcam, Ảnh, Video) dùng chung "page": test qua tab nào cũng là cùng một
# logic, tab chỉ khác nhau ở nơi lấy khung hình. Cần chỉnh cho một tab thì chỉnh "page" cho cả bốn; không thêm
# cấu hình hay nhánh riêng theo tab. "camera" là dịch vụ camera nền: tự phát hiện, không lấy người.
PROFILES = {
    "page": {"include_people": True, "min_support": 2, "max_gap_seconds": 1.25, "max_crops": 10, "iou_threshold": .10},
    "camera": {"include_people": False, "min_support": 2, "max_gap_seconds": 1.5, "max_crops": 5, "iou_threshold": .15},
}
# Mọi luồng trong một tiến trình dùng chung bộ đếm, nên track id không trùng giữa các luồng.
_TRACK_IDS = itertools.count(1)
# Biển thấp hơn mức này (px) thì chữ trên biển quá nhỏ để chắc chắn tự đọc được: giữ thêm vùng
# thân xe quanh biển để tìm số kẻ trên thân xe.
_CONTEXT_PLATE_HEIGHT = 40
# Các mức cạnh dài đầu vào của lượt phát hiện nhanh.
# Bộ phát hiện không thấy biển thấp hơn _FAST_MIN_PLATE_HEIGHT px ở cỡ đầu vào: biển như vậy ở mức 416
# thì chỉ lượt soát mới thấy; biển đang cao gấp đôi mức đó ở mức 288 thì lượt nhanh dùng 288.
_FAST_LONG_SIDES = (416, 288)
_FAST_MIN_PLATE_HEIGHT = 12
# Lượt nhanh không thấy biển nào suốt _IDLE_SECONDS thì soát kỹ một lượt. Lượt soát sau chỉ chạy khi các
# lượt nhanh từ lần soát trước đã dùng gấp _DISCOVERY_SHARE lần thời gian của nó (lượt soát chiếm không quá
# 1/4 thời gian phát hiện), để luồng đang trống — webcam chưa chĩa vào biển — không bị lượt soát làm chậm.
_IDLE_SECONDS = 2.5
_DISCOVERY_SHARE = 3
# Một crop tự đọc được với độ tin cậy này thì không cần tới số kẻ trên thân xe.
_OWN_READ_CONFIDENCE = .70


def format_plate(value: str) -> str:
    """Biển số (đã chuẩn hoá hoặc chưa) → dạng hiển thị 30F-945.35, dùng chung cho mọi nơi hiện/lưu."""
    if not isinstance(value, str) or not value.strip():
        return ""
    original = value.strip().upper()
    clean = re.sub(r"[^A-Z0-9]", "", original)
    patterns = (
        r"^(\d{2}[A-Z]{1,2})(\d{4,5})$",  # 30F-945.35
        r"^(\d{2}[A-Z]\d)(\d{4,5})$",  # 29A1-123.45
        r"^(T\d{2})(\d{4,5})$",  # Temporary plates
        r"^([A-Z]{2})(\d{4,5})$",  # Military / special plates
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, clean)
        if match:
            prefix, number = match.groups()
            suffix = f"{number[:3]}.{number[3:]}" if len(number) == 5 else number
            return f"{prefix}-{suffix}"
    return original


def detect_plate_regions(detector, frame, *, lock=None, input_size=None):
    """Return detections in frame coordinates; the two rows of a square plate detected as separate boxes
    are merged into one plate box. input_size(shape) gives the detector input (width, height) for the
    frame; None uses the model's own export size.
    """
    options = {} if input_size is None else {"size": input_size(frame.shape)}
    with lock or contextlib.nullcontext():
        detections = detector.detect(frame, **options)
    height, width = frame.shape[:2]
    boxes = []
    for x1, y1, x2, y2, confidence, cls_id in detections:
        x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), min(width, int(x2)), min(height, int(y2))
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2, min(1., max(0., float(confidence))), int(cls_id)))
    kept = []
    for box in sorted(boxes, key=lambda b: b[4], reverse=True):
        if not any(box[5] == other[5] and box_iou(box, other) >= .45 for other in kept):
            kept.append(box)
    return merge_plate_boxes(kept)


def crop_plate(frame, bbox):
    """Cắt biển số với một quy tắc đệm cho mọi nguồn: 10% ngang, 12% dọc (đệm cố định lấn chữ nhỏ).

    Trả về view của khung gốc; bộ bỏ phiếu chỉ sao chép những crop nó giữ lại.
    """
    x1, y1, x2, y2 = (int(value) for value in bbox[:4])
    px, py = max(2, round((x2 - x1) * .10)), max(1, round((y2 - y1) * .12))
    return frame[max(0, y1 - py):min(frame.shape[0], y2 + py), max(0, x1 - px):min(frame.shape[1], x2 + px)]


def plate_context(frame, bbox):
    """Vùng thân xe quanh một biển nhỏ: rộng thêm 1,5 lần biển mỗi bên, cao thêm 3 lần biển trên dưới.

    Số kẻ trên thân xe nằm ngay trên hoặc dưới biển; vùng hẹp theo chiều ngang để không lấy
    nhầm chữ của xe bên cạnh. Trả về view của khung gốc.
    """
    x1, y1, x2, y2 = (int(value) for value in bbox[:4])
    dx, dy = round((x2 - x1) * 1.5), (y2 - y1) * 3
    return frame[max(0, y1 - dy):min(frame.shape[0], y2 + dy), max(0, x1 - dx):min(frame.shape[1], x2 + dx)]


def plate_crop_quality(crop):
    """Cổng chất lượng duy nhất trước OCR: số đo ảnh (assess_image) và luật hình dạng biển số.

    Crop là biển khi: rộng/cao từ 0,6 đến 7, tối thiểu 20×10 px, không tối đen, và có nét
    (1–70% điểm ảnh là cạnh).
    """
    quality = assess_image(crop)
    if quality["valid"]:
        height, width = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = np.count_nonzero(cv2.Canny(gray, 40, 140)) / (height * width)
        quality["valid"] = bool(.6 <= width / height <= 7. and width >= 20 and height >= 10
                                and gray.mean() >= 20 and .01 <= edges <= .70)
    return quality


class PlateFrameAnalyzer:
    """Observe native crops now and recognize only retained crops when requested."""

    def __init__(self, detector, reader, *, detector_lock=None, ocr_lock=None, include_people=False):
        self.detector, self.reader = detector, reader
        self._small_seen = None         # lần gần nhất lượt soát chỉ thấy toàn biển quá nhỏ với lượt nhanh
        self._idle_since = None         # thời điểm bắt đầu không thấy biển nào
        self._discovery_cost = 0.       # giây của lượt soát gần nhất
        self._fast_spent = 0.           # giây các lượt nhanh đã dùng từ lượt soát đó
        self._long_side = _FAST_LONG_SIDES[0]
        self.detector_lock = detector_lock
        self.ocr_lock = ocr_lock or threading.Lock()
        self.include_people = include_people

    def is_valid(self, text):
        return self.reader.is_valid_vn_plate(text)

    def detect(self, frame, timestamp=0, *, still=False):
        """Phát hiện trên một khung; mức soát do luồng tự chọn theo những gì nó vừa thấy, không theo nguồn.

        Lượt nhanh (đầu vào nhỏ, đúng tỉ lệ khung) theo các biển đang thấy. Lượt soát (đầu vào cao đủ cỡ
        xuất của mô hình) tìm biển mà lượt nhanh không thấy được; nó chạy khi khung đứng yên (still: ảnh
        đơn, video tạm dừng), sau lần gần nhất lượt soát chỉ thấy toàn biển quá nhỏ với lượt nhanh (trong
        _IDLE_SECONDS, hoặc _DISCOVERY_SHARE lượt soát nếu máy chậm: một lượt lâu hơn _IDLE_SECONDS thì cửa
        sổ cố định đã hết trước khi lượt kế tiếp bắt đầu), và khi lượt nhanh không thấy biển nào suốt
        _IDLE_SECONDS (lặp lại theo _DISCOVERY_SHARE).
        """
        keep_small = max(_IDLE_SECONDS, _DISCOVERY_SHARE * self._discovery_cost)
        discover = still or (
            self._small_seen is not None and timestamp - self._small_seen < keep_small) or (
            self._idle_since is not None and timestamp - self._idle_since >= _IDLE_SECONDS
            and self._fast_spent >= _DISCOVERY_SHARE * self._discovery_cost)
        if discover:
            input_size = self.detector.discovery_size
        else:
            input_size = functools.partial(self.detector.input_size, long_side=self._long_side)
        started = time.perf_counter()
        detections = detect_plate_regions(self.detector, frame, lock=self.detector_lock, input_size=input_size)
        spent = time.perf_counter() - started
        heights = [box[3] - box[1] for box in detections if box[5] == 0]
        # Chiều cao biển (px) ở từng mức đầu vào nhanh.
        scales = [self.detector.input_size(frame.shape, side)[1] / frame.shape[0] for side in _FAST_LONG_SIDES]
        if discover:
            self._discovery_cost, self._fast_spent = spent, 0.
            if heights:
                small = all(height * scales[0] < _FAST_MIN_PLATE_HEIGHT for height in heights)
                self._small_seen = timestamp if small else None
        else:
            self._fast_spent += spent
        if heights:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = timestamp
        roomy = heights and min(heights) * scales[1] >= 2 * _FAST_MIN_PLATE_HEIGHT
        self._long_side = _FAST_LONG_SIDES[1 if roomy else 0]
        return detections

    def observe(self, frame, timestamp=0, *, detections=None, still=False):
        """Crop + quality for each detection; detections may come from a caller that also needs them."""
        if detections is None:
            detections = self.detect(frame, timestamp, still=still)
        observations = []
        for x1, y1, x2, y2, confidence, cls_id in detections:
            if cls_id == 1 and self.include_people:
                observations.append({"type": "person", "bbox": [x1, y1, x2, y2], "confidence": confidence,
                                     "crop": frame[y1:y2, x1:x2]})
                continue
            if cls_id != 0:
                continue
            crop = crop_plate(frame, (x1, y1, x2, y2))
            # Biển chạm mép khung hình là biển bị cắt: vẫn theo dõi nhưng không đọc, vì phần còn lại
            # của một biển vẫn có thể ra một chuỗi đúng cấu trúc.
            cut = x1 <= 1 or y1 <= 1 or x2 >= frame.shape[1] - 1 or y2 >= frame.shape[0] - 1
            quality = plate_crop_quality(crop)
            if not quality["valid"]:
                continue
            observations.append({
                "type": "plate", "bbox": [x1, y1, x2, y2], "ocr_read": cut,
                "detector_confidence": confidence, "quality": quality, "crop": crop,
                "context": plate_context(frame, (x1, y1, x2, y2)) if y2 - y1 < _CONTEXT_PLATE_HEIGHT else None,
            })
        return observations

    def recognize_crop(self, crop, context=None):
        """Đọc một crop biển số; context là vùng thân xe quanh biển (chỉ có với biển nhỏ).

        Biển tự đọc được thì dùng kết quả đó. Biển không tự đọc ra chuỗi nào mà quanh nó có một
        dòng chữ đúng cấu trúc biển số thì lấy dòng chữ đó. Bộ gom track chỉ gọi một lần cho mỗi crop.
        """
        with self.ocr_lock:
            text, confidence = self.reader.read(crop)
            if context is not None and not text:
                # Chiều cao biển suy từ crop: crop_plate đệm 12% trên và dưới.
                spotted, spotted_confidence = self.reader._ocr.spot(context, crop.shape[0] / 1.24)
                if spotted and spotted_confidence >= _OWN_READ_CONFIDENCE:
                    text, confidence = spotted, spotted_confidence
        return {"text": text, "confidence": float(confidence)}

    def recognize_track(self, crops):
        """Gộp bằng chứng đọc của các crop cùng một track khi từng crop chưa đủ chốt biển."""
        with self.ocr_lock:
            text, confidence = self.reader._ocr.read_many(crops)
        return {"text": text, "confidence": float(confidence)}


class PlateStream:
    """Luồng biển số của một nguồn (một webcam, một camera, một video, một ảnh); profile: khóa của PROFILES."""

    def __init__(self, detector, reader, profile, *, normalize=None, detector_lock=None, ocr_lock=None):
        settings = PROFILES[profile]
        self.analyzer = PlateFrameAnalyzer(detector, reader, detector_lock=detector_lock, ocr_lock=ocr_lock,
                                           include_people=settings["include_people"])
        self.aggregator = OCRTrackAggregator(
            normalize=normalize, validator=self.analyzer.is_valid,
            recognize=self.analyzer.recognize_crop, recognize_track=self.analyzer.recognize_track,
            max_gap_seconds=settings["max_gap_seconds"], max_crops=settings["max_crops"],
            iou_threshold=settings["iou_threshold"], max_tracks=64, min_support=settings["min_support"],
            track_ids=_TRACK_IDS,
        )
        self.track_meta = {}

    def step(self, frame, timestamp, *, detections=None, finish=False):
        """Một khung → (người, track đang theo dõi, track đã kết thúc).

        finish=True là khung đứng yên (ảnh đơn, video tạm dừng): soát kỹ khung đó và chốt luôn mọi track.
        """
        observations = self.analyzer.observe(frame, timestamp, detections=detections, still=finish)
        people = [item for item in observations if item["type"] == "person"]
        expired = self.aggregator.update(timestamp, [item for item in observations if item["type"] == "plate"])
        if finish:
            expired.extend(self.aggregator.finish())
            return people, [], expired
        return people, self.aggregator.snapshot(), expired
