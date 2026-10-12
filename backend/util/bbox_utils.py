
import logging

logger = logging.getLogger(__name__)


def merge_plate_boxes(detections: list) -> list:
    plates = []
    others = []
    
    for det in detections:
        x1, y1, x2, y2, conf, cls_id = det
        if cls_id == 0:
            plates.append(list(det))
        else:
            others.append(det)
    
    if len(plates) <= 1:
        return detections
    
    plates.sort(key=lambda b: b[1])
    
    merged = []
    used = [False] * len(plates)
    
    for i in range(len(plates)):
        if used[i]:
            continue
        
        current = plates[i][:]
        
        for j in range(i + 1, len(plates)):
            if used[j]:
                continue
            
            candidate = plates[j]
            
            if _should_merge(current, candidate):
                current[0] = min(current[0], candidate[0])
                current[1] = min(current[1], candidate[1])
                current[2] = max(current[2], candidate[2])
                current[3] = max(current[3], candidate[3])
                current[4] = max(current[4], candidate[4])
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
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    
    h_a = ay2 - ay1
    h_b = by2 - by1
    min_h = min(h_a, h_b)
    max_h = max(h_a, h_b)
    
    if min_h <= 0:
        return False
    
    gap = by1 - ay2
    
    if gap > max_h * 1.0:
        return False
    
    overlap_x1 = max(ax1, bx1)
    overlap_x2 = min(ax2, bx2)
    overlap_w = max(0, overlap_x2 - overlap_x1)
    
    w_a = ax2 - ax1
    w_b = bx2 - bx1
    min_w = min(w_a, w_b)
    
    if min_w <= 0:
        return False
    
    if overlap_w / min_w < 0.3:
        return False
    
    if max(w_a, w_b) / min(w_a, w_b) > 3.0:
        return False
    
    return True
