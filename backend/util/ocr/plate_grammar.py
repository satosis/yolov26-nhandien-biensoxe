"""Cấu trúc biển số Việt Nam dưới dạng automat ký tự, và giải mã CTC bám theo automat đó.

Bộ nhận dạng dòng chữ trả về xác suất từng ký tự theo từng cột ảnh. Thay vì lấy ký tự mạnh nhất
rồi sửa lại bằng luật thay chữ, ở đây ta tìm chuỗi có xác suất cao nhất trong số các chuỗi đúng
cấu trúc biển số: vị trí mã tỉnh chỉ xét mã tỉnh có thật, vị trí seri chỉ xét chữ, phần số chỉ xét
số. Các hình dạng mà phông biển số không phân biệt được (8/B, 0/D/O, 5/S...) góp xác suất cho
nhau, việc chọn chữ hay số do vị trí trong biển quyết định.

Không phụ thuộc mô hình hay OpenCV: chỉ nhận ma trận log xác suất (cột 0 là blank).
"""
from __future__ import annotations

import math
import unicodedata

import numpy as np

from .constants import VALID_VN_PROVINCES

DIGITS = "0123456789"
# Chữ seri đang dùng trên biển dân sự (không có I, J, O, Q, W); R là rơ-moóc.
SERIES = "ABCDEFGHKLMNPRSTUVXYZ"
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Cột phát xạ: 0 là blank (gồm cả dấu cách), sau đó là các ký hiệu này.
SYMBOLS = DIGITS + _LETTERS + "-."
_COLUMN = {symbol: index + 1 for index, symbol in enumerate(SYMBOLS)}
# Hình dạng mà phông biển số ở độ phân giải thấp không phân biệt được: ký hiệu -> các ký tự trông giống.
# Ký tự trông giống chỉ góp một phần xác suất: ở vị trí chỉ nhận số (hoặc chỉ nhận chữ) nó vẫn được
# hiểu đúng, còn ở vị trí nhận cả hai thì lựa chọn của mô hình vẫn thắng.
_LOOKALIKE_SHARE = .1
_LOOKALIKES = {
    "0": "ODQo", "1": "Il", "2": "Zz", "4": "A", "5": "Ss", "6": "Gb", "8": "B", "9": "gq",
    "D": "0OQ", "Z": "2", "A": "4", "S": "5", "G": "6", "B": "8",
}
# Chữ thường có hình giống chữ hoa (biển số chỉ có chữ hoa).
_SAME_SHAPE_LOWER = "ckopsuvwxyz"
# Cột ảnh không thuộc biển (viền, thân xe, vật khác): hoặc trống thật, hoặc chịu mức phạt này.
_FILLER = math.log(.5)
# Tiên nghiệm về dạng biển (log xác suất): seri hai chữ (LD, KT...) hiếm hơn seri một chữ; số 4
# chữ số là biển đời cũ, ít hơn số 5 chữ số.
_TWO_LETTER_SERIES = math.log(.2)
_FOUR_DIGITS = math.log(.3)
_EMISSION_CACHE: dict[tuple, np.ndarray] = {}


class PlateAutomaton:
    """Automat tất định: arcs[trạng thái] = [(ký hiệu, trạng thái đích, log trọng số)].

    accepting[trạng thái] là log trọng số khi kết thúc ở trạng thái đó.
    """

    def __init__(self):
        self.arcs: list[list[tuple[str, int, float]]] = [[]]
        self.accepting: dict[int, float] = {}
        self.number_start = -1      # trạng thái của chữ số đầu tiên trong phần số

    def state(self) -> int:
        self.arcs.append([])
        return len(self.arcs) - 1

    def arc(self, source: int, symbols: str, target: int, weight: float = 0.) -> None:
        self.arcs[source].extend((symbol, target, weight) for symbol in symbols)

    def prior(self, text: str) -> float:
        """Log tiên nghiệm của một chuỗi theo automat; -inf nếu chuỗi không được chấp nhận."""
        node, total = 0, 0.
        for symbol in text:
            step = next(((target, weight) for arc, target, weight in self.arcs[node] if arc == symbol), None)
            if step is None:
                return -math.inf
            node, total = step[0], total + step[1]
        return total + self.accepting.get(node, -math.inf)


