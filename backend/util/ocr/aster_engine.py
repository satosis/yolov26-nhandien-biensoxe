"""Shared AsterOCR engine (asterocr.AsterOCR) + OpenCV line/cell cropping before OCR."""

from __future__ import annotations

import io
import logging
from pathlib import Path
import threading
from typing import Optional

from asterocr import AsterOCR
import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

MAX_ONNX_STRIP_WIDTH = 1024  # asternet.onnx PositionalEncoding max_len=256 (T = W / 4)

_RECOGNIZERS_LOCK = threading.Lock()
_RECOGNIZERS: dict[Optional[int], "AsterONNXRecognizer"] = {}


def validate_native_threads(native_threads: Optional[int] = None) -> Optional[int]:
    """Validate the thread limit asked for by the caller."""
    value = native_threads
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("OCR native_threads must be an integer between 1 and 64")
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OCR native_threads must be an integer between 1 and 64") from exc
    if not 1 <= workers <= 64 or str(workers) != str(value):
        raise ValueError("OCR native_threads must be an integer between 1 and 64")
    return workers


def _to_bgr_uint8(image, *, is_bgr: bool = True) -> Optional[np.ndarray]:
    if image is None:
        return None
    if isinstance(image, (str, Path)):
        path = Path(image)
        if not path.is_file():
            return None
        with Image.open(path) as img:
            arr = np.asarray(ImageOps.exif_transpose(img).convert("RGB"), dtype=np.uint8)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if arr.size else None
    if isinstance(image, (bytes, bytearray)):
        if not image:
            return None
        with Image.open(io.BytesIO(bytes(image))) as img:
            arr = np.asarray(ImageOps.exif_transpose(img).convert("RGB"), dtype=np.uint8)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if arr.size else None
    if hasattr(image, "convert") and hasattr(image, "size"):
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.size == 0:
            return None
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if not isinstance(image, np.ndarray) or image.size == 0:
        return None
    if image.ndim not in (2, 3) or min(image.shape[:2]) < 1:
        return None
    arr = image
    if arr.dtype != np.uint8:
        if arr.dtype.kind == "f":
            if not np.isfinite(arr).all():
                return None
            if arr.min() >= 0.0 and arr.max() <= 1.0:
                arr = np.rint(arr * 255.0).astype(np.uint8)
            else:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
        elif arr.dtype.kind == "b":
            arr = arr.astype(np.uint8) * 255
        else:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 1:
        return cv2.cvtColor(arr[:, :, 0], cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR if is_bgr else cv2.COLOR_RGBA2BGR)
    if arr.shape[2] == 3:
        return arr if is_bgr else cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return None


