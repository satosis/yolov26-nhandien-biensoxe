"""Shared, model-free image preparation and temporal OCR aggregation.

All confidences use the interval [0, 1]. Timestamps are seconds in the source
timeline, rather than the time spent processing a file. Image quality is a
ranking signal: a blurred or dark image is still a valid image.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
import hashlib
import itertools
import math
from typing import Any

import cv2
import numpy as np
from backend.util.bbox_utils import box_iou


_PROFILES = {
    "document": (96, 3.0, 2800, 6_000_000, 1.5),
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


def enhance_ocr_image(image: np.ndarray, clip_limit: float) -> np.ndarray:
    """Local contrast (CLAHE on lightness) plus a mild unsharp mask, at the same size."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(4, 4)).apply(lab[:, :, 0])
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 0.8)
    return cv2.addWeighted(enhanced, 1.20, blurred, -0.20, 0)


def prepare_ocr_image(image: np.ndarray, profile: str = "document") -> np.ndarray:
    """Prepare a BGR uint8 document image without changing its aspect or rejecting blur.

    Upscaling is limited to 3x. Output dimensions and area are bounded
    independently of the source dimensions. Input arrays are never modified.
    Callers should also try the raw image. Plates use backend.util.ocr.plate_image.
    """
    return enhance_ocr_image(_profile_image(image, profile), _PROFILES[profile][4])


def iter_ocr_variants(image: np.ndarray, profile: str = "document", max_variants: int = 3):
    """Yield raw, prepared, and neutral-contrast BGR images lazily, at most 3.

    The first image preserves the original pixels (and shares its array when
    it is already BGR uint8). Only the derived images are resized/bounded.
    """
    if profile not in _PROFILES:
        raise ValueError(f"Unknown OCR profile: {profile!r}")
    raw = _bgr(image)
    count = max(0, min(int(max_variants), _MAX_VARIANTS))
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
    context: np.ndarray | None = None

    @property
    def rank(self) -> float:
        return 0.55 * self.quality_score + 0.30 * (self.confidence if self.valid_text else 0.0) + 0.15 * self.detector_confidence


@dataclass
class _Track:
    track_id: int
    start_time: float
    last_time: float
    bbox: tuple
    valid_votes: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)
    motion_steps: int = 0
    hypotheses: dict[str, _Hypothesis] = field(default_factory=dict)
    samples: list[_Sample] = field(default_factory=list)
    pooled: tuple | None = None