def _prefix(automaton: PlateAutomaton, *, two_rows: bool) -> list[int]:
    """Phần đầu biển dân sự: mã tỉnh + seri.

    Biển hai dòng gồm cả biển xe máy nên seri có thể kèm một chữ số và có gạch sau mã tỉnh
    (29-B1, 29B1, 30A, 51LD). Biển một dòng là biển ô tô: seri chỉ có chữ (30A, 51LD).
    Biển tạm, biển quân đội, ngoại giao không nằm trong automat: dạng ngắn và hiếm đó khiến ảnh
    không ra chữ cũng giải mã được thành một biển hợp lệ.
    """
    after_province = automaton.state()
    for first in DIGITS:
        seconds = "".join(digit for digit in DIGITS if int(first + digit) in VALID_VN_PROVINCES)
        if seconds:
            middle = automaton.state()
            automaton.arc(0, first, middle)
            automaton.arc(middle, seconds, after_province)
    series, extended = automaton.state(), automaton.state()
    automaton.arc(after_province, SERIES, series)
    automaton.arc(series, SERIES, extended, _TWO_LETTER_SERIES)
    if two_rows:
        dashed = automaton.state()
        automaton.arc(after_province, "-", dashed)
        automaton.arc(dashed, SERIES, series)
        automaton.arc(series, DIGITS, extended)
    return [series, extended]


def _number(automaton: PlateAutomaton, starts: list[int]) -> None:
    """Phần số: 123.45, 12345 hoặc 1234."""
    first, second, third = automaton.state(), automaton.state(), automaton.state()
    automaton.number_start = first
    for start in starts:
        automaton.arc(start, DIGITS, first)
    automaton.arc(first, DIGITS, second)
    automaton.arc(second, DIGITS, third)
    fourth, fifth = automaton.state(), automaton.state()
    automaton.arc(third, DIGITS, fourth)
    automaton.arc(fourth, DIGITS, fifth)
    dotted, dotted_fourth = automaton.state(), automaton.state()
    automaton.arc(third, ".", dotted)
    automaton.arc(dotted, DIGITS, dotted_fourth)
    automaton.arc(dotted_fourth, DIGITS, fifth)
    automaton.accepting.update({fourth: _FOUR_DIGITS, fifth: 0.})


def _top_row() -> PlateAutomaton:
    automaton = PlateAutomaton()
    automaton.accepting.update(dict.fromkeys(_prefix(automaton, two_rows=True), 0.))
    return automaton


def _bottom_row() -> PlateAutomaton:
    automaton = PlateAutomaton()
    _number(automaton, [0])
    return automaton


def _one_row() -> PlateAutomaton:
    automaton = PlateAutomaton()
    ends = _prefix(automaton, two_rows=False)
    dash = automaton.state()
    for end in ends:
        automaton.arc(end, "-", dash)
    _number(automaton, ends + [dash])
    return automaton


TOP_ROW, BOTTOM_ROW, ONE_ROW = _top_row(), _bottom_row(), _one_row()


def canonical(prefix: str, number: str) -> str:
    """Dạng chuẩn của một biển: 30A-123.45, 29B1-123.45, 51B-1234."""
    digits = number.replace(".", "")
    return f"{prefix.replace('-', '')}-{digits[:3]}.{digits[3:]}" if len(digits) == 5 else f"{prefix.replace('-', '')}-{digits}"


def spellings(prefix: str, number: str) -> tuple[list[str], list[str]]:
    """Các cách một biển dạng chuẩn hiện ra trên dòng chữ: có hoặc không có gạch, chấm."""
    prefixes = [prefix, f"{prefix[:2]}-{prefix[2:]}"] if prefix[:2].isdigit() else [prefix]
    return prefixes, ([number, f"{number[:3]}.{number[3:]}"] if len(number) == 5 else [number])


def emissions(log_probs: np.ndarray, vocabulary: tuple) -> np.ndarray:
    """Đổi ma trận log xác suất theo bảng chữ của mô hình sang các cột SYMBOLS (cột 0 là blank).

    Ký tự có dấu gộp về chữ gốc, chữ thường cùng hình gộp về chữ hoa, hình giống nhau góp một
    phần cho cả chữ lẫn số. Dấu cách tính như blank.
    """
    matrix = _EMISSION_CACHE.get(vocabulary)
    if matrix is None:
        matrix = np.zeros((len(vocabulary), len(SYMBOLS) + 1), dtype=np.float64)
        matrix[0, 0] = 1.
        for index, char in enumerate(vocabulary[1:], start=1):
            if char == " ":
                matrix[index, 0] = 1.
                continue
            base = {"đ": "d", "Đ": "D", ",": ".", ":": ".", "_": "-"}.get(char, unicodedata.normalize("NFD", char)[0])
            for symbol, shapes in _LOOKALIKES.items():
                if base in shapes:
                    matrix[index, _COLUMN[symbol]] = _LOOKALIKE_SHARE
            if base in SYMBOLS:
                matrix[index, _COLUMN[base]] = 1.
            elif base in _SAME_SHAPE_LOWER:
                matrix[index, _COLUMN[base.upper()]] = 1.
        _EMISSION_CACHE[vocabulary] = matrix
    log_probs = log_probs - np.logaddexp.reduce(log_probs, axis=1, keepdims=True)
    return np.log(np.exp(log_probs) @ matrix + 1e-12)


