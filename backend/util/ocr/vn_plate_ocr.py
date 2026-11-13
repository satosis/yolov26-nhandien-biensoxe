"""Lõi OCR biển số Việt Nam.

Mỗi crop đi qua đúng một chuỗi bước: cắt sát từng dòng chữ (plate_image.text_rows) → bộ nhận
dạng ASTER cho xác suất ký tự theo từng cột → giải mã theo cấu trúc biển số (plate_grammar).
Kết quả của một dòng chữ không phải một chuỗi mà là bằng chứng (ma trận xác suất), nên nhiều
khung hình của cùng một xe được gộp ở mức xác suất chứ không chỉ đếm phiếu chuỗi trùng nhau.
"""
from collections import OrderedDict
import hashlib
import logging
import re
import threading
from typing import Optional

import cv2
import numpy as np

from . import plate_grammar as grammar
from .aster_engine import AsterONNXRecognizer
from .constants import VALID_VN_PROVINCES
from .pipeline import _bgr
from .plate_image import line_strip, text_lines, text_rows

logger = logging.getLogger(__name__)

# Dưới mức này chuỗi tốt nhất gần như ngẫu nhiên: coi là chưa đọc được, không trả chữ.
MIN_CONFIDENCE = 0.40
# Xác suất trung bình mỗi ký tự của dòng khớp kém nhất: từ _CLEAR_LETTER trở lên là chữ khớp rõ
# với ảnh, từ _UNCLEAR_LETTER trở xuống là ảnh không ra chữ (đo trên bộ biển có đáp án: dưới 0,3
# hầu như không lần đọc nào đúng).
_CLEAR_LETTER = 0.50
_UNCLEAR_LETTER = 0.25
# Số ứng viên mỗi dòng đem ghép thành biển, và số ứng viên mỗi crop đem gộp nhiều khung.
_ROW_CANDIDATES = 6
_FUSION_CANDIDATES = 4


def _logsumexp(values) -> float:
    return float(np.logaddexp.reduce(np.fromiter(values, dtype=np.float64)))


class _Evidence(list):
    """Bằng chứng của một crop: ma trận xác suất từng dòng, kèm các biển ứng viên đã giải mã (tính một lần)."""
    plates = None


