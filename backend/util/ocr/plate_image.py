"""Ảnh biển số: khoanh mặt biển, nắn thẳng, cắt từng dòng chữ, ảnh hiển thị.

Đây là nơi duy nhất biến đổi ảnh biển số. Lõi OCR (vn_plate_ocr), luồng nhận diện
(backend/ai/plate_analysis) và mọi chỗ hiển thị/lưu ảnh biển số đều gọi các hàm này.

Crop từ bộ phát hiện có đệm viền nên chứa cả thân xe quanh biển. OCR không đọc cả crop: mặt biển
được khoanh theo mép (plate_face) và nắn thẳng bằng một lần warp từ crop gốc, rồi từng dòng chữ
được cắt sát (text_rows) và đưa về đúng chiều cao của bộ nhận dạng (line_strip). Ảnh hiển thị
chính là mặt biển đó.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from .aster_engine import normalize_grayscale_strip
from .pipeline import _bgr, enhance_ocr_image

# Chiều cao làm việc của crop biển 1 dòng và crop có thể là biển 2 dòng.
ONE_ROW_HEIGHT = 80
TWO_ROW_HEIGHT = 160
# Crop rất nhỏ không có thêm chi tiết để phóng: giới hạn phóng to.
MAX_UPSCALE = 8.0
# Rộng/cao nhỏ hơn mức này thì có thể là biển 2 dòng; dòng thật do khe trống giữa hai hàng chữ quyết định.
TWO_ROW_ASPECT = 2.6


def _resize(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    interpolation = cv2.INTER_LANCZOS4 if size[1] > image.shape[0] else cv2.INTER_AREA
    return cv2.resize(image, size, interpolation=interpolation)


def normalize(crop: np.ndarray) -> tuple[np.ndarray, float]:
    """Đưa crop về chiều cao làm việc bằng một lần resize. Trả về (ảnh BGR, tỉ lệ so với crop gốc)."""
    image = _bgr(crop)
    height, width = image.shape[:2]
    target = TWO_ROW_HEIGHT if width / height < TWO_ROW_ASPECT else ONE_ROW_HEIGHT
    scale = min(target / height, MAX_UPSCALE)
    if scale == 1.0:
        return image, 1.0
    return _resize(image, (max(8, round(width * scale)), max(8, round(height * scale)))), scale


def enhance(image: np.ndarray) -> np.ndarray:
    """Tăng tương phản cục bộ và làm nét nhẹ một lần, không đổi kích thước."""
    return enhance_ocr_image(image, clip_limit=2.0)


def _dark_background(gray: np.ndarray) -> bool:
    """Biển nền tối chữ sáng (xanh, đỏ): phần lớn điểm ảnh giữa crop nằm ở phía tối."""
    height, width = gray.shape
    center = gray[round(height * .2):round(height * .8), round(width * .2):round(width * .8)]
    low, middle, high = np.percentile(center, (10, 50, 90))
    return (middle - low) < (high - middle)


def _ink(gray: np.ndarray) -> np.ndarray:
    """Mặt nạ nét tối (1) trên nền sáng hơn quanh nó."""
    low, high = np.percentile(gray, (5, 95))
    block = max(9, round(gray.shape[0] * .30) | 1)
    return cv2.adaptiveThreshold(gray, 1, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                 block, max(4., .12 * (high - low)))


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Các đoạn [đầu, cuối) liên tiếp True của một mảng 1 chiều."""
    edges = np.flatnonzero(np.diff(np.concatenate(([False], flags, [False])).astype(np.int8)))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _face_quad(gray: np.ndarray, ink: np.ndarray) -> Optional[np.ndarray]:
    """Bốn góc mặt biển: vùng sáng như giữa biển, bị mép biển (viền hoặc thân xe) khép kín."""
    height, width = gray.shape
    middle = (slice(round(height * .3), round(height * .7)), slice(round(width * .3), round(width * .7)))
    text_level, face_level = np.percentile(gray[middle], (5, 60))
    closed = cv2.dilate(ink, np.ones((5, 5), np.uint8))
    free = ((gray > face_level - .4 * (face_level - text_level)) & (closed == 0)).astype(np.uint8)
    _, labels, _, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    ids, counts = np.unique(labels[middle][labels[middle] > 0], return_counts=True)
    if not len(ids):
        return None
    mask = (labels == ids[np.argmax(counts)]).astype(np.uint8)
    if mask[0].sum() + mask[-1].sum() + mask[:, 0].sum() + mask[:, -1].sum() > .04 * 2 * (height + width):
        return None  # mép biển hở: vùng sáng tràn ra thân xe
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    perimeter = cv2.arcLength(hull, True)
    quad = None
    for epsilon in (.02, .03, .045, .06, .08):
        approx = cv2.approxPolyDP(hull, epsilon * perimeter, True)
        if len(approx) == 4:
            quad = np.float32(approx.reshape(4, 2))
            break
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull))
    sums, differences = quad.sum(axis=1), quad[:, 0] - quad[:, 1]
    quad = np.float32((quad[np.argmin(sums)], quad[np.argmax(differences)],
                       quad[np.argmax(sums)], quad[np.argmin(differences)]))
    if cv2.contourArea(quad) < .12 * height * width or cv2.contourArea(hull) < .8 * cv2.contourArea(quad):
        return None
    # Bước khép mép đã lấn 2px mỗi phía của mặt biển.
    center = quad.mean(axis=0)
    return center + (quad - center) * (1 + 4 / max(8., float(np.linalg.norm(quad - center, axis=1).mean())))


