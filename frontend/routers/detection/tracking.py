import re
import math
import threading
import cv2


class TrackedPlate:
    def __init__(self, track_id: int, bbox: list, det_conf: float, ts: float, frame_bgr=None):
        self.track_id = track_id
        self.bbox = list(bbox)
        self.det_conf = det_conf
        self.first_ts = ts
        self.last_ts = ts
        self.text = ""
        self.ocr_conf = 0.0
        self.plate_type = "white"
        self.status = "no_plate"
        self.owner_name = "unknown"
        self.confirmed = False
        self.ocr_attempts = 0
        self.missed_frames = 0
        self.vx = 0.0
        self.vy = 0.0
        self.template = None
        self.db_saved = False
        self.plate_candidates = {}
        if frame_bgr is not None:
            self._save_template(frame_bgr, bbox)

    def _save_template(self, frame_bgr, b):
        h, w = frame_bgr.shape[:2]
        x1, y1, x2, y2 = b
        pw, ph = x2 - x1, y2 - y1
        tx1 = max(0, x1 - int(pw * 0.15))
        ty1 = max(0, y1 - int(ph * 0.15))
        tx2 = min(w, x2 + int(pw * 0.15))
        ty2 = min(h, y2 + int(ph * 0.15))
        if tx2 > tx1 and ty2 > ty1:
            self.template = frame_bgr[ty1:ty2, tx1:tx2].copy()

    def update_bbox(self, new_bbox: list, det_conf: float, ts: float, frame_bgr=None):
        dt = max(0.05, ts - self.last_ts)
        old_cx = (self.bbox[0] + self.bbox[2]) / 2.0
        old_cy = (self.bbox[1] + self.bbox[3]) / 2.0
        new_cx = (new_bbox[0] + new_bbox[2]) / 2.0
        new_cy = (new_bbox[1] + new_bbox[3]) / 2.0

        curr_vx = (new_cx - old_cx) / dt * 0.2
        curr_vy = (new_cy - old_cy) / dt * 0.2
        self.vx = 0.6 * self.vx + 0.4 * curr_vx
        self.vy = 0.6 * self.vy + 0.4 * curr_vy

        self.bbox = list(new_bbox)
        self.det_conf = max(self.det_conf, det_conf)
        self.last_ts = ts
        self.missed_frames = 0
        if frame_bgr is not None and self.confirmed:
            self._save_template(frame_bgr, new_bbox)

    def set_ocr_result(self, text: str, conf: float, ptype: str, status: str, owner_name: str, frame_bgr=None):
        self.ocr_attempts += 1
        clean = (text or "").strip().upper()
        if not clean or clean == "(BIỂN SỐ ?)":
            return False

        if clean not in self.plate_candidates:
            self.plate_candidates[clean] = {
                "count": 0, "max_conf": 0.0, "ptype": ptype, "status": status, "owner_name": owner_name
            }
        cand = self.plate_candidates[clean]
        cand["count"] += 1
        cand["max_conf"] = max(cand["max_conf"], conf)
        if status != "unknown" and cand["status"] == "unknown":
            cand["status"] = status
            cand["owner_name"] = owner_name

        def _candidate_score(c_txt):
            c_data = self.plate_candidates[c_txt]
            is_std_pattern = bool(re.match(r"^[0-9]{2}[A-Z]{1,2}-?[0-9]{3,5}", c_txt))
            alphanumeric = re.sub(r"[^A-Z0-9]", "", c_txt)
            has_letter = bool(re.search(r"[A-Z]", alphanumeric))
            full_len = len(alphanumeric) >= 7

            score = c_data["max_conf"] * math.sqrt(c_data["count"])
            if is_std_pattern and full_len:
                score *= 1.6
            elif has_letter and full_len:
                score *= 1.3
            elif not has_letter:
                score *= 0.6
            return score

        best_text = max(self.plate_candidates.keys(), key=_candidate_score)
        best_data = self.plate_candidates[best_text]
        old_text = self.text
        self.text = best_text
        self.ocr_conf = best_data["max_conf"]
        self.plate_type = best_data["ptype"]
        self.status = best_data["status"]
        self.owner_name = best_data["owner_name"]

        alphanumeric = re.sub(r"[^A-Z0-9]", "", best_text)
        has_letter = bool(re.search(r"[A-Z]", alphanumeric))
        if len(alphanumeric) >= 7 and has_letter:
            if (best_data["count"] >= 2 and best_data["max_conf"] >= 0.88) or best_data["max_conf"] >= 0.96:
                self.confirmed = True
                if frame_bgr is not None:
                    self._save_template(frame_bgr, self.bbox)

        return (best_text != old_text)


