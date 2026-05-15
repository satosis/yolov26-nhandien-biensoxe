"""
bbox_utils.py – Bounding box merging for multi-line license plates.

Problem: YOLO may detect each LINE of a 2-line plate as a separate detection.
Solution: Merge plate bboxes that are vertically adjacent and horizontally overlapping.
"""

import logging

logger = logging.getLogger(__name__)


def merge_plate_boxes(detections: list) -> list:
    """
    Merge vertically-adjacent plate detections (cls_id=0) into single bounding boxes.
    
    Two plate boxes are merged if:
      1. They overlap horizontally (IoU_x > 30%)
      2. The vertical gap between them is smaller than the height of the smaller box
    
    Non-plate detections (person, door) are passed through unchanged.
    
    Input/Output format: list of (x1, y1, x2, y2, conf, cls_id)
    """
    plates = []
    others = []
    
    for det in detections:
        x1, y1, x2, y2, conf, cls_id = det
        if cls_id == 0:
            plates.append(list(det))
        else:
            others.append(det)
    
    if len(plates) <= 1:
        return detections  # Nothing to merge
    
    # Sort plates by Y (top to bottom)
    plates.sort(key=lambda b: b[1])
    
    merged = []
    used = [False] * len(plates)
    
    for i in range(len(plates)):
        if used[i]:
            continue
        
        current = plates[i][:]  # Copy
        
        for j in range(i + 1, len(plates)):
            if used[j]:
                continue
            
            candidate = plates[j]
            
            if _should_merge(current, candidate):
                # Merge: expand current to encompass candidate
                current[0] = min(current[0], candidate[0])  # x1
                current[1] = min(current[1], candidate[1])  # y1
                current[2] = max(current[2], candidate[2])  # x2
                current[3] = max(current[3], candidate[3])  # y2
                current[4] = max(current[4], candidate[4])  # conf = max
                used[j] = True
                logger.info(
                    "MERGED 2-line plate: (%d,%d,%d,%d) + (%d,%d,%d,%d) → (%d,%d,%d,%d)",
                    plates[i][0], plates[i][1], plates[i][2], plates[i][3],
                    candidate[0], candidate[1], candidate[2], candidate[3],
                    current[0], current[1], current[2], current[3],
                )
        
        merged.append(tuple(current))
        used[i] = True
    
    return merged + list(others)


def _should_merge(a: list, b: list) -> bool:
    """
    Check if two plate bboxes should be merged.
    a and b are [x1, y1, x2, y2, conf, cls_id].
    """
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    
    # Heights
    h_a = ay2 - ay1
    h_b = by2 - by1
    min_h = min(h_a, h_b)
    max_h = max(h_a, h_b)
    
    if min_h <= 0:
        return False
    
    # If they're sorted top-to-bottom: a is above b
    gap = by1 - ay2  # Positive = gap, negative = overlap
    
    if gap > max_h * 1.0:
        return False  # Too far apart vertically
    
    # 2. Horizontal overlap: they must share significant horizontal range
    overlap_x1 = max(ax1, bx1)
    overlap_x2 = min(ax2, bx2)
    overlap_w = max(0, overlap_x2 - overlap_x1)
    
    w_a = ax2 - ax1
    w_b = bx2 - bx1
    min_w = min(w_a, w_b)
    
    if min_w <= 0:
        return False
    
    # At least 30% horizontal overlap relative to the smaller box
    if overlap_w / min_w < 0.3:
        return False
    
    # 3. Size sanity: the two boxes shouldn't differ wildly in width
    if max(w_a, w_b) / min(w_a, w_b) > 3.0:
        return False  # One is way wider — probably not the same plate
    
    return True