def level(crop: np.ndarray) -> np.ndarray:
    """Xoay crop để các nét ngang (mép biển, hàng chữ, nẹp cản xe) nằm ngang.

    Camera cầm tay hay đặt nghiêng làm biển lệch tới vài chục độ, quá tầm của bước dò hàng chữ.
    Góc lấy theo hướng chiếm ưu thế của các cạnh gần ngang (trong ±45°); lệch dưới 4° thì giữ nguyên.
    """
    image = _bgr(crop)
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if max(height, width) > 160:
        gray = cv2.resize(gray, None, fx=160 / max(height, width), fy=160 / max(height, width), interpolation=cv2.INTER_AREA)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    # Hướng của cạnh vuông góc với gradient; chỉ lấy cạnh gần ngang (gradient gần dọc).
    angles = np.degrees(np.arctan2(-gx * np.sign(gy), np.abs(gy)))
    weights = np.hypot(gx, gy) * (np.abs(gy) > np.abs(gx))
    histogram, _ = np.histogram(angles, bins=91, range=(-45.5, 45.5), weights=weights)
    histogram = np.convolve(histogram, np.ones(5), mode="same")
    angle = float(np.argmax(histogram) - 45)
    if abs(angle) < 4:
        return image
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _locate(crop: np.ndarray):
    """(crop BGR chữ tối nền sáng, ảnh xám làm việc, tỉ lệ, bốn góc mặt biển hoặc None)."""
    source = level(crop)
    image, scale = normalize(source)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if _dark_background(gray):
        source, image, gray = 255 - source, 255 - image, 255 - gray
    return source, image, gray, scale, _face_quad(gray, _ink(gray))


def _warp(source: np.ndarray, quad: np.ndarray, scale: float) -> np.ndarray:
    """Nắn mặt biển bằng một lần warp từ crop gốc, giữ kích thước làm việc và tỉ lệ thật của biển."""
    top, right = np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[1])
    bottom, left = np.linalg.norm(quad[2] - quad[3]), np.linalg.norm(quad[3] - quad[0])
    width, height = max(8, round(max(top, bottom))), max(8, round(max(left, right)))
    target = np.float32(((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)))
    return cv2.warpPerspective(source, cv2.getPerspectiveTransform(quad / scale, target), (width, height),
                               flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)


def _face_rows(face: np.ndarray) -> list[np.ndarray]:
    """Dòng chữ trên mặt biển đã nắn: bỏ nét dính mép, tách hai hàng ở khe trống gần giữa."""
    ink = _ink(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY))
    height, width = ink.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if x == 0 or y == 0 or x + w == width or y + h == height or area < 4:
            ink[labels == label] = 0
    profile = ink.sum(axis=1).astype(np.float64)
    spans = [(0, height)]
    if profile.any() and width / height < TWO_ROW_ASPECT:
        low, high = round(height * .3), round(height * .7)
        cut = low + int(np.argmin(profile[low:high]))
        if profile[cut] <= .15 * profile.max() and profile[:cut].any() and profile[cut:].any():
            spans = [(0, cut), (cut, height)]
    rows = []
    for top, bottom in spans:
        part = ink[top:bottom]
        ys, xs = np.flatnonzero(part.any(axis=1)), np.flatnonzero(part.any(axis=0))
        if not len(ys):
            continue
        size = ys[-1] - ys[0] + 1
        pad_y, pad_x = max(1, round(size * .12)), max(2, round(size * .18))
        rows.append(face[max(0, top + ys[0] - pad_y):min(height, top + ys[-1] + 1 + pad_y),
                         max(0, xs[0] - pad_x):min(width, xs[-1] + 1 + pad_x)])
    return rows