class SpatialPlateTracker:
    def __init__(self, max_lost_seconds: float = 6.0):
        self.next_id = 1
        self.tracks: dict[int, TrackedPlate] = {}
        self.max_lost_seconds = max_lost_seconds

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

    def match_and_update(self, detections: list, now_ts: float, frame_bgr=None):
        stale = [tid for tid, tr in self.tracks.items() if now_ts - tr.last_ts > self.max_lost_seconds]
        for tid in stale:
            del self.tracks[tid]

        matched_detections = set()
        matched_tracks = set()
        results = []

        # 1. Match IoU first
        for tid, track in list(self.tracks.items()):
            best_iou = 0.20
            best_d_idx = None
            for d_idx, det in enumerate(detections):
                if d_idx in matched_detections:
                    continue
                score = self._compute_iou(det[:4], track.bbox)
                if score > best_iou:
                    best_iou = score
                    best_d_idx = d_idx

            if best_d_idx is not None:
                det = detections[best_d_idx]
                track.update_bbox(det[:4], det[4], now_ts, frame_bgr=frame_bgr)
                matched_detections.add(best_d_idx)
                matched_tracks.add(tid)
                results.append((track, det[:4], det[4], False))

        # 2. Match Centroid Distance
        for tid, track in list(self.tracks.items()):
            if tid in matched_tracks:
                continue
            tw = track.bbox[2] - track.bbox[0]
            th = track.bbox[3] - track.bbox[1]
            max_dist = max(tw, th) * 2.5

            best_dist = max_dist
            best_d_idx = None
            for d_idx, det in enumerate(detections):
                if d_idx in matched_detections:
                    continue
                d = self._center_dist(det[:4], track.bbox)
                if d < best_dist:
                    best_dist = d
                    best_d_idx = d_idx

            if best_d_idx is not None:
                det = detections[best_d_idx]
                track.update_bbox(det[:4], det[4], now_ts, frame_bgr=frame_bgr)
                matched_detections.add(best_d_idx)
                matched_tracks.add(tid)
                results.append((track, det[:4], det[4], False))

        # 3. Create new tracks for unmatched detections
        for d_idx, det in enumerate(detections):
            if d_idx not in matched_detections:
                tid = self.next_id
                self.next_id += 1
                track = TrackedPlate(tid, det[:4], det[4], now_ts, frame_bgr=frame_bgr)
                self.tracks[tid] = track
                results.append((track, det[:4], det[4], True))

        # 4. Visual Coasting
        for tid, track in list(self.tracks.items()):
            if tid in matched_tracks:
                continue
            if not track.confirmed or not track.text or track.text == "(BIỂN SỐ ?)":
                continue

            track.missed_frames += 1
            if track.missed_frames <= 30 and track.template is not None and frame_bgr is not None:
                fh, fw = frame_bgr.shape[:2]
                tcx = (track.bbox[0] + track.bbox[2]) // 2
                tcy = (track.bbox[1] + track.bbox[3]) // 2

                pred_cx = int(tcx + track.vx)
                pred_cy = int(tcy + track.vy)

                bw = track.bbox[2] - track.bbox[0]
                bh = track.bbox[3] - track.bbox[1]
                sr = max(70, int(max(bw, bh) * 2.5))
                sx1, sy1 = max(0, pred_cx - sr), max(0, pred_cy - sr)
                sx2, sy2 = min(fw, pred_cx + sr), min(fh, pred_cy + sr)

                temp_h, temp_w = track.template.shape[:2]
                if (sy2 - sy1) >= temp_h and (sx2 - sx1) >= temp_w:
                    search_area = frame_bgr[sy1:sy2, sx1:sx2]
                    match_res = cv2.matchTemplate(search_area, track.template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(match_res)

                    if max_val >= 0.35:
                        mx = sx1 + max_loc[0]
                        my = sy1 + max_loc[1]
                        new_bbox = [mx, my, mx + bw, my + bh]
                        track.bbox = new_bbox
                        track.last_ts = now_ts
                        matched_tracks.add(tid)
                        results.append((track, new_bbox, track.det_conf, False))
                    elif track.missed_frames <= 8:
                        new_bbox = [track.bbox[0] + int(track.vx), track.bbox[1] + int(track.vy),
                                    track.bbox[2] + int(track.vx), track.bbox[3] + int(track.vy)]
                        if 0 <= new_bbox[0] < fw and 0 <= new_bbox[1] < fh:
                            track.bbox = new_bbox
                            track.last_ts = now_ts
                            matched_tracks.add(tid)
                            results.append((track, new_bbox, track.det_conf, False))

        return results

    def reset(self):
        self.tracks.clear()
        self.next_id = 1


_trackers_lock = threading.Lock()
_trackers: dict[str, SpatialPlateTracker] = {}


def _get_spatial_tracker(source: str, s_id=None) -> SpatialPlateTracker:
    key = f"{source}_{s_id or 'default'}"
    with _trackers_lock:
        if key not in _trackers:
            _trackers[key] = SpatialPlateTracker(max_lost_seconds=4.0)
        return _trackers[key]
