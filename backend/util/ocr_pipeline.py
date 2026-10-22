"""Shared, model-free image preparation and temporal OCR aggregation.

All confidences use the interval [0, 1]. Timestamps are seconds in the source
timeline, rather than the time spent processing a file. Image quality is a
ranking signal: a blurred or dark image is still a valid image.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import math
from typing import Any

import cv2
import numpy as np


_PROFILES = {
    "document": (96, 3.0, 2800, 6_000_000, 1.5),
    "plate": (96, 4.0, 1600, 1_500_000, 2.0),
    "handwriting": (64, 3.0, 2400, 4_000_000, 1.3),
}
_MAX_VARIANTS = 3
_MAX_RETAINED_EDGE = 1600
_MAX_RETAINED_PIXELS = 1_500_000


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (bool, np.bool_)):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _confidence(value: Any) -> float:
    value = _number(value)
    return value if 0.0 <= value <= 1.0 else 0.0


def _bgr(image: np.ndarray) -> np.ndarray:
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("OCR image must be a nonempty numpy array")
    if image.ndim not in (2, 3) or min(image.shape[:2]) < 1:
        raise ValueError("OCR image must have height and width")
    if image.ndim == 3 and image.shape[2] not in (1, 3, 4):
        raise ValueError("OCR image must have 1, 3, or 4 channels")
    if image.dtype.kind not in "buif" or (image.dtype.kind == "f" and not np.isfinite(image).all()):
        raise ValueError("OCR image must contain finite numeric pixels")
    if image.dtype != np.uint8:
        if image.dtype.kind == "b":
            image = image.astype(np.uint8) * 255
        elif image.dtype.kind == "f" and image.min() >= 0 and image.max() <= 1:
            image = np.rint(image * 255).astype(np.uint8)
        else:
            image = np.clip(image, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 1:
        return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def _bounded_resize(image: np.ndarray, scale: float, max_edge: int, max_pixels: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(scale, max_edge / max(height, width), math.sqrt(max_pixels / (height * width)))
    new_width = max(1, int(math.floor(width * scale)))
    new_height = max(1, int(math.floor(height * scale)))
    if (new_width, new_height) == (width, height):
        return image
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(image, (new_width, new_height), interpolation=interpolation)


def _profile_image(image: np.ndarray, profile: str) -> np.ndarray:
    if profile not in _PROFILES:
        raise ValueError(f"Unknown OCR profile: {profile!r}")
    image = _bgr(image)
    min_height, max_scale, max_edge, max_pixels, _ = _PROFILES[profile]
    scale = max(1.0, min(max_scale, min_height / image.shape[0]))
    return _bounded_resize(image, scale, max_edge, max_pixels)


def assess_image(image: np.ndarray) -> dict:
    """Return native dimensions and bounded-cost quality metrics.

    Sharpness is Laplacian variance on a preview of at most 1024 pixels per
    edge; brightness is in [0, 255]. ``valid`` describes the image structure,
    not whether its text is readable. Invalid input returns zero metrics.
    """
    result = {"width": 0, "height": 0, "sharpness": 0.0, "brightness": 0.0,
              "quality_score": 0.0, "valid": False}
    try:
        image = _bgr(image)
    except ValueError:
        return result
    height, width = image.shape[:2]
    preview = _bounded_resize(image, 1.0, 1024, 1024 * 1024)
    gray = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    sharpness_score = sharpness / (sharpness + 80.0)
    exposure_score = 1.0 - abs(brightness - 127.5) / 127.5
    size_score = min(1.0, min(height, width) / 64.0)
    quality = 0.55 * sharpness_score + 0.20 * exposure_score + 0.25 * size_score
    result.update(width=int(width), height=int(height), sharpness=sharpness,
                  brightness=brightness, quality_score=float(np.clip(quality, 0, 1)), valid=True)
    return result


def _contrast(image: np.ndarray, clip_limit: float) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(4, 4)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def prepare_ocr_image(image: np.ndarray, profile: str = "document") -> np.ndarray:
    """Prepare a BGR uint8 image without changing its aspect or rejecting blur.

    Upscaling is limited to 3x (documents/handwriting) or 4x (plates). Output
    dimensions and area are bounded independently of the source dimensions.
    Input arrays are never modified. Callers should also try the raw image.
    """
    image = _profile_image(image, profile)
    enhanced = _contrast(image, _PROFILES[profile][4])
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 0.8)
    return cv2.addWeighted(enhanced, 1.20, blurred, -0.20, 0)


def iter_ocr_variants(image: np.ndarray, profile: str = "document", max_variants: int = 3):
    """Yield raw, prepared, and neutral-contrast BGR images lazily, at most 3.

    The first image preserves the original pixels (and shares its array when
    it is already BGR uint8). Only the derived images are resized/bounded.
    """
    if profile not in _PROFILES:
        raise ValueError(f"Unknown OCR profile: {profile!r}")
    if isinstance(max_variants, bool) or not isinstance(max_variants, (int, np.integer)) or max_variants < 0:
        raise ValueError("max_variants must be a nonnegative integer")
    raw = _bgr(image)
    count = min(int(max_variants), _MAX_VARIANTS)
    if count == 0:
        return
    yield raw
    if count >= 2:
        yield prepare_ocr_image(raw, profile)
    if count >= 3:
        bounded = _profile_image(raw, profile)
        gray = cv2.cvtColor(bounded, cv2.COLOR_BGR2GRAY)
        normalized = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(gray)
        yield cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)


def fuse_temporal_crops(crops: Iterable[np.ndarray], *, min_alignment: float = 0.70) -> np.ndarray | None:
    """Align and denoise several views of the same moving object's plate.

    This is deliberately conservative: it returns no fused image unless at
    least three similarly shaped crops can be registered to the sharpest view
    with a small translation and a strong ECC score. It improves temporal
    noise/compression consistency, but is not super-resolution and cannot
    recover detail absent from every source frame.
    """
    images = []
    for crop in crops:
        try:
            image = _bgr(crop)
        except ValueError:
            continue
        if min(image.shape[:2]) < 8:
            continue
        images.append(image)
    if len(images) < 3:
        return None

    aspects = [image.shape[1] / image.shape[0] for image in images]
    if max(aspects) / min(aspects) > 1.30:
        return None

    # Pick a stable reference by native sharpness/quality, then normalize the
    # other detections to its geometry. The regular OCR path still tries the
    # original source crops independently.
    metrics = [(image, assess_image(image)) for image in images]
    reference_source, _ = max(metrics, key=lambda item: (
        item[1]["quality_score"], item[1]["sharpness"], item[0].shape[0] * item[0].shape[1],
    ))
    reference = _profile_image(reference_source, "plate")
    height, width = reference.shape[:2]
    if height < 24 or width < 48:
        return None
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    reference_gray = cv2.GaussianBlur(clahe.apply(reference_gray), (3, 3), 0)
    aligned = [reference]
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
    for image in images:
        if image is reference_source:
            continue
        candidate = cv2.resize(image, (width, height), interpolation=cv2.INTER_CUBIC
                               if image.shape[0] < height else cv2.INTER_AREA)
        gray = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(clahe.apply(gray), (3, 3), 0)
        warp = np.eye(2, 3, dtype=np.float32)
        try:
            correlation, warp = cv2.findTransformECC(
                reference_gray, gray, warp, cv2.MOTION_TRANSLATION, criteria, None, 3,
            )
        except cv2.error:
            continue
        dx, dy = float(warp[0, 2]), float(warp[1, 2])
        if correlation < min_alignment or abs(dx) > width * .06 or abs(dy) > height * .10:
            continue
        aligned.append(cv2.warpAffine(
            candidate, warp, (width, height),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REFLECT_101,
        ))

    if len(aligned) < 3:
        return None
    stack = np.stack(aligned, axis=0)
    temporal_median = np.median(stack, axis=0).astype(np.uint8)
    return cv2.addWeighted(reference, 0.55, temporal_median, 0.45, 0)


def best_prediction(predictions: Iterable[Mapping], text_key: str = "text",
                    confidence_key: str = "confidence") -> dict | None:
    """Choose nonempty text by confidence, using a finite score to break ties.

    Invalid/out-of-range confidence is zero, not a confident fallback. The
    returned copy preserves caller metadata and leaves predictions untouched.
    """
    best = None
    best_rank = (-1.0, -1.0)
    for prediction in predictions:
        if not isinstance(prediction, Mapping):
            continue
        text = prediction.get(text_key)
        if not isinstance(text, str) or not text.strip():
            continue
        candidate = dict(prediction)
        candidate[text_key] = text.strip()
        candidate[confidence_key] = _confidence(prediction.get(confidence_key))
        score = max(0.0, _number(prediction.get("score")))
        if "score" in candidate:
            candidate["score"] = score
        rank = (candidate[confidence_key], score)
        if rank > best_rank:
            best, best_rank = candidate, rank
    return best


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple, np.ndarray)):
        return None
    if isinstance(value, np.ndarray) and value.ndim != 1:
        return None
    if len(value) != 4:
        return None
    try:
        if any(isinstance(item, (bool, np.bool_)) for item in value):
            return None
        box = tuple(float(item) for item in value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not all(math.isfinite(item) for item in box) or box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _iou(a: tuple, b: tuple) -> float:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def _center(box: tuple) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


@dataclass
class _Hypothesis:
    text: str
    valid: bool
    support: int = 0
    confidence_sum: float = 0.0
    weighted_sum: float = 0.0
    last_timestamp: float = -1.0
    last_confidence: float = 0.0
    last_weight: float = 0.0
    best_confidence: float = 0.0

    @property
    def confidence(self) -> float:
        return self.confidence_sum / self.support if self.support else self.best_confidence


@dataclass
class _Sample:
    timestamp: float
    text: str
    confidence: float
    valid_text: bool
    detector_confidence: float
    quality_score: float
    crop: np.ndarray | None
    signature: str
    read: bool

    @property
    def rank(self) -> float:
        return 0.55 * self.quality_score + 0.30 * (self.confidence if self.valid_text else 0.0) + 0.15 * self.detector_confidence


@dataclass
class _Track:
    track_id: int
    start_time: float
    last_time: float
    bbox: tuple
    observations: int = 0
    valid_votes: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)
    motion_steps: int = 0
    hypotheses: dict[str, _Hypothesis] = field(default_factory=dict)
    samples: list[_Sample] = field(default_factory=list)


class OCRTrackAggregator:
    """Associate OCR observations and emit one result when a track ends.

    Observation dictionaries contain ``bbox`` in source coordinates and may
    include text, confidence, detector_confidence, quality, and a BGR crop.
    Matching is one-to-one per update and uses predicted motion plus IoU.
    ``normalize`` and ``validator`` are domain callbacks; a plate caller should
    supply its full-format validator so fragments never become OCR votes.

    With ``recognize``, OCR is deferred until finalization and runs only on the
    retained, temporally diverse ``max_crops`` samples. An optional
    ``recognize_track`` callback can make one final prediction from aligned
    crops if individual reads remain uncertain. Exceptions propagate to the
    caller.
    Mark already-attempted empty reads with ``ocr_read=True`` (``read`` and
    ``ocr_attempted`` are also accepted). No models or services are loaded here.

    Events are JSON-ready except for optional ``best_crop``. Recognition needs
    matching valid text from two distinct source timestamps, average confidence
    >= .70, and >= 60% of valid votes. A single read remains uncertain.

    At most ``max_tracks`` identities are active. When slots are full, new
    unmatched detections are dropped and counted in ``dropped_observations``;
    callers should surface that counter as reduced candidate coverage.
    """

    def __init__(self, *, normalize: Callable[[str], str] | None = None,
                 validator: Callable[[str], bool] | None = None,
                 recognize: Callable[[np.ndarray], Mapping | None] | None = None,
                 recognize_track: Callable[[list[np.ndarray]], Mapping | None] | None = None,
                 max_gap_seconds: float = 1.5, max_crops: int = 5, iou_threshold: float = 0.15,
                 max_tracks: int = 128):
        gap = _number(max_gap_seconds, -1.0)
        threshold = _number(iou_threshold, -1.0)
        if gap <= 0 or not 0 <= threshold <= 1:
            raise ValueError("max_gap_seconds must be positive and iou_threshold must be in [0, 1]")
        if isinstance(max_crops, bool) or not isinstance(max_crops, (int, np.integer)) or not 1 <= max_crops <= 32:
            raise ValueError("max_crops must be an integer from 1 to 32")
        if isinstance(max_tracks, bool) or not isinstance(max_tracks, (int, np.integer)) or not 1 <= max_tracks <= 1024:
            raise ValueError("max_tracks must be an integer from 1 to 1024")
        for callback in (normalize, validator, recognize, recognize_track):
            if callback is not None and not callable(callback):
                raise ValueError("OCR callbacks must be callable")
        self.normalize = normalize or (lambda text: " ".join(text.split()))
        self.validator = validator
        self.recognize = recognize
        self.recognize_track = recognize_track
        self.max_gap_seconds = gap
        self.max_crops = int(max_crops)
        self.max_tracks = int(max_tracks)
        self.dropped_observations = 0
        self.iou_threshold = threshold
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1
        self._last_timestamp: float | None = None

    def _text(self, value: Any) -> tuple[str, bool]:
        if not isinstance(value, str) or not value.strip():
            return "", False
        text = self.normalize(value)
        if not isinstance(text, str) or not text.strip():
            return "", False
        text = text.strip()
        return text, bool(self.validator(text)) if self.validator is not None else True

    def _association(self, track: _Track, observation: Mapping, timestamp: float) -> float | None:
        box = observation["bbox"]
        gap = timestamp - track.last_time
        tx, ty = _center(track.bbox)
        ox, oy = _center(box)
        tw, th = track.bbox[2] - track.bbox[0], track.bbox[3] - track.bbox[1]
        ow, oh = box[2] - box[0], box[3] - box[1]
        if max(tw / ow, ow / tw, th / oh, oh / th) > 3.0:
            return None
        dx, dy = track.velocity[0] * gap, track.velocity[1] * gap
        predicted = (track.bbox[0] + dx, track.bbox[1] + dy, track.bbox[2] + dx, track.bbox[3] + dy)
        diagonal = max(math.hypot(tw, th), math.hypot(ow, oh), 1.0)
        distance = math.hypot(ox - tx - dx, oy - ty - dy) / diagonal
        current_distance = math.hypot(ox - tx, oy - ty) / diagonal
        predicted_iou, current_iou = _iou(predicted, box), _iou(track.bbox, box)
        allowed_distance = 0.85 + min(0.5, gap)
        if track.motion_steps == 0:
            allowed_distance = 1.25 + min(0.5, gap)
        if (predicted_iou <= 0 or predicted_iou < self.iou_threshold) and distance > allowed_distance:
            return None
        # Prevent a stale velocity prediction jumping onto a distant neighbor.
        if gap > self.max_gap_seconds * 0.6 and current_distance > 3.0 and predicted_iou < 0.5:
            return None
        score = 0.65 * predicted_iou + 0.15 * current_iou + 0.20 / (1.0 + distance)
        text, valid = self._text(observation.get("text"))
        known = [item for item in track.hypotheses.values() if item.valid and item.support and item.confidence >= 0.70]
        if valid and known and _confidence(observation.get("confidence")) >= 0.70:
            previous = max(known, key=lambda item: item.weighted_sum)
            # Reliable text helps resolve spatially ambiguous crossings, but
            # cannot bypass the geometry/motion gates above.
            score += 0.25 if text == previous.text else -0.25
        return score

    def _vote(self, track: _Track, sample: _Sample) -> None:
        if not sample.text:
            return
        hypothesis = track.hypotheses.get(sample.text)
        valid_vote = sample.valid_text and sample.confidence > 0
        repeated = hypothesis is not None and hypothesis.last_timestamp == sample.timestamp
        if valid_vote and not repeated:
            # Include competing reads even if their hypotheses are later evicted.
            track.valid_votes += 1
        if hypothesis is None:
            # Keep diagnostics bounded even for a long, continuously visible track.
            limit = max(8, self.max_crops * 2)
            if len(track.hypotheses) >= limit:
                weakest = min(track.hypotheses.values(), key=lambda item: (item.valid, item.weighted_sum, item.best_confidence))
                if weakest.valid and weakest.weighted_sum > sample.confidence:
                    return
                del track.hypotheses[weakest.text]
            hypothesis = track.hypotheses[sample.text] = _Hypothesis(sample.text, sample.valid_text)
        hypothesis.best_confidence = max(hypothesis.best_confidence, sample.confidence)
        if not sample.valid_text or sample.confidence <= 0:
            return
        weight = sample.confidence * (0.5 + 0.5 * sample.quality_score)
        if hypothesis.last_timestamp == sample.timestamp:
            if sample.confidence > hypothesis.last_confidence:
                hypothesis.confidence_sum += sample.confidence - hypothesis.last_confidence
                hypothesis.weighted_sum += weight - hypothesis.last_weight
                hypothesis.last_confidence, hypothesis.last_weight = sample.confidence, weight
            return
        hypothesis.support += 1
        hypothesis.confidence_sum += sample.confidence
        hypothesis.weighted_sum += weight
        hypothesis.last_timestamp = sample.timestamp
        hypothesis.last_confidence, hypothesis.last_weight = sample.confidence, weight

    def _sample(self, timestamp: float, observation: Mapping) -> _Sample:
        text, valid = self._text(observation.get("text"))
        crop = observation.get("crop")
        try:
            crop = _bgr(crop) if crop is not None else None
        except ValueError:
            crop = None
        quality = observation.get("quality")
        if not isinstance(quality, Mapping):
            quality = assess_image(crop)
        score = _confidence(quality.get("quality_score"))
        signature = ""
        if crop is not None:
            preview = cv2.resize(crop, (24, 24), interpolation=cv2.INTER_AREA)
            signature = hashlib.blake2b(preview.tobytes(), digest_size=8).hexdigest()
            crop = _bounded_resize(crop, 1.0, _MAX_RETAINED_EDGE, _MAX_RETAINED_PIXELS)
        raw_text = observation.get("text")
        read = (isinstance(raw_text, str) and bool(raw_text.strip())) or any(
            observation.get(flag) is True for flag in ("ocr_read", "read", "ocr_attempted"))
        return _Sample(timestamp, text, _confidence(observation.get("confidence")), valid,
                       _confidence(observation.get("detector_confidence")), score, crop, signature, read)

    def _retain(self, track: _Track, sample: _Sample) -> None:
        if sample.crop is None:
            return
        existing = next((item for item in track.samples if item.timestamp == sample.timestamp), None)
        if existing is not None:
            if existing.rank >= sample.rank:
                return
            # Never OCR a second variant of an already-read source frame.
            if existing.read and not sample.read:
                sample.text, sample.confidence, sample.valid_text = existing.text, existing.confidence, existing.valid_text
                sample.read = True
            track.samples = [item for item in track.samples if item is not existing]
        # Own retained memory; callers may reuse the decoder's source buffer.
        sample.crop = sample.crop.copy()
        available = track.samples + [sample]
        if len(available) <= self.max_crops:
            track.samples = available
            return
        selected = [max(available, key=lambda item: item.rank)]
        available = [item for item in available if item is not selected[0]]
        span = max(0.001, track.last_time - track.start_time)
        while len(selected) < self.max_crops:
            def rank(item: _Sample) -> float:
                temporal = min(abs(item.timestamp - other.timestamp) for other in selected) / span
                novelty = float(not any(item.signature == other.signature for other in selected))
                return 0.80 * item.rank + 0.15 * temporal + 0.05 * novelty
            chosen = max(available, key=rank)
            selected.append(chosen)
            available = [item for item in available if item is not chosen]
        track.samples = selected

    def _observe(self, track: _Track, timestamp: float, observation: Mapping) -> None:
        box = observation["bbox"]
        gap = timestamp - track.last_time
        if gap > 0:
            before, after = _center(track.bbox), _center(box)
            velocity = ((after[0] - before[0]) / gap, (after[1] - before[1]) / gap)
            if track.motion_steps:
                velocity = tuple(0.65 * new + 0.35 * old for new, old in zip(velocity, track.velocity))
            track.velocity = velocity
            track.motion_steps += 1
        if track.observations == 0 or gap > 0:
            track.observations += 1
        track.bbox, track.last_time = box, timestamp
        sample = self._sample(timestamp, observation)
        if sample.read:
            self._vote(track, sample)
        self._retain(track, sample)

    def _finalize(self, track: _Track, *, allow_temporal_fusion: bool = True) -> dict:
        if self.recognize is not None:
            for sample in sorted(track.samples, key=lambda item: item.timestamp):
                if sample.read or sample.crop is None:
                    continue
                prediction = self.recognize(sample.crop)
                if prediction is not None and not isinstance(prediction, Mapping):
                    raise ValueError("recognize must return a prediction mapping or None")
                prediction = prediction or {}
                sample.text, sample.valid_text = self._text(prediction.get("text"))
                sample.confidence = _confidence(prediction.get("confidence"))
                sample.read = True
                self._vote(track, sample)
        valid = [item for item in track.hypotheses.values() if item.valid and item.support]
        winner = max(valid, key=lambda item: (item.weighted_sum, item.support, item.confidence)) if valid else None
        status = "unreadable"
        if winner is not None:
            status = "recognized" if winner.support >= 2 and winner.confidence >= 0.70 and winner.support / track.valid_votes >= 0.60 else "uncertain"
        # If separate frames did not produce a stable reading, one conservative
        # registration/fusion pass can recover a candidate from shared temporal
        # detail. This is one inference, never an extra independent frame vote.
        if (allow_temporal_fusion and status != "recognized" and self.recognize_track is not None
                and len(track.samples) >= 3):
            crops = [sample.crop for sample in track.samples if sample.crop is not None]
            if len(crops) >= 3:
                prediction = self.recognize_track(crops)
                if prediction is not None and not isinstance(prediction, Mapping):
                    raise ValueError("recognize_track must return a prediction mapping or None")
                if prediction:
                    text, valid_text = self._text(prediction.get("text"))
                    confidence = _confidence(prediction.get("confidence"))
                    if valid_text and confidence > 0:
                        quality = max((sample.quality_score for sample in track.samples), default=0.)
                        weight = confidence * (0.5 + 0.5 * quality)
                        hypothesis = track.hypotheses.get(text)
                        if hypothesis is None or hypothesis.support == 0:
                            if hypothesis is None:
                                hypothesis = track.hypotheses[text] = _Hypothesis(text, True)
                            hypothesis.valid = True
                            hypothesis.support = 1
                            hypothesis.confidence_sum = confidence
                            hypothesis.weighted_sum = weight
                            hypothesis.last_timestamp = track.last_time
                            hypothesis.last_confidence = confidence
                            hypothesis.last_weight = weight
                            hypothesis.best_confidence = max(hypothesis.best_confidence, confidence)
                            track.valid_votes += 1
                        else:
                            hypothesis.best_confidence = max(hypothesis.best_confidence, confidence)
                            if hypothesis.support:
                                hypothesis.confidence_sum = max(
                                    hypothesis.confidence_sum, confidence * hypothesis.support,
                                )
                                hypothesis.weighted_sum = max(
                                    hypothesis.weighted_sum, weight * hypothesis.support,
                                )
                        valid = [item for item in track.hypotheses.values() if item.valid and item.support]
                        winner = max(valid, key=lambda item: (item.weighted_sum, item.support, item.confidence))
                        status = "recognized" if (
                            winner.support >= 2 and winner.confidence >= 0.70
                            and winner.support / track.valid_votes >= 0.60
                        ) else "uncertain"
        ranked = sorted(track.hypotheses.values(), key=lambda item: (item.valid, item.weighted_sum, item.best_confidence), reverse=True)
        preferred = [sample for sample in track.samples if winner is not None and sample.text == winner.text]
        best_sample = max(preferred or track.samples, key=lambda item: item.rank, default=None)
        return {
            "track_id": track.track_id, "start_time": track.start_time, "end_time": track.last_time,
            "bbox": list(track.bbox), "text": winner.text if winner else "",
            "confidence": float(winner.confidence) if winner else 0.0, "status": status,
            "detector_confidence": max((sample.detector_confidence for sample in track.samples), default=0.0),
            "quality_score": max((sample.quality_score for sample in track.samples), default=0.0),
            "observations": track.observations,
            "candidates": [{"text": item.text, "confidence": float(item.confidence),
                            "support": item.support, "valid": item.valid} for item in ranked[:self.max_crops]],
            "best_crop": best_sample.crop if best_sample else None,
        }

    def snapshot(self) -> list[dict]:
        """Recognize newly retained crops and return active track states.

        Unlike :meth:`update` expiration or :meth:`finish`, this leaves tracks
        active and defers temporal fusion until they end. Live camera callers can
        therefore surface stable OCR votes while a vehicle is still in view.
        """
        return [self._finalize(track, allow_temporal_fusion=False)
                for track in self._tracks.values()]

    def update(self, timestamp: float, observations: Iterable[Mapping]) -> list[dict]:
        """Observe one source frame and return tracks expired before that frame.

        Invalid observations are ignored. Invalid or decreasing timestamps raise
        ValueError so a broken source timeline cannot silently mix vehicles.
        """
        timestamp = _number(timestamp, -1.0)
        if timestamp < 0 or (self._last_timestamp is not None and timestamp < self._last_timestamp):
            raise ValueError("OCR timestamps must be finite, nonnegative, and nondecreasing")
        if observations is None or isinstance(observations, (str, bytes, Mapping)):
            raise ValueError("observations must be an iterable of observation mappings")
        normalized = []
        try:
            iterator = iter(observations)
        except TypeError as error:
            raise ValueError("observations must be iterable") from error
        for observation in iterator:
            if not isinstance(observation, Mapping):
                continue
            box = _bbox(observation.get("bbox"))
            if box is not None:
                normalized.append({**observation, "bbox": box})
        expired = [track for track in self._tracks.values() if timestamp - track.last_time > self.max_gap_seconds]
        events = [self._finalize(track) for track in expired]
        for track in expired:
            del self._tracks[track.track_id]
        self._last_timestamp = timestamp
        pairs = []
        for track in self._tracks.values():
            for index, observation in enumerate(normalized):
                score = self._association(track, observation, timestamp)
                if score is not None:
                    pairs.append((score, track.track_id, index))
        used_tracks, used_observations = set(), set()
        for _, track_id, index in sorted(pairs, reverse=True):
            if track_id in used_tracks or index in used_observations:
                continue
            self._observe(self._tracks[track_id], timestamp, normalized[index])
            used_tracks.add(track_id)
            used_observations.add(index)
        unmatched = [(index, item) for index, item in enumerate(normalized) if index not in used_observations]
        # Preserve active identities. Fill remaining slots from the strongest
        # detector candidates and report overflow rather than triggering an OCR
        # finalization storm on a noisy frame.
        unmatched.sort(key=lambda item: _confidence(item[1].get("detector_confidence")), reverse=True)
        for _, observation in unmatched:
            if len(self._tracks) >= self.max_tracks:
                self.dropped_observations += 1
                continue
            track = _Track(self._next_id, timestamp, timestamp, observation["bbox"])
            self._next_id += 1
            self._tracks[track.track_id] = track
            self._observe(track, timestamp, observation)
        return events

    def finish(self) -> list[dict]:
        """Flush all remaining tracks once, including unreadable/short tracks."""
        events = [self._finalize(track) for track in self._tracks.values()]
        self._tracks.clear()
        return events
