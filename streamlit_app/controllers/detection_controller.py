import time
import re
import cv2
import numpy as np
import os

# ── COCO class IDs we care about ──
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 1: "bicycle"}
PERSON_CLASS = {0: "person"}

# Custom model classes (when using custom_detector.pt)
CUSTOM_VEHICLE_CLASSES = {1: "car", 2: "truck", 3: "motorcycle"}
CUSTOM_PERSON_CLASS = {0: "person"}
CUSTOM_PLATE_CLASS = {4: "license_plate"}
CUSTOM_DOOR_CLASSES = {5: "door_open", 6: "door_closed"}

# Class names that indicate a vehicle (for flexible matching)
VEHICLE_NAMES = {"car", "truck", "motorcycle", "bus", "bicycle", "vehicle"}
PERSON_NAMES = {"person", "human", "pedestrian"}
PLATE_NAMES = {"license_plate", "plate", "license plate", "number_plate"}
DOOR_NAMES = {"door_open", "door_closed", "gate_open", "gate_closed"}

# Vietnamese plate pattern
VN_PLATE_PATTERN = re.compile(r'\d{2}[A-Z]\d{3,5}\.?\d{0,2}')


def enhance_plate(crop: np.ndarray) -> np.ndarray:
    """Enhance a cropped image for better OCR readability."""
    h, w = crop.shape[:2]
    # Scale up for better OCR
    scale = max(3, min(6, 300 // max(h, 1)))
    up = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    # CLAHE for better contrast
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blur = cv2.GaussianBlur(enhanced, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def read_plate_ocr(ocr, crop: np.ndarray) -> tuple[str, float]:
    """Run PaddleOCR on a crop and extract plate text."""
    if ocr is None:
        return "", 0.0
    try:
        result = ocr.ocr(crop, cls=True)
    except Exception:
        return "", 0.0
    if not result or not result[0]:
        return "", 0.0
    texts, confs = [], []
    for line in result[0]:
        if line and len(line) >= 2:
            texts.append(line[1][0])
            confs.append(float(line[1][1]))
    if not texts:
        return "", 0.0
    raw_text = " ".join(texts).upper()
    # Clean up: keep only alphanumerics, dots, dashes
    cleaned = re.sub(r'[^A-Z0-9.\-]', '', raw_text)
    avg_conf = sum(confs) / len(confs)
    return cleaned, avg_conf


def find_plate_region(crop: np.ndarray) -> list[tuple[int, int, int, int]]:
    """
    Heuristic to find rectangular plate-like regions within a vehicle crop.
    Returns list of (x, y, w, h) candidate bounding boxes.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    
    # Dilate to connect edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 3))
    dilated = cv2.dilate(edges, kernel, iterations=2)
    
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    h_img, w_img = crop.shape[:2]
    candidates = []
    
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        aspect = w / max(h, 1)
        area = w * h
        area_ratio = area / max(w_img * h_img, 1)
        
        # Vietnamese plates: aspect ratio ~2.0-5.0, reasonable area  
        if 1.5 < aspect < 6.0 and 0.005 < area_ratio < 0.25 and w > 40 and h > 12:
            candidates.append((x, y, w, h))
    
    # Sort by area (largest first) and return top 3
    candidates.sort(key=lambda c: c[2] * c[3], reverse=True)
    return candidates[:3]


def draw_label(img, x1, y1, label, color):
    """Draw label with background on image."""
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(img, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, y1), color, -1)
    cv2.putText(img, label, (x1 + 3, max(y1 - 4, th)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)


class DetectionController:
    def __init__(self, model, ocr, face_app):
        self.model = model
        self.ocr = ocr
        self.face_app = face_app

    def detect_plates(self, img: np.ndarray, conf_thresh: float, iou_thresh: float):
        """
        2-Stage Detection Pipeline:
        Stage 1: YOLO detects objects. Supports both COCO and Custom (7-class) models.
        Stage 2: OCR fine-tuning for plates.
        """
        t0 = time.perf_counter()
        
        # Check if we are using the custom model or COCO
        is_custom = "license_plate" in self.model.names.values()
        
        results = self.model(img, imgsz=640, conf=conf_thresh, iou=iou_thresh, verbose=False)

        detections = []
        annotated = img.copy()
        
        vehicles_found = []
        persons_found = []
        doors_found = []
        direct_plates = []

        h_img, w_img = img.shape[:2]
        img_area = h_img * w_img

        for r in results:
            for b in r.boxes:
                cls_id = int(b.cls[0])
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                det_conf = float(b.conf[0])
                
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w_img, x2), min(h_img, y2)
                bw, bh = x2 - x1, y2 - y1
                if bw < 5 or bh < 5:
                    continue

                cls_name = self.model.names.get(cls_id, f"cls_{cls_id}").lower()

                # --- 1. PERSON ---
                if cls_name in PERSON_NAMES or cls_id in PERSON_CLASS or (is_custom and cls_id == 0):
                    persons_found.append({
                        "cls_name": "person", "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })
                
                # --- 2. LICENSE PLATE (Direct Detection) ---
                elif cls_name in PLATE_NAMES or (is_custom and cls_id == 4):
                    direct_plates.append({
                        "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })

                # --- 3. ROLLING DOORS ---
                elif cls_name in DOOR_NAMES or (is_custom and cls_id in (5, 6)):
                    doors_found.append({
                        "cls_name": cls_name, "bbox": (x1, y1, x2, y2), "conf": det_conf
                    })

                # --- 4. VEHICLES ---
                elif cls_name in VEHICLE_NAMES or cls_id in VEHICLE_CLASSES or (is_custom and cls_id in (1, 2, 3)):
                    vehicles_found.append({
                        "cls_id": cls_id,
                        "cls_name": VEHICLE_CLASSES.get(cls_id, cls_name),
                        "bbox": (x1, y1, x2, y2),
                        "conf": det_conf
                    })
                
                # --- 5. FALLBACK (MISC) ---
                else:
                    box_area = bw * bh
                    if box_area > img_area * 0.05: # Large unknown object could be a vehicle
                        vehicles_found.append({
                            "cls_id": cls_id,
                            "cls_name": cls_name,
                            "bbox": (x1, y1, x2, y2),
                            "conf": det_conf
                        })

        # --- PROCESS DIRECT PLATES ---
        for dp in direct_plates:
            px1, py1, px2, py2 = dp["bbox"]
            p_crop = img[py1:py2, px1:px2]
            if p_crop.size == 0: continue
            
            p_text, p_ocr_conf = self._best_ocr(p_crop)
            if p_ocr_conf > 0.2:
                detections.append({
                    "type": "vehicle", "cls_name": "license_plate",
                    "bbox": dp["bbox"], "det_conf": dp["conf"],
                    "plate_text": p_text, "ocr_conf": p_ocr_conf,
                    "final_conf": dp["conf"] * p_ocr_conf,
                    "crop": p_crop, "enhanced": enhance_plate(p_crop)
                })
                # Draw direct plate
                cv2.rectangle(annotated, (px1, py1), (px2, py2), (0, 255, 0), 2)
                draw_label(annotated, px1, py1, f"PLATE: {p_text}", (0, 255, 0))

        # --- PROCESS VEHICLES (2-STAGE) ---
        for veh in vehicles_found:
            x1, y1, x2, y2 = veh["bbox"]
            # Skip if this vehicle already contains a direct_plate (optional optimization)
            
            crop = img[y1:y2, x1:x2]
            if crop.size == 0: continue

            plate_text, ocr_conf, plate_crop, enhanced = self._ocr_vehicle_crop(crop)
            final_conf = veh["conf"] * ocr_conf if ocr_conf > 0 else veh["conf"]

            # Draw vehicle
            color = (0, 200, 0) if plate_text else (0, 165, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            lbl = f"{veh['cls_name'].upper()} {plate_text} ({ocr_conf:.0%})" if plate_text else f"{veh['cls_name'].upper()} ({veh['conf']:.0%})"
            draw_label(annotated, x1, y1, lbl, color)

            detections.append({
                "type": "vehicle", "cls_name": veh["cls_name"],
                "bbox": (x1, y1, x2, y2), "det_conf": veh["conf"],
                "plate_text": plate_text, "ocr_conf": ocr_conf,
                "final_conf": final_conf, "crop": plate_crop, "enhanced": enhanced,
            })

        # --- PROCESS DOORS ---
        for door in doors_found:
            x1, y1, x2, y2 = door["bbox"]
            color = (255, 0, 255) # Magenta for doors
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            draw_label(annotated, x1, y1, f"{door['cls_name'].upper()} ({door['conf']:.0%})", color)
            detections.append({
                "type": "door", "cls_name": door["cls_name"],
                "bbox": (x1, y1, x2, y2), "det_conf": door["conf"]
            })

        # --- FALLBACK: SCAN FULL IMAGE ---
        if not [d for d in detections if d.get("plate_text")]:
            plate_text, ocr_conf, plate_crop, enhanced = self._ocr_full_image(img)
            if ocr_conf > 0.3:
                h_a, w_a = annotated.shape[:2]
                draw_label(annotated, 10, h_a - 40, f"FULL-SCAN: {plate_text}", (0, 255, 255))
                detections.append({
                    "type": "vehicle", "cls_name": "direct_scan",
                    "bbox": (0, 0, w_a, h_a), "det_conf": 1.0,
                    "plate_text": plate_text, "ocr_conf": ocr_conf,
                    "final_conf": ocr_conf, "crop": plate_crop, "enhanced": enhanced,
                })

        # --- PROCESS PERSONS ---
        for p in persons_found:
            x1, y1, x2, y2 = p["bbox"]
            color = (255, 180, 0)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            draw_label(annotated, x1, y1, f"PERSON ({p['conf']:.0%})", color)
            detections.append({
                "type": "person", "cls_name": "person",
                "bbox": (x1, y1, x2, y2), "det_conf": p["conf"]
            })

        det_ms = (time.perf_counter() - t0) * 1000
        return annotated, detections, det_ms

    def _ocr_vehicle_crop(self, crop: np.ndarray) -> tuple:
        """Run OCR on a vehicle crop using multiple strategies."""
        best_text, best_conf = "", 0.0
        best_crop, best_enh = crop, crop
        
        # Strategy 1: Find plate-like rectangles via contours
        plate_candidates = find_plate_region(crop)
        for px, py, pw, ph in plate_candidates:
            pad = 5
            py1, px1 = max(0, py - pad), max(0, px - pad)
            py2 = min(crop.shape[0], py + ph + pad)
            px2 = min(crop.shape[1], px + pw + pad)
            sub = crop[py1:py2, px1:px2]
            if sub.size == 0:
                continue
            text, conf = self._best_ocr(sub)
            if conf > best_conf and len(text) >= 4:
                best_text, best_conf = text, conf
                best_crop, best_enh = sub, enhance_plate(sub)
        
        # Strategy 2: Bottom half of vehicle (where plates usually are)
        if best_conf < 0.3:
            h = crop.shape[0]
            bottom = crop[h // 3:, :]
            if bottom.size > 0:
                text, conf = self._best_ocr(bottom)
                if conf > best_conf and len(text) >= 4:
                    best_text, best_conf = text, conf
                    best_crop, best_enh = bottom, enhance_plate(bottom)
        
        # Strategy 3: Full crop
        if best_conf < 0.3:
            text, conf = self._best_ocr(crop)
            if conf > best_conf and len(text) >= 3:
                best_text, best_conf = text, conf
                best_crop, best_enh = crop, enhance_plate(crop)
        
        return best_text, best_conf, best_crop, best_enh

    def _ocr_full_image(self, img: np.ndarray) -> tuple:
        """Scan the entire image for plates when YOLO detection fails."""
        best_text, best_conf = "", 0.0
        best_crop, best_enh = img, img
        
        # Try plate region detection on full image
        candidates = find_plate_region(img)
        for px, py, pw, ph in candidates:
            pad = 8
            py1, px1 = max(0, py - pad), max(0, px - pad)
            py2 = min(img.shape[0], py + ph + pad)
            px2 = min(img.shape[1], px + pw + pad)
            sub = img[py1:py2, px1:px2]
            if sub.size == 0:
                continue
            text, conf = self._best_ocr(sub)
            if conf > best_conf and len(text) >= 4:
                best_text, best_conf = text, conf
                best_crop, best_enh = sub, enhance_plate(sub)
        
        # Also try OCR on the full image directly 
        if best_conf < 0.3:
            text, conf = self._best_ocr(img)
            if conf > best_conf and len(text) >= 3:
                best_text, best_conf = text, conf
                best_crop, best_enh = img, img

        return best_text, best_conf, best_crop, best_enh

    def _best_ocr(self, crop: np.ndarray) -> tuple[str, float]:
        """Try OCR on both original and enhanced versions, return the best result."""
        text1, conf1 = read_plate_ocr(self.ocr, crop)
        enh = enhance_plate(crop)
        text2, conf2 = read_plate_ocr(self.ocr, enh)
        if conf2 > conf1:
            return text2, conf2
        return text1, conf1

    def detect_faces(self, img: np.ndarray, known_dir: str):
        if self.face_app is None:
            return img.copy(), [], 0

        # Load known embeddings
        known: dict[str, np.ndarray] = {}
        if os.path.isdir(known_dir):
            for person in os.listdir(known_dir):
                pdir = os.path.join(known_dir, person)
                if not os.path.isdir(pdir):
                    continue
                embs = []
                for f in os.listdir(pdir):
                    if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                        continue
                    pimg = cv2.imread(os.path.join(pdir, f))
                    if pimg is None:
                        continue
                    faces = self.face_app.get(pimg)
                    if faces:
                        embs.append(faces[0].normed_embedding)
                if embs:
                    known[person] = np.mean(embs, axis=0)

        t0 = time.perf_counter()
        faces = self.face_app.get(img)
        det_ms = (time.perf_counter() - t0) * 1000

        annotated = img.copy()
        detections = []

        for face in faces:
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
            emb = face.normed_embedding

            name, sim = "STRANGER", 0.0
            for pname, ref in known.items():
                s = float(np.dot(emb, ref))
                if s > sim:
                    sim = s
                    name = pname if s > 0.35 else "STRANGER"

            color = (0, 200, 0) if name != "STRANGER" else (0, 0, 220)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{name} ({sim:.2f})"
            draw_label(annotated, x1, y1, label, color)

            detections.append({"name": name, "similarity": sim, "bbox": (x1, y1, x2, y2)})

        return annotated, detections, det_ms
