"""Move detection boxes between slow detector runs with sparse optical flow."""
from __future__ import annotations
from collections import deque
import cv2
import numpy as np


class FlowBoxTracker:
    def __init__(self, scale: float = 0.5, max_history: int = 48):
        self.scale = scale
        self._history: deque = deque(maxlen=max_history)   # (timestamp, gray small frame)
        self.boxes: dict = {}                                # key -> [x1, y1, x2, y2] float, full-size coords

    def _gray(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0:
            return frame
        h, w = frame.shape[:2]
        nw = max(1, int(round(w * self.scale)))
        nh = max(1, int(round(h * self.scale)))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA) if (nw != w or nh != h) else frame
        if resized.ndim == 2:
            return resized
        if resized.ndim == 3:
            if resized.shape[2] == 3:
                return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            if resized.shape[2] == 4:
                return cv2.cvtColor(resized, cv2.COLOR_BGRA2GRAY)
            if resized.shape[2] == 1:
                return resized[:, :, 0]
        return resized

    def advance(self, frame: np.ndarray, timestamp: float) -> None:
        """Dời mọi box từ khung trước sang khung này rồi lưu khung vào lịch sử."""
        cur_gray = self._gray(frame)
        if self._history:
            prev_ts, prev_gray = self._history[-1]
            for key, box in list(self.boxes.items()):
                self.boxes[key] = self._move(prev_gray, cur_gray, box)
        self._history.append((float(timestamp), cur_gray))

    def reseed(self, boxes: dict, timestamp: float) -> None:
        """`boxes` đo trên khung có `timestamp`; dời chúng qua các khung lưu sau đó tới khung mới nhất, rồi thay self.boxes."""
        curr_boxes = {k: [float(v) for v in box] for k, box in boxes.items()}
        frames = [f for f in self._history if f[0] >= timestamp]
        if len(frames) >= 2:
            for i in range(len(frames) - 1):
                prev_gray = frames[i][1]
                cur_gray = frames[i + 1][1]
                for k, box in list(curr_boxes.items()):
                    curr_boxes[k] = self._move(prev_gray, cur_gray, box)
        self.boxes = curr_boxes

    def trim(self, timestamp: float) -> None:
        """Bỏ khung cũ hơn `timestamp`, luôn giữ khung mới nhất."""
        while len(self._history) > 1 and self._history[0][0] < timestamp:
            self._history.popleft()

    def _move(self, prev: np.ndarray, cur: np.ndarray, box: list) -> list:
        if prev is None or cur is None or prev.shape != cur.shape or not box or len(box) != 4:
            return [float(v) for v in box]

        h, w = prev.shape[:2]
        orig_w = cur.shape[1] / self.scale
        orig_h = cur.shape[0] / self.scale

        sx1 = int(round(box[0] * self.scale))
        sy1 = int(round(box[1] * self.scale))
        sx2 = int(round(box[2] * self.scale))
        sy2 = int(round(box[3] * self.scale))

        rx1 = max(0, min(w, sx1))
        ry1 = max(0, min(h, sy1))
        rx2 = max(0, min(w, sx2))
        ry2 = max(0, min(h, sy2))

        if (rx2 - rx1) < 4 or (ry2 - ry1) < 4:
            return [float(v) for v in box]

        roi = prev[ry1:ry2, rx1:rx2]
        corners = cv2.goodFeaturesToTrack(roi, maxCorners=30, qualityLevel=0.01, minDistance=3)
        if corners is None or len(corners) < 3:
            return [float(v) for v in box]

        pts = corners.reshape(-1, 2).astype(np.float32) + np.array([rx1, ry1], dtype=np.float32)
        pts = pts.reshape(-1, 1, 2)

        p1, st1, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
        if p1 is None or st1 is None:
            return [float(v) for v in box]

        p0_back, st0, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, None, winSize=(21, 21), maxLevel=3)
        if p0_back is None or st0 is None:
            return [float(v) for v in box]

        st1_flat = (st1.reshape(-1) == 1)
        st0_flat = (st0.reshape(-1) == 1)
        status_both = st1_flat & st0_flat
        if not np.any(status_both):
            return [float(v) for v in box]

        pts_2d = pts.reshape(-1, 2)
        p1_2d = p1.reshape(-1, 2)
        p0_back_2d = p0_back.reshape(-1, 2)

        err_back = np.linalg.norm(pts_2d - p0_back_2d, axis=1)
        good = status_both & (err_back < 1.0)
        if np.sum(good) < 3:
            return [float(v) for v in box]

        dxs = p1_2d[good, 0] - pts_2d[good, 0]
        dys = p1_2d[good, 1] - pts_2d[good, 1]
        dx = float(np.median(dxs)) / self.scale
        dy = float(np.median(dys)) / self.scale

        bw = float(box[2] - box[0])
        bh = float(box[3] - box[1])

        nx1 = float(box[0]) + dx
        ny1 = float(box[1]) + dy
        nx2 = nx1 + bw
        ny2 = ny1 + bh

        if orig_w > bw:
            if nx1 < 0:
                nx1 = 0.0
                nx2 = bw
            elif nx2 > orig_w:
                nx2 = orig_w
                nx1 = orig_w - bw
        else:
            nx1 = 0.0
            nx2 = orig_w

        if orig_h > bh:
            if ny1 < 0:
                ny1 = 0.0
                ny2 = bh
            elif ny2 > orig_h:
                ny2 = orig_h
                ny1 = orig_h - bh
        else:
            ny1 = 0.0
            ny2 = orig_h

        return [nx1, ny1, nx2, ny2]