class OCRTrackAggregator:
    """Associate OCR observations and emit one result when a track ends.

    Observation dictionaries contain ``bbox`` in source coordinates and may
    include text, confidence, detector_confidence, quality, and a BGR crop.
    Matching is one-to-one per update and uses predicted motion plus IoU.
    ``normalize`` and ``validator`` are domain callbacks; a plate caller should
    supply its full-format validator so fragments never become OCR votes.

    With ``recognize``, OCR is deferred until finalization and runs only on the
    retained, temporally diverse ``max_crops`` samples; it receives the crop and
    the optional ``context`` image of its observation. An optional
    ``recognize_track`` callback pools the evidence of all retained crops when
    individual reads have not settled; it is asked again only when the retained
    crops change. Exceptions propagate to the caller.
    Mark already-attempted empty reads with ``ocr_read=True``. No models or
    services are loaded here.

    Events are JSON-ready except for optional ``best_crop``. Recognition needs
    matching valid text from ``min_support`` distinct source timestamps (two by
    default), average confidence >= .70, and >= 60% of valid votes; or pooled
    evidence with confidence >= .90. A source that ends after a single frame (a
    still image) cannot give a second reading: there one reading is enough.

    A slow source keeps its tracks: the association window grows to three times
    the observed interval between analysed frames.

    At most ``max_tracks`` identities are active. When slots are full, new
    unmatched detections are dropped.
    """

    def __init__(self, *, normalize: Callable[[str], str] | None = None,
                 validator: Callable[[str], bool] | None = None,
                 recognize: Callable[[np.ndarray, np.ndarray | None], Mapping | None] | None = None,
                 recognize_track: Callable[[list[np.ndarray]], Mapping | None] | None = None,
                 max_gap_seconds: float = 1.5, max_crops: int = 5, iou_threshold: float = 0.15,
                 max_tracks: int = 128, min_support: int = 2, track_ids: Iterator[int] | None = None):
        gap = _number(max_gap_seconds, -1.0)
        threshold = _number(iou_threshold, -1.0)
        if gap <= 0 or not 0 <= threshold <= 1:
            raise ValueError("max_gap_seconds must be positive and iou_threshold must be in [0, 1]")
        self.normalize = normalize or (lambda text: " ".join(text.split()))
        self.validator = validator
        self.recognize = recognize
        self.recognize_track = recognize_track
        self.max_gap_seconds = self._base_gap = gap
        self._frame_gap = 0.0
        self.max_crops = max(1, min(int(max_crops), 32))
        self.max_tracks = max(1, min(int(max_tracks), 1024))
        self.min_support = max(1, int(min_support))
        self.iou_threshold = threshold
        self._tracks: dict[int, _Track] = {}
        # A shared counter keeps ids unique across several aggregators of one process.
        self._track_ids = track_ids or itertools.count(1)
        self._last_timestamp: float | None = None
        self._frames = 0

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
        predicted_iou, current_iou = box_iou(predicted, box), box_iou(track.bbox, box)
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
        read = (isinstance(raw_text, str) and bool(raw_text.strip())) or observation.get("ocr_read") is True
        context = observation.get("context")
        return _Sample(timestamp, text, _confidence(observation.get("confidence")), valid,
                       _confidence(observation.get("detector_confidence")), score, crop, signature, read,
                       context if isinstance(context, np.ndarray) and context.size else None)

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
        # Encoded video often repeats the exact same decoded frame. It is one
        # visual observation, not another independent OCR vote or fusion input.
        if sample.signature and any(item.signature == sample.signature for item in track.samples):
            return
        # Own retained memory; callers may reuse the decoder's source buffer.
        sample.crop = sample.crop.copy()
        if sample.context is not None:
            sample.context = sample.context.copy()
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
        track.bbox, track.last_time = box, timestamp
        sample = self._sample(timestamp, observation)
        if sample.read:
            self._vote(track, sample)
        self._retain(track, sample)

    def _finalize(self, track: _Track, min_support: int | None = None) -> dict:
        if self.recognize is not None:
            for sample in sorted(track.samples, key=lambda item: item.timestamp):
                if sample.read or sample.crop is None:
                    continue
                prediction = self.recognize(sample.crop, sample.context)
                if prediction is not None and not isinstance(prediction, Mapping):
                    raise ValueError("recognize must return a prediction mapping or None")
                prediction = prediction or {}
                sample.text, sample.valid_text = self._text(prediction.get("text"))
                sample.confidence = _confidence(prediction.get("confidence"))
                sample.read = True
                self._vote(track, sample)
        valid = [item for item in track.hypotheses.values() if item.valid and item.support]
        winner = max(valid, key=lambda item: (item.weighted_sum, item.support, item.confidence)) if valid else None
        text, confidence = (winner.text, float(winner.confidence)) if winner else ("", 0.0)
        status = "unreadable"
        if winner is not None:
            needed = self.min_support if min_support is None else min_support
            status = "recognized" if winner.support >= needed and winner.confidence >= 0.70 and winner.support / track.valid_votes >= 0.60 else "uncertain"
        # Separate frames that did not settle on one reading still share evidence:
        # pool it over the retained crops. The pooled reading is not another vote.
        crops = [sample.crop for sample in track.samples if sample.crop is not None]
        if status != "recognized" and self.recognize_track is not None and len(crops) >= 2:
            key = tuple(sample.signature for sample in track.samples if sample.crop is not None)
            if track.pooled is None or track.pooled[0] != key:
                prediction = self.recognize_track(crops)
                if prediction is not None and not isinstance(prediction, Mapping):
                    raise ValueError("recognize_track must return a prediction mapping or None")
                prediction = prediction or {}
                pooled_text, pooled_valid = self._text(prediction.get("text"))
                track.pooled = (key, pooled_text if pooled_valid else "", _confidence(prediction.get("confidence")))
            _, pooled_text, pooled_confidence = track.pooled
            if pooled_text and pooled_confidence > 0 and (pooled_confidence >= 0.90 or winner is None or pooled_text == text):
                confidence = max(pooled_confidence, confidence) if pooled_text == text else pooled_confidence
                text = pooled_text
                status = "recognized" if pooled_confidence >= 0.90 else "uncertain"
        preferred = [sample for sample in track.samples if text and sample.text == text]
        best_sample = max(preferred or track.samples, key=lambda item: item.rank, default=None)
        return {
            "track_id": track.track_id, "end_time": track.last_time,
            "bbox": list(track.bbox), "text": text, "confidence": confidence, "status": status,
            "detector_confidence": max((sample.detector_confidence for sample in track.samples), default=0.0),
            "best_crop_time": best_sample.timestamp if best_sample else None,
            "best_crop": best_sample.crop if best_sample else None,
        }

    def snapshot(self) -> list[dict]:
        """Recognize newly retained crops and return active track states.

        Unlike :meth:`update` expiration or :meth:`finish`, this leaves tracks
        active, so live camera callers can surface a reading while a vehicle is
        still in view.
        """
        return [self._finalize(track) for track in self._tracks.values()]

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
        if self._last_timestamp is not None and timestamp > self._last_timestamp:
            gap = timestamp - self._last_timestamp
            self._frame_gap = gap if self._frame_gap == 0.0 else 0.7 * self._frame_gap + 0.3 * gap
            self.max_gap_seconds = max(self._base_gap, 3.0 * self._frame_gap)
        expired = [track for track in self._tracks.values() if timestamp - track.last_time > self.max_gap_seconds]
        events = [self._finalize(track) for track in expired]
        for track in expired:
            del self._tracks[track.track_id]
        if self._last_timestamp is None or timestamp > self._last_timestamp:
            self._frames += 1
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
                break
            track = _Track(next(self._track_ids), timestamp, timestamp, observation["bbox"])
            self._tracks[track.track_id] = track
            self._observe(track, timestamp, observation)
        return events

    def finish(self) -> list[dict]:
        """Flush all remaining tracks once, including unreadable/short tracks.

        The support needed is capped by the number of frames the source had, so a
        single still image is decided by its one reading.
        """
        support = min(self.min_support, self._frames)
        events = [self._finalize(track, support) for track in self._tracks.values()]
        self._tracks.clear()
        return events