class VNPlateOCR:
    """Đọc biển số từ crop của bộ phát hiện (có đệm viền)."""

    def __init__(self, native_threads: Optional[int] = None):
        self._recognizer = AsterONNXRecognizer(native_threads=native_threads)
        self._vocabulary = self._recognizer.vocabulary
        self._evidence: OrderedDict = OrderedDict()
        self._evidence_lock = threading.Lock()
        logger.info("AsterOCR plate OCR initialised (native_threads=%s)", self._recognizer.native_threads)

        # Suffix pattern: 4-5 digits, with or without dots (e.g. 123.45, 12.34, 12345, 1234)
        _suffix = r"(?:[0-9]{3}\.[0-9]{2}|[0-9]{2}\.[0-9]{2}|[0-9]{4,5})"
        # 1. Standard: 29A-123.45, 51B-1234, 51LD-123.45, 29A1-123.45
        self.vn_civ_pattern = re.compile(rf"^[0-9]{{2}}[A-Z]{{1,2}}[0-9]?-?{_suffix}$")
        # 2. Temporary: T60-619.56
        self.vn_temp_pattern = re.compile(rf"^T[0-9]{{2}}-?{_suffix}$")
        # 3. Military: TM-12.34, AA-1234
        self.vn_mil_pattern = re.compile(rf"^[A-Z]{{2}}-?{_suffix}$")

    def is_valid_vn_plate(self, text: str) -> bool:
        """Check if cleaned plate matches standard Vietnamese formats."""
        if not text or len(text) < 6:
            return False
        clean = text.strip().upper()
        if self.vn_civ_pattern.match(clean):
            return True
        if self.vn_temp_pattern.match(clean):
            return True
        if self.vn_mil_pattern.match(clean):
            return True
        # Check valid province + valid suffix (must have series letter(s) between province code and numbers)
        alphanumeric = re.sub(r"[^A-Z0-9]", "", clean)
        if len(alphanumeric) >= 7 and alphanumeric[:2].isdigit():
            if int(alphanumeric[:2]) in VALID_VN_PROVINCES:
                series = alphanumeric[2:-5] if len(alphanumeric) >= 7 else alphanumeric[2:-4]
                if series and any(c.isalpha() for c in series):
                    if re.search(r"[0-9]{4,5}$", alphanumeric):
                        return True
        return False

    def detect_plate_type(self, crop: np.ndarray) -> str:
        """Detect plate background color: white, yellow, blue, red."""
        h, w = crop.shape[:2]
        cy, cx = h // 2, w // 2
        dy, dx = max(1, h // 4), max(1, w // 4)
        roi = crop[cy - dy:cy + dy, cx - dx:cx + dx]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        avg_h = np.mean(hsv[:, :, 0])
        avg_s = np.mean(hsv[:, :, 1])

        if 10 <= avg_h <= 45 and avg_s > 70:
            return "yellow"
        if 85 <= avg_h <= 140 and avg_s > 50:
            return "blue"
        if (avg_h <= 12 or avg_h >= 160) and avg_s > 50:
            return "red"
        return "white"

    def _frames(self, row: np.ndarray) -> np.ndarray:
        """Xác suất ký hiệu biển số theo từng cột của một dòng chữ đã cắt sát."""
        return grammar.emissions(self._recognizer.line_log_probs(line_strip(row)), self._vocabulary)

    def evidence(self, crop: np.ndarray) -> list[np.ndarray]:
        """Bằng chứng đọc của một crop: ma trận xác suất của từng dòng chữ (1 hoặc 2 dòng).

        Tính một lần cho mỗi crop (bộ nhớ đệm nhỏ) vì cùng crop được đọc lại khi gộp nhiều khung.
        """
        image = np.ascontiguousarray(_bgr(crop))
        key = (image.shape, hashlib.blake2b(memoryview(image), digest_size=16).digest())
        with self._evidence_lock:
            if key in self._evidence:
                self._evidence.move_to_end(key)
                return self._evidence[key]
        rows = _Evidence(self._frames(row) for row in text_rows(image))
        with self._evidence_lock:
            self._evidence[key] = rows
            if len(self._evidence) > 96:
                self._evidence.popitem(last=False)
        return rows

    @staticmethod
    def _plates(rows: list[np.ndarray]) -> dict[tuple[str, str], float]:
        """Các biển ứng viên (đầu, số) của một crop kèm log xác suất, cách viết trùng nhau được gộp."""
        if getattr(rows, "plates", None) is not None:
            return rows.plates
        plates: dict[tuple[str, str], float] = {}
        if len(rows) == 2:
            tops = grammar.decode(rows[0], grammar.TOP_ROW, limit=_ROW_CANDIDATES)
            bottoms = grammar.decode(rows[1], grammar.BOTTOM_ROW, limit=_ROW_CANDIDATES)
            pairs = [(top, bottom, top_score + bottom_score)
                     for top, _, top_score in tops for bottom, _, bottom_score in bottoms]
        elif rows:
            pairs = [(text[:split], text[split:], score)
                     for text, split, score in grammar.decode(rows[0], grammar.ONE_ROW, limit=2 * _ROW_CANDIDATES)]
        else:
            pairs = []
        for prefix, number, score in pairs:
            key = (prefix.replace("-", ""), number.replace(".", ""))
            plates[key] = float(np.logaddexp(plates.get(key, -np.inf), score))
        if isinstance(rows, _Evidence):
            rows.plates = plates
        return plates

    @staticmethod
    def _one_row_spellings(plate: tuple[str, str]) -> list[str]:
        prefixes, numbers = grammar.spellings(*plate)
        return [prefix + dash + number for prefix in prefixes for dash in ("", "-") for number in numbers]

    @classmethod
    def _support(cls, plate: tuple[str, str], rows: list[np.ndarray]) -> float:
        """Điểm của một biển theo bằng chứng của một crop, cùng thang với plate_grammar.decode.

        Cộng trên các cách viết có/không gạch, chấm; gồm cả tiên nghiệm dạng biển.
        """
        def score(frames, automaton, texts):
            scores = [grammar.likelihood(frames, text) + automaton.prior(text)
                      for text in texts if automaton.prior(text) > -np.inf]
            return _logsumexp(scores) if scores else -np.inf

        if len(rows) == 2:
            prefixes, numbers = grammar.spellings(*plate)
            return score(rows[0], grammar.TOP_ROW, prefixes) + score(rows[1], grammar.BOTTOM_ROW, numbers)
        return score(rows[0], grammar.ONE_ROW, cls._one_row_spellings(plate))

    @classmethod
    def _letters(cls, plate: tuple[str, str], rows: list[np.ndarray]) -> float:
        """Log xác suất trung bình mỗi ký tự của dòng khớp kém nhất, chỉ tính chính các ký tự của biển."""
        def per_letter(frames, automaton, texts, count):
            scores = [grammar.likelihood(frames, text, filler=0.)
                      for text in texts if automaton.prior(text) > -np.inf]
            return min(0., _logsumexp(scores) / count) if scores else -np.inf

        if len(rows) == 2:
            prefixes, numbers = grammar.spellings(*plate)
            return min(per_letter(rows[0], grammar.TOP_ROW, prefixes, len(plate[0])),
                       per_letter(rows[1], grammar.BOTTOM_ROW, numbers, len(plate[1])))
        return per_letter(rows[0], grammar.ONE_ROW, cls._one_row_spellings(plate), len(plate[0]) + len(plate[1]))

    def _judge(self, plates: dict[tuple[str, str], float], samples: list[list[np.ndarray]]) -> tuple[str, float, list]:
        """Chọn biển tốt nhất và độ tin cậy: (chuỗi chuẩn, độ tin cậy, bảng xếp hạng).

        Độ tin cậy là phần xác suất của biển tốt nhất so với mọi ứng viên đúng cấu trúc, giảm
        dần về 0 khi dòng khớp kém nhất của nó không ra chữ: ảnh không phải biển số thì ứng viên
        nào cũng khớp kém, và một dòng đọc rõ không bù được cho dòng kia. Nhiều crop thì lấy
        trung bình bằng chứng: các khung gần giống nhau của xe đứng yên không nhân bội thành chắc chắn.
        """
        if not plates:
            return "", 0.0, []
        ranked = sorted(plates.items(), key=lambda item: -item[1])
        best, best_score = ranked[0]
        share = float(np.exp(best_score - _logsumexp(score for _, score in ranked)))
        letters = float(np.exp(np.mean([self._letters(best, rows) for rows in samples])))
        confidence = share * float(np.clip((letters - _UNCLEAR_LETTER) / (_CLEAR_LETTER - _UNCLEAR_LETTER), 0., 1.))
        return grammar.canonical(*best), confidence, [(grammar.canonical(*plate), score) for plate, score in ranked[:5]]

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        """Cửa vào OCR biển số duy nhất cho mọi luồng: (chuỗi, độ tin cậy); chuỗi rỗng khi chưa đọc được."""
        rows = self.evidence(crop)
        text, confidence, ranked = self._judge(self._plates(rows), [rows])
        logger.debug("[PlateOCR] '%s' conf=%.2f rows=%d candidates=%s", text, confidence, len(rows), ranked)
        return (text, confidence) if confidence >= MIN_CONFIDENCE else ("", 0.0)

    def read_many(self, crops: list[np.ndarray]) -> tuple[str, float]:
        """Gộp bằng chứng của nhiều crop cùng một biển: (chuỗi, độ tin cậy).

        Mỗi ứng viên được chấm bằng trung bình log xác suất trên các crop có cùng số dòng chữ.
        """
        samples = [rows for rows in (self.evidence(crop) for crop in crops) if rows]
        if not samples:
            return "", 0.0
        layout = max((1, 2), key=lambda count: sum(len(rows) == count for rows in samples))
        samples = [rows for rows in samples if len(rows) == layout]
        candidates: set[tuple[str, str]] = set()
        for rows in samples:
            plates = self._plates(rows)
            candidates.update(sorted(plates, key=plates.get, reverse=True)[:_FUSION_CANDIDATES])
        fused = {plate: float(np.mean([self._support(plate, rows) for rows in samples])) for plate in candidates}
        text, confidence, _ = self._judge(fused, samples)
        return (text, confidence) if confidence >= MIN_CONFIDENCE else ("", 0.0)

    def spot(self, region: np.ndarray, plate_height: float) -> tuple[str, float]:
        """Tìm một chuỗi biển số viết trên thân xe trong vùng quanh biển: (chuỗi, độ tin cậy).

        Xe tải, xe khách, xe van phải kẻ biển số trên thân xe; chữ kẻ lớn hơn biển nên còn đọc
        được khi biển quá nhỏ. Mỗi dòng chữ trong vùng được giải mã như biển một dòng, phần còn
        lại của dòng (đèn, mép xe) bị bỏ qua.
        """
        best = ("", 0.0)
        for line in text_lines(region, plate_height):
            rows = [self._frames(line)]
            text, confidence, _ = self._judge(self._plates(rows), [rows])
            if confidence > best[1]:
                best = (text, confidence)
        return best if best[1] >= MIN_CONFIDENCE else ("", 0.0)