def _shear(image: np.ndarray, slope: float, interpolation: int = cv2.INTER_LINEAR) -> np.ndarray:
    height, width = image.shape[:2]
    matrix = np.float32([[1, 0, 0], [slope, 1, -slope * width / 2]])
    return cv2.warpAffine(image, matrix, (width, height), flags=interpolation, borderMode=cv2.BORDER_REPLICATE)


def _bands(ink: np.ndarray, min_height: float) -> list[tuple[int, int, float]]:
    """Các dải hàng có ít nhất ba nét cắt ngang (hàng chữ): [(đầu, cuối, tổng số nét)]."""
    strokes = (np.diff(ink.astype(np.int8), axis=1) == 1).sum(axis=1) + ink[:, 0]
    smooth = np.convolve(strokes, np.ones(3) / 3, mode="same")
    merged: list[list[int]] = []
    for start, end in _runs(smooth >= 2.6):
        if merged and start - merged[-1][1] <= max(1, round(ink.shape[0] * .03)):
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end, float(strokes[start:end].sum())) for start, end in merged if end - start >= min_height]


def _columns(ink: np.ndarray, start: int, end: int) -> tuple[int, int]:
    """Phạm vi ngang của chữ trong một dải: nằm giữa các vách (mép biển, thân xe tối)."""
    height, width = ink.shape
    size = end - start
    walls = ink[max(0, start - round(size * .3)):min(height, end + round(size * .3))].mean(axis=0) >= .85
    text = ink[start:end].any(axis=0) & ~walls
    spans = [(int(text[left:right].sum()), left, right) for left, right in _runs(~walls)]
    mass, left, right = max(spans, default=(0, 0, width))
    if mass == 0:
        return 0, width
    columns = np.flatnonzero(text[left:right])
    margin = max(2, round(size * .18))
    return max(left, left + int(columns[0]) - margin), min(right, left + int(columns[-1]) + 1 + margin)


def _band_rows(image: np.ndarray, gray: np.ndarray) -> list[np.ndarray]:
    """Dòng chữ khi không khoanh được mặt biển (biển quá nhỏ, mép lẫn vào thân xe)."""
    ink = _ink(gray)
    height, width = ink.shape
    bands = _bands(ink, max(4, round(height * .10)))
    if not bands:
        return []
    chosen = [max(bands, key=lambda band: band[2])]
    if width / height < TWO_ROW_ASPECT:
        pairs = []
        for upper, lower in zip(bands, bands[1:]):
            small, large = sorted((upper[1] - upper[0], lower[1] - lower[0]))
            if small >= .45 * large and lower[0] - upper[1] <= .8 * large:
                pairs.append((upper[2] + lower[2], upper, lower))
        if pairs:
            chosen = list(max(pairs)[1:])
        elif chosen[0][1] - chosen[0][0] > height * .5:
            # Một dải cao chứa cả hai hàng: cắt ở hàng ít nét nhất gần giữa dải.
            start, end, _ = chosen[0]
            profile = ink[start:end].sum(axis=1).astype(np.float64)
            low, high = round((end - start) * .35), round((end - start) * .65)
            cut = low + int(np.argmin(profile[low:high]))
            if profile[cut] <= .6 * profile.mean():
                chosen = [(start, start + cut, 0.), (start + cut + 1, end, 0.)]
    rows = []
    for start, end, _ in chosen:
        margin = max(1, round((end - start) * .12))
        left, right = _columns(ink, start, end)
        rows.append(image[max(0, start - margin):min(height, end + margin), left:right])
    return rows


def text_rows(crop: np.ndarray) -> list[np.ndarray]:
    """Các dòng chữ của biển trong crop: 1 hoặc 2 ảnh BGR cắt sát, chữ tối trên nền sáng.

    Khoanh được mặt biển thì nắn thẳng rồi cắt dòng trên mặt biển; không thì tìm thẳng các dải
    chữ trên crop. Danh sách rỗng khi crop không có hàng chữ nào.
    """
    source, image, gray, scale, quad = _locate(crop)
    if quad is not None:
        rows = _face_rows(_warp(source, quad, scale))
        if rows:
            return rows
    return _band_rows(image, gray)