def _compact(frames: np.ndarray) -> np.ndarray:
    """Gộp các cột chắc chắn trống liền nhau thành một cột (vẫn tách được hai ký tự lặp)."""
    blank = frames[:, 0] > -.01
    keep = np.ones(len(frames), dtype=bool)
    keep[1:] = ~(blank[1:] & blank[:-1])
    return frames[keep]


def _add(first: float, second: float) -> float:
    """log(e^first + e^second) trên số thực Python (nhanh hơn numpy nhiều lần khi gọi từng cặp số)."""
    if first < second:
        first, second = second, first
    return first if second == -math.inf else first + math.log1p(math.exp(second - first))


def decode(frames: np.ndarray, automaton: PlateAutomaton, *, beam: int = 16, limit: int = 8) -> list[tuple[str, int, float]]:
    """Các chuỗi automat chấp nhận: [(chuỗi, vị trí bắt đầu phần số hoặc -1, log điểm)], tốt nhất trước.

    frames là kết quả của emissions(). Điểm = log P(các cột | chuỗi) + log tiên nghiệm của dạng
    biển. Xác suất cộng trên mọi cách dóng CTC; các cột trước ký tự đầu và sau ký tự cuối là
    phần không thuộc biển (trống hoặc chịu phạt _FILLER).
    """
    negative = -math.inf
    root = (0, "", -1)
    beams: dict[tuple[int, str, int], tuple[float, float]] = {root: (0., negative)}
    finished: dict[tuple[str, int], float] = {}
    for frame in _compact(frames).tolist():
        blank = frame[0]
        filler = max(blank, _FILLER)
        grown: dict[tuple[int, str, int], list[float]] = {}
        for key in finished:
            finished[key] += filler
        for (node, text, split), (p_blank, p_char) in beams.items():
            total = _add(p_blank, p_char)
            entry = grown.setdefault((node, text, split), [negative, negative])
            if text:
                entry[0] = _add(entry[0], total + blank)
                entry[1] = _add(entry[1], p_char + frame[_COLUMN[text[-1]]])
                if node in automaton.accepting and p_char > negative:
                    finished[(text, split)] = _add(finished.get((text, split), negative),
                                                   p_char + filler + automaton.accepting[node])
            else:
                entry[0] = _add(entry[0], total + filler)
            last = text[-1:]
            for symbol, target, weight in automaton.arcs[node]:
                score = (p_blank if symbol == last else total) + frame[_COLUMN[symbol]] + weight
                if score > negative:
                    key = (target, text + symbol, len(text) if target == automaton.number_start else split)
                    entry = grown.setdefault(key, [negative, negative])
                    entry[1] = _add(entry[1], score)
        ranked = sorted(grown.items(), key=lambda item: -_add(item[1][0], item[1][1]))
        beams = {key: (value[0], value[1]) for key, value in ranked[:beam]}
        beams.setdefault(root, tuple(grown[root]))
    for (node, text, split), (_, p_char) in beams.items():
        if node in automaton.accepting and p_char > negative:
            finished[(text, split)] = _add(finished.get((text, split), negative), p_char + automaton.accepting[node])
    return [(text, split, score) for (text, split), score in sorted(finished.items(), key=lambda item: -item[1])[:limit]]


def likelihood(frames: np.ndarray, text: str, *, filler: float = _FILLER) -> float:
    """log P(chuỗi | các cột), cùng mô hình với decode(). filler=0 bỏ qua phần không thuộc biển."""
    labels = [_COLUMN[text[0]]]
    for symbol in text[1:]:
        labels += [0, _COLUMN[symbol]]
    labels = np.array(labels)
    skip = np.zeros(len(labels), dtype=bool)
    skip[2:] = (labels[2:] != 0) & (labels[2:] != labels[:-2])
    alpha = np.full(len(labels), -np.inf)
    before, after = 0., -np.inf
    for frame in _compact(frames):
        cost = max(float(frame[0]), filler)
        after = np.logaddexp(after, alpha[-1]) + cost
        moved = np.logaddexp(alpha, np.concatenate(([before], alpha[:-1])))
        moved = np.logaddexp(moved, np.where(skip, np.concatenate(([-np.inf, -np.inf], alpha[:-2])), -np.inf))
        alpha = moved + frame[labels]
        before += cost
    return float(np.logaddexp(after, alpha[-1]))
