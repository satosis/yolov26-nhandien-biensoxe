import time
import numpy as np

class SimpleTracker:
    def __init__(self, max_lost=30, iou_thresh=0.3):
        self.next_id = 1
        self.tracks = {}  # {id: {"bbox": (x1, y1, x2, y2), "lost": 0, "type": "person/face"}}
        self.max_lost = max_lost
        self.iou_thresh = iou_thresh
        self.start_time = time.time()
        self.frame_count = 0

    def compute_iou(self, boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        iou = interArea / float(boxAArea + boxBArea - interArea + 1e-6)
        return iou

    def update(self, detections):
        self.frame_count += 1
        new_tracks = {}
        
        # detections is a list of {"bbox": (x1, y1, x2, y2), "type": "person/face", ...}
        for det in detections:
            best_id = None
            best_iou = self.iou_thresh
            
            for tid, track in self.tracks.items():
                if track["type"] != det["type"]:
                    continue
                iou = self.compute_iou(det["bbox"], track["bbox"])
                if iou > best_iou:
                    best_iou = iou
                    best_id = tid
            
            if best_id is not None:
                new_tracks[best_id] = {
                    "bbox": det["bbox"], 
                    "lost": 0, 
                    "type": det["type"],
                    "data": det
                }
                del self.tracks[best_id]
            else:
                # New track
                new_tracks[self.next_id] = {
                    "bbox": det["bbox"], 
                    "lost": 0, 
                    "type": det["type"],
                    "data": det
                }
                self.next_id += 1

        # Keep lost tracks for a bit
        for tid, track in self.tracks.items():
            if track["lost"] < self.max_lost:
                track["lost"] += 1
                new_tracks[tid] = track
        
        self.tracks = new_tracks
        return self.tracks

    def get_fps(self):
        elapsed = time.time() - self.start_time
        if elapsed == 0: return 0
        return self.frame_count / elapsed