def _needs_polarity_inversion(bgr: np.ndarray) -> bool:
    """Detect white/light text painted on dark/blue/red backgrounds."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    center = gray[2:-2, 4:-4] if h > 6 and w > 10 else gray
    bg = float(np.median(border))
    p98 = float(np.percentile(center, 98))
    p2 = float(np.percentile(center, 2))
    bright_contrast = p98 - bg
    dark_contrast = bg - p2
    if bg < 110.0 and bright_contrast > dark_contrast + 8.0:
        return True
    if bright_contrast > dark_contrast * 1.4 + 20.0:
        return True
    return False


def normalize_grayscale_strip(bgr: np.ndarray, *, invert: bool = False) -> np.ndarray:
    """Normalize a crop to smooth background-leveled 0-255 grayscale (3-channel BGR)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    if invert:
        gray = 255 - gray
    h, w = gray.shape[:2]
    if min(h, w) < 6:
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    dk = max(5, min(31, (h // 3) | 1))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dk, dk))
    bg = cv2.dilate(gray, kernel)
    bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=max(3.0, dk * 0.6))
    safe_bg = np.maximum(bg, 48).astype(np.float32)
    norm = np.clip((gray.astype(np.float32) * 255.0) / safe_bg, 0, 255).astype(np.uint8)
    return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)


def is_low_contrast_or_blurry(bgr: np.ndarray) -> bool:
    """Detect blurry, dark, backlit, or low-contrast strips where binary fallback helps."""
    if bgr is None or bgr.size == 0 or min(bgr.shape[:2]) < 6:
        return False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    mean_val = float(gray.mean())
    std_val = float(gray.std())
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return mean_val < 90.0 or mean_val > 230.0 or std_val < 28.0 or blur_var < 55.0


def _binary_fallback_strip(bgr: np.ndarray, *, invert: bool = False) -> np.ndarray:
    """Create a 0/255 adaptive-threshold fallback strip for blurry or backlit crops."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    if invert:
        gray = 255 - gray
    block = max(11, (min(gray.shape[:2]) // 2) | 1)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 8,
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


class AsterONNXRecognizer:
    """Thread-safe AsterOCR recognizer using asterocr.AsterOCR on prepared line strips."""

    def __init__(self, native_threads: Optional[int] = None):
        workers = validate_native_threads(native_threads)
        device = "cpu"
        try:
            from backend.ai.models._ort_utils import get_device

            if get_device() == "GPU":
                device = "cuda"
        except Exception:
            pass

        self._ocr = AsterOCR(backend="onnx", device=device)
        self.native_threads = workers
        self.device = device
        self._lock = threading.Lock()

        if workers is not None:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = workers
            opts.inter_op_num_threads = 1
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            providers = ["CPUExecutionProvider"]
            if device == "cuda" and "CUDAExecutionProvider" in ort.get_available_providers():
                providers.insert(0, "CUDAExecutionProvider")
            self._ocr.session = ort.InferenceSession(
                self._ocr.model_path, sess_options=opts, providers=providers,
            )
            self._ocr.input_name = self._ocr.session.get_inputs()[0].name
            self._ocr.output_name = self._ocr.session.get_outputs()[0].name

        logger.info("AsterOCR initialized (device=%s, native_threads=%s)", device, workers)

    def _run_strip(self, bgr: np.ndarray) -> tuple[str, float]:
        h, w = bgr.shape[:2]
        target_w = max(32, int(w * 32.0 / max(1, h)))
        target_w = (target_w + 3) // 4 * 4
        if target_w > MAX_ONNX_STRIP_WIDTH:
            return self._recognize_wide_strip(bgr)

        pil_rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        with self._lock:
            tensor = self._ocr.preprocess_image(pil_rgb)
            if tensor.shape[-1] > MAX_ONNX_STRIP_WIDTH:
                tensor = tensor[:, :, :, :MAX_ONNX_STRIP_WIDTH]
            ort_output = self._ocr.session.run([self._ocr.output_name], {self._ocr.input_name: tensor})[0]
            text = str(self._ocr.decode_ctc(ort_output) or "").strip()

        if not text:
            return "", 0.0

        logits = ort_output[:, 0, :] if (ort_output.ndim == 3 and ort_output.shape[1] == 1) else (
            ort_output[0, :, :] if ort_output.ndim == 3 else ort_output
        )
        best_indices = np.argmax(logits, axis=-1)
        best_logits = logits[np.arange(len(best_indices)), best_indices]
        non_blank = best_indices != self._ocr.vocab.blank_idx
        if np.any(non_blank):
            probs = np.exp(np.clip(best_logits[non_blank], -30.0, 0.0))
            conf = float(np.clip(np.mean(probs), 0.0, 1.0))
        else:
            conf = 0.0
        return text, conf

    def _recognize_wide_strip(self, bgr: np.ndarray) -> tuple[str, float]:
        """Trim blank margins and split wide strips cleanly at low-ink column gaps."""
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        bg = float(np.median(np.concatenate([gray[0, :], gray[-1, :]])))
        ink = np.abs(bg - gray.astype(np.float32)).sum(axis=0)
        active = np.where(ink > max(20.0, h * 0.8))[0]
        if active.size == 0:
            return "", 0.0

        pad_x = max(8, h // 4)
        trim_x1 = max(0, int(active[0]) - pad_x)
        trim_x2 = min(w, int(active[-1]) + 1 + pad_x)
        if trim_x1 > 0 or trim_x2 < w:
            trimmed = bgr[:, trim_x1:trim_x2]
            target_trimmed_w = ((max(32, int(trimmed.shape[1] * 32.0 / max(1, h))) + 3) // 4) * 4
            if target_trimmed_w <= MAX_ONNX_STRIP_WIDTH:
                return self._run_strip(trimmed)
            bgr = trimmed
            h, w = bgr.shape[:2]
            ink = ink[trim_x1:trim_x2]

        max_chunk_w = max(64, int((MAX_ONNX_STRIP_WIDTH * max(1, h)) / 36.0))
        if w <= max_chunk_w:
            scale_h = max(h, int(np.ceil((w * 32.0) / MAX_ONNX_STRIP_WIDTH)))
            safe_bgr = cv2.resize(bgr, (w, scale_h), interpolation=cv2.INTER_AREA)
            return self._run_strip(safe_bgr)

        lo = max(16, w // 3)
        hi = min(w - 16, (2 * w) // 3)
        if lo < hi:
            split_x = lo + int(np.argmin(ink[lo:hi]))
        else:
            split_x = w // 2
        left_bgr = bgr[:, : min(w, split_x + pad_x)]
        right_bgr = bgr[:, max(0, split_x - pad_x) :]
        t1, c1 = self._run_strip(left_bgr)
        t2, c2 = self._run_strip(right_bgr)
        texts = [t for t in (t1.strip(), t2.strip()) if t]
        confs = [c for t, c in ((t1, c1), (t2, c2)) if t.strip()]
        if not texts:
            return "", 0.0
        return " ".join(texts), float(sum(confs) / len(confs))

    def recognize_line(
        self,
        image,
        *,
        auto_polarity: bool = False,
        is_bgr: bool = True,
        pad: Optional[bool] = None,
        normalize_gray: bool = True,
    ) -> tuple[str, float]:
        """Prepare a cropped text strip as smooth 0-255 background-normalized grayscale and recognize via AsterOCR."""
        bgr = _to_bgr_uint8(image, is_bgr=is_bgr)
        if bgr is None:
            return "", 0.0
        inverted = bool(auto_polarity and _needs_polarity_inversion(bgr))

        orig_h, w = bgr.shape[:2]
        h = orig_h
        if h < 18:
            scale = 24.0 / max(1, h)
            bgr = cv2.resize(
                bgr,
                (max(1, int(round(w * scale))), 24),
                interpolation=cv2.INTER_CUBIC,
            )
            h, w = bgr.shape[:2]

        if normalize_gray:
            bgr = normalize_grayscale_strip(bgr, invert=inverted)
        elif inverted:
            bgr = 255 - bgr

        should_pad = (orig_h < 80) if pad is None else bool(pad)
        if should_pad:
            border_pixels = np.vstack([bgr[0, :, :], bgr[-1, :, :], bgr[:, 0, :], bgr[:, -1, :]])
            bg_bgr = tuple(int(v) for v in np.median(border_pixels, axis=0))
            pad_x = max(8, min(16, int(round(h * 0.24))))
            pad_y = max(3, min(8, int(round(h * 0.14))))
            bgr = cv2.copyMakeBorder(
                bgr, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT, value=bg_bgr,
            )

        return self._run_strip(bgr)

    @property
    def vocabulary(self) -> tuple:
        """Bảng ký tự của mô hình theo thứ tự cột đầu ra (cột 0 là blank)."""
        return tuple(self._ocr.vocab.chars)

    def line_log_probs(self, bgr: np.ndarray) -> np.ndarray:
        """Log xác suất từng ký tự theo từng cột ảnh (T × số ký tự) của một dòng chữ BGR.

        Ảnh được dùng nguyên trạng (chỉ co về cao 32px của mô hình): người gọi tự cắt sát và
        chuẩn bị dòng chữ. Dùng cho giải mã có ràng buộc, khi cần xác suất chứ không chỉ chuỗi.
        """
        pil_rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        with self._lock:
            tensor = self._ocr.preprocess_image(pil_rgb)
            if tensor.shape[-1] > MAX_ONNX_STRIP_WIDTH:
                tensor = tensor[:, :, :, :MAX_ONNX_STRIP_WIDTH]
            output = self._ocr.session.run([self._ocr.output_name], {self._ocr.input_name: tensor})[0]
        return output[:, 0, :] if (output.ndim == 3 and output.shape[1] == 1) else output[0]

    def predict(self, image, *, is_bgr: bool = True) -> str:
        text, _ = self.recognize_line(image, is_bgr=is_bgr)
        return text


def get_aster_recognizer(native_threads: Optional[int] = None) -> AsterONNXRecognizer:
    """Return a cached AsterONNXRecognizer keyed by validated thread count."""
    workers = validate_native_threads(native_threads)
    with _RECOGNIZERS_LOCK:
        recognizer = _RECOGNIZERS.get(workers)
        if recognizer is None:
            recognizer = AsterONNXRecognizer(native_threads=workers)
            _RECOGNIZERS[workers] = recognizer
        return recognizer


def _split_wide_boxes(
    cboxes: list[tuple[int, int, int, int, int]],
    cell_h: float,
) -> list[list[tuple[int, int, int, int, int]]]:
    x1 = min(b[0] for b in cboxes)
    x2 = max(b[2] for b in cboxes)
    if (x2 - x1) / max(1.0, cell_h) <= 24.0 or len(cboxes) <= 1:
        return [cboxes]
    cboxes = sorted(cboxes, key=lambda b: b[0])
    gaps = []
    for idx in range(len(cboxes) - 1):
        cur_right = max(b[2] for b in cboxes[: idx + 1])
        next_left = cboxes[idx + 1][0]
        gap = next_left - cur_right
        ratio = (idx + 1) / len(cboxes)
        score = gap * (1.0 - abs(ratio - 0.5))
        gaps.append((score, gap, idx + 1))
    best = max(gaps, key=lambda item: item[0])
    split_at = best[2] if best[1] >= 1 else len(cboxes) // 2
    return _split_wide_boxes(cboxes[:split_at], cell_h) + _split_wide_boxes(cboxes[split_at:], cell_h)


def detect_document_boxes(img_bgr: np.ndarray, recognizer: Optional[AsterONNXRecognizer] = None) -> list[list]:
    """Crop document text cells/lines with OpenCV and recognize each crop via asterocr.AsterOCR."""
    if img_bgr is None or img_bgr.size == 0 or min(img_bgr.shape[:2]) < 8:
        return []
    rec = recognizer or get_aster_recognizer()
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape

    bin_inv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 14,
    )
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, W // 16), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(56, H // 16)))
    hlines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, hk)
    vlines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, vk)
    lines_mask = cv2.bitwise_or(hlines, vlines)
    lines_dil = cv2.dilate(lines_mask, np.ones((3, 3), np.uint8))
    seg_bin = cv2.subtract(bin_inv, lines_dil)

    clean_img = img_bgr.copy()
    clean_img[lines_mask > 0] = (255, 255, 255)

    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(seg_bin)
    max_raw_h = max(48.0, H * 0.25)
    raw_cc: list[tuple[int, int, int, int, int]] = []
    for i in range(1, n_labels):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 3 or w < 2 or h < 2 or w > W * 0.22 or h > max_raw_h:
            continue
        patch_gray = gray[y : y + h, x : x + w]
        if patch_gray.size and int(patch_gray.min()) > 165:
            continue
        raw_cc.append((x, y, x + w, y + h, area))

    if not raw_cc:
        return []

    sample_cap = max(36.0, H * 0.16)
    body_heights = [b[3] - b[1] for b in raw_cc if 6 <= (b[3] - b[1]) <= sample_cap]
    med_h = float(np.median(body_heights)) if body_heights else 14.0
    max_glyph_h = max(36.0, med_h * 2.8)
    square_cap = max(38.0, med_h * 2.6)
    all_cc = [
        b for b in raw_cc
        if (b[3] - b[1]) <= max_glyph_h
        and not ((b[2] - b[0]) > square_cap and (b[3] - b[1]) > square_cap)
    ]
    if not all_cc:
        return []
    min_body_h = max(5.0, med_h * 0.45)
    bodies = [b for b in all_cc if (b[3] - b[1]) >= min_body_h]
    dots = [b for b in all_cc if (b[3] - b[1]) < min_body_h]

    rows: list[list] = []
    for box in sorted(bodies, key=lambda b: (b[1] + b[3]) / 2.0):
        _, y1, _, y2, _ = box
        bh = y2 - y1
        cy = (y1 + y2) / 2.0
        best_r = None
        best_d = 1e9
        for r in rows:
            ry1, ry2, _ = r
            rh = ry2 - ry1
            d = abs((ry1 + ry2) / 2.0 - cy)
            if d <= max(4.0, min(rh, bh) * 0.48) and d < best_d:
                best_d = d
                best_r = r
        if best_r is None:
            rows.append([float(y1), float(y2), [box]])
        else:
            best_r[2].append(box)
            best_r[0] = float(np.median([c[1] for c in best_r[2]]))
            best_r[1] = float(np.median([c[3] for c in best_r[2]]))

    for box in dots:
        x1, y1, x2, y2, _ = box
        cy = (y1 + y2) / 2.0
        cx = (x1 + x2) / 2.0
        best_r = None
        best_d = 1e9
        for r in rows:
            ry1, ry2, rboxes = r
            rh = max(med_h, ry2 - ry1)
            if ry1 - rh * 0.70 <= cy <= ry2 + rh * 0.60:
                if any(c[0] - rh * 1.1 <= cx <= c[2] + rh * 1.1 for c in rboxes):
                    d = abs((ry1 + ry2) / 2.0 - cy)
                    if d < best_d:
                        best_d = d
                        best_r = r
        if best_r is not None:
            best_r[2].append(box)

    out_items: list[list] = []
    for _, _, rboxes in sorted(rows, key=lambda r: (r[0] + r[1]) / 2.0):
        rboxes.sort(key=lambda b: b[0])
        body_hs = [b[3] - b[1] for b in rboxes if (b[3] - b[1]) >= min_body_h]
        row_med_h = float(np.median(body_hs)) if body_hs else med_h
        gap_thr = max(9.0, row_med_h * 1.5)

        cells = []
        first = rboxes[0]
        cur = [first[0], first[1], first[2], first[3], [first]]
        for box in rboxes[1:]:
            if box[0] - cur[2] <= gap_thr:
                cur[0] = min(cur[0], box[0])
                cur[1] = min(cur[1], box[1])
                cur[2] = max(cur[2], box[2])
                cur[3] = max(cur[3], box[3])
                cur[4].append(box)
            else:
                cells.append(cur)
                cur = [box[0], box[1], box[2], box[3], [box]]
        cells.append(cur)

        for cx1, cy1, cx2, cy2, cboxes in cells:
            if len(cboxes) == 1 and (cx2 - cx1) < 5 and (cy2 - cy1) < 10:
                continue
            cell_h = max(row_med_h, cy2 - cy1)
            parts = _split_wide_boxes(cboxes, cell_h)
            part_texts: list[str] = []
            part_confs: list[float] = []
            for sub in parts:
                sx1 = max(0, min(b[0] for b in sub) - 1)
                sy1 = max(0, min(b[1] for b in sub) - 1)
                sx2 = min(W, max(b[2] for b in sub) + 1)
                sy2 = min(H, max(b[3] for b in sub) + 1)
                cell_crop = clean_img[sy1:sy2, sx1:sx2]
                txt, conf = rec.recognize_line(cell_crop, is_bgr=True)
                if (not txt.strip() or conf < 0.78) and is_low_contrast_or_blurry(cell_crop):
                    bin_crop = _binary_fallback_strip(cell_crop)
                    b_txt, b_conf = rec.recognize_line(bin_crop, is_bgr=True, normalize_gray=False)
                    if b_txt.strip() and b_conf > conf:
                        txt, conf = b_txt, b_conf
                txt = txt.strip()
                if txt:
                    part_texts.append(txt)
                    part_confs.append(conf)
            if not part_texts:
                continue
            merged_txt = " ".join(part_texts)
            avg_conf = float(sum(part_confs) / len(part_confs))
            if (len(cboxes) == 1 and avg_conf < 0.78 and not merged_txt.isdigit()) or (
                len(parts) >= 4 and avg_conf < 0.72
            ):
                continue

            pad_x = max(4, int(round(cell_h * 0.28)))
            pad_y = max(2, int(round(cell_h * 0.16)))
            bx1 = float(max(0, cx1 - pad_x))
            by1 = float(max(0, cy1 - pad_y))
            bx2 = float(min(W, cx2 + pad_x))
            by2 = float(min(H, cy2 + pad_y))
            quad = [[bx1, by1], [bx2, by1], [bx2, by2], [bx1, by2]]
            out_items.append([quad, merged_txt, avg_conf])

    return out_items


def read_document_text(
    image,
    recognizer: Optional[AsterONNXRecognizer] = None,
    *,
    is_bgr: bool = True,
) -> str:
    """Crop lines/cells from a document image with OpenCV and read via asterocr.AsterOCR."""
    img_bgr = _to_bgr_uint8(image, is_bgr=is_bgr)
    if img_bgr is None:
        return ""
    rec = recognizer or get_aster_recognizer()
    items = detect_document_boxes(img_bgr, recognizer=rec)
    if not items:
        return rec.predict(img_bgr, is_bgr=True)
    rows: list[list] = []
    for item in items:
        box = item[0]
        cy = sum(pt[1] for pt in box) / 4.0
        lx = min(pt[0] for pt in box)
        ht = max(pt[1] for pt in box) - min(pt[1] for pt in box)
        row = next((r for r in rows if abs(r[0] - cy) <= max(6.0, min(r[2], ht) * 0.48)), None)
        if row is None:
            row = [cy, [], ht]
            rows.append(row)
        row[1].append((lx, str(item[1])))
    return "\n".join(" ".join(val for _, val in sorted(cells)) for _, cells, _ in sorted(rows, key=lambda r: r[0]))
