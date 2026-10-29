import time
import numpy as np


class PersonTrack:
    def __init__(self, track_id: int, bbox: list):
        self.track_id = track_id
        self.last_bbox = bbox
        self.centroid = self._get_centroid(bbox)
        self.identity = "STRANGER"
        self.confidence = 0.0
        self.is_locked = False
        self.last_seen = time.time()
        self.frames_since_last_id = 0
        self.last_trigger_ts = 0.0

    def _get_centroid(self, bbox):
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    def update(self, bbox):
        self.last_bbox = bbox
        self.centroid = self._get_centroid(bbox)
        self.last_seen = time.time()
        self.frames_since_last_id += 1


class SimpleTracker:
    def __init__(self, max_distance: int = 150, max_age: float = 3.0):
        self.tracks: list[PersonTrack] = []
        self._next_id = 1
        self.max_distance = max_distance
        self.max_age = max_age

    def update(self, detections: list[tuple]) -> list[tuple[tuple, PersonTrack]]:
        now = time.time()
        
        self.tracks = [t for t in self.tracks if now - t.last_seen < self.max_age]
        
        results = []
        unmatched_dets = list(detections)
        
        for track in self.tracks:
            if not unmatched_dets: break
            
            best_idx = -1
            min_dist = self.max_distance
            
            for i, det in enumerate(unmatched_dets):
                x1, y1, x2, y2, conf, cls_id = det
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                dist = np.sqrt((cx - track.centroid[0])**2 + (cy - track.centroid[1])**2)
                
                if dist < min_dist:
                    min_dist = dist
                    best_idx = i
            
            if best_idx != -1:
                det = unmatched_dets.pop(best_idx)
                track.update(det[:4])
                results.append((det, track))
        
        for det in unmatched_dets:
            new_track = PersonTrack(self._next_id, list(det[:4]))
            self._next_id += 1
            self.tracks.append(new_track)
            results.append((det, new_track))
            
        return results
