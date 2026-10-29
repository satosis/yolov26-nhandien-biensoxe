import os
import logging
import importlib.util
import numpy as np
import cv2

from backend.services import config as cfg
from backend.util.ocr_pipeline import prepare_ocr_image
from ._ort_utils import (
    ort,
    _HAS_ORT,
    _patch_e2e_loss,
    _onnx_runtime_target,
    _plate_ort_session_options,
)

logger = logging.getLogger("inference.plate_detector")


def validate_plate_crop(crop) -> bool:
    if crop is None or crop.size == 0:
        return False

    h, w = crop.shape[:2]
    aspect = w / max(1, h)

    if aspect < 0.6 or aspect > 7.0:
        return False

    if w < 20 or h < 10:
        return False

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.mean() < 20:
        return False

    edges = cv2.Canny(gray, 40, 140)
    edge_ratio = np.count_nonzero(edges) / (h * w)
    if edge_ratio < 0.01 or edge_ratio > 0.70:
        return False

    return True


def enhance_plate(crop):
    """Compatibility wrapper; OCR itself keeps the original crop as its first try."""
    return prepare_ocr_image(crop, profile="plate")


class PlateDetector:
    def __init__(self, native_threads=None, prefer_float=False):
        self._engine_type = "cpu"
        self._ort_session = None
        self._model = None
        self._rknn = None
        model_path = cfg.PLATE_MODEL_PATH
        
        logger.info("PlateDetector: Initializing... Target model: %s", model_path)

        if model_path.endswith(".pt") and os.path.exists(model_path):
            import torch
            from ultralytics import YOLO

            _patch_e2e_loss()
            self._model = YOLO(model_path, task="detect")
            device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
            self._model.to(device)
            logger.info("PlateDetector: PyTorch loaded using %s", device.upper())
            self._engine_type = f"yolo_{device}"
            try:
                dummy = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = self._model(dummy, imgsz=640, device=device, verbose=False)
            except Exception as e:
                logger.debug("Warmup warning: %s", e)
            return

        rknn_path = cfg.RKNN_MODEL_PATH
        if rknn_path and os.path.exists(rknn_path) and importlib.util.find_spec("rknnlite"):
            from rknnlite.api import RKNNLite
            self._rknn = RKNNLite()
            ret = self._rknn.load_rknn(rknn_path)
            if ret == 0:
                ret = self._rknn.init_runtime()
            if ret != 0:
                logger.error("RKNN model load or initialization failed (code=%s)", ret)
                raise RuntimeError(f"RKNN load/init failed with code {ret}")
            self._engine_type = "rknn"
            logger.info("PlateDetector: Successfully loaded RKNN (NPU) engine: %s", rknn_path)

        if self._engine_type == "cpu" and _HAS_ORT:
            path = model_path
            if path.endswith(".pt"):
                onnx_path = path.replace(".pt", ".onnx")
                int8_path = path.replace(".pt", "_int8.onnx")
                if os.path.exists(int8_path):
                    path = int8_path
                elif os.path.exists(onnx_path):
                    path = onnx_path

            if path.endswith(".onnx") and os.path.exists(path):
                path, providers = _onnx_runtime_target(
                    path, ort.get_available_providers(),
                    explicit_model="PLATE_MODEL_PATH" in os.environ,
                    prefer_float=prefer_float,
                )
                provider_options = [({"MLComputeUnits": "CPUAndGPU"}
                                     if name == "CoreMLExecutionProvider" else {})
                                    for name in providers]
                self._ort_session = ort.InferenceSession(
                    path, sess_options=_plate_ort_session_options(native_threads),
                    providers=providers, provider_options=provider_options)
                active = self._ort_session.get_providers()
                accelerated = next((name for name in providers if name in active and name != "CPUExecutionProvider"), None)
                self._engine_type = "onnx_" + (accelerated or "cpu")
                logger.info("PlateDetector: ONNX model=%s providers=%s active=%s", path, providers, active)

        if self._engine_type == "cpu":
            if not os.path.exists(model_path):
                logger.error("Configured plate model is missing")
                raise FileNotFoundError(f"PlateDetector model not found: {model_path}")
            if model_path.endswith(".onnx"):
                logger.error("ONNX Runtime is unavailable for the configured plate model")
                raise RuntimeError("ONNX Runtime is required for the configured plate model")
            from ultralytics import YOLO

            _patch_e2e_loss()
            self._model = YOLO(cfg.PLATE_MODEL_PATH, task="detect")
            self._model.to("cpu")
            self._engine_type = "yolo_cpu"
            logger.info("PlateDetector: Fallback to YOLO CPU: %s", cfg.PLATE_MODEL_PATH)

    def detect(self, frame):
        if self._model:
            return self._detect_yolo(frame)
        elif self._ort_session:
            return self._detect_onnx(frame)
        elif self._engine_type == "rknn":
            return self._detect_rknn(frame)
        logger.error("PlateDetector has no available inference engine")
        raise RuntimeError("No inference engine available")

    def _detect_rknn(self, frame):
        blob = cv2.resize(frame, (640, 640))
        outputs = self._rknn.inference(inputs=[blob])
        pred = outputs[0][0]
        return self._process_pred(pred, frame.shape[:2])

    def _detect_onnx(self, frame):
        input_name = self._ort_session.get_inputs()[0].name
        blob = cv2.dnn.blobFromImage(frame, 1/255.0, (640, 640), swapRB=True)
        outputs = self._ort_session.run(None, {input_name: blob})
        pred = outputs[0][0]
        return self._process_pred(pred, frame.shape[:2])

    def _detect_yolo(self, frame):
        dev = "mps" if "mps" in self._engine_type else ("cuda" if "cuda" in self._engine_type else "cpu")
        results = self._model(frame, imgsz=640, conf=0.25, device=dev, verbose=False)
        boxes = []
        for r in results:
            if not r.boxes: continue
            for b in r.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                conf = float(b.conf[0])
                cls_id = int(b.cls[0])
                boxes.append((x1, y1, x2, y2, conf, cls_id))
        return boxes

    def _process_pred(self, pred, img_shape):
        h, w = img_shape
        sx, sy = w / 640, h / 640
        boxes = []
        for row in pred:
            conf = float(row[4])
            if conf < 0.25: continue
            
            cls_id = int(row[5])
            b = row[:4]
            if b[2] < b[0] or b[3] < b[1]:
                cx, cy, wb, hb = b
                x1, y1, x2, y2 = cx - wb/2, cy - hb/2, cx + wb/2, cy + hb/2
            else:
                x1, y1, x2, y2 = b

            if x2 <= 1.05 and y2 <= 1.05:
                x1, y1, x2, y2 = x1 * 640, y1 * 640, x2 * 640, y2 * 640

            boxes.append((int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), conf, cls_id))
        return boxes

    def close(self):
        if self._rknn:
            self._rknn.release()
            logger.info("PlateDetector: RKNN released")
        self._model = None
