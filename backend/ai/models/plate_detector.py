import os
import logging
import numpy as np
import cv2

from backend.services import config as cfg
from ._ort_utils import (
    ort,
    _HAS_ORT,
    _onnx_runtime_target,
    _plate_ort_session_options,
)

logger = logging.getLogger("inference.plate_detector")

# Mô hình xuất ở cỡ cố định 640×640. Chạy ở đầu vào nhỏ đúng tỉ lệ khung hình (cạnh dài cho
# trước) nhanh hơn nhiều lần: 416 nhanh hơn ~5 lần, 288 ~10 lần; biển phải cao từ ~12px ở cỡ đó.
_EXPORT_SIZE = 640
_STRIDES = (8, 16, 32)
# Mỗi cỡ đầu vào khác cỡ xuất là một phiên riêng (0,4–0,6 GB RAM): chỉ giữ các cỡ dùng gần đây nhất.
_MAX_SIZED_SESSIONS = 6


def _resized_model(path: str, width: int, height: int) -> bytes:
    """Bản mô hình cho cỡ đầu vào khác: tính lại các hằng phụ thuộc kích thước của bản xuất cố định.

    Đó là lưới điểm neo và bước lưới của đầu ra (ba mức 8, 16, 32) và các phép reshape của khối
    attention ở mức 32. Trọng số giữ nguyên.
    """
    import onnx
    from onnx import numpy_helper

    model = onnx.load(path)
    graph = model.graph
    shape = graph.input[0].type.tensor_type.shape.dim
    shape[2].dim_value, shape[3].dim_value = height, width
    del graph.value_info[:]
    points, strides = [], []
    for stride in _STRIDES:
        ys, xs = np.meshgrid(np.arange(height // stride) + .5, np.arange(width // stride) + .5, indexing="ij")
        points.append(np.stack((xs.ravel(), ys.ravel())))
        strides.append(np.full(xs.size, stride))
    points, strides = np.concatenate(points, axis=1)[None], np.concatenate(strides)[None]
    anchors = sum((_EXPORT_SIZE // stride) ** 2 for stride in _STRIDES)
    coarse = _EXPORT_SIZE // _STRIDES[-1]
    rows, columns = height // _STRIDES[-1], width // _STRIDES[-1]
    rebuilt = 0
    for tensor in graph.initializer:
        array = numpy_helper.to_array(tensor)
        if array.shape == (1, 2, anchors):
            array = points.astype(array.dtype)
        elif array.shape == (1, anchors):
            array = strides.astype(array.dtype)
        elif array.dtype == np.int64 and array.shape == (4,) and array[2:].tolist() == [coarse, coarse]:
            array = np.array(array[:2].tolist() + [rows, columns])
        elif array.dtype == np.int64 and array.shape == (4,) and array[3] == coarse * coarse:
            array = np.array(array[:3].tolist() + [rows * columns])
        else:
            continue
        tensor.CopyFrom(numpy_helper.from_array(array, tensor.name))
        rebuilt += 1
    if rebuilt < 3:
        raise ValueError(f"Model {path} has no rebuildable anchor grid")
    return model.SerializeToString()


class PlateDetector:
    def __init__(self, native_threads=None, prefer_float=False):
        self._ort_session = None
        model_path = cfg.PLATE_MODEL_PATH

        logger.info("PlateDetector: Initializing... Target model: %s", model_path)

        if not _HAS_ORT:
            logger.error("ONNX Runtime is unavailable for the configured plate model")
            raise RuntimeError("ONNX Runtime is required for the configured plate model")

        path, providers = _onnx_runtime_target(
            model_path,
            ort.get_available_providers(),
            explicit_model="PLATE_MODEL_PATH" in os.environ,
            prefer_float=prefer_float,
        )
        if not os.path.exists(path):
            logger.error("Configured plate model is missing: %s", path)
            raise FileNotFoundError(f"PlateDetector model not found: {path}")
        if not path.lower().endswith(".onnx"):
            logger.error("PlateDetector only supports .onnx models: %s", path)
            raise ValueError(f"PlateDetector requires an .onnx model: {path}")

        provider_options = [
            ({"MLComputeUnits": "CPUAndGPU"} if name == "CoreMLExecutionProvider" else {})
            for name in providers
        ]
        self._session_options = {"providers": providers, "provider_options": provider_options}
        self._native_threads = native_threads
        self._model_file = path
        self._sized_sessions = {}
        self._ort_session = ort.InferenceSession(
            path, sess_options=_plate_ort_session_options(native_threads), **self._session_options,
        )
        logger.info("PlateDetector: ONNX model=%s providers=%s active=%s", path, providers, self._ort_session.get_providers())

    @staticmethod
    def input_size(frame_shape, long_side):
        """(rộng, cao) đầu vào nhỏ: cạnh dài long_side, đúng tỉ lệ khung, bội của 32."""
        height, width = frame_shape[:2]
        scale = long_side / max(height, width)
        return max(96, round(width * scale / 32) * 32), max(96, round(height * scale / 32) * 32)

    @staticmethod
    def discovery_size(frame_shape):
        """(rộng, cao) đầu vào của lượt soát biển nhỏ: cao đủ cỡ xuất, rộng theo tỉ lệ khung nhưng không quá cỡ xuất.

        Chiều cao của biển trong đầu vào quyết định có thấy được biển nhỏ hay không. Khung ngang cho đúng
        cỡ xuất 640×640 (khung bị ép ngang, biển vẫn đủ cao); khung dọc cho đầu vào hẹp, đúng tỉ lệ khung.
        """
        return PlateDetector.input_size(frame_shape, _EXPORT_SIZE)[0], _EXPORT_SIZE

    def detect(self, frame, size=None):
        """Phát hiện trên khung. size = (rộng, cao): dùng đầu vào cỡ đó thay cho cỡ xuất của mô hình."""
        if self._ort_session is None:
            logger.error("PlateDetector has no available inference engine")
            raise RuntimeError("No inference engine available")
        if size is None or tuple(size) == (_EXPORT_SIZE, _EXPORT_SIZE):
            return self._detect_onnx(frame, self._ort_session, (_EXPORT_SIZE, _EXPORT_SIZE))
        session = self._sized_sessions.pop(size, None)
        if session is None:
            if len(self._sized_sessions) >= _MAX_SIZED_SESSIONS:
                del self._sized_sessions[next(iter(self._sized_sessions))]
            session = ort.InferenceSession(
                _resized_model(self._model_file, *size),
                sess_options=_plate_ort_session_options(self._native_threads), **self._session_options,
            )
            logger.info("PlateDetector: input %sx%s ready", *size)
        self._sized_sessions[size] = session  # cỡ vừa dùng nằm cuối, cỡ lâu không dùng nhất nằm đầu
        return self._detect_onnx(frame, session, size)

    def _detect_onnx(self, frame, session, size):
        blob = cv2.dnn.blobFromImage(frame, 1/255.0, size, swapRB=True)
        outputs = session.run(None, {session.get_inputs()[0].name: blob})
        pred = outputs[0][0]
        return self._process_pred(pred, frame.shape[:2], size)

    def _process_pred(self, pred, img_shape, size=(_EXPORT_SIZE, _EXPORT_SIZE)):
        h, w = img_shape
        sx, sy = w / size[0], h / size[1]
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
                x1, y1, x2, y2 = x1 * size[0], y1 * size[1], x2 * size[0], y2 * size[1]

            boxes.append((int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), conf, cls_id))
        return boxes

    def close(self):
        self._ort_session = None
        self._sized_sessions = {}