def _glyph_chains(glyphs: list[tuple[int, int, int, int]]) -> list[list[tuple[int, int, int, int]]]:
    """Xâu các nét cỡ ký tự (x, y, rộng, cao) nằm cùng hàng, cao gần bằng nhau, cách nhau không xa."""
    chains: list[list[tuple[int, int, int, int]]] = []
    for glyph in sorted(glyphs):
        x, y, width, height = glyph
        best = None
        for chain in chains:
            last_x, last_y, last_width, last_height = chain[-1]
            gap = x - (last_x + last_width)
            if (abs((y + height / 2) - (last_y + last_height / 2)) <= .35 * last_height
                    and .7 <= height / last_height <= 1.43 and -.3 * last_height <= gap <= 2.5 * last_height
                    and (best is None or gap < best[0])):
                best = (gap, chain)
        if best is None:
            chains.append([glyph])
        else:
            best[1].append(glyph)
    return chains


def text_lines(region: np.ndarray, plate_height: float, limit: int = 2) -> list[np.ndarray]:
    """Các dòng chữ ngang trong vùng quanh biển (số kẻ trên thân xe), cắt sát, chữ tối trên nền sáng.

    Một dòng là ít nhất năm nét cỡ ký tự xếp thành hàng; chữ kẻ thường cao từ 0,25 đến 1,8 lần
    chiều cao biển. Mỗi chiều sáng/tối trả tối đa `limit` dòng dài nhất, đã xoay về nằm ngang.
    """
    source = _bgr(region)
    scale = float(np.clip(40. / plate_height, 1., 4.))
    if scale > 1:
        source = _resize(source, (round(source.shape[1] * scale), round(source.shape[0] * scale)))
    size = plate_height * scale
    height, width = source.shape[:2]
    lines = []
    for image in (source, 255 - source):
        ink = _ink(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        glyphs = [(int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:count]
                  if .25 * size <= h <= 1.8 * size and w <= 1.5 * h and area >= .2 * w * h
                  and x > 0 and y > 0 and x + w < width and y + h < height]
        chains = sorted((chain for chain in _glyph_chains(glyphs) if len(chain) >= 5), key=len, reverse=True)
        for chain in chains[:limit]:
            tall = float(np.median([glyph[3] for glyph in chain]))
            left = max(0, chain[0][0] - round(.4 * tall))
            right = min(width, chain[-1][0] + chain[-1][2] + round(.4 * tall))
            top = max(0, min(glyph[1] for glyph in chain) - round(.5 * tall))
            bottom = min(height, max(glyph[1] + glyph[3] for glyph in chain) + round(.5 * tall))
            centers = np.float64([(x + w / 2 - left, y + h / 2 - top) for x, y, w, h in chain])
            slope, offset = np.polyfit(centers[:, 0], centers[:, 1], 1)
            band = _shear(image[top:bottom, left:right], -float(slope))
            middle = offset + slope * (right - left) / 2
            half = tall / 2 + max(1, round(.12 * tall))
            line = band[max(0, round(middle - half)):min(bottom - top, round(middle + half))]
            if line.shape[0] >= 4:
                lines.append(line)
    return lines


def line_strip(row: np.ndarray) -> np.ndarray:
    """Chuẩn bị một dòng chữ đã cắt sát cho bộ nhận dạng: san nền, đệm bằng màu nền.

    Bộ nhận dạng đọc tốt nhất khi chữ cao khoảng 2/3 đầu vào: dòng cắt đã chừa 12% mỗi phía,
    ở đây đệm thêm 10% trên dưới. Đệm hai đầu để ký tự đầu, cuối không chạm mép.
    """
    strip = normalize_grayscale_strip(row)
    height = strip.shape[0]
    edge = np.vstack([strip[0], strip[-1], strip[:, 0], strip[:, -1]])
    pad_y, pad_x = max(1, round(height * .10)), max(2, round(height * .25))
    return cv2.copyMakeBorder(strip, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT,
                              value=tuple(int(value) for value in np.median(edge, axis=0)))


def plate_face(crop: np.ndarray) -> Optional[np.ndarray]:
    """Mặt biển đã nắn thẳng, giữ màu gốc; None khi không khoanh được mép biển."""
    source, _, _, scale, quad = _locate(crop)
    if quad is None:
        return None
    return _warp(_bgr(crop), quad, scale)


def display_image(crop: np.ndarray) -> np.ndarray:
    """Ảnh biển số để hiển thị: mặt biển đã nắn (nếu khoanh được), tăng nét một lần."""
    face = plate_face(crop)
    return enhance(face if face is not None else normalize(crop)[0])

